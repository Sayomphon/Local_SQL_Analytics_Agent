PYTHON ?= python3.11
VENV := .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python

.PHONY: setup db test lint eval-gold demo-success demo-repair demo-blocked clean

setup:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

db:
	$(PY) scripts/create_demo_db.py

test:
	$(VENV)/bin/pytest

lint:
	$(VENV)/bin/ruff check src tests scripts

# Validates the evaluation harness end-to-end by replaying gold SQL (no LLM needed).
eval-gold:
	$(PY) scripts/run_eval.py --backend gold-replay

# Deterministic golden demo scenarios (scripted LLM responses, no GPU needed).
demo-success:
	$(PY) scripts/demo_cli.py --scenario success

demo-repair:
	$(PY) scripts/demo_cli.py --scenario repair

demo-blocked:
	$(PY) scripts/demo_cli.py --scenario blocked

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache src/*.egg-info
