import { useCallback, useEffect, useRef, useState } from "react";
import { askGenie, exportCsv, getHealth, getPosture, getSuggestions } from "./api";
import EvidenceDrawer from "./components/EvidenceDrawer";
import ResultChart from "./components/ResultChart";
import ResultTable from "./components/ResultTable";
import type { Cell, Health, Posture, Suggestions, Turn } from "./types";

const HERO_QUESTION =
  "If we only had budget for three more controls this quarter, which three would " +
  "close the most high-criticality gaps across the most frameworks?";

let turnSeq = 0;

export default function App() {
  const [suggestions, setSuggestions] = useState<Suggestions | null>(null);
  const [posture, setPosture] = useState<Posture | null>(null);
  const [postureError, setPostureError] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);

  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [drawerId, setDrawerId] = useState<string | null>(null);

  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setHealth(null));
    getSuggestions().then(setSuggestions).catch(() => setSuggestions(null));
    getPosture()
      .then(setPosture)
      .catch((e) => setPostureError(String(e.message ?? e)));
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns.length]);

  const ask = useCallback(
    async (question: string) => {
      if (!question.trim() || busy) return;
      setBusy(true);
      setInput("");

      const id = `t${++turnSeq}`;
      setTurns((prev) => [
        ...prev,
        {
          id,
          question,
          status: "SUBMITTED",
          statusLabel: "Sending to Genie…",
          elapsed: 0,
          sql: null,
          answer: null,
          error: null,
          done: false,
        },
      ]);

      const patch = (fn: (t: Turn) => Turn) =>
        setTurns((prev) => prev.map((t) => (t.id === id ? fn(t) : t)));

      try {
        await askGenie(question, conversationId, (ev) => {
          if (ev.type === "status") {
            patch((t) => ({ ...t, status: ev.stage, statusLabel: ev.label, elapsed: ev.elapsed_s }));
          } else if (ev.type === "sql") {
            patch((t) => ({ ...t, sql: ev.sql }));
          } else if (ev.type === "done") {
            setConversationId(ev.answer.conversation_id);
            patch((t) => ({
              ...t,
              answer: ev.answer,
              sql: ev.answer.sql ?? t.sql,
              error: ev.answer.error,
              done: true,
            }));
          } else if (ev.type === "error") {
            patch((t) => ({ ...t, error: ev.message, done: true }));
          }
        });
      } catch (e) {
        patch((t) => ({ ...t, error: String((e as Error).message ?? e), done: true }));
      } finally {
        setBusy(false);
      }
    },
    [busy, conversationId],
  );

  const tileQuestion = (key: string) => suggestions?.tiles?.[key];

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">ComplyLens</span>
        <span className="tag">Northwind Regional Bank · 5 frameworks</span>
        <span className="spacer" />
        <span className="powered">
          Powered by <b>Databricks Genie</b>
        </span>
      </header>

      <main className="main">
        {health?.status === "misconfigured" && (
          <div className="banner">
            <b>Not fully configured.</b> Missing: {health.missing_configuration.join("; ")}.
            Add the resources in the app's <code>Resources</code> panel and redeploy.
          </div>
        )}

        {health?.mock && (
          <div className="banner">
            <b>Offline preview.</b> Answers come from local fixture data, not a Genie Agent —
            the SQL shown is what Genie writes for each question, but it was not executed.
            Unset <code>COMPLYLENS_MOCK</code> and bind a Genie Agent for real answers.
          </div>
        )}

        {health?.status === "mock_data_missing" && (
          <div className="banner">
            <b>Fixture data missing.</b> Run{" "}
            <code>python data_generator/export_for_sql.py</code> and restart the backend.
          </div>
        )}

        {/* -------- posture tiles: one Genie call, four numbers -------- */}
        <div className="tiles">
          <Tile
            label="Overall coverage"
            value={posture ? `${posture.coverage_pct}%` : null}
            tone={
              posture?.coverage_pct == null
                ? undefined
                : posture.coverage_pct >= 75
                  ? "good"
                  : posture.coverage_pct >= 55
                    ? "warn"
                    : "bad"
            }
            sub={posture ? `${posture.total_obligations} obligations tracked` : "…"}
            error={postureError}
            onClick={() => { const q = tileQuestion("coverage"); if (q) ask(q); }}
          />
          <Tile
            label="High-criticality gaps"
            value={posture?.high_criticality_gaps ?? null}
            tone="bad"
            sub="Not fully covered"
            error={postureError}
            onClick={() => { const q = tileQuestion("high_criticality_gaps"); if (q) ask(q); }}
          />
          <Tile
            label="Open gaps"
            value={posture?.gaps ?? null}
            tone="warn"
            sub={posture ? `${posture.partial} partially covered` : "…"}
            error={postureError}
            onClick={() => { const q = tileQuestion("frameworks"); if (q) ask(q); }}
          />
          <Tile
            label="Frameworks"
            value={5}
            sub="FFIEC · NIST · ISO · SOC 2 · PCI"
            onClick={() => { const q = tileQuestion("weakest"); if (q) ask(q); }}
          />
        </div>

        <div className="provenance">
          <span className="dot" />
          {posture
            ? <>These numbers came from a Genie conversation, not a hardcoded query
                {posture.elapsed_s ? ` (${posture.elapsed_s}s)` : ""}. Click any tile to open it.</>
            : postureError
              ? <span style={{ color: "var(--bad)" }}>Could not load posture: {postureError}</span>
              : "Asking Genie for the current posture…"}
        </div>

        {/* -------- ask -------- */}
        <div className="askbox">
          <form
            className="askrow"
            onSubmit={(e) => {
              e.preventDefault();
              ask(input);
            }}
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask anything about our compliance posture…"
              disabled={busy}
              aria-label="Ask a question"
            />
            <button className="btn" type="submit" disabled={busy || !input.trim()}>
              {busy ? "Asking…" : "Ask Genie"}
            </button>
          </form>

          {suggestions && (
            <div className="groups">
              {suggestions.groups.map((g) => (
                <div className="group" key={g.id}>
                  <div className="gl" title={g.hint}>{g.label}</div>
                  <div className="chips">
                    {g.questions.map((q) => (
                      <button
                        key={q}
                        className={`chip${q === HERO_QUESTION ? " hero" : ""}`}
                        disabled={busy}
                        onClick={() => ask(q)}
                        title={q}
                      >
                        {shorten(q)}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* -------- answers -------- */}
        <div className="turns">
          {turns.map((t) => (
            <TurnCard key={t.id} turn={t} onOpenEvidence={setDrawerId} />
          ))}
          <div ref={endRef} />
        </div>

        {turns.length === 0 && (
          <div className="empty">
            <h3>Ask a question to begin</h3>
            <p>
              Every answer comes back with the SQL Genie wrote and the policy clause behind it.
            </p>
          </div>
        )}
      </main>

      <footer className="footer">
        Northwind Regional Bank is fictional; its policy corpus and assessment results are
        synthetic. Framework obligations are real — ISO 27001, SOC 2 and PCI DSS requirement
        text is paraphrased, since those standards are copyrighted. FFIEC and NIST material is
        US Government work in the public domain.
      </footer>

      {drawerId && (
        <EvidenceDrawer obligationId={drawerId} onClose={() => setDrawerId(null)} onAsk={ask} />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */

function shorten(q: string): string {
  if (q.length <= 62) return q;
  return q.slice(0, 59).replace(/[\s,]+\S*$/, "") + "…";
}

function Tile({
  label,
  value,
  sub,
  tone,
  error,
  onClick,
}: {
  label: string;
  value: string | number | null;
  sub?: string;
  tone?: "good" | "warn" | "bad";
  error?: string | null;
  onClick?: () => void;
}) {
  const loading = value === null && !error;
  return (
    <button className="tile" onClick={onClick} disabled={loading || Boolean(error)}>
      <div className="label">{label}</div>
      {loading ? (
        <div className="skeleton" style={{ height: 34, width: "58%", marginTop: 6 }} />
      ) : error ? (
        <div className="value" style={{ fontSize: 17, color: "var(--muted)" }}>—</div>
      ) : (
        <div className={`value${tone ? ` ${tone}` : ""}`}>{value}</div>
      )}
      {sub && <div className="sub">{sub}</div>}
      <div className="ask">Ask Genie →</div>
    </button>
  );
}

function TurnCard({
  turn,
  onOpenEvidence,
}: {
  turn: Turn;
  onOpenEvidence: (id: string) => void;
}) {
  const a = turn.answer;
  const rows = a?.rows ?? [];
  const cols = a?.columns ?? [];

  return (
    <article className="turn">
      <div className="q">
        <span className="marker">Q</span>
        <span className="qt">{turn.question}</span>
      </div>
      <div className="body">
        {!turn.done && (
          <>
            <div className="status">
              <span className="spinner" />
              <span>{turn.statusLabel}</span>
              <span className="elapsed">{turn.elapsed.toFixed(1)}s</span>
            </div>
            {turn.sql && (
              <div className="stages">
                <div className="s on">Genie wrote the SQL — waiting on the warehouse…</div>
              </div>
            )}
          </>
        )}

        {turn.error && <div className="errbox">{turn.error}</div>}

        {a && !turn.error && (
          <>
            {a.text && <div className="headline">{a.text}</div>}
            {rows.length > 0 && (
              <>
                <ResultChart columns={cols} rows={rows} />
                <ResultTable
                  columns={cols}
                  rows={rows}
                  onRowClick={(row: Record<string, Cell>) => {
                    const id = row["obligation_id"];
                    if (id) onOpenEvidence(String(id));
                  }}
                />
              </>
            )}

            <div className="meta">
              <span>
                {a.row_count} row{a.row_count === 1 ? "" : "s"}
                {a.truncated ? ` (showing ${rows.length})` : ""}
              </span>
              <span className="sep">·</span>
              <span>{a.elapsed_s}s</span>
              {rows.some((r) => "obligation_id" in r) && (
                <>
                  <span className="sep">·</span>
                  <span>Click a row for evidence</span>
                </>
              )}
              <span style={{ flex: 1 }} />
              {rows.length > 0 && (
                <button
                  className="btn ghost sm"
                  onClick={() =>
                    exportCsv(turn.question, a.sql, cols.map((c) => c.name), rows)
                  }
                >
                  Export audit pack
                </button>
              )}
            </div>
          </>
        )}

        {turn.sql && (
          <details className="sql">
            <summary>Show the SQL Genie wrote</summary>
            <pre>{turn.sql}</pre>
          </details>
        )}
      </div>
    </article>
  );
}
