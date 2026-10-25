"""Offline end-to-end: sample case -> approve call -> desk-mode call -> case_done at $0 with $500 back."""
import asyncio
import json
from datetime import date

import httpx
import pytest

from billback import app as app_mod
from billback import config, orchestrator
from billback.events import bus

# Kinds web/case.html renders from the /ws/cases/{id} stream.
CASE_HTML_KINDS = {"case_started", "agent_started", "agent_finished", "step", "review_done", "balance",
                   "approval_requested", "approval_resolved", "call_ready", "transcript", "call_outcome",
                   "call_ended", "plan_proposed", "tool_result", "policy_quote", "case_done", "finding"}


class _Today(date):
    @classmethod
    def today(cls):
        return date(2026, 10, 24)


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setattr(orchestrator, "date", _Today)
    transport = httpx.ASGITransport(app=app_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
        await c.post("/api/reset")


async def _wait_for(case_id: str, pred, timeout: float = 20):
    for _ in range(int(timeout / 0.05)):
        for e in bus.history(case_id):
            if e["kind"] == "error":
                pytest.fail(f"case errored: {e['text']}")
            if pred(e):
                return e
        await asyncio.sleep(0.05)
    pytest.fail("timed out waiting for event")


async def _approve(c, case_id: str, agent: str) -> None:
    e = await _wait_for(case_id, lambda e: e["kind"] == "approval_requested" and e["agent"] == agent)
    r = await c.post(f"/api/approvals/{e['approval_id']}", json={"approved": True})
    assert r.json() == {"ok": True}
    return e


async def test_offline_end_to_end(client):
    c = client
    case_id = (await c.post("/api/cases")).json()["case_id"]

    await _approve(c, case_id, "caller")
    ready = await _wait_for(case_id, lambda e: e["kind"] == "call_ready")
    assert ready["mode"] == "desk" and ready["rep_url"] == f"/rep.html?call={ready['call_id']}"

    rep_lines = ["Patient Financial Services, this is Diane, how can I help you?",
                 "Okay, let me pull that up.", "I see it.", "Okay.",
                 "Yes, I've opened a financial assistance review and put the account on hold.",
                 "Your case number is 58213."]
    for line in rep_lines:
        r = (await c.post(f"/api/calls/{ready['call_id']}/turn", json={"rep_said": line})).json()
        assert r["say"]
    assert r["ended"] is True
    assert r["outcome"]["case_number"] == "58213"

    # $600 is still owed: the interest-free plan (Sutter's terms) waits for the patient's OK, then goes to the bank
    await _approve(c, case_id, "planner")
    done = await _wait_for(case_id, lambda e: e["kind"] == "case_done")
    s = done["summary"]
    assert s["started_at"] == 8700 and s["now"] == 600 and s["refund"] == 0
    assert s["case_number"] == "58213"
    assert (s["plan"]["months"], s["plan"]["monthly"]) == (6, 100.0) and s["plan_created"] is True

    hist = bus.history(case_id)
    kinds = {e["kind"] for e in hist}
    assert CASE_HTML_KINDS <= kinds, CASE_HTML_KINDS - kinds

    # both app agents report in the shared format (agents/report.py): named panels, steps with proof, findings
    started = {e["agent"]: e for e in hist if e["kind"] == "agent_started" and e["agent"] in ("auditor", "policy")}
    assert started["auditor"]["name"] == "Nessie agent" and started["policy"]["name"] == "Bright Data agent"
    assert all(started[a]["covers"] for a in started)
    steps = [e for e in hist if e["kind"] == "step"]
    assert all(e["status"] in {"running", "ok", "flag", "clear", "win", "wait"} and isinstance(e["proof"], list) for e in steps)
    paid = next(e for e in steps if e["key"] == "auditor:payments" and e["status"] != "running")
    assert paid["status"] == "flag" and any("$500.00" in p["text"] and p["source"] for p in paid["proof"])
    finds = {e["title"]: e for e in hist if e["kind"] == "finding"}
    assert finds["Insurance wasn’t applied"]["saved"] == 7600
    assert finds["A payment you made was missed"]["saved"] == 500
    assert finds["Interest-free payment plan"]["saved"] == 0 and finds["Interest-free payment plan"]["value"] == "6 × $100/mo"
    assert all(f["proof"] and f["source"] for f in finds.values())
    assert [e["amount"] for e in hist if e["kind"] == "balance"] == [8700, 600]
    assert orchestrator.cases[case_id]["status"] == "done"
    # the "Okay." repeats from the rep all show in the transcript
    assert sum(1 for e in hist if e["kind"] == "transcript" and e["speaker"] == "rep") == len(rep_lines)


async def _offline_call(c, scenario: str) -> tuple[dict, list[str], dict]:
    """Runs a demo case's offline call; returns (call brief, what BillBack said, the call's outcome)."""
    case_id = (await c.post(f"/api/cases?scenario={scenario}")).json()["case_id"]
    await _approve(c, case_id, "caller")
    ready = await _wait_for(case_id, lambda e: e["kind"] == "call_ready")
    for _ in range(10):
        r = (await c.post(f"/api/calls/{ready['call_id']}/turn", json={"rep_said": "Your case number is 58213."})).json()
        if r["ended"]:
            break
    said = [e["text"] for e in bus.history(case_id) if e["kind"] == "transcript" and e["speaker"] == "billback"]
    return orchestrator.cases[case_id]["result"]["brief"], said, r["outcome"]


async def test_offline_caller_says_only_the_plans_asks(client):
    """Every line on the call is backed by an ask in the approved plan (docs/PLAN.md guardrails)."""
    # Rosa: 228% of the poverty line, not eligible at her hospital; the plan asks insurance, payment, payment plan
    brief, said, outcome = await _offline_call(client, "default")
    assert "discount" not in brief["ask_kinds"] and "plan" in brief["ask_kinds"]
    assert brief["income_pct_fpl"] is None and brief["policy_quotes"] == [] and brief["policy_tier"] is None
    text = " ".join(said).lower()
    assert "qualif" not in text and "financial assistance" not in text and "poverty" not in text
    assert "insurance adjustment" in text and "$500 payment" in text and "6 monthly payments of $100" in text
    assert outcome["case_number"] == "58213" and outcome["account_on_hold"] is True
    assert not outcome.get("assistance_review_opened")
    await client.post("/api/reset")

    # Marcus: the bill already matches his insurance; the plan asks for free care only
    brief, said, outcome = await _offline_call(client, "freecare")
    assert "insurance" not in brief["ask_kinds"] and "discount" in brief["ask_kinds"]
    assert brief["income_pct_fpl"] is not None and brief["policy_quotes"]
    text = " ".join(said).lower()
    assert "insurance adjustment" not in text and "corrected balance" not in text
    assert "qualifies under your own financial assistance policy" in text
    assert outcome["case_number"] == "58213" and outcome["assistance_review_opened"] is True


async def test_chat_starts_exactly_one_case(client):
    c = client
    case_id, history, started = None, [], []
    for msg in ["hi", "4000", "yes", "thanks!"]:
        r = (await c.post("/api/chat", data={"history": json.dumps(history), "message": msg,
                                             "case_id": case_id or ""})).json()
        assert r["reply"]
        started.append(r.get("started", False))
        history += [{"role": "user", "content": msg}, {"role": "assistant", "content": r["reply"]}]
        case_id = r.get("case_id") or case_id
    assert case_id and list(orchestrator.cases) == [case_id]
    assert started.count(True) == 1  # only the turn that starts the case sends the chat to the case page
    # an old client that doesn't send case_id still can't start a second one
    hist = '[{"role":"user","content":"a"},{"role":"assistant","content":"b"},{"role":"user","content":"c"},' \
           '{"role":"assistant","content":""},{"role":"user","content":"d"}]'
    r = (await c.post("/api/chat", data={"history": hist, "message": "thanks"})).json()
    assert "case_id" not in r and len(orchestrator.cases) == 1


async def test_webhooks_without_request_id_are_not_dropped(client, monkeypatch):
    c = client
    monkeypatch.setattr(config, "CARTESIA_WEBHOOK_SECRET", "s3cret")
    case = orchestrator.new_case()
    orchestrator.calls_by_cartesia_id["ac_1"] = case["id"]
    h = {"x-webhook-secret": "s3cret"}
    assert (await c.post("/webhooks/cartesia", json={"type": "call_turn"}, headers={})).status_code == 401
    for text in ("hello", "Okay.", "Okay."):
        await c.post("/webhooks/cartesia", headers=h, json={"type": "call_turn", "turn": {"role": "user", "text": text}})
    await c.post("/webhooks/cartesia", headers=h, json={"type": "call_completed", "call": {"id": "ac_1"}})
    texts = [e["text"] for e in bus.history(case["id"]) if e["kind"] == "transcript"]
    assert texts == ["hello", "Okay.", "Okay."]
    assert case["call_done"].is_set()
    orchestrator.calls_by_cartesia_id.clear()


async def test_placeholder_tool_token_is_rejected(client, monkeypatch):
    monkeypatch.setattr(config, "CARTESIA_TOOL_TOKEN", "")
    r = await client.post("/api/cartesia/tools/record_outcome", headers={"authorization": "Bearer "},
                          json={"case_id": "x"})
    assert r.status_code == 401


@pytest.mark.parametrize("said,expected", [
    ("Your case number is 58213.", "58213"), ("case number BB 4471", "BB4471"),
    ("it's fa-2291", "FA-2291"), ("FA2291", "FA2291"), ("no number, sorry", "no number, sorry")])
def test_scripted_case_number(said, expected):
    from billback.agents.caller import _case_number
    assert _case_number(said) == expected


async def _run_policy(case):
    from billback.agents import policy
    loop = asyncio.get_running_loop()
    txns, owed = loop.create_future(), loop.create_future()
    txns.set_result([{"id": f"t{i}", "merchant": "", **t} for i, t in enumerate(case["transactions"])])
    owed.set_result({"owed": 600.0, "responsibility": 1100.0, "paid": 500.0})
    return await policy.run(case, txns, owed)


@pytest.mark.parametrize("reply", [
    {"tiers": [{"max_fpl_pct": None, "discount_pct": 50}, {"max_fpl_pct": "400%", "discount_pct": "AGB"}]},
    ValueError("no JSON in model output"),
])
async def test_bad_policy_extraction_never_crashes(monkeypatch, reply):
    from billback import llm
    from billback.agents import policy
    async def fake_json_out(*a, **k):
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "json_out", fake_json_out)
    monkeypatch.setattr(orchestrator, "date", _Today)
    case = orchestrator.new_case()
    # a hospital that isn't in the policy DB, with Bright Data off -> the offline test fixture path
    case["hospital"] = {**case["hospital"], "name": "Nowhere General Hospital"}
    out = await _run_policy(case)
    assert out["origin"] == "offline_fixture" and out["live"] is False
    if isinstance(reply, Exception):  # fixture's known tiers, labelled as the fixture
        assert out["target"] == 0 and out["refund"] == 225 and out["source"] == policy.FIXTURE_LABEL  # 75% tier
    else:  # nothing usable: honest "no discount", no crash
        assert out["policy"]["tiers"] == [] and out["target"] == 600


async def test_policy_comes_from_the_policy_db_offline():
    """Rosa's hospital is in the committed policy DB: no network, real verified Sutter policy. Her $1,100 share
    is 3% of income, under the 10% the write-off needs, so $600 stays, on Sutter's interest-free plan terms."""
    case = orchestrator.new_case()
    out = await _run_policy(case)
    assert out["origin"] == "policy_db" and out["live"] is True and out["fetched_at"]
    assert "sutterhealth" in out["source"] or "sutterhealt" in out["source"]
    assert out["target"] == 600 and out["refund"] == 0 and out["eligibility"]["discount_pct"] == 0
    assert (out["plan"]["months"], out["plan"]["monthly"]) == (6, 100.0)
    terms = out["plan_terms"]
    assert terms["interest_free"] is True and terms["max_pct_monthly_income"] == 10 and "interest-free" in terms["quote"]
    assert all(t["verified"] and t["source_url"] for t in out["policy"]["tiers"])
    kinds = [e for e in bus.history(case["id"]) if e["kind"] == "policy_source"]
    assert kinds and kinds[0]["origin"] == "policy_db" and kinds[0]["hospital_id"] == "sutter-cpmc"


async def test_caller_llm_error_keeps_history_valid(monkeypatch):
    from billback import llm
    from billback.agents.caller import CallSession
    async def boom(**k):
        raise RuntimeError("overloaded")
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "create", boom)
    s = CallSession("c1", {})
    say = await s.turn("hello?")
    assert say == "Sorry, could you say that again?" and s.messages == [] and not s.ended
    assert any(e["kind"] == "warning" for e in bus.history("c1"))
