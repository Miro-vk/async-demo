"""Closed vocabularies shared by every pipeline stage.

Pure domain module: no FastAPI, no DB driver, no HTTP client.
"""

from __future__ import annotations

from enum import Enum


class EmailClass(str, Enum):
    """Stage 1 output. UNCLEAR is a positive assertion by the classifier that the
    email does not fit the other three -- it is NOT the same thing as abstention.
    Abstention is a separate decision made by `domain.policy` and can apply to any
    label, including a confidently-asserted NEW_MATTER."""

    NEW_MATTER = "new_matter"
    EXISTING_CLIENT = "existing_client"
    VENDOR_OR_SPAM = "vendor_or_spam"
    UNCLEAR = "unclear"


class PracticeArea(str, Enum):
    COMMERCIAL_LITIGATION = "commercial_litigation"
    EMPLOYMENT = "employment"
    REAL_ESTATE = "real_estate"
    INTELLECTUAL_PROPERTY = "intellectual_property"
    FAMILY = "family"
    PERSONAL_INJURY = "personal_injury"
    UNKNOWN = "unknown"


class SpanStatus(str, Enum):
    """Did we manage to ground the model's quote in the source text?

    The model is asked for a verbatim quote, never for character offsets -- offsets
    are computed here. A quote we cannot locate is the signal that the value may be
    invented, so NOT_FOUND caps confidence and routes to review.
    """

    VERIFIED = "verified"          # exact substring match
    NORMALIZED = "normalized"      # matched after collapsing whitespace
    NOT_FOUND = "not_found"        # model quoted text that is not in the email
    ABSENT = "absent"              # model offered no quote at all


class ValidatorStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class PartyRole(str, Enum):
    PROSPECTIVE_CLIENT = "prospective_client"
    OPPOSING = "opposing"
    THIRD_PARTY = "third_party"
    UNKNOWN = "unknown"


class ClientType(str, Enum):
    INDIVIDUAL = "individual"
    ORGANIZATION = "organization"


class MatterStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class ConflictSeverity(str, Enum):
    """Ordered. Anything at POSSIBLE or above blocks automatic dispatch."""

    NONE = "none"
    WEAK = "weak"           # e.g. shared email domain only
    POSSIBLE = "possible"   # normalized-name or token-set match
    PROBABLE = "probable"   # exact adverse-party match

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {
    ConflictSeverity.NONE: 0,
    ConflictSeverity.WEAK: 1,
    ConflictSeverity.POSSIBLE: 2,
    ConflictSeverity.PROBABLE: 3,
}


class MatchMethod(str, Enum):
    EXACT = "exact"
    NORMALIZED = "normalized"
    STEM_ONLY = "stem_only"   # same distinguishing words, different entity suffix
    TOKEN_SET = "token_set"
    EMAIL_ADDRESS = "email_address"
    EMAIL_DOMAIN = "email_domain"


class DecisionAction(str, Enum):
    PROCEED = "proceed"   # auto-dispatch; draft is still never sent
    REVIEW = "review"     # human review queue
    STOP = "stop"         # terminal, no further stages (spam)


class ReviewReasonCode(str, Enum):
    """Why a human is being asked. Every reason cites a field path or a rule id."""

    LOW_CONFIDENCE_CLASSIFICATION = "low_confidence_classification"
    CLASSIFIER_SAID_UNCLEAR = "classifier_said_unclear"
    LOW_CONFIDENCE_FIELD = "low_confidence_field"
    UNVERIFIED_SPAN = "unverified_span"
    VALIDATOR_FAILED = "validator_failed"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    AMBIGUOUS_MATTER_TYPE = "ambiguous_matter_type"
    CONFLICT_HIT = "conflict_hit"
    CONFLICT_CHECK_VACUOUS = "conflict_check_vacuous"
    PARTIAL_PARSE = "partial_parse"
    NO_ATTORNEY_AVAILABLE = "no_attorney_available"
    MODEL_PARSE_FAILURE = "model_parse_failure"


class DraftStatus(str, Enum):
    """There is no SENT state anywhere in this system, by design."""

    DRAFT = "draft"
    APPROVED_NOT_SENT = "approved_not_sent"
    REJECTED = "rejected"


class ReviewOutcome(str, Enum):
    APPROVED = "approved"
    APPROVED_WITH_EDITS = "approved_with_edits"
    REJECTED = "rejected"
