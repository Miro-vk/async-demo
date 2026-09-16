import { useEffect, useRef } from "react";
import type { Span } from "../types";

export interface Highlight {
  id: string;
  label: string;
  span: Span;
}

interface Props {
  text: string;
  highlights: Highlight[];
  activeId: string | null;
  onHover: (id: string | null) => void;
}

export interface Segment {
  start: number;
  end: number;
  text: string;
  /** Which highlight's styling this run wears. */
  highlight: Highlight | null;
  /** Every highlight covering this run. Two fields can cite the same words, and
   *  hovering either one must light them -- ownership decides what it looks like,
   *  this decides what it responds to. */
  covering: Highlight[];
}

/** Slice the source into rendered segments.
 *
 *  Spans overlap constantly -- two fields cite the same sentence, or one quote
 *  encloses another -- and a DOM range cannot be in two places. Rather than drop
 *  the loser, the overlapping part is truncated so every field still highlights
 *  *something*. Dropping it entirely meant hovering a field lit up nothing at all,
 *  which reads as a broken link between the value and its evidence -- exactly the
 *  connection this view exists to make.
 *
 *  `partial` counts the fields whose highlight had to be shortened, so the header
 *  can say so rather than quietly showing less than it claims. */
export function segment(
  text: string,
  highlights: Highlight[],
): { segments: Segment[]; partial: number; shown: number } {
  const usable = highlights.filter(
    (h) => h.span.start >= 0 && h.span.end > h.span.start && h.span.end <= text.length,
  );

  // Assign each character to exactly one highlight, shortest span first.
  //
  // Spans overlap constantly -- a quote nested inside a longer quote, two fields
  // citing the same clause -- and a flat DOM cannot nest them. Earlier attempts
  // at this walked the spans in order and truncated or dropped the losers, which
  // silently left some fields with no highlight at all: hovering them lit nothing,
  // which reads as a broken link between a value and its evidence. Ownership per
  // character is easy to verify and gives every span a run, because a longer span
  // always has characters left over once a shorter one inside it has taken its own.
  //
  // Shortest-first means the more specific quote wins the contested characters.
  const owner = new Int32Array(text.length).fill(-1);
  usable
    .map((h, index) => ({ h, index }))
    .sort((a, b) =>
      a.h.span.end - a.h.span.start - (b.h.span.end - b.h.span.start) ||
      a.h.span.start - b.h.span.start)
    .forEach(({ h, index }) => {
      for (let c = h.span.start; c < h.span.end; c += 1) {
        if (owner[c] === -1) owner[c] = index;
      }
    });

  const segments: Segment[] = [];
  const owned = new Map<number, number>();
  const lit = new Set<Highlight>();
  let runStart = 0;

  const flush = (end: number) => {
    if (end <= runStart) return;
    const index = owner[runStart];
    const covering =
      index === -1
        ? []
        : usable.filter((h) => h.span.start <= runStart && h.span.end >= end);
    covering.forEach((h) => lit.add(h));
    segments.push({
      start: runStart,
      end,
      text: text.slice(runStart, end),
      highlight: index === -1 ? null : usable[index],
      covering,
    });
    if (index !== -1) owned.set(index, (owned.get(index) ?? 0) + (end - runStart));
  };

  for (let c = 1; c <= text.length; c += 1) {
    if (c === text.length || owner[c] !== owner[runStart]) {
      flush(c);
      runStart = c;
    }
  }

  const partial = usable.filter(
    (h, index) => (owned.get(index) ?? 0) < h.span.end - h.span.start,
  ).length;

  return { segments, partial, shown: lit.size };
}

export function SourceText({ text, highlights, activeId, onHover }: Props) {
  const { segments, partial, shown } = segment(text, highlights);
  const activeRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [activeId]);

  return (
    <div className="source">
      <div className="source-head">
        <span className="source-title">Source email</span>
        <span className="source-note">
          {shown} of {highlights.length} extracted values shown in place
          {partial > 0 && ` · ${partial} trimmed where quotes overlap`}
        </span>
      </div>
      <pre className="source-body">
        {segments.map((seg, index) =>
          seg.highlight ? (
            <mark
              key={index}
              ref={
                seg.covering.some((h) => h.id === activeId)
                  ? (node) => (activeRef.current = node)
                  : undefined
              }
              className={[
                "mk",
                `mk-${seg.highlight.span.derived ? "derived" : seg.highlight.span.status}`,
                seg.covering.some((h) => h.id === activeId) ? "mk-on" : "",
              ].join(" ")}
              data-label={
                seg.covering.find((h) => h.id === activeId)?.label ??
                seg.covering.map((h) => h.label).join(" + ")
              }
              onMouseEnter={() => onHover(seg.highlight!.id)}
              onMouseLeave={() => onHover(null)}
            >
              {seg.text}
            </mark>
          ) : (
            <span key={index}>{seg.text}</span>
          ),
        )}
      </pre>
    </div>
  );
}

/** A quote the model supplied that is nowhere in the email.
 *
 *  Shown struck through, beside the email rather than inside it, because there is
 *  no position in the source to put it -- which is exactly the finding. */
export function PhantomQuote({ quote }: { quote: string }) {
  return (
    <div className="phantom">
      <span className="phantom-glyph" aria-hidden="true">⚠</span>
      <div>
        <div className="phantom-title">Quoted text not found in this email</div>
        <div className="phantom-quote">“{quote}”</div>
      </div>
    </div>
  );
}
