"""Offline end-to-end: sample case -> approve call -> desk-mode call -> approve plan -> case_done at $150."""
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
                   "call_ended", "plan_proposed", "tool_result", "policy_quote", "case_done"}


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

    plan_req = await _approve(c, case_id, "planner")
    assert plan_req["title"] == "Set up a 3 x $50 interest-free plan with Bayview Community Hospital?"

    done = await _wait_for(case_id, lambda e: e["kind"] == "case_done")
    s = done["summary"]
    assert s["started_at"] == 8700 and s["now"] == 150
    assert s["case_number"] == "58213"
    assert s["plan"]["months"] == 3 and s["plan"]["monthly"] == 50
    assert s["plan_created"] is True

    hist = bus.history(case_id)
    kinds = {e["kind"] for e in hist}
    assert CASE_HTML_KINDS <= kinds, CASE_HTML_KINDS - kinds
    assert [e["amount"] for e in hist if e["kind"] == "balance"] == [8700, 2100, 150]
    assert orchestrator.cases[case_id]["status"] == "done"
    # the "Okay." repeats from the rep all show in the transcript
    assert sum(1 for e in hist if e["kind"] == "transcript" and e["speaker"] == "rep") == len(rep_lines)


async def test_chat_starts_exactly_one_case(client):
    c = client
    case_id, history = None, []
    for msg in ["hi", "4000", "yes", "thanks!"]:
        r = (await c.post("/api/chat", data={"history": json.dumps(history), "message": msg,
                                             "case_id": case_id or ""})).json()
        assert r["reply"]
        history += [{"role": "user", "content": msg}, {"role": "assistant", "content": r["reply"]}]
        case_id = r.get("case_id") or case_id
    assert case_id and list(orchestrator.cases) == [case_id]
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
    owed.set_result({"owed": 2100.0, "responsibility": 2600.0, "paid": 500.0})
    return await policy.run(case, txns, owed)


@pytest.mark.parametrize("reply", [
    {"tiers": [{"max_fpl_pct": None, "discount_pct": 50}, {"max_fpl_pct": "400%", "discount_pct": "AGB"}]},
    ValueError("no JSON in model output"),
])
async def test_bad_policy_extraction_never_crashes(monkeypatch, reply):
    from billback import llm
    async def fake_json_out(*a, **k):
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "json_out", fake_json_out)
    monkeypatch.setattr(orchestrator, "date", _Today)
    case = orchestrator.new_case()
    out = await _run_policy(case)
    if isinstance(reply, Exception):  # falls back to the labelled sample policy
        assert out["target"] == 150 and out["source"] == "sample policy (offline)"
    else:  # nothing usable: honest "no discount", no crash
        assert out["policy"]["tiers"] == [] and out["target"] == 2100


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
