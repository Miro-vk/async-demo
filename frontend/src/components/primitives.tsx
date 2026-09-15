import type { Action, Severity, TracedField } from "../types";

/* Status is never colour alone: every chip carries a glyph and a word. Two of the
   four status colours sit under 3:1 on the light surface by design, so the glyph
   is the accessible channel, not a decoration. */

const ACTION_META: Record<Action, { glyph: string; label: string; tone: string }> = {
  proceed: { glyph: "→", label: "Proceed", tone: "good" },
  review:  { glyph: "◆", label: "Needs review", tone: "warning" },
  stop:    { glyph: "■", label: "Filed, no action", tone: "muted" },
};

const SEVERITY_META: Record<Severity, { glyph: string; label: string; tone: string }> = {
  none:     { glyph: "·", label: "No conflict", tone: "muted" },
  weak:     { glyph: "◦", label: "Weak signal", tone: "warning" },
  possible: { glyph: "◆", label: "Possible conflict", tone: "serious" },
  probable: { glyph: "▲", label: "Probable conflict", tone: "critical" },
};

export function ActionChip({ action, compact }: { action: Action; compact?: boolean }) {
  const meta = ACTION_META[action];
  return (
    <span className={`chip tone-${meta.tone}`}>
      <span className="chip-glyph" aria-hidden="true">{meta.glyph}</span>
      {!compact && meta.label}
    </span>
  );
}

export function SeverityChip({ severity }: { severity: Severity }) {
  const meta = SEVERITY_META[severity];
  return (
    <span className={`chip tone-${meta.tone}`}>
      <span className="chip-glyph" aria-hidden="true">{meta.glyph}</span>
      {meta.label}
    </span>
  );
}

/** Where a value came from. Glyph plus word, same rule as status. */
export function Provenance({ kind }: { kind: "model" | "rules" | "human" }) {
  const meta = {
    model: { glyph: "◇", label: "Model" },
    rules: { glyph: "▪", label: "Rules" },
    human: { glyph: "✎", label: "Human" },
  }[kind];
  return (
    <span className={`prov prov-${kind}`}>
      <span aria-hidden="true">{meta.glyph}</span> {meta.label}
    </span>
  );
}

/** The arithmetic, not the number.
 *
 *  "0.28" is unreadable. "self-reported 0.95 | span not_found x0.30 = 0.28" tells a
 *  reviewer in one line that the model was sure and could not back it up. The
 *  string is computed server-side so the factors have one home. */
export function ConfidenceReceipt({ field }: { field: TracedField<unknown> }) {
  const pct = Math.round(field.confidence * 100);
  const band = field.confidence >= 0.75 ? "good" : field.confidence >= 0.5 ? "warning" : "critical";
  return (
    <div className="receipt">
      <div className="receipt-row">
        <div className={`meter meter-${band}`}>
          <div className="meter-fill" style={{ width: `${pct}%` }} />
        </div>
        <span className="receipt-value">{field.confidence.toFixed(2)}</span>
      </div>
      <code className="receipt-math">{field.explanation}</code>
    </div>
  );
}

export function SpanBadge({ field }: { field: TracedField<unknown> }) {
  if (!field.span) return <span className="span-badge sb-absent">no quote offered</span>;
  if (field.span.status === "not_found")
    return <span className="span-badge sb-missing">quote not in email</span>;
  if (field.span.derived)
    return <span className="span-badge sb-derived">located by system</span>;
  if (field.span.occurrences > 1)
    return <span className="span-badge sb-multi">quoted ×{field.span.occurrences}</span>;
  return <span className="span-badge sb-ok">quoted</span>;
}

export function StatTile({
  label, value, tone, glyph, active, onClick,
}: {
  label: string; value: number; tone: string; glyph: string;
  active?: boolean; onClick?: () => void;
}) {
  return (
    <button className={`tile tone-${tone} ${active ? "tile-on" : ""}`} onClick={onClick}>
      <span className="tile-glyph" aria-hidden="true">{glyph}</span>
      <span className="tile-value">{value}</span>
      <span className="tile-label">{label}</span>
    </button>
  );
}
