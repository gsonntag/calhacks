"""Talk to Remy (the mic on the Cases page, agents/talk.py). Anything with consequences takes two turns:
Remy says exactly what it will do, and only a yes after that runs it. Offline: the browser's transcript is
used (no Cartesia key) and the rules answer (no Claude)."""
from remy import orchestrator
from remy.events import bus

from test_decide import _plan_ready
from test_flow import _wait_for, client  # noqa: F401  (client is a fixture)


def say(c, case_id: str, text: str):
    return c.post(f"/api/cases/{case_id}/talk", data={"heard": text})


async def test_status_by_voice_changes_nothing(client):
    case_id = await _plan_ready(client)
    r = (await say(client, case_id, "how's it going?")).json()
    assert r["heard"] == "how's it going?" and r["heard_by"] == "browser"
    assert "call" in r["reply"].lower() and r["pending"] is None  # it says what's waiting on you
    assert orchestrator.cases[case_id]["status"] == "awaiting_call_approval"
    qa = [e for e in bus.history(case_id) if e["kind"] == "agent_qa"]
    assert qa[-1]["voice"] is True and qa[-1]["question"] == "how's it going?"  # also in the case page's chat


async def test_call_needs_a_yes_after_remy_says_what_it_will_do(client):
    case_id = await _plan_ready(client)
    r = (await say(client, case_id, "go ahead and call them")).json()
    assert r["pending"] == "start_call" and "I'll call California Pacific Medical Center billing" in r["reply"]
    assert "plan v1" in r["reply"] and "apply your insurance" in r["reply"].lower()
    assert orchestrator.cases[case_id]["status"] == "awaiting_call_approval"  # nothing yet

    r = (await say(client, case_id, "yes, do it")).json()
    assert r["did"] == "start_call" and r["pending"] is None
    ready = await _wait_for(case_id, lambda e: e["kind"] == "call_ready")
    assert orchestrator.cases[case_id]["plan"]["approved_v"] == 1
    await client.post(f"/api/calls/{ready['call_id']}/hangup")


async def test_yes_with_nothing_proposed_does_nothing(client):
    case_id = await _plan_ready(client)
    r = (await say(client, case_id, "yes")).json()
    assert r["did"] is None and orchestrator.cases[case_id]["status"] == "awaiting_call_approval"


async def test_no_cancels_what_was_proposed(client):
    case_id = await _plan_ready(client)
    assert (await say(client, case_id, "call them")).json()["pending"] == "start_call"
    r = (await say(client, case_id, "no, wait")).json()
    assert r["pending"] is None and r["did"] is None
    assert (await say(client, case_id, "yes")).json()["did"] is None


async def test_a_stale_yes_never_runs_a_changed_plan(client):
    case_id = await _plan_ready(client)
    assert (await say(client, case_id, "call them")).json()["pending"] == "start_call"
    await client.post(f"/api/cases/{case_id}/planner", json={"message": "I can pay $50/mo"})  # plan v2 now
    r = (await say(client, case_id, "yes")).json()
    assert r["did"] is None and r["pending"] == "start_call" and "plan v2" in r["reply"]  # says it again, v2
    assert orchestrator.cases[case_id]["status"] == "awaiting_call_approval"


async def test_plan_changes_by_voice_go_to_the_planner(client):
    case_id = await _plan_ready(client)
    r = (await say(client, case_id, "I can only pay $50/mo")).json()
    assert r["did"] == "plan_change" and "plan v2" in r["reply"]
    said = [e for e in bus.history(case_id) if e["kind"] == "planner_message" and e["role"] == "user"]
    assert said[-1]["via"] == "voice"


async def test_nothing_heard(client):
    case_id = await _plan_ready(client)
    r = (await say(client, case_id, "  ")).json()
    assert r["heard"] == "" and "didn't catch" in r["reply"]
