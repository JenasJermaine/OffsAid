# OffsAid cross-case linking: build guide for a coding agent

Written 2026-11-05. Audience: a coding agent working in the OffsAid repo. Everything else in the system (extraction, agent check, document check, web-checking agent, scam-language check) is already built. This guide covers only **cross-case linking**: the stage between the three checks and the evidence report. Read it all before changing anything. The team's explanation of the idea is section 5 of the Claude Doc "OffsAid: how we build it"; this guide turns it into build steps.

## 0. Prompt to paste to the agent

> You are working in the OffsAid repo. Implement cross-case linking exactly as described in `CROSS_CASE_LINKING_GUIDE.md`, using the provided files in the staging folder `<PATH>` (`case_linking.py`, `schema.sql`, `make_seed_data.py`, `seed_linking.py`, `test_case_linking.py`, `seed_data/`). First inspect the repo (how the Postgres connection, migrations, config, routes and tests are organised) and report your plan: where each file goes, how the schema becomes a migration, and where in the pipeline linking is called. Then build it, run the tests (including the database tests against a throwaway database), and report what you verified and what you could not. Do not change the matching rules or thresholds, do not store raw phone numbers, accounts, emails or letter files, and do not describe a match as proof that someone is a scammer.

## 1. What this stage does

For every new case the system asks: **have we seen this contact, payment account, letter or wording before?** It compares the new case with all earlier cases and returns links as evidence. It never decides that anyone is a scammer.

Pipeline position:

```
extraction -> agent check + opportunity check + scam-language check -> CROSS-CASE LINKING -> evidence report (+ KEFWA queue)
```

What is compared:

| Signal | Stored as | Match | Strength |
| --- | --- | --- | --- |
| Agent's phone / WhatsApp number | HMAC hash of E.164 number (`kind=phone`) | exact | strong |
| Payment account (M-Pesa number, till, paybill, bank account) | HMAC hash. A mobile number used as a payment account is stored as `phone`, so "pay to this M-Pesa number" matches an earlier case where it was a contact | exact | strong |
| Sender email | HMAC hash of the normalised address (gmail dots and `+tags` removed) | exact | strong |
| Sender email domain (not free-mail) | HMAC hash | exact | medium |
| Letter template | 64-bit perceptual hash of page 1 + letter text with names, dates, amounts and contacts masked | hash distance <= 10 bits AND text similarity >= 0.6 | strong |
| Scam wording | the agent's masked messages + the labels found by the scam-language check | text similarity >= 0.6; flagged only if the earlier case shares a label | medium |

What counts as a **flag** (not every match is one):

- A repeat alone is **not** suspicious. A real agent has one number and many players. Same number, same club, same agent name = `known_contact`, no flag.
- Flag: the same identifier under **different club names**; or an identifier that appears in a case with status `confirmed_scam`; or a letter template reused under a different club name; or wording that matches an earlier case and shares a scam-language label.
- Escalate to the KEFWA queue when any of the first three above happens. Wording alone never escalates. `ESCALATE_STATUSES = {"confirmed_scam"}`; adding `"reported"` is a one-line change the team may want, but it is their decision.

Case statuses: `unreviewed` (default for live cases), `reported` (a player or a public warning says so), `confirmed_scam` (KEFWA reviewed), `verified_genuine`.

Thresholds (`PHASH_MAX_DISTANCE`, `TEXT_SIMILARITY_MIN`, `WORDING_SIMILARITY_MIN`, `SAME_NAME_RATIO`) are untested starting points. They separate the simulated seed cases cleanly (same layout 0 to 2 bits apart, different layouts at least 24 bits apart) but have not been tried on real letters. Keep them in one place and say so in the README.

## 2. Files provided

| File | Purpose | Put it |
| --- | --- | --- |
| `case_linking.py` | The whole logic: normalising, hashing, masking, `find_links`, `record_case`, `process_case`, `player_view`, `set_status`, `delete_case` | with the other services/pipeline modules |
| `schema.sql` | Three tables (`cases`, `identifiers`, `documents`) and indexes. Needs the `pg_trgm` extension and Postgres 14+ | convert to the repo's migration tool |
| `make_seed_data.py` | Generates 10 simulated cases and letter images | `scripts/` or next to the seed script |
| `seed_linking.py` | Loads a seed JSON file into the tables (idempotent) | same place |
| `seed_data/seed_cases.json` + `letters/` | Output of `make_seed_data.py`, committed so the demo works without running it | `data/` or `seed_data/`; fine to commit (all invented) |
| `test_case_linking.py` | 26 tests: 9 pure, 17 against a real Postgres | the tests folder |
| This guide | | `docs/` |

Follow the repo's conventions over these suggestions. If the repo already has a database layer (SQLAlchemy, asyncpg, a repository pattern), keep the SQL and the logic and replace only the four thin helpers at the bottom of `case_linking.py` (`_cursor`, `_exec`, `_one`, `_all`) and the `conn.transaction()` calls. The module uses psycopg 3 and dict rows.

## 3. Setup

1. **Dependencies:** `psycopg[binary]>=3.1`, `phonenumbers`, `rapidfuzz` (probably present from the agent check), `imagehash`, `Pillow`. Add them in the repo's style. PDF letters need a page renderer (`PyMuPDF` or `pdf2image`); check what the document check already uses and reuse it.
2. **Secret key:** `OFFSAID_HASH_KEY`, at least 32 characters. Generate once: `python -c "import secrets; print(secrets.token_hex(32))"`. Add it to the env example file with a comment. **Treat it as permanent:** raw values are not stored, so if the key is lost or changed every stored hash becomes unmatchable and the tables must be wiped and re-seeded. Back it up in the team's secret store. Never log it or commit it. The code refuses to run without it.
3. **Migration:** turn `schema.sql` into a migration. `create extension pg_trgm` needs the right database privilege; on a managed database, enable the extension in its dashboard. Do not add pgvector for this version.
4. **Throwaway database for tests:** create an empty database and export `OFFSAID_TEST_DB` to its DSN. The test fixture drops and recreates the three tables, so never point it at a real database.

## 4. How the module works (so you do not break it)

- **Normalising** (`canonical_identifiers`): phones to E.164 (`0712 345 678`, `254712345678`, `+254 712 345 678` become one value); a payment "account" that is a mobile number becomes a `phone`; emails lower-cased with gmail dots and `+tags` removed; an email also yields its domain unless it is free-mail (`FREE_MAIL`), because `gmail.com` is shared by everyone.
- **Hashing** (`hash_identifier`): `HMAC-SHA256(key, kind + ":" + value)`. A plain hash would be reversible by trying every phone number. Keep this function the only place hashes are made, for live and seed cases alike.
- **Masking** (`mask_text`): replaces emails, URLs, phones, amounts, dates, digits and the case's club, agent and extra names with placeholders, lower-cases and strips punctuation. Two letters with the same template and different clubs mask to identical text.
- **Letters:** `image_phash` makes a signed 64-bit hash (bigint column). The SQL compares hashes with `bit_count((phash # new)::bit(64))` and wording with `similarity()` from `pg_trgm`, so scale is fine for thousands of cases. A letter match needs **both** hash and text to match.
- **Club comparison** (`same_name`): normalised (titles and FC/AFC/SC removed) then RapidFuzz `token_sort_ratio >= 85`, so "Coast City FC" equals "Coast City" and a typo, but not "Coast City United".
- **Order matters:** `process_case` matches against earlier cases and **then** stores the new one, so a case never matches itself. It is idempotent on `external_ref` (use the WhatsApp message id): a retried webhook returns the same `case_id` and re-runs matching without duplicating rows.
- **Output** of `find_links` / `process_case`:

```json
{"links": [
   {"type": "phone", "strength": "strong", "earlier_cases": 1, "statuses": {"unreviewed": 1},
    "different_club": true, "distinct_clubs_earlier": 1, "known_contact": false, "flag": true,
    "involves_escalation_status": false, "first_seen": "...", "last_seen": "...",
    "case_ids": [12],                      // INTERNAL: never show to the player
    "summary": "This phone number appeared in 1 earlier case, under a different club name than in this offer. None of them has been reviewed."}],
 "escalate": true,
 "escalation_reasons": ["phone appears under different club names"],
 "case_id": 57}
```

## 5. Build steps

1. **Place the files and add the migration.** Run the migration on the dev database. Confirm the tables and the `pg_trgm` extension exist.
2. **Run the tests.** Pure tests first (no database), then the full set against the throwaway database. All 26 must pass before you integrate. Do not edit a test to make it pass; if one fails, find out why.
3. **Map the pipeline outputs to `CaseInput`.** Find where the pipeline holds the extraction result and the three checks' results, and write one function `build_case_input(...)` next to the call site:

| `CaseInput` field | Take it from |
| --- | --- |
| `external_ref` | WhatsApp message id of the forwarded offer (for idempotency) |
| `claimed_club`, `claimed_agent` | the extraction JSON (club, agent name or signatory as written in the offer) |
| `identifiers` | agent phone / WhatsApp number (`kind=phone`, `role=agent_contact`); each payment account, M-Pesa number, till or paybill the offer asks to pay (`kind=account`, `role=payment_to`); sender email (`kind=email`, `role=sender`); explicit sender domain if the extractor gives one (`kind=domain`). Use the raw values; the module normalises them. **Do not include the player's own number or name** |
| `letter_text` | the text the extractor read from the letter (PDF text or vision-LLM transcription) |
| `letter_phash` | `image_phash(first_page_image)`; render PDFs to an image first. `None` if there is no letter |
| `wording_text` | the **agent's** messages joined with newlines (use `scam_language.select_agent_messages` output if that module exists) |
| `wording_labels` | the labels from the scam-language check (`[f["label"] for f in flags]`) |
| `mask_names` | other names in the letter worth masking (player name, signatory) |
| `summary` | a small dict of the extracted fields and check results (club, fee amounts, check statuses). **No player name, number or ID** |

4. **Call `process_case`** after the three checks have finished and before the report is built. It runs sync SQL, so call it in a thread pool if the pipeline is async. Use one transaction per case; `record_case` already wraps its inserts in one.
5. **Failure policy:** wrap the call in try/except. If linking fails (database down, missing key), log the error **without message text or identifiers**, continue with the report, and add a line "Comparison with earlier cases was not available for this check." Never fail the whole report because linking failed.
6. **Evidence report:** add a section "Comparison with earlier cases", filled from `player_view(result)`. For each link show its `summary` sentence (flagged links first; `known_contact` links neutral). If there are no links show `no_match_note`. Rules for the wording: facts only, no percentage, no "scammer", no "safe". The report must never show case ids, dates, other players' details or any raw identifier.
7. **KEFWA queue:** when `result["escalate"]` is true, add the case to the existing review queue with `result["escalation_reasons"]` and the internal `case_ids` of the matching cases, so a reviewer can open them. Add a reviewer action that calls `set_status(conn, case_id, status)`; a `confirmed_scam` status makes later cases that share an identifier escalate.
8. **Rate limiting:** a bad actor could use the bot to test whether a number is in our database. Rate-limit linking per WhatsApp user in the same way other expensive routes are limited, and never reveal more than the `player_view` sentences.
9. **Erasure:** expose `delete_case(conn, case_id)` to an admin command or script, so a player's data can be removed on request.
10. **Seed the database** (section 6) and run the demo (section 7).

## 6. Seeding

Linking returns nothing from an empty database, so seed it. Every row carries a `source`, and the UI must show simulated cases as simulated.

**Simulated demo cases (do this first).** `python make_seed_data.py seed_data` writes ten invented cases (`seed_data/seed_cases.json`, plus letter images). All numbers use the invalid prefix `+254 999` so they cannot belong to a real person. Then:

```bash
export OFFSAID_HASH_KEY=...      # the SAME key the API uses
export DATABASE_URL=postgresql://...
python seed_linking.py seed_data/seed_cases.json     # safe to re-run, keyed on external_ref
```

What the ten cases are for:

| Case | Contents | Used for |
| --- | --- | --- |
| sim-01 Coast City FC | phone, till, email, letter L1, scam wording | live demo: a new offer from another club using the same number |
| sim-02 Harbour Town FC | phone, till, gmail address, letter L2 | same payment account under another club; repurposed letter L2 |
| sim-03 Lakeside Stars | phone, till, letter L3 | payment number matches a contact number |
| sim-04 Northern Lions | scam wording W2, letter L4 | same wording from a different number |
| sim-05 Riverbend Rovers | `verified_genuine`, letter L5 | legitimate repeat: "known contact", no flag |
| sim-06 Gold Coast Academy | `confirmed_scam`, scam wording | match with a confirmed case escalates |
| sim-07 Riverside FC | `verified_genuine`, letter L6 | genuine offer |
| sim-08 to sim-10 | unrelated identifiers, no letters | so "no match" also appears |

**Real seeds (later, with the team).** Same JSON format (`{"cases": [ ... ]}`), `source` is `kefwa_past` or `public_warning`. Rules, which the agent must enforce in code or review:

- Load only agent-side details: contact numbers, payment accounts, emails, claimed club and agent, letter fingerprints. **Never the player's name, number, ID or messages.**
- `kefwa_past`: only with written consent from KEFWA and the players, anonymised before loading. Use status `confirmed_scam` only where KEFWA says so.
- `public_warning`: status `reported`, `source_url` required (the seed loader rejects rows without it), and the summary should name the source. We repeat what the source says and credit it; we do not call a named person a scammer ourselves.
- If a real seed has a letter image, `seed_linking.py` computes its hash and the image is not kept.

## 7. Demo script (the live demo from the build plan)

After seeding, run two offers through `process_case`:

```python
case = CaseInput(external_ref="demo-1", claimed_club="Lakeview United", claimed_agent="Ms Otieno",
                 identifiers=[{"kind": "phone", "value": "+254 999 000 001", "role": "agent_contact"}])
result = process_case(conn, case)
print(player_view(result))
```

Expected: one `phone` link, flagged, "This phone number appeared in 1 earlier case, under a different club name than in this offer. None of them has been reviewed.", `escalated: true`. Run it again with the same `external_ref`: same `case_id`, still one stored row. Use a different, unseeded number: no links, and the note says that no match does not mean the offer is safe.

## 8. Tests and what they prove

`pytest test_case_linking.py -v`. Pure tests cover phone formats, mobile-number-as-account, email and domain normalising, the keyed hash, the required key, masking (swapped letters mask identically, no leaks), club-name comparison, and hash-space separation of the layouts. Database tests cover: same number under a different club; same payment account; repurposed letter; same layout with different wording (no match); same club reusing its own letter (no flag); same wording from a different number (flagged, never escalates alone); legitimate repeat; match with a confirmed scam; no match; payment number matching a contact number; gmail dots and `+tags`; sender-domain match; idempotent retry that never matches itself; the player view hiding internals; **nothing raw stored** (the test dumps the three tables and checks no raw number, email or text survived); status change and erasure.

Verified here: all 26 pass on Postgres 16 with `pg_trgm`. **Not verified:** the integration with the real extraction output and the repo's database layer, PDF page rendering, thresholds on real letters, performance beyond a few cases.

## 9. Privacy and security checklist

- No raw phone number, account, email or letter file is stored; the test above enforces it for identifiers and masked text. Letter images are hashed in memory and discarded; do not write uploads to disk or object storage for linking.
- `OFFSAID_HASH_KEY` lives only in the environment or secret store.
- Do not log message text, identifiers, hashes with their raw values, or `case_ids` in application logs.
- The player view contains only summary sentences. Internal case ids go only to the KEFWA queue.
- The database holds data about agents and, indirectly, about players' offers. Kenya's Data Protection Act, 2019 applies (consent, purpose, minimisation, retention). Agree the retention period with KEFWA and the university and implement it as a scheduled delete of old `live` cases if they require it. This is not legal advice.
- Seeds from KEFWA or public warnings follow section 6's rules.

## 10. What not to do

- Do not tune thresholds or rules to make a demo look better. Real tuning needs real letters.
- Do not use a plain hash instead of the keyed hash, and do not store the raw value "just for debugging".
- Do not call a link a verdict. No "scammer", no risk percentage, no "safe".
- Do not match players to each other. Only the other party's identifiers (agent, payment account, sender) are linked.
- Do not add pgvector or embeddings in this iteration; wording similarity uses `pg_trgm` and works without a model.

## 11. Done checklist (report back with this filled in)

- [ ] Repo inspected; plan for files, migration and call site reported
- [ ] Dependencies added; `OFFSAID_HASH_KEY` documented and set in dev
- [ ] Migration created and applied; `pg_trgm` available
- [ ] All 26 tests pass against a throwaway database
- [ ] `build_case_input` written; `process_case` called after the three checks, before the report, with the failure policy
- [ ] Report section added from `player_view`; no internal fields exposed
- [ ] KEFWA queue receives escalated cases with reasons and case ids; reviewer can set status
- [ ] Rate limiting and erasure in place
- [ ] Simulated seeds loaded; demo script gives the expected output
- [ ] Anything not verified is listed plainly (for example, no real extraction output was available)

## 12. Later

Tune thresholds on real letters; add `reported` to `ESCALATE_STATUSES` if the team agrees; load real seeds from KEFWA and public warnings; consider embeddings (pgvector) for paraphrased wording; a periodic retention job; a KEFWA review screen that shows linked cases side by side.
