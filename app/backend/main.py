"""ComplyLens — FastAPI backend.

Serves the React SPA and a small API. The API deliberately contains no analytical SQL:
every number the user sees comes from a Genie conversation. The two exceptions are
record lookups (the evidence drawer and the catalog panel), which are point reads rather
than analysis, and are called out as such below.

If Genie were removed, this app would have nothing to display.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import mock
from .config import get_settings
from .genie_client import GenieClient
from .suggestions import POSTURE_QUESTION, SUGGESTION_GROUPS, TILE_QUESTIONS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("complylens")

settings = get_settings()
app = FastAPI(title="ComplyLens", version="1.0.0", docs_url="/api/docs")

_genie: GenieClient | None = None


def genie() -> GenieClient:
    global _genie
    if _genie is None:
        if not settings.genie_space_id:
            raise HTTPException(
                503,
                "GENIE_SPACE_ID is not set. Bind a Genie Agent resource to this app with "
                "CAN RUN permission, then redeploy.",
            )
        _genie = GenieClient(
            space_id=settings.genie_space_id,
            timeout_s=settings.genie_timeout_s,
            poll_interval_s=settings.genie_poll_interval_s,
            max_rows=settings.max_rows,
        )
    return _genie


# ---------------------------------------------------------------------------
# Health and metadata
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, Any]:
    missing = settings.missing()
    if settings.mock:
        status = "mock" if mock.available() else "mock_data_missing"
    else:
        status = "ok" if not missing else "misconfigured"
    return {
        "status": status,
        "mock": settings.mock,
        "genie_space_bound": bool(settings.genie_space_id),
        "warehouse_bound": bool(settings.warehouse_id),
        "view_schema": settings.view_schema,
        "missing_configuration": missing,
    }


@app.get("/api/suggestions")
def suggestions() -> dict[str, Any]:
    """Certified questions for the chips. Same list the agent was benchmarked on."""
    return {"groups": SUGGESTION_GROUPS, "tiles": TILE_QUESTIONS}


# ---------------------------------------------------------------------------
# Ask — the core of the app
# ---------------------------------------------------------------------------


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    conversation_id: str | None = None


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@app.post("/api/ask")
def ask(req: AskRequest) -> StreamingResponse:
    """Stream a Genie answer as Server-Sent Events.

    Streaming matters here for an unglamorous reason: on Free Edition's 2X-Small
    warehouse a question can take 20+ seconds, and a spinner with no narration reads as
    a hung app. Emitting Genie's own stage transitions turns the wait into progress.
    """
    if settings.mock:
        if not mock.available():
            raise HTTPException(
                503,
                "Mock mode is on but the fixture data is missing. Run: "
                "python data_generator/export_for_sql.py",
            )

        def stream():
            for event in mock.stream(req.question):
                yield _sse(event)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    client = genie()

    def stream():
        try:
            for event in client.ask(req.question, req.conversation_id):
                yield _sse(event)
        except Exception as e:  # noqa: BLE001
            log.exception("stream failed")
            yield _sse({"type": "error", "message": str(e)[:400]})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# ---------------------------------------------------------------------------
# Posture tiles — one Genie call, four tiles
# ---------------------------------------------------------------------------

_posture_cache: dict[str, Any] = {"at": 0.0, "payload": None}


@app.get("/api/posture")
def posture(refresh: bool = Query(False)) -> dict[str, Any]:
    """Headline numbers for the tiles.

    Q01 returns the overall percentage plus the covered / partial / gap /
    high-criticality-gap counts in a single row, so all four tiles come from one Genie
    round trip. Cached briefly because it is fetched on every page load and the answer
    only changes when the pipeline re-runs.
    """
    now = time.monotonic()
    if not refresh and _posture_cache["payload"] and (now - _posture_cache["at"]) < settings.posture_cache_ttl_s:
        return {**_posture_cache["payload"], "cached": True}

    if settings.mock:
        if not mock.available():
            raise HTTPException(503, "Mock fixture data missing — run export_for_sql.py")
        result = mock.answer(POSTURE_QUESTION)
        row = result["rows"][0]
        return {
            "coverage_pct": row["overall_coverage_pct"],
            "total_obligations": row["total_obligations"],
            "covered": row["covered"],
            "partial": row["partial"],
            "gaps": row["gaps"],
            "high_criticality_gaps": row["high_criticality_gaps"],
            "sql": result["sql"],
            "narrative": result["text"],
            "conversation_id": "mock-conversation",
            "elapsed_s": 0.4,
            "source": "mock",
            "cached": False,
        }

    answer = None
    for event in genie().ask(POSTURE_QUESTION):
        if event["type"] == "done":
            answer = event["answer"]
        elif event["type"] == "error":
            raise HTTPException(502, event["message"])

    if not answer or not answer.get("rows"):
        raise HTTPException(502, "Genie returned no posture data.")

    row = answer["rows"][0]

    def num(*names, default=None):
        for n in names:
            for key in row:
                if key.lower() == n:
                    try:
                        return float(row[key]) if "." in str(row[key]) else int(row[key])
                    except (TypeError, ValueError):
                        return row[key]
        return default

    payload = {
        "coverage_pct": num("overall_coverage_pct", "coverage_pct"),
        "total_obligations": num("total_obligations"),
        "covered": num("covered"),
        "partial": num("partial"),
        "gaps": num("gaps", "gap_count"),
        "high_criticality_gaps": num("high_criticality_gaps", "high_criticality_gap_count"),
        "sql": answer.get("sql"),
        "narrative": answer.get("text"),
        "conversation_id": answer.get("conversation_id"),
        "elapsed_s": answer.get("elapsed_s"),
        "source": "genie",
        "cached": False,
    }
    _posture_cache.update({"at": now, "payload": payload})
    return payload


# ---------------------------------------------------------------------------
# Evidence drawer — a record lookup, not analysis
# ---------------------------------------------------------------------------


def _run_sql(statement: str, params: list[dict] | None = None) -> list[dict[str, Any]]:
    """Execute a parameterised point read against the serving views.

    Used only for the evidence drawer and the catalog panel. Both are single-record
    lookups triggered by clicking a row Genie already returned — routing them through a
    second Genie conversation would add 20 seconds to a click that should be instant,
    with no analytical value. Every aggregate number in the app still comes from Genie.
    """
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.sql import StatementState

    if not settings.warehouse_id:
        raise HTTPException(503, "DATABRICKS_WAREHOUSE_ID is not set. Bind a SQL warehouse resource.")

    w = WorkspaceClient()
    resp = w.statement_execution.execute_statement(
        warehouse_id=settings.warehouse_id,
        statement=statement,
        parameters=params or [],
        wait_timeout="30s",
    )
    if resp.status and resp.status.state != StatementState.SUCCEEDED:
        detail = resp.status.error.message if resp.status.error else str(resp.status.state)
        raise HTTPException(502, f"Query failed: {detail}")

    if not resp.manifest or not resp.manifest.schema:
        return []
    names = [c.name for c in (resp.manifest.schema.columns or [])]
    data = (resp.result.data_array if resp.result else None) or []
    return [dict(zip(names, r)) for r in data]


@app.get("/api/evidence/{obligation_id:path}")
def evidence(obligation_id: str) -> dict[str, Any]:
    """Full detail for one obligation: the requirement, its evidence, and its siblings.

    The sibling list is what makes this an audit artifact rather than a row detail —
    it shows the same underlying safeguard satisfying obligations in four other
    frameworks simultaneously.
    """
    if settings.mock:
        result = mock.evidence(obligation_id)
        if not result:
            raise HTTPException(404, f"No obligation with id {obligation_id}")
        return result

    schema = settings.view_schema

    detail = _run_sql(
        f"""
        SELECT obligation_id, framework, control_ref, obligation_title, requirement_text,
               domain, criticality, text_provenance, coverage_status, gap_reason,
               assessment_confidence, assessment_method, human_reviewed,
               unified_control_id, unified_control_name, csf_function,
               control_id, implementation_status, control_owner, control_owner_team,
               control_last_tested_date, control_is_untested,
               policy_doc_number, policy_title, policy_clause_ref, policy_section_heading,
               evidence_text, evidence_page_no, policy_last_reviewed_date, policy_is_stale
        FROM {schema}.v_obligation_coverage
        WHERE obligation_id = :oid
        """,
        [{"name": "oid", "value": obligation_id}],
    )
    if not detail:
        raise HTTPException(404, f"No obligation with id {obligation_id}")

    siblings = _run_sql(
        f"""
        SELECT target_framework AS framework, target_control_ref AS control_ref,
               target_obligation_title AS obligation_title,
               target_criticality AS criticality,
               target_coverage_status AS coverage_status,
               overlap_type
        FROM {schema}.v_framework_overlap
        WHERE source_obligation_id = :oid
        ORDER BY target_framework, target_control_ref
        """,
        [{"name": "oid", "value": obligation_id}],
    )
    return {"obligation": detail[0], "also_satisfies": siblings}


@app.get("/api/catalog")
def catalog() -> dict[str, Any]:
    """Frameworks in scope and the client context, for the About panel."""
    return {
        "frameworks": _run_sql(
            f"""
            SELECT framework, full_name, version, issuing_body, jurisdiction,
                   obligation_count, coverage_pct, gap_count, high_criticality_gap_count
            FROM {settings.view_schema}.d_frameworks ORDER BY coverage_pct
            """
        ),
        "client": {
            "name": "Northwind Regional Bank, N.A.",
            "profile": "US regional bank, ~$8.4B assets, 41 branches across Washington, "
                       "Oregon and Idaho, with an in-house issued card program.",
            "disclaimer": "Northwind Regional Bank is fictional. Its policy corpus, control "
                          "inventory and assessment results are synthetic and generated by this "
                          "project. Framework obligations are real; ISO 27001, SOC 2 and PCI DSS "
                          "requirement text is our own paraphrase, since those standards are "
                          "copyrighted.",
        },
    }


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class ExportRequest(BaseModel):
    question: str
    sql: str | None = None
    columns: list[str] = []
    rows: list[dict[str, Any]] = []


@app.post("/api/export")
def export(req: ExportRequest) -> StreamingResponse:
    """Download the answer as an audit pack: the question, the SQL, and the rows.

    A compliance officer cannot hand a screenshot to an examiner. Bundling the exact
    query alongside the data is what makes the result defensible six months later.
    """
    buf = io.StringIO()
    buf.write(f"# ComplyLens export\n")
    buf.write(f"# Question: {req.question}\n")
    buf.write(f"# Generated (UTC): {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")
    if req.sql:
        buf.write("# SQL generated by the Genie Agent:\n")
        for line in req.sql.splitlines():
            buf.write(f"#   {line}\n")
    buf.write("\n")

    cols = req.columns or (list(req.rows[0].keys()) if req.rows else [])
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(req.rows)

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="complylens_export.csv"'},
    )


# ---------------------------------------------------------------------------
# Static SPA — must be mounted last so /api routes win
# ---------------------------------------------------------------------------

DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if DIST.exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        """Serve index.html for any non-API path so client-side routing works."""
        candidate = DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")
else:
    @app.get("/")
    def no_build() -> dict[str, str]:
        return {
            "error": "Frontend not built.",
            "fix": "Run `npm install && npm run build` in the app/ directory.",
            "api_docs": "/api/docs",
        }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.app_port)
