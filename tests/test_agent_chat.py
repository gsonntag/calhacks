"""The case page's agent chat (one bubble, pick who answers): BillBack (knows the whole case), the Nessie agent and
the Bright Data agent (each only its own evidence), and the planner. All through /api/cases/{id}/ask with `agent`.
Offline (no Claude): templates and the planner's rules answer, so these run without keys."""
from billback import orchestrator
from billback.events import bus

from test_decide import _plan_ready, _versions
from test_flow import client  # noqa: F401  (fixture)


def _qa(case_id: str) -> list[dict]:
    return [e for e in bus.history(case_id) if e["kind"] == "agent_qa"]


async def test_each_review_agent_answers_from_all_of_its_own_evidence(client):
    case_id = await _plan_ready(client)
    n = len(_versions(case_id))
    ask = lambda agent: client.post(f"/api/cases/{case_id}/ask", json={"question": "What did you find?", "agent": agent})  # noqa: E731

    r = (await ask("auditor")).json()
    assert r["agent"] == "auditor" and r["agent_name"] == "Nessie agent"
    assert "$1,100.00" in r["answer"] and "$500" in r["answer"]  # every finding of its own, not just one
    assert "Golden State Health Plan · claim C-2026-0815-44917" in r["sources"]

    r = (await ask("policy")).json()
    assert r["agent"] == "policy" and r["agent_name"] == "Bright Data agent"
    assert "$1,100.00" not in r["answer"]  # the insurance statement is the Nessie agent's evidence

    assert [e["agent"] for e in _qa(case_id)] == ["auditor", "policy"]
    assert _qa(case_id)[0]["question"] == "What did you find?"
    assert len(_versions(case_id)) == n  # asking changes nothing


async def test_billback_answers_about_the_whole_case(client):
    case_id = await _plan_ready(client)
    r = (await client.post(f"/api/cases/{case_id}/ask", json={"question": "how's it going?", "agent": "billback"})).json()
    assert r["agent"] == "billback" and r["agent_name"] == "BillBack" and r["answer"]
    assert _qa(case_id)[-1]["agent"] == "billback"


async def test_billback_hands_plan_changes_to_the_planner(client):
    case_id = await _plan_ready(client)
    n = len(_versions(case_id))
    r = (await client.post(f"/api/cases/{case_id}/ask", json={"question": "I can pay $150/mo", "agent": "billback"})).json()
    assert r["agent"] == "decide" and r["via"] == "billback" and r["v"] == n + 1
    said = [e for e in bus.history(case_id) if e["kind"] == "planner_message" and e["role"] == "user"]
    assert said[-1]["text"] == "I can pay $150/mo" and said[-1]["via"] == "billback"
    assert not _qa(case_id)  # the planner's own messages are the record of it


async def test_planner_is_reachable_through_ask(client):
    case_id = await _plan_ready(client)
    r = (await client.post(f"/api/cases/{case_id}/ask", json={"question": "refund first please", "agent": "decide"})).json()
    assert r["agent"] == "decide" and r["answer"] == r["reply"]  # refused here: this case has no refund
    assert [e["role"] for e in bus.history(case_id) if e["kind"] == "planner_message"] == ["user", "planner"]


async def test_unknown_agent_and_agents_still_working_are_refused(client):
    case_id = await _plan_ready(client)
    assert (await client.post(f"/api/cases/{case_id}/ask", json={"question": "?", "agent": "nope"})).status_code == 409
    del orchestrator.cases[case_id]["facts"]  # as while the Review is still running
    r = await client.post(f"/api/cases/{case_id}/ask", json={"question": "?", "agent": "auditor"})
    assert r.status_code == 409 and "still checking" in r.json()["error"]
