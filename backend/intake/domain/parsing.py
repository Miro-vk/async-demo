"""Turning a model's text response into a dict, tolerantly but without guessing.

Models wrap JSON in code fences, preface it with "Here is the analysis:", and
occasionally emit trailing commentary. Those are formatting noise and we absorb
them. What we never do is repair the JSON itself -- a response we cannot parse
cleanly becomes a ParseFailure and the email goes to a human.
"""

from __future__ import annotations

import json
from typing import Any

MAX_EXCERPT_CHARS = 400


def strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.splitlines()
    lines = lines[1:]  # drop the opening ``` or ```json
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _first_json_object(text: str) -> str | None:
    """Slice out the first balanced {...} block, ignoring braces inside strings."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def parse_model_json(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    """Return (data, error). Exactly one of the two is None."""
    if raw is None or not raw.strip():
        return None, "model returned an empty response"

    candidate = strip_code_fences(raw)
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        block = _first_json_object(candidate)
        if block is None:
            # Distinguish "the model wrote prose" from "the model ran out of
            # tokens mid-object". The second is a max_tokens problem, not a
            # prompting problem, and the two get fixed in different places.
            if "{" in candidate:
                return None, "response appears truncated (unterminated JSON object)"
            return None, "no JSON object found in the response"
        try:
            data = json.loads(block)
        except json.JSONDecodeError as exc:
            return None, f"response is not valid JSON ({exc.msg} at position {exc.pos})"

    if not isinstance(data, dict):
        return None, f"expected a JSON object, got {type(data).__name__}"
    return data, None


def excerpt_for_failure(raw: str) -> str:
    text = (raw or "").strip()
    if len(text) <= MAX_EXCERPT_CHARS:
        return text
    return text[:MAX_EXCERPT_CHARS] + "..."


def as_float(value: Any) -> float | None:
    """Accept 0.9, "0.9", and "90%" -- reject anything else rather than coercing."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().rstrip("%")
        try:
            number = float(text)
        except ValueError:
            return None
        return number / 100 if value.strip().endswith("%") else number
    return None


def as_str(value: Any) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return None
