# OffsAid: scam-language classification handoff (v3)

Updated 2026-11-04 (supersedes v2). Purpose: let a new Claude chat continue the classification build without re-asking anything. Read the whole file first.

**Files to give the new chat along with this doc** (all in the outputs folder of the previous chat; the user has them downloaded):

- `OffsAid_classifier_training_v5.ipynb` (the one comprehensive notebook, Steps 0 to 10 plus 9b)
- `generate_synthetic.py` (data generator, v5), `synthetic_train.csv` (5,000 rows), `synthetic_val.csv` (1,000 rows)
- `gold_labelling_sheet_A.csv`, `gold_labelling_sheet_B.csv`, `make_gold.py` (gold-set tooling, section 5)
- The label guide is a Claude Doc named "OffsAid label guide and gold-set rules" in the user's account (artifact link `https://claude.ai/artifact/GkMD9ZaYheq8Qkr5kdcbQ6`). A new chat may not be able to open it; the user can export it as Word or PDF, or paste it in.

## 1. Context

- **Project:** OffsAid (Team Offside), a KamiLimu innovation competition entry. A WhatsApp bot that helps Kenyan footballers check recruitment offers from agents. A player forwards an offer, the agent, the documents and the chat. The system returns an evidence report (never a verdict such as "scammer") and refers unclear or high-risk cases to KEFWA (Kenya Footballers Welfare Association).
- **System parts:** extraction (vision LLM turns screenshots and documents into fields), agent check (fuzzy match against a licensed-agent list), opportunity check (sender domain, domain age, contacts, club, signatory, transfer-window dates, web-checking agent), **scam-language check (this handoff)**, cross-case linking (the team's stated innovation: matching phone numbers, payment accounts, letter templates and wording across cases), evidence report, KEFWA review queue.
- **Who:** Jermaine owns the classification part. Teammates build the rest. Stack plan: FastAPI on a VPS or Render, Postgres, WhatsApp Business API.
- **The user is new to fine-tuning.** Explain each step in plain language, give copy-paste code, and say what success and failure look like. Workflow that has worked: the user runs a notebook cell in Colab, pastes the output or error, Claude interprets and patches. Claude cannot train the model itself (no GPU in the chat); every training run happens in the user's Colab.
- **Time pressure:** the team must show a prototype very soon. The user wants forward progress, and prefers fixing weaknesses without waiting for the gold set. That is reasonable, but see "Honest status" in section 7.

## 2. Role of the classifier

The scam-language check reads the player's conversation with the agent and flags wording that signals fraud, quoting the exact lines. It is built in layers so something works on day one:

1. **Rules.** Partly built. Built: **negation rules** (cancel `guaranteed_outcome` and `visa_travel_arranged` for "we can't guarantee...") and a **fee rule** (adds `advance_fee_request`). **Not built:** phrase lists and regex for the other labels in English, Swahili and Sheng, and a standalone rules baseline scored on the gold set (that baseline is the number the model must beat).
2. **LLM few-shot:** conversation plus label list plus 3 to 5 examples per label, returning JSON. **Not built.**
3. **Fine-tuned small model** (built). One signal beside the rules and the LLM, not the decision-maker. If it does not beat them on the gold set, we say so and keep it as one signal.

It must never call anyone a scammer. It only reports which wording patterns it found. A false flag on an honest agent is the worst error, so precision matters as much as recall.

## 3. Decisions made

| Decision | Choice and reason |
| --- | --- |
| Approach | Fine-tune a pretrained model (not pretraining from scratch), then add rules after it |
| Base model | `Davlan/afro-xlmr-small`: XLM-R-base with vocabulary cut to about 70K tokens, adapted to 17 African languages including Swahili, plus English, French, Arabic. About 140.3M parameters. Listed as MIT on the Hub (check the model page). Later comparison: `Davlan/afro-xlmr-base` (about 0.3B) |
| Task type | Multi-label classification, one message at a time (sigmoid scores per label); combine across the conversation later |
| Labels (7) | `advance_fee_request`, `personal_account_payment`, `urgency_pressure`, `secrecy`, `guaranteed_outcome`, `visa_travel_arranged`, `off_platform_move`. "None" means no label above threshold. The team's build doc still lists "none" as a label and needs a small edit |
| GPU | Colab Pro T4 with `fp16=True` (about 9 minutes per 1,000 steps; the v5 data is 5,000 rows so a bit longer). Avoid A100/G4. Disconnect when done |
| Thresholds | **Flat 0.5 for all labels** until tuned on the real gold set. Thresholds tuned on synthetic validation were unstable between runs (0.2 to 0.8) so they are not trusted |
| Post-model rules | Run inside `classify()` after the model. Negation rules can cancel a model flag; the fee rule can only add `advance_fee_request` when the model did not raise it. No retraining is needed to change them |
| Saving format | Hugging Face folder via `trainer.save_model()` and `tokenizer.save_pretrained()` plus our own `thresholds.json`. Not pickle or joblib: pickle can execute code on load and these files tie to library versions. The rules live in code (the Step 9 cell), not in the saved folder, so the API service must include them |
| Deployment | Model runs on CPU inside FastAPI. Render Standard ($25/month, 2 GB RAM) or a VPS. Quantising to int8 is an option. Model folder goes to Drive for now (zip about 500 MB); later a private Hugging Face repo or ship with the service |

**Interface contract with the API builder** (one optional field added since v2; tell the API teammate):

```json
// input
{"messages": [{"sender": "agent", "text": "..."}]}
// output
{"flags": [{"label": "advance_fee_request", "quote": "...", "score": 0.91, "source": "model"}]}
```

`source` is `"model"` or `"rule"`. Rule-added flags use `score: 0.5` as a placeholder, not a probability.

## 4. Label definitions (full version with examples is in the label guide doc)

- `advance_fee_request`: asks the player or family to pay before a signed club contract (finder's, registration, trial, visa, ticket, medical, courier, clearance or admin fees). Near-miss (not a flag): "there is no fee", "the club pays for courier/medical".
- `personal_account_payment`: payment to a personal mobile-money number or bank account (including a relative's number) instead of an official club account.
- `urgency_pressure`: pressure to act or pay quickly ("decide by tomorrow", "before Friday", "slot closes tonight"). Near-miss: a plain document deadline on the club website, "no rush, take your time".
- `secrecy`: asks the player not to tell family, coaches, KEFWA or others.
- `guaranteed_outcome`: promises a trial, contract or move as certain. Near-miss: negated or warning statements ("we can't guarantee anything", "be careful of anyone who guarantees a contract"), now handled by the negation rule. Borderline: congratulations after a real trial can be legitimate.
- `visa_travel_arranged`: claims a visa, ticket, accommodation or invitation is already approved or arranged. Near-miss: legitimate visa information ("check the embassy website"), and negated claims ("we can't guarantee the visa").
- `off_platform_move`: asks to move the chat to another app or channel.

The two real examples per label in the guide are still illustrative (written by Claude); they need replacing with real, anonymised ones.

## 5. Data

**Synthetic data (v5, current).** `generate_synthetic.py` assembles messages from hand-written clauses (289 scam clauses, 96 hard-negative messages, 21 neutral lines, in English, Swahili, Sheng and code-mixed) with random slot values (amounts, fake phone numbers, clubs, countries), noise (lowercase, dropped punctuation, SMS spelling, typos, emoji, caps), and legitimate "hard negative" messages. Outputs `synthetic_train.csv` (5,000 rows) and `synthetic_val.csv` (1,000 rows), columns `text` plus the 7 label columns. Reproducible (`SEED = 42`); fee clauses are down-weighted (`FEE_WEIGHT = 0.5`) when sampling. Validation uses **held-out clauses** (47 of 289 scam clauses, plus every fourth negative and neutral line) that training never sees. That tests generalisation to new wording but is still synthetic.

Label counts, train: fee 1,814, personal 1,040, urgency 928, secrecy 763, guaranteed 1,218, visa 690, off-platform 715; about 26% rows with no label. Validation: fee 326, personal 241, urgency 263, secrecy 170, guaranteed 389, **visa 69, off-platform 50** (small, so those per-label scores are noisy).

Clauses were written by Claude and **not** checked by a native Swahili or Sheng speaker. They need review.

**What changed from v3 to v5, and why:**

- **v4:** about 45 new fee clauses (courier, customs, clearance, "medical money", bank charges, stamp duty, mostly without an amount), more G/V/negation hard negatives, "congratulations after a real trial" negatives, relatives' numbers. Result: the model learned the doctor and customs fee wordings partly, but not courier or clearance.
- **Diagnosis:** a simple TF-IDF logistic regression trained on the same v4 CSV scored 0.94 to 1.00 on the fee test messages (its own validation micro F1 was 0.847). So the data contained the pattern and the transformer had taken a shortcut: **48% of fee rows had a money amount versus 3% of non-fee rows**.
- **v5:** about 16 legitimate messages and 8 neutral lines that contain amounts but are not payment requests (salary, bonuses, "the club pays your KES 10,000 registration", "never send money, not even KES 5,000"). Amount share became 60% in fee rows versus 28% in non-fee rows (an amount now suggests fee about 55% of the time instead of about 95%).

Known limits: variety is the real fix, not more templates (have an LLM paraphrase each clause, then add real examples). Every clause and every dev-test message was written by the same Claude, so results on them overstate real performance.

**Gold test set (tooling built, no messages collected yet; top priority).** 100 to 200 real messages (consented cases, KEFWA, interviewed players, public scam reports). Never trained on and never used to tune thresholds or rules. All reported results must come from this set, and it must include genuine offers (at least a quarter with no flags) so the false-alarm rate can be measured.

- `gold_labelling_sheet_A.csv` and `_B.csv`: 200 blank rows each with the same ids; two people label independently. Columns: `id`, `text`, `source`, `language`, `genuine_offer`, then the 7 label columns (1/0).
- `make_gold.py`: `python make_gold.py gold_labelling_sheet_A.csv gold_labelling_sheet_B.csv` prints per-label agreement and overlap, writes `gold_disagreements.csv`, and writes `gold_test.csv` (only `text` plus the 7 labels, the format Step 8 expects). Resolve disagreements in sheet A, then re-run. It has not been run on real data yet.
- Rules for labellers (anonymise numbers and names, consent, decisions log) are in the label guide doc.
- The development messages in section 7 are contaminated and are **not** valid gold data.

**Optional warm-up data (none is about football recruitment):** UCI SMS Spam Collection; Swahili SMS Detection on Kaggle; advance-fee scam-baiting transcripts (Edwards et al. 2017); Kaggle fraud-email and recruitment-scam sets.

## 6. What has been done

1. Chose model and GPU; loaded it and verified a forward pass.
2. Built `OffsAid_classifier_training_v5.ipynb`: Step 0 install and GPU check; 1 upload and load CSVs; 2 load model; 3 tokenise (`max_length=128`); 4 metrics and Trainer; 5 train; 6 per-label report and thresholds; 7 save model, `thresholds.json` and zip to Drive (**`SAVE_DIR = offsaid_classifier_v4`**); 8 gold-set evaluation (waits for `gold_test.csv`); **9 inference with rules**; **9b raw scores for the messages the model has been missing**; 10 CPU latency check.
3. Training config that works: 5 epochs, batch 32, lr 4e-5, weight decay 0.01, `warmup_steps=100`, linear schedule, `eval_strategy="epoch"`, `save_strategy="epoch"`, `load_best_model_at_end=True`, `metric_for_best_model="f1_macro"`, `fp16=True`, `seed=42`.
4. Five data versions generated and trained (v1 to v5). Each retrain: restart the runtime, upload the new CSVs, re-run from Step 0 or 1.
5. `classify()` returns **sentence-level quotes**: it scores each sentence and the whole message, and a sentence wins over the whole message when it crosses the threshold.
6. **Negation rules** (Step 9 cell, run after the model):
   - `negated_guarantee(text)`: True only if the text has a guarantee/promise word (guarantee, promise, assure, kuhakikisha, ahadi, kuahidi...) and **every** such use is negated (a negator such as can't, no one, hakuna, hatuwezi within 3 words before it, in the same clause) or warned about (careful, beware, jihadhari earlier in the clause). Clause breaks are punctuation and "but / ila / lakini". It cancels `guaranteed_outcome`.
   - `negated_visa_claim(text)`: True if every clause that mentions visa/ticket/flight/permit/hotel/travel only does so inside a negated guarantee. It cancels `visa_travel_arranged`. "Your visa is approved" and "we can't guarantee the visa, but your flight is booked" still flag.
   - Tested on 18 sentences without the model: all passed.
7. **Fee rule** (`fee_rule(sentence)`): True when a payment word is within 7 words of a fee word in a clause, and the sentence does not say nobody pays. Strong fee words (fee, charges, cost, customs, clearance, duty, ada, gharama, ushuru, forodha) count with any payment word including "send". Weak words (courier, DHL, shipping, delivery, insurance, tax) count only with a real pay verb (pay, settle, cover, clear, lipa...), so "send the contract by courier" does not fire. Also fires on "medical money", "pesa ya medical/courier/customs" and "pay the doctor". Exoneration patterns: no fee, free, you pay nothing, the club pays/covers, paid by the club, never/do not send or pay, hakuna malipo, bila malipo, inalipa, bure, usitume. It runs on sentence units only and only adds a flag the model did not raise.
   - Measured on synthetic data: fires on 54 to 59% of fee rows and on 0 of 3,860 non-fee rows. **This 0% is optimistic** (the rule was written while looking at the same clause bank). Real false-alarm rate is unknown.
   - Known gaps: "send money for the DHL" (no pay verb); Swahili passive forms where a comma inside a clause separates the noun from the verb; Sheng phrasings outside the bank; honest sentences that combine a pay verb with these nouns without an exoneration phrase.

## 7. Results so far

**Synthetic validation (micro / macro F1 at threshold 0.5).**

| Run | Micro F1 | Macro F1 | Notes |
| --- | --- | --- | --- |
| v1 (800 val rows) | 0.967 | 0.969 | guaranteed_outcome recall 0.878, urgency recall 0.88 |
| v2 | 0.958 | 0.963 | visa precision 0.90, urgency recall 0.79 |
| v3 | 0.971 | 0.976 | urgency 0.93/0.96 (P/R), guaranteed 0.945/0.884, visa 0.95/1.00 |
| v4 and v5 (1,000 val rows) | **not reported** | **not reported** | the user ran these but the Step 6 report was not pasted into the chat |

Ignore the "samples avg" row in the report (rows with no label count as zero). Synthetic scores show the pipeline works; they are **not** real-world accuracy.

**Development messages, final state (model v5 data + rules, threshold 0.5).** All 12 now return what the author wanted. These are contaminated: the author wrote them, then wrote the rules after seeing the model miss them. They are sanity checks only.

| Message | Result |
| --- | --- |
| "Congratulations bro! ... paying the courier for your papers." | guaranteed (model), fee (**rule**) |
| "DHL is holding your contract until the delivery fee is paid. Send it today." | urgency (model), fee (**rule**) |
| Swahili "Hakuna mtu atakuhakikishia visa..." / "We cannot promise any visa..." | nothing (correct) |
| "The doctor must be paid before your medical, send ... sister's number" | fee and personal account (model) |
| "Tuma ile pesa ya customs ... usiwaambie wengine" | fee and secrecy (model) |
| Congratulations after the trial, no-fee trial invite, "the club will pay for your courier", "send your medical results" | nothing (correct) |
| "Your flight is booked and your visa is approved, just settle the clearance charges." | visa (model), fee (**rule**) |
| "We can't guarantee the visa, but your contract is 100% guaranteed." | guaranteed only (negation rules working) |

**What the model alone still cannot do (Step 9b raw scores):** courier, delivery and clearance wording scores 0.01 to 0.05 for the fee label; "Congratulations! Your contract is waiting. Just cover the courier charges" scores 0.19 for `guaranteed_outcome`; "medical money before Friday, send to my wife's number" scores 0.54 for `personal_account_payment` (barely over threshold). The rule layer covers the fee misses; these other scores are fragile. Earlier runs also showed a likely false `urgency_pressure` on slot-payment messages ("...ndio slot ikae", 0.88) and an occasional false `visa_travel_arranged` on delivery wording; neither appeared in the final run.

**Honest status (use this wording with the team):** working pipeline with a fine-tuned model plus negation and fee rules; evaluated only on synthetic data and hand-written development messages; real-world accuracy not yet measured. Do not quote "12 of 12" or the synthetic F1 as accuracy.

**Not yet run or not yet reported:** Step 8 (needs the gold set), Step 10 (CPU latency), Step 6 for the v4/v5 data. The user confirmed Step 7 (save to Drive) was run in an earlier session; the exact zip that holds the latest v5-data model is unconfirmed (the notebook saves it as `offsaid_classifier_v4.zip`, earlier ones are `_v2` and `_v3`).

## 8. Next steps, in order

1. **Gold set:** collect and label 100 to 200 real messages with two people using the sheets, run `make_gold.py`, then Step 8. Report per-label precision, recall and F1 and the false-flag rate on genuine offers, with the set size stated. Re-tune thresholds on this set only. If only a few dozen messages are available, run it anyway and say how small the set is. **Stop tuning against the 12 development messages; further patches would only overfit to them.**
2. **Prototype hookup:** wrap `classify()` (including the rule code) in a FastAPI endpoint matching the contract; run Step 10 first to confirm CPU speed. Save and back up the model zip and the Step 9 cell.
3. **Rules baseline for the remaining labels** (English, Swahili, Sheng), scored on the gold set. This is the number to beat.
4. **LLM few-shot layer** returning the JSON contract, scored on the same gold set.
5. **Variety:** LLM-generated paraphrases of each clause, native-speaker review of Swahili and Sheng, real examples from gold-set errors (never the gold messages themselves; keep a separate pool for training).
6. Combine the signals; decide how to aggregate per-message flags across a whole conversation. Consider tighter clause-level quotes (for example split on commas) if sentence quotes are too long.
7. Compare with `afro-xlmr-base`, plain XLM-R base, and rules/LLM. Keep the fine-tuned model only as one signal if it does not win. The TF-IDF classifier used as a diagnostic is a cheap extra signal worth testing on the gold set.
8. Edit the team build doc: remove "none" as a label; update the classifier description to match what is built (model plus rules, with the honest status above).

## 9. Known fixes and gotchas

- `warmup_ratio` is not accepted by the installed `transformers`: use `warmup_steps=100`. `eval_strategy` is accepted.
- The pip message "cudf-cu13 requires pyarrow<24" is harmless; if `datasets` fails, `pip install "pyarrow<24"` and restart the runtime.
- Restart the runtime before each retrain so old data and weights are not reused.
- "Writing model shards" lines during training are checkpoint saves (harmless).
- If memory runs out, lower the batch size; if `fp16` errors, set `fp16=False`.
- Thresholds saved in `thresholds.json` are whatever Step 6 left in `thresholds` (reset to 0.5 for all labels).
- **The Step 9 cell does not need retraining.** Changing rules means replacing that cell and running it with the model already in memory. If the session ended, reload the model from the Drive zip first.
- Each retrain bumps `SAVE_DIR` so earlier model zips on Drive are not overwritten.
- Claude cannot run the model in the chat. The Step 9 cell was checked here only with a stand-in model that returns near-zero scores, which proved the code runs and the rule fires; model behaviour is whatever Colab shows.

## 10. Risks and open questions

- The gold set may stay small. If so, report results cautiously and say how small it is.
- Sheng coverage is weak in the base model, so real Sheng examples matter.
- Synthetic data and rules written by the same author inflate scores. Only the gold set counts.
- The rules can cause false alarms on honest agents in ways no one has measured yet. Watch the gold-set false-flag rate on genuine offers first.
- Public datasets are not about football recruitment, so they only warm the model up.
- Check the base model's license on the Hub page and confirm model IDs before relying on them.
- Label ambiguity: "Congratulations, you were selected" can be legitimate after a trial.
- The transformer's fee recognition is still weak on its own; if the fee rule is removed or fails on real data, the model alone will miss fees without amounts.

## 11. How to work with the user

- Plain language, one step at a time, copy-paste code, and what success looks like. The user wants one comprehensive notebook and prefers to paste errors rather than have many separate chats.
- Say what is verified versus assumed. Do not call something fixed until the user's Colab output shows it, and do not present synthetic scores or development-message results as accuracy. The team has been corrected on overstated claims, so keep wording precise.
- The user is time-pressured and has a usage limit, so be efficient and avoid long detours. When updating data or the notebook, send the full files and say exactly which cells to re-run, and whether a retrain is needed.
