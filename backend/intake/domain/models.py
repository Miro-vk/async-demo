"""Typed models every pipeline stage reads and writes.

Pure domain module: no FastAPI, no DB driver, no HTTP client. `tests/test_layering.py`
enforces that mechanically.

The central type is `TracedField`: no extracted value exists in this system without a
confidence score and a pointer back to the characters it came from.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from intake.domain.enums import (
    ClientType,
    ConflictSeverity,
    DecisionAction,
    DraftStatus,
    EmailClass,
    MatchMethod,
    MatterStatus,
    PartyRole,
    PracticeArea,
    ReviewOutcome,
    ReviewReasonCode,
    SpanStatus,
    ValidatorStatus,
)

T = TypeVar("T")


class Frozen(BaseModel):
    """Stage outputs are immutable once produced; a later stage may not quietly
    rewrite an earlier stage's findings."""

    model_config = ConfigDict(frozen=True)


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #


class Span(Frozen):
    """A character range in the source email, with the text it covers.

    Offsets are computed by `domain.spans` from a verbatim quote the model supplies.
    We never ask the model for offsets directly -- LLMs are unreliable at counting
    characters, and a computed offset cannot be hallucinated.
    """

    start: int
    end: int
    quote: str
    status: SpanStatus

    @property
    def located(self) -> bool:
        return self.status in (SpanStatus.VERIFIED, SpanStatus.NORMALIZED)


class TracedField(Frozen, Generic[T]):
    """A value plus everything a human needs to decide whether to trust it.

    `confidence` is composite, computed in domain code from three inputs:
      1. the model's self-report (weakly calibrated -- never used alone)
      2. whether the span could be grounded in the source text
      3. whether a field-specific validator accepted the value
    See `domain.confidence` for the combining rule.
    """

    value: T | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    self_reported: float | None = Field(default=None, ge=0.0, le=1.0)
    span: Span | None = None
    validator: ValidatorStatus = ValidatorStatus.NOT_APPLICABLE
    validator_note: str | None = None

    @property
    def is_present(self) -> bool:
        return self.value is not None


# --------------------------------------------------------------------------- #
# Inbox
# --------------------------------------------------------------------------- #


class Email(Frozen):
    id: str
    received_at: datetime
    from_name: str
    from_email: str
    to_address: str
    subject: str
    body: str
    prose_source: Literal["template", "naturalized"] = "template"

    @property
    def searchable_text(self) -> str:
        """Subject and body as one string. Spans are offsets into THIS, so the
        UI and the span grounder must agree on exactly one representation."""
        return f"{self.subject}\n\n{self.body}"


# --------------------------------------------------------------------------- #
# Firm records (the things we resolve against)
# --------------------------------------------------------------------------- #


class Attorney(Frozen):
    id: str
    name: str
    email: str
    practice_areas: list[PracticeArea]
    capacity: int
    current_load: int

    @property
    def has_headroom(self) -> bool:
        return self.current_load < self.capacity


class ClientRecord(Frozen):
    id: str
    display_name: str
    client_type: ClientType
    emails: list[str] = []
    domains: list[str] = []
    opened_on: date


class MatterRecord(Frozen):
    id: str
    client_id: str
    caption: str
    practice_area: PracticeArea
    status: MatterStatus
    opened_on: date
    closed_on: date | None = None
    responsible_attorney_id: str
    adverse_parties: list[str] = []


# --------------------------------------------------------------------------- #
# Stage 1: classify
# --------------------------------------------------------------------------- #


class Classification(Frozen):
    email_id: str
    label: TracedField[EmailClass]
    rationale: str


# --------------------------------------------------------------------------- #
# Stage 2: extract
# --------------------------------------------------------------------------- #


class Party(Frozen):
    name: TracedField[str]
    role: PartyRole
    email: str | None = None
    is_organization: bool = False


class KeyDate(Frozen):
    label: str
    value: TracedField[date]


class MonetaryAmount(Frozen):
    label: str
    value: TracedField[float]
    currency: str = "USD"


class Extraction(Frozen):
    email_id: str
    parties: list[Party] = []
    matter_type: TracedField[PracticeArea]
    jurisdiction: TracedField[str]
    key_dates: list[KeyDate] = []
    amounts: list[MonetaryAmount] = []
    summary: str = ""

    @property
    def opposing_parties(self) -> list[Party]:
        """The spec lists opposing party as its own field. It is stored as a role on
        `parties` instead, so a party never appears in two places with two
        confidences; this is the accessor."""
        return [p for p in self.parties if p.role == PartyRole.OPPOSING]

    @property
    def client_side_parties(self) -> list[Party]:
        return [p for p in self.parties if p.role == PartyRole.PROSPECTIVE_CLIENT]


# --------------------------------------------------------------------------- #
# Stage 3: resolve + conflicts
# --------------------------------------------------------------------------- #


class PartyMatch(Frozen):
    """One extracted party matched against one firm record."""

    inquiry_party: str
    inquiry_role: PartyRole
    client_id: str
    client_name: str
    method: MatchMethod
    score: float = Field(ge=0.0, le=1.0)


class ConflictHit(Frozen):
    """A reason the firm may not be free to take this matter.

    Carries the rule that fired and the exact record and field it fired on, because
    'the system flagged a conflict' is useless to the human who has to clear it.
    """

    rule_id: str
    severity: ConflictSeverity
    inquiry_party: str
    matched_record_kind: Literal["client", "matter"]
    matched_record_id: str
    matched_field: str
    matched_value: str
    score: float = Field(ge=0.0, le=1.0)
    explanation: str


class Resolution(Frozen):
    email_id: str
    client_matches: list[PartyMatch] = []
    matter_ids: list[str] = []
    conflicts: list[ConflictHit] = []

    @property
    def max_severity(self) -> ConflictSeverity:
        if not self.conflicts:
            return ConflictSeverity.NONE
        return max((c.severity for c in self.conflicts), key=lambda s: s.rank)


# --------------------------------------------------------------------------- #
# Stage 4: dispatch
# --------------------------------------------------------------------------- #


class MatterStub(Frozen):
    caption: str
    practice_area: PracticeArea
    jurisdiction: str | None
    prospective_client: str | None
    opposing_parties: list[str] = []


class AcknowledgmentDraft(Frozen):
    """A draft. This system has no send path and no SENT status, on purpose."""

    subject: str
    body: str
    status: DraftStatus = DraftStatus.DRAFT


class Dispatch(Frozen):
    email_id: str
    attorney_id: str | None
    routing_reason: str
    acknowledgment: AcknowledgmentDraft | None = None
    matter_stub: MatterStub | None = None


# --------------------------------------------------------------------------- #
# Abstention
# --------------------------------------------------------------------------- #


class Reason(Frozen):
    """Why the system is asking for a human. Always cites a field path or rule id."""

    code: ReviewReasonCode
    message: str
    field_path: str | None = None
    rule_id: str | None = None


class Decision(Frozen):
    action: DecisionAction
    reasons: list[Reason] = []

    @property
    def needs_human(self) -> bool:
        return self.action == DecisionAction.REVIEW


# --------------------------------------------------------------------------- #
# Review queue
# --------------------------------------------------------------------------- #


class FieldEdit(Frozen):
    """A reviewer correcting an extracted value. The original is retained -- an
    audit trail that silently overwrites the model's output is not an audit trail."""

    field_path: str
    original_value: str | None
    corrected_value: str | None
    edited_by: str
    edited_at: datetime


class ReviewAction(Frozen):
    email_id: str
    outcome: ReviewOutcome
    reviewer: str
    note: str = ""
    edits: list[FieldEdit] = []
    acted_at: datetime


# --------------------------------------------------------------------------- #
# Corpus + ground truth
# --------------------------------------------------------------------------- #


class GroundTruth(Frozen):
    """What the generator knows it planted. Never read by the pipeline -- only by
    the eval harness, which is why stage accuracy is measurable at all."""

    email_id: str
    label: EmailClass
    practice_area: PracticeArea | None = None
    jurisdiction: str | None = None
    prospective_client: str | None = None
    opposing_parties: list[str] = []
    key_dates: list[str] = []
    amounts: list[float] = []
    planted_trap_rules: list[str] = []
    trap_record_ids: list[str] = []  # client/matter ids the trap points at
    expected_action: DecisionAction
    is_ambiguous: bool = False
    notes: str = ""


class Corpus(Frozen):
    seed: int
    attorneys: list[Attorney]
    clients: list[ClientRecord]
    matters: list[MatterRecord]
    emails: list[Email]
    ground_truth: list[GroundTruth]
