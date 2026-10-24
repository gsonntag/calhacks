"""Runs a case end to end: intake -> auditor -> policy -> (approve) call -> (approve) plan.

The pipeline order is fixed on purpose (predictable on stage); the agents inside each step are
Claude-driven where judgement is needed and deterministic where money is computed.
"""
import asyncio
import json
import uuid
from datetime import date

from . import config, finance, llm
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


def audit_summary(audit: dict) -> dict:
    rec = audit["reconciliation"]
    note = (f"duplicate ${rec['duplicates'][0]['amount']:,.2f} charge flagged" if rec["duplicates"]
            else "no errors on the bill")
    return {"result": f"${rec['owed']:,.0f} owed", "note": note}


def policy_summary(pol: dict) -> dict:
    d = pol["eligibility"]["discount_pct"]
    return {"result": "Free care → $0" if d == 100 else f"{d:.0f}% off → ${pol['target']:,.0f}" if d else "No discount",
            "note": "charity-care discount" if d else "income is above the policy's limits"}


def call_asks(audit: dict, pol: dict) -> list[str]:
    rec, d = audit["reconciliation"], pol["eligibility"]["discount_pct"]
    asks = [f"Apply your insurance: the EOB says you owe ${rec['patient_responsibility']:,.0f}, not ${rec['billed_due']:,.0f}"]
    if rec["paid"]:
        asks.append(f"Show the ${rec['paid']:,.0f} you already paid, bringing it to ${rec['owed']:,.0f}")
    if d:
        asks.append(f"Ask for the {'free care' if d == 100 else f'{d:.0f}% charity-care discount'} under their own policy, "
                    f"bringing the bill to ${pol['target']:,.0f}")
    asks.append("Ask them to pause collections while it's reviewed, and get a case number")
    return asks


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
    if not case["call_outcome"].get("case_number") and llm.available():
        await _extract_outcome(case)
    return case["call_outcome"]


OUTCOME_SYSTEM = """You read the transcript of a call between BillBack (an assistant calling for a patient) and a
hospital billing representative. Extract only what the representative actually agreed to or said.
Return JSON: {"case_number": string|null, "rep_name": string|null, "account_on_hold": bool,
"assistance_review_opened": bool, "adjustment_applied": bool, "payment_applied": bool, "next_step": string}"""


async def _extract_outcome(case: dict) -> None:
    """Polling mode has no mid-call outcome tool, so Claude reads the finished transcript instead."""
    lines = [f"{'BillBack' if e['speaker'] == 'billback' else 'Billing'}: {e['text']}"
             for e in bus.history(case["id"]) if e["kind"] == "transcript"]
    if not lines:
        return
    try:
        out = await llm.json_out(OUTCOME_SYSTEM, "\n".join(lines), model=config.MODEL_FAST)
        record_outcome(case, out)
    except Exception as e:
        bus.emit(case["id"], "warning", "caller", text=f"Couldn't read the call outcome: {e}")


calls_by_cartesia_id: dict[str, str] = {}


async def run_case(case: dict) -> None:
    cid = case["id"]
    try:
        bank, account_id, bank_label = get_bank(case)
        bus.emit(cid, "case_started", modes=config.mode(), bank=bank_label, documents=case.get("documents", []),
                 bill={k: case["bill"][k] for k in ("provider", "amount_due", "notice", "due_date")})
        bus.emit(cid, "balance", amount=case["bill"]["amount_due"], label="Hospital says")

        case["status"] = "reviewing"
        loop = asyncio.get_running_loop()
        txns_ready, owed_ready = loop.create_future(), loop.create_future()
        audit, pol = await asyncio.gather(
            auditor.run(case, bank, account_id, bank_label, txns_ready, owed_ready),
            policy.run(case, txns_ready, owed_ready))

        owed, target = audit["reconciliation"]["owed"], pol["target"]
        if target != owed:
            bus.emit(cid, "balance", amount=target, label="With the hospital's own discount")
        brief = caller.build_brief(case, audit, pol)
        case["result"].update({"owed": owed, "target": target, "brief": brief})
        bus.emit(cid, "review_done", auditor=audit_summary(audit), policy=policy_summary(pol))

        case["status"] = "awaiting_call_approval"
        ok = await gate.request(cid, "caller", f"Call {case['hospital']['name']} billing for you?", {
            "asks": call_asks(audit, pol),
            "say": "I won't agree to pay anything on the call. Any payment comes back to you first."})
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
        plan = await planner.run(case, bank, account_id, balance, pol["income"]["monthly"])

        case["status"] = "done"
        case["result"].update({"outcome": outcome, "plan": plan["plan"], "final_balance": balance})
        bus.emit(cid, "case_done", summary={
            "started_at": case["bill"]["amount_due"], "now": balance, "case_number": outcome.get("case_number"),
            "plan": plan["plan"], "plan_created": bool(plan.get("created") and not plan["created"].get("blocked"))})
    except Exception as e:
        case["status"] = "error"
        bus.emit(cid, "error", text=f"{type(e).__name__}: {e}")
        raise
