"""Step 5: the scorecard (agents/scorecard.py) on the fixture calls in demo/outcomes/, and the Decide agent's
plan for what's left after a call, end to end offline: call -> scorecard -> next plan -> "Call now" -> call 2."""
import json

import pytest

from billback import config, orchestrator
from billback.agents import scorecard
from billback.events import bus

from test_followups import FIRST_CALL, SECOND_CALL, _approve, _desk_call, _wait_for, client  # noqa: F401

FIXTURES = sorted((config.DEMO_DIR / "outcomes").glob("*.json"))


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_fixture_calls_get_the_expected_marks(path):
    fx = json.loads(path.read_text())
    marks = scorecard.score(fx["plan"]["asks"], fx["outcome"], fx["info"], fx["rep_lines"], fx["plan"]["money"])
    assert {m["id"]: m["status"] for m in marks} == fx["expect"]
    assert [m["id"] for m in marks] == [a["id"] for a in fx["plan"]["asks"]]  # the plan's order
    for m in marks:  # every mark but "unclear" says why
        assert m["label"] == scorecard.LABELS[m["status"]]
        assert m["evidence"] and (m["status"] == "unclear" or m["source"])


def test_fixtures_cover_every_outcome():
    seen = {s for p in FIXTURES for s in json.loads(p.read_text())["expect"].values()}
    assert {"got", "refused", "later", "unclear"} <= seen


def test_a_win_needs_evidence():
    ask = [{"id": "insurance", "kind": "insurance", "text": "Apply your insurance"}]
    money = {"owed": 600, "target": 600}
    # Claude says "got" but the quote isn't the rep's words: stays unclear
    info = {"asks": {"insurance": {"status": "got", "quote": "the adjustment has been applied"}}}
    assert scorecard.score(ask, {}, info, ["Let me look into it."], money)[0]["status"] == "unclear"
    # a rep line that says no is never read as a win
    assert scorecard.score(ask, {}, {}, ["The adjustment was not applied."], money)[0]["status"] == "unclear"
    # the balance billing states counts only if it's at or below what's owed
    assert scorecard.score(ask, {}, {"confirmed_balance": 1100}, [], money)[0]["status"] == "unclear"
    assert scorecard.score(ask, {}, {"confirmed_balance": 600}, [], money)[0]["status"] == "got"


def test_supervisor_is_not_needed_when_nothing_was_refused():
    asks = [{"id": "hold", "kind": "hold", "text": "Hold"}, {"id": "supervisor", "kind": "supervisor", "text": "Supervisor"}]
    marks = scorecard.score(asks, {"account_on_hold": True}, {}, [], {"owed": 0, "target": 0})
    assert [m["status"] for m in marks] == ["got", "not_needed"]
    assert scorecard.summary(marks) == "1 got"


def _plans(cid: str, after: int = 0) -> list[dict]:
    return [e["plan"] for e in bus.history(cid)[after:] if e["kind"] == "plan_version"]


async def test_after_the_call_the_plan_keeps_only_whats_left(client):
    c = client
    cid = (await c.post("/api/cases")).json()["case_id"]
    await _approve(c, cid)
    await _desk_call(c, cid, FIRST_CALL)
    await _wait_for(cid, lambda e: e["kind"] == "case_done")

    sc = next(e for e in bus.history(cid) if e["kind"] == "scorecard")
    marks = {m["id"]: m["status"] for m in sc["marks"]}
    # Diane opened a review, put the account on hold, promised a corrected statement, gave a case number
    assert marks == {"insurance": "later", "payment": "later", "discount": "later", "refund": "later",
                     "hold": "got", "case_number": "got"}
    case = orchestrator.cases[cid]
    assert case["calls"][0]["scorecard"] == sc["marks"] and case["calls"][0]["plan_v"] == 1

    v2 = _plans(cid)[-1]
    assert v2["v"] == 2 and v2["round"] == 2 and v2["reason"]["kind"] == "call_result"
    assert [d["kind"] for d in v2["done"]] == ["hold"]
    assert [a["kind"] for a in v2["asks"]] == ["insurance", "discount", "payment", "refund", "case_number"]
    assert all(a["text"].startswith("Check it was done: ") for a in v2["asks"][:-1])
    assert "promised" in v2["asks"][0]["goal"] and "Diane" in v2["asks"][0]["goal"]
    assert v2["next"]["kind"] == "wait" and v2["next"]["when"] == "2026-10-30"
    assert v2["after_call"]["n"] == 1 and v2["after_call"]["rep"] == "Diane"
    assert v2["money"] == _plans(cid)[0]["money"]  # the money comes from the facts, not the call
    # step 5's Decide agent has its own panel
    assert any(e["kind"] == "agent_finished" and e["agent"] == "decide:2" for e in bus.history(cid))
    assert case["status"] == "done" and not any(e["kind"] == "plan_approval_reset" for e in bus.history(cid))

    # the planner chat works on the next plan, and its messages say which round they're for
    r = (await c.post(f"/api/cases/{cid}/planner", json={"message": "Ask for a supervisor if they say no"})).json()
    assert r["v"] == 3 and r["plan"]["round"] == 2 and "supervisor" in [a["kind"] for a in r["plan"]["asks"]]
    assert {e["round"] for e in bus.history(cid) if e["kind"] == "planner_message"} == {2}

    # "Call now" on step 5's plan: no second approval; only what's left goes on the call
    assert (await c.post(f"/api/cases/{cid}/call-again", json={"v": 2})).status_code == 409  # not the latest
    n = len(bus.history(cid))
    assert (await c.post(f"/api/cases/{cid}/call-again", json={"v": 3})).json()["ok"] is True
    ready = await _wait_for(cid, lambda e: e["kind"] == "call_ready", after=n)
    assert not any(e["kind"] == "approval_requested" for e in bus.history(cid)[n:])
    assert any(e["kind"] == "plan_approved" and e["v"] == 3 for e in bus.history(cid)[n:])
    brief = (await c.get(f"/api/calls/{ready['call_id']}")).json()["brief"]
    assert brief["call_type"] == "follow_up" and brief["plan_version"] == 3
    assert brief["goals"] == [a["goal"] for a in r["plan"]["asks"]]
    assert not any("collections" in g for g in brief["goals"])  # the hold was already done
    await _desk_call(c, cid, SECOND_CALL, after=n)
    await _wait_for(cid, lambda e: e["kind"] == "case_done", after=n)

    # call 2 (Marco): the adjustment is on, the refund goes out in two weeks
    marks2 = {m["id"]: m["status"] for m in case["calls"][1]["scorecard"]}
    assert marks2["insurance"] == "got" and marks2["case_number"] == "got"
    v4 = _plans(cid)[-1]
    assert v4["round"] == 3 and v4["after_call"]["n"] == 2
    assert {d["kind"] for d in v4["done"]} >= {"hold", "insurance"}
    assert "insurance" not in [a["kind"] for a in v4["asks"]]
