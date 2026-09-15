"""Talking to the model, and not talking to it.

Four providers, one interface:

  AnthropicProvider  the real thing
  CachingProvider    wraps another provider and memoises by prompt hash
  ReplayProvider     cache only; a miss is an error. Hermetic demo runs.
  StubProvider       NOT A MODEL. A regex-and-keyword stand-in so the pipeline,
                     the review queue, and the UI can be exercised with no API key.
                     Its output is clearly marked as `stub` everywhere it appears,
                     including in the stored trace, because a demo that quietly
                     passes off keyword matching as model output would be lying to
                     whoever is watching it.

Caching exists because a seeded corpus is only half of reproducibility. With the
cache populated, the same demo produces the same output on every run, at no cost
and with no network.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from intake.llm.prompts import PromptSpec
from intake.paths import LLM_CACHE_PATH

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_CACHE_PATH = LLM_CACHE_PATH


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    provider: str
    cached: bool = False
    latency_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None


class CacheMiss(RuntimeError):
    pass


class Provider(Protocol):
    name: str

    def complete(self, spec: PromptSpec) -> LLMResponse: ...


def cache_key(spec: PromptSpec, model: str) -> str:
    payload = f"{model}\x00{spec.system}\x00{spec.user}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #


class ResponseCache:
    """A JSON file of prompt hash -> response text. Small enough to check in."""

    def __init__(self, path: Path = DEFAULT_CACHE_PATH) -> None:
        self.path = path
        self._entries: dict[str, dict] = {}
        if path.exists():
            self._entries = json.loads(path.read_text(encoding="utf-8")).get("entries", {})

    def get(self, key: str) -> str | None:
        entry = self._entries.get(key)
        return entry["text"] if entry else None

    def put(self, key: str, spec: PromptSpec, model: str, text: str) -> None:
        self._entries[key] = {
            "stage": spec.stage,
            "email_id": spec.email_id,
            "model": model,
            "text": text,
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"entries": dict(sorted(self._entries.items()))}
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def __len__(self) -> int:
        return len(self._entries)


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self.model = model
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            import anthropic  # imported lazily: the domain tests never need the SDK

            self._client = anthropic.Anthropic()
        return self._client

    def complete(self, spec: PromptSpec) -> LLMResponse:
        client = self._ensure_client()
        started = time.monotonic()
        message = client.messages.create(
            model=self.model,
            max_tokens=spec.max_tokens,
            system=spec.system,
            output_config={"effort": spec.effort},
            messages=[{"role": "user", "content": spec.user}],
        )
        elapsed = int((time.monotonic() - started) * 1000)
        return LLMResponse(
            text=message.content[0].text,
            model=self.model,
            provider=self.name,
            latency_ms=elapsed,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )


class CachingProvider:
    """Memoise another provider by prompt hash."""

    def __init__(self, inner: Provider, cache: ResponseCache, model: str = DEFAULT_MODEL) -> None:
        self.inner = inner
        self.cache = cache
        self.model = model
        self.name = f"cached:{inner.name}"

    def complete(self, spec: PromptSpec) -> LLMResponse:
        key = cache_key(spec, self.model)
        hit = self.cache.get(key)
        if hit is not None:
            return LLMResponse(text=hit, model=self.model, provider=self.inner.name, cached=True)
        response = self.inner.complete(spec)
        self.cache.put(key, spec, self.model, response.text)
        self.cache.save()
        return response


class ReplayProvider:
    """Cache-only. Used when a run must be hermetic and a miss should be loud."""

    name = "replay"

    def __init__(self, cache: ResponseCache, model: str = DEFAULT_MODEL) -> None:
        self.cache = cache
        self.model = model

    def complete(self, spec: PromptSpec) -> LLMResponse:
        hit = self.cache.get(cache_key(spec, self.model))
        if hit is None:
            raise CacheMiss(
                f"no cached response for {spec.stage} on {spec.email_id}. "
                f"Run with a live provider to populate data/llm_cache.json."
            )
        return LLMResponse(text=hit, model=self.model, provider=self.name, cached=True)


class ScriptedProvider:
    """Returns canned responses. For tests."""

    name = "scripted"

    def __init__(self, responses: dict[str, str], default: str = "{}") -> None:
        self.responses = responses
        self.default = default
        self.calls: list[PromptSpec] = []

    def complete(self, spec: PromptSpec) -> LLMResponse:
        self.calls.append(spec)
        key = f"{spec.stage}:{spec.email_id}"
        return LLMResponse(
            text=self.responses.get(key, self.responses.get(spec.stage, self.default)),
            model="scripted",
            provider=self.name,
        )


# --------------------------------------------------------------------------- #
# Offline stub
# --------------------------------------------------------------------------- #

_SPAM_CUES = [
    "unsubscribe", "click here", "banking details have changed", "storage quota",
    "no-cost audit", "15 minutes", "early registration", "restocking fee",
    "first three depositions", "candidates actively looking",
]
_EXISTING_CUES = ["invoice", "settlement letter", "our call", "mat-", "board meets", "AP is asking"]
_NEW_MATTER_CUES = {
    "supply agreement": "commercial_litigation",
    "breach of contract": "commercial_litigation",
    "purchase orders": "commercial_litigation",
    "terminated": "employment",
    "wrongful termination": "employment",
    "let go from": "employment",
    "retaliation": "employment",
    "under contract to purchase": "real_estate",
    "encroachment": "real_estate",
    "closing": "real_estate",
    "trademark": "intellectual_property",
    "cease and desist": "intellectual_property",
    "the mark": "intellectual_property",
    "divorce": "family",
    "my spouse": "family",
    "custody": "family",
    "rear-ended": "personal_injury",
    "rear ended": "personal_injury",
    "physical therapy": "personal_injury",
    "adjuster": "personal_injury",
}

_ORG_SUFFIX = r"(?:LLC|L\.L\.C\.|Inc\.|Incorporated|LLP|L\.L\.P\.|Corp\.|Corporation|Ltd\.|Limited|PLLC|Co\.)"
_ORG_RE = re.compile(rf"\b([A-Z][A-Za-z.&'-]*(?: [A-Z][A-Za-z.&'-]*){{0,4}} {_ORG_SUFFIX})")
_JURISDICTION_RE = re.compile(
    r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)* (?:County|Parish|District), [A-Z][a-z]+(?: [A-Z][a-z]+)*)"
)
_MONEY_RE = re.compile(r"\$\s?[\d,]+(?:\.\d{2})?[kKmM]?")
_DATE_RES = [
    re.compile(r"\b([A-Z][a-z]{2,8} \d{1,2}, \d{4})\b"),
    re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b"),
    re.compile(r"\b(\d{1,2} [A-Z][a-z]{2,8} \d{4})\b"),
]
_DATE_FORMATS = ["%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%d %B %Y", "%d %b %Y"]


class StubProvider:
    """Keyword and regex heuristics standing in for a model when no key is present.

    This is not an approximation of the model and is not presented as one. It exists
    so that `make demo` shows a working pipeline rather than an error, and so that
    the review queue has something to hold. Its confidence values are deliberately
    modest, which means a lot of its output correctly ends up in front of a human --
    the honest outcome for a system whose extractor is a pile of regexes.
    """

    name = "stub"

    def complete(self, spec: PromptSpec) -> LLMResponse:
        text = spec.source_text
        payload = (
            self._classify(text) if spec.stage == "classify" else self._extract(text)
        )
        return LLMResponse(
            text=json.dumps(payload), model="stub-heuristics", provider=self.name
        )

    # -- stage 1 ---------------------------------------------------------- #

    def _classify(self, text: str) -> dict:
        lowered = text.lower()

        for cue in _SPAM_CUES:
            if cue in lowered:
                return self._labelled("vendor_or_spam", 0.72, text, cue, "Solicitation cue present.")
        for cue in _EXISTING_CUES:
            if cue.lower() in lowered:
                return self._labelled(
                    "existing_client", 0.64, text, cue, "References existing firm work."
                )
        hits = [cue for cue in _NEW_MATTER_CUES if cue in lowered]
        if hits:
            certainty = 0.74 if len(hits) > 2 else 0.52
            return self._labelled(
                "new_matter", certainty, text, hits[0], f"{len(hits)} new-matter cue(s)."
            )
        return {
            "label": "unclear",
            "confidence": 0.35,
            "rationale": "No recognisable intake signal (stub heuristics).",
        }

    def _labelled(self, label: str, conf: float, text: str, cue: str, why: str) -> dict:
        return {
            "label": label,
            "confidence": conf,
            "quote": self._verbatim(text, cue),
            "rationale": f"{why} (stub heuristics)",
        }

    @staticmethod
    def _verbatim(text: str, needle: str) -> str | None:
        """Return the cue exactly as it is cased in the source, never as typed here."""
        index = text.lower().find(needle.lower())
        return text[index : index + len(needle)] if index != -1 else None

    # -- stage 2 ---------------------------------------------------------- #

    def _extract(self, text: str) -> dict:
        lowered = text.lower()

        area, area_quote, area_conf = "unknown", None, 0.3
        matched = [(cue, a) for cue, a in _NEW_MATTER_CUES.items() if cue in lowered]
        if matched:
            areas = {a for _, a in matched}
            cue, area = matched[0]
            area_quote = self._verbatim(text, cue)
            # Two candidate areas is exactly the ambiguity a human should resolve.
            area_conf = 0.7 if len(areas) == 1 else 0.45

        jurisdiction = _JURISDICTION_RE.search(text)
        parties = self._parties(text)

        return {
            "parties": parties,
            "matter_type": {"value": area, "confidence": area_conf, "quote": area_quote},
            "jurisdiction": {
                "value": jurisdiction.group(1) if jurisdiction else None,
                "confidence": 0.78 if jurisdiction else 0.2,
                "quote": jurisdiction.group(1) if jurisdiction else None,
            },
            "key_dates": self._dates(text),
            "amounts": self._amounts(text),
            "summary": "Extracted by offline stub heuristics, not by a model.",
        }

    def _parties(self, text: str) -> list[dict]:
        """Sender first, then organisations named in the body.

        The sender is the inquiring party and everyone else is provisionally
        adverse. That is a crude rule and it is wrong whenever someone writes on
        another party's behalf -- hence the low confidence, which is what sends
        these to a human rather than into an automatic conflicts clearance.
        """
        parties: list[dict] = []
        header, _, remainder = text.partition("\n")
        sender = header[len("From: ") :].split("<")[0].strip() if header.startswith("From: ") else ""

        # Organisations appearing in the From line or the signature block are the
        # sender's own; the rest of the body is where counterparties live.
        signature = "\n".join(text.rstrip().splitlines()[-4:])
        own_orgs = {m.group(1) for m in _ORG_RE.finditer(header)}
        own_orgs |= {m.group(1) for m in _ORG_RE.finditer(signature)}

        if sender:
            parties.append(
                {"name": sender, "role": "prospective_client", "is_organization": False,
                 "confidence": 0.55, "quote": sender}
            )
        for name in sorted(own_orgs):
            parties.append(
                {"name": name, "role": "prospective_client", "is_organization": True,
                 "confidence": 0.5, "quote": name}
            )
        for match in _ORG_RE.finditer(remainder):
            name = match.group(1)
            if name in own_orgs or any(p["name"] == name for p in parties):
                continue
            parties.append(
                {"name": name, "role": "opposing", "is_organization": True,
                 "confidence": 0.5, "quote": name}
            )
            if len(parties) >= 6:
                break
        return parties

    def _dates(self, text: str) -> list[dict]:
        found: list[dict] = []
        seen: set[str] = set()
        for regex in _DATE_RES:
            for match in regex.finditer(text):
                raw = match.group(1)
                parsed = self._parse_date(raw)
                if parsed is None or parsed.isoformat() in seen:
                    continue
                seen.add(parsed.isoformat())
                found.append(
                    {
                        "label": "mentioned date",
                        "value": parsed.isoformat(),
                        "confidence": 0.8,
                        "quote": raw,
                    }
                )
        return found[:4]

    @staticmethod
    def _parse_date(raw: str) -> date | None:
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        return None

    def _amounts(self, text: str) -> list[dict]:
        out: list[dict] = []
        for match in list(_MONEY_RE.finditer(text))[:4]:
            raw = match.group(0)
            out.append(
                {"label": "mentioned amount", "value": raw, "currency": "USD",
                 "confidence": 0.8, "quote": raw}
            )
        return out


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #


def build_provider(
    mode: str = "auto",
    model: str = DEFAULT_MODEL,
    cache_path: Path = DEFAULT_CACHE_PATH,
) -> Provider:
    """Pick a provider.

    auto   cached live calls if a key is present, otherwise replay from cache,
           otherwise the stub. The mode a demo machine will land on.
    live   always call the API (still cached)
    replay cache only; a miss raises
    stub   offline heuristics
    """
    cache = ResponseCache(cache_path)

    if mode == "stub":
        return StubProvider()
    if mode == "replay":
        return ReplayProvider(cache, model)
    if mode == "live":
        return CachingProvider(AnthropicProvider(model), cache, model)
    if mode != "auto":
        raise ValueError(f"unknown provider mode {mode!r}")

    if os.environ.get("ANTHROPIC_API_KEY"):
        return CachingProvider(AnthropicProvider(model), cache, model)
    if len(cache):
        return ReplayProvider(cache, model)
    return StubProvider()
