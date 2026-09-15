import { useEffect, useRef } from "react";
import { humanize } from "../format";
import type { Counts, InboxItem } from "../types";
import { ActionChip, SeverityChip, StatTile } from "./primitives";

interface Props {
  items: InboxItem[];
  counts: Counts;
  filter: string;
  selected: number;
  onFilter: (filter: string) => void;
  onSelect: (index: number) => void;
  onOpen: (id: string) => void;
}

const FILTERS = [
  { key: "queue",   label: "Review queue", glyph: "◆", tone: "warning", of: (c: Counts) => c.pending_review },
  { key: "proceed", label: "Cleared",      glyph: "→", tone: "good",    of: (c: Counts) => c.proceed },
  { key: "stop",    label: "Filed",        glyph: "■", tone: "muted",   of: (c: Counts) => c.stop },
  { key: "all",     label: "All mail",     glyph: "▦", tone: "neutral", of: (c: Counts) => c.total },
];

export function Queue({ items, counts, filter, selected, onFilter, onSelect, onOpen }: Props) {
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listRef.current
      ?.querySelectorAll(".row")[selected]
      ?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  return (
    <div className="queue">
      {/* Counts, not a chart: four numbers with no trend and no parts-of-a-whole
          question behind them. A donut here would be decoration. */}
      <div className="tiles">
        {FILTERS.map((f) => (
          <StatTile
            key={f.key}
            label={f.label}
            value={f.of(counts)}
            tone={f.tone}
            glyph={f.glyph}
            active={filter === f.key}
            onClick={() => onFilter(f.key)}
          />
        ))}
      </div>

      <div className="queue-hint">
        <kbd>j</kbd> <kbd>k</kbd> move · <kbd>↵</kbd> open · <kbd>1</kbd>–<kbd>4</kbd> filter
      </div>

      <div className="rows" ref={listRef}>
        {items.length === 0 && <p className="empty">Nothing here.</p>}
        {items.map((item, index) => (
          <div
            key={item.email_id}
            className={`row ${index === selected ? "row-on" : ""} ${item.reviewed ? "row-done" : ""}`}
            onClick={() => { onSelect(index); onOpen(item.email_id); }}
            onMouseEnter={() => onSelect(index)}
          >
            <div className="row-main">
              <div className="row-top">
                <span className="row-from">{item.from_name}</span>
                {item.label && <span className="row-label">{humanize(item.label)}</span>}
                {item.reviewed && <span className="row-done-flag">reviewed</span>}
              </div>
              <div className="row-subject">{item.subject}</div>
              <div className="row-preview">{item.preview}</div>
            </div>
            <div className="row-side">
              {item.action && <ActionChip action={item.action} compact />}
              {item.conflict_severity && item.conflict_severity !== "none" && (
                <SeverityChip severity={item.conflict_severity} />
              )}
              {item.reason_count > 0 && (
                <span className="row-reasons">
                  {item.reason_count} reason{item.reason_count > 1 ? "s" : ""}
                  {item.top_reason && <em>{humanize(item.top_reason)}</em>}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
