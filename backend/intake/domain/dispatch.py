"""Stage 4: route to an attorney, open a matter stub, draft an acknowledgment.

Deterministic. No model runs here, and the acknowledgment is a template rather
than generated prose -- a deliberate choice, not a shortcut.

A first contact from a prospective client is the single most dangerous piece of
writing a firm sends. Say too much and it reads as advice; use the wrong words and
it implies representation the firm has not agreed to; drift off-script and it
strays toward the unauthorised practice of law. Firms use vetted templates with
slots for exactly this reason, and the interesting engineering problem here is
deciding *whether* to send, not composing the sentence. A model-written
acknowledgment would be more impressive in a demo and worse in a law firm.

So the only model calls in the whole pipeline are classify and extract. The model
reads; the rules decide. That boundary is worth keeping visible.

Nothing here sends anything. There is no send path and no SENT status.
"""

from __future__ import annotations

from intake.domain.enums import DraftStatus, EmailClass, MatterStatus, PartyRole, PracticeArea
from intake.domain.models import (
    AcknowledgmentDraft,
    Attorney,
    Classification,
    Dispatch,
    Email,
    Extraction,
    MatterStub,
    Resolution,
)
from intake.domain.records import FirmRecords

PRACTICE_AREA_LABELS = {
    PracticeArea.COMMERCIAL_LITIGATION: "commercial litigation",
    PracticeArea.EMPLOYMENT: "employment",
    PracticeArea.REAL_ESTATE: "real estate",
    PracticeArea.INTELLECTUAL_PROPERTY: "intellectual property",
    PracticeArea.FAMILY: "family law",
    PracticeArea.PERSONAL_INJURY: "personal injury",
    PracticeArea.UNKNOWN: "general",
}

FIRM_NAME = "Vance & Brock LLP"


def dispatch(
    email: Email,
    classification: Classification | None,
    extraction: Extraction | None,
    resolution: Resolution | None,
    records: FirmRecords,
    attorneys: list[Attorney],
) -> Dispatch:
    label = classification.label.value if classification else None

    attorney, routing_reason = route(label, extraction, resolution, records, attorneys)
    stub = build_matter_stub(extraction) if label == EmailClass.NEW_MATTER else None
    draft = (
        draft_acknowledgment(email, extraction, attorney)
        if label in (EmailClass.NEW_MATTER, EmailClass.EXISTING_CLIENT)
        else None
    )

    return Dispatch(
        email_id=email.id,
        attorney_id=attorney.id if attorney else None,
        routing_reason=routing_reason,
        acknowledgment=draft,
        matter_stub=stub,
    )


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #


def route(
    label: EmailClass | None,
    extraction: Extraction | None,
    resolution: Resolution | None,
    records: FirmRecords,
    attorneys: list[Attorney],
) -> tuple[Attorney | None, str]:
    """Pick an attorney, and say why in a sentence a human can check."""
    by_id = {a.id: a for a in attorneys}

    if label == EmailClass.VENDOR_OR_SPAM:
        return None, "solicitation; no attorney assigned"
    if label == EmailClass.UNCLEAR:
        return None, "email does not fit an intake category; needs a human before routing"

    # Existing-client mail goes to whoever already owns the file. Reassigning it on
    # practice area would cut across a relationship the firm already has.
    if label == EmailClass.EXISTING_CLIENT and resolution and resolution.matter_ids:
        open_matters = [
            m
            for m in (records.matter(mid) for mid in resolution.matter_ids)
            if m is not None and m.status == MatterStatus.OPEN
        ]
        if open_matters:
            newest = max(open_matters, key=lambda m: (m.opened_on, m.id))
            attorney = by_id.get(newest.responsible_attorney_id)
            if attorney:
                return attorney, (
                    f"responsible attorney on {newest.id} ({newest.caption}), "
                    f"the client's most recently opened open matter"
                )

    area = extraction.matter_type.value if extraction else None
    if area is None or area == PracticeArea.UNKNOWN:
        return None, "no practice area determined, so no routing target"

    eligible = [a for a in attorneys if area in a.practice_areas]
    if not eligible:
        return None, f"no attorney covers {PRACTICE_AREA_LABELS.get(area, area.value)}"

    with_room = [a for a in eligible if a.has_headroom]
    if not with_room:
        return None, (
            f"every {PRACTICE_AREA_LABELS.get(area, area.value)} attorney is at "
            f"capacity ({len(eligible)} checked)"
        )

    # Most headroom first; id breaks ties so routing is reproducible.
    chosen = min(with_room, key=lambda a: (a.current_load - a.capacity, a.id))
    return chosen, (
        f"{PRACTICE_AREA_LABELS.get(area, area.value)}; {chosen.name} has the most "
        f"capacity ({chosen.current_load}/{chosen.capacity} matters)"
    )


# --------------------------------------------------------------------------- #
# Matter stub
# --------------------------------------------------------------------------- #


def build_matter_stub(extraction: Extraction | None) -> MatterStub | None:
    if extraction is None:
        return None

    client = next(
        (p.name.value for p in extraction.parties if p.role == PartyRole.PROSPECTIVE_CLIENT),
        None,
    )
    opposing = [p.name.value for p in extraction.opposing_parties if p.name.value]
    area = extraction.matter_type.value or PracticeArea.UNKNOWN

    if client and opposing:
        caption = f"{client} adv. {opposing[0]}"
    elif client:
        caption = f"{client} -- {PRACTICE_AREA_LABELS.get(area, 'general')} matter"
    else:
        caption = "Unidentified prospective client -- intake pending"

    return MatterStub(
        caption=caption,
        practice_area=area,
        jurisdiction=extraction.jurisdiction.value,
        prospective_client=client,
        opposing_parties=opposing,
    )


# --------------------------------------------------------------------------- #
# Acknowledgment
# --------------------------------------------------------------------------- #

ACKNOWLEDGMENT_TEMPLATE = """Dear {salutation},

Thank you for contacting {firm}. We have received your message{subject_clause} and
it has been passed to {attorney_clause} for review.

Someone will be in touch to arrange an initial conversation. Please note that we
have not yet agreed to represent you, and no attorney-client relationship is
created by this message or by your having written to us. Until we have completed
our intake checks and confirmed an engagement in writing, please do not send us
confidential information or documents.

If your matter involves a deadline in the near future, please tell us as soon as
possible so we can advise you on timing.

Regards,

Client Intake
{firm}"""


def draft_acknowledgment(
    email: Email, extraction: Extraction | None, attorney: Attorney | None
) -> AcknowledgmentDraft:
    """Fill the firm's template. Never sent -- an approval marks it
    APPROVED_NOT_SENT and there is no code path beyond that."""
    salutation = email.from_name.strip() or "Sir or Madam"
    subject_clause = f" regarding {email.subject.strip()}" if email.subject.strip() else ""

    if attorney:
        area = extraction.matter_type.value if extraction else None
        label = PRACTICE_AREA_LABELS.get(area) if area else None
        attorney_clause = (
            f"{attorney.name}, who handles {label} matters"
            if label
            else f"{attorney.name}"
        )
    else:
        attorney_clause = "the appropriate attorney"

    return AcknowledgmentDraft(
        subject=f"Re: {email.subject}".strip(),
        body=ACKNOWLEDGMENT_TEMPLATE.format(
            salutation=salutation,
            firm=FIRM_NAME,
            subject_clause=subject_clause,
            attorney_clause=attorney_clause,
        ),
        status=DraftStatus.DRAFT,
    )
