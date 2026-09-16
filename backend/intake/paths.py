"""Where the demo's data files live.

Anchored to the repository rather than the working directory, so `make eval`, a
pytest run, and the API server all read the same corpus and the same response
cache regardless of where they were started from.
"""

from __future__ import annotations

import os
from pathlib import Path

# Walking up from this file is right for a checkout and for an editable install,
# and wrong for a normal install, where it lands in site-packages. INTAKE_ROOT
# makes the deployment say where the data lives instead of the layout implying it.
_override = os.environ.get("INTAKE_ROOT")
REPO_ROOT = Path(_override).resolve() if _override else Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"

CORPUS_PATH = DATA_DIR / "corpus.json"
NATURALIZED_PATH = DATA_DIR / "naturalized.json"
LLM_CACHE_PATH = DATA_DIR / "llm_cache.json"
DATABASE_PATH = DATA_DIR / "intake.sqlite3"

# Built front end, when there is one. In development Vite serves it on :5173 and
# proxies /api here; in the container it is built once and served by this process,
# so the demo is a single port and a single thing to run.
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
