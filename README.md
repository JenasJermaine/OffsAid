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

## Wording patterns in the agent's messages

`POST /v1/scam-language/screenshot` takes a PNG or JPEG chat screenshot (20 MiB limit, same
as the other uploads). A separate vision call transcribes each bubble and marks who sent it.
Bubbles on the right are the player; bubbles on the left are the other party. Only the other
party's text is checked. `POST /v1/scam-language/messages` takes messages that are already
extracted, for tests and for the other steps.

The reply is an evidence section, "Wording patterns in the agent's messages". Each pattern
is the quoted line and a plain-language meaning. An empty list means none of the known
patterns were found. It does not mean the offer is genuine, and the report never calls
anyone a scammer. Quotes from the other party are returned with the flags so the report can
show what was read. Nothing here stores the raw chat.

The checker is a fine-tuned classifier plus two rule layers (negation, and a fee rule). It
loads once at startup from `OFFSAID_MODEL_DIR` (default `models/offsaid_classifier_v4`).
The weights are not in git; see `models/README.md`. For local inference, the folder has to
be downloaded or mounted before startup; it needs about 1 to 1.5 GB once loaded, so keep a
single worker. If neither local weights nor a Modal endpoint is configured, the process
still starts in `rules_only` mode: only the fee rule can fire, and the report says so. A
rule flag's score of 0.5 is a placeholder, not a probability. Model scores are uncalibrated
and are not shown as a chance of fraud.

For local inference with the weights on disk, install the optional CPU model dependencies:

```bash
pip install -r requirements-model.txt
```

To host inference on Modal instead, the Render app can use the authenticated endpoint in
`modal_scam_model.py`; it sends the agent-message text to Modal and receives the same
flags and rules-based result. Modal mounts the `offsaid-model` Volume at
`/model/offsaid_classifier_v4`. The screenshot transcription step still uses Anthropic,
and does not use this classifier endpoint. In the Modal dashboard, create a Secret named
`offsaid-scam-api` with a random `SCAM_MODEL_API_TOKEN` value, then deploy from the repository root.
The current upload instructions place the checkpoint under `/model/offsaid_classifier_v4` inside
the Volume, which the mounted function reads at `/model/model/offsaid_classifier_v4`. Redeploy
after changing the Modal Secret so new containers receive its value:

```powershell
modal deploy modal_scam_model.py
```

Set `SCAM_MODEL_API_URL` in the app host to the deployed URL ending in `/classify`, and set
the same token in `SCAM_MODEL_API_TOKEN` there. Keep the token in the host's secret settings,
not in source control. The model endpoint is protected by bearer-token authentication and
does not persist message text. Modal still processes the text for inference; use it only if
that data handling is appropriate for your users. If Modal is unreachable, the app logs the
failure and uses its local fee-wording rule only (`rules_only`).

Without `ANTHROPIC_API_KEY` the
screenshot route cannot read bubbles, so it returns no flags and says the chat could not
be read, instead of inventing a transcript.

Evaluated only on synthetic data and a short list of hand-written messages. Real-world
accuracy has not been measured, and there is no gold set yet. Do not quote an accuracy
figure. Other limits, from the integration notes:

- The model still misses some fee wording that has no amount (courier, delivery, clearance). The fee rule covers some of those. False alarms on honest agents are unmeasured.
- Known false positive: delivery wording can raise `visa_travel_arranged`. Slot-payment messages may raise `urgency_pressure`.
- Sheng and mixed-language coverage is limited. The Swahili and Sheng phrases were not reviewed by a native speaker.
- Voice notes, images and documents are not read by this check. Only the other party's text is.
- Thresholds are a flat 0.5, not tuned. The score on a rule-added flag is a placeholder.

WhatsApp image replies include this section after the document check. PDF replies do not.

## WhatsApp

`app/whatsapp_webhook.py` connects Steps 2 and 3 to the WhatsApp Cloud API. Point the Meta
app's webhook at `https://<your-host>/webhook` (HTTPS is required, so put nginx or Caddy in
front) and subscribe to `messages`. Set `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`,
`WHATSAPP_VERIFY_TOKEN` and `WHATSAPP_APP_SECRET`. With the app secret set, unsigned requests
are rejected.

Both webhooks (Meta and Twilio) run the same guided chat as the browser demo (`app/conversation.py`,
see "Browser demo" below): a menu, `@agent-check`, `@document-check`, `@message-check` and `@done`. One
chat per WhatsApp number is kept in memory (`app/chat_sessions.py`), so a restart sends the player back
to the menu. The WhatsApp number is the contact KEFWA receives when a case is referred. When a player
forwards a PDF or an image, the bot:

1. replies "we received your document, this can take a minute",
2. runs the document check and sends the first report (inconsistencies, warning signs,
   what is consistent and what is not confirmed, with the quoted lines). When the
   upload is an image, that same message includes the wording section,
3. if the document names a club, runs the web-checking agent and sends a second message
   with each claim's status and source link, the club's official contact and the draft message.

The replies are templates filled from the check results, not free-form model text. Other
file types get a "PDF, PNG or JPEG only" reply.
Meta's webhook retries are processed once (`inbound_messages`). The player's number is stored
only as a salted hash, except in a KEFWA referral. Without `WHATSAPP_TOKEN`, replies are logged
instead of sent. The agent check (Step 1) is reached on WhatsApp through `@agent-check`.

Twilio uses the same replies on `POST /twilio/webhook`. Set `TWILIO_ACCOUNT_SID`,
`TWILIO_AUTH_TOKEN` and `TWILIO_WHATSAPP_FROM` (with the `whatsapp:` prefix). Point the
sender's "A message comes in" webhook at `https://<your-host>/twilio/webhook`, method POST.
For a local trial, run `ngrok http 8000` and paste that https address. The auth token
rejects a request whose signature does not match that public URL.

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

If you want local classifier inference rather than the Modal endpoint, also run
`.\.venv\Scripts\python.exe -m pip install -r requirements-model.txt`.

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
| `OFFSAID_MODEL_DIR` | Unpacked scam-language model. Default `models/offsaid_classifier_v4`. Not in git. |
| `SCAM_MODEL_API_URL` | Optional HTTPS Modal endpoint URL ending in `/classify`. |
| `SCAM_MODEL_API_TOKEN` | Bearer token shared with the Modal Secret; keep it private. |
| `SCAM_MODEL_TIMEOUT_SECONDS` | Timeout for the Modal call. Default 120. |

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

For local model inference, install `requirements-model.txt` and unpack the weights into
`models/offsaid_classifier_v4` (or set `OFFSAID_MODEL_DIR`). Alternatively set
`SCAM_MODEL_API_URL` and `SCAM_MODEL_API_TOKEN` to use the Modal service; in that mode the
weights and PyTorch are not installed on this app server. Leave both API settings empty to
use local weights when available, or the fee rule alone when they are not.

## Cross-case linking

For every offer forwarded on WhatsApp we ask: have we seen this contact number, payment account,
email, letter template or wording before? Full build notes: `docs/CROSS_CASE_LINKING_GUIDE.md`.

- **Where it runs:** `app/whatsapp_webhook._send_offer` (Meta and Twilio), after the document and
  wording checks and before the first report. `app/linking_step.py` maps the check results to a
  `CaseInput` and calls `app/case_linking.process_case`. The report gets a section
  "Comparison with earlier cases" with fact sentences only: no case ids, dates, raw numbers,
  percentages or verdicts. A match is evidence that details were seen before, never proof about a person.
- **Postgres only** (14+, `pg_trgm`). On SQLite, or if linking fails for any reason, the report says
  "Comparison with earlier cases was not available for this check." and everything else is sent as usual.
- **Nothing raw is stored:** numbers, accounts and emails are HMAC-SHA256 hashes keyed with
  `OFFSAID_HASH_KEY`; letters are kept as a perceptual hash plus masked text; files are not kept.
  The key is permanent: losing or changing it makes every stored hash unmatchable.
- **Thresholds are untested starting points** (`PHASH_MAX_DISTANCE`, `TEXT_SIMILARITY_MIN`,
  `WORDING_SIMILARITY_MIN`, `SAME_NAME_RATIO`, all in `app/case_linking.py` and nowhere else). They
  separate the simulated seed letters cleanly but have not been tried on real letters.
- **KEFWA queue:** escalated cases go to the `review_queue` table with the reasons and the ids of the
  matching cases. Reviewer routes (HTTP Basic, `ADMIN_PASSWORD`; disabled when empty):
  `GET /admin/review-queue`, `POST /admin/cases/{id}/status` (`unreviewed`, `reported`,
  `confirmed_scam`, `verified_genuine`), `DELETE /admin/cases/{id}` (erasure request).
- **Rate limit:** `LINKING_MAX_PER_HOUR` runs per WhatsApp user (in memory, one worker).

Setup, once per database (`DATABASE_URL` and `OFFSAID_HASH_KEY` from `.env`):

```bash
python -m scripts.migrate                                   # migrations/*.sql: tables + pg_trgm
python -m scripts.seed_linking data/linking_seed/seed_cases.json   # 10 simulated demo cases, safe to re-run
```

The simulated cases use the invalid prefix `+254 999` and are labelled as simulated in the report.
`python scripts/make_seed_data.py data/linking_seed` regenerates them and their letter images.

## Browser demo of the WhatsApp chat

`http://127.0.0.1:8000/demo` is a WhatsApp-style chat page for demos without a WhatsApp number.
On Windows, `.\scripts\demo.ps1 -Reset` starts the local Postgres and the app with clean demo data.

The chat (`app/conversation.py`) is guided: a welcome menu, then each check runs on its own.
`@agent-check` (name, licence number, phone, country; at least one of the first three),
`@document-check` (with examples of documents to send), `@message-check` (a chat screenshot, or
pasted messages), and `@done` for a summary of everything checked (only `@done`: a plain "done" or "no"
is not a command). `@restart` (or `@start-over`) clears the chat's checks after a second
`@restart` to confirm, so a check run with wrong details is not summarised or referred to KEFWA. When a check raised concerns, the
summary refers the case to KEFWA: players are told at the start, and again then, that KEFWA receives
their phone number and the findings so they can contact them. Referrals and linking escalations show
on the KEFWA review page `/admin/review` (login `ADMIN_USERNAME` / `ADMIN_PASSWORD`).
Documents go through `whatsapp_webhook._send_offer`, the same checks as a WhatsApp document. The
WhatsApp and Twilio webhooks run this same chat. The paperclip menu uploads a file from the computer or picks a sample; either opens a preview where a
caption such as `@document-check` or `@message-check` can be added before sending, as on WhatsApp. "Lakeview United offer"
(`data/demo/lakeview_united_offer.pdf`, invented, from `scripts/make_demo_letter.py`) reuses a phone
number and a till from the simulated linking seeds under a new club name, so on a seeded Postgres
database the report shows flagged links and a KEFWA escalation. Chats live in memory and are lost
on restart. Set `DEMO_CHAT_ENABLED=false` to turn the page off.

## Tests

```bash
make test
```

The tests use in-memory SQLite, so they need no database server. The cross-case linking database
tests run only when `OFFSAID_TEST_DB` points at an **empty, disposable** Postgres 14+ database with
`pg_trgm` (their fixtures drop and recreate the linking tables):

```bash
OFFSAID_TEST_DB=postgresql://user@localhost:5432/offsaid_test pytest tests/test_case_linking.py tests/test_linking_step.py
```

## Layout

```
app/main.py          FastAPI app: /health, /v1/agent-check, /v1/document-check, /v1/web-check, /v1/scam-language
app/static/landing.html  Public landing page at / (images in app/static/assets, from the OffsAid deck)
app/whatsapp_webhook.py  WhatsApp Cloud API webhook (document check, wording section, linking, web check)
app/report.py        WhatsApp reply templates, including the wording and linking sections
app/case_linking.py  Cross-case linking: normalising, keyed hashing, masking, matching rules, thresholds
app/linking_step.py  Linking in the pipeline: CaseInput mapping, failure policy, rate limit, KEFWA queue
app/admin.py         KEFWA reviewer routes (review queue, case status, erasure)
app/demo_chat.py     Browser demo of the WhatsApp chat (/demo, app/static/demo.html)
app/scam_language.py Wording check: model plus negation and fee rules
app/scam_runtime.py  Loads that model once, and runs it on an extracted chat
app/chat_extraction.py  Vision call that transcribes a chat screenshot with senders
app/web_agent.py     Step 3: the web-checking agent and its tools
app/web_check_jobs.py  Step 3 background jobs, results and audit log
app/document_extraction.py  Step 2: read the file, vision-model extraction, pattern fallback
app/document_checks.py      Step 2: the claim checks
app/lookups.py       Wikidata club and RDAP domain lookups
app/matching.py      RapidFuzz matching (licence, then phone, then name)
app/normalize.py     name/phone/licence normalisation
app/seed.py          CSV loader
models/              Unpacked classifier (git-ignored) and models/README.md
ml/scam_language/    Generator, synthetic CSVs, training notebook
deploy/              systemd unit
scripts/             DB setup, agent loader, test-letter generator, wording smoke check,
                     migrations runner, linking seed generator and loader
migrations/          Plain-SQL Postgres migrations (cross-case linking tables, KEFWA review queue)
data/test_letters/   Step 2 test letters and manifest
data/linking_seed/   Simulated cross-case linking cases and letter images (all invented)
docs/                Classifier handoff, integration notes, cross-case linking guide
```

`app/llm.py` and `app/whatsapp.py` are used by Steps 2 and 3. `app/pipeline_models.py` holds
parked tables (registration windows); nothing imports it yet.
