PYTHON ?= python3
VENV := .venv
BIN := $(VENV)/bin

.PHONY: install db seed test run dev

$(BIN)/python:
	$(PYTHON) -m venv $(VENV)

install: $(BIN)/python
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -r requirements.txt

# Needs sudo and OFFSAID_DB_PASSWORD set; see README.
db:
	bash scripts/setup_postgres.sh

seed:
	$(BIN)/python scripts/load_agents.py

test:
	$(BIN)/pytest -q

run:
	./run.sh

dev:
	./run.sh --reload
