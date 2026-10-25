"""The rail's ⋯ menu: stop a case's agents, archive it, delete it."""
import asyncio

import httpx
import pytest

from remy import app as app_mod
from remy import events, orchestrator
from remy.events import bus
from remy.harness import gate


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app_mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
        await c.post("/api/reset")


async def _waiting_for_ok(c) -> str:
    """A sample case, run until it waits for the patient's OK to call."""
    case_id = (await c.post("/api/cases")).json()["case_id"]
    for _ in range(400):
        if gate.pending_for(case_id):
            return case_id
        await asyncio.sleep(0.05)
    pytest.fail("the case never asked to call")


async def test_stop_cancels_the_run_and_its_approval(client):
    case_id = await _waiting_for_ok(client)
    r = await client.post(f"/api/cases/{case_id}/stop")
    assert r.json() == {"ok": True, "status": "interrupted"}
    assert gate.pending_for(case_id) == []
    assert not [t for t in app_mod._tasks if t.get_name() == case_id and not t.done()]
    assert any(e["kind"] == "warning" and "Stopped" in e["text"] for e in bus.history(case_id))
    assert (await client.post(f"/api/cases/{case_id}/stop")).status_code == 409  # nothing left to stop


async def test_archive_and_back(client):
    case_id = await _waiting_for_ok(client)
    assert (await client.post(f"/api/cases/{case_id}/archive", json={"archived": True})).json()["archived"] is True
    row = next(r for r in (await client.get("/api/dashboard")).json() if r["id"] == case_id)
    assert row["archived"] is True and row["status"] == "awaiting_call_approval"  # archiving changes nothing else
    assert (await client.post(f"/api/cases/{case_id}/archive", json={"archived": False})).json()["archived"] is False


async def test_delete_removes_the_case_but_keeps_the_audit_log(client):
    case_id = await _waiting_for_ok(client)
    assert (await client.delete(f"/api/cases/{case_id}")).json() == {"ok": True}
    assert case_id not in orchestrator.cases and gate.pending_for(case_id) == []
    assert all(r["id"] != case_id for r in (await client.get("/api/dashboard")).json())
    assert not (events.DATA_DIR / "cases" / f"{case_id}.json").exists()  # a restart won't bring it back
    assert (events.DATA_DIR / f"{case_id}.audit.jsonl").exists()
    assert bus.history(case_id) == []
    assert (await client.delete(f"/api/cases/{case_id}")).status_code == 404


async def test_not_while_on_a_call(client):
    case_id = await _waiting_for_ok(client)
    orchestrator.cases[case_id]["status"] = "calling"
    for r in (await client.post(f"/api/cases/{case_id}/stop"), await client.delete(f"/api/cases/{case_id}")):
        assert r.status_code == 409 and "end the call" in r.json()["error"]
    assert case_id in orchestrator.cases
