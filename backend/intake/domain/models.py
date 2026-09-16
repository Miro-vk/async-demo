"""Typed models every pipeline stage reads and writes.

Pure domain module: no FastAPI, no DB driver, no HTTP client. `tests/test_layering.py`
enforces that mechanically.

The central type is `TracedField`: no extracted value exists in this system without a
confidence score and a pointer back to the characters it came from.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, computed_field

from intake.domain import confidence as _confidence

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
    occurrences: int = 1
    """How many times the quote appears in the source. More than one means the
    span is located but not unique -- we show the first, and the reviewer should
    know there were others."""

    derived: bool = False
    """True when the model supplied no quote and the system located the extracted
    value itself in the source text. The evidence is real -- those characters are
    in the email -- but the model never demonstrated it was reading them, so this
    scores below a quote the model actually produced."""

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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def explanation(self) -> str:
        """The arithmetic behind `confidence`, as one readable line.

        Served rather than recomputed in the front end: the factors live in
        domain.confidence, and a TypeScript copy of them would drift the first
        time somebody tuned one. A reviewer sees
        "self-reported 0.95 | span not_found x0.30 | ... = 0.28" instead of a bare
        number, which is the difference between a score and an explanation.
        """
        from intake.domain.spans import is_weak

        return _confidence.explain(
            self.self_reported, self.span, self.validator, is_weak(self.span)
        )


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
        """The email rendered as one string, headers included.

        This is the single source of truth for character offsets. The prompt shows
        the model exactly this text, spans index into exactly this text, and the UI
        highlights exactly this text. If those three ever diverge, a quote lifted
        from a header becomes ungroundable and a correct extraction gets penalised
        for evidence it really did have -- so prompts.py renders the email by
        calling this property rather than formatting its own copy.
        """
        return (
            f"From: {self.from_name} <{self.from_email}>\n"
            f"Received: {self.received_at.isoformat()}\n"
            f"Subject: {self.subject}\n\n"
            f"{self.body}"
        )


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
# Model output that could not be used
# --------------------------------------------------------------------------- #


class ParseFailure(Frozen):
    """The model returned something this stage could not turn into typed data.

    This is a value, not an exception. A malformed response is a routine event in a
    system built on a language model, and the correct response to it is to send the
    email to a human -- not to crash the pipeline and not to guess at what was meant.
    """

    stage: str
    reason: str
    raw_excerpt: str = ""


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
    parse_warnings: list[str] = []
    """Individual list entries the model returned malformed. They are dropped
    rather than guessed at, and recorded here so the drop is visible in the trace
    instead of looking like the model simply found nothing."""

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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rule_label(self) -> str:
        """`rule_id` as a person would say it.

        The id stays on the model because it is what the eval scores against and
        what a bug report should quote. It is not what a reviewer should be shown.
        """
        from intake.domain.labels import conflict_rule

        return conflict_rule(self.rule_id)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def matched_field_label(self) -> str:
        """Where in the record the rule looked, named as a place rather than a column."""
        from intake.domain.labels import matched_field

        return matched_field(self.matched_field)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def matched_record_label(self) -> str:
        """Which kind of record the id belongs to, so the id reads as a reference."""
        from intake.domain.labels import record_kind

        return record_kind(self.matched_record_kind)


class Resolution(Frozen):
    email_id: str
    client_matches: list[PartyMatch] = []
    matter_ids: list[str] = []
    conflicts: list[ConflictHit] = []

    checked_party_names: list[str] = []
    """Which names were actually run against the firm's records."""

    completeness_notes: list[str] = []
    """Why the check may be incomplete -- no party extracted, a name too vague to
    look up, an extraction that failed outright."""

    @property
    def is_vacuous(self) -> bool:
        """True when nothing was checked.

        A vacuous check produces an empty conflicts list, which looks exactly like
        a clean result and means the opposite. "We found no conflicts" and "we had
        no names to look for" must never render the same way, so policy treats this
        as a reason for review rather than as a clearance.
        """
        return not self.checked_party_names

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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def field_label(self) -> str:
        """`field_path` in English.

        The path stays machine-readable because the UI scrolls to it; this is what
        a person is shown. "extraction.parties[0].name" is the inside of the
        program, and putting it in front of a reviewer makes a careful system look
        careless.
        """
        from intake.domain.labels import field_heading

        return field_heading(self.field_path)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rule_label(self) -> str:
        """The name of the rule that fired, in English. Empty when no rule did."""
        from intake.domain.labels import conflict_rule

        return conflict_rule(self.rule_id) if self.rule_id else ""


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
# Execution trace
# --------------------------------------------------------------------------- #


class StageTrace(Frozen):
    """What actually happened when a stage ran.

    Carries the raw model response verbatim. Storing only the parsed result would
    make a disagreement between what the model said and what the system concluded
    impossible to investigate after the fact, which is the moment you most want to
    be able to investigate it.
    """

    email_id: str
    stage: str
    provider: str
    model: str
    cached: bool = False
    latency_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw_response: str = ""
    ok: bool = True
    failure_reason: str | None = None


# --------------------------------------------------------------------------- #
# A whole email, all four stages
# --------------------------------------------------------------------------- #


class PipelineResult(Frozen):
    """Everything the system concluded about one email, and how.

    Stage outputs are optional because a stage can fail without taking the run
    with it: a classification that could not be parsed still produces a Decision
    (review, citing the failure), and the trace still records what the model
    actually said. Storing the failures alongside the successes is what lets the
    UI show four stages with one of them red, rather than an empty page.
    """

    email_id: str
    classification: Classification | None = None
    extraction: Extraction | None = None
    resolution: Resolution | None = None
    dispatch: Dispatch | None = None
    decision: Decision
    traces: list[StageTrace] = []
    failures: list[ParseFailure] = []
    review: ReviewAction | None = None

    @property
    def awaiting_review(self) -> bool:
        return self.decision.action == DecisionAction.REVIEW and self.review is None

    @property
    def used_a_model(self) -> bool:
        """False when every stage was deterministic -- worth showing in the trace."""
        return any(t.provider not in ("deterministic",) for t in self.traces)


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
