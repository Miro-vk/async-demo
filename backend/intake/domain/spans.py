"""Grounding model-supplied quotes in the source text.

The model is asked for a verbatim quote, never for character offsets. LLMs cannot
count characters reliably, but they can copy text, and an offset computed here from
a quote that genuinely appears in the email cannot be hallucinated.

A quote we cannot find is the single most useful signal in the system: it means the
model produced a value it could not point at. That does not raise an exception -- it
produces a span with status NOT_FOUND, which caps confidence and routes the email to
a human. The hallucinated quote is preserved so a reviewer can see what was claimed.
"""

from __future__ import annotations

from intake.domain.enums import SpanStatus
from intake.domain.models import Span

# Quotes shorter than this are not evidence of anything -- "a" appears in every
# email. They ground, but the confidence rule treats them as weak.
WEAK_QUOTE_CHARS = 4


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Lowercase and collapse whitespace, keeping a map back to original offsets.

    Length-changing lowercase forms (a handful of Unicode characters) are left
    as-is so the map stays one-to-one.
    """
    chars: list[str] = []
    index_map: list[int] = []
    in_space = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if in_space:
                continue
            chars.append(" ")
            index_map.append(i)
            in_space = True
        else:
            lowered = ch.lower()
            chars.append(lowered if len(lowered) == 1 else ch)
            index_map.append(i)
            in_space = False
    return "".join(chars), index_map


def ground_quote(source: str, quote: str | None) -> Span | None:
    """Locate `quote` in `source` and return a span describing what happened.

    Returns None only when the model offered no quote at all. Every other outcome
    -- including a quote that is nowhere in the email -- returns a Span, because
    "the model claimed this text and it does not exist" is information a reviewer
    needs to see.
    """
    if quote is None:
        return None
    cleaned = quote.strip()
    if not cleaned:
        return None

    # Exact match first: the common case, and the only one that is unambiguous.
    start = source.find(cleaned)
    if start != -1:
        return Span(
            start=start,
            end=start + len(cleaned),
            quote=cleaned,
            status=SpanStatus.VERIFIED,
            occurrences=source.count(cleaned),
        )

    # Then tolerate whitespace and case differences -- a model that re-wraps a
    # quoted line has still pointed at real text.
    norm_source, index_map = _normalize_with_map(source)
    norm_quote, _ = _normalize_with_map(cleaned)
    pos = norm_source.find(norm_quote)
    if pos != -1 and norm_quote:
        original_start = index_map[pos]
        original_end = index_map[pos + len(norm_quote) - 1] + 1
        return Span(
            start=original_start,
            end=original_end,
            quote=source[original_start:original_end],
            status=SpanStatus.NORMALIZED,
            occurrences=norm_source.count(norm_quote),
        )

    return Span(
        start=-1, end=-1, quote=cleaned, status=SpanStatus.NOT_FOUND, occurrences=0
    )


def is_weak(span: Span | None) -> bool:
    """A span that is technically located but too short or too repeated to be
    meaningful evidence."""
    if span is None or not span.located:
        return False
    return len(span.quote.strip()) < WEAK_QUOTE_CHARS or span.occurrences > 3


def excerpt(source: str, span: Span | None, padding: int = 60) -> str:
    """Source text around a span, for showing a reviewer why a value was produced."""
    if span is None or not span.located:
        return ""
    start = max(0, span.start - padding)
    end = min(len(source), span.end + padding)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(source) else ""
    return f"{prefix}{source[start:end]}{suffix}"
