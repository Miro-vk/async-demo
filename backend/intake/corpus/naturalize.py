"""Rewrite templated inquiry prose so it reads like real inbox mail.

The generator produces correct but stiff prose -- every commercial-litigation email
has the same four paragraphs in the same order. This pass sends each email to Claude
to be rewritten in a different voice, and caches the result in `data/naturalized.json`
so the corpus stays reproducible without an API key.

The important part is the verifier. A rewrite that drops or alters a planted fact
would silently corrupt ground truth and make every eval number meaningless, so each
rewritten body is checked for the surface form of every fact the generator planted.
A rewrite that loses one is rejected and the template prose is kept. Fail closed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from intake.paths import NATURALIZED_PATH
from intake.domain.models import Corpus, Email, GroundTruth

OVERLAY_PATH = NATURALIZED_PATH

SYSTEM_PROMPT = """You rewrite synthetic law-firm intake emails so they read like \
real mail a person actually typed.

Rules:
- Preserve every name, date, dollar amount, address, and place name EXACTLY as \
written, character for character. Do not reformat a date or round an amount.
- Change the voice, structure, paragraph order, greeting and sign-off. Vary the \
register: some senders are terse, some ramble, some are angry, some are formal.
- Real people write on phones, bury the important fact in paragraph three, use \
lowercase, and leave typos. Some of these should.
- Do not add new legal facts, new parties, or new dates.
- Keep it the same length or shorter.

Return JSON only: {"subject": "...", "body": "..."}"""


@dataclass(frozen=True)
class Fact:
    """One planted fact and the surface forms that count as preserving it."""

    label: str
    accepted_forms: list[str]

    def satisfied_by(self, text: str) -> bool:
        return any(form in text for form in self.accepted_forms)


def _date_surface_forms(iso: str) -> list[str]:
    d = date.fromisoformat(iso)
    return [
        d.strftime("%B %-d, %Y"),
        f"{d.month}/{d.day}/{d.year}",
        d.strftime("%d %B %Y").lstrip("0"),
        d.strftime("%b %-d, %Y"),
    ]


def _amount_surface_forms(amount: float) -> list[str]:
    forms = [f"${amount:,.0f}"]
    if amount >= 1000:
        forms.append(f"${amount / 1000:.0f}k")
    return forms


def required_facts(email: Email, gt: GroundTruth) -> list[Fact]:
    """Facts that must survive the rewrite.

    Only facts whose surface form is actually present in the template are required --
    otherwise the verifier would demand text that was never there to begin with.
    """
    source = f"{email.subject}\n\n{email.body}"
    candidates: list[Fact] = []

    if gt.prospective_client:
        candidates.append(Fact(f"party:{gt.prospective_client}", [gt.prospective_client]))
    for party in gt.opposing_parties:
        candidates.append(Fact(f"opposing:{party}", [party]))
    if gt.jurisdiction:
        candidates.append(Fact(f"jurisdiction:{gt.jurisdiction}", [gt.jurisdiction]))
    for iso in gt.key_dates:
        candidates.append(Fact(f"date:{iso}", _date_surface_forms(iso)))
    for amount in gt.amounts:
        candidates.append(Fact(f"amount:{amount}", _amount_surface_forms(amount)))

    return [f for f in candidates if f.satisfied_by(source)]


def verify(subject: str, body: str, facts: list[Fact]) -> list[str]:
    """Return the labels of facts the rewrite lost. Empty means the rewrite is safe."""
    text = f"{subject}\n\n{body}"
    return [f.label for f in facts if not f.satisfied_by(text)]


def template_hash(email: Email) -> str:
    """Identifies the exact template prose a cached rewrite was derived from, so a
    change to the generator marks its cached rewrites stale instead of silently
    pairing new facts with old prose."""
    payload = f"{email.subject}\n\n{email.body}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


@dataclass(frozen=True)
class OverlayResult:
    email_id: str
    status: str  # applied | rejected_missing_facts | stale | absent
    detail: str = ""


def apply_overlay(
    corpus: Corpus, overlay: dict
) -> tuple[Corpus, list[OverlayResult]]:
    """Swap naturalized prose into the corpus where it is present and verifiable.

    Anything questionable keeps its template prose. The demo would rather read a
    little stiff than run on a corpus whose ground truth has drifted.
    """
    entries = overlay.get("entries", {})
    truth_by_id = {g.email_id: g for g in corpus.ground_truth}
    results: list[OverlayResult] = []
    emails: list[Email] = []

    for email in corpus.emails:
        entry = entries.get(email.id)
        if not entry:
            emails.append(email)
            results.append(OverlayResult(email.id, "absent"))
            continue

        if entry.get("template_hash") != template_hash(email):
            emails.append(email)
            results.append(
                OverlayResult(email.id, "stale", "template prose changed since rewrite")
            )
            continue

        facts = required_facts(email, truth_by_id[email.id])
        missing = verify(entry["subject"], entry["body"], facts)
        if missing:
            emails.append(email)
            results.append(
                OverlayResult(email.id, "rejected_missing_facts", ", ".join(missing))
            )
            continue

        emails.append(
            email.model_copy(
                update={
                    "subject": entry["subject"],
                    "body": entry["body"],
                    "prose_source": "naturalized",
                }
            )
        )
        results.append(OverlayResult(email.id, "applied"))

    return corpus.model_copy(update={"emails": emails}), results


def load_overlay(path: Path = OVERLAY_PATH) -> dict:
    if not path.exists():
        return {"entries": {}}
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Live regeneration (requires ANTHROPIC_API_KEY)
# --------------------------------------------------------------------------- #


def build_user_prompt(email: Email, facts: list[Fact]) -> str:
    must_keep = "\n".join(f"- {f.accepted_forms[0]}" for f in facts)
    return (
        f"Rewrite this email.\n\n"
        f"Subject: {email.subject}\n\n{email.body}\n\n"
        f"---\nThese strings must appear verbatim in your rewrite:\n{must_keep}"
    )


def main() -> None:
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Regenerate the naturalized prose cache")
    parser.add_argument("--corpus", type=Path, default=Path("data/corpus.json"))
    parser.add_argument("--out", type=Path, default=OVERLAY_PATH)
    parser.add_argument("--model", default="claude-opus-5")
    parser.add_argument(
        "--only", nargs="*", default=None, help="email ids to rewrite (default: all)"
    )
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set. The checked-in data/naturalized.json is the "
            "cached output of this pass; you only need a key to regenerate it."
        )

    import anthropic  # imported lazily so the corpus loads without the SDK

    from intake.corpus.generate import load_corpus

    corpus = load_corpus(args.corpus)
    truth_by_id = {g.email_id: g for g in corpus.ground_truth}
    client = anthropic.Anthropic()

    overlay = load_overlay(args.out)
    entries = overlay.setdefault("entries", {})
    overlay["model"] = args.model

    for email in corpus.emails:
        if args.only and email.id not in args.only:
            continue
        facts = required_facts(email, truth_by_id[email.id])
        response = client.messages.create(
            model=args.model,
            max_tokens=1600,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_prompt(email, facts)}],
        )
        raw = response.content[0].text.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            print(f"{email.id}: unparseable response, keeping template prose")
            continue

        missing = verify(parsed["subject"], parsed["body"], facts)
        if missing:
            print(f"{email.id}: rewrite dropped {missing}, keeping template prose")
            continue

        entries[email.id] = {
            "template_hash": template_hash(email),
            "subject": parsed["subject"],
            "body": parsed["body"],
        }
        print(f"{email.id}: ok")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(overlay, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(entries)} rewrites to {args.out}")


if __name__ == "__main__":
    main()
