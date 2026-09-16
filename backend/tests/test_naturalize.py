"""The naturalize pass is allowed to change the prose. It is not allowed to change
the facts. These tests pin that down, because a rewrite that quietly drops a party
name would corrupt ground truth and make every eval number a lie."""

from __future__ import annotations

from pathlib import Path

import pytest

from intake.corpus.generate import DEFAULT_SEED, generate_corpus
from intake.corpus.naturalize import (
    OVERLAY_PATH,
    Fact,
    apply_overlay,
    load_overlay,
    required_facts,
    template_hash,
    verify,
)
from intake.domain.enums import EmailClass


@pytest.fixture(scope="module")
def corpus():
    return generate_corpus(DEFAULT_SEED)


@pytest.fixture(scope="module")
def new_matter_email(corpus):
    truth = {g.email_id: g for g in corpus.ground_truth}
    for email in corpus.emails:
        gt = truth[email.id]
        if gt.label == EmailClass.NEW_MATTER and gt.amounts and gt.opposing_parties:
            return email, gt
    pytest.fail("no suitable new-matter email in corpus")


def test_required_facts_are_all_present_in_the_template(new_matter_email) -> None:
    email, gt = new_matter_email
    facts = required_facts(email, gt)
    assert facts
    source = f"{email.subject}\n\n{email.body}"
    assert all(f.satisfied_by(source) for f in facts)


def test_verify_accepts_an_honest_rewrite(new_matter_email) -> None:
    email, gt = new_matter_email
    facts = required_facts(email, gt)
    assert verify(email.subject, email.body, facts) == []


def test_verify_catches_a_dropped_party_name(new_matter_email) -> None:
    email, gt = new_matter_email
    facts = required_facts(email, gt)
    mangled = email.body.replace(gt.opposing_parties[0], "a company")
    missing = verify(email.subject, mangled, facts)
    assert any(m.startswith("opposing:") for m in missing)


def test_verify_catches_a_rounded_amount(new_matter_email) -> None:
    email, gt = new_matter_email
    facts = required_facts(email, gt)
    amount_fact = next(f for f in facts if f.label.startswith("amount:"))
    mangled = email.body
    for form in amount_fact.accepted_forms:
        mangled = mangled.replace(form, "a significant sum")
    assert amount_fact.label in verify(email.subject, mangled, facts)


def test_a_date_may_be_reformatted_but_not_changed() -> None:
    """Surface form is free; the date itself is not."""
    fact = Fact("date:2026-03-02", ["March 2, 2026", "3/2/2026", "2 March 2026"])
    assert fact.satisfied_by("we close on 3/2/2026")
    assert not fact.satisfied_by("we close on March 9, 2026")


# --------------------------------------------------------------------------- #
# Overlay application
# --------------------------------------------------------------------------- #


def test_overlay_rejects_a_rewrite_whose_template_has_changed(corpus) -> None:
    email = corpus.emails[0]
    overlay = {
        "entries": {
            email.id: {
                "template_hash": "0000000000000000",
                "subject": "anything",
                "body": "anything at all",
            }
        }
    }
    updated, results = apply_overlay(corpus, overlay)
    result = next(r for r in results if r.email_id == email.id)
    assert result.status == "stale"
    assert updated.emails[0].body == email.body, "stale rewrite must not be applied"


def test_overlay_rejects_a_rewrite_that_drops_a_fact(corpus, new_matter_email) -> None:
    email, gt = new_matter_email
    overlay = {
        "entries": {
            email.id: {
                "template_hash": template_hash(email),
                "subject": "Legal question",
                "body": "Hi, we have a dispute with a supplier. Please call me.",
            }
        }
    }
    updated, results = apply_overlay(corpus, overlay)
    result = next(r for r in results if r.email_id == email.id)
    assert result.status == "rejected_missing_facts"
    assert result.detail
    rewritten = next(e for e in updated.emails if e.id == email.id)
    assert rewritten.body == email.body
    assert rewritten.prose_source == "template"


def test_overlay_applies_a_faithful_rewrite(corpus, new_matter_email) -> None:
    email, gt = new_matter_email
    overlay = {
        "entries": {
            email.id: {
                "template_hash": template_hash(email),
                "subject": f"Rewritten: {email.subject}",
                "body": f"Completely different opening.\n\n{email.body}",
            }
        }
    }
    updated, results = apply_overlay(corpus, overlay)
    assert next(r for r in results if r.email_id == email.id).status == "applied"
    rewritten = next(e for e in updated.emails if e.id == email.id)
    assert rewritten.prose_source == "naturalized"
    assert rewritten.subject.startswith("Rewritten:")


# --------------------------------------------------------------------------- #
# The checked-in artifact
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not OVERLAY_PATH.exists(), reason="no naturalized prose checked in")
def test_checked_in_prose_still_matches_the_generator(corpus) -> None:
    """Fails loudly if someone edits the generator without refreshing the prose
    cache. The demo would still run -- it would just quietly go back to reading
    like a template."""
    _, results = apply_overlay(corpus, load_overlay(OVERLAY_PATH))
    broken = [r for r in results if r.status in ("stale", "rejected_missing_facts")]
    assert not broken, (
        "naturalized prose is out of date with the generator: "
        + ", ".join(f"{r.email_id}({r.status})" for r in broken)
        + ". Re-run `python -m intake.corpus.naturalize` for those ids."
    )


@pytest.mark.skipif(not OVERLAY_PATH.exists(), reason="no naturalized prose checked in")
def test_most_inquiry_mail_is_naturalized(corpus) -> None:
    _, results = apply_overlay(corpus, load_overlay(OVERLAY_PATH))
    applied = sum(1 for r in results if r.status == "applied")
    assert applied >= 30, f"only {applied} emails have naturalized prose"
