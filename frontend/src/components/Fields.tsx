import type { Extraction, TracedField } from "../types";
import { humanize } from "../format";
import { ConfidenceReceipt, SpanBadge } from "./primitives";

export interface FieldRow {
  id: string;
  label: string;
  display: string;
  field: TracedField<unknown>;
  editable: boolean;
}

/** Flatten an extraction into rows the trace can list and the source can point at.
 *  Ids match the field_path vocabulary policy uses in its reasons, so a review
 *  reason can link straight to the field it is complaining about. */
export function fieldRows(extraction: Extraction): FieldRow[] {
  const rows: FieldRow[] = [
    {
      id: "extraction.matter_type",
      label: "Practice area",
      display: humanize(extraction.matter_type.value),
      field: extraction.matter_type,
      editable: true,
    },
    {
      id: "extraction.jurisdiction",
      label: "Jurisdiction",
      display: extraction.jurisdiction.value ?? "—",
      field: extraction.jurisdiction,
      editable: true,
    },
  ];
  extraction.parties.forEach((party, index) =>
    rows.push({
      id: `extraction.parties[${index}].name`,
      label: humanize(party.role),
      display: party.name.value ?? "—",
      field: party.name,
      editable: true,
    }),
  );
  extraction.key_dates.forEach((date, index) =>
    rows.push({
      id: `extraction.key_dates[${index}].value`,
      label: date.label,
      display: String(date.value.value ?? "—"),
      field: date.value,
      editable: true,
    }),
  );
  extraction.amounts.forEach((amount, index) =>
    rows.push({
      id: `extraction.amounts[${index}].value`,
      label: amount.label,
      display:
        amount.value.value === null
          ? "—"
          : `${amount.currency} ${Number(amount.value.value).toLocaleString()}`,
      field: amount.value,
      editable: true,
    }),
  );
  return rows;
}

interface Props {
  rows: FieldRow[];
  activeId: string | null;
  flagged: Set<string>;
  onHover: (id: string | null) => void;
  onEdit: (row: FieldRow) => void;
}

export function Fields({ rows, activeId, flagged, onHover, onEdit }: Props) {
  return (
    <div className="fields">
      {rows.map((row) => (
        <div
          key={row.id}
          id={`field-${row.id}`}
          className={[
            "field",
            row.id === activeId ? "field-on" : "",
            flagged.has(row.id) ? "field-flagged" : "",
          ].join(" ")}
          onMouseEnter={() => onHover(row.id)}
          onMouseLeave={() => onHover(null)}
        >
          <div className="field-head">
            <span className="field-label">{row.label}</span>
            <SpanBadge field={row.field} />
          </div>
          <div className="field-value">
            <span title={row.display}>{row.display}</span>
            {row.editable && (
              <button className="field-edit" onClick={() => onEdit(row)}>
                Correct
              </button>
            )}
          </div>
          {row.field.validator_note && (
            <p className="field-note">{row.field.validator_note}</p>
          )}
          <ConfidenceReceipt field={row.field} />
        </div>
      ))}
    </div>
  );
}
