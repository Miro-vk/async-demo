VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip
export PYTHONPATH := backend

PROVIDER ?= auto

.PHONY: setup seed test eval cache naturalize clean

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

cache:  ## populate data/llm_cache.json from real model calls (requires ANTHROPIC_API_KEY)
	@test -n "$$ANTHROPIC_API_KEY" || (echo "ANTHROPIC_API_KEY is not set"; exit 1)
	$(PY) -m intake.pipeline.evaluate --provider live

naturalize:  ## refresh the cached prose rewrites (requires ANTHROPIC_API_KEY)
	$(PY) -m intake.corpus.naturalize

clean:
	rm -rf data/intake.sqlite3 .pytest_cache
	find backend -name __pycache__ -type d -exec rm -rf {} +
