"""Runs a case end to end: intake -> auditor -> policy -> (approve) call -> (approve) plan,
then follow-ups: each call is logged to the case timeline with what billing promised and when to check back,
and "Call again" runs a follow-up call (same approval gate, same call paths) with the earlier calls in the brief.

The pipeline order is fixed on purpose (predictable on stage); the agents inside each step are
Claude-driven where judgement is needed and deterministic where money is computed.
"""
import asyncio
import json
import time
import uuid
from datetime import date, timedelta

from . import config, events, finance, followups, llm, voice
from .agents import auditor, caller, planner, policy
from .events import bus
from .harness import gate
from .integrations import cartesia
from .integrations.nessie import get_bank

cases: dict[str, dict] = {}
RUNTIME = ("call_done", "turns_seen", "wh_counts", "wh_turn_ids")
ACTIVE = ("created", "reviewing", "awaiting_call_approval", "calling", "planning")


SCENARIOS = {"default": "case.json", "freecare": "case_freecare.json"}


def demo_case(scenario: str = "default") -> dict:
    return json.loads((config.DEMO_DIR / SCENARIOS.get(scenario, "case.json")).read_text())


def _blank(v):
    """Same shape as the sample case, no values: a live case is filled only from the patient's documents."""
    if isinstance(v, dict):
        return {k: _blank(x) for k, x in v.items()}
    return [] if isinstance(v, list) else 0.0 if isinstance(v, float) else ""


def blank_case() -> dict:
    base = {k: _blank(v) for k, v in demo_case().items() if k in ("patient", "hospital", "bill", "eob")}
    base["patient"]["household_size"] = 1  # not on a bill or EOB; the patient can correct it
    return base


def new_case(overrides: dict | None = None, scenario: str = "default", from_documents: bool = False) -> dict:
    """from_documents: live mode. The case starts empty and is filled from the uploaded bill/EOB (intake);
    bank history comes from Nessie and the policy from the policy DB. Otherwise (offline/mock) it starts
    from the sample case."""
    scenario = scenario if scenario in SCENARIOS else "default"
    base = blank_case() if from_documents else demo_case(scenario)
    base.update({k: v for k, v in (overrides or {}).items() if v})
    case = {**base, "id": uuid.uuid4().hex[:8], "scenario": scenario, "status": "created", "today": date.today().isoformat(),
            "created_at": time.time(), "result": {}, "call_outcome": {}, "calls": [], "followups": [], "clock_days": 0}
    _reset_call_state(case)
    cases[case["id"]] = case
    save(case)
    return case


def _reset_call_state(case: dict) -> None:
    case.update({"call_done": asyncio.Event(), "call_outcome": {}, "turns_seen": set(), "wh_counts": {},
                 "wh_turn_ids": set(), "call_started_ts": time.time()})


def public(case: dict) -> dict:
    return {k: v for k, v in case.items() if k not in RUNTIME and k != "transactions"}


def save(case: dict) -> None:
    events.save_case(case["id"], {k: v for k, v in case.items() if k not in RUNTIME})


def set_status(case: dict, status: str) -> None:
    case["status"] = status
    save(case)


def load_cases() -> int:
    """Startup: bring back persisted cases and their event history. A run cut off by the restart can't
    resume mid-step, so it's marked interrupted (and can still be called again if the review finished)."""
    n = 0
    for c in events.load_cases():
        if not c.get("id") or c["id"] in cases:
            continue
        for k, v in (("calls", []), ("followups", []), ("clock_days", 0), ("result", {})):
            c.setdefault(k, v)
        call_outcome = c.get("call_outcome") or {}
        _reset_call_state(c)
        c["call_outcome"] = call_outcome
        c["call_done"].set()
        if c.get("status") in ACTIVE:
            c["status"] = "interrupted"
        cases[c["id"]] = c
        bus.restore(c["id"])
        n += 1
    return n


def case_now(case: dict) -> date:
    """The case clock: today plus any simulated days (demo control on the dashboard)."""
    return date.today() + timedelta(days=case.get("clock_days", 0))


def advance_clock(case: dict, days: int | None) -> None:
    case["clock_days"] = 0 if days is None else max(0, case.get("clock_days", 0) + days)
    save(case)
    bus.emit(case["id"], "clock", today=case_now(case).isoformat(), clock_days=case["clock_days"])


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
    plan = pol.get("plan") or {}
    if not d and plan.get("months"):
        result = f"${pol['target']:,.0f} · {plan['months']} × ${plan['monthly']:,.0f}/mo, 0% interest"
    return {"result": result,
            "note": "charity-care discount" if d else "no discount tier fits; interest-free plan under their policy"
            if plan.get("months") else "income is above the policy's limits"}


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
    plan = pol.get("plan") or {}
    if plan.get("months") and pol["target"] > 0:
        asks.append(f"Set up an interest-free payment plan for the ${pol['target']:,.0f}: "
                    f"{plan['months']} × ${plan['monthly']:,.0f}/month, as their own billing policy allows")
    asks.append("Ask them to pause collections while it's corrected, and get a case number")
    return asks


def save_replay(case: dict) -> None:
    """Keep the event stream of the latest finished case per scenario for case.html?replay=1 (Wi-Fi insurance)."""
    evs = [e for e in bus.history(case["id"]) if e["kind"] not in ("tool_call", "tool_result", "agent_thought")
           or e.get("tool") == "create_payment_plan"]
    (events.DATA_DIR / f"replay_{case.get('scenario', 'default')}.json").write_text(json.dumps(evs, default=str))


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
    if voice.twilio_ready():  # our own pipeline: Twilio + Cartesia STT/TTS + the Claude call brain
        try:
            await voice.run_twilio_call(case, brief)
        except voice.DialError as e:  # nothing rang: safe to fall back
            bus.emit(case["id"], "warning", "caller", text=f"Phone call failed ({e}); switching to desk mode")
            return await _desk_call(case, brief)
        await _fill_outcome(case, "phone")
        return case["call_outcome"]
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
    await _fill_outcome(case, "phone")
    return case["call_outcome"]


async def _desk_call(case: dict, brief: dict) -> dict:
    session = caller.CallSession(case["id"], brief)
    caller.sessions[session.id] = session
    case["call_id"] = session.id
    bus.emit(case["id"], "call_ready", "caller", mode="desk", call_id=session.id,
             rep_url=f"/rep.html?call={session.id}")
    await _wait_call(case)
    case["call_outcome"].update(session.outcome)
    await _fill_outcome(case, "desk")
    return case["call_outcome"]


def _call_transcript(case: dict) -> list[dict]:
    """This call's turns only (a case can have several calls)."""
    return [e for e in bus.history(case["id"]) if e["kind"] == "transcript" and e["ts"] >= case.get("call_started_ts", 0)]


async def _fill_outcome(case: dict, mode: str) -> None:
    """After every call (phone or desk): read the transcript for what billing promised and when, fill any
    outcome fields the call agent didn't record (what it recorded during the call wins), and log the call."""
    info = await _extract_outcome(case)
    _log_call(case, mode, info)


OUTCOME_KEYS = ("case_number", "rep_name", "account_on_hold", "assistance_review_opened", "adjustment_applied",
                "payment_applied", "next_step")


async def _extract_outcome(case: dict) -> dict:
    """Claude reads the finished transcript (polling mode has no mid-call outcome tool); the deterministic
    reader fills whatever Claude didn't return, and runs alone when Claude is unavailable."""
    turns = _call_transcript(case)
    fallback = followups.fallback_extract([e["text"] for e in turns if e["speaker"] == "rep"])
    out: dict = {}
    if turns and llm.available():
        lines = [f"{'BillBack' if e['speaker'] == 'billback' else 'Billing'}: {e['text']}" for e in turns]
        try:
            out = await llm.json_out(followups.EXTRACT_SYSTEM, "\n".join(lines), model=config.MODEL_FAST)
        except Exception as e:
            bus.emit(case["id"], "warning", "caller", text=f"Couldn't read the call outcome: {e}")
    info = followups.merge(out if isinstance(out, dict) else {}, fallback)
    known = case["call_outcome"]
    missing = {k: info[k] for k in OUTCOME_KEYS if info.get(k) not in (None, "", False) and not known.get(k)}
    if missing:
        record_outcome(case, missing)
    return info


def _log_call(case: dict, mode: str, info: dict) -> dict:
    kind = "followup" if case["calls"] else "initial"
    call = followups.build_call(len(case["calls"]) + 1, kind, case_now(case), mode, info, case["call_outcome"])
    for f in case["followups"]:
        if f["status"] == "open":
            f.update(status="done", closed_by_call=call["n"], closed=call["date"])
    case["calls"].append(call)
    fu = followups.followup_for(call, case["hospital"]["name"])
    if fu:
        case["followups"].append(fu)
    save(case)
    bus.emit(case["id"], "call_logged", "caller", call=call)
    bus.emit(case["id"], "followup_scheduled", "caller", followup=fu)
    return call


def open_followup(case: dict) -> dict | None:
    return next((f for f in reversed(case.get("followups") or []) if f["status"] == "open"), None)


def current_balance(case: dict) -> float:
    if case["result"].get("final_balance") is not None:
        return case["result"]["final_balance"]
    bal = [e["amount"] for e in bus.history(case["id"]) if e["kind"] == "balance"]
    return bal[-1] if bal else case["bill"]["amount_due"]


def next_action(case: dict) -> dict:
    """What happens next on this case, for the dashboard and status answers."""
    st, today = case["status"], case_now(case)
    if st == "awaiting_call_approval":
        return {"action": f"Approve the call to {case['hospital']['name']} billing", "due": today.isoformat(), "overdue": False}
    if st in ("created", "reviewing", "calling", "planning"):
        return {"action": {"calling": "On the phone with billing now", "planning": "Working out a plan for what's left"}
                .get(st, "Reviewing the bill"), "due": None, "overdue": False}
    fu = open_followup(case)
    if fu:
        due = date.fromisoformat(fu["due"])
        return {"action": fu["action"], "due": fu["due"], "condition": fu["condition"], "check": fu["check"],
                "overdue": today > due, "due_today": today == due, "days_left": (due - today).days}
    if st == "interrupted":
        return {"action": "Interrupted by a restart" + (" — call again to pick it up" if can_call_again(case) else ""),
                "due": None, "overdue": False}
    if st == "error":
        return {"action": "Something went wrong — start a new case", "due": None, "overdue": False}
    return {"action": "Nothing pending — billing has done everything it promised", "due": None, "overdue": False}


def can_call_again(case: dict) -> bool:
    return case["status"] in ("done", "interrupted") and bool(case["result"].get("brief"))


def summary(case: dict) -> dict:
    """One row of the case dashboard."""
    calls, res = case.get("calls") or [], case["result"]
    last = calls[-1] if calls else None
    num = next((c["case_number"] for c in reversed(calls) if c.get("case_number")), None) or case["call_outcome"].get("case_number")
    return {"id": case["id"], "patient": f"{case['patient']['first_name']} {case['patient']['last_name']}",
            "provider": case["bill"]["provider"], "hospital": case["hospital"]["name"], "status": case["status"],
            "original": case["bill"]["amount_due"], "current_balance": current_balance(case), "target": res.get("target"),
            "refund": res.get("refund", 0) if res.get("final_balance") is not None and res.get("final_balance") == res.get("target") else 0,
            "case_number": num, "calls": calls, "last_call": last, "followups": case.get("followups") or [],
            "next": next_action(case), "today": case_now(case).isoformat(), "clock_days": case.get("clock_days", 0),
            "created": case.get("today"), "created_at": case.get("created_at"), "can_call_again": can_call_again(case),
            "plan": res.get("plan")}


def _history_for_llm(s: dict) -> dict:
    """The stored history with every date pre-formatted ("Fri Oct 30"), so the model never computes a weekday."""
    f, nxt = followups.fmt, s["next"]
    return {"today": f(s["today"]), "patient": s["patient"], "hospital": s["hospital"], "case_status": s["status"],
            "billed_amount": s["original"], "balance_after_corrections": s["target"], "refund_owed_to_patient": s["refund"],
            "case_number": s["case_number"], "payment_plan": s["plan"],
            "next_step": {**nxt, "due": f(nxt.get("due"))},
            "calls": [{"date": f(c["date"]), "type": c["kind"], "rep": c.get("rep"), "case_number": c.get("case_number"),
                       "promised": c.get("promised"), "timeframe": c.get("timeframe_text"),
                       "check_back_by": f(c.get("follow_up_date")) if c.get("promised") else None,
                       "still_open": c.get("open_items"), "balance_confirmed_by_rep": c.get("confirmed_balance"),
                       "refund_status": c.get("refund_status"), "summary": c.get("summary")} for c in s["calls"]],
            "followups": [{"created": f(x["created"]), "due": f(x["due"]), "action": x["action"], "status": x["status"]}
                          for x in s["followups"]]}


async def answer_status(case: dict, question: str) -> dict:
    """"What's the status?" — Claude answers from the stored history; a template answers offline."""
    s = summary(case)
    if llm.available():
        model = config.MODEL_FAST
        opts = {"thinking": {"type": "disabled"}, "effort": None} if "haiku-5-5" in model else {"effort": "low"}
        try:
            resp = await llm.create(system=followups.STATUS_SYSTEM, model=model, max_tokens=1024, messages=[{
                "role": "user", "content": f"CASE HISTORY (JSON):\n{json.dumps(_history_for_llm(s), default=str)}"
                                           f"\n\nPatient asks: {question}"}], **opts)
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
            if text:
                return {"answer": text, "source": "claude"}
        except Exception as e:
            print("status answer LLM error, using template:", type(e).__name__)
    return {"answer": followups.fallback_status(s), "source": "history"}


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
        bank, account_id, bank_label = await get_bank(case)
        bus.emit(cid, "case_started", modes=config.mode(), bank=bank_label, documents=case.get("documents", []),
                 bill={k: case["bill"][k] for k in ("provider", "amount_due", "notice", "due_date")})
        bus.emit(cid, "balance", amount=case["bill"]["amount_due"], label="Hospital says")

        set_status(case, "reviewing")
        loop = asyncio.get_running_loop()
        txns_ready, owed_ready = loop.create_future(), loop.create_future()
        audit, pol = await asyncio.gather(
            auditor.run(case, bank, account_id, bank_label, txns_ready, owed_ready),
            policy.run(case, txns_ready, owed_ready))

        owed, target = audit["reconciliation"]["owed"], pol["target"]
        if target != owed:
            bus.emit(cid, "balance", amount=target, label="With the hospital's own discount")
        brief = caller.build_brief(case, audit, pol)
        brief["previous_calls"] = followups.previous_calls(case)
        case["result"].update({"owed": owed, "target": target, "brief": brief, "income_monthly": pol["income"]["monthly"],
                               "refund": pol.get("refund", 0)})
        bus.emit(cid, "review_done", auditor=audit_summary(audit), policy=policy_summary(pol))

        set_status(case, "awaiting_call_approval")
        ok = await gate.request(cid, "caller", f"Call {case['hospital']['name']} billing for you?", {
            "asks": call_asks(audit, pol),
            "say": "I won't agree to pay anything on the call. Any payment comes back to you first."})
        outcome: dict = {}
        if ok:
            set_status(case, "calling")
            _reset_call_state(case)
            outcome = await (_phone_call(case, brief) if (voice.twilio_ready() or cartesia.phone_ready()) else _desk_call(case, brief))

        set_status(case, "planning")
        balance = target if outcome.get("assistance_review_opened") or outcome.get("account_on_hold") else owed
        if balance != target:
            bus.emit(cid, "balance", amount=balance, label="Still owed")
        plan = await planner.run(case, bank, account_id, balance, pol["income"]["monthly"],
                                 policy_cap_pct=(pol.get("plan_terms") or {}).get("max_pct_monthly_income"))

        c = plan.get("created") or {}
        plan_created = bool(c) and not c.get("blocked") and not c.get("error")
        if c.get("error"):
            bus.emit(cid, "warning", "planner", text="Couldn't save the plan to your bank")
        case["result"].update({"outcome": outcome, "plan": plan["plan"], "plan_created": plan_created, "final_balance": balance})
        set_status(case, "done")
        bus.emit(cid, "case_done", summary={
            "started_at": case["bill"]["amount_due"], "now": balance, "case_number": outcome.get("case_number"),
            "plan": plan["plan"], "plan_created": plan_created,
            "refund": pol.get("refund", 0) if balance == target else 0})
        save_replay(case)
    except Exception as e:
        set_status(case, "error")
        bus.emit(cid, "error", text=f"{type(e).__name__}: {e}")
        raise


async def call_again(case: dict) -> None:
    """Follow-up call on a finished case: same approval gate and call paths, with the earlier calls in the
    brief. Confirms the corrected balance/write-off, checks the refund, keeps the hold, logs the call."""
    cid = case["id"]
    try:
        brief = {**case["result"]["brief"], "call_type": "follow_up", "goals": followups.followup_goals(case),
                 "previous_calls": followups.previous_calls(case)}
        n = len(case["calls"]) + 1
        bus.emit(cid, "followup_started", "caller", n=n, previous_calls=brief["previous_calls"])
        set_status(case, "awaiting_call_approval")
        ok = await gate.request(cid, "caller", f"Call {case['hospital']['name']} billing again?", {
            "asks": followups.followup_asks(case),
            "say": "Following up on the last call. I won't agree to pay anything on the call."})
        outcome: dict = {}
        if ok:
            set_status(case, "calling")
            _reset_call_state(case)
            outcome = await (_phone_call(case, brief) if (voice.twilio_ready() or cartesia.phone_ready()) else _desk_call(case, brief))
        res = case["result"]
        confirmed = case["calls"][-1].get("confirmed_balance") if ok and case["calls"] else None
        if isinstance(confirmed, (int, float)) and confirmed != current_balance(case):
            res["final_balance"] = confirmed
            bus.emit(cid, "balance", amount=confirmed, label="Confirmed by billing")
        balance = current_balance(case)
        set_status(case, "done")
        bus.emit(cid, "case_done", summary={
            "started_at": case["bill"]["amount_due"], "now": balance,
            "case_number": summary(case)["case_number"], "plan": res.get("plan") or {"months": 0},
            "plan_created": res.get("plan_created", False), "followup_call": n if ok else None,
            "refund": res.get("refund", 0) if balance == res.get("target") else 0})
    except Exception as e:
        set_status(case, "error")
        bus.emit(cid, "error", text=f"{type(e).__name__}: {e}")
        raise
