"""The corpus is the demo's fixture. If it drifts, every downstream number moves."""

from __future__ import annotations

import pytest

from intake.corpus.generate import DEFAULT_SEED, generate_corpus
from intake.domain.enums import ClientType, DecisionAction, EmailClass, MatterStatus, PracticeArea


@pytest.fixture(scope="module")
def corpus():
    return generate_corpus(DEFAULT_SEED)


# --------------------------------------------------------------------------- #
# Reproducibility
# --------------------------------------------------------------------------- #


def test_same_seed_produces_identical_corpus() -> None:
    assert generate_corpus(7).model_dump_json() == generate_corpus(7).model_dump_json()


def test_different_seed_produces_different_corpus() -> None:
    assert generate_corpus(7).model_dump_json() != generate_corpus(8).model_dump_json()


def test_generation_does_not_depend_on_the_wall_clock(corpus) -> None:
    """Every timestamp is derived from the fixed EPOCH, so a corpus generated today
    and one generated next month are the same corpus."""
    assert {e.received_at for e in generate_corpus(7).emails} == {
        e.received_at for e in generate_corpus(7).emails
    }


# --------------------------------------------------------------------------- #
# Shape
# --------------------------------------------------------------------------- #


def test_corpus_has_the_documented_volumes(corpus) -> None:
    assert len(corpus.clients) == 200
    assert len(corpus.matters) == 150
    assert 48 <= len(corpus.emails) <= 55
    assert len(corpus.ground_truth) == len(corpus.emails)


def test_every_email_has_ground_truth(corpus) -> None:
    assert {e.id for e in corpus.emails} == {g.email_id for g in corpus.ground_truth}


def test_referential_integrity(corpus) -> None:
    client_ids = {c.id for c in corpus.clients}
    attorney_ids = {a.id for a in corpus.attorneys}
    for matter in corpus.matters:
        assert matter.client_id in client_ids
        assert matter.responsible_attorney_id in attorney_ids


def test_closed_matters_have_a_close_date(corpus) -> None:
    for matter in corpus.matters:
        if matter.status == MatterStatus.CLOSED:
            assert matter.closed_on is not None
            assert matter.closed_on >= matter.opened_on
        else:
            assert matter.closed_on is None


def test_corporations_do_not_get_divorced(corpus) -> None:
    """A regression guard. The generator once produced captions reading
    'In re Marriage of Ironwood Aggregates Ltd.'"""
    by_id = {c.id: c for c in corpus.clients}
    personal_areas = {PracticeArea.FAMILY, PracticeArea.PERSONAL_INJURY}
    for matter in corpus.matters:
        if matter.practice_area in personal_areas:
            assert by_id[matter.client_id].client_type == ClientType.INDIVIDUAL, matter.caption


def test_every_email_is_non_empty(corpus) -> None:
    for email in corpus.emails:
        assert email.subject.strip()
        assert len(email.body.strip()) > 40
        assert "@" in email.from_email


def test_new_matter_emails_cover_every_practice_area(corpus) -> None:
    areas = {
        g.practice_area
        for g in corpus.ground_truth
        if g.label == EmailClass.NEW_MATTER and g.practice_area
    }
    assert areas == set(PracticeArea) - {PracticeArea.UNKNOWN}


# --------------------------------------------------------------------------- #
# Abstention material
# --------------------------------------------------------------------------- #


def test_corpus_contains_deliberately_ambiguous_mail(corpus) -> None:
    ambiguous = [g for g in corpus.ground_truth if g.is_ambiguous]
    assert 5 <= len(ambiguous) <= 8
    assert all(g.expected_action == DecisionAction.REVIEW for g in ambiguous)


def test_unclear_is_a_label_not_just_an_abstention(corpus) -> None:
    """UNCLEAR is something the classifier asserts. It must exist in the corpus
    independently of low-confidence abstention, or the distinction is untestable."""
    assert any(g.label == EmailClass.UNCLEAR for g in corpus.ground_truth)


def test_spam_terminates_rather_than_queueing_for_review(corpus) -> None:
    spam = [g for g in corpus.ground_truth if g.label == EmailClass.VENDOR_OR_SPAM]
    assert spam
    assert all(g.expected_action == DecisionAction.STOP for g in spam)


# --------------------------------------------------------------------------- #
# Conflict traps
# --------------------------------------------------------------------------- #


EXPECTED_TRAPS = {
    "CORP_NAME_VARIANT": 3,
    "ADVERSE_PARTY_CLOSED_MATTER": 2,
    "EMAIL_DOMAIN_ONLY": 2,
}


def test_all_three_trap_kinds_are_planted(corpus) -> None:
    counts: dict[str, int] = {}
    for gt in corpus.ground_truth:
        for rule in gt.planted_trap_rules:
            counts[rule] = counts.get(rule, 0) + 1
    assert counts == EXPECTED_TRAPS


def test_traps_point_at_records_that_actually_exist(corpus) -> None:
    known = {c.id for c in corpus.clients} | {m.id for m in corpus.matters}
    trapped = [g for g in corpus.ground_truth if g.planted_trap_rules]
    assert trapped
    for gt in trapped:
        assert gt.trap_record_ids, f"{gt.email_id} has a trap with no referenced record"
        for record_id in gt.trap_record_ids:
            assert record_id in known, f"{gt.email_id} points at missing record {record_id}"


def test_every_trap_expects_human_review(corpus) -> None:
    for gt in corpus.ground_truth:
        if gt.planted_trap_rules:
            assert gt.expected_action == DecisionAction.REVIEW


def test_name_variant_traps_are_not_all_the_same_shape(corpus) -> None:
    """Three traps that all drop a corporate suffix would only ever exercise one
    branch of the matcher."""
    variants = [
        g for g in corpus.ground_truth if "CORP_NAME_VARIANT" in g.planted_trap_rules
    ]
    shapes = {g.notes.rsplit("(", 1)[-1] for g in variants}
    assert len(shapes) == len(variants), f"variant traps repeat a shape: {shapes}"


def test_closed_matter_traps_really_reference_closed_matters(corpus) -> None:
    by_id = {m.id: m for m in corpus.matters}
    for gt in corpus.ground_truth:
        if "ADVERSE_PARTY_CLOSED_MATTER" in gt.planted_trap_rules:
            matter = by_id[gt.trap_record_ids[0]]
            assert matter.status == MatterStatus.CLOSED
            assert gt.prospective_client in matter.adverse_parties


def test_domain_only_traps_share_a_domain_but_not_a_name(corpus) -> None:
    by_id = {c.id: c for c in corpus.clients}
    emails = {e.id: e for e in corpus.emails}
    for gt in corpus.ground_truth:
        if "EMAIL_DOMAIN_ONLY" in gt.planted_trap_rules:
            client = by_id[gt.trap_record_ids[0]]
            sender_domain = emails[gt.email_id].from_email.split("@")[1]
            assert sender_domain in client.domains
            assert gt.prospective_client != client.display_name
