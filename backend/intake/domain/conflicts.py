"""Conflict rules.

No model is involved at any point here. A conflicts opinion that cannot be walked
through line by line is not usable by the person who has to sign off on it, so every
hit names the rule that fired, the exact record and field it fired on, and the
spelling that matched.

Three rules, matching the three shapes the corpus plants:

  ADVERSE_PARTY_IS_CLIENT       we would be acting against someone we act for
  PROSPECT_WAS_ADVERSE_PARTY    we would be acting for someone we acted against
  SENDER_DOMAIN_MATCHES_CLIENT  the email comes from a client's domain, but no
                                name matches -- weak, and never self-clearing

Severity is about how confident we are that the two names are the same entity, not
about how serious the conflict would be if they are. That second judgement belongs
to a human, and the system's job is to get it in front of one.
"""

from __future__ import annotations

from dataclasses import dataclass

from intake.domain.enums import ConflictSeverity, MatchMethod, MatterStatus, PartyRole
from intake.domain.models import ConflictHit, Email, Extraction
from intake.domain.normalize import email_domain, is_generic_domain, suffix_class
from intake.domain.records import FirmRecords

RULE_ADVERSE_PARTY_IS_CLIENT = "ADVERSE_PARTY_IS_CLIENT"
RULE_PROSPECT_WAS_ADVERSE_PARTY = "PROSPECT_WAS_ADVERSE_PARTY"
RULE_SENDER_DOMAIN_MATCHES_CLIENT = "SENDER_DOMAIN_MATCHES_CLIENT"

# A name that reduced to the same string is a strong signal; a name that merely
# shares most of its words, or that names a different entity type, is a lead worth
# a human's time rather than a finding.
STRONG_METHODS = {MatchMethod.EXACT, MatchMethod.NORMALIZED}

# Which party roles the rules below actually look up. A third party mentioned in
# passing drives nothing here, so policy must not hold its extraction confidence
# to the bar it applies to names that do. Exported so the two modules cannot drift.
CLIENT_SIDE_ROLES = {PartyRole.PROSPECTIVE_CLIENT, PartyRole.UNKNOWN}
OPPOSING_ROLES = {PartyRole.OPPOSING}
CONFLICT_RELEVANT_ROLES = CLIENT_SIDE_ROLES | OPPOSING_ROLES


def describe_match(method: MatchMethod, inquiry_name: str, record_name: str) -> str:
    """How the two names matched, in words a reviewer can check in one read."""
    if method == MatchMethod.EXACT:
        return "the same name as"
    if method == MatchMethod.NORMALIZED:
        return "the same name, differently punctuated or abbreviated, as"
    if method == MatchMethod.STEM_ONLY:
        left, right = suffix_class(inquiry_name), suffix_class(record_name)
        return (
            f"the same distinguishing name as, but a different entity type "
            f"({left} vs {right}) -- these may be affiliated companies, which this "
            f"system cannot determine, or unrelated ones sharing a name:"
        )
    return "a partial word-for-word match against"


@dataclass(frozen=True)
class ConflictContext:
    email: Email
    extraction: Extraction | None
    records: FirmRecords


def _client_side_names(extraction: Extraction | None) -> list[str]:
    if extraction is None:
        return []
    return [
        p.name.value
        for p in extraction.parties
        if p.role in CLIENT_SIDE_ROLES and p.name.value
    ]


def _opposing_names(extraction: Extraction | None) -> list[str]:
    if extraction is None:
        return []
    return [p.name.value for p in extraction.opposing_parties if p.name.value]


# --------------------------------------------------------------------------- #
# Rule 1
# --------------------------------------------------------------------------- #


def adverse_party_is_client(ctx: ConflictContext) -> list[ConflictHit]:
    """The party we would be opposing is someone the firm represents.

    A former client -- one with no open matters -- is graded lower than a current
    one, because the duty is narrower. It is still a hit: "we closed that file" is
    a reason a human might clear the conflict, never a reason not to raise it.
    """
    hits: list[ConflictHit] = []
    for name in _opposing_names(ctx.extraction):
        for match in ctx.records.find_clients_by_name(name):
            current = ctx.records.has_open_matter(match.client.id)
            if match.method in STRONG_METHODS:
                severity = ConflictSeverity.PROBABLE if current else ConflictSeverity.POSSIBLE
            else:
                severity = ConflictSeverity.POSSIBLE

            standing = "a current client" if current else "a former client (no open matters)"
            how = describe_match(match.method, name, match.client.display_name)
            hits.append(
                ConflictHit(
                    rule_id=RULE_ADVERSE_PARTY_IS_CLIENT,
                    severity=severity,
                    inquiry_party=name,
                    matched_record_kind="client",
                    matched_record_id=match.client.id,
                    matched_field="display_name",
                    matched_value=match.client.display_name,
                    score=match.score,
                    explanation=(
                        f"The inquiry names '{name}' as an opposing party. That is "
                        f"{how} {standing}, {match.client.display_name} "
                        f"({match.client.id}). If they are the same entity, acting "
                        f"on this matter would put the firm against its own client."
                    ),
                )
            )
    return hits


# --------------------------------------------------------------------------- #
# Rule 2
# --------------------------------------------------------------------------- #


def prospect_was_adverse_party(ctx: ConflictContext) -> list[ConflictHit]:
    """The person asking for help is someone the firm has acted against.

    Matter status deliberately does not enter the severity calculation. A closed
    matter means the work finished, not that the firm forgot what it learned, and
    grading these lower because a file was closed is precisely the mistake this
    rule exists to prevent. The status is reported so a human can weigh it.
    """
    hits: list[ConflictHit] = []
    for name in _client_side_names(ctx.extraction):
        for match in ctx.records.find_adverse_parties(name):
            severity = (
                ConflictSeverity.PROBABLE
                if match.method in STRONG_METHODS
                else ConflictSeverity.POSSIBLE
            )
            matter = match.matter
            client = ctx.records.client(matter.client_id)
            client_name = client.display_name if client else matter.client_id
            status = (
                "closed " + matter.closed_on.isoformat()
                if matter.status == MatterStatus.CLOSED and matter.closed_on
                else matter.status.value
            )
            hits.append(
                ConflictHit(
                    rule_id=RULE_PROSPECT_WAS_ADVERSE_PARTY,
                    severity=severity,
                    inquiry_party=name,
                    matched_record_kind="matter",
                    matched_record_id=matter.id,
                    matched_field="adverse_parties",
                    matched_value=match.adverse_name,
                    score=match.score,
                    explanation=(
                        f"'{name}' is asking the firm to act, and is "
                        f"{describe_match(match.method, name, match.adverse_name)} "
                        f"'{match.adverse_name}', an adverse party on {matter.id} "
                        f"({matter.caption}), which the firm ran for {client_name}. "
                        f"That matter is {status}; a closed matter does not clear this."
                    ),
                )
            )
    return hits


# --------------------------------------------------------------------------- #
# Rule 3
# --------------------------------------------------------------------------- #


def sender_domain_matches_client(ctx: ConflictContext) -> list[ConflictHit]:
    """Mail from a client's domain, with nothing else connecting it to that client.

    Graded WEAK on purpose. A shared domain means the sender works somewhere, and
    that somewhere is a client -- which could be the client's general counsel
    writing about a new matter, or an employee writing about a personal problem
    their employer must never hear about. The two need opposite handling and this
    signal cannot distinguish them, so it goes to a human every time and is never
    allowed to clear itself.

    Suppressed when a party in the email already matches that client by name,
    because then the connection is established by something stronger and this rule
    would only add noise.
    """
    domain = email_domain(ctx.email.from_email)
    if domain is None or is_generic_domain(domain):
        return []

    all_names = _client_side_names(ctx.extraction) + _opposing_names(ctx.extraction)
    hits: list[ConflictHit] = []

    for match in ctx.records.find_clients_by_email(ctx.email.from_email):
        if match.method != MatchMethod.EMAIL_DOMAIN:
            continue
        if any(
            m.client.id == match.client.id
            for name in all_names
            for m in ctx.records.find_clients_by_name(name)
        ):
            continue

        hits.append(
            ConflictHit(
                rule_id=RULE_SENDER_DOMAIN_MATCHES_CLIENT,
                severity=ConflictSeverity.WEAK,
                inquiry_party=ctx.email.from_email,
                matched_record_kind="client",
                matched_record_id=match.client.id,
                matched_field="domains",
                matched_value=domain,
                score=match.score,
                explanation=(
                    f"The sender writes from @{domain}, which belongs to client "
                    f"{match.client.display_name} ({match.client.id}), but no party "
                    f"named in the email matches that client. This could be the "
                    f"client contacting us under a new name, or an employee writing "
                    f"about something adverse to their employer. A human has to look."
                ),
            )
        )
    return hits


ALL_RULES = (
    adverse_party_is_client,
    prospect_was_adverse_party,
    sender_domain_matches_client,
)


def run_all(ctx: ConflictContext) -> list[ConflictHit]:
    """Every rule, every hit. Nothing is deduplicated or suppressed by severity --
    two rules firing on the same inquiry is information, not repetition."""
    hits: list[ConflictHit] = []
    for rule in ALL_RULES:
        hits.extend(rule(ctx))
    return sorted(hits, key=lambda h: (-h.severity.rank, -h.score, h.rule_id))
