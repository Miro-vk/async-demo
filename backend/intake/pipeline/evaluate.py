"""Score the classify and extract stages against the corpus's ground truth.

The generator knows every fact it planted, so stage quality is measurable rather
than asserted. Two numbers matter more than raw accuracy:

  Span health -- what fraction of extracted values could be grounded in the source.
  An extractor that is accurate but ungroundable is useless here, because nobody
  can check it.

  Confidence separation -- mean confidence on the classifications that were right
  versus the ones that were wrong. If those two numbers are close, the confidence
  score is decorative and the abstention threshold cannot work. This is the number
  that tells you whether the whole design is doing anything.

Run against whichever provider is configured. With the stub that means you are
measuring regexes, and the report says so.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from intake.db import repo
from intake.domain.enums import EmailClass, PracticeArea, SpanStatus
from intake.domain.models import Classification, Extraction, GroundTruth, ParseFailure
from intake.llm.client import build_provider
from intake.pipeline.runner import run_classify, run_extract


def _norm(text: str | None) -> str:
    return (text or "").strip().lower()


@dataclass
class Report:
    provider: str = ""
    total: int = 0
    classify_correct: int = 0
    classify_failures: int = 0
    confusion: Counter = field(default_factory=Counter)
    conf_when_right: list[float] = field(default_factory=list)
    conf_when_wrong: list[float] = field(default_factory=list)

    extract_failures: int = 0
    area_correct: int = 0
    area_total: int = 0
    juris_correct: int = 0
    juris_total: int = 0
    party_found: int = 0
    party_total: int = 0
    date_found: int = 0
    date_total: int = 0
    amount_found: int = 0
    amount_total: int = 0
    span_status: Counter = field(default_factory=Counter)

    ambiguous_ids: list[str] = field(default_factory=list)
    conf_on_ambiguous: list[float] = field(default_factory=list)
    conf_on_clear: list[float] = field(default_factory=list)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def score_classification(result, gt: GroundTruth, report: Report) -> None:
    if isinstance(result, ParseFailure):
        report.classify_failures += 1
        return
    assert isinstance(result, Classification)
    predicted = result.label.value
    correct = predicted == gt.label
    report.classify_correct += int(correct)
    report.confusion[(gt.label.value, predicted.value)] += 1
    (report.conf_when_right if correct else report.conf_when_wrong).append(
        result.label.confidence
    )
    (report.conf_on_ambiguous if gt.is_ambiguous else report.conf_on_clear).append(
        result.label.confidence
    )


def score_extraction(result, gt: GroundTruth, report: Report) -> None:
    if isinstance(result, ParseFailure):
        report.extract_failures += 1
        return
    assert isinstance(result, Extraction)

    for field_ in _all_traced_fields(result):
        report.span_status[
            field_.span.status.value if field_.span else SpanStatus.ABSENT.value
        ] += 1

    if gt.practice_area and gt.practice_area != PracticeArea.UNKNOWN:
        report.area_total += 1
        report.area_correct += int(result.matter_type.value == gt.practice_area)

    if gt.jurisdiction:
        report.juris_total += 1
        report.juris_correct += int(_norm(result.jurisdiction.value) == _norm(gt.jurisdiction))

    found_names = {_norm(p.name.value) for p in result.parties}
    expected = [gt.prospective_client] if gt.prospective_client else []
    expected += list(gt.opposing_parties)
    for name in expected:
        report.party_total += 1
        report.party_found += int(_norm(name) in found_names)

    found_dates = {d.value.value.isoformat() for d in result.key_dates if d.value.value}
    for iso in gt.key_dates:
        report.date_total += 1
        report.date_found += int(iso in found_dates)

    found_amounts = {a.value.value for a in result.amounts if a.value.value is not None}
    for amount in gt.amounts:
        report.amount_total += 1
        report.amount_found += int(any(abs(amount - f) < 0.01 for f in found_amounts))


def _all_traced_fields(extraction: Extraction) -> list:
    fields = [extraction.matter_type, extraction.jurisdiction]
    fields += [p.name for p in extraction.parties]
    fields += [d.value for d in extraction.key_dates]
    fields += [a.value for a in extraction.amounts]
    return [f for f in fields if f.value is not None]


def evaluate(db_path: Path, provider_mode: str, limit: int | None = None) -> Report:
    conn = repo.connect(db_path)
    try:
        emails = repo.list_emails(conn)
        truth = {g.email_id: g for g in repo.list_ground_truth(conn)}
    finally:
        conn.close()

    if limit:
        emails = emails[:limit]

    provider = build_provider(provider_mode)
    report = Report(provider=getattr(provider, "name", provider_mode))

    for email in emails:
        gt = truth[email.id]
        report.total += 1
        if gt.is_ambiguous:
            report.ambiguous_ids.append(email.id)
        score_classification(run_classify(email, provider).output, gt, report)
        score_extraction(run_extract(email, provider).output, gt, report)

    return report


def _pct(numerator: int, denominator: int) -> str:
    if not denominator:
        return "   n/a"
    return f"{numerator / denominator:6.1%}"


def print_report(report: Report) -> None:
    print(f"\nProvider: {report.provider}   Emails: {report.total}")
    if report.provider == "stub":
        print("  NOTE: the stub provider is keyword and regex heuristics, not a model.")
        print("        These numbers measure the stand-in, not the pipeline's ceiling.")

    print("\nCLASSIFY")
    print(f"  accuracy          {_pct(report.classify_correct, report.total)}"
          f"  ({report.classify_correct}/{report.total})")
    print(f"  unparseable       {report.classify_failures}")

    print("\n  confusion (actual -> predicted, wrong only)")
    wrong = {k: v for k, v in report.confusion.items() if k[0] != k[1]}
    if not wrong:
        print("    none")
    for (actual, predicted), count in sorted(wrong.items(), key=lambda kv: -kv[1]):
        print(f"    {actual:<16} -> {predicted:<16} {count}")

    right, wrong_conf = _mean(report.conf_when_right), _mean(report.conf_when_wrong)
    print("\n  confidence separation")
    print(f"    mean when correct   {right:.3f}")
    print(f"    mean when wrong     {wrong_conf:.3f}")
    print(f"    gap                 {right - wrong_conf:+.3f}"
          f"   {'(confidence is informative)' if right - wrong_conf > 0.05 else '(WEAK -- thresholds cannot separate these)'}")

    amb, clear = _mean(report.conf_on_ambiguous), _mean(report.conf_on_clear)
    print(f"    mean on ambiguous   {amb:.3f}  ({len(report.ambiguous_ids)} emails)")
    print(f"    mean on clear-cut   {clear:.3f}")
    print(f"    gap                 {clear - amb:+.3f}"
          f"   {'(hesitates where it should)' if clear - amb > 0.05 else '(does not hesitate on hard mail)'}")

    print("\nEXTRACT")
    print(f"  unparseable       {report.extract_failures}")
    print(f"  practice area     {_pct(report.area_correct, report.area_total)}"
          f"  ({report.area_correct}/{report.area_total})")
    print(f"  jurisdiction      {_pct(report.juris_correct, report.juris_total)}"
          f"  ({report.juris_correct}/{report.juris_total})")
    print(f"  party recall      {_pct(report.party_found, report.party_total)}"
          f"  ({report.party_found}/{report.party_total})")
    print(f"  date recall       {_pct(report.date_found, report.date_total)}"
          f"  ({report.date_found}/{report.date_total})")
    print(f"  amount recall     {_pct(report.amount_found, report.amount_total)}"
          f"  ({report.amount_found}/{report.amount_total})")

    total_spans = sum(report.span_status.values())
    print(f"\n  span health ({total_spans} extracted values)")
    for status in ["verified", "normalized", "absent", "not_found"]:
        count = report.span_status.get(status, 0)
        print(f"    {status:<12} {_pct(count, total_spans)}  ({count})")
    ungrounded = report.span_status.get("not_found", 0)
    if ungrounded:
        print(f"    {ungrounded} value(s) quoted text that is not in the email.")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Score classify and extract on the corpus")
    parser.add_argument("--db", type=Path, default=Path("data/intake.sqlite3"))
    parser.add_argument("--provider", default="auto",
                        choices=["auto", "live", "replay", "stub"])
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    print_report(evaluate(args.db, args.provider, args.limit))


if __name__ == "__main__":
    main()
