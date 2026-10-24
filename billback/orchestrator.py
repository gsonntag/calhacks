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


SCENARIOS = {"default": "case.json", "freecare": "case_freecare.json"}


def demo_case(scenario: str = "default") -> dict:
    return json.loads((config.DEMO_DIR / SCENARIOS.get(scenario, "case.json")).read_text())


def new_case(overrides: dict | None = None, scenario: str = "default") -> dict:
    scenario = scenario if scenario in SCENARIOS else "default"
    base = demo_case(scenario)
    base.update({k: v for k, v in (overrides or {}).items() if v})
    case = {**base, "id": uuid.uuid4().hex[:8], "scenario": scenario, "status": "created", "today": date.today().isoformat(),
            "result": {}, "call_done": asyncio.Event(),
            "call_outcome": {}, "turns_seen": set(), "wh_counts": {}, "wh_turn_ids": set()}
    cases[case["id"]] = case
    return case


def public(case: dict) -> dict:
    return {k: v for k, v in case.items() if k not in ("call_done", "transactions", "turns_seen", "wh_counts", "wh_turn_ids")}


def audit_summary(audit: dict) -> dict:
    rec = audit["reconciliation"]
    note = (auditor._dup_note(rec["duplicates"][0]) if rec["duplicates"]
            else "no errors on the bill")
    return {"result": f"${rec['owed']:,.0f} owed", "note": note}


def policy_summary(pol: dict) -> dict:
    d = pol["eligibility"]["discount_pct"]
    result = (f"Free care → ${pol['target']:,.0f}" if d == 100 else f"{d:.0f}% off → ${pol['target']:,.0f}") if d else "No discount"
    if pol.get("refund"):
        result += f" + ${pol['refund']:,.0f} back"
    return {"result": result,
            "note": "charity-care discount" if d else "income is above the policy's limits"}


def call_asks(audit: dict, pol: dict) -> list[str]:
    rec, d = audit["reconciliation"], pol["eligibility"]["discount_pct"]
    asks = []
    if rec["billed_due"] > rec["patient_responsibility"]:
        asks.append(f"Apply your insurance: the EOB says you owe ${rec['patient_responsibility']:,.0f}, not ${rec['billed_due']:,.0f}")
    if rec["paid"]:
        asks.append(f"Show the ${rec['paid']:,.0f} you already paid, bringing it to ${rec['owed']:,.0f}")
    if d:
        refund = f" and refunding ${pol['refund']:,.0f} you overpaid" if pol.get("refund") else ""
        asks.append(f"Ask for the {'free care' if d == 100 else f'{d:.0f}% charity-care discount'} under their own policy, "
                    f"bringing the bill to ${pol['target']:,.0f}{refund}")
    asks.append("Ask them to pause collections while it's reviewed, and get a case number")
    return asks


def save_replay(case: dict) -> None:
    """Keep the event stream of the latest finished case per scenario for case.html?replay=1 (Wi-Fi insurance)."""
    events = [e for e in bus.history(case["id"]) if e["kind"] not in ("tool_call", "tool_result", "agent_thought")
              or e.get("tool") == "create_payment_plan"]
    (config.DATA_DIR / f"replay_{case.get('scenario', 'default')}.json").write_text(json.dumps(events, default=str))


def record_turn(case: dict, role: str, text: str, n: int = 0) -> None:
    """Webhooks and polling both report turns; emit each (role, text, n) once. n is which repeat of that
    (role, text) this is in the call, so a rep saying "Okay." twice shows twice."""
    key = (role, (text or "").strip(), n)
    if not key[1] or key in case["turns_seen"]:
        return
    case["turns_seen"].add(key)
    bus.emit(case["id"], "transcript", "caller", speaker="billback" if role == "assistant" else "rep", text=text)


def repeat_index(transcript: list, i: int) -> int:
    """How many earlier turns in this transcript have the same (role, text) as turn i."""
    key = (transcript[i].get("role"), (transcript[i].get("text") or "").strip())
    return sum(1 for t in transcript[:i] if (t.get("role"), (t.get("text") or "").strip()) == key)


def record_webhook_turn(case: dict, turn: dict) -> None:
    tid = turn.get("id")
    if tid and tid in case["wh_turn_ids"]:
        return
    if tid:
        case["wh_turn_ids"].add(tid)
    key = (turn.get("role"), (turn.get("text") or "").strip())
    n = case["wh_counts"].get(key, 0)
    case["wh_counts"][key] = n + 1
    record_turn(case, turn.get("role"), turn.get("text", ""), n)


def record_outcome(case: dict, fields: dict) -> None:
    case["call_outcome"].update({k: v for k, v in fields.items() if v not in (None, "") and k != "case_id"})
    bus.emit(case["id"], "call_outcome", "caller", outcome=case["call_outcome"])


def finish_call(case: dict, call: dict | None = None) -> None:
    if case["call_done"].is_set():
        return
    transcript = (call or {}).get("transcript") or []
    for i, t in enumerate(transcript):
        record_turn(case, t.get("role"), t.get("text", ""), repeat_index(transcript, i))
    bus.emit(case["id"], "call_ended", "caller", reason=(call or {}).get("end_reason", ""), outcome=case["call_outcome"])
    case["call_done"].set()


async def _wait_call(case: dict) -> None:
    try:
        await asyncio.wait_for(case["call_done"].wait(), timeout=900)
    except asyncio.TimeoutError:
        bus.emit(case["id"], "warning", "caller", text="Call timed out")


async def _phone_call(case: dict, brief: dict) -> dict:
    name = f"{case['patient']['first_name']} {case['patient']['last_name']}"
    try:
        call_id = await cartesia.place_call(case["id"], name, brief)
    except Exception as e:  # only the dial is covered: never switch modes after a real call started
        bus.emit(case["id"], "warning", "caller", text=f"Phone call failed ({e}); switching to desk mode")
        return await _desk_call(case, brief)
    case["cartesia_call_id"] = call_id
    calls_by_cartesia_id[call_id] = case["id"]
    bus.emit(case["id"], "call_ready", "caller", mode="phone", call_id=call_id, to=config.REP_PHONE_NUMBER[-4:])
    poller = asyncio.create_task(cartesia.poll_call(
        call_id, on_turn=lambda tr, i: record_turn(case, tr[i].get("role"), tr[i].get("text", ""), repeat_index(tr, i)),
        on_done=lambda call: finish_call(case, call),
        on_error=lambda text: bus.emit(case["id"], "warning", "caller", text=text)))
    await _wait_call(case)
    poller.cancel()
    await _fill_outcome(case)
    return case["call_outcome"]


async def _desk_call(case: dict, brief: dict) -> dict:
    session = caller.CallSession(case["id"], brief)
    caller.sessions[session.id] = session
    case["call_id"] = session.id
    bus.emit(case["id"], "call_ready", "caller", mode="desk", call_id=session.id,
             rep_url=f"/rep.html?call={session.id}")
    await _wait_call(case)
    case["call_outcome"].update(session.outcome)
    await _fill_outcome(case)
    return case["call_outcome"]


async def _fill_outcome(case: dict) -> None:
    """If the call agent didn't record a case number and a hold/review, read them from the transcript.
    Only fills missing fields; what the agent recorded during the call wins."""
    o = case["call_outcome"]
    if llm.available() and not (o.get("case_number") and (o.get("account_on_hold") or o.get("assistance_review_opened"))):
        await _extract_outcome(case)


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
        known = case["call_outcome"]
        record_outcome(case, {k: v for k, v in out.items() if not known.get(k)})
    except Exception as e:
        bus.emit(case["id"], "warning", "caller", text=f"Couldn't read the call outcome: {e}")


calls_by_cartesia_id: dict[str, str] = {}


async def end_call(case: dict) -> None:
    """Manual hang-up from the dashboard or rep console, for either call mode."""
    s = caller.sessions.get(case.get("call_id", ""))
    if s:
        if not s.ended:
            await s._end("hung up")
        case["call_done"].set()
        return
    if case.get("cartesia_call_id"):
        try:
            await cartesia.cancel_call(case["cartesia_call_id"])
        except Exception as e:
            bus.emit(case["id"], "warning", "caller", text=f"Couldn't cancel the phone call: {e}")
    finish_call(case)


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
            outcome = await (_phone_call(case, brief) if cartesia.phone_ready() else _desk_call(case, brief))

        case["status"] = "planning"
        balance = target if outcome.get("assistance_review_opened") or outcome.get("account_on_hold") else owed
        if balance != target:
            bus.emit(cid, "balance", amount=balance, label="Still owed")
        plan = await planner.run(case, bank, account_id, balance, pol["income"]["monthly"])

        c = plan.get("created") or {}
        plan_created = bool(c) and not c.get("blocked") and not c.get("error")
        if c.get("error"):
            bus.emit(cid, "warning", "planner", text="Couldn't save the plan to your bank")
        case["status"] = "done"
        case["result"].update({"outcome": outcome, "plan": plan["plan"], "final_balance": balance})
        bus.emit(cid, "case_done", summary={
            "started_at": case["bill"]["amount_due"], "now": balance, "case_number": outcome.get("case_number"),
            "plan": plan["plan"], "plan_created": plan_created,
            "refund": pol.get("refund", 0) if balance == target else 0})
        save_replay(case)
    except Exception as e:
        case["status"] = "error"
        bus.emit(cid, "error", text=f"{type(e).__name__}: {e}")
        raise
