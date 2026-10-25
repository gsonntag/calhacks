"""What the caller may agree to on the patient's behalf: the mandate, enforced in code.

The patient approved one plan (agents/decide.py) before the call. Its brief says what the call may accept:
the corrected balance, and the interest-free payment plan when the plan has one. Anything a billing rep
offers or asks for goes through `evaluate` before the caller answers it (the caller's check_offer tool).
It fails closed: an offer kind or a piece of information this module doesn't know is refused.

Refusals are spoken with their reason, sent to the case page as a `guardrail` event and kept in the audit
log, so the patient sees every decision the call made for them.

`screen` is the backstop for when the model doesn't ask: every sentence the caller speaks passes it before TTS,
and a number or a promise the mandate didn't clear is replaced with a safe line.
"""
import re
from dataclasses import dataclass

KINDS = ("payment_plan", "balance", "pay_now", "settlement", "info_request")
# what the caller may give to verify identity: only fields the brief carries (the patient's own account)
SHAREABLE = {"account_number": "account_number", "claim_number": "claim_number",
             "date_of_birth": "patient_date_of_birth", "member_id": "insurance_member_id", "name": "patient"}
NEVER_SHARED = {"ssn": "a Social Security number", "card": "card details", "bank": "bank account details",
                "password": "passwords or PINs", "other_patient": "another patient's information"}
SLACK = 0.5  # rounding on spoken amounts
# what the model may call a piece of information, normalised (lowercase, spaces and hyphens to "_")
INFO_ALIASES = {"dob": "date_of_birth", "birthdate": "date_of_birth", "birth_date": "date_of_birth",
                "birthday": "date_of_birth", "member_number": "member_id", "insurance_id": "member_id",
                "insurance_member_id": "member_id", "policy_number": "member_id", "account": "account_number",
                "claim": "claim_number", "patient_name": "name", "social": "ssn", "social_security": "ssn",
                "social_security_number": "ssn", "credit_card": "card", "card_number": "card", "debit_card": "card",
                "bank_account": "bank", "routing": "bank", "routing_number": "bank", "pin": "password"}


@dataclass
class Decision:
    allowed: bool
    reason: str
    say: str

    def as_dict(self) -> dict:
        return {"decision": "allowed" if self.allowed else "refused", "reason": self.reason, "say": self.say}


def _money(x: float) -> str:
    return f"${x:,.0f}"


def _num(x) -> float | None:
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def evaluate(brief: dict, kind: str, monthly=None, months=None, total=None, interest_pct=None,
             info: str = "") -> Decision:
    """One offer or request from the rep, against the brief the patient approved."""
    target = _num(brief.get("balance_after_assistance"))
    plan = brief.get("proposed_payment_plan") or {}
    monthly, months, total, interest = _num(monthly), _num(months), _num(total), _num(interest_pct)

    if kind == "pay_now":
        return Decision(False, "Remy never pays on a call",
                        "I can't make any payment on this call. The patient will set up any payments themselves.")

    if kind == "info_request":
        what = re.sub(r"[\s\-]+", "_", (info or "").strip().lower())
        what = INFO_ALIASES.get(what, what)
        if what in NEVER_SHARED:
            return Decision(False, f"Never shares {NEVER_SHARED[what]}",
                            f"I'm not able to give out {NEVER_SHARED[what]}. I can give you the account number instead.")
        field = SHAREABLE.get(what)
        if field and brief.get(field):
            return Decision(True, f"The patient's {what.replace('_', ' ')} is in the approved brief for verification", "")
        return Decision(False, f"“{info}” isn't something the patient approved sharing",
                        "I don't have that to share. I can give you the account number and the claim number.")

    if kind in ("balance", "settlement"):
        amount = total if total is not None else monthly
        if amount is None:
            return Decision(False, "No amount was given", "What amount would that be?")
        if target is not None and amount <= target + SLACK:
            return Decision(True, f"{_money(amount)} is at or below the corrected balance of {_money(target)}", "")
        what = "settle" if kind == "settlement" else "agree to a balance"
        return Decision(False, f"{_money(amount)} is more than the corrected balance of {_money(target or 0)}",
                        f"I can't {what} of {_money(amount)}. Based on the insurance statement and the hospital's own "
                        f"policy, the balance should be {_money(target or 0)}.")

    if kind == "payment_plan":
        if not plan or not plan.get("months"):
            return Decision(False, "The patient's plan has no payment plan to agree to",
                            "I'm not able to agree to a payment plan today. The patient will decide on that once the "
                            "balance is corrected.")
        if interest and interest > 0:
            return Decision(False, f"The plan has {interest:g}% interest; the patient approved interest-free only",
                            "The patient can only agree to an interest-free plan, as your billing policy allows.")
        cap = float(plan.get("monthly") or 0)
        if monthly is not None and monthly > cap + SLACK:
            return Decision(False, f"{_money(monthly)}/month is more than the {_money(cap)}/month the patient approved",
                            f"That's more than the patient can pay. They can do {plan.get('text') or _money(cap) + ' a month'}.")
        n = float(plan["months"])
        if months is not None and months > n:  # same monthly for longer is a bigger total
            return Decision(False, f"A {months:g}-month plan is longer than the {n:g}-month plan the patient approved",
                            f"The patient can only agree to {plan.get('text') or f'{n:g} monthly payments'}.")
        if total is None and monthly is not None and months is not None:
            total = monthly * months  # the approved plan's last payment may be smaller: its full months are fine
            target = max(target or 0, cap * n)
        if total is not None and target is not None and total > target + SLACK:
            return Decision(False, f"A plan for {_money(total)} would accept more than the corrected {_money(target)}",
                            f"A plan has to be for the corrected balance of {_money(target)}, not {_money(total)}. "
                            "Can we correct the balance first?")
        if monthly is None and total is None:
            return Decision(False, "No monthly amount or total was given", "What would the monthly payment be?")
        return Decision(True, "Interest-free and within the plan the patient approved", "")

    return Decision(False, f"Unknown kind of offer: {kind}", "I'll need to check with the patient before agreeing to that.")


TOOL_SPEC = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": list(KINDS),
                 "description": "payment_plan: rep offers a plan · balance: rep wants you to agree an amount is owed · "
                                "settlement: a lump-sum deal · pay_now: rep asks for payment today · "
                                "info_request: rep asks for a piece of information"},
        "monthly": {"type": "number", "description": "monthly payment the rep offered"},
        "months": {"type": "number"},
        "total": {"type": "number", "description": "total or balance amount"},
        "interest_pct": {"type": "number"},
        "info": {"type": "string", "description": "for info_request: ssn, date_of_birth, member_id, account_number, "
                                                  "claim_number, name, card, bank, password, other_patient, or what was asked"},
    },
    "required": ["kind"],
}


# ---------------------------------------------------------------- the speech screen
# check_offer is the model's to call; this isn't. Every sentence Remy speaks passes `screen` on its way to
# TTS (CallSession._reply), so a number or a promise the mandate didn't clear is never said, whatever the model wrote.

UNLOCKS = ("payment_plan", "balance", "settlement")  # an allowed check_offer of these lets the caller agree
SAY = {"pii": "I can't share that, but I can give you the account number.",
       "commit": "I'm not able to agree to that on this call; the patient will decide.",
       "pay_now": "I can't make any payment on this call. The patient will set up any payments themselves.",
       "human": "I'm an automated assistant, calling on behalf of the patient with their authorization."}

_DIGIT_WORDS = {"zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                "six": "6", "seven": "7", "eight": "8", "nine": "9"}
_W = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|\d)"
_SPOKEN_RUN = re.compile(rf"\b{_W}(?:[\s,\-]+{_W}){{4,}}\b", re.I)
_DIGIT_RUN = re.compile(r"(?<![\d.,])\d(?:[ \-]?\d)*(?![\d.,]\d)")  # "123-45-6789", "4111 1111 1111 1111"
_NEG = re.compile(r"\b(not|never|no|cannot|can't|won't|don't|doesn't|isn't|unable|n't)\b|n't\b", re.I)
_ASK = re.compile(r"\b(would|could|can|should|do|does|did|will|shall|may|might|must|what|how|if|whether)\s*$", re.I)

_WE = r"\b(?:i|we|i'm|we're|i'll|we'll|i'd|we'd|i've|we've|the patient|she|he|she'll|he'll|she'd|he'd)"
_GAP = r"(?:\s+[\w'$.]+){0,4}?\s+"  # up to four words between who and the verb ("I'd be happy to pay")
_COMMIT = [  # who + verb; group 1 is the verb. Skipped when the clause has a negation before the verb.
    ("commit", re.compile(_WE + _GAP + r"(agree(?:d|ing)?|commit(?:ted)?)\s+to\b", re.I)),
    ("commit", re.compile(_WE + _GAP + r"(pay\b(?!\s+(?:close\s+)?attention)|"  # not "pay attention"
                          r"make\s+(?:the\s+|a\s+|those\s+|these\s+)?payments?\b(?!\s+(?:plan|arrangement)))", re.I)),
    ("commit", re.compile(_WE + _GAP + r"(accept|authori[sz]e)\b(?:\s+[\w$.']+){0,4}?\s+"
                          r"(?:plan|offer|terms|balance|settlement|payments?|amount|deal|charge)\b", re.I)),
    ("commit", re.compile(_WE + _GAP + r"(take)\s+(?:that|the|this|your)\s+(?:payment\s+)?(?:plan|offer|deal)\b", re.I)),
]
_IMPERATIVE = [  # no who: skipped when the clause has a negation before them
    ("commit", re.compile(r"\bgo ahead and\s+(enrol+|sign|set (?:it|that|this|the plan) up|set up the plan|"
                          r"start the plan|accept)\b", re.I)),
    ("commit", re.compile(r"\b(?:enrol+|sign)\s+(?:her|him|them|me|us|the patient)\s+(?:up\s+)?(?:for|in|into|on)\s+"
                          r"(?:the|a|that|this|your)\s+(?:payment\s+|monthly\s+)?plan\b", re.I)),
    ("commit", re.compile(r"^\W*(?:yes\W+|okay\W+|ok\W+|sure\W+)?(?:please\s+)?(?:set (?:it|that|this) up|"
                          r"enrol+ (?:her|him|them|us|me)|sign (?:her|him|them|us|me) up|let's do (?:it|that))\b", re.I)),
    ("commit", re.compile(r"\b(?:it's a deal|you've got a deal|sounds like a deal)\b|^\W*deal\W*$", re.I)),
    ("pay_now", re.compile(r"\b(?:charge|run|debit)\s+(?:the|my|her|his|their|that|this|our)\s+(?:card|account|"
                           r"credit card|debit card|bank account)\b", re.I)),
    ("pay_now", re.compile(r"\b(?:process|take)\s+(?:the|a|that|this)\s+payment\s+(?:now|today)\b", re.I)),
    ("pay_now", re.compile(r"\bput\s+(?:it|that|this|the balance)\s+on\s+(?:the|my|her|his|their)\s+card\b", re.I)),
    ("human", re.compile(r"\b(?:i'm|i am)\s+(?:just\s+|actually\s+|really\s+)?(?:an?\s+)?(?:real\s+|actual\s+|live\s+)?"
                         r"(?:person|human(?: being)?)\b", re.I)),
    ("commit", re.compile(r"\bput\s+(?:her|him|them|me|us|the patient)\s+down\s+for\b", re.I)),
    ("pay_now", re.compile(r"\bbill\s+(?:the|my|her|his|their|our)\s+(?:credit\s+|debit\s+)?card\b", re.I)),
]
_NOT_A_BOT = re.compile(r"\b(?:i'm|i am)\s+not\s+(?:an?\s+)?(?:robot|bot|machine|computer|recording|ai|a\.i\.|"
                        r"automated|artificial|virtual)\b", re.I)
_NOW = re.compile(r"\b(?:today|now|right now|over the phone|on this call)\b", re.I)
_CLAUSE = re.compile(r"[,;:]\s|[.!?]\s|[—–]|\bbut\b|\bthough\b", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])[\"')\]]*\s+")
_MODAL = re.compile(r"\b(?:would|could|should|can|will|might|may)\b|\w'(?:d|ll)\b", re.I)  # "the patient would pay ...?"
_SSN_PART = re.compile(r"\b(?:ssn|social(?:\s+security)?)\b.{0,20}?\d{4}", re.I)      # "her SSN ends in 6789"
_PII_WORDS = re.compile(r"\b(?:ssn|s\s?s\s?n|social|security|card|credit|debit|cvv)\b", re.I)
# amounts in a promise: "$600", "$100 a month", "monthly payments of $100"; spoken amounts can't be checked
_AMOUNT = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)", re.I)
_PER_MONTH_AFTER = re.compile(r"\s*(?:a|per|each|every|/)\s*mo(?:nth)?\b|\s+monthly\b", re.I)
_PER_MONTH_BEFORE = re.compile(r"\bmonthly\s+(?:payments?|installments?)\s+of\s+$", re.I)
_WORD_AMOUNT = re.compile(r"\b(?:hundred|thousand|grand|dollars?|bucks)\b", re.I)
_CORRECTED = re.compile(r"\b(?:corrected|correct balance|correction)\b", re.I)


_ZIP4 = re.compile(r"(?<![\d\-])\d{5}-\d{4}(?![\d\-])")


def _digits(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _runs(text: str) -> list[str]:
    """Digit runs as they'd be read out: "123-45-6789" and "4111 1111 1111 1111" are one run each, but
    "account 48213977 6 months" is two (groups of digits joined only when they look like one number)."""
    out = []
    for m in _DIGIT_RUN.finditer(text):
        groups = re.split(r"[ \-]", m.group())
        sizes = {len(g) for g in groups}
        if len(groups) == 1 or sizes == {1} or min(sizes) >= 2 and max(sizes) <= 6:
            out.append("".join(groups))
        else:
            out += groups
    for m in _SPOKEN_RUN.finditer(text):  # "one two three, four five ..."
        out.append("".join(_DIGIT_WORDS.get(w.lower(), w) for w in re.findall(r"\w+", m.group())))
    return out


def _brief_numbers(brief) -> list[str]:
    """Every digit string in the brief (IDs, previous case numbers, payment references, the DOB): saying one
    back is not a leak. Amounts (numbers, not strings) are left out."""
    out: list[str] = []

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            out.extend(_runs(v))
            if len(v) <= 40:  # an ID: all its digits, however it's grouped
                out.append(_digits(v))
    walk(brief)
    return [d for d in out if len(d) >= 4]


def _within(text: str, total=None, monthly=None) -> bool:
    """Every dollar amount in `text` is at most `total` (a monthly one at most `monthly`). No limits: any amount."""
    if total is None and monthly is None:
        return True
    if _WORD_AMOUNT.search(text):  # "three hundred": can't check it, so it's not cleared
        return False
    for m in _AMOUNT.finditer(text):
        v = float(m[1].replace(",", ""))
        per_month = _PER_MONTH_AFTER.match(text, m.end()) or _PER_MONTH_BEFORE.search(text[:m.start()])
        cap = monthly if per_month and monthly is not None else total
        if cap is not None and v > cap + SLACK:
            return False
    return True


def cleared_amounts(brief: dict, kind: str, monthly=None, months=None, total=None, **_) -> dict:
    """What an allowed check_offer clears for `screen`: a promise may name these amounts or less."""
    monthly, months, total = _num(monthly), _num(months), _num(total)
    if kind != "payment_plan":
        return {"total": total if total is not None else monthly, "monthly": None}
    if monthly is None:
        monthly = _num((brief.get("proposed_payment_plan") or {}).get("monthly"))
    if total is None:
        total = (monthly * months if monthly is not None and months is not None
                 else _num(brief.get("balance_after_assistance")))
    return {"total": total, "monthly": monthly}


def _limits(allowed) -> dict:
    """`allowed` as {kind: {"total": .., "monthly": ..}}; a plain list of kinds has no amount limits."""
    return dict(allowed) if isinstance(allowed, dict) else {k: {} for k in allowed}


def screen(text: str, brief: dict, allowed=(), heard=()) -> tuple[str, list[dict]]:
    """One spoken sentence (or line), before it goes to TTS. Returns the text to say and what was blocked.
    `allowed` is what check_offer allowed and is still live: offer kinds, or {kind: {"total", "monthly"}} to
    clear only promises within those amounts. `heard` is what the rep said this call (a number they gave may
    be read back). A blocked sentence is replaced whole with a safe line in the code's own words."""
    found: list[tuple[str, str]] = []
    # numbers: an SSN (9 digits) or card (13-19 digits), unless it's in the brief or the rep just said it
    ok = _brief_numbers(brief)
    rep = {r for h in heard if not _PII_WORDS.search(h or "") for r in _runs(h or "")}  # never confirms an SSN
    for run in _runs(_ZIP4.sub(" ", text)):  # a ZIP+4 (94702-1234) is 9 digits but not an SSN (123-45-6789)
        if len(run) != 9 and not 13 <= len(run) <= 19 or any(run in d for d in ok) or run in rep:
            continue
        found.append(("pii", "Would have said what looks like a Social Security number" if len(run) == 9
                      else "Would have said what looks like a card number"))
    if _SSN_PART.search(text):
        found.append(("pii", "Would have said part of a Social Security number"))
    # promises: agreeing, paying, enrolling (unless check_offer allowed it), paying now (never), being human.
    # A negation earlier in the same clause ("I can't agree to pay") or a question ("how much would I pay",
    # "so the patient would pay $100 a month?") is not a promise.
    for sentence in _SENTENCE.split(text):
        question = sentence.rstrip().rstrip("\"')]").endswith("?")
        for clause in _CLAUSE.split(sentence):
            for rule, pat in _COMMIT:
                m = pat.search(clause)
                if not m or _NEG.search(clause[:m.start(1)]) or _ASK.search(clause[:m.start()]):
                    continue
                if question and _MODAL.search(clause[m.start():m.start(1)]):
                    continue
                found.append(("pay_now" if m.group(1).lower().startswith(("pay", "make")) and _NOW.search(clause)
                              else rule, ""))
            for rule, pat in _IMPERATIVE:
                m = pat.search(clause)
                if m and not _NEG.search(" ".join(clause[:m.start()].split()[-4:])):
                    found.append((rule, ""))
    if _NOT_A_BOT.search(text):
        found.append(("human", ""))
    reasons = {"commit": "Would have agreed to pay or to a plan without a check_offer that allowed it",
               "pay_now": "Remy never pays on a call", "human": "Would have claimed to be a person"}
    lim = _limits(allowed)
    cleared = any(_within(text, lim[k].get("total"), lim[k].get("monthly")) for k in UNLOCKS if k in lim)
    # the corrected balance (or less), and the approved plan's monthly (or less), are what the patient approved
    target = _num(brief.get("balance_after_assistance"))
    plan_monthly = _num((brief.get("proposed_payment_plan") or {}).get("monthly")) or 0.0
    corrected = target is not None and bool(_CORRECTED.search(text)) and _within(text, target, plan_monthly)
    blocked = []
    for rule, why in found:
        if rule == "commit" and (cleared or corrected):
            continue
        if rule not in (b["rule"] for b in blocked):
            blocked.append({"rule": rule, "reason": why or reasons[rule], "say": SAY[rule]})
    if not blocked:
        return text, []
    tail = text[len(text.rstrip()):]  # keep the trailing space the sentence chunker leaves for TTS
    return " ".join(dict.fromkeys(b["say"] for b in blocked)) + tail, blocked
