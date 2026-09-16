import { describe, expect, it } from "vitest";
import { segment, type Highlight } from "../components/SourceText";

/** The only non-trivial logic in the front end, and it got three things wrong
 *  before these existed: it dropped overlapping spans, it dropped spans nested
 *  inside others, and it starved spans that shared identical text. Each failure
 *  looked the same from the outside -- hovering a field lit nothing -- which is
 *  the one thing this view must never do. */

const TEXT = "From: Dana Vasquez\n\nWe signed with Meridian Mfg. Corp. on May 1, 2024.";

function highlight(id: string, quote: string, derived = false): Highlight {
  const start = TEXT.indexOf(quote);
  if (start === -1) throw new Error(`fixture error: ${quote} not in text`);
  return {
    id,
    label: id,
    span: { start, end: start + quote.length, quote, status: "verified", occurrences: 1, derived },
  };
}

function rendered(highlights: Highlight[]) {
  const { segments } = segment(TEXT, highlights);
  return segments;
}

function idsLit(highlights: Highlight[]): Set<string> {
  const lit = new Set<string>();
  for (const seg of rendered(highlights)) {
    for (const h of seg.covering ?? []) lit.add(h.id);
  }
  return lit;
}

describe("segment", () => {
  it("reconstructs the source text exactly", () => {
    const hs = [highlight("a", "Meridian Mfg. Corp."), highlight("b", "May 1, 2024")];
    expect(rendered(hs).map((s) => s.text).join("")).toBe(TEXT);
  });

  it("marks only the quoted characters", () => {
    const seg = rendered([highlight("a", "Meridian Mfg. Corp.")]);
    const marked = seg.filter((s) => s.highlight);
    expect(marked).toHaveLength(1);
    expect(marked[0].text).toBe("Meridian Mfg. Corp.");
  });

  it("keeps offsets aligned with the source", () => {
    for (const seg of rendered([highlight("a", "Dana Vasquez"), highlight("b", "May 1, 2024")])) {
      expect(TEXT.slice(seg.start, seg.end)).toBe(seg.text);
    }
  });

  it("lights every field when spans overlap", () => {
    const lit = idsLit([
      highlight("outer", "signed with Meridian Mfg. Corp. on May 1, 2024"),
      highlight("inner", "Meridian Mfg. Corp."),
    ]);
    expect(lit).toEqual(new Set(["outer", "inner"]));
  });

  it("lights every field when one span is nested in another", () => {
    const { shown } = segment(TEXT, [
      highlight("outer", "We signed with Meridian Mfg. Corp. on May 1, 2024."),
      highlight("nested", "May 1, 2024"),
    ]);
    expect(shown).toBe(2);
  });

  it("lights both fields when two cite identical text", () => {
    const lit = idsLit([highlight("first", "May 1, 2024"), highlight("second", "May 1, 2024")]);
    expect(lit).toEqual(new Set(["first", "second"]));
  });

  it("gives the contested characters to the more specific quote", () => {
    const seg = rendered([
      highlight("broad", "signed with Meridian Mfg. Corp."),
      highlight("precise", "Meridian Mfg. Corp."),
    ]);
    const owning = seg.find((s) => s.text === "Meridian Mfg. Corp.");
    expect(owning?.highlight?.id).toBe("precise");
  });

  it("reports how many spans had to be trimmed", () => {
    const { partial } = segment(TEXT, [
      highlight("broad", "signed with Meridian Mfg. Corp."),
      highlight("precise", "Meridian Mfg. Corp."),
    ]);
    expect(partial).toBe(1);
  });

  it("ignores spans that do not point into the text", () => {
    const bogus: Highlight = {
      id: "bad", label: "bad",
      span: { start: -1, end: -1, quote: "invented", status: "not_found", occurrences: 0, derived: false },
    };
    const { segments, shown } = segment(TEXT, [bogus]);
    expect(shown).toBe(0);
    expect(segments.map((s) => s.text).join("")).toBe(TEXT);
  });

  it("handles no highlights at all", () => {
    const { segments, shown } = segment(TEXT, []);
    expect(shown).toBe(0);
    expect(segments).toHaveLength(1);
    expect(segments[0].highlight).toBeNull();
  });
});
