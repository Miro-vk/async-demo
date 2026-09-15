"""Run the inbox through the pipeline and store the results.

Deterministic against a populated response cache: same corpus, same cache, same
decisions every time.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path

from intake.db import repo
from intake.domain import policy
from intake.domain.records import FirmRecords
from intake.llm.client import build_provider
from intake.paths import DATABASE_PATH
from intake.pipeline.orchestrator import process_email

# A fixed processing timestamp keeps re-runs byte-identical. The demo's clock is
# the corpus's clock, not the wall.
PROCESSED_AT = datetime(2026, 3, 2, 10, 0, 0)


def process_inbox(db_path: Path, provider_mode: str = "auto", verbose: bool = True) -> dict:
    conn = repo.connect(db_path)
    try:
        repo.initialize(conn)
        emails = repo.list_emails(conn)
        records = FirmRecords(repo.list_clients(conn), repo.list_matters(conn))
        attorneys = repo.list_attorneys(conn)
        provider = build_provider(provider_mode)

        counts: Counter = Counter()
        for email in emails:
            result = process_email(email, provider, records, attorneys, policy.DEFAULT)
            repo.save_run(conn, result, PROCESSED_AT)
            counts[result.decision.action.value] += 1
            if verbose:
                reasons = ", ".join(sorted({r.code.value for r in result.decision.reasons}))
                print(f"  {email.id}  {result.decision.action.value:<8} {reasons}")
        return dict(counts)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the inbox through the pipeline")
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--provider", default="auto",
                        choices=["auto", "live", "replay", "stub"])
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    counts = process_inbox(args.db, args.provider, verbose=not args.quiet)
    total = sum(counts.values())
    print(f"\nprocessed {total} emails")
    for action in ("proceed", "review", "stop"):
        n = counts.get(action, 0)
        print(f"  {action:<8} {n:>3}  ({n / total:.0%})" if total else f"  {action}: 0")
    print("\nNothing has been sent. Approved drafts reach 'approved_not_sent' and stop there.")


if __name__ == "__main__":
    main()
