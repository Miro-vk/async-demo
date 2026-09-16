import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { EmailDetail, Outcome, Thresholds } from "../types";
import { humanize } from "../format";
import { Fields, fieldRows, type FieldRow } from "./Fields";
import { ActionChip, Provenance, SeverityChip } from "./primitives";
import { PhantomQuote, SourceText, type Highlight } from "./SourceText";
import { Stages } from "./Stages";

interface Props {
  detail: EmailDetail;
  thresholds: Thresholds | null;
  onBack: () => void;
  onReviewed: (detail: EmailDetail) => void;
}

const REASON_TITLES: Record<string, string> = {
  conflict_hit: "Conflict",
  conflict_check_vacuous: "Nothing was checked",
  low_confidence_classification: "Unsure of the label",
  classifier_said_unclear: "Fits no category",
  low_confidence_field: "Shaky extraction",
  unverified_span: "Unsupported value",
  validator_failed: "Failed a check",
  missing_required_field: "Missing something needed",
  ambiguous_matter_type: "Ambiguous matter type",
  no_attorney_available: "Nobody to route to",
  model_parse_failure: "Unreadable model output",
  partial_parse: "Output partly discarded",
};

export function Detail({ detail, thresholds, onBack, onReviewed }: Props) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const [edits, setEdits] = useState<Record<string, { original: string | null; corrected: string }>>({});
  const [editing, setEditing] = useState<FieldRow | null>(null);
  const [draft, setDraft] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const result = detail.result;
  const rows = useMemo(
    () => (result?.extraction ? fieldRows(result.extraction) : []),
    [result],
  );

  const highlights: Highlight[] = useMemo(() => {
    const collected: Highlight[] = [];
    if (result?.classification?.label.span) {
      collected.push({
        id: "classification.label",
        label: "classification",
        span: result.classification.label.span,
      });
    }
    rows.forEach((row) => {
      if (row.field.span) collected.push({ id: row.id, label: row.label, span: row.field.span });
    });
    return collected;
  }, [result, rows]);

  const phantoms = useMemo(
    () =>
      [
        ...(result?.classification?.label ? [result.classification.label] : []),
        ...rows.map((r) => r.field),
      ].filter((f) => f.span?.status === "not_found"),
    [result, rows],
  );

  // The fields panel already names each field the way a person would ("opposing
  // party", not "second party's name"), so reasons borrow those labels and fall
  // back to the server's rendering of the path.
  const rowLabels = useMemo(
    () => new Map(rows.map((row) => [row.id, row.label])),
    [rows],
  );

  const flagged = useMemo(
    () => new Set((result?.decision.reasons ?? []).map((r) => r.field_path).filter(Boolean) as string[]),
    [result],
  );

  useEffect(() => {
    setActiveId(null);
    setEdits({});
    setEditing(null);
    setNote("");
    setError(null);
  }, [detail.email_id]);

  if (!result) return <div className="empty">This email has not been processed yet.</div>;

  const editCount = Object.keys(edits).length;

  async function submit(outcome: Outcome) {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.review(detail.email_id, {
        outcome,
        note,
        edits: Object.entries(edits).map(([field_path, e]) => ({
          field_path,
          original_value: e.original,
          corrected_value: e.corrected,
        })),
      });
      onReviewed(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="detail">
      <header className="detail-head">
        <button className="back" onClick={onBack}>← Queue</button>
        <div className="detail-subject">
          <h1>{detail.subject}</h1>
          <p>
            {detail.from_name} &lt;{detail.from_email}&gt; ·{" "}
            {new Date(detail.received_at).toLocaleDateString(undefined, {
              day: "numeric", month: "short", year: "numeric",
            })}
          </p>
        </div>
        <ActionChip action={result.decision.action} />
      </header>

      {result.decision.reasons.length > 0 && (
        <section className="why">
          <h2>
            Why this needs a person
            <span className="why-count">{result.decision.reasons.length}</span>
          </h2>
          <ul>
            {result.decision.reasons.map((reason, index) => (
              <li key={index}>
                <button
                  className="why-item"
                  onMouseEnter={() => reason.field_path && setActiveId(reason.field_path)}
                  onMouseLeave={() => setActiveId(null)}
                  onClick={() =>
                    reason.field_path &&
                    document.getElementById(`field-${reason.field_path}`)?.scrollIntoView({
                      block: "center", behavior: "smooth",
                    })
                  }
                >
                  <span className="why-title">
                    {REASON_TITLES[reason.code] ?? reason.code.replace(/_/g, " ")}
                  </span>
                  <span className="why-message">{reason.message}</span>
                  <span className="why-ref">
                    <span className="why-where">
                      {humanize(rowLabels.get(reason.field_path ?? "") ?? reason.field_label)}
                    </span>
                    {reason.rule_label && (
                      <span className="why-rule">{reason.rule_label}</span>
                    )}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="detail-body">
        <div className="detail-left">
          <SourceText
            text={detail.source_text}
            highlights={highlights}
            activeId={activeId}
            onHover={setActiveId}
          />
          {phantoms.map((field, index) => (
            <PhantomQuote key={index} quote={field.span!.quote} />
          ))}
          <p className="prose-note">
            Prose is {detail.prose_source === "naturalized" ? "model-rewritten" : "templated"}{" "}
            synthetic text. No real person wrote it.
          </p>
        </div>

        <div className="detail-right">
          <Stages result={result} thresholds={thresholds} />

          {result.classification && (
            <section className="panel">
              <h2>Classification</h2>
              <div className="classification">
                <strong>{humanize(result.classification.label.value)}</strong>
                <code className="receipt-math">{result.classification.label.explanation}</code>
                <p className="muted">{result.classification.rationale}</p>
              </div>
            </section>
          )}

          <section className="panel">
            <h2>
              Extracted facts <Provenance kind="model" />
            </h2>
            <Fields
              rows={rows}
              activeId={activeId}
              flagged={flagged}
              onHover={setActiveId}
              onEdit={(row) => {
                setEditing(row);
                setDraft(row.display === "—" ? "" : row.display);
              }}
            />
            {result.extraction?.parse_warnings.map((warning, index) => (
              <p key={index} className="warn">{warning}</p>
            ))}
          </section>

          <section className="panel">
            <h2>
              Conflicts <Provenance kind="rules" />
            </h2>
            {result.resolution?.conflicts.length ? (
              result.resolution.conflicts.map((hit, index) => (
                <article key={index} className={`conflict sev-${hit.severity}`}>
                  <header>
                    <SeverityChip severity={hit.severity} />
                    <span className="conflict-rule">{hit.rule_label}</span>
                  </header>
                  <p>{hit.explanation}</p>
                  <dl className="conflict-ref">
                    <div>
                      <dt>{hit.matched_record_label}</dt>
                      <dd><code>{hit.matched_record_id}</code></dd>
                    </div>
                    <div><dt>Checked against</dt><dd>{hit.matched_field_label}</dd></div>
                    <div><dt>Matched</dt><dd>{hit.matched_value}</dd></div>
                  </dl>
                </article>
              ))
            ) : (
              <p className={result.resolution?.checked_party_names.length ? "cleared" : "warn"}>
                {result.resolution?.checked_party_names.length
                  ? `No conflicts. Checked: ${result.resolution.checked_party_names.join(", ")}.`
                  : "Nothing was checked — this is not a clearance."}
              </p>
            )}
            {result.resolution?.completeness_notes.map((note_, index) => (
              <p key={index} className="muted small">{note_}</p>
            ))}
          </section>

          {result.dispatch && (
            <section className="panel">
              <h2>
                Dispatch <Provenance kind="rules" />
              </h2>
              <p className="routing">{result.dispatch.routing_reason}</p>
              {detail.attorney && (
                <p className="attorney">
                  <strong>{detail.attorney.name}</strong> · {detail.attorney.current_load}/
                  {detail.attorney.capacity} matters
                </p>
              )}
              {result.dispatch.matter_stub && (
                <div className="stub">
                  <span className="stub-label">Matter stub</span>
                  <strong>{result.dispatch.matter_stub.caption}</strong>
                </div>
              )}
              {result.dispatch.acknowledgment && (
                <details className="ack">
                  <summary>
                    Acknowledgment draft
                    <span className={`ack-status ack-${result.dispatch.acknowledgment.status}`}>
                      {result.dispatch.acknowledgment.status.replace(/_/g, " ")}
                    </span>
                  </summary>
                  <pre>{result.dispatch.acknowledgment.body}</pre>
                  <p className="muted small">
                    Templated, not generated. This system has no way to send it.
                  </p>
                </details>
              )}
            </section>
          )}

          <section className="panel review">
            <h2>Review</h2>
            {editCount > 0 && (
              <ul className="pending-edits">
                {Object.entries(edits).map(([path, e]) => (
                  <li key={path}>
                    <code>{path.replace("extraction.", "")}</code>
                    <span className="was">{e.original ?? "—"}</span>
                    <span aria-hidden="true">→</span>
                    <span className="now">{e.corrected}</span>
                  </li>
                ))}
              </ul>
            )}
            <textarea
              placeholder="Note for the audit trail"
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
            {error && <p className="warn">{error}</p>}
            <div className="review-actions">
              <button
                className="btn btn-approve"
                disabled={busy}
                onClick={() => submit(editCount ? "approved_with_edits" : "approved")}
              >
                {editCount ? `Approve with ${editCount} correction${editCount > 1 ? "s" : ""}` : "Approve"}
              </button>
              <button className="btn btn-reject" disabled={busy} onClick={() => submit("rejected")}>
                Reject
              </button>
            </div>
            <p className="muted small">
              Approving marks the draft <strong>approved, not sent</strong>. Nothing leaves this system.
            </p>
            {detail.review_log.length > 0 && (
              <ol className="log">
                {detail.review_log.map((entry, index) => (
                  <li key={index}>
                    <strong>{entry.outcome.replace(/_/g, " ")}</strong> by {entry.reviewer}
                    {entry.note && <> — “{entry.note}”</>}
                    {entry.edits.map((e, i) => (
                      <div key={i} className="log-edit">
                        <code>{e.field_path.replace("extraction.", "")}</code>{" "}
                        <span className="was">{e.original_value ?? "—"}</span> →{" "}
                        <span className="now">{e.corrected_value}</span>
                      </div>
                    ))}
                  </li>
                ))}
              </ol>
            )}
          </section>
        </div>
      </div>

      {editing && (
        <div className="modal-scrim" onClick={() => setEditing(null)}>
          <div className="modal" onClick={(event) => event.stopPropagation()}>
            <h3>Correct {editing.label}</h3>
            <p className="muted small">
              The model read <span className="was">{editing.display}</span>. Your correction is
              recorded as human-asserted and keeps the original.
            </p>
            <input
              autoFocus
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  setEdits({ ...edits, [editing.id]: { original: editing.display, corrected: draft } });
                  setEditing(null);
                }
                if (event.key === "Escape") setEditing(null);
              }}
            />
            <div className="review-actions">
              <button
                className="btn btn-approve"
                onClick={() => {
                  setEdits({ ...edits, [editing.id]: { original: editing.display, corrected: draft } });
                  setEditing(null);
                }}
              >
                Stage correction
              </button>
              <button className="btn" onClick={() => setEditing(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
