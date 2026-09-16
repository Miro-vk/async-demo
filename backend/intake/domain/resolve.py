"""Stage 3: match the inquiry against the firm's records and check for conflicts.

A pure function over typed models. No model call, no database handle, no clock --
the same email and the same records always produce the same resolution, which is
what lets a conflicts result be reproduced and argued with.

The output carries not just what was found but what was looked for. `checked_party_names`
and `completeness_notes` exist because an empty conflicts list is ambiguous: it means
"we checked and found nothing" or "we had nothing to check", and those two must never
render identically to a human.
"""

from __future__ import annotations

from intake.domain.conflicts import ConflictContext, run_all
from intake.domain.enums import MatchMethod, PartyRole
from intake.domain.models import Email, Extraction, PartyMatch, Resolution
from intake.domain.records import FirmRecords

# A name this short cannot be looked up usefully -- "Bob" would match half the
# database. We record that we skipped it rather than pretending we checked it.
MIN_CHECKABLE_NAME_CHARS = 3


def resolve(
    email: Email,
    extraction: Extraction | None,
    records: FirmRecords,
) -> Resolution:
    checked: list[str] = []
    notes: list[str] = []
    client_matches: list[PartyMatch] = []

    # The sender's own address is the most reliable identifier available and does
    # not depend on extraction having worked.
    for match in records.find_clients_by_email(email.from_email):
        client_matches.append(
            PartyMatch(
                inquiry_party=email.from_email,
                inquiry_role=PartyRole.PROSPECTIVE_CLIENT,
                client_id=match.client.id,
                client_name=match.client.display_name,
                method=match.method,
                score=match.score,
            )
        )
    if not client_matches:
        notes.append("Sender address matches no client record.")

    if extraction is None:
        notes.append(
            "Extraction failed, so no party names were available to check. This "
            "conflicts result covers the sender address only."
        )
    else:
        named = [p for p in extraction.parties if p.name.value]
        if not named:
            notes.append("No party names were extracted, so no name check was run.")

        for party in named:
            name = party.name.value
            if len(name.strip()) < MIN_CHECKABLE_NAME_CHARS:
                notes.append(f"Skipped '{name}': too short to look up reliably.")
                continue

            checked.append(name)
            for match in records.find_clients_by_name(name):
                client_matches.append(
                    PartyMatch(
                        inquiry_party=name,
                        inquiry_role=party.role,
                        client_id=match.client.id,
                        client_name=match.client.display_name,
                        method=match.method,
                        score=match.score,
                    )
                )

            # Low-confidence names still get checked -- a conflict found from a
            # shaky extraction is still a conflict -- but the shakiness is recorded
            # so nobody reads the clean result as stronger than it is.
            if party.name.confidence < 0.5:
                notes.append(
                    f"Checked '{name}' but the extraction confidence was "
                    f"{party.name.confidence:.2f}; the name itself may be wrong."
                )

    conflicts = run_all(ConflictContext(email=email, extraction=extraction, records=records))

    # Matters worth surfacing: those belonging to a client we identified strongly.
    matter_ids: list[str] = []
    for match in client_matches:
        if match.method in (MatchMethod.EMAIL_ADDRESS, MatchMethod.EXACT, MatchMethod.NORMALIZED):
            matter_ids.extend(m.id for m in records.matters_for_client(match.client_id))

    return Resolution(
        email_id=email.id,
        client_matches=_dedupe_matches(client_matches),
        matter_ids=sorted(set(matter_ids)),
        conflicts=conflicts,
        checked_party_names=checked,
        completeness_notes=notes,
    )


def _dedupe_matches(matches: list[PartyMatch]) -> list[PartyMatch]:
    """Keep the strongest match per (party, client) pair, best first."""
    best: dict[tuple[str, str], PartyMatch] = {}
    for match in matches:
        key = (match.inquiry_party, match.client_id)
        if key not in best or match.score > best[key].score:
            best[key] = match
    return sorted(best.values(), key=lambda m: (-m.score, m.client_id))
