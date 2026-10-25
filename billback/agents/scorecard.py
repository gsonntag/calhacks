"""Step 5: the scorecard. After every call, each ask on the plan the call used gets one mark:

  got        billing agreed to it or did it
  refused    billing said no
  later      billing promised it for later (a review, a corrected statement, a refund)
  unclear    the call didn't settle it
  not_needed only for "ask for a supervisor": nothing was refused

Plain code decides the mark. It reads what the call agent recorded during the call (record_outcome), what
the rep said, and, when Claude is on, Claude's reading of the transcript (followups.EXTRACT_SYSTEM's "asks").
Claude's mark is only used when its quote is really in the rep's words; otherwise the ask stays unclear.
A win is never claimed without evidence.
"""
import re

STATUSES = ("got", "refused", "later", "unclear", "not_needed")
LABELS = {"got": "Got it", "refused": "Refused", "later": "Promised later", "unclear": "No clear answer",
          "not_needed": "Not needed"}

GOT = {  # kind -> pattern in the rep's words that means it was done
    "insurance": r"\b(adjustment|insurance)\b.{0,30}\b(is|was|has been|been)\s+(on there|on the account|applied|posted|made|processed)\b"
                 r"|\b(applied|posted)\s+the\s+(insurance|adjustment)\b",
    "payment": r"\bpayment\b.{0,30}\b(is|was|has been|been)\s+(applied|posted|credited|on there|on the account)\b"
               r"|\b(applied|posted|credited)\s+(your|the|that)\s+payment\b",
    "plan": r"\b(payment )?plan\b.{0,60}\b(set up|noted|approved|in place|works|is fine|done)\b"
            r"|\b(set up|noted|approved)\b.{0,30}\bplan\b",
}
PROMISE = {  # kind -> promised action (followups.PROMISES) that means it's coming later
    "insurance": "Send a corrected statement",
    "payment": "Send a corrected statement",
    "discount": "Complete the financial-assistance review",
    "refund": "Process the refund",
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9$]+", " ", (s or "").lower()).strip()


def _quoted(quote: str, rep_text: str) -> bool:
    """The quote is the rep's own words (case, punctuation and spacing don't matter)."""
    q = _norm(quote)
    return len(q) >= 4 and q in _norm(rep_text)


NO = r"\b(can'?t|cannot|won'?t|unable|not|no|don'?t|isn'?t|wasn'?t|haven'?t|hasn'?t)\b"


def _rep_line(pattern: str, rep_lines: list[str]) -> str | None:
    """The first rep line that says it, unless that line says no ("I can't set up a plan")."""
    return next((l for l in rep_lines if re.search(pattern, l, re.I) and not re.search(NO, l, re.I)), None)


def from_call(ask: dict, outcome: dict, info: dict, rep_lines: list[str], money: dict) -> tuple[str, str, str]:
    """(status, evidence, source) from what the call agent recorded and the rep's words; no Claude."""
    kind, low = ask["kind"], " ".join(rep_lines).lower()
    confirmed = info.get("confirmed_balance")
    promised = info.get("promised_actions") or []
    if kind == "hold" and outcome.get("account_on_hold"):
        return "got", "Account put on hold", "call record"
    if kind == "case_number" and outcome.get("case_number"):
        return "got", f"Case {outcome['case_number']}" + (f" from {outcome['rep_name']}" if outcome.get("rep_name") else ""), "call record"
    if kind == "insurance" and outcome.get("adjustment_applied"):
        return "got", "Insurance adjustment applied", "call record"
    if kind == "payment" and outcome.get("payment_applied"):
        return "got", "Your payment was applied", "call record"
    if isinstance(confirmed, (int, float)):
        limit = money["target"] if kind in ("discount",) else money["owed"] if kind in ("insurance", "payment") else None
        if limit is not None and confirmed <= limit + 0.5:
            return "got", f"Billing confirmed the balance is ${confirmed:,.0f}", "rep said"
    if kind in GOT:
        line = _rep_line(GOT[kind], rep_lines)
        if line:
            return "got", line, "rep said"
    if kind == "refund" and re.search(r"\b(issued|sent|processed|mailed)\b", info.get("refund_status") or "") \
            and not re.search(r"\b(will|going to|within|in \w+ (days?|weeks?))\b", info.get("refund_status") or ""):
        return "got", f"Refund: {info['refund_status']}", "rep said"
    if PROMISE.get(kind) in promised:
        when = f" ({info['timeframe_text']})" if info.get("timeframe_text") else ""
        return "later", f"They said they'll {PROMISE[kind][0].lower() + PROMISE[kind][1:]}{when}", "rep said"
    if kind in ("discount", "refund") and (outcome.get("assistance_review_opened") or info.get("assistance_review_opened")):
        what = "The financial-assistance review is open" + ("; the refund comes once it's applied" if kind == "refund" else "")
        return "later", what, "call record"
    if kind == "hold" and re.search(r"\b(on hold|paus\w+ collections)\b", low):
        return "got", _rep_line(r"\b(on hold|paus\w+ collections)\b", rep_lines) or "Account on hold", "rep said"
    return "unclear", "Not settled on the call", ""


def score(asks: list[dict], outcome: dict, info: dict, rep_lines: list[str], money: dict) -> list[dict]:
    """One mark per ask, in the plan's order. Order of trust: what the call recorded or the rep plainly said
    ("got") > Claude's reading, when its quote is in the rep's words > a promise for later > unclear."""
    rep_text = " ".join(rep_lines)
    claude = info.get("asks") if isinstance(info.get("asks"), dict) else {}
    out = []
    for a in asks:
        status, evidence, source = from_call(a, outcome, info, rep_lines, money)
        c = claude.get(a["id"]) if isinstance(claude.get(a["id"]), dict) else {}
        if status != "got" and c.get("status") in ("got", "refused", "later") and _quoted(c.get("quote", ""), rep_text):
            status, evidence, source = c["status"], c["quote"].strip(), "rep said"
        out.append({"id": a["id"], "kind": a["kind"], "text": a["text"], "status": status, "label": LABELS[status],
                    "evidence": evidence, "source": source})
    refused = any(x["status"] == "refused" for x in out)
    for x in out:
        if x["kind"] == "supervisor" and x["status"] == "unclear" and not refused:
            x.update(status="not_needed", label=LABELS["not_needed"], evidence="Nothing was refused", source="")
    return out


def tally(marks: list[dict]) -> dict:
    return {s: sum(1 for m in marks if m["status"] == s) for s in STATUSES}


def summary(marks: list[dict]) -> str:
    """'3 got · 1 promised later · 1 unclear'."""
    t = tally(marks)
    names = {"got": "got", "refused": "refused", "later": "promised later", "unclear": "unclear"}
    return " · ".join(f"{t[s]} {names[s]}" for s in names if t[s]) or "nothing to mark"
