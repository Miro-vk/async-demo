"""The impure half: providers, caching, and the trace each stage leaves behind."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from intake.corpus.generate import generate_corpus
from intake.domain.enums import EmailClass
from intake.domain.models import Email, Extraction, ParseFailure
from intake.llm.client import (
    CacheMiss,
    CachingProvider,
    ReplayProvider,
    ResponseCache,
    ScriptedProvider,
    StubProvider,
    build_provider,
    cache_key,
)
from intake.llm.prompts import build_classification_request, build_extraction_request
from intake.pipeline.runner import run_classify, run_extract


@pytest.fixture
def email() -> Email:
    return Email(
        id="em-test",
        received_at=datetime(2026, 3, 1, 9, 0),
        from_name="Delphine Winslow",
        from_email="d.winslow@example.com",
        to_address="info@firm.example",
        subject="survey problem",
        body="We are under contract on 3784 Dunmore St and the survey came back wrong.",
    )


# --------------------------------------------------------------------------- #
# Traces
# --------------------------------------------------------------------------- #


def test_a_successful_stage_records_the_raw_response(email) -> None:
    raw = json.dumps({"label": "new_matter", "confidence": 0.9, "quote": "under contract"})
    result = run_classify(email, ScriptedProvider({"classify": raw}))
    assert not result.failed
    assert result.trace.ok
    assert result.trace.raw_response == raw
    assert result.trace.stage == "classify"
    assert result.trace.email_id == "em-test"


def test_a_malformed_response_is_returned_not_raised(email) -> None:
    """A bad response must not take the rest of the batch down with it."""
    result = run_classify(email, ScriptedProvider({"classify": "sorry, I can't help"}))
    assert result.failed
    assert isinstance(result.output, ParseFailure)
    assert result.trace.ok is False
    assert result.trace.failure_reason
    assert result.trace.raw_response == "sorry, I can't help"


def test_the_trace_records_which_provider_answered(email) -> None:
    result = run_classify(email, StubProvider())
    assert result.trace.provider == "stub"
    assert result.trace.model == "stub-heuristics"


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #


def test_cache_key_is_stable_and_prompt_sensitive(email) -> None:
    spec = build_classification_request(email)
    assert cache_key(spec, "m") == cache_key(spec, "m")
    assert cache_key(spec, "m") != cache_key(spec, "other-model")
    assert cache_key(spec, "m") != cache_key(build_extraction_request(email), "m")


def test_caching_provider_calls_through_once_then_serves_the_cache(email, tmp_path) -> None:
    inner = ScriptedProvider({"classify": '{"label": "new_matter", "confidence": 0.8}'})
    cache = ResponseCache(tmp_path / "cache.json")
    provider = CachingProvider(inner, cache, model="test-model")
    spec = build_classification_request(email)

    first = provider.complete(spec)
    second = provider.complete(spec)

    assert len(inner.calls) == 1, "second call should not reach the inner provider"
    assert first.text == second.text
    assert second.cached is True


def test_cache_survives_a_reload(email, tmp_path) -> None:
    path = tmp_path / "cache.json"
    inner = ScriptedProvider({"classify": '{"label": "unclear", "confidence": 0.4}'})
    spec = build_classification_request(email)
    CachingProvider(inner, ResponseCache(path), model="test-model").complete(spec)

    reloaded = ResponseCache(path)
    assert len(reloaded) == 1
    assert reloaded.get(cache_key(spec, "test-model"))


def test_replay_provider_is_loud_about_a_miss(email, tmp_path) -> None:
    """Hermetic runs must fail visibly rather than quietly falling back."""
    provider = ReplayProvider(ResponseCache(tmp_path / "empty.json"))
    with pytest.raises(CacheMiss, match="em-test"):
        provider.complete(build_classification_request(email))


def test_build_provider_falls_back_to_the_stub_without_a_key(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = build_provider("auto", cache_path=tmp_path / "none.json")
    assert provider.name == "stub"


def test_build_provider_prefers_the_cache_over_the_stub(email, tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    path = tmp_path / "cache.json"
    inner = ScriptedProvider({"classify": '{"label": "new_matter", "confidence": 0.8}'})
    CachingProvider(inner, ResponseCache(path)).complete(build_classification_request(email))
    assert build_provider("auto", cache_path=path).name == "replay"


def test_unknown_provider_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown provider mode"):
        build_provider("magic")


# --------------------------------------------------------------------------- #
# Stub
# --------------------------------------------------------------------------- #


def test_stub_identifies_itself_everywhere_it_appears(email) -> None:
    """The demo must never imply that keyword matching was a model."""
    response = StubProvider().complete(build_classification_request(email))
    assert response.provider == "stub"
    assert response.model == "stub-heuristics"
    assert "stub" in json.loads(response.text)["rationale"].lower()


def test_stub_only_ever_quotes_text_that_exists(email) -> None:
    """The stub is weak, but it is not allowed to be dishonest: a quote it emits
    must ground, or it would manufacture the exact failure mode the system is
    built to detect."""
    corpus = generate_corpus(1234)
    stub = StubProvider()
    for sample in corpus.emails[:20]:
        for build, run in [
            (build_classification_request, run_classify),
            (build_extraction_request, run_extract),
        ]:
            payload = json.loads(stub.complete(build(sample)).text)
            for quote in _quotes_in(payload):
                assert quote in sample.searchable_text, f"{sample.id}: invented {quote!r}"


def _quotes_in(payload) -> list[str]:
    found: list[str] = []
    if isinstance(payload, dict):
        quote = payload.get("quote")
        if isinstance(quote, str):
            found.append(quote)
        for value in payload.values():
            found.extend(_quotes_in(value))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(_quotes_in(item))
    return found


def test_stub_output_parses_cleanly_for_every_email_in_the_corpus() -> None:
    """A smoke test over the whole pipeline: no email in the corpus should make a
    stage throw, and the offline path must never produce a ParseFailure."""
    corpus = generate_corpus(20260517)
    stub = StubProvider()
    for sample in corpus.emails:
        classification = run_classify(sample, stub)
        extraction = run_extract(sample, stub)
        assert not classification.failed, f"{sample.id}: {classification.trace.failure_reason}"
        assert not extraction.failed, f"{sample.id}: {extraction.trace.failure_reason}"
        assert isinstance(classification.output.label.value, EmailClass)
        assert isinstance(extraction.output, Extraction)


# --------------------------------------------------------------------------- #
# Response block handling
# --------------------------------------------------------------------------- #


class _Block:
    """Stands in for an SDK content block without importing the SDK."""

    def __init__(self, type_: str, text: str | None = None, thinking: str | None = None):
        self.type = type_
        if text is not None:
            self.text = text
        if thinking is not None:
            self.thinking = thinking


def test_text_is_selected_by_block_type_not_position() -> None:
    """Regression guard. Current models think by default, so content[0] is
    routinely a ThinkingBlock; `content[0].text` raised AttributeError on every
    live call."""
    from intake.llm.client import text_from_blocks

    content = [
        _Block("thinking", thinking="Let me consider the sender..."),
        _Block("text", text='{"label": "new_matter"}'),
    ]
    assert text_from_blocks(content) == '{"label": "new_matter"}'


def test_multiple_text_blocks_are_joined() -> None:
    from intake.llm.client import text_from_blocks

    content = [_Block("text", text="part one"), _Block("text", text="part two")]
    assert text_from_blocks(content) == "part one\npart two"


def test_a_response_with_no_text_yields_an_empty_string() -> None:
    """A refusal, or thinking that hit the cap. The empty string becomes a
    ParseFailure downstream and the email goes to a human -- no crash."""
    from intake.llm.client import text_from_blocks

    assert text_from_blocks([_Block("thinking", thinking="...")]) == ""
    assert text_from_blocks([]) == ""
    assert text_from_blocks(None) == ""


def test_an_empty_response_routes_to_review_rather_than_crashing(email) -> None:
    from intake.domain.models import ParseFailure

    result = run_classify(email, ScriptedProvider({"classify": ""}))
    assert result.failed
    assert isinstance(result.output, ParseFailure)
    assert "empty" in result.output.reason
