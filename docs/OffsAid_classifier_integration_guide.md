# OffsAid: integrating the message classifier into the repo (guide for a coding agent)

Written 2026-11-04. Audience: a coding agent (Claude Code or similar) working in the team's repo, plus Jermaine. Goal: take the finished scam-language classifier and wire it into the existing system so the prototype can run end to end. Read the whole guide before touching anything.

## 0. Prompt to paste to the agent

> You are working in the OffsAid repo. Jermaine owns the scam-language classifier; the agent check, document check and web-checking agent are already built by teammates. Read `OffsAid_classifier_integration_guide.md` (in the staging folder `<PATH>`) and do exactly what it says: (1) inspect the repo and the staging folder, (2) propose where each staged file should go and move them, (3) install the model, (4) add `scam_language.py` as a service module and an API route, (5) connect it to the screenshot-extraction step so only the other party's messages are checked, (6) run the tests and smoke checks in section 8, (7) report what you did and anything you could not verify. Do not retrain the model, do not commit large files, and do not describe the classifier's accuracy as measured, because it has not been.

## 1. Where the project stands (be accurate about this)

- OffsAid is a WhatsApp-based verification tool for Kenyan footballers. The player sends a screenshot of the chat with a recruitment agent. The system returns an **evidence report, never a verdict**. It must not call anyone a scammer.
- Already built by teammates: agent check (licensed-agent list), document check, web-checking agent, and the extraction step that turns screenshots into structured fields.
- **This task:** the scam-language check. It reads what the agent wrote and flags wording patterns (fee requests, pressure, secrecy, guarantees, and so on), each with a quote.
- What exists: a fine-tuned multi-label model (`Davlan/afro-xlmr-small`, 7 labels) plus two rule layers: negation rules (so "we can't guarantee the visa" is not flagged) and a fee rule (catches fee wording the model misses, such as courier or customs charges).
- **Honest status, for any text the system or the team writes about it:** evaluated only on synthetic data and hand-written test messages. Real-world accuracy has **not** been measured. A real labelled test set ("gold set") does not exist yet and is deliberately skipped for this prototype iteration. Do not quote any accuracy figure. Do not claim it "detects scams".
- Model details: 140M parameters, runs on CPU in about 0.3 s per message (about 1 s for a five-message chat, measured in Colab). It was trained on the v5 synthetic data. The Drive zip may be named `offsaid_classifier_v4.zip` because the notebook's save name was never bumped; check the notebook (`SAVE_DIR`) and the files' dates, and use the newest model zip.

## 2. Files Jermaine will put in one staging folder

| File | What it is | Likely destination |
| --- | --- | --- |
| `offsaid_classifier_*.zip` | The trained model (about 500 MB). Hugging Face folder | `models/` (see section 4). **Never commit it** |
| `generate_synthetic.py` | Synthetic data generator (v5) | ML/training folder, for example `ml/scam_language/` |
| `synthetic_train.csv`, `synthetic_val.csv` | Training and validation data (5,000 and 1,000 rows) | `ml/scam_language/data/` |
| `OffsAid_classifier_training*.ipynb` | The Colab notebook with real outputs in the cells | `ml/scam_language/notebooks/` |
| `OffsAid_classification_handoff*.md` | Background and decisions log | `docs/` |
| `scam_language.py` | **Inference module (new):** rules plus model loader plus `check_chat()` | The backend services/ package, wherever the other checks live |
| `test_scam_language.py` | Unit tests that need no model | Next to the other backend tests |
| This guide | Instructions | `docs/` |
| Gold-set files (`gold_*.csv`, `make_gold.py`), if present | For later evaluation, not used now | `ml/scam_language/gold/`, with the CSVs git-ignored if they contain real messages |

If a file is not in the staging folder, say so; do not recreate it.

### 2.1 How to decide the destinations

1. Run a tree listing of the repo (two or three levels, ignoring `node_modules`, `.git`, `venv`) and read the top-level `README`, any `docs/`, and the backend entry point (for example `main.py` or `app/`). Note how the existing checks (agent, document, web agent) are organised: service modules, routes, schemas, tests.
2. **Follow the repo's existing conventions** over the defaults in the table. Put the new service module next to its siblings and write the route in the same style (router prefix, schema location, error handling, config and env-var handling).
3. If the repo has no obvious home for ML training assets, create `ml/scam_language/` with `data/`, `notebooks/` and `gold/`. If there is no `models/` folder, create it.
4. Before moving anything, write the planned mapping as a short list in your reply, then do the moves with `git mv` for tracked files or plain `mv` otherwise. Leave the staging folder with only what you did not use.
5. Check sizes: GitHub rejects files over 100 MB and the model is about 500 MB. The two CSVs are small enough to commit; the model is not.

## 3. Install the dependencies

The service needs `torch` (CPU build), `transformers`, `sentencepiece` and `safetensors` (installed with transformers). On a CPU server install the small CPU wheel:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install "transformers>=4.40" sentencepiece
```

Add these to the project's dependency file (`requirements.txt` or `pyproject.toml`) in the style the repo already uses, and mention the CPU index in a comment. Check the deployment target first (Render Standard has 2 GB RAM; the model needs roughly 1 to 1.5 GB loaded, so it fits but leaves little spare; do not run several workers, each would load its own copy).

## 4. Install the model

1. Unzip the model zip into `models/offsaid_classifier_v4/` (or `_v5` if that is what the zip contains; use the name that matches the zip). Expected contents: `config.json`, `model.safetensors`, tokenizer files (`tokenizer.json` or `sentencepiece.bpe.model`, `tokenizer_config.json`), and `thresholds.json`.
2. Add `models/*` to `.gitignore` (keep a `models/README.md` that says where the model comes from, for example the Drive link or, later, a private Hugging Face repo, and how to unpack it). Do not commit weights.
3. Add a config setting `OFFSAID_MODEL_DIR` (default `models/offsaid_classifier_v4`), documented in the repo's env example file. For deployment the model must be downloaded or mounted at start-up; note this in the README or deployment notes because it will not arrive through git.
4. Verify the folder: `config.json` must show `num_labels` 7 and the labels in `id2label`. `ScamLanguageChecker.load()` checks that the labels match and raises a clear error if not.

## 5. The inference module

`scam_language.py` is provided and already tested (nine unit tests pass without the model). Place it in the services package. What it contains:

- `ScamLanguageChecker.load(model_dir)`: loads model and tokenizer once, on CPU, reads `thresholds.json` (all 0.5 for now).
- `classify(messages)`: input `[{"sender": "agent", "text": "..."}]`, output the contract below. It scores each sentence and the whole message, quotes the **sentence** that triggered each label, applies the negation rules after the model, and lets the fee rule add `advance_fee_request` only when the model did not raise it.
- `check_chat(extraction)`: the function the API and the orchestrator should call. It accepts the extractor's output (a list, or a dict with `messages`), keeps **only the other party's messages** (see section 6), classifies them, and adds `messages_checked` and `mode` to the result.
- `select_agent_messages()`: drops the player's own messages, empty text, quoted replies, system notices and attachments.
- If the model folder is missing, `ScamLanguageChecker()` works in `rules_only` mode (only the fee rule can fire). The service should log a warning and keep running, and the response's `mode` field will say `rules_only`. Do not hide this from the report.

**Output contract** (unchanged from the one agreed with the API builder, plus two extra fields on each flag):

```json
{
  "flags": [
    {"label": "advance_fee_request",
     "quote": "Tuma KES 15,000 kwa mpesa yangu ...",
     "score": 0.997,
     "source": "model",
     "meaning": "Asks for a payment or fee before any signed club contract."}
  ],
  "messages_checked": 3,
  "mode": "model+rules"
}
```

`source` is `"model"` or `"rule"`. A rule-added flag has `score: 0.5`, a placeholder and not a probability: the UI and report must not show it as a confidence percentage. Scores from the model are uncalibrated; show the quote and the plain-language meaning, and do not present scores as accuracy or likelihood of fraud.

The seven labels: `advance_fee_request`, `personal_account_payment`, `urgency_pressure`, `secrecy`, `guaranteed_outcome`, `visa_travel_arranged`, `off_platform_move`. "None" is simply an empty `flags` list.

## 6. From screenshot to the other party's messages

**Interpretation to confirm with Jermaine if it matters:** the request was to extract only the agent's side of the chat from the screenshot and run those messages through the model. This means separating the agent's **text messages**, not cropping image regions. A vision LLM reads the screenshot and returns each message with who sent it; the code then keeps only the agent's. Cropping bubbles by colour would be fragile (dark mode, other apps, wallpapers), so do not do it.

### 6.1 Check what the existing extractor returns

Find the existing screenshot-extraction function or prompt (search the repo for the vision-LLM call). Three cases:

1. It already returns messages with a sender field: use it as is. `select_agent_messages` understands `sender`, `role` and `from`, and text under `text`, `message` or `body`. If the field names differ, add a thin adapter; do not change the extractor's other consumers.
2. It returns only a block of text or fields without senders: add a **chat extraction** call (a separate prompt, so existing behaviour is untouched) using the prompt below.
3. There is no extractor yet: build one using the same vision-LLM client the repo already uses.

### 6.2 Extraction prompt and output schema for a chat screenshot

Ask the vision model for JSON only, in this shape:

```json
{"app": "whatsapp|telegram|sms|other",
 "messages": [
   {"sender": "agent|player|unknown",
    "text": "verbatim text of the bubble",
    "type": "text|voice|image|document|system|deleted",
    "quoted_reply": "text of the quoted block inside the bubble, or null",
    "time": "HH:MM or null"}
 ]}
```

Rules to put in the prompt:

- In a WhatsApp or Telegram screenshot, bubbles on the **right** (usually green or blue) are the **player** (the person who took the screenshot). Bubbles on the **left** (white or grey) are the other party, the **agent**. Use alignment, not colour alone, because dark mode changes the colours. If it cannot tell, use `"unknown"`.
- Transcribe **verbatim**: keep Swahili, Sheng, typos, emoji, phone numbers and amounts exactly. Do not translate, correct or summarise.
- If a bubble contains a reply-quote (a grey block showing an earlier message), put that block in `quoted_reply` and **not** in `text`. Otherwise the player's words get attributed to the agent.
- Skip date separators, "Messages are end-to-end encrypted" banners, read ticks and timestamps (timestamps go in `time`).
- Voice notes, images, documents, stickers and deleted messages: add an entry with the right `type` and empty `text`; they are not classified. Images and documents belong to the document check.
- Messages that run on over several bubbles from the same sender stay separate entries, in order.
- Group chats with several named senders are out of scope for the prototype; if you see one, set `app` accordingly and mark every non-player sender `agent`, and tell the user the check is less reliable.

Map the output into `select_agent_messages`: it keeps entries that are not `player`, have text, and are not quoted or system items. For the extractor's `type` field to be honoured, the adapter must map `type` into the dict (already supported: `type` values `system` and `quoted` are skipped; also make the adapter skip `voice`, `image`, `document` and `deleted` by leaving their text empty, which the filter drops).

### 6.3 Things to verify with real screenshots (the prototype will meet these)

- A screenshot with the agent's messages on the left and the player's replies on the right: only the left side should be checked.
- A message that quotes the player's earlier text.
- Long messages that wrap across lines (one bubble, not several).
- Very short chats (one message) and chats with no agent text (return an empty `flags` list and `messages_checked: 0`; the report should say "no agent text found in this screenshot" rather than "no problems found").

Store nothing beyond what the repo's privacy approach already allows: do not log message text or screenshots in plain application logs, and follow the team's existing retention approach for uploaded images.

## 7. The API route and the pipeline function

Match the repo's style (router layout, schemas, dependency injection, error format). The sketch below uses FastAPI; adapt names and imports.

```python
# services/scam_language.py       <- the provided module goes here
# api/routes/scam_language.py     <- new route file, registered like the others

import logging
from contextlib import asynccontextmanager
from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from services.scam_language import ScamLanguageChecker      # adjust to the repo's package path

log = logging.getLogger(__name__)
router = APIRouter(prefix="/scam-language", tags=["scam-language"])


def load_checker():
    """Call once at start-up (FastAPI lifespan) and store on app.state.scam_checker."""
    try:
        return ScamLanguageChecker.load()          # uses OFFSAID_MODEL_DIR
    except Exception:
        log.exception("Model failed to load; scam-language check running in rules_only mode")
        return ScamLanguageChecker()               # rules only; response will say mode=rules_only


class Msg(BaseModel):
    sender: str = "agent"
    text: str

class MessagesIn(BaseModel):
    messages: list[Msg]


@router.post("/messages")
async def check_messages(body: MessagesIn, request: Request):
    """Already-extracted messages in, flags out. Used by tests and by teammates' code."""
    checker = request.app.state.scam_checker
    msgs = [m.model_dump() for m in body.messages]          # .dict() on pydantic v1
    return await run_in_threadpool(checker.check_chat, msgs)  # model call blocks: keep it off the event loop


# Pipeline function: call this from the orchestrator that already runs the other checks.
def run_scam_language_check(checker, extraction):
    """extraction = the chat-extraction output (section 6). Returns the contract in section 5."""
    return checker.check_chat(extraction)
```

Also add (using the existing extractor): `POST /scam-language/screenshot` that takes the uploaded image, calls the chat-extraction step from section 6, passes the result to `check_chat`, and returns the flags together with the extracted agent messages (so the report can show what was read). Reject non-images and apply the same size limit as the other upload routes.

Points to get right:

- **Load the model once** at start-up, not per request. Run `check_chat` in a thread pool because PyTorch blocks. Use one worker process unless memory allows more.
- First request after start-up may be slower (model warm-up); do a dummy call at start-up.
- Wire the result into the **evidence report** as a section such as "Wording patterns found in the agent's messages": for each flag show the `quote` and the `meaning`; if `flags` is empty, say that none of the known patterns were found, **not** that the agent is safe. Never convert flags into a verdict, a risk percentage or the word "scam".
- If a cross-case-linking teammate wants the wording, give them the `quote` strings; do not add new storage of raw chats without asking.
- Keep the `source` field in the stored result so a reviewer (KEFWA) can see whether a flag came from the model or the fee rule.

## 8. Tests and smoke checks

1. **Unit tests, no model needed:** `pytest test_scam_language.py` (9 tests: negation, fee rule, message selection, rules-only mode). Move the file to the repo's tests folder and fix the import path. All must pass.
2. **Smoke test with the real model:** run `checker.classify` on the messages below. These came from the Colab run and were written by the same people who wrote the rules, so they prove the plumbing works; they are **not** an accuracy test. Assert that every expected label is present, and that the "nothing" cases return an empty list. Extra labels on messages that expect some are tolerated (message 2 is known to also raise `visa_travel_arranged`, a false positive on delivery wording: keep it as a known issue, do not patch it now).

| Message | Expected labels |
| --- | --- |
| Congratulations bro! The club has picked you. Only thing left is paying the courier for your papers. | guaranteed_outcome, advance_fee_request (rule) |
| Hi, DHL is holding your contract until the delivery fee is paid. Send it today. | urgency_pressure, advance_fee_request (rule); also visa_travel_arranged (known false positive) |
| Hakuna mtu atakuhakikishia visa, ni ubalozi unaoamua. Trial ni bure. | nothing |
| We cannot promise any visa, the embassy decides. Come to the open trial, no charge. | nothing |
| The doctor must be paid before your medical, send the money to my sister's number 0722 111 222. | personal_account_payment, advance_fee_request (rule) |
| Pole kwa kuchelewa. Tuma ile pesa ya customs ndio documents zitoke, usiwaambie wengine. | secrecy, advance_fee_request (rule) |
| Congratulations on your trial. The coach will go through all players and we will email results next week. | nothing |
| Your flight is booked and your visa is approved, just settle the clearance charges. | visa_travel_arranged, advance_fee_request (rule) |
| We can't guarantee the visa, but your contract is 100% guaranteed. | guaranteed_outcome only |
| Your trial is on Saturday at 9am. Bring boots and ID. There is no fee. | nothing |
| The club will pay for your courier and documents, you pay nothing. | nothing |
| Please send your medical results and passport copy by email. | nothing |

3. **Route test:** call `POST /scam-language/messages` with three messages (one scam-like, one legitimate, one from `player`), and confirm: the player message is not counted, `messages_checked` is 2, and the legitimate message produces no flags.
4. **Screenshot test:** with two or three real or realistic chat screenshots, confirm the extractor separates the sides correctly before blaming the model.
5. **Memory and speed:** note the memory after start-up and the time for a five-message chat. Expect about 1 s on CPU.

## 9. Known limits (put these in the README for the module)

- Evaluated only on synthetic data and the hand-written messages above. The model alone still misses fee wording without an amount (courier, delivery, clearance); the fee rule covers some of these. Rules are written from the same examples, so false alarms on honest agents are unmeasured.
- Known false positive on delivery wording (`visa_travel_arranged`). Possible false positive on `urgency_pressure` for slot-payment messages.
- Sheng and mixed-language coverage is limited; the Swahili and Sheng training phrases were not reviewed by a native speaker.
- Only the other party's text is checked. Voice notes, images and documents are not read by this module.
- Thresholds are a flat 0.5, not tuned.
- The score on a rule-added flag is a placeholder.

## 10. What not to do

- Do not retrain, edit the rules, or tune thresholds to make the 12 messages pass: that only overfits to them. Rule or model changes wait for the gold set.
- Do not commit model weights, real chat screenshots or any message containing real phone numbers.
- Do not describe the system as detecting scams or give a percent confidence of fraud.
- Do not change the output contract without telling the teammates who consume it; the two new per-flag fields (`source`, `meaning`) and the two new top-level fields (`messages_checked`, `mode`) are additions only.

## 11. Done checklist (report back with this filled in)

- [ ] Repo structure inspected; mapping of staged files written down; files moved
- [ ] Model unpacked to the model folder, git-ignored, `models/README.md` written, env var documented
- [ ] Dependencies added (CPU torch, transformers, sentencepiece)
- [ ] `scam_language.py` placed; model loads once at start-up; fallback to rules-only works
- [ ] Route(s) added in the repo's style; pipeline function available to the orchestrator
- [ ] Chat-extraction step returns senders; only the other party's messages are checked
- [ ] Unit tests pass; smoke checks run against the real model (state which expectations, if any, did not hold)
- [ ] Evidence report section added with wording from section 7
- [ ] Anything not verified is listed plainly (for example, no real screenshots were available)

## 12. Later (after the prototype)

Build the gold set (100 to 200 real messages, two labellers), run Step 8 of the notebook, then tune thresholds and rules on it; add the LLM few-shot layer and a rules baseline for the other labels; native-speaker review of the Swahili and Sheng phrases; replace the Drive copy of the model with a private Hugging Face repo and download it at deployment.
