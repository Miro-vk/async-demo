"""Where the demo's data files live.

Anchored to the repository rather than the working directory, so `make eval`, a
pytest run, and the API server all read the same corpus and the same response
cache regardless of where they were started from.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"

CORPUS_PATH = DATA_DIR / "corpus.json"
NATURALIZED_PATH = DATA_DIR / "naturalized.json"
LLM_CACHE_PATH = DATA_DIR / "llm_cache.json"
DATABASE_PATH = DATA_DIR / "intake.sqlite3"
