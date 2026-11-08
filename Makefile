PYTHON ?= python3
VENV := .venv
BIN := $(VENV)/bin

.PHONY: install install-model db seed migrate seed-linking test run dev

$(BIN)/python:
	$(PYTHON) -m venv $(VENV)

install: $(BIN)/python
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -r requirements.txt

install-model: install
	$(BIN)/pip install -r requirements-model.txt

# Needs sudo and OFFSAID_DB_PASSWORD set; see README.
db:
	bash scripts/setup_postgres.sh

seed:
	$(BIN)/python scripts/load_agents.py

# Cross-case linking tables (Postgres only), then the simulated demo cases.
migrate:
	$(BIN)/python -m scripts.migrate

seed-linking:
	$(BIN)/python -m scripts.seed_linking data/linking_seed/seed_cases.json

test:
	$(BIN)/pytest -q

run:
	./run.sh

dev:
	./run.sh --reload
