"""Thin wrapper over the Databricks Genie Conversation API.

Genie's own UI gives users the generated SQL, a chart and a download. Calling the
Conversation API from a custom app loses all of that unless you rebuild it, so this
module returns everything needed to reconstruct those affordances: the text answer, the
SQL Genie wrote, the result rows, and the column schema.

It also emits progress events while polling. On Free Edition's 2X-Small warehouse a
question can take 20 seconds or more, and silence for 20 seconds reads as "broken"
rather than "working". Narrating the stage the query is in is the difference.

Requires databricks-sdk >= 0.57; Databricks Apps pre-installs 0.33.0, which predates
the Genie API, hence the pin in requirements.txt.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.dashboards import (
    GenieAttachment,
    GenieMessage,
    MessageStatus,
)

log = logging.getLogger(__name__)

# Human-readable narration for Genie's internal states.
STAGE_LABELS = {
    "FETCHING_METADATA": "Reading your compliance schema…",
    "FILTERING_CONTEXT": "Selecting the relevant tables…",
    "ASKING_AI": "Genie is writing the SQL…",
    "PENDING_WAREHOUSE": "Waiting for the SQL warehouse…",
    "EXECUTING_QUERY": "Running the query…",
    "COMPLETED": "Done",
    "FAILED": "Genie could not answer that",
    "CANCELLED": "Cancelled",
    "QUERY_RESULT_EXPIRED": "The result expired — please ask again",
    "SUBMITTED": "Sent to Genie…",
}


@dataclass
class GenieAnswer:
    """Everything the UI needs to render one answer."""

    conversation_id: str
    message_id: str
    question: str
    text: str | None = None
    sql: str | None = None
    columns: list[dict[str, str]] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    elapsed_s: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "question": self.question,
            "text": self.text,
            "sql": self.sql,
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "elapsed_s": round(self.elapsed_s, 1),
            "error": self.error,
        }


class GenieClient:
    def __init__(self, space_id: str, timeout_s: int = 180,
                 poll_interval_s: float = 1.2, max_rows: int = 500):
        self.space_id = space_id
        self.timeout_s = timeout_s
        self.poll_interval_s = poll_interval_s
        self.max_rows = max_rows
        self._w = WorkspaceClient()

    # -- public ------------------------------------------------------------

    def ask(self, question: str, conversation_id: str | None = None) -> Iterator[dict]:
        """Ask Genie and yield progress events, ending with a `done` or `error` event.

        Events:
          {"type": "status",  "stage": ..., "label": ..., "elapsed_s": ...}
          {"type": "sql",     "sql": ...}        as soon as Genie produces it
          {"type": "done",    "answer": {...}}
          {"type": "error",   "message": ...}
        """
        started = time.monotonic()
        try:
            if conversation_id:
                message = self._w.genie.create_message(
                    space_id=self.space_id,
                    conversation_id=conversation_id,
                    content=question,
                )
            else:
                started_conv = self._w.genie.start_conversation(
                    space_id=self.space_id, content=question
                )
                message = started_conv.message
                conversation_id = started_conv.conversation_id

            message_id = message.id or message.message_id
            yield self._status("SUBMITTED", started)

            answer = yield from self._poll(conversation_id, message_id, question, started)
            yield {"type": "done", "answer": answer.to_dict()}

        except Exception as e:  # noqa: BLE001 - surfaced to the user, not swallowed
            log.exception("Genie request failed")
            yield {"type": "error", "message": str(e)[:400],
                   "elapsed_s": round(time.monotonic() - started, 1)}

    # -- internals ---------------------------------------------------------

    def _status(self, stage: str, started: float) -> dict:
        return {
            "type": "status",
            "stage": stage,
            "label": STAGE_LABELS.get(stage, stage.replace("_", " ").title()),
            "elapsed_s": round(time.monotonic() - started, 1),
        }

    def _poll(self, conversation_id: str, message_id: str,
              question: str, started: float) -> Iterator[dict]:
        """Poll until terminal, emitting a status event whenever the stage changes."""
        last_stage = None
        sql_emitted = False
        deadline = started + self.timeout_s

        while time.monotonic() < deadline:
            msg: GenieMessage = self._w.genie.get_message(
                space_id=self.space_id,
                conversation_id=conversation_id,
                message_id=message_id,
            )
            stage = msg.status.value if isinstance(msg.status, MessageStatus) else str(msg.status)

            if stage != last_stage:
                yield self._status(stage, started)
                last_stage = stage

            # Genie exposes the SQL before the warehouse finishes. Showing it early is
            # free reassurance that something real is happening.
            if not sql_emitted:
                sql = self._extract_sql(msg)
                if sql:
                    yield {"type": "sql", "sql": sql}
                    sql_emitted = True

            if stage in ("COMPLETED", "FAILED", "CANCELLED", "QUERY_RESULT_EXPIRED"):
                return self._build_answer(
                    conversation_id, message_id, question, msg, stage, started
                )

            time.sleep(self.poll_interval_s)

        return GenieAnswer(
            conversation_id=conversation_id, message_id=message_id, question=question,
            error=f"Genie did not respond within {self.timeout_s}s. "
                  "The SQL warehouse may be starting up — try again.",
            elapsed_s=time.monotonic() - started,
        )

    @staticmethod
    def _extract_sql(msg: GenieMessage) -> str | None:
        for att in msg.attachments or []:
            if getattr(att, "query", None) and getattr(att.query, "query", None):
                return att.query.query
        return None

    @staticmethod
    def _extract_text(msg: GenieMessage) -> str | None:
        parts = [
            att.text.content
            for att in (msg.attachments or [])
            if getattr(att, "text", None) and getattr(att.text, "content", None)
        ]
        return "\n\n".join(parts) if parts else None

    def _build_answer(self, conversation_id: str, message_id: str, question: str,
                      msg: GenieMessage, stage: str, started: float) -> GenieAnswer:
        answer = GenieAnswer(
            conversation_id=conversation_id,
            message_id=message_id,
            question=question,
            text=self._extract_text(msg),
            sql=self._extract_sql(msg),
            elapsed_s=time.monotonic() - started,
        )

        if stage != "COMPLETED":
            answer.error = (
                msg.error.error if getattr(msg, "error", None)
                else STAGE_LABELS.get(stage, stage)
            )
            return answer

        query_att = next(
            (a for a in (msg.attachments or []) if getattr(a, "query", None)), None
        )
        if query_att:
            self._attach_rows(answer, conversation_id, message_id, query_att)

        # A completed message with neither prose nor rows is a non-answer; say so rather
        # than rendering an empty card.
        if not answer.text and not answer.rows:
            answer.error = (
                "Genie completed without returning data. Try rephrasing, or pick one of "
                "the suggested questions."
            )
        return answer

    def _attach_rows(self, answer: GenieAnswer, conversation_id: str,
                     message_id: str, attachment: GenieAttachment) -> None:
        try:
            result = self._w.genie.get_message_attachment_query_result(
                space_id=self.space_id,
                conversation_id=conversation_id,
                message_id=message_id,
                attachment_id=attachment.attachment_id,
            )
            sr = result.statement_response
            if not sr or not sr.manifest or not sr.manifest.schema:
                return

            cols = [
                {"name": c.name, "type": (c.type_name.value if c.type_name else "STRING")}
                for c in (sr.manifest.schema.columns or [])
            ]
            answer.columns = cols

            data = (sr.result.data_array if sr.result else None) or []
            if len(data) > self.max_rows:
                answer.truncated = True
                data = data[: self.max_rows]

            names = [c["name"] for c in cols]
            answer.rows = [dict(zip(names, row)) for row in data]
            answer.row_count = sr.manifest.total_row_count or len(answer.rows)
        except Exception as e:  # noqa: BLE001
            log.warning("Could not fetch query result: %s", e)
            answer.error = f"Genie wrote the SQL but the result could not be fetched: {e}"
