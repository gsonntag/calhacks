"""Runs a case end to end: intake -> auditor -> policy -> (approve) call -> (approve) plan.

The pipeline order is fixed on purpose (predictable on stage); the agents inside each step are
Claude-driven where judgement is needed and deterministic where money is computed.
"""
import asyncio
import json
import uuid
from datetime import date

from . import config, finance
from .agents import auditor, caller, planner, policy
from .events import bus
from .harness import gate
from .integrations import cartesia
from .integrations.nessie import get_bank

cases: dict[str, dict] = {}


def demo_case() -> dict:
    return json.loads((config.DEMO_DIR / "case.json").read_text())


def new_case(overrides: dict | None = None) -> dict:
    base = demo_case()
    base.update({k: v for k, v in (overrides or {}).items() if v})
    case = {**base, "id": uuid.uuid4().hex[:8], "status": "created", "today": date.today().isoformat(),
            "result": {}, "call_done": asyncio.Event(),
            "call_outcome": {}, "turns_seen": set()}
    cases[case["id"]] = case
    return case


def public(case: dict) -> dict:
    return {k: v for k, v in case.items() if k not in ("call_done", "transactions", "turns_seen")}


def record_turn(case: dict, role: str, text: str) -> None:
    """Webhooks and polling both report turns; emit each (role, text) once."""
    key = (role, text.strip())
    if not text.strip() or key in case["turns_seen"]:
        return
    case["turns_seen"].add(key)
    bus.emit(case["id"], "transcript", "caller", speaker="billback" if role == "assistant" else "rep", text=text)


def record_outcome(case: dict, fields: dict) -> None:
    case["call_outcome"].update({k: v for k, v in fields.items() if v not in (None, "") and k != "case_id"})
    bus.emit(case["id"], "call_outcome", "caller", outcome=case["call_outcome"])


def finish_call(case: dict, call: dict | None = None) -> None:
    if case["call_done"].is_set():
        return
    for t in (call or {}).get("transcript") or []:
        record_turn(case, t.get("role"), t.get("text", ""))
    bus.emit(case["id"], "call_ended", "caller", reason=(call or {}).get("end_reason", ""), outcome=case["call_outcome"])
    case["call_done"].set()


async def _wait_call(case: dict) -> None:
    try:
        await asyncio.wait_for(case["call_done"].wait(), timeout=900)
    except asyncio.TimeoutError:
        bus.emit(case["id"], "warning", "caller", text="Call timed out")


async def _phone_call(case: dict, brief: dict) -> dict:
    name = f"{case['patient']['first_name']} {case['patient']['last_name']}"
    call_id = await cartesia.place_call(case["id"], name, brief)
    case["cartesia_call_id"] = call_id
    calls_by_cartesia_id[call_id] = case["id"]
    bus.emit(case["id"], "call_ready", "caller", mode="phone", call_id=call_id, to=config.REP_PHONE_NUMBER[-4:])
    poller = asyncio.create_task(cartesia.poll_call(
        call_id, on_turn=lambda i, role, text: record_turn(case, role, text),
        on_done=lambda call: finish_call(case, call)))
    await _wait_call(case)
    poller.cancel()
    return case["call_outcome"]


calls_by_cartesia_id: dict[str, str] = {}


async def run_case(case: dict) -> None:
    cid = case["id"]
    try:
        bank, account_id, bank_label = get_bank(case)
        bus.emit(cid, "case_started", modes=config.mode(), bank=bank_label,
                 bill={k: case["bill"][k] for k in ("provider", "amount_due", "notice", "due_date")})
        bus.emit(cid, "balance", amount=case["bill"]["amount_due"], label="Hospital says")

        case["status"] = "auditing"
        audit = await auditor.run(case, bank, account_id, bank_label)
        case["status"] = "checking_policy"
        pol = await policy.run(case, audit["income"]["annual"])

        owed = audit["reconciliation"]["owed"]
        target = finance.apply_discount(owed, pol["eligibility"]["discount_pct"])
        if target != owed:
            bus.emit(cid, "balance", amount=target, label="With the hospital's own discount")
        brief = caller.build_brief(case, audit, pol)
        case["result"].update({"owed": owed, "target": target, "brief": brief})

        case["status"] = "awaiting_call_approval"
        ok = await gate.request(cid, "caller", f"Call {case['hospital']['name']} billing for you?", {
            "say": f"I'll ask them to fix the balance to ${owed:,.2f}, open a financial-assistance review "
                   f"(you qualify for {pol['eligibility']['discount_pct']}% off), and hold collections. "
                   "I won't agree to pay anything on the call."})
        outcome: dict = {}
        if ok:
            case["status"] = "calling"
            if cartesia.phone_ready():
                outcome = await _phone_call(case, brief)
            else:
                session = caller.CallSession(cid, brief)
                caller.sessions[session.id] = session
                case["call_id"] = session.id
                bus.emit(cid, "call_ready", "caller", mode="desk", call_id=session.id,
                         rep_url=f"{config.PUBLIC_BASE_URL}/rep.html?call={session.id}")
                await _wait_call(case)
                outcome = session.outcome

        case["status"] = "planning"
        balance = target if outcome.get("assistance_review_opened") or outcome.get("account_on_hold") else owed
        plan = await planner.run(case, bank, account_id, balance, audit["income"]["monthly"])

        case["status"] = "done"
        case["result"].update({"outcome": outcome, "plan": plan["plan"], "final_balance": balance})
        bus.emit(cid, "case_done", summary={
            "started_at": case["bill"]["amount_due"], "now": balance, "case_number": outcome.get("case_number"),
            "plan": plan["plan"], "plan_created": bool(plan.get("created") and not plan["created"].get("blocked"))})
    except Exception as e:
        case["status"] = "error"
        bus.emit(cid, "error", text=f"{type(e).__name__}: {e}")
        raise
