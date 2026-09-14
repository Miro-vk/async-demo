VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip
export PYTHONPATH := backend

PROVIDER ?= auto

.PHONY: setup seed test eval naturalize clean

setup:  ## create the virtualenv and install dependencies
	python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e ".[dev]"

seed:  ## regenerate the corpus and rebuild the demo database
	$(PY) -m intake.db.seed --db data/intake.sqlite3

test:  ## run the unit tests (no API key, no network, no database required)
	$(PY) -m pytest backend/tests -q

eval:  ## score classify + extract against ground truth (PROVIDER=auto|live|replay|stub)
	$(PY) -m intake.pipeline.evaluate --provider $(PROVIDER)

naturalize:  ## refresh the cached prose rewrites (requires ANTHROPIC_API_KEY)
	$(PY) -m intake.corpus.naturalize

clean:
	rm -rf data/intake.sqlite3 .pytest_cache
	find backend -name __pycache__ -type d -exec rm -rf {} +
