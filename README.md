# OffsAid

OffsAid helps football players check recruitment offers. This repository currently holds
**Step 1: agent verification** and **Step 2: document claim checks**, two independent
endpoints. For the agent check, you give it an agent's name, phone number or licence
number, and it checks them against the licensed-agent list. It returns one of:

- `found`: the agent is on the licensed list.
- `possible_match`: a close match, or the same name with a different detail (for example
  a different country). Check the details.
- `not_found`: no matching licensed agent.

Every answer carries the disclaimer that a licensed agent can still make a false claim, so
"licensed" never means the offer is real. Each lookup is logged as a salted hash, never the
raw name or number.

## Step 2: document claim checks

`POST /v1/document-check` takes one uploaded document (PDF, PNG or JPEG, up to 20 MiB and
20 pages) and nothing else. It is independent of the agent check: it does not call
`/v1/agent-check`, and agent details found in the document are only extracted for the record.
We check what a document claims against public sources and flag inconsistencies; we do not
verify documents. Each check returns `consistent`, `not_confirmed` or `contradicted`, the
exact quotes it used, its sources, and whether it is a red flag. "Not confirmed" never means fake.

```bash
curl -s http://127.0.0.1:8000/v1/document-check \
  -F 'document=@data/test_letters/02_scam_selangor_gmail_mpesa.pdf;type=application/pdf'
```

**Extraction.** With `ANTHROPIC_API_KEY` set, one call to the vision model (`LLM_MODEL`) reads
the PDF or image and fills a fixed schema (`app/document_schemas.py: DocumentFields`): club,
country, league, sender email, contact phones, signatory, dates, the player's contract status,
fee requests and promises, each with the exact quote. Any value whose quote is not in the
document is dropped. A rule-based pass for requests for money runs as well, so a model miss is
not silent. Without the key, a pattern fallback extracts only what it can find in any layout
(emails, phone numbers, websites, requests for money); club, signatory and dates stay empty and
their checks say "not confirmed". Scans and screenshots need the model, or Tesseract for OCR
(`brew install tesseract` / `sudo apt install tesseract-ocr`).

**Checks** (`app/document_checks.py`), following "OffsAid: how we build it", section 2:

| Check | Source | Strength |
|---|---|---|
| Sender email domain vs the club's official website; free email; lookalike domains | Wikidata | strong |
| Sender domain age | RDAP | strong |
| Contact phone country vs the club's country | phonenumbers | strong |
| Fee or payment request, personal account, irreversible payment methods | rules | strong |
| Club exists, country and league | Wikidata | medium |
| Transfer window and player status (FIFA RSTP art. 6 and 18) | `data/registration_windows.simulated.csv` | medium |
| Trial and travel dates in a possible order | extracted dates | medium |
| Contradictions the model quotes | vision model | medium |
| File metadata (design tools such as Canva) | PDF metadata | weak |

Not built here, by design: the signatory check and finding the club's official contact need
the web-checking agent (Step 3) and are listed under `pending_checks`. Template reuse and
repeat phone numbers or accounts need the case history (cross-case linking); the response
returns hashed `linking_keys` for that step to store. Logo comparison is left out, since a
matching logo is no evidence either way.

Set `NETWORK_CHECKS_ENABLED=false` to turn off Wikidata and RDAP. Wikimedia needs a contact
URL in the User-Agent (`LOOKUP_USER_AGENT`).

**Registration windows.** `data/registration_windows.simulated.csv` has the format the build
doc describes (league, season, opening and closing date, source link, date checked), but the
dates are **simulated**. Before real use, fill each row from the association's own
announcement, add the source link and set `simulated` to `false`.

**Test letters.** `data/test_letters/` holds ten letters written the way real ones arrive:
letterheads and prose, a two-page agency letter, a scanned letter with no text layer and a
WhatsApp screenshot. Four look genuine (hard negatives) and six follow known scam patterns.
`manifest.json` gives the fields a correct extraction should return and the check results we
expect, plus the Wikidata and RDAP answers the tests use offline. All people, numbers and
accounts are fictional; real clubs appear only as the club named, and none of these letters came
from them. Regenerate with `venv/bin/python scripts/generate_test_letters.py`.

## Step 3: web-checking agent

The agent takes the fields from a document check, looks each claim up on the web and
returns what it could confirm, with a source link for every confirmation. It gathers
evidence and does not judge the offer (`app/web_agent.py`, "how we build it", section 3).

It checks the club's official website (Wikidata first, then search), whether the sender's
domain belongs to the club and how old it is, whether the signatory is listed on the club's
site, any announced trial or academy programme, the club's official contact details, and a
league's registration dates when the window table has no row for it. Each claim comes back
as `confirmed`, `not_found` or `contradicted`, with a link and a short note, plus a drafted
message the player or KEFWA can send to the club. We never send it.

Tools: Anthropic's server-side `web_search`, plus `fetch_page`, `domain_age` (RDAP) and
`wikidata_club` written in Python. The rules are enforced in code:

- A `confirmed` or `contradicted` row is downgraded to `not_found` unless its link is a page
  the agent opened or a Wikidata/RDAP record it looked up. A search result it never opened
  does not count. Official contact details are kept only if their page was opened.
- Each case is capped by `WEB_AGENT_MAX_TOOL_CALLS` (default 12, searches included) and
  `WEB_AGENT_TIMEOUT_SECONDS` (default 90).
- Offer fields, search results and pages are passed to the model as data, never instructions.
- `fetch_page` only opens public http(s) addresses on ports 80/443, so a letter or page
  cannot point it at the server's own network.
- Every search, page and lookup is stored in `web_check_logs` for KEFWA to audit.

It runs as a background job: `POST /v1/web-check` with `{"fields": <fields from
/v1/document-check>}` returns an id at once; `GET /v1/web-checks/{id}` returns the result and
the log. Without `ANTHROPIC_API_KEY` it is skipped.

## WhatsApp

`app/whatsapp_webhook.py` connects Steps 2 and 3 to the WhatsApp Cloud API. Point the Meta
app's webhook at `https://<your-host>/webhook` (HTTPS is required, so put nginx or Caddy in
front) and subscribe to `messages`. Set `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`,
`WHATSAPP_VERIFY_TOKEN` and `WHATSAPP_APP_SECRET`. With the app secret set, unsigned requests
are rejected.

When a player forwards a PDF or an image, the bot:

1. replies "we received your document, this can take a minute",
2. runs the document check and sends the first report (inconsistencies, warning signs,
   what is consistent and what is not confirmed, with the quoted lines),
3. if the document names a club, runs the web-checking agent and sends a second message
   with each claim's status and source link, the club's official contact and the draft message.

The replies are templates filled from the check results, not free-form model text. Other
file types get a "PDF, PNG or JPEG only" reply, and text messages get a short explanation.
Meta's webhook retries are processed once (`inbound_messages`). The player's number is stored
only as a salted hash. Without `WHATSAPP_TOKEN`, replies are logged instead of sent. The
agent check (Step 1) is not wired to WhatsApp; it stays at `/v1/agent-check`.

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
app/main.py          FastAPI app: /health, /v1/agent-check, /v1/document-check, /v1/web-check
app/whatsapp_webhook.py  WhatsApp Cloud API webhook (Steps 2 and 3)
app/report.py        WhatsApp reply templates
app/web_agent.py     Step 3: the web-checking agent and its tools
app/web_check_jobs.py  Step 3 background jobs, results and audit log
app/document_extraction.py  Step 2: read the file, vision-model extraction, pattern fallback
app/document_checks.py      Step 2: the claim checks
app/lookups.py       Wikidata club and RDAP domain lookups
app/matching.py      RapidFuzz matching (licence, then phone, then name)
app/normalize.py     name/phone/licence normalisation
app/seed.py          CSV loader
deploy/              systemd unit
scripts/             DB setup, agent loader, test-letter generator
data/test_letters/   Step 2 test letters and manifest
```

`app/llm.py` and `app/whatsapp.py` are used by Steps 2 and 3. `app/pipeline_models.py` is
groundwork for the later stages (case history, cross-case linking, KEFWA review queue);
nothing imports it yet.
