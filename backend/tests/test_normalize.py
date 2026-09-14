"""Name normalization. Naive by design, but the naivety has to be the intended one."""

from __future__ import annotations

import pytest

from intake.domain.normalize import (
    email_domain,
    is_generic_domain,
    normalize_name,
    normalize_org_name,
    normalize_person_name,
    suffix_class,
    suffixes_compatible,
    token_similarity,
)


@pytest.mark.parametrize(
    "left,right",
    [
        ("Meridian Manufacturing Corp.", "Meridian Mfg. Corp."),
        ("Sterling Industries L.L.C.", "Sterling Industries, LLC"),
        ("Copperfield Dental Associates Inc.", "Copperfield Dental Associates Incorporated"),
        ("Oakhaven Staffing PLLC", "Oakhaven Staffing"),
        ("Quarry Lane Hospitality Group Corp.", "Quarry Lane Hosp. Grp. Corp."),
        ("O'Brien & Sons, Inc.", "OBrien and Sons Inc"),
    ],
)
def test_cosmetic_variants_reduce_to_the_same_string(left, right) -> None:
    assert normalize_name(left) == normalize_name(right)
    assert token_similarity(left, right) == 1.0


@pytest.mark.parametrize(
    "left,right",
    [
        ("Northwind Analytics LLC", "Northwind Logistics LLC"),
        ("Oakhaven Staffing PLLC", "Oakhaven Ventures PLLC"),
        ("John Smith", "Jane Smith"),
        ("Meridian Capital Partners LLC", "Meridian"),
    ],
)
def test_different_companies_do_not_collapse(left, right) -> None:
    assert normalize_name(left) != normalize_name(right)
    assert token_similarity(left, right) < 0.75


def test_a_bare_first_word_is_not_a_confident_match() -> None:
    """Dividing by the larger token count keeps 'Meridian' from scoring 1.0
    against 'Meridian Capital Partners'."""
    assert token_similarity("Meridian", "Meridian Capital Partners") < 0.5


def test_similarity_is_symmetric() -> None:
    for a, b in [("Meridian Mfg. Corp.", "Meridian Manufacturing Corp."),
                 ("Acme LLC", "Acme Holdings LLC")]:
        assert token_similarity(a, b) == token_similarity(b, a)


# --------------------------------------------------------------------------- #
# Entity types
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name,expected",
    [("Acme LLC", "LLC"), ("Acme L.L.C.", "LLC"), ("Acme Inc.", "INC"),
     ("Acme Incorporated", "INC"), ("Acme Corp.", "CORP"), ("Acme Corporation", "CORP"),
     ("Acme Ltd.", "LTD"), ("Acme Limited", "LTD"), ("Acme LLP", "LLP"),
     ("Acme Staffing", None), ("Dana Vasquez", None)],
)
def test_suffix_classes(name, expected) -> None:
    assert suffix_class(name) == expected


def test_a_dropped_suffix_is_compatible_with_any() -> None:
    """One of the planted traps is exactly this shape."""
    assert suffixes_compatible("Oakhaven Staffing", "Oakhaven Staffing PLLC")
    assert suffixes_compatible("Oakhaven Staffing PLLC", "Oakhaven Staffing")


def test_different_entity_types_are_not_compatible() -> None:
    """A regression guard on a real false positive: the matcher once reported
    'Summit Ridge Systems Ltd.' and 'Summit Ridge Systems LLP' as the same client
    with 0.95 confidence. They are two different registered entities."""
    assert not suffixes_compatible("Summit Ridge Systems Ltd.", "Summit Ridge Systems LLP")
    assert not suffixes_compatible("Talmadge Industries Corp.", "Talmadge Industries L.L.C.")
    assert not suffixes_compatible("Juniper Orthopedics PLLC", "Juniper Orthopedics Corp.")


def test_spelling_variants_within_a_type_stay_compatible() -> None:
    assert suffixes_compatible("Acme Inc.", "Acme Incorporated")
    assert suffixes_compatible("Acme L.L.C.", "Acme LLC")
    assert suffixes_compatible("Acme Corp.", "Acme Corporation")


# --------------------------------------------------------------------------- #
# People and email
# --------------------------------------------------------------------------- #


def test_person_names_drop_titles_and_honorifics() -> None:
    assert normalize_person_name("Dr. Priya Raghavan, Esq.") == "priya raghavan"
    assert normalize_person_name("Smith, John") == "john smith"


def test_comma_reordering_does_not_mangle_company_names() -> None:
    """'Meridian Capital, LLC' must not become 'LLC Meridian Capital'."""
    assert normalize_name("Meridian Capital, LLC") == "meridian capital"


def test_a_name_that_is_all_suffix_does_not_normalize_to_nothing() -> None:
    """An empty normalized form would match every other empty one."""
    assert normalize_name("The Company Ltd") != ""
    assert normalize_org_name("Co.") != ""


@pytest.mark.parametrize(
    "address,expected",
    [("a.b@Example.COM", "example.com"), ("x@sub.domain.co.uk", "sub.domain.co.uk"),
     ("not-an-email", None), (None, None), ("", None)],
)
def test_email_domain_extraction(address, expected) -> None:
    assert email_domain(address) == expected


def test_mailbox_providers_are_never_identity() -> None:
    """Treating gmail.com as a client's domain would match a prospective client
    against every gmail user in the database."""
    for domain in ["gmail.com", "outlook.com", "yahoo.com", "protonmail.com", "icloud.com"]:
        assert is_generic_domain(domain)
    assert not is_generic_domain("meridianmanufacturing.com")
