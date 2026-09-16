"""Turning wire format into English.

Enum values and field paths are how this system talks to itself. A reviewer
reading "Party 'Imani Redmond' (prospective_client) ... extraction.parties[0].name"
is being shown the inside of the program, and it makes a careful system look
careless. Everything a human reads goes through here.

The paths themselves stay machine-readable on `Reason.field_path`, because the UI
uses them to scroll to the offending field. Only the prose is translated.
"""

from __future__ import annotations

import re

from intake.domain.enums import EmailClass, PartyRole, PracticeArea

EMAIL_CLASS = {
    EmailClass.NEW_MATTER: "a new matter",
    EmailClass.EXISTING_CLIENT: "existing-client mail",
    EmailClass.VENDOR_OR_SPAM: "a solicitation",
    EmailClass.UNCLEAR: "unclear",
}

PRACTICE_AREA = {
    PracticeArea.COMMERCIAL_LITIGATION: "commercial litigation",
    PracticeArea.EMPLOYMENT: "employment",
    PracticeArea.REAL_ESTATE: "real estate",
    PracticeArea.INTELLECTUAL_PROPERTY: "intellectual property",
    PracticeArea.FAMILY: "family law",
    PracticeArea.PERSONAL_INJURY: "personal injury",
    PracticeArea.UNKNOWN: "no identifiable area",
}

CONFLICT_RULE = {
    "ADVERSE_PARTY_IS_CLIENT": "Acting against a client",
    "PROSPECT_WAS_ADVERSE_PARTY": "Acted against this party before",
    "SENDER_DOMAIN_MATCHES_CLIENT": "Sender's domain belongs to a client",
}

# Which column of which record the rule actually read. A reviewer checking the
# finding by hand needs to know where to look, and "adverse_parties" is a column
# name rather than a place.
MATCHED_FIELD = {
    "display_name": "the client's name on file",
    "adverse_parties": "the adverse parties on that matter",
    "domains": "the domains on file for that client",
}

# Entity-type abbreviations as a lawyer writes them, not as the matcher keys them.
ENTITY_TYPE = {
    "LLC": "LLC", "INC": "Inc.", "CORP": "Corp.", "LTD": "Ltd.", "LLP": "LLP",
    "PLLC": "PLLC", "PLC": "PLC", "CO": "Co.", "LP": "LP", "PC": "P.C.",
    "PA": "P.A.", "GMBH": "GmbH", "SA": "S.A.", "NV": "N.V.", "BV": "B.V.",
}

RECORD_KIND = {
    "client": "Client record",
    "matter": "Matter record",
}

PARTY_ROLE = {
    PartyRole.PROSPECTIVE_CLIENT: "the prospective client",
    PartyRole.OPPOSING: "the opposing party",
    PartyRole.THIRD_PARTY: "a third party",
    PartyRole.UNKNOWN: "a party whose role is unclear",
}

# Field paths, longest pattern first so the indexed forms win over their prefixes.
_FIELD_NAMES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^extraction\.parties\[(\d+)\]\.name$"), "the {ordinal} party's name"),
    (re.compile(r"^extraction\.key_dates\[(\d+)\]\.value$"), "the {ordinal} date"),
    (re.compile(r"^extraction\.amounts\[(\d+)\]\.value$"), "the {ordinal} amount"),
    (re.compile(r"^resolution\.conflicts\[(\d+)\]$"), "the conflicts check"),
    (re.compile(r"^extraction\.matter_type$"), "the practice area"),
    (re.compile(r"^extraction\.jurisdiction$"), "the jurisdiction"),
    (re.compile(r"^extraction\.parties$"), "the parties"),
    (re.compile(r"^classification\.label$"), "the classification"),
    (re.compile(r"^classification$"), "the classification"),
    (re.compile(r"^dispatch\.attorney_id$"), "routing"),
    (re.compile(r"^dispatch$"), "routing"),
    (re.compile(r"^resolution$"), "the conflicts check"),
    (re.compile(r"^extraction$"), "the extracted facts"),
]


def ordinal(index: int) -> str:
    """0-based index to a 1-based ordinal: 0 -> first, 1 -> second."""
    words = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh",
             "eighth", "ninth", "tenth"]
    return words[index] if index < len(words) else f"{index + 1}th"


def field_name(path: str | None) -> str:
    """A noun phrase for a field path, for putting in a sentence."""
    if not path:
        return "this email"
    for pattern, template in _FIELD_NAMES:
        match = pattern.match(path)
        if match:
            if "{ordinal}" in template:
                return template.format(ordinal=ordinal(int(match.group(1))))
            return template
    # Anything unmapped still reads as words rather than as a path.
    return path.rsplit(".", 1)[-1].replace("_", " ")


def field_heading(path: str | None) -> str:
    """Title-case form, for a label beside a reason rather than inside a sentence."""
    name = field_name(path)
    for prefix in ("the ", "a "):
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    return name[:1].upper() + name[1:]


def email_class(label: EmailClass | None) -> str:
    return EMAIL_CLASS.get(label, "unclassified") if label else "unclassified"


def practice_area(area: PracticeArea | None) -> str:
    return PRACTICE_AREA.get(area, "no identifiable area") if area else "no identifiable area"


def party_role(role: PartyRole | None) -> str:
    return PARTY_ROLE.get(role, "a party") if role else "a party"


def conflict_rule(rule_id: str | None) -> str:
    """The name of a conflict rule, as a person would say it.

    Unmapped ids still come out as words rather than as a constant, so a rule
    added without a label here degrades to readable instead of to shouting.
    """
    if not rule_id:
        return "Conflicts rule"
    known = CONFLICT_RULE.get(rule_id)
    if known:
        return known
    words = rule_id.replace("_", " ").lower()
    return words[:1].upper() + words[1:]


def matched_field(name: str | None) -> str:
    if not name:
        return "an unnamed field"
    return MATCHED_FIELD.get(name, name.replace("_", " "))


def record_kind(kind: str | None) -> str:
    if not kind:
        return "Record"
    return RECORD_KIND.get(kind, kind.replace("_", " ").capitalize())


def entity_type(code: str | None) -> str:
    """An entity-type code as it is written on a letterhead: LTD -> Ltd."""
    if not code:
        return "no stated entity type"
    return ENTITY_TYPE.get(code.upper(), code)


def stop(text: str) -> str:
    """End a sentence without doubling a full stop.

    Company names end in "Ltd." and "Inc." often enough that a naive f-string
    produces "a matter run for Harborview Hospitality Group Co..", which is the
    kind of detail that tells a reader nobody looked.
    """
    return text if text.rstrip().endswith(".") else text + "."


def typeset(text: str) -> str:
    """ASCII dash stand-ins to real dashes, for prose about a record.

    The firm's stored captions use "--" the way a typewriter did. That spelling is
    load-bearing everywhere else -- it is part of the email text the model was
    prompted with, and the cached responses are keyed on that text -- so it is
    fixed here, where a person reads it, rather than in the data.
    """
    return text.replace(" -- ", " \u2014 ").replace("--", "\u2014")


def date_in_words(value: object) -> str:
    """A date a reviewer can read without parsing it: 11 March 2024."""
    try:
        return f"{value.day} {value.strftime('%B %Y')}"  # type: ignore[attr-defined]
    except AttributeError:
        return str(value)
