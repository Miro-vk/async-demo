"""Build the demo database: generate the corpus, apply naturalized prose, load SQLite.

Deterministic end to end. Running this twice with the same seed produces the same
database contents, which is what makes a demo repeatable in front of an audience.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from intake.corpus.generate import DEFAULT_SEED, generate_corpus, load_corpus, write_corpus
from intake.corpus.naturalize import OVERLAY_PATH, apply_overlay, load_overlay
from intake.db import repo
from intake.domain.models import Corpus

DEFAULT_DB = Path("data/intake.sqlite3")
DEFAULT_CORPUS = Path("data/corpus.json")


def build_corpus(
    seed: int = DEFAULT_SEED,
    overlay_path: Path = OVERLAY_PATH,
    verbose: bool = True,
) -> Corpus:
    """Generate the corpus and swap in naturalized prose where it verifies."""
    corpus = generate_corpus(seed)
    corpus, results = apply_overlay(corpus, load_overlay(overlay_path))

    if verbose:
        counts = Counter(r.status for r in results)
        print(
            f"  prose: {counts['applied']} naturalized, "
            f"{counts['absent']} template, "
            f"{counts['stale']} stale, "
            f"{counts['rejected_missing_facts']} rejected"
        )
        for r in results:
            if r.status in ("stale", "rejected_missing_facts"):
                print(f"    {r.email_id}: {r.status} ({r.detail}) -- kept template prose")

    return corpus


def seed_database(corpus: Corpus, db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = repo.connect(db_path)
    try:
        repo.initialize(conn)
        repo.insert_corpus(conn, corpus)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the intake demo database")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--overlay", type=Path, default=OVERLAY_PATH)
    parser.add_argument(
        "--from-file",
        action="store_true",
        help="load the corpus JSON instead of regenerating it",
    )
    args = parser.parse_args()

    if args.from_file:
        print(f"loading corpus from {args.corpus}")
        corpus = load_corpus(args.corpus)
    else:
        print(f"generating corpus (seed={args.seed})")
        corpus = build_corpus(args.seed, args.overlay)
        write_corpus(corpus, args.corpus)
        print(f"  wrote {args.corpus}")

    seed_database(corpus, args.db)

    conn = repo.connect(args.db)
    try:
        print(f"seeded {args.db}")
        print(
            f"  attorneys={len(repo.list_attorneys(conn))} "
            f"clients={len(repo.list_clients(conn))} "
            f"matters={len(repo.list_matters(conn))} "
            f"emails={len(repo.list_emails(conn))}"
        )
        labels = Counter(g.label.value for g in repo.list_ground_truth(conn))
        print(f"  labels: {dict(sorted(labels.items()))}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
