"""Field-level sanity checks.

These are the third input to the confidence score. They are deliberately shallow --
they catch a model that returned a date in 1873, a negative settlement amount, or
"Unknown" as a party name. They are not legal validation and they do not know
anything about the firm's records; that is the resolve stage's job.

Each returns (status, note). The note is written for a human reading the trace, so
it says what was wrong rather than naming a rule.
"""

from __future__ import annotations

import re
from datetime import date

from intake.domain.enums import ValidatorStatus

PASS = (ValidatorStatus.PASSED, None)

# Values models reach for when they have nothing. Treating these as real values is
# how a "party" of "Unknown" ends up in a conflict check.
PLACEHOLDERS = {
    "unknown", "n/a", "na", "none", "not specified", "not stated", "unspecified",
    "tbd", "unnamed", "redacted", "null", "the client", "the company", "my company",
    "opposing party", "the other party", "-", "--",
}

US_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho", "illinois",
    "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland",
    "massachusetts", "michigan", "minnesota", "mississippi", "missouri", "montana",
    "nebraska", "nevada", "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah",
    "vermont", "virginia", "washington", "west virginia", "wisconsin", "wyoming",
    "district of columbia",
}

# How far from the email's own date a mentioned date may plausibly sit.
MAX_YEARS_PAST = 60
MAX_YEARS_FUTURE = 5

MAX_PLAUSIBLE_AMOUNT = 1_000_000_000.0


def validate_party_name(name: str | None) -> tuple[ValidatorStatus, str | None]:
    if name is None:
        return ValidatorStatus.NOT_APPLICABLE, None
    cleaned = name.strip()
    if len(cleaned) < 2:
        return ValidatorStatus.FAILED, "party name is too short to identify anyone"
    if cleaned.lower() in PLACEHOLDERS:
        return ValidatorStatus.FAILED, f"'{cleaned}' is a placeholder, not a party"
    if not re.search(r"[A-Za-z]", cleaned):
        return ValidatorStatus.FAILED, "party name contains no letters"
    return PASS


def validate_jurisdiction(text: str | None) -> tuple[ValidatorStatus, str | None]:
    """Structural only: does this look like a US jurisdiction?

    We check the trailing component against a list of states rather than against
    anything the firm knows. A jurisdiction we do not recognise is not rejected --
    it is flagged, which lowers confidence and asks a human.
    """
    if text is None:
        return ValidatorStatus.NOT_APPLICABLE, None
    cleaned = text.strip()
    if not cleaned:
        return ValidatorStatus.FAILED, "empty jurisdiction"
    if cleaned.lower() in PLACEHOLDERS:
        return ValidatorStatus.FAILED, f"'{cleaned}' is a placeholder, not a place"

    tail = cleaned.split(",")[-1].strip().lower()
    if tail in US_STATES or cleaned.lower() in US_STATES:
        return PASS
    if re.search(r"\b(county|parish|district|circuit|borough)\b", cleaned, re.I):
        return ValidatorStatus.PASSED, "recognised as a court locality, state not matched"
    return ValidatorStatus.FAILED, f"'{cleaned}' does not resolve to a US state"


def validate_date(
    value: date | None, reference: date | None = None
) -> tuple[ValidatorStatus, str | None]:
    """A date far outside the email's own timeframe is almost always a parse error
    or a hallucinated year."""
    if value is None:
        return ValidatorStatus.NOT_APPLICABLE, None
    if reference is None:
        return PASS
    years = (value - reference).days / 365.25
    if years > MAX_YEARS_FUTURE:
        return (
            ValidatorStatus.FAILED,
            f"{value.isoformat()} is more than {MAX_YEARS_FUTURE} years after the email",
        )
    if -years > MAX_YEARS_PAST:
        return (
            ValidatorStatus.FAILED,
            f"{value.isoformat()} is more than {MAX_YEARS_PAST} years before the email",
        )
    return PASS


def validate_amount(value: float | None) -> tuple[ValidatorStatus, str | None]:
    if value is None:
        return ValidatorStatus.NOT_APPLICABLE, None
    if value <= 0:
        return ValidatorStatus.FAILED, "amount is zero or negative"
    if value > MAX_PLAUSIBLE_AMOUNT:
        return ValidatorStatus.FAILED, f"amount {value:,.0f} is implausibly large"
    return PASS
