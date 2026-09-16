"""Reducing names to a form two spellings of the same entity can agree on.

This is deliberately naive string matching, and it is the system's most significant
stated limitation. "Meridian Manufacturing Corp." and "Meridian Mfg. Corp." are the
same company, and this module gets that right. "Meridian Manufacturing Corp." and
its wholly-owned subsidiary under a completely different name are also, for conflicts
purposes, often the same problem -- and this module has no idea. There is no
corporate-family graph here, no DUNS lookup, no beneficial-ownership data.

What that buys is explainability. Every match this produces can be shown to a human
as "these two strings reduce to the same thing, here is the reduction". A reviewer
can check it in a second and overrule it. That is the trade this demo is making on
purpose: a matcher that is weaker but auditable, feeding a policy that abstains
rather than one that is cleverer and silently wrong.
"""

from __future__ import annotations

import re

CORPORATE_SUFFIXES = {
    "llc", "inc", "incorporated", "corp", "corporation", "ltd", "limited",
    "llp", "pllc", "plc", "co", "company", "lp", "pc", "pa", "gmbh", "sa", "nv", "bv",
}

# Corporate suffixes grouped by the entity type they denote. Spellings inside a
# group are the same entity written differently; spellings in different groups are
# different registrations. "Meridian Mfg. Inc." and "Meridian Manufacturing
# Incorporated" are one company. "Summit Ridge Systems Ltd." and "Summit Ridge
# Systems LLP" are two, and treating them as one puts a false certainty in front of
# whoever has to clear the conflict.
SUFFIX_CLASSES = {
    "llc": "LLC",
    "inc": "INC", "incorporated": "INC",
    "corp": "CORP", "corporation": "CORP",
    "ltd": "LTD", "limited": "LTD",
    "llp": "LLP",
    "pllc": "PLLC",
    "plc": "PLC",
    "co": "CO", "company": "CO",
    "lp": "LP",
    "pc": "PC", "pa": "PA",
    "gmbh": "GMBH", "sa": "SA", "nv": "NV", "bv": "BV",
}

PERSONAL_TITLES = {"mr", "mrs", "ms", "miss", "dr", "prof", "sir", "madam"}
PERSONAL_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "esq", "phd", "md", "cpa"}

# Consonant-skeleton abbreviations that prefix matching cannot catch. This is a
# hand-maintained dictionary, which is exactly as fragile as it sounds -- a real
# system would need something far larger, and would still miss cases.
ABBREVIATION_EXPANSIONS = {
    "mfg": "manufacturing",
    "grp": "group",
    "ptnrs": "partners",
    "hldgs": "holdings",
    "svcs": "services",
    "mgmt": "management",
    "intl": "international",
    "natl": "national",
    "bros": "brothers",
    "assn": "association",
    "dept": "department",
    "cap": "capital",
    "assoc": "associates",
    "constr": "construction",
    "distrib": "distribution",
    "diag": "diagnostics",
    "hosp": "hospitality",
    "ind": "industries",
    "auto": "automotive",
    "log": "logistics",
}

# Mailbox providers. A shared address here says two people use the same email
# service, nothing more. Treating one as an identity signal would match a
# prospective client against every gmail user in the database.
GENERIC_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "yahoo.com", "ymail.com", "protonmail.com", "proton.me", "icloud.com",
    "me.com", "mac.com", "aol.com", "gmx.com", "zoho.com", "mail.com",
    "fastmail.com", "hey.com", "msn.com", "comcast.net", "verizon.net",
}

MIN_PREFIX_MATCH = 3


def _strip_punctuation(text: str) -> str:
    # "L.L.C." -> "llc", "O'Brien & Sons, Inc." -> "obrien sons inc"
    text = text.replace("&", " and ")
    return re.sub(r"[^\w\s]", "", text)


def tokenize(name: str) -> list[str]:
    """Lowercase word tokens with punctuation removed. Order preserved."""
    return _strip_punctuation(name or "").lower().split()


def expand_token(token: str) -> str:
    return ABBREVIATION_EXPANSIONS.get(token, token)


def normalize_org_name(name: str | None) -> str:
    """Reduce a company name to its distinguishing words.

    Drops corporate suffixes, expands known abbreviations, and collapses
    whitespace and punctuation. `Meridian Manufacturing Corp.`, `Meridian Mfg.
    Corp.` and `Meridian Manufacturing, Corporation` all reduce to
    `meridian manufacturing`.
    """
    tokens = [expand_token(t) for t in tokenize(name)]
    stripped = [t for t in tokens if t not in CORPORATE_SUFFIXES]
    # A name made entirely of suffix words ("The Company Ltd") keeps them rather
    # than normalizing to the empty string, which would match everything.
    return " ".join(stripped or tokens)


def normalize_person_name(name: str | None) -> str:
    """Drop titles and honorifics; handle "Last, First" ordering."""
    raw = (name or "").strip()
    if raw.count(",") == 1:
        last, _, first = raw.partition(",")
        if first.strip() and len(first.split()) <= 2:
            raw = f"{first.strip()} {last.strip()}"

    tokens = tokenize(raw)
    tokens = [t for t in tokens if t not in PERSONAL_TITLES and t not in PERSONAL_SUFFIXES]
    return " ".join(tokens)


def normalize_name(name: str | None) -> str:
    """Normalize without being told whether this is a person or a company.

    Extracted parties do not reliably carry that flag, so both reductions are
    applied: titles and corporate suffixes are both dropped.
    """
    tokens = [expand_token(t) for t in tokenize(_reorder_comma_name(name))]
    stripped = [
        t
        for t in tokens
        if t not in CORPORATE_SUFFIXES
        and t not in PERSONAL_TITLES
        and t not in PERSONAL_SUFFIXES
    ]
    return " ".join(stripped or tokens)


def _reorder_comma_name(name: str | None) -> str:
    raw = (name or "").strip()
    if raw.count(",") == 1:
        last, _, first = raw.partition(",")
        first_tokens = first.strip().split()
        # "Smith, John" reorders; "Meridian Capital, LLC" must not.
        if first_tokens and len(first_tokens) <= 2 and not any(
            t.strip(".").lower() in CORPORATE_SUFFIXES for t in first_tokens
        ):
            return f"{first.strip()} {last.strip()}"
    return raw


def tokens_match(left: str, right: str) -> bool:
    """Two tokens count as the same word if one abbreviates the other.

    Prefix matching catches `constr`/`construction` and `assoc`/`associates`;
    the expansion dictionary above catches the rest.
    """
    if left == right:
        return True
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    return len(shorter) >= MIN_PREFIX_MATCH and longer.startswith(shorter)


def token_similarity(left: str, right: str) -> float:
    """Fraction of the larger name's words that the smaller one accounts for.

    Symmetric, 0.0 to 1.0. Dividing by the larger token count means
    `Meridian` alone does not score 1.0 against `Meridian Capital Partners` --
    a bare first word should not look like a confident match.
    """
    left_tokens = [expand_token(t) for t in normalize_name(left).split()]
    right_tokens = [expand_token(t) for t in normalize_name(right).split()]
    if not left_tokens or not right_tokens:
        return 0.0

    remaining = list(right_tokens)
    matched = 0
    for token in left_tokens:
        for index, candidate in enumerate(remaining):
            if tokens_match(token, candidate):
                matched += 1
                remaining.pop(index)
                break

    return matched / max(len(left_tokens), len(right_tokens))


def email_domain(address: str | None) -> str | None:
    if not address or "@" not in address:
        return None
    domain = address.rsplit("@", 1)[1].strip().lower()
    return domain or None


def is_generic_domain(domain: str | None) -> bool:
    return domain is not None and domain.lower() in GENERIC_EMAIL_DOMAINS


def suffix_class(name: str | None) -> str | None:
    """The entity type a name ends in, or None if it names no type.

    None is compatible with everything: a name written without its suffix is the
    same company, and catching exactly that is one of the corpus's planted traps.
    """
    tokens = tokenize(name)
    if not tokens:
        return None
    return SUFFIX_CLASSES.get(tokens[-1])


def suffixes_compatible(left: str | None, right: str | None) -> bool:
    """Whether two names could denote the same registered entity.

    Compatible when they agree, or when either omits its suffix. Incompatible when
    both are stated and differ -- those are two entities that may well be related,
    which this system has no way to determine and does not pretend to.
    """
    left_class, right_class = suffix_class(left), suffix_class(right)
    if left_class is None or right_class is None:
        return True
    return left_class == right_class
