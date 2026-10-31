# OffsAid

OffsAid helps football players check recruitment offers. This repository currently holds
**Part 1: agent verification**. You give it an agent's name, phone number or licence
number, and it checks them against the licensed-agent list. It returns one of:

- `found`: the agent is on the licensed list.
- `possible_match`: a close match, or the same name with a different detail (for example
  a different country). Check the details.
- `not_found`: no matching licensed agent.

Every answer carries the disclaimer that a licensed agent can still make a false claim, so
"licensed" never means the offer is real. Each lookup is logged as a salted hash, never the
raw name or number.

The agent list in `data/licensed_agents.simulated.csv` is **simulated** demo data. Replace
it with KEFWA's list before real use.

It runs directly on a Linux server: Python in a virtual environment, PostgreSQL and
Uvicorn under systemd. For local Windows development, use Python and SQLite; no Linux,
Make, Docker, or separate database server is required.

## Setup on Windows (PowerShell)

Install Python 3.10 or newer from [python.org](https://www.python.org/downloads/windows/)
and make sure the Python launcher (`py`) is available. Open PowerShell in the project
folder (the folder containing `requirements.txt`), then run:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

In `.env`, set the database to a local SQLite file and replace the example salt with a
long random value:

```dotenv
DATABASE_URL=sqlite:///./offsaid.db
LOOKUP_HASH_SALT=replace-with-a-long-random-secret
```

Save `.env`, then start the API:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) to try the API. The app
creates its tables and loads the simulated agent CSV automatically when the database is
empty. You can run the test suite in a second PowerShell window with:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The SQLite setup is intended for local development. Use the Linux/PostgreSQL instructions
below for a server deployment.

## Requirements

- Linux server (tested target: Ubuntu 22.04/24.04); local development also works on Windows
- Python 3.10+ with `venv` (`sudo apt install python3 python3-venv`)
- PostgreSQL 14+ (`sudo apt install postgresql`)

## Setup

```bash
git clone <repo-url> /opt/offsaid && cd /opt/offsaid
make install                                   # creates .venv, installs requirements.txt

OFFSAID_DB_PASSWORD='choose-a-password' make db  # creates the offsaid role + database
cp .env.example .env                           # then edit it, see below
make seed                                      # loads the licensed-agent CSV
```

`.env` settings:

| Variable | Meaning |
|---|---|
| `DATABASE_URL` | `postgresql+psycopg://offsaid:<password>@localhost:5432/offsaid` |
| `LOOKUP_HASH_SALT` | Secret salt for hashed lookup logs. Set a long random value and keep it stable. |
| `FOUND_MIN_SCORE` | Name score (0-100) needed for `found`. Default 92. |
| `POSSIBLE_MIN_SCORE` | Name score needed for `possible_match`. Default 80. |

The tables are created on startup. If the agent table is empty, startup also loads the
CSV, so `make seed` is only needed to add rows to an existing list.

## Run

```bash
make dev    # auto-reload, http://127.0.0.1:8000
make run    # same, without reload
```

Interactive API docs are at `http://127.0.0.1:8000/docs`.

```bash
curl -s http://127.0.0.1:8000/v1/agent-check \
  -H 'Content-Type: application/json' \
  -d '{"name": "Otieno James", "claimed_country": "Kenya"}'
```

## Deploy with systemd

```bash
sudo useradd --system --home /opt/offsaid --shell /usr/sbin/nologin offsaid
sudo chown -R offsaid:offsaid /opt/offsaid
sudo cp deploy/offsaid.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now offsaid
journalctl -u offsaid -f            # logs
```

The service listens on `127.0.0.1:8000`. Put a TLS reverse proxy (nginx or Caddy) in front
of it for public access. Meta's WhatsApp webhooks will need HTTPS once that part is added.

## Tests

```bash
make test
```

The tests use in-memory SQLite, so they need no database server.

## Layout

```
app/main.py          FastAPI app: /health, /v1/agent-check
app/matching.py      RapidFuzz matching (licence, then phone, then name)
app/normalize.py     name/phone/licence normalisation
app/seed.py          CSV loader
deploy/              systemd unit
scripts/             DB setup and agent loader
```

`app/llm.py`, `app/whatsapp.py` and `app/pipeline_models.py` are groundwork for the later
stages (WhatsApp bot, offer checks, KEFWA review queue). Nothing imports them yet.
