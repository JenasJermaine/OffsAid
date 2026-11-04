PYTHON ?= python3
VENV := .venv
BIN := $(VENV)/bin

.PHONY: install db seed test run dev

$(BIN)/python:
	$(PYTHON) -m venv $(VENV)

install: $(BIN)/python
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -r requirements.txt
	# CPU wheel. A plain `pip install torch` may pull a CUDA build the server does not need.
	$(BIN)/pip install torch --index-url https://download.pytorch.org/whl/cpu

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
