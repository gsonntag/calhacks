"""No-wifi demo (REMY_OFFLINE=1): mocks for the parts of Remy that need a network or a second person.

Everything else already has an offline mode (the sample case, the bank mirror, the committed policy DB, the
scripted caller, the browser's voice). These fill the rest so the whole UI flows through on one laptop:

    play_billing      plays hospital billing on a desk-mode call, so no teammate is needed on rep.html
    demo_utterance    what the patient "says" when the mic hears nothing (no Cartesia, no Chrome speech service)
    bank_income       the Get started bank prefill, from the sample patients' bank histories in demo/
    monthly_income    the "Income by month" line Tiger Data gives online, computed here from the same transactions
    recheck_result    "Recheck now" against the stored copy of the policy (no Bright Data request)

Every mock says it's a mock wherever it shows up, so a demo is never passed off as live."""
import asyncio
import json
import re
from collections import defaultdict
from datetime import date

from . import config, finance
from .events import bus

LABEL = "offline demo"

# Billing's side of the call. Billing answers what the scripted caller (agents/caller.py) just asked: it promises a
# corrected statement for the corrections (the insurance adjustment, a missed payment, a wrong line), opens the
# financial-assistance review and puts the account on hold, gives a case number and confirms the read-back. It
# agrees to nothing on the spot: corrections are promised for later. After that it keeps confirming until the
# caller wraps up. REP_SCRIPT is what billing says on Rosa's call (insurance, payment, hold).
REP_GREETING = "Patient Financial Services, this is Diane. How can I help you?"
REP_LOOKUP = "Okay, let me pull that account up."
REP_CORRECTION = ("I see that. I'll send it to be corrected, and we'll send you a corrected statement once the "
                  "corrections are processed.")
REP_CORRECTION_MORE = "Okay, I've noted that too. It'll be on the corrected statement."
REP_REVIEW = "Okay, I've opened a financial assistance review and put the account on hold."
REP_HOLD = "Okay, I've put the account on hold."
REP_CASE = "Your case number is 58213."
REP_CONFIRM = "Yes, that's right."
REP_OK = "Okay, I see it here."
REP_AFTER = "Okay. Your case number is 58213. Is there anything else?"
REP_SCRIPT = [REP_GREETING, REP_LOOKUP, REP_CORRECTION, REP_CORRECTION_MORE, REP_HOLD, REP_CASE, REP_CONFIRM]


def billing_reply(i: int, remy_said: str, corrections: int = 0) -> str:
    """What billing says to Remy's last line. i: billing's turn number; corrections: how many correction asks
    billing has already answered this call."""
    s = (remy_said or "").lower()
    if i == 0:
        return REP_GREETING
    if i == 1:
        return REP_LOOKUP
    if "read that back" in s:
        return REP_CONFIRM
    if "case or reference number" in s:
        return REP_CASE
    if "open a financial assistance review" in s:
        return REP_REVIEW
    if "on hold" in s or "hold the account" in s:
        return REP_HOLD
    if re.search(r"\b(insurance|eob|payment|line \d+|duplicate|posted price|corrected balance)\b", s):
        return REP_CORRECTION_MORE if corrections else REP_CORRECTION
    if "goodbye" in s or "anything else" in s or re.search(r"\b58213\b", s):
        return REP_AFTER
    return REP_OK


MAX_TURNS = 16
FIRST = 2.5          # seconds before billing picks up
PAUSE = 1.2          # seconds billing "thinks" before answering
READ_SPEED = 0.055   # seconds per character of Remy's last line, so the case page can show (and speak) it


async def play_billing(case: dict, session) -> None:
    """Drive a desk-mode call from billing's side until it ends. Same path as POST /api/calls/{id}/turn."""
    said, corrections = "", 0
    for i in range(MAX_TURNS):
        await asyncio.sleep(PAUSE + min(7.0, len(said) * READ_SPEED) if i else FIRST)
        if session.ended:
            break
        line = billing_reply(i, said, corrections)
        corrections += line in (REP_CORRECTION, REP_CORRECTION_MORE)
        try:
            said = await session.turn(line) or ""
        except Exception as e:
            bus.emit(case["id"], "warning", "caller", text=f"Offline billing mock: turn failed ({type(e).__name__})")
            said = ""
        if session.ended:
            break
    if not session.ended:
        await session._end("hung up")
    case["call_done"].set()


def demo_utterance(case: dict) -> str:
    """The next thing a patient would sensibly say, from where the case stands (agents/talk.py rules)."""
    from .agents import talk
    v = case.get("voice") or {}
    if v.get("pending"):
        return "Yes, go ahead."
    can = talk.options(case)
    if "start_call" in can:
        return "Call them for me."
    last = (v.get("history") or [{}])[-1].get("heard")
    if last == "Where do things stand?":  # after a call: hear the result first, then follow up
        return "Call them again." if "call_again" in can else "What happens next?"
    return "Where do things stand?"


def _sample_cases() -> list[dict]:
    files = [config.DEMO_DIR / "case.json", config.DEMO_DIR / "case_freecare.json",
             *sorted((config.DEMO_DIR / "scenarios").glob("*/case.json"))]
    out = []
    for f in files:
        try:
            out.append(json.loads(f.read_text()))
        except (OSError, json.JSONDecodeError):
            pass
    return out


def bank_income(first: str | None, last: str | None) -> dict | None:
    """Take-home income from a sample patient's bank history (demo/), matched on name. None for anyone else."""
    if not (first and last):
        return None
    for c in _sample_cases():
        p = c.get("patient") or {}
        if (p.get("first_name", "").lower(), p.get("last_name", "").lower()) == (first.lower(), last.lower()):
            today = finance._d(c["today"]) if c.get("today") else _latest(c.get("transactions") or [])
            inc = finance.income(c.get("transactions") or [], today=today)
            if inc["annual"]:
                return {"annual": inc["annual"], "source": f"{inc['source']} ({LABEL}: sample bank)"}
    return None


def _latest(txns: list[dict]) -> date | None:
    days = [finance._d(t["date"]) for t in txns if t.get("date")]
    return max(days) if days else None


def monthly_income(txns: list[dict]) -> list[dict]:
    """Income per calendar month, counted like tiger.monthly_cashflow (payroll + gig/payment-app deposits)."""
    by = defaultdict(float)
    for t in txns:
        text = finance._norm(f"{t.get('description', '')} {t.get('merchant', '')}")
        if t.get("type") == "deposit" and t.get("date") and (re.search(finance.PAYROLL, text)
                                                              or re.search(finance.OTHER_INCOME, text)):
            by[str(t["date"])[:7]] += float(t["amount"])
    return [{"month": m, "income": round(v, 2)} for m, v in sorted(by.items())][-12:]


def recheck_result(row: dict | None) -> dict:
    """A recheck against the stored copy: nothing is fetched, so nothing can have changed."""
    if not row:
        return {"ok": False, "changed": None, "offline": True}
    return {"ok": True, "changed": False, "version": row["version"], "origin": LABEL, "offline": True,
            "tiers": row.get("tiers_verified")}
