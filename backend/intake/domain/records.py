"""An indexed view of the firm's clients and matters, with the matching rules.

Normalized forms are computed once when the index is built rather than on every
comparison. At 200 clients and 150 matters a linear scan per party is nothing; the
index exists for clarity, not speed, and the scan is O(clients) per name looked up.
A firm with 200,000 clients would need a real search index, and the token-similarity
pass is the part that would not survive the move.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from intake.domain.enums import MatchMethod, MatterStatus
from intake.domain.models import ClientRecord, MatterRecord
from intake.domain.normalize import (
    email_domain,
    is_generic_domain,
    normalize_name,
    suffixes_compatible,
    token_similarity,
)

# How close two names must be before we call it a match at all. Below this we say
# nothing rather than surfacing noise -- a conflicts queue full of near-misses gets
# ignored, and an ignored conflicts queue is worse than none.
TOKEN_SET_THRESHOLD = 0.75

SCORE_EXACT = 1.0
SCORE_NORMALIZED = 0.95
SCORE_STEM_ONLY = 0.6
SCORE_EMAIL_ADDRESS = 1.0
SCORE_EMAIL_DOMAIN = 0.5


@dataclass(frozen=True)
class NameMatch:
    """One name matching one record, with how it matched."""

    client: ClientRecord
    method: MatchMethod
    score: float


@dataclass(frozen=True)
class AdverseMatch:
    """A name matching an adverse party listed on one of the firm's matters."""

    matter: MatterRecord
    adverse_name: str
    method: MatchMethod
    score: float


@dataclass(frozen=True)
class _IndexedClient:
    record: ClientRecord
    normalized: str
    lowered: str
    emails: frozenset[str]
    domains: frozenset[str]


@dataclass(frozen=True)
class _IndexedMatter:
    record: MatterRecord
    adverse: tuple[tuple[str, str], ...]  # (raw name, normalized name)


def _compare(query: str, candidate_raw: str, candidate_lowered: str, candidate_normalized: str):
    """Return (method, score) for the strongest match, or None.

    Stem equality is necessary but not sufficient. Two names whose distinguishing
    words agree while their corporate suffixes name different entity types are
    reported as STEM_ONLY -- a lead, not an identification.
    """
    lowered = query.strip().lower()
    if lowered and lowered == candidate_lowered:
        return MatchMethod.EXACT, SCORE_EXACT

    normalized = normalize_name(query)
    if normalized and normalized == candidate_normalized:
        if suffixes_compatible(query, candidate_raw):
            return MatchMethod.NORMALIZED, SCORE_NORMALIZED
        return MatchMethod.STEM_ONLY, SCORE_STEM_ONLY

    similarity = token_similarity(query, candidate_normalized)
    if similarity >= TOKEN_SET_THRESHOLD:
        if not suffixes_compatible(query, candidate_raw):
            return MatchMethod.STEM_ONLY, SCORE_STEM_ONLY
        return MatchMethod.TOKEN_SET, round(similarity, 4)
    return None


class FirmRecords:
    """Everything the resolve stage is allowed to know about the firm."""

    def __init__(
        self,
        clients: list[ClientRecord],
        matters: list[MatterRecord],
    ) -> None:
        self._clients = [
            _IndexedClient(
                record=client,
                normalized=normalize_name(client.display_name),
                lowered=client.display_name.strip().lower(),
                emails=frozenset(e.strip().lower() for e in client.emails),
                # Generic mailbox providers are dropped at index time so they can
                # never become an identity signal downstream.
                domains=frozenset(
                    d.strip().lower()
                    for d in client.domains
                    if not is_generic_domain(d)
                ),
            )
            for client in clients
        ]
        self._matters = [
            _IndexedMatter(
                record=matter,
                adverse=tuple((name, normalize_name(name)) for name in matter.adverse_parties),
            )
            for matter in matters
        ]
        self._matters_by_client: dict[str, list[MatterRecord]] = {}
        for matter in matters:
            self._matters_by_client.setdefault(matter.client_id, []).append(matter)

    # -- lookups ---------------------------------------------------------- #

    @property
    def clients(self) -> list[ClientRecord]:
        return [c.record for c in self._clients]

    @property
    def matters(self) -> list[MatterRecord]:
        return [m.record for m in self._matters]

    def client(self, client_id: str) -> ClientRecord | None:
        for indexed in self._clients:
            if indexed.record.id == client_id:
                return indexed.record
        return None

    def matters_for_client(self, client_id: str) -> list[MatterRecord]:
        return list(self._matters_by_client.get(client_id, []))

    def has_open_matter(self, client_id: str) -> bool:
        return any(
            m.status == MatterStatus.OPEN for m in self._matters_by_client.get(client_id, [])
        )

    def find_clients_by_name(self, name: str | None) -> list[NameMatch]:
        if not name or not name.strip():
            return []
        matches = [
            NameMatch(client=indexed.record, method=result[0], score=result[1])
            for indexed in self._clients
            if (result := _compare(
                name, indexed.record.display_name, indexed.lowered, indexed.normalized
            )) is not None
        ]
        return sorted(matches, key=lambda m: -m.score)

    def find_clients_by_email(self, address: str | None) -> list[NameMatch]:
        """Address match identifies a client. Domain match only suggests one."""
        if not address or "@" not in address:
            return []
        lowered = address.strip().lower()
        domain = email_domain(lowered)

        matches: list[NameMatch] = []
        for indexed in self._clients:
            if lowered in indexed.emails:
                matches.append(
                    NameMatch(indexed.record, MatchMethod.EMAIL_ADDRESS, SCORE_EMAIL_ADDRESS)
                )
            elif domain and domain in indexed.domains:
                matches.append(
                    NameMatch(indexed.record, MatchMethod.EMAIL_DOMAIN, SCORE_EMAIL_DOMAIN)
                )
        return sorted(matches, key=lambda m: -m.score)

    def find_adverse_parties(self, name: str | None) -> list[AdverseMatch]:
        """Every matter on which this name appears as someone the firm opposed.

        Matter status is returned, not filtered. A closed matter does not clear a
        conflict, and deciding that here would hide the judgement from the rules
        that are supposed to make it.
        """
        if not name or not name.strip():
            return []
        matches: list[AdverseMatch] = []
        for indexed in self._matters:
            for raw, normalized in indexed.adverse:
                result = _compare(name, raw, raw.strip().lower(), normalized)
                if result is not None:
                    matches.append(
                        AdverseMatch(indexed.record, raw, result[0], result[1])
                    )
        return sorted(matches, key=lambda m: -m.score)
