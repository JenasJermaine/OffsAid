"""OffsAid scam-language check: model + rules, ready to call from an API route.

Usage (inside the API service):

    from scam_language import ScamLanguageChecker
    checker = ScamLanguageChecker.load()            # once, at startup (reads OFFSAID_MODEL_DIR)
    result  = checker.check_chat(extraction)        # extraction = output of the screenshot-extraction step
    # or: checker.classify([{"sender": "agent", "text": "..."}])

Output contract:
    {"flags": [{"label", "quote", "score", "source", "meaning"}]}
    source = "model" (the classifier) or "rule" (fee rule; its score 0.5 is a placeholder, not a probability)

IMPORTANT: this reports wording patterns. It never decides that someone is a scammer.
Status: evaluated only on synthetic data and hand-written test messages, not on a real gold set.

The rule code below is the Step 9 cell of OffsAid_classifier_training.ipynb, unchanged except for
being wrapped in a module. torch / transformers are imported only when a model is loaded, so the
rules can be unit-tested without them.
"""
import json
import os
import re

LABELS = ["advance_fee_request", "personal_account_payment", "urgency_pressure",
          "secrecy", "guaranteed_outcome", "visa_travel_arranged", "off_platform_move"]

# plain-language meaning, for the evidence report (wording patterns, not accusations)
LABEL_MEANING = {
    "advance_fee_request": "Asks for a payment or fee before any signed club contract.",
    "personal_account_payment": "Asks for payment to a personal number or account instead of an official club account.",
    "urgency_pressure": "Pressures the player to act or pay quickly.",
    "secrecy": "Asks the player not to tell family, coaches or others.",
    "guaranteed_outcome": "Promises a trial, contract or move as certain.",
    "visa_travel_arranged": "Claims a visa, ticket or travel is already approved or arranged.",
    "off_platform_move": "Asks to move the conversation to another app or channel.",
}

# ---------------------------------------------------------------------------------------------
# negation rules (run AFTER the model; the model itself is unchanged)
# ---------------------------------------------------------------------------------------------
# words that mean "promise / guarantee" in English and Swahili
_GUAR = re.compile(r"\b(guarant\w*|promis\w*|assur\w*|\w*hakikish\w*|hatuhakikishi|uhakika|ahadi|ahidi\w*|\w*ahidi\w*)\b", re.I)
# a negator closely before it, e.g. "can't guarantee", "no one can promise", "hakuna mtu anaweza guarantee"
_NEG = re.compile(
    r"\b(can't|cant|cannot|can not|won't|wont|couldn't|unable|not|never|no one|nobody|none|"
    r"hakuna|hatuwezi|siwezi|hawezi|hawawezi|hapana)\b", re.I)
# a warning earlier in the same clause, e.g. "be careful of anyone who guarantees..."
_WARN = re.compile(r"\b(careful|beware|avoid|scams?|scammers?|fake|jihadhari|tahadhari|usiamini|wadanganyifu)\b", re.I)
# clause boundaries: punctuation or "but / ila / lakini"
_BREAK = re.compile(r"[,;:.!?\n]|\b(?:but|however|ila|lakini)\b", re.I)
# visa / travel words
_VISA = re.compile(r"\b(visa|visas|ticket|tickets|tiketi|flight|permit|kibali|hotel|hoteli|travel|safari|passport)\b", re.I)


def negated_guarantee(text, max_gap=3):
    """True only if the text uses a guarantee/promise word and EVERY such use is negated or warned about.
    No guarantee word, or at least one un-negated one -> False (the model decides)."""
    hits = list(_GUAR.finditer(text))
    if not hits:
        return False
    for h in hits:
        start = max((m.end() for m in _BREAK.finditer(text[:h.start()])), default=0)
        before = text[start:h.start()]
        negated = any(len(before[n.end():].split()) <= max_gap for n in _NEG.finditer(before))
        if not negated and _WARN.search(before):
            negated = True
        if not negated:
            return False
    return True


def negated_visa_claim(text):
    """True if every clause that mentions visa/ticket/flight/permit... only does so inside a negated
    guarantee ("we can't guarantee the visa"). A clause like "your visa is approved" -> False."""
    clauses = [c for c in _BREAK.split(text) if c and c.strip()]
    hits = [c for c in clauses if _VISA.search(c)]
    return bool(hits) and all(negated_guarantee(c) for c in hits)


# label -> rule that cancels a flag
SUPPRESS = {"guaranteed_outcome": negated_guarantee, "visa_travel_arranged": negated_visa_claim}

# ---------------------------------------------------------------------------------------------
# fee rule: a payment word near a fee word, unless the sentence says nobody pays
# ---------------------------------------------------------------------------------------------
_PAYV = {"pay", "paid", "paying", "payment", "payments", "send", "sending", "settle", "settled", "cover", "covering", "clear", "cleared",
         "transfer", "pays", "lipa", "lipia", "lipwa", "tuma", "nitumie", "weka", "kulipa", "malipo", "ulipe", "ilipwe", "ilipiwe", "kulipwa"}
# strong fee words count with any payment word; weak ones (courier, delivery...) only with a real pay verb,
# so "send the contract by courier" or "send your delivery address" is not a fee request
_FEEN = {"fee", "fees", "charge", "charges", "cost", "costs", "customs", "clearance", "duty", "ada", "gharama", "ushuru", "forodha"}
_WEAKN = {"courier", "dhl", "shipping", "delivery", "insurance", "tax"}
_TRUEPAY = {"pay", "paid", "paying", "payment", "payments", "pays", "settle", "settled", "cover", "covering", "clear", "cleared",
            "lipa", "lipia", "lipwa", "kulipa", "ulipe", "ilipwe", "ilipiwe", "kulipwa", "malipo"}
# doctor / medical only count with a real pay verb ("pay the doctor"), never with "send" ("send your medical results")
_PAYONLY_V = {"pay", "paid", "paying", "payment", "payments", "lipa", "lipwa", "kulipa", "ulipe", "ilipwe", "kulipwa"}
_PAYONLY_N = {"doctor", "medical", "medicals", "daktari", "vipimo"}
_FEE_PHRASE = re.compile(r"\b(medical|vipimo|embassy|visa)\s+(money|fee|fees|payment)\b|\bpesa\s+ya\s+(medical|vipimo|courier|customs|clearance|docs|documents|embassy|dhl)\b|\bmedical money\b", re.I)
_NOT_A_REQUEST = re.compile(
    r"\b(no|zero|without)\s+(\w+\s+){0,2}(fee|fees|charge|charges|cost|costs|payment|payments)\b|\bno cost\b|\bfree\b|\bfree of charge\b|"
    r"\b(do|does|did) not (ask|need|require|charge)\b|\b(don't|doesn't|never) (ask|charge)\b|\bnever (ask|send|pay)\b|"
    r"\b(you|players?) (do not|don't|will not|won't|never) pay\b|\bpay(s)? nothing\b|\bpaid by\b|\bnot (pay|paying)\b|"
    r"\b(club|club's|company|buying club)\s+(will\s+)?(pay|pays|paying|cover|covers|covering|handle|handles|reimburse\w*)\b|"
    r"\b(do not|don't|never)\s+(send|pay|transfer|share)\b|\bhakuna malipo\b|\bbila\s+(malipo|gharama)\b|\bhulipi\b|\bhaulipi\b|"
    r"\b(usitume|usilipe|usitume)\b|\binalipa\b|\bitalipa\b|\bitakulipia\b|\binalipwa\b|\bzinalipwa\b|\bhaitakuomba\b|\bhaiulizi\b|\bbure\b", re.I)


def fee_rule(text, max_gap=7):
    """True if a clause has a payment word within max_gap words of a fee word and the sentence does not say
    nobody pays. Deliberately simple: it only ADDS advance_fee_request when the model scored it below threshold."""
    text = re.sub(r"(?<=\d),(?=\d)", "", text)   # 20,000 -> 20000 so the comma is not a clause break
    if _NOT_A_REQUEST.search(text):
        return False
    if _FEE_PHRASE.search(text):
        return True
    for clause in _BREAK.split(text):
        words = re.findall(r"[a-zA-Z']+", clause.lower())
        v = [i for i, w in enumerate(words) if w in _PAYV]
        n = [i for i, w in enumerate(words) if w in _FEEN]
        if any(abs(i - j) <= max_gap for i in v for j in n):
            return True
        tv = [i for i, w in enumerate(words) if w in _TRUEPAY]
        wn = [i for i, w in enumerate(words) if w in _WEAKN]
        if any(abs(i - j) <= max_gap for i in tv for j in wn):
            return True
        pv = [i for i, w in enumerate(words) if w in _PAYONLY_V]
        pn = [i for i, w in enumerate(words) if w in _PAYONLY_N]
        if any(abs(i - j) <= max_gap for i in pv for j in pn):
            return True
    return False


# ---------------------------------------------------------------------------------------------
# picking the messages to check from the extraction output
# ---------------------------------------------------------------------------------------------
_PLAYER_NAMES = {"player", "user", "me", "self", "you", "sender_is_user"}


def select_agent_messages(messages):
    """Keep only what the OTHER party wrote. Drops the player's own messages, empty text, and items the
    extractor marked as quoted replies, system notices or non-text attachments.
    Accepts dicts with 'text' (or 'message'/'body') and 'sender' (or 'role'/'from')."""
    kept = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        text = (m.get("text") or m.get("message") or m.get("body") or "").strip()
        sender = str(m.get("sender") or m.get("role") or m.get("from") or "agent").strip().lower()
        if not text or sender in _PLAYER_NAMES:
            continue
        if m.get("quoted") or m.get("is_quoted") or m.get("system") or m.get("type") in {"system", "quoted"}:
            continue
        kept.append({"sender": "agent", "text": text})
    return kept


# ---------------------------------------------------------------------------------------------
# the checker
# ---------------------------------------------------------------------------------------------
class ScamLanguageChecker:
    """Holds the model (loaded once) and runs model + rules."""

    def __init__(self, tokenizer=None, model=None, labels=None, thresholds=None, device="cpu"):
        self.tokenizer, self.model, self.device = tokenizer, model, device
        self.labels = labels or LABELS
        self.thresholds = thresholds or {l: 0.5 for l in self.labels}
        self.mode = "model+rules" if model is not None else "rules_only"

    @classmethod
    def load(cls, model_dir=None, device="cpu"):
        """Load the saved Hugging Face folder (config.json, model.safetensors, tokenizer files, thresholds.json).
        Label order is read from the model's own config, so it cannot silently drift from training."""
        model_dir = model_dir or os.environ.get("OFFSAID_MODEL_DIR", "models/offsaid_classifier_v4")
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        torch.set_num_threads(int(os.environ.get("OFFSAID_TORCH_THREADS", "2")))
        tokenizer = AutoTokenizer.from_pretrained(model_dir)
        model = AutoModelForSequenceClassification.from_pretrained(model_dir).to(device).eval()
        labels = [model.config.id2label[i] for i in range(model.config.num_labels)]
        if set(labels) != set(LABELS):
            raise RuntimeError(f"Model labels {labels} do not match the expected labels {LABELS}")
        thresholds = {l: 0.5 for l in labels}
        th_path = os.path.join(model_dir, "thresholds.json")
        if os.path.exists(th_path):
            thresholds.update(json.load(open(th_path)).get("thresholds", {}))
        return cls(tokenizer, model, labels, thresholds, device)

    # one probability list per unit of text; overridden in tests
    def _score(self, units):
        import torch
        enc = self.tokenizer(units, return_tensors="pt", truncation=True, max_length=128, padding=True).to(self.device)
        with torch.no_grad():
            return torch.sigmoid(self.model(**enc).logits).cpu().tolist()

    def classify(self, messages):
        """messages: [{"sender": "agent", "text": "..."}] -> {"flags": [...]}"""
        best, via_rule = {}, {}
        for m in messages:
            text = m["text"]
            sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]
            if not sents:
                continue
            units = sents + ([text] if len(sents) > 1 else [])    # sentences and the whole message
            probs = self._score(units) if self.model is not None else [[0.0] * len(self.labels) for _ in units]
            for u, p in zip(units, probs):
                is_sentence = (u != text) or len(sents) == 1
                for i, l in enumerate(self.labels):
                    if p[i] < self.thresholds[l]:
                        continue
                    if l in SUPPRESS and SUPPRESS[l](u):     # "we can't guarantee the visa" is not a claim
                        continue
                    rank = (1 if is_sentence else 0, float(p[i]))
                    if l not in best or rank > best[l][0]:
                        best[l] = (rank, u)
                # fee rule: sentence units only; only adds a flag the model did not raise
                if is_sentence and fee_rule(u) and "advance_fee_request" not in via_rule:
                    via_rule["advance_fee_request"] = u
        flags = [{"label": l, "quote": q, "score": round(r[1], 3), "source": "model"} for l, (r, q) in best.items()]
        if "advance_fee_request" in via_rule and "advance_fee_request" not in best:
            flags.append({"label": "advance_fee_request", "quote": via_rule["advance_fee_request"],
                          "score": 0.5, "source": "rule"})
        for f in flags:
            f["meaning"] = LABEL_MEANING.get(f["label"], "")
        return {"flags": flags}

    def check_chat(self, extraction):
        """extraction: the extractor's output. Either a list of messages or a dict with a 'messages' list.
        Only the other party's (agent's) messages are classified."""
        msgs = extraction.get("messages", []) if isinstance(extraction, dict) else extraction
        agent_msgs = select_agent_messages(msgs)
        result = self.classify(agent_msgs)
        result["messages_checked"] = len(agent_msgs)
        result["mode"] = self.mode
        return result
