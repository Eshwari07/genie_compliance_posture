import { useEffect, useState } from "react";
import { getEvidence } from "../api";
import type { Evidence } from "../types";

const truthy = (v: unknown) => v === true || v === "true" || v === "True";

/**
 * Turns a row Genie returned into an audit artifact.
 *
 * A coverage percentage is only useful if you can defend it. This drawer answers the
 * three questions an examiner actually asks about any single requirement: what does it
 * demand, what proves we do it, and what else does that same proof cover. The last one
 * is the payoff of the unified control hub — one policy clause visibly satisfying
 * obligations in four other frameworks at once.
 */
export default function EvidenceDrawer({
  obligationId,
  onClose,
  onAsk,
}: {
  obligationId: string;
  onClose: () => void;
  onAsk: (q: string) => void;
}) {
  const [data, setData] = useState<Evidence | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    getEvidence(obligationId)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(String(e.message ?? e)));
    return () => {
      cancelled = true;
    };
  }, [obligationId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const o = data?.obligation;

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label="Obligation evidence">
        <header>
          <div className="t">
            <div className="eyebrow">
              {o ? `${o.framework} · ${o.control_ref}` : "Loading evidence"}
            </div>
            <h3>{o?.obligation_title ?? obligationId}</h3>
          </div>
          <button className="xbtn" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        {error && (
          <section>
            <div className="errbox">{error}</div>
          </section>
        )}

        {!data && !error && (
          <section>
            <div className="skeleton" style={{ height: 15, marginBottom: 8 }} />
            <div className="skeleton" style={{ height: 15, width: "80%", marginBottom: 8 }} />
            <div className="skeleton" style={{ height: 15, width: "60%" }} />
          </section>
        )}

        {o && (
          <>
            <section>
              <h4>The requirement</h4>
              <p style={{ margin: "0 0 12px", color: "var(--ink)", fontSize: 13.5 }}>
                {o.requirement_text}
              </p>
              <dl className="kv">
                <dt>Status</dt>
                <dd>
                  <span className={`badge ${o.coverage_status.toLowerCase()}`}>
                    {o.coverage_status}
                  </span>
                </dd>
                <dt>Criticality</dt>
                <dd>
                  <span className={`badge ${o.criticality.toLowerCase()}`}>{o.criticality}</span>
                </dd>
                <dt>Domain</dt>
                <dd>{o.domain}</dd>
                <dt>Text source</dt>
                <dd>
                  {o.text_provenance === "verbatim_public"
                    ? "Verbatim, public domain"
                    : o.text_provenance === "paraphrased"
                      ? "Paraphrased (source is copyrighted)"
                      : o.text_provenance}
                </dd>
              </dl>
            </section>

            <section>
              <h4>{o.coverage_status === "Gap" ? "Why this is a gap" : "Evidence"}</h4>
              {o.evidence_text ? (
                <div className="quote">
                  {o.evidence_text}
                  <span className="cite">
                    {o.policy_doc_number} · clause {o.policy_clause_ref}
                    {o.policy_section_heading ? ` · ${o.policy_section_heading}` : ""}
                    {o.evidence_page_no ? ` · page ${o.evidence_page_no}` : ""}
                  </span>
                </div>
              ) : (
                <div className="nogap">
                  {o.gap_reason ?? "No policy clause addresses this requirement."}
                </div>
              )}

              {o.gap_reason && o.evidence_text && (
                <p style={{ marginTop: 10, fontSize: 12.5, color: "var(--muted)" }}>
                  <b>Shortfall:</b> {o.gap_reason}
                </p>
              )}

              {truthy(o.policy_is_stale) && (
                <p style={{ marginTop: 10, fontSize: 12.5, color: "var(--warn)" }}>
                  ⚠ The evidencing policy was last reviewed{" "}
                  {o.policy_last_reviewed_date} and is overdue for review.
                </p>
              )}
            </section>

            <section>
              <h4>Accountability</h4>
              <dl className="kv">
                <dt>Control</dt>
                <dd>{o.unified_control_name}</dd>
                <dt>Implementation</dt>
                <dd>{o.implementation_status ?? "—"}</dd>
                <dt>Owner</dt>
                <dd>
                  {o.control_owner ?? "—"}
                  {o.control_owner_team ? ` · ${o.control_owner_team}` : ""}
                </dd>
                <dt>Last tested</dt>
                <dd>
                  {o.control_last_tested_date ?? "Never"}
                  {truthy(o.control_is_untested) && (
                    <span className="badge gap" style={{ marginLeft: 8 }}>
                      Untested
                    </span>
                  )}
                </dd>
                <dt>Assessed by</dt>
                <dd>
                  {o.assessment_method.replace(/_/g, " ")}
                  {truthy(o.human_reviewed) && (
                    <span className="badge neutral" style={{ marginLeft: 8 }}>
                      Analyst reviewed
                    </span>
                  )}
                </dd>
              </dl>
            </section>

            <section>
              <h4>
                Also satisfies — {data.also_satisfies.length} obligation
                {data.also_satisfies.length === 1 ? "" : "s"} in other frameworks
              </h4>
              {data.also_satisfies.length > 0 ? (
                <>
                  <div className="callout">
                    All of these map to <b>{o.unified_control_name}</b>. One control,{" "}
                    {new Set(data.also_satisfies.map((s) => s.framework)).size + 1} frameworks
                    — this is the work you would otherwise do five times.
                  </div>
                  <div className="sib">
                    {data.also_satisfies.map((s) => (
                      <div className="sibrow" key={`${s.framework}-${s.control_ref}`}>
                        <span className="fw">{s.framework}</span>
                        <span className="ref">{s.control_ref}</span>
                        <span className="ti">{s.obligation_title}</span>
                        <span className={`badge ${s.coverage_status.toLowerCase()}`}>
                          {s.coverage_status}
                        </span>
                      </div>
                    ))}
                  </div>
                </>
              ) : (
                <p style={{ fontSize: 13, color: "var(--muted)" }}>
                  This obligation is unique to {o.framework} in our tracked set.
                </p>
              )}
            </section>

            <section>
              <button
                className="btn ghost sm"
                onClick={() => {
                  onAsk(
                    `If we fully implement ${o.unified_control_name}, which obligations does that close and in which frameworks?`,
                  );
                  onClose();
                }}
              >
                Ask Genie what implementing this would close
              </button>
            </section>
          </>
        )}
      </aside>
    </>
  );
}
