"""What we ask the model, and the shape we ask for it in.

Three instructions here are load-bearing rather than decorative:

  Quote, don't count. We ask for verbatim text and compute offsets ourselves. The
  prompt says copy exactly and says what happens if you don't.

  Omit rather than invent. A model that cannot find supporting text is told to leave
  the quote out. An absent quote costs some confidence; a fabricated one costs a lot
  more, and we can detect the difference.

  UNCLEAR is a finding, not a hedge. The classifier is told explicitly that low
  certainty about a new matter should be expressed as a low confidence on
  NEW_MATTER, not as an UNCLEAR label. Those two things route differently and
  collapsing them would destroy the distinction the review queue depends on.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from intake.domain.models import Email


@dataclass(frozen=True)
class PromptSpec:
    """A request, described without reference to any particular provider."""

    stage: str
    system: str
    user: str
    max_tokens: int = 2000
    temperature: float = 0.0
    source_text: str = ""
    email_id: str | None = None
    metadata: dict = field(default_factory=dict)


CLASSIFY_SYSTEM = """You triage incoming mail for a law firm's public info@ inbox.

Assign exactly one label:

  new_matter       someone outside the firm describing a legal problem they want
                   help with, whether or not they are already a client
  existing_client  correspondence about work the firm is already doing -- billing,
                   scheduling, documents, case updates
  vendor_or_spam   solicitations, marketing, recruiting, conference invitations,
                   phishing, and fraud attempts
  unclear          the email does not fit any of the above

On "unclear": use it only when the email genuinely does not belong in the other
three categories -- a grievance about the firm, a message with no discernible
purpose, a wrong-number email. Do NOT use it to express uncertainty about which
of the other labels applies. If you think it is probably a new matter but you are
not sure, the answer is new_matter with a low confidence, not unclear.

Note that an existing client raising a brand-new unrelated problem is a new_matter.
Note that a solicitation containing a real legal question is a new_matter.

Also return:
  confidence  0.0 to 1.0, your honest certainty in the label
  quote       a short verbatim span from the email that drove your decision. Copy
              it character for character from the text you were given. If nothing
              in the email clearly supports your label, omit the quote entirely --
              do not paraphrase and do not compose one.
  rationale   one sentence, for a human reading the audit trail

Return JSON only:
{"label": "...", "confidence": 0.0, "quote": "...", "rationale": "..."}"""


EXTRACT_SYSTEM = """You extract structured facts from a law firm intake email.

Return JSON only, in this shape:

{
  "parties":     [{"name": "...", "role": "prospective_client|opposing|third_party",
                   "is_organization": true, "email": null,
                   "confidence": 0.0, "quote": "..."}],
  "matter_type": {"value": "commercial_litigation|employment|real_estate|
                            intellectual_property|family|personal_injury|unknown",
                  "confidence": 0.0, "quote": "..."},
  "jurisdiction":{"value": "...", "confidence": 0.0, "quote": "..."},
  "key_dates":   [{"label": "...", "value": "YYYY-MM-DD",
                   "confidence": 0.0, "quote": "..."}],
  "amounts":     [{"label": "...", "value": 0, "currency": "USD",
                   "confidence": 0.0, "quote": "..."}],
  "summary":     "one or two sentences"
}

Rules:

Every quote must be copied verbatim from the email. Offsets are computed by
checking your quote against the source text, so a quote that does not appear
exactly will be detected and the field will be flagged as unsupported. Copy, do
not paraphrase, do not tidy up punctuation or whitespace.

If a fact is not in the email, leave it out or set its value to null. Do not infer
a jurisdiction from a party's name, do not infer a date from context, and do not
supply a party you were not given. An absent field is a correct answer; an
invented one is not.

If a fact is present but you are unsure you have read it correctly, include it
with a low confidence and the quote you are working from.

If the matter could plausibly belong to more than one practice area, pick the best
fit and lower the confidence to reflect the ambiguity.

Use "prospective_client" for the person or company seeking help, and "opposing"
for anyone they are or would be adverse to. Getting the opposing party right
matters more than getting every third party listed, because it drives a conflicts
check."""


def _email_block(email: Email) -> str:
    """Exactly the text spans are grounded against -- see Email.searchable_text.

    Do not reformat this. Showing the model one rendering and resolving quotes
    against another silently breaks span verification.
    """
    return email.searchable_text


def build_classification_request(email: Email) -> PromptSpec:
    return PromptSpec(
        stage="classify",
        system=CLASSIFY_SYSTEM,
        user=f"Classify this email.\n\n---\n{_email_block(email)}\n---",
        max_tokens=600,
        source_text=email.searchable_text,
        email_id=email.id,
    )


def build_extraction_request(email: Email) -> PromptSpec:
    return PromptSpec(
        stage="extract",
        system=EXTRACT_SYSTEM,
        user=f"Extract the facts from this email.\n\n---\n{_email_block(email)}\n---",
        max_tokens=2000,
        source_text=email.searchable_text,
        email_id=email.id,
    )
