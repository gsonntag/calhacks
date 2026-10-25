"""Memory across calls: what billing promised, when to check back, and answering "what's the status?".

Claude reads each finished call; a deterministic reader (regexes over the rep's lines) fills whatever
Claude didn't return and runs alone offline. Dates are computed here, never generated.
"""
import re
from datetime import date, timedelta

DEFAULT_BUSINESS_DAYS = 5

EXTRACT_SYSTEM = """You read the transcript of a call between BillBack (an assistant calling for a patient) and a
hospital billing representative. Extract only what the representative actually said or agreed to.
Return JSON: {"case_number": string|null, "rep_name": string|null, "account_on_hold": bool,
"assistance_review_opened": bool, "adjustment_applied": bool, "payment_applied": bool, "next_step": string,
"promised_actions": [short imperative phrases for what the HOSPITAL said it will do, e.g. "Send a corrected statement"],
"timeframe_business_days": int|null (business days the rep gave for those actions; "a week" = 5, "a few days" = 3),
"timeframe_text": string|null (the rep's words, e.g. "within 5 business days"),
"follow_up_condition": string|null (lowercase clause that completes "call back if ...", e.g. "the corrected bill hasn't arrived"),
"open_items": [at most 3 short phrases for what is still unresolved], "confirmed_balance": number|null (only if the rep
stated the balance now on the account), "refund_status": string|null (a few words),
"summary": string (one short sentence, max 25 words, naming the rep, e.g. "Diane opened case FA-20931, put the account
on hold and will send a corrected statement within 5 business days."),
"asks": {"<ask id>": {"status": "got"|"refused"|"later"|"unclear", "quote": string}} (one entry per ask in ASKS ON THE
PLAN, if given: "got" = the rep agreed or did it on the call, "refused" = the rep said no, "later" = the rep promised it
for later, "unclear" = not settled. "quote" is the rep's exact words that show it, copied verbatim; empty if unclear)}"""

STATUS_SYSTEM = """You are BillBack, an AI agent that fights hospital bills for a patient. The patient asks about their
case. Answer in the first person ("I called..."), in at most 3 short sentences (under 70 words), using only the CASE
HISTORY JSON. Copy dates exactly as written there (e.g. "Fri Oct 30"); never compute or change a weekday. Mention the
rep's name and case number when known, what billing promised, the balance after corrections, and the next step with
its date. "balance_after_corrections" is what the bill should come down to once billing applies everything; only call it
confirmed if a call has balance_confirmed_by_rep. If the next step is overdue, say so and offer to call again.
Never invent facts, amounts or dates. Plain text, no markdown."""

_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
        "ten": 10, "a couple of": 2, "a couple": 2, "a few": 3, "several": 5, "a": 1}
_NUM_RE = r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten|a couple of|a couple|a few|several|a)"
_SPAN = _NUM_RE + r"\s*(?:(?:-|to)\s*\w+\s*)?(business|working)?\s*(day|week)s?\b"  # "7-10 business days"

PROMISES = [  # (pattern in the rep's words, promised action, condition to check before calling back)
    (r"\b(corrected|revised|updated|new)\s+(bill|statement|invoice)|re-?bill|send .*statement",
     "Send a corrected statement", "the corrected bill hasn't arrived"),
    (r"\brefund", "Process the refund", "the refund hasn't come through"),
    (r"\b(financial assistance|charity|assistance) (review|application)|\b(opened|started|submitted) (a |the )?(\w+ )?review",
     "Complete the financial-assistance review", "the assistance decision hasn't come through"),
    (r"\b(letter|in writing|mail|email)\b", "Send the decision in writing", "the letter hasn't arrived"),
    (r"\bcall (you|her|him|them) back\b|\bget back to\b", "Call back with an update", "billing hasn't called back"),
]


def add_business_days(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def fmt(d: str | date | None) -> str:
    """'Fri Oct 30'."""
    if not d:
        return ""
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return f"{d:%a} {d:%b} {d.day}"


def business_days(text: str) -> int | None:
    t = text.lower()
    m = re.search(_SPAN, t)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _NUM[m.group(1)]
        return n * 5 if m.group(3) == "week" else n
    if re.search(r"\bnext week\b", t):
        return 5
    if re.search(r"\b(tomorrow|next business day)\b", t):
        return 1
    return None


def _rep_name(lines: list[str]) -> str | None:
    for line in lines:
        m = re.search(r"\b(?:this is|my name is|name's|i'm|speaking with)\s+([A-Z][a-z]+)\b", line)
        if m and m.group(1).lower() not in {"calling", "sorry", "not", "the", "just", "happy", "here", "going"}:
            return m.group(1)
    return None


def _case_number(lines: list[str]) -> str | None:
    for line in reversed(lines):
        m = re.search(r"\b(?:case|reference|ref|ticket|confirmation)\b(?:\s+(?:number|no\.?|#))?(?:\s+is)?[:\s#]*"
                      r"((?:[A-Za-z]{1,4}[ -]?)?\d[\d-]{2,})", line, re.I)
        if m:
            return re.sub(r"(?<=[A-Za-z]) (?=\d)", "", m.group(1)).upper()
    return None


def fallback_extract(rep_lines: list[str]) -> dict:
    """Offline reader of the rep's side of the call."""
    text = " ".join(rep_lines)
    low = text.lower()
    promised, conds = [], []
    for pat, action, cond in PROMISES:
        if re.search(pat, low) and action not in promised:
            promised.append(action)
            conds.append(cond)
    days = business_days(text)
    tf = re.search(r"((?:with)?in\s+)?" + _SPAN, low)
    return {"rep_name": _rep_name(rep_lines), "case_number": _case_number(rep_lines),
            "account_on_hold": bool(re.search(r"\b(on hold|hold on the account|paus\w+ collections)\b", low)),
            "assistance_review_opened": bool(re.search(r"\b(opened|open|started|submitted)\b.*\breview\b", low)),
            "promised_actions": promised, "follow_up_condition": conds[0] if conds else None,
            "timeframe_business_days": days, "timeframe_text": tf.group(0).strip() if tf else None}


def merge(primary: dict, fallback: dict) -> dict:
    """Claude's fields win; the deterministic reading fills what Claude left empty."""
    out = dict(fallback)
    out.update({k: v for k, v in primary.items() if v not in (None, "", [])})
    return out


def build_call(n: int, kind: str, call_date: date, mode: str, info: dict, outcome: dict) -> dict:
    """One entry in the case's call timeline, with the follow-up date derived from what was promised."""
    promised = info.get("promised_actions") or []
    if not promised and (outcome.get("assistance_review_opened") or info.get("assistance_review_opened")):
        promised = ["Complete the financial-assistance review"]
    days = info.get("timeframe_business_days")
    assumed = not isinstance(days, int) or days <= 0
    days = DEFAULT_BUSINESS_DAYS if assumed else min(days, 60)
    rep, num = info.get("rep_name") or outcome.get("rep_name"), outcome.get("case_number") or info.get("case_number")
    summary = info.get("summary") or _summary(rep, num, promised, outcome)
    return {"n": n, "kind": kind, "date": call_date.isoformat(), "mode": mode, "rep": rep, "case_number": num,
            "promised": promised, "timeframe_text": info.get("timeframe_text"), "business_days": days,
            "timeframe_assumed": assumed, "follow_up_date": add_business_days(call_date, days).isoformat(),
            "follow_up_condition": _clause(info.get("follow_up_condition"))
            or ("the corrected bill hasn't arrived" if promised else None),
            "open_items": info.get("open_items") or [], "confirmed_balance": info.get("confirmed_balance"),
            "refund_status": info.get("refund_status"), "outcome": dict(outcome), "summary": summary}


def _clause(s: str | None) -> str | None:
    """'The bill has not arrived.' -> 'the bill has not arrived' (it's read after "if")."""
    s = (s or "").strip().rstrip(".")
    return s[:1].lower() + s[1:] if s else None


def _summary(rep: str | None, num: str | None, promised: list[str], outcome: dict) -> str:
    bits = []
    if num:
        bits.append(f"opened case {num}")
    if outcome.get("account_on_hold"):
        bits.append("put the account on hold")
    if promised:
        bits.append("will " + " and ".join(p[0].lower() + p[1:] for p in promised))
    return f"{rep or 'Billing'} " + (", ".join(bits) if bits else "took the details") + "."


def followup_for(call: dict, hospital: str) -> dict | None:
    if not call["promised"] and not call["open_items"]:
        return None
    cond = call["follow_up_condition"] or "they haven't followed through"
    return {"id": f"f{call['n']}", "from_call": call["n"], "created": call["date"], "due": call["follow_up_date"],
            "check": call["promised"] or call["open_items"], "condition": cond,
            "action": f"Call {hospital} back if {cond}", "status": "open"}


def followup_goals(case: dict) -> list[str]:
    calls, res = case.get("calls") or [], case.get("result") or {}
    last = calls[-1] if calls else {}
    num = next((c["case_number"] for c in reversed(calls) if c.get("case_number")), None)
    ref = f" (case {num})" if num else ""
    goals = [f"Say you're following up on the call on {fmt(last.get('date'))}"
             + (f" with {last['rep']}" if last.get("rep") else "") + f"{ref}; give the account number."]
    for p in last.get("promised") or []:
        goals.append(f"Ask whether this was done: {p}.")
    if res.get("target") is not None:
        goals.append(f"Confirm the balance now on the account is ${res['target']:,.0f} (the write-off/adjustment was applied).")
    refund = (res.get("brief") or {}).get("refund_due_after_assistance")
    if refund:
        goals.append(f"Ask for the status of the ${refund:,.0f} refund and when it will be issued.")
    goals.append("Make sure the account stays on hold from collections until the corrected bill is issued.")
    goals.append("Get a date for anything still pending and the rep's name.")
    return goals


def followup_asks(case: dict) -> list[str]:
    calls, res = case.get("calls") or [], case.get("result") or {}
    num = next((c["case_number"] for c in reversed(calls) if c.get("case_number")), None)
    asks = [f"Check that the balance was corrected to ${res.get('target', 0):,.0f}" + (f" under case {num}" if num else "")]
    refund = (res.get("brief") or {}).get("refund_due_after_assistance")
    if refund:
        asks.append(f"Ask when your ${refund:,.0f} refund will be issued")
    asks.append("Keep the account on hold until the corrected bill is out")
    return asks


def previous_calls(case: dict) -> list[dict]:
    return [{"date": c["date"], "rep": c.get("rep"), "case_number": c.get("case_number"),
             "promised": c.get("promised") or [], "outcome": c.get("summary")} for c in case.get("calls") or []]


def fallback_status(ctx: dict) -> str:
    """Deterministic answer to "what's the status?" from the stored history."""
    calls, nxt, hosp = ctx["calls"], ctx.get("next") or {}, ctx["hospital"]
    money = lambda n: f"${n:,.0f}"
    if not calls:
        return {"awaiting_call_approval": f"I've reviewed your bill and I'm ready to call {hosp} billing; I just need your OK.",
                "calling": f"I'm on the phone with {hosp} billing right now.",
                "reviewing": "I'm reviewing your bill against your bank records and the hospital's policy right now."
                }.get(ctx["status"], f"I haven't called {hosp} yet. Your balance is {money(ctx['current_balance'])}.")
    parts = []
    for c in calls[-2:]:
        who = f"{c['rep']} " if c.get("rep") else "billing "
        did = [f"opened case {c['case_number']}"] if c.get("case_number") and c["n"] == 1 else []
        if c.get("promised"):
            p = " and ".join(x[0].lower() + x[1:] for x in c["promised"])
            did.append(f"said they'd {p}" + (f" {c['timeframe_text']}" if c.get("timeframe_text") else ""))
        if c.get("confirmed_balance") is not None:
            did.append(f"confirmed the balance is now {money(c['confirmed_balance'])}")
        verb = "called back" if c["kind"] == "followup" else "called"
        parts.append(f"I {verb} {hosp} on {fmt(c['date'])}; {who}" + (", ".join(did) if did else "took the details") + ".")
    if ctx.get("target") is not None:
        bal = f"Your balance should go from {money(ctx['original'])} to {money(ctx['target'])}"
        if ctx.get("refund"):
            bal += f", and you're owed {money(ctx['refund'])} back"
        parts.append(bal + ".")
    if nxt.get("due"):
        if nxt.get("overdue"):
            parts.append(f"I planned to check back by {fmt(nxt['due'])} and that's now overdue — want me to call again?")
        else:
            parts.append(f"I'll call back {fmt(nxt['due'])} if {nxt.get('condition') or 'nothing has changed'}.")
    return " ".join(parts)


STATUS_RE = re.compile(r"\b(status|update|updates|news|progress|follow.?up|where are we|what happened|"
                       r"did you (call|hear)|any word|heard back|what's going on|how's my (case|bill))\b", re.I)
CASE_Q_RE = re.compile(r"\b(did they|owe|balance|refund|case number|call(ed)? back|next step|when)\b", re.I)


def is_status_question(text: str, in_case: bool = False) -> bool:
    """in_case: the chat already has a case, so questions about the balance or refund are about it too
    (outside a case, "the balance is $8,700" is someone describing a new bill)."""
    return bool(STATUS_RE.search(text or "") or (in_case and CASE_Q_RE.search(text or "")))
