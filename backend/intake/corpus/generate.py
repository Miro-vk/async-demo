"""Seeded synthetic corpus generator.

Everything the demo runs on is produced here: the firm's client and matter records,
the attorney roster, and the inquiry emails. Same seed in, byte-identical corpus out
-- no wall clock, no uuid, no unordered iteration, no global `random`.

The generator also emits ground truth, because it knows the answers it planted. That
is what makes `make eval` possible: stage accuracy and conflict-trap recall are
measured, not asserted.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from random import Random

from intake.corpus import pools
from intake.domain.normalize import normalize_name
from intake.domain.enums import (
    ClientType,
    DecisionAction,
    EmailClass,
    MatterStatus,
    PracticeArea,
)
from intake.domain.models import (
    Attorney,
    ClientRecord,
    Corpus,
    Email,
    GroundTruth,
    MatterRecord,
)

DEFAULT_SEED = 20260517

# A fixed "now". Using datetime.now() here would make the corpus non-reproducible,
# which would defeat the point of seeding it.
EPOCH = datetime(2026, 3, 2, 9, 0, 0)

N_CLIENTS = 200
N_MATTERS = 150

PRACTICE_AREAS = [
    PracticeArea.COMMERCIAL_LITIGATION,
    PracticeArea.EMPLOYMENT,
    PracticeArea.REAL_ESTATE,
    PracticeArea.INTELLECTUAL_PROPERTY,
    PracticeArea.FAMILY,
    PracticeArea.PERSONAL_INJURY,
]


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _domain_for(org_name: str) -> str:
    words = [w for w in re.split(r"[^A-Za-z]+", org_name) if len(w) > 1]
    stem = ""
    for word in words:
        if stem and len(stem) + len(word) > 20:
            break
        stem += word
    return f"{stem.lower()}.com"


def _fmt_date(d: date, rng: Random) -> str:
    """Vary the surface form. Extraction has to cope with all of these; ground
    truth always stores ISO."""
    style = rng.randrange(4)
    if style == 0:
        return d.strftime("%B %-d, %Y")
    if style == 1:
        return f"{d.month}/{d.day}/{d.year}"
    if style == 2:
        return d.strftime("%d %B %Y").lstrip("0")
    return d.strftime("%b %-d, %Y")


def _fmt_money(amount: float, rng: Random) -> str:
    if amount >= 1000 and rng.random() < 0.3:
        return f"${amount / 1000:.0f}k"
    return f"${amount:,.0f}"


def _phone(rng: Random) -> str:
    return f"({rng.randrange(200, 990)}) {rng.randrange(200, 990)}-{rng.randrange(1000, 9999):04d}"


# --------------------------------------------------------------------------- #
# Firm records
# --------------------------------------------------------------------------- #


def build_attorneys(rng: Random) -> list[Attorney]:
    attorneys: list[Attorney] = []
    for i, (name, areas, capacity) in enumerate(pools.ATTORNEY_SEEDS):
        first, last = name.split(" ", 1)
        attorneys.append(
            Attorney(
                id=f"atty-{i + 1:02d}",
                name=name,
                email=f"{first[0].lower()}{_slug(last)}@vancebrock.example",
                practice_areas=[PracticeArea(a) for a in areas],
                capacity=capacity,
                current_load=rng.randrange(2, capacity),
            )
        )
    return attorneys


def build_clients(rng: Random, n: int = N_CLIENTS) -> list[ClientRecord]:
    clients: list[ClientRecord] = []
    used: set[str] = set()
    while len(clients) < n:
        idx = len(clients)
        is_org = rng.random() < 0.6
        if is_org:
            name = (
                f"{rng.choice(pools.ORG_TOKENS)} {rng.choice(pools.ORG_NOUNS)} "
                f"{rng.choice(pools.ORG_SUFFIXES)}"
            )
            if name in used:
                continue
            domain = _domain_for(name)
            contact_first = rng.choice(pools.FIRST_NAMES)
            contact_last = rng.choice(pools.LAST_NAMES)
            emails = [
                f"{contact_first.lower()}.{contact_last.lower()}@{domain}",
                f"legal@{domain}",
            ]
            domains = [domain]
            ctype = ClientType.ORGANIZATION
        else:
            name = f"{rng.choice(pools.FIRST_NAMES)} {rng.choice(pools.LAST_NAMES)}"
            if name in used:
                continue
            first, last = name.split(" ")
            domain = rng.choice(pools.FREE_MAIL_DOMAINS)
            emails = [f"{first.lower()}.{last.lower()}@{domain}"]
            domains = []  # a gmail domain is not an identifying domain
            ctype = ClientType.INDIVIDUAL

        used.add(name)
        opened = date(2018, 1, 1) + timedelta(days=rng.randrange(0, 2800))
        clients.append(
            ClientRecord(
                id=f"cli-{idx + 1:04d}",
                display_name=name,
                client_type=ctype,
                emails=emails,
                domains=domains,
                opened_on=opened,
            )
        )
    return clients


AREAS_BY_CLIENT_TYPE = {
    ClientType.ORGANIZATION: [
        PracticeArea.COMMERCIAL_LITIGATION,
        PracticeArea.EMPLOYMENT,
        PracticeArea.REAL_ESTATE,
        PracticeArea.INTELLECTUAL_PROPERTY,
    ],
    ClientType.INDIVIDUAL: [
        PracticeArea.EMPLOYMENT,
        PracticeArea.REAL_ESTATE,
        PracticeArea.FAMILY,
        PracticeArea.PERSONAL_INJURY,
    ],
}


def _adverse_name(rng: Random, clients: list[ClientRecord]) -> str:
    """Adverse parties are mostly strangers, but sometimes another client of the
    firm -- which is how a realistic conflicts surface arises without planting
    anything."""
    if rng.random() < 0.18:
        return rng.choice(clients).display_name
    if rng.random() < 0.55:
        return (
            f"{rng.choice(pools.ORG_TOKENS)} {rng.choice(pools.ORG_NOUNS)} "
            f"{rng.choice(pools.ORG_SUFFIXES)}"
        )
    return f"{rng.choice(pools.FIRST_NAMES)} {rng.choice(pools.LAST_NAMES)}"


def build_matters(
    rng: Random,
    clients: list[ClientRecord],
    attorneys: list[Attorney],
    n: int = N_MATTERS,
) -> list[MatterRecord]:
    matters: list[MatterRecord] = []
    for i in range(n):
        client = rng.choice(clients)
        # A corporation does not get divorced. Practice areas are constrained by the
        # client's type, or the generator emits captions like
        # "In re Marriage of Ironwood Aggregates Ltd."
        area = rng.choice(AREAS_BY_CLIENT_TYPE[client.client_type])
        eligible = [a for a in attorneys if area in a.practice_areas]
        attorney = rng.choice(eligible) if eligible else rng.choice(attorneys)

        opened = client.opened_on + timedelta(days=rng.randrange(0, 900))
        is_closed = rng.random() < 0.38
        closed = opened + timedelta(days=rng.randrange(90, 1100)) if is_closed else None
        if closed and closed > EPOCH.date():
            closed = None
            is_closed = False

        adverse = [_adverse_name(rng, clients)]
        if rng.random() < 0.25:
            adverse.append(_adverse_name(rng, clients))

        caption = pools.CAPTION_SHAPES[area.value].format(
            client=client.display_name, adverse=adverse[0]
        )
        matters.append(
            MatterRecord(
                id=f"mat-{i + 1:04d}",
                client_id=client.id,
                caption=caption,
                practice_area=area,
                status=MatterStatus.CLOSED if is_closed else MatterStatus.OPEN,
                opened_on=opened,
                closed_on=closed,
                responsible_attorney_id=attorney.id,
                adverse_parties=adverse,
            )
        )
    return matters


# --------------------------------------------------------------------------- #
# Inquiry scenarios
#
# Each builder returns the templated prose plus the facts it planted. The prose is
# later rewritten by the naturalize pass (see naturalize.py); the facts are what the
# eval harness scores extraction against, so they must survive that rewrite intact.
# --------------------------------------------------------------------------- #


def _sender_for(
    rng: Random, party_name: str, is_org: bool, forced_domain: str | None = None
) -> tuple[str, str]:
    if is_org:
        first = rng.choice(pools.FIRST_NAMES)
        last = rng.choice(pools.LAST_NAMES)
        domain = forced_domain or _domain_for(party_name)
        return f"{first} {last}", f"{first.lower()}.{last.lower()}@{domain}"
    first, _, last = party_name.partition(" ")
    domain = forced_domain or rng.choice(pools.FREE_MAIL_DOMAINS)
    return party_name, f"{first.lower()}.{_slug(last)}@{domain}"


def _new_org_name(rng: Random) -> str:
    return (
        f"{rng.choice(pools.ORG_TOKENS)} {rng.choice(pools.ORG_NOUNS)} "
        f"{rng.choice(pools.ORG_SUFFIXES)}"
    )


def _new_person_name(rng: Random) -> str:
    return f"{rng.choice(pools.FIRST_NAMES)} {rng.choice(pools.LAST_NAMES)}"


def _scn_commercial(rng, client, adverse, juris, signer):
    signed = EPOCH.date() - timedelta(days=rng.randrange(400, 900))
    stopped = EPOCH.date() - timedelta(days=rng.randrange(60, 240))
    amount = float(rng.randrange(45, 880) * 1000)
    body = (
        f"Hello,\n\n"
        f"I'm writing on behalf of {client} regarding a supply agreement we entered "
        f"into with {adverse} on {_fmt_date(signed, rng)}. They delivered on schedule "
        f"for about a year and then stopped entirely around {_fmt_date(stopped, rng)}. "
        f"We have been left holding roughly {_fmt_money(amount, rng)} in unfilled "
        f"purchase orders and we've had to source elsewhere at a premium.\n\n"
        f"Our last three emails have gone unanswered. The agreement has a venue clause "
        f"pointing to {juris}, which is also where we operate.\n\n"
        f"Is a breach of contract claim something your firm would take on? I can send "
        f"over the agreement and the PO history.\n\n"
        f"{signer}\nOperations, {client}\n{_phone(rng)}"
    )
    return (
        f"Breach of supply agreement - {adverse}",
        body,
        {"key_dates": [signed.isoformat(), stopped.isoformat()], "amounts": [amount]},
    )


def _scn_employment(rng, client, adverse, juris, signer):
    terminated = EPOCH.date() - timedelta(days=rng.randrange(20, 150))
    reported = terminated - timedelta(days=rng.randrange(20, 90))
    salary = float(rng.randrange(58, 185) * 1000)
    body = (
        f"Hi,\n\n"
        f"I was let go from {adverse} on {_fmt_date(terminated, rng)} and I believe it "
        f"was retaliation. On {_fmt_date(reported, rng)} I reported to HR that our shift "
        f"supervisor was falsifying safety inspection logs. Nothing happened, and about "
        f"two months later I was told my position was being eliminated. They filled it "
        f"six weeks after.\n\n"
        f"I was there {rng.randrange(3, 12)} years with no disciplinary write-ups. My "
        f"salary was {_fmt_money(salary, rng)}. I'm in {juris}.\n\n"
        f"I'd like someone to look at this before the filing window closes.\n\n"
        f"{signer}\n{_phone(rng)}"
    )
    return (
        "Wrongful termination - possible retaliation",
        body,
        {
            "key_dates": [terminated.isoformat(), reported.isoformat()],
            "amounts": [salary],
        },
    )


def _scn_real_estate(rng, client, adverse, juris, signer):
    closing = EPOCH.date() + timedelta(days=rng.randrange(14, 70))
    contract = EPOCH.date() - timedelta(days=rng.randrange(20, 90))
    price = float(rng.randrange(310, 1900) * 1000)
    address = f"{rng.randrange(100, 9800)} {rng.choice(pools.STREETS)}"
    city = rng.choice(pools.CITIES)
    body = (
        f"Good morning,\n\n"
        f"We are under contract to purchase {address}, {city} from {adverse}. We signed "
        f"on {_fmt_date(contract, rng)} at a price of {_fmt_money(price, rng)} and we "
        f"are set to close {_fmt_date(closing, rng)}.\n\n"
        f"The survey came back this week showing the detached garage and about eight "
        f"feet of the driveway sit on the neighboring parcel. Seller's agent is taking "
        f"the position that this is a pre-existing condition and not their problem. Our "
        f"lender has already flagged it.\n\n"
        f"The property is in {juris}. We would rather not walk away but we are not "
        f"closing on an encroachment. Can you advise this week?\n\n"
        f"{signer}\n{_phone(rng)}"
    )
    return (
        f"Encroachment issue before closing - {address}",
        body,
        {"key_dates": [contract.isoformat(), closing.isoformat()], "amounts": [price]},
    )


def _scn_ip(rng, client, adverse, juris, signer):
    first_use = EPOCH.date() - timedelta(days=rng.randrange(1200, 4000))
    discovered = EPOCH.date() - timedelta(days=rng.randrange(15, 90))
    revenue = float(rng.randrange(120, 2600) * 1000)
    mark = f"{rng.choice(pools.ORG_TOKENS).split()[0]}{rng.choice(['wave', 'kit', 'form', 'loom', 'pilot'])}"
    body = (
        f"Hello,\n\n"
        f"{client} has sold under the mark \"{mark}\" continuously since "
        f"{_fmt_date(first_use, rng)}. We hold a registration and the mark is on every "
        f"unit we ship.\n\n"
        f"On {_fmt_date(discovered, rng)} a customer forwarded me a listing from "
        f"{adverse} using a name that is one letter off from ours, in the same product "
        f"category, at a lower price point. We sent an informal note through their "
        f"website contact form and got no response.\n\n"
        f"That product line is roughly {_fmt_money(revenue, rng)} of annual revenue for "
        f"us. We're based in {juris}.\n\n"
        f"What would a cease and desist look like here, and how quickly can it go out?\n\n"
        f"{signer}\n{client}\n\nSent from my phone"
    )
    return (
        f"Trademark infringement - {adverse}",
        body,
        {
            "key_dates": [first_use.isoformat(), discovered.isoformat()],
            "amounts": [revenue],
        },
    )


def _scn_family(rng, client, adverse, juris, signer):
    married = EPOCH.date() - timedelta(days=rng.randrange(2600, 8000))
    separated = EPOCH.date() - timedelta(days=rng.randrange(30, 400))
    equity = float(rng.randrange(80, 640) * 1000)
    body = (
        f"Hi,\n\n"
        f"I'm looking for representation in a divorce. My spouse is {adverse}. We "
        f"married {_fmt_date(married, rng)} and separated on {_fmt_date(separated, rng)}. "
        f"We have two children, ages {rng.randrange(4, 12)} and {rng.randrange(13, 17)}.\n\n"
        f"The main asset is the house, which has around {_fmt_money(equity, rng)} in "
        f"equity. There is also a retirement account in their name that I think was "
        f"partly funded during the marriage.\n\n"
        f"It has been civil so far and I would like to keep it that way if possible, but "
        f"they retained someone last week so I should too. We live in {juris}.\n\n"
        f"Please let me know about a consultation.\n\n{signer}"
    )
    return (
        "Divorce - consultation request",
        body,
        {
            "key_dates": [married.isoformat(), separated.isoformat()],
            "amounts": [equity],
        },
    )


def _scn_personal_injury(rng, client, adverse, juris, signer):
    accident = EPOCH.date() - timedelta(days=rng.randrange(14, 220))
    bills = float(rng.randrange(6, 94) * 1000)
    street = rng.choice(pools.STREETS)
    city = rng.choice(pools.CITIES)
    body = (
        f"Hello,\n\n"
        f"I was rear-ended on {_fmt_date(accident, rng)} at the intersection of {street} "
        f"and Bellamy in {city}. The other driver, {adverse}, was cited at the scene. I "
        f"was stopped at a light.\n\n"
        f"I've been in physical therapy {rng.randrange(6, 30)} times since and I'm still "
        f"having neck and shoulder problems. My medical bills are at about "
        f"{_fmt_money(bills, rng)} right now and I've missed work.\n\n"
        f"Their insurance adjuster has called me four times and is pushing me to give a "
        f"recorded statement and take a quick settlement. I have not signed anything.\n\n"
        f"The accident was in {juris}. Can someone advise me before I talk to them "
        f"again?\n\n{client}\n{_phone(rng)}"
    )
    return (
        "Car accident - insurer pressuring me to settle",
        body,
        {"key_dates": [accident.isoformat()], "amounts": [bills]},
    )


# Whether the adverse party in a given practice area is naturally an entity or a
# natural person. Getting this wrong produces nonsense like an LLC being cited at the
# scene of a car accident.
ADVERSE_IS_ORG = {
    PracticeArea.COMMERCIAL_LITIGATION: True,
    PracticeArea.INTELLECTUAL_PROPERTY: True,
    PracticeArea.EMPLOYMENT: True,       # the employer
    PracticeArea.REAL_ESTATE: None,      # seller can be either
    PracticeArea.FAMILY: False,          # the spouse
    PracticeArea.PERSONAL_INJURY: False, # the other driver
}


def _looks_like_org(name: str) -> bool:
    return name.split()[-1] in pools.ORG_SUFFIXES


SCENARIO_BUILDERS = {
    PracticeArea.COMMERCIAL_LITIGATION: _scn_commercial,
    PracticeArea.EMPLOYMENT: _scn_employment,
    PracticeArea.REAL_ESTATE: _scn_real_estate,
    PracticeArea.INTELLECTUAL_PROPERTY: _scn_ip,
    PracticeArea.FAMILY: _scn_family,
    PracticeArea.PERSONAL_INJURY: _scn_personal_injury,
}

# Practice areas where the inquiring party is naturally a company, not a person.
ORG_CLIENT_AREAS = {
    PracticeArea.COMMERCIAL_LITIGATION,
    PracticeArea.INTELLECTUAL_PROPERTY,
}


# --------------------------------------------------------------------------- #
# Conflict traps
#
# Three shapes, planted on purpose so the resolve stage has something real to catch:
#   CORP_NAME_VARIANT            opposing party is an existing client under a
#                                cosmetically different corporate name
#   ADVERSE_PARTY_CLOSED_MATTER  the prospective client is someone the firm
#                                previously opposed; the matter being closed does
#                                not clear the conflict
#   EMAIL_DOMAIN_ONLY            sender's domain belongs to an existing client but
#                                no name matches -- a weak signal that must never
#                                auto-clear
# --------------------------------------------------------------------------- #

ABBREVIATIONS = {
    "Capital": "Cap.",
    "Manufacturing": "Mfg.",
    "Industries": "Ind.",
    "Associates": "Assoc.",
    "Group": "Grp.",
    "Partners": "Ptnrs.",
    "Construction": "Constr.",
    "Distribution": "Distrib.",
    "Logistics": "Log.",
    "Holdings": "Hldgs.",
    "Diagnostics": "Diag.",
    "Hospitality": "Hosp.",
    "Automotive": "Auto.",
}

SUFFIX_VARIANTS = {
    "LLC": "L.L.C.",
    "L.L.C.": "LLC",
    "Inc.": "Incorporated",
    "Corp.": "Corporation",
    "Ltd.": "Limited",
    "LLP": "L.L.P.",
    "Co.": "Company",
    "PLLC": "P.L.L.C.",
}


def corporate_variant(
    name: str, rng: Random, strategy: str | None = None
) -> tuple[str, str]:
    """Return a cosmetically different spelling of a corporate name, plus a note
    describing what was changed. Surface only -- same entity, different string."""
    words = name.split()
    suffix = words[-1]
    stem_words = words[:-1]

    strategies = []
    if suffix in SUFFIX_VARIANTS:
        strategies.append("suffix_swap")
    strategies.append("drop_suffix")
    if any(w in ABBREVIATIONS for w in stem_words):
        strategies.append("abbreviate")
    strategies.append("comma")

    choice = strategy if strategy in strategies else rng.choice(strategies)
    if choice == "suffix_swap":
        return " ".join(stem_words + [SUFFIX_VARIANTS[suffix]]), f"suffix {suffix} -> {SUFFIX_VARIANTS[suffix]}"
    if choice == "drop_suffix":
        return " ".join(stem_words), f"dropped corporate suffix {suffix}"
    if choice == "abbreviate":
        out = [ABBREVIATIONS.get(w, w) for w in stem_words]
        return " ".join(out + [suffix]), "abbreviated a name word"
    return f"{' '.join(stem_words)}, {suffix}", "added comma before suffix"


def _entry(**kw) -> dict:
    base = {
        "label": EmailClass.NEW_MATTER,
        "practice_area": None,
        "jurisdiction": None,
        "prospective_client": None,
        "opposing": [],
        "key_dates": [],
        "amounts": [],
        "traps": [],
        "trap_record_ids": [],
        "expected_action": DecisionAction.PROCEED,
        "ambiguous": False,
        "notes": "",
    }
    base.update(kw)
    return base


def _build_new_matter(
    rng: Random,
    area: PracticeArea,
    *,
    client_name: str | None = None,
    adverse_name: str | None = None,
    forced_domain: str | None = None,
    traps: list[str] | None = None,
    trap_record_ids: list[str] | None = None,
    expected_action: DecisionAction = DecisionAction.PROCEED,
    notes: str = "",
) -> dict:
    is_org = area in ORG_CLIENT_AREAS
    client = client_name or (_new_org_name(rng) if is_org else _new_person_name(rng))
    if adverse_name:
        adverse = adverse_name
    else:
        wants_org = ADVERSE_IS_ORG[area]
        if wants_org is None:
            wants_org = rng.random() < 0.5
        client_first = client.split()[0]
        for _ in range(12):
            adverse = _new_org_name(rng) if wants_org else _new_person_name(rng)
            if adverse.split()[0] != client_first:
                break
    juris = rng.choice(pools.JURISDICTIONS)
    from_name, from_email = _sender_for(rng, client, is_org, forced_domain)
    subject, body, facts = SCENARIO_BUILDERS[area](rng, client, adverse, juris, from_name)
    return _entry(
        from_name=from_name,
        from_email=from_email,
        subject=subject,
        body=body,
        label=EmailClass.NEW_MATTER,
        practice_area=area,
        jurisdiction=juris,
        prospective_client=client,
        opposing=[adverse],
        key_dates=facts["key_dates"],
        amounts=facts["amounts"],
        traps=traps or [],
        trap_record_ids=trap_record_ids or [],
        expected_action=expected_action,
        notes=notes,
    )


def _trap_new_matters(
    rng: Random, clients: list[ClientRecord], matters: list[MatterRecord]
) -> list[dict]:
    by_id = {c.id: c for c in clients}
    entries: list[dict] = []

    # --- CORP_NAME_VARIANT: adverse party is an existing client, misspelled ---
    org_clients = [c for c in clients if c.client_type == ClientType.ORGANIZATION]
    abbreviable = [
        c for c in org_clients
        if any(w in ABBREVIATIONS for w in c.display_name.split()[:-1])
    ]
    swappable = [c for c in org_clients if c.display_name.split()[-1] in SUFFIX_VARIANTS]
    variant_plan = [
        (abbreviable[4], "abbreviate", PracticeArea.COMMERCIAL_LITIGATION),
        (swappable[9], "suffix_swap", PracticeArea.INTELLECTUAL_PROPERTY),
        (org_clients[112], "drop_suffix", PracticeArea.REAL_ESTATE),
    ]
    for target, strategy, area in variant_plan:
        variant, how = corporate_variant(target.display_name, rng, strategy)
        entries.append(
            _build_new_matter(
                rng,
                area,
                adverse_name=variant,
                traps=["CORP_NAME_VARIANT"],
                trap_record_ids=[target.id],
                expected_action=DecisionAction.REVIEW,
                notes=(
                    f"Adverse party '{variant}' is existing client "
                    f"{target.id} '{target.display_name}' ({how})."
                ),
            )
        )

    # --- ADVERSE_PARTY_CLOSED_MATTER: we are asked to act for a former opponent ---
    closed = [m for m in matters if m.status == MatterStatus.CLOSED and m.adverse_parties]
    # Employment inquiries come from a terminated individual; commercial ones from a
    # company. Pick closed matters whose former opponent has the right shape.
    closed_person = [m for m in closed if not _looks_like_org(m.adverse_parties[0])]
    closed_org = [m for m in closed if _looks_like_org(m.adverse_parties[0])]
    for matter, area in zip(
        [closed_person[2], closed_org[5]],
        [PracticeArea.EMPLOYMENT, PracticeArea.COMMERCIAL_LITIGATION],
    ):
        former_opponent = matter.adverse_parties[0]
        entries.append(
            _build_new_matter(
                rng,
                area,
                client_name=former_opponent,
                traps=["ADVERSE_PARTY_CLOSED_MATTER"],
                trap_record_ids=[matter.id],
                expected_action=DecisionAction.REVIEW,
                notes=(
                    f"Prospective client '{former_opponent}' was the adverse party on "
                    f"closed matter {matter.id} ({by_id[matter.client_id].display_name})."
                ),
            )
        )

    # --- EMAIL_DOMAIN_ONLY: right domain, wrong everything else ---
    domain_clients = [c for c in org_clients if c.domains]
    for target, area in zip(
        [domain_clients[22], domain_clients[61]],
        [PracticeArea.EMPLOYMENT, PracticeArea.PERSONAL_INJURY],
    ):
        entries.append(
            _build_new_matter(
                rng,
                area,
                forced_domain=target.domains[0],
                traps=["EMAIL_DOMAIN_ONLY"],
                trap_record_ids=[target.id],
                expected_action=DecisionAction.REVIEW,
                notes=(
                    f"Sender writes from @{target.domains[0]}, the domain of existing "
                    f"client {target.id} '{target.display_name}', but no party name "
                    f"matches that client."
                ),
            )
        )

    return entries


# --------------------------------------------------------------------------- #
# Existing-client, vendor/spam, and ambiguous mail
# --------------------------------------------------------------------------- #


def _existing_client_emails(
    rng: Random,
    clients: list[ClientRecord],
    matters: list[MatterRecord],
    attorneys: list[Attorney],
    count: int,
) -> list[dict]:
    by_id = {c.id: c for c in clients}
    atty_by_id = {a.id: a for a in attorneys}
    open_matters = [m for m in matters if m.status == MatterStatus.OPEN]
    picked = rng.sample(open_matters, count)
    entries: list[dict] = []

    # An individual divorce client does not have a board or an AP department.
    # Shape selection follows the client type; it consumes no randomness, so this
    # choice does not perturb any other part of the seeded corpus.
    org_shapes = [
        (
            "Re: {caption} - document request",
            "Hi {atty_first},\n\nCould you send over the most recent version of the "
            "settlement letter on {caption} ({mid})? Our board meets Thursday and I want "
            "to walk them through it.\n\nAlso, AP is asking about invoice {inv} dated "
            "{invdate} - is that the one covering the deposition prep?\n\nThanks,\n{contact}",
        ),
        (
            "Invoice {inv} - billing question",
            "Hello,\n\nWe received invoice {inv} dated {invdate} for {caption}. There's a "
            "line item for {amount} that I don't recognize and it wasn't in the estimate. "
            "Can someone from billing walk me through it before we process payment?\n\n"
            "{contact}",
        ),
        (
            "Question on {mid}",
            "{atty_first},\n\nFollowing up on our call. You mentioned you'd know more "
            "about scheduling by the end of the month on {caption}. I need to brief our "
            "leadership either way.\n\nAny update?\n\n{contact}",
        ),
        (
            "Re: {caption}",
            "Hi {atty_first},\n\nThe other side's counsel emailed our general inbox "
            "directly this morning about {caption}. Nobody here responded. Wanted to flag "
            "it to you first - should we route anything further straight to you?\n\n{contact}",
        ),
    ]
    individual_shapes = [
        (
            "Re: {caption} - can I get a copy?",
            "Hi {atty_first},\n\nCould you send me the latest version of the settlement "
            "letter on {caption} ({mid})? I'd like to read it over properly before we "
            "talk again.\n\nAlso I got invoice {inv} dated {invdate} - is that the "
            "deposition prep you mentioned?\n\nThanks,\n{contact}",
        ),
        (
            "Invoice {inv} - question",
            "Hello,\n\nI received invoice {inv} dated {invdate} for {caption}. There's a "
            "line item for {amount} that I don't recognize and I don't think it was in "
            "what we discussed. Could someone explain it before I pay?\n\n{contact}",
        ),
        (
            "Question on {mid}",
            "{atty_first},\n\nFollowing up on our call. You said you'd know more about "
            "scheduling by the end of the month on {caption}.\n\nAny update? I need to "
            "know whether to request time off work.\n\n{contact}",
        ),
        (
            "Re: {caption}",
            "Hi {atty_first},\n\nThe other side's lawyer emailed me directly this "
            "morning about {caption}. I didn't respond. Wanted to tell you before I did "
            "anything - should I just forward these to you from now on?\n\n{contact}",
        ),
    ]

    for i, matter in enumerate(picked):
        client = by_id[matter.client_id]
        atty = atty_by_id[matter.responsible_attorney_id]
        shapes = (
            org_shapes
            if client.client_type == ClientType.ORGANIZATION
            else individual_shapes
        )
        subject_t, body_t = shapes[i % len(shapes)]
        contact_email = client.emails[0]
        contact_name = (
            client.display_name
            if client.client_type == ClientType.INDIVIDUAL
            else contact_email.split("@")[0].replace(".", " ").title()
        )
        invoice = f"INV-{rng.randrange(10000, 99999)}"
        invdate = EPOCH.date() - timedelta(days=rng.randrange(10, 60))
        amount = float(rng.randrange(400, 9000))
        fill = {
            "caption": matter.caption,
            "mid": matter.id,
            "atty_first": atty.name.split()[0],
            "contact": contact_name,
            "inv": invoice,
            "invdate": _fmt_date(invdate, rng),
            "amount": _fmt_money(amount, rng),
        }
        entries.append(
            _entry(
                from_name=contact_name,
                from_email=contact_email,
                subject=subject_t.format(**fill),
                body=body_t.format(**fill),
                label=EmailClass.EXISTING_CLIENT,
                practice_area=matter.practice_area,
                prospective_client=client.display_name,
                expected_action=DecisionAction.PROCEED,
                notes=f"References open matter {matter.id} for client {client.id}.",
            )
        )
    return entries


def _vendor_spam_emails(rng: Random, count: int) -> list[dict]:
    templates = [
        (
            "Corbin Vale", "corbin.vale@reviewiq.example",
            "Cut document review spend by 60% - 15 min demo?",
            "Hi there,\n\nI work with mid-size litigation practices to cut first-pass "
            "document review costs using our AI review platform. Firms your size are "
            "typically saving {savings} per matter.\n\nWorth 15 minutes next week? I have "
            "Tuesday at 10 or Thursday at 2.\n\nBest,\nCorbin Vale\nEnterprise Sales, "
            "ReviewIQ\n\nUnsubscribe | 400 Market St, Suite 1100",
        ),
        (
            "Danika Poole", "danika@apexlegalmkt.example",
            "Your firm is invisible on Google",
            "Hello,\n\nI ran an audit of your firm's local search presence and found "
            "{issues} critical issues holding you back from page one. Your competitors "
            "are capturing the personal injury searches in your area.\n\nI can send the "
            "full audit at no cost. Just reply YES.\n\nRegards,\nDanika Poole\nApex Legal "
            "Marketing",
        ),
        (
            "Halston Reeve", "hreeve@reevesearch.example",
            "Senior associate candidates - litigation",
            "Good afternoon,\n\nI place litigation associates in firms across the region. "
            "I currently have three candidates with {yrs}+ years of commercial litigation "
            "experience actively looking, including one coming out of an AmLaw 100 "
            "practice.\n\nWould you like me to send profiles?\n\nThank you,\nHalston "
            "Reeve\nReeve Legal Search",
        ),
        (
            "Summit Committee", "registrar@summitcle.example",
            "Invitation: Annual Trial Advocacy Summit",
            "Dear Counsel,\n\nYou are invited to the Annual Trial Advocacy Summit, "
            "{dates}. This year's program includes {credits} hours of CLE credit and a "
            "keynote on evidentiary challenges to machine-generated records.\n\nEarly "
            "registration closes Friday.\n\nThe Summit Committee",
        ),
        (
            "Supply Division", "orders@supply-division.example",
            "Toner supplies - order confirmation needed",
            "ATTENTION ACCOUNTS PAYABLE\n\nYour scheduled toner shipment is ready for "
            "dispatch. Order total {total}. Please confirm your shipping address to avoid "
            "a restocking fee.\n\nReply with confirmation within 48 hours.\n\nSupply "
            "Division",
        ),
        (
            "IT Helpdesk", "no-reply@mail-secure.example",
            "Action required: mailbox storage exceeded",
            "Your mailbox has exceeded its storage quota of 2GB. You will stop receiving "
            "new messages within 24 hours.\n\nCLICK HERE to validate your account and "
            "restore full mailbox capacity.\n\nIT Helpdesk\nDo not reply to this message.",
        ),
        (
            "Accounts Receivable", "ar@ar-notices.example",
            "Updated remittance details",
            "Hello,\n\nPlease note our banking details have changed effective "
            "immediately. Kindly update your records and direct all outstanding payments "
            "to the new account below for invoice processing.\n\nDo not send funds to the "
            "previous account. Confirm receipt of this notice by reply.\n\nAccounts "
            "Receivable",
        ),
        (
            "Marguerite Sinclair", "msinclair@precisionreporting.example",
            "Certified court reporters - same day rough drafts",
            "Hello,\n\nWe provide certified court reporting and videography across "
            "{states} states with same-day rough drafts and realtime streaming.\n\nNew "
            "clients receive {discount} off their first three depositions.\n\nMarguerite "
            "Sinclair\nPrecision Reporting Group",
        ),
    ]
    entries: list[dict] = []
    for i in range(count):
        sender_name, sender_email, subject, body_t = templates[i % len(templates)]
        body = body_t.format(
            savings=_fmt_money(float(rng.randrange(8, 40) * 1000), rng),
            issues=rng.randrange(3, 12),
            yrs=rng.randrange(4, 9),
            dates="June 11-13",
            credits=rng.randrange(8, 16),
            total=_fmt_money(float(rng.randrange(200, 900)), rng),
            states=rng.randrange(12, 40),
            discount=f"{rng.randrange(10, 30)}%",
        )
        entries.append(
            _entry(
                from_name=sender_name,
                from_email=sender_email,
                subject=subject,
                body=body,
                label=EmailClass.VENDOR_OR_SPAM,
                expected_action=DecisionAction.STOP,
                notes="Solicitation or fraud attempt; no matter, no acknowledgment.",
            )
        )
    return entries


def _ambiguous_emails(
    rng: Random,
    clients: list[ClientRecord],
    matters: list[MatterRecord],
    attorneys: list[Attorney],
) -> list[dict]:
    """Seven inquiries that a careful human would also hesitate over.

    These are composed rather than sampled: the ambiguity is the point, and random
    slot-filling does not reliably produce genuine ambiguity. Facts and names are
    still drawn from the seeded pools.
    """
    by_id = {c.id: c for c in clients}
    atty_by_id = {a.id: a for a in attorneys}
    open_matters = [m for m in matters if m.status == MatterStatus.OPEN]
    entries: list[dict] = []

    # A1 -- an existing client raising a genuinely new, unrelated matter.
    matter = open_matters[11]
    client = by_id[matter.client_id]
    atty = atty_by_id[matter.responsible_attorney_id]
    new_adverse = _new_org_name(rng)
    term_date = EPOCH.date() - timedelta(days=42)
    entries.append(
        _entry(
            from_name=client.display_name,
            from_email=client.emails[0],
            subject=f"Re: {matter.caption} - and something else",
            body=(
                f"Hi {atty.name.split()[0]},\n\n"
                f"Two things. First, nothing new on {matter.id}, we're still waiting on "
                f"their production and I assume you'll tell me when that lands.\n\n"
                f"Second and more urgent: we terminated a warehouse manager on "
                f"{_fmt_date(term_date, rng)} and yesterday we got a demand letter from "
                f"a firm representing him alleging disability discrimination. Completely "
                f"unrelated to the {matter.id} business. Do you handle that or does it "
                f"go to someone else there? The letter gives us fourteen days.\n\n"
                f"{client.display_name}"
            ),
            label=EmailClass.NEW_MATTER,
            practice_area=PracticeArea.EMPLOYMENT,
            prospective_client=client.display_name,
            opposing=[new_adverse],
            key_dates=[term_date.isoformat()],
            expected_action=DecisionAction.REVIEW,
            ambiguous=True,
            notes=(
                "Existing client, but the substance is a new unrelated matter. Both "
                "labels are defensible; intake should not pick one silently."
            ),
        )
    )

    # A2 -- no signal whatsoever.
    entries.append(
        _entry(
            from_name="D. Marchetti",
            from_email="dmarchetti@gmail.com",
            subject="following up",
            body=(
                "Hi - can someone call me about the thing we discussed? I have the "
                "paperwork now.\n\nSame number as before.\n\nD."
            ),
            label=EmailClass.UNCLEAR,
            expected_action=DecisionAction.REVIEW,
            ambiguous=True,
            notes="No matter type, no parties, no context. Nothing to extract.",
        )
    )

    # A3 -- a vendor pitch that turns into a real legal question halfway through.
    vendor_org = _new_org_name(rng)
    entries.append(
        _entry(
            from_name="Tobias Winslow",
            from_email=f"tobias@{_domain_for(vendor_org)}",
            subject="Partnership opportunity + a question",
            body=(
                f"Hello,\n\n"
                f"I run {vendor_org}, we build intake software for small law firms and "
                f"I'd love to show you what we've built - I think it would save your "
                f"staff several hours a week.\n\n"
                f"Separately, and honestly this is the more pressing thing: we received "
                f"a cease and desist last Tuesday from a company claiming our product "
                f"name infringes their mark. We've been using the name for three years. "
                f"Our response is due in fourteen days and we don't have counsel.\n\n"
                f"Happy to talk about either. Or both.\n\nTobias Winslow\nFounder"
            ),
            label=EmailClass.NEW_MATTER,
            practice_area=PracticeArea.INTELLECTUAL_PROPERTY,
            prospective_client=vendor_org,
            expected_action=DecisionAction.REVIEW,
            ambiguous=True,
            notes=(
                "Opens as a solicitation, closes as a real IP inquiry. Classifying on "
                "the first paragraph loses a client."
            ),
        )
    )

    # A4 -- facts that sit across two practice areas at once.
    injured = EPOCH.date() - timedelta(days=95)
    fired = EPOCH.date() - timedelta(days=22)
    entries.append(
        _entry(
            from_name="Ramona Achebe",
            from_email="r.achebe@outlook.com",
            subject="Injured at work and then let go",
            body=(
                f"Hello,\n\n"
                f"I was hurt on the job on {_fmt_date(injured, rng)} - a pallet stack "
                f"came down on my shoulder because the racking was overloaded. I filed a "
                f"workers comp claim and had surgery in January.\n\n"
                f"On {_fmt_date(fired, rng)} they terminated me. They say it was a "
                f"restructuring. I was the only person let go and I had just sent in my "
                f"return-to-work restrictions.\n\n"
                f"I don't know if this is a workers comp thing, a wrongful termination "
                f"thing, or a lawsuit against the racking company. Maybe all three? My "
                f"medical bills are around $38,000 so far.\n\n"
                f"I'm in {rng.choice(pools.JURISDICTIONS)}.\n\nRamona Achebe"
            ),
            label=EmailClass.NEW_MATTER,
            practice_area=PracticeArea.EMPLOYMENT,
            prospective_client="Ramona Achebe",
            key_dates=[injured.isoformat(), fired.isoformat()],
            amounts=[38000.0],
            expected_action=DecisionAction.REVIEW,
            ambiguous=True,
            notes=(
                "Employment, personal injury, and workers comp all fit. Matter type is "
                "genuinely underdetermined -- the abstention path, not a guess."
            ),
        )
    )

    # A5 -- the actual inquiry is buried under two layers of forwarding.
    closing = EPOCH.date() + timedelta(days=31)
    entries.append(
        _entry(
            from_name="Imani Redmond",
            from_email="imani.redmond@gmail.com",
            subject="Fwd: Fwd: can you look at this",
            body=(
                f"Sending this to you, my brother said you helped him.\n\n"
                f"---------- Forwarded message ----------\n"
                f"From: Curtis Redmond <curtis.r@gmail.com>\n"
                f"Subject: Fwd: can you look at this\n\n"
                f"Imani - forward this to a lawyer, I don't know who to ask.\n\n"
                f"---------- Forwarded message ----------\n"
                f"From: Brookvale Title Services <ops@brookvaletitle.example>\n"
                f"Subject: Title commitment - 4412 Gable Way\n\n"
                f"Ms. Redmond,\n\n"
                f"Our title search on 4412 Gable Way returned an unreleased mortgage "
                f"from 2009 in the name of a prior owner, as well as a mechanic's lien "
                f"recorded in November. We cannot issue a clean commitment until both "
                f"are resolved. Your scheduled closing is {_fmt_date(closing, rng)}.\n\n"
                f"Please advise how you wish to proceed."
            ),
            label=EmailClass.NEW_MATTER,
            practice_area=PracticeArea.REAL_ESTATE,
            prospective_client="Imani Redmond",
            key_dates=[closing.isoformat()],
            expected_action=DecisionAction.REVIEW,
            ambiguous=True,
            notes=(
                "The sender is not the party, the party is not the signer, and the "
                "operative facts are three levels down a forward chain."
            ),
        )
    )

    # A6 -- asking for someone else, no names given.
    entries.append(
        _entry(
            from_name="Felicity Bramble",
            from_email="fbramble@protonmail.com",
            subject="asking for a friend",
            body=(
                "Hi,\n\n"
                "A friend of mine was in a bad accident about two months ago and is "
                "still in the hospital so she asked me to reach out. The other driver "
                "ran a red light and there were witnesses. Her insurance is being "
                "difficult about the medical bills.\n\n"
                "I don't want to share her details without asking her first. Can you "
                "tell me whether this is something you'd take and roughly what it "
                "costs? Then I'll have her contact you directly.\n\n"
                "Thanks,\nFelicity"
            ),
            label=EmailClass.NEW_MATTER,
            practice_area=PracticeArea.PERSONAL_INJURY,
            expected_action=DecisionAction.REVIEW,
            ambiguous=True,
            notes=(
                "Real matter, but the prospective client is deliberately unnamed. "
                "Conflict checking is impossible without the party."
            ),
        )
    )

    # A7 -- not an inquiry at all; a grievance against the firm.
    entries.append(
        _entry(
            from_name="Barnaby Kowalski",
            from_email="bkowalski@yahoo.com",
            subject="This is the third time I am writing",
            body=(
                "To whoever actually reads this inbox,\n\n"
                "I was a client of this firm in 2023 and I am still owed an accounting "
                "of the retainer balance. Two voicemails and one email, no response.\n\n"
                "I am not asking for legal advice. I am asking for my money and my file. "
                "If I do not hear back by Friday I will be contacting the state bar and "
                "I will be taking this to small claims.\n\n"
                "Barnaby Kowalski"
            ),
            label=EmailClass.UNCLEAR,
            expected_action=DecisionAction.REVIEW,
            ambiguous=True,
            notes=(
                "Former client, no new matter, and a bar-complaint threat. Fits none of "
                "the four labels cleanly and must reach a human fast."
            ),
        )
    )

    return entries


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #


def build_emails(
    rng: Random,
    clients: list[ClientRecord],
    matters: list[MatterRecord],
    attorneys: list[Attorney],
    clean_new_matters: int = 20,
) -> tuple[list[Email], list[GroundTruth]]:
    entries: list[dict] = []
    for i in range(clean_new_matters):
        entries.append(_build_new_matter(rng, PRACTICE_AREAS[i % len(PRACTICE_AREAS)]))
    entries.extend(_trap_new_matters(rng, clients, matters))
    entries.extend(_existing_client_emails(rng, clients, matters, attorneys, 9))
    entries.extend(_vendor_spam_emails(rng, 8))
    entries.extend(_ambiguous_emails(rng, clients, matters, attorneys))

    rng.shuffle(entries)
    _mark_incidental_conflicts(entries, clients, matters)

    emails: list[Email] = []
    truths: list[GroundTruth] = []
    start = EPOCH - timedelta(days=11)
    step_minutes = (11 * 24 * 60) // max(len(entries), 1)

    for i, e in enumerate(entries):
        eid = f"em-{i + 1:03d}"
        received = start + timedelta(minutes=i * step_minutes + rng.randrange(0, 45))
        emails.append(
            Email(
                id=eid,
                received_at=received,
                from_name=e["from_name"],
                from_email=e["from_email"],
                to_address="info@vancebrock.example",
                subject=e["subject"],
                body=e["body"],
                prose_source="template",
            )
        )
        truths.append(
            GroundTruth(
                email_id=eid,
                label=e["label"],
                practice_area=e["practice_area"],
                jurisdiction=e["jurisdiction"],
                prospective_client=e["prospective_client"],
                opposing_parties=e["opposing"],
                key_dates=e["key_dates"],
                amounts=e["amounts"],
                planted_trap_rules=e["traps"],
                trap_record_ids=e["trap_record_ids"],
                expected_action=e["expected_action"],
                is_ambiguous=e["ambiguous"],
                notes=e["notes"],
            )
        )
    return emails, truths


def _mark_incidental_conflicts(
    entries: list[dict], clients: list[ClientRecord], matters: list[MatterRecord]
) -> None:
    """Record conflicts the generator created without meaning to.

    Party names for inquiries are drawn from the same pools the client and matter
    records came from, so collisions happen: an inquiry's opposing party turns out
    to be an existing client, or its prospective client turns out to be someone the
    firm once opposed. Those are real conflicts. The firm would want a human on
    them whether or not anyone planted them, so the expected action is REVIEW.

    Without this, the eval scored the pipeline as over-cautious for catching
    genuine conflicts, purely because they were accidents of generation.

    Consumes no randomness and changes no email text -- it only corrects the
    expected action, so cached model responses stay valid.

    Limitation: this uses the same name normalization the matcher uses, so ground
    truth agrees with the matcher by construction on *how* names are compared.
    Trap recall, scored against deliberately planted traps, is the independent
    measure of the rules.
    """
    clients_by_name: dict[str, ClientRecord] = {}
    for client in clients:
        clients_by_name.setdefault(normalize_name(client.display_name), client)

    adverse_by_name: dict[str, MatterRecord] = {}
    for matter in matters:
        for name in matter.adverse_parties:
            adverse_by_name.setdefault(normalize_name(name), matter)

    for entry in entries:
        if entry["traps"]:
            continue  # already expected to reach review

        notes: list[str] = []
        for opposing in entry["opposing"]:
            match = clients_by_name.get(normalize_name(opposing))
            if match:
                notes.append(
                    f"Opposing party '{opposing}' is existing client {match.id} "
                    f"'{match.display_name}' (incidental, not planted)."
                )

        prospect = entry["prospective_client"]
        if prospect:
            match = adverse_by_name.get(normalize_name(prospect))
            if match:
                notes.append(
                    f"Prospective client '{prospect}' is an adverse party on "
                    f"{match.id} (incidental, not planted)."
                )

        if notes:
            entry["expected_action"] = DecisionAction.REVIEW
            entry["notes"] = " ".join([entry["notes"], *notes]).strip()


def generate_corpus(seed: int = DEFAULT_SEED) -> Corpus:
    """Build the whole corpus from one seed.

    Each phase gets its own RNG stream so that changing the number of emails does
    not reshuffle the client list, and vice versa.
    """
    attorneys = build_attorneys(Random(seed + 11))
    clients = build_clients(Random(seed + 101), N_CLIENTS)
    matters = build_matters(Random(seed + 202), clients, attorneys, N_MATTERS)
    emails, truths = build_emails(Random(seed + 303), clients, matters, attorneys)
    return Corpus(
        seed=seed,
        attorneys=attorneys,
        clients=clients,
        matters=matters,
        emails=emails,
        ground_truth=truths,
    )


def write_corpus(corpus: Corpus, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(corpus.model_dump_json(indent=2) + "\n", encoding="utf-8")


def load_corpus(path: Path) -> Corpus:
    return Corpus.model_validate_json(path.read_text(encoding="utf-8"))


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Generate the synthetic intake corpus")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=Path("data/corpus.json"))
    args = parser.parse_args()

    corpus = generate_corpus(args.seed)
    write_corpus(corpus, args.out)

    counts: dict[str, int] = {}
    for gt in corpus.ground_truth:
        counts[gt.label.value] = counts.get(gt.label.value, 0) + 1
    traps: dict[str, int] = {}
    for gt in corpus.ground_truth:
        for rule in gt.planted_trap_rules:
            traps[rule] = traps.get(rule, 0) + 1

    print(f"seed={corpus.seed} -> {args.out}")
    print(f"  clients={len(corpus.clients)} matters={len(corpus.matters)} "
          f"attorneys={len(corpus.attorneys)} emails={len(corpus.emails)}")
    print(f"  labels: {json.dumps(counts, sort_keys=True)}")
    print(f"  ambiguous: {sum(1 for g in corpus.ground_truth if g.is_ambiguous)}")
    print(f"  planted traps: {json.dumps(traps, sort_keys=True)}")


if __name__ == "__main__":
    main()
