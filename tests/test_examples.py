"""The demo patients every "Try an example" entry point offers (remy/examples.py, GET /api/examples): Raj first,
then Marcus and Rosa; a scenario this server doesn't have is listed disabled and can't be started."""
import asyncio
import importlib.util
from pathlib import Path

import httpx
import pytest

from remy import app as app_mod
from remy import config, examples, orchestrator
from remy.events import bus

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test") as c:
        yield c
        await c.post("/api/reset")


async def test_examples_list_raj_first_then_marcus_and_rosa(client):
    r = (await client.get("/api/examples")).json()
    assert [(e["scenario"], e["name"]) for e in r] == [("raj", "Raj"), ("freecare", "Marcus"), ("default", "Rosa")]
    assert r[0]["label"].startswith("Raj — his bank history qualifies him")
    assert r[0]["available"] == examples.exists("raj") == (config.DEMO_DIR / "scenarios/raj-cpmc/case.json").is_file()
    if r[0]["available"]:  # once demo/scenarios/raj-cpmc/ is on the branch
        assert r[0]["household_size"] == 4 and r[0]["amount_due"] == 16088.70
    for e in r[1:]:
        assert e["available"] and e["household_size"] >= 1 and e["short"].startswith(e["name"])
    assert r[1]["household_size"] == 3 and r[1]["short"] == "Marcus · $5,600 HDHP"
    assert r[2]["short"] == "Rosa · $8,700 ER bill"


async def test_missing_scenario_is_listed_disabled_and_404s(client, monkeypatch, tmp_path):
    """Before demo/scenarios/raj-cpmc/ lands (or on a server without it), Raj shows disabled; starting him fails
    cleanly and starts nothing."""
    monkeypatch.setattr(examples, "EXAMPLES", [{"scenario": "nobody-here", "name": "Nobody", "label": "Nobody"},
                                               *examples.EXAMPLES[1:]])
    r = (await client.get("/api/examples")).json()
    assert r[0] == {"scenario": "nobody-here", "name": "Nobody", "label": "Nobody", "available": False,
                    "note": "Not on this server yet"}
    for bad in ("nobody-here", "../case", "raj cpmc", ""):
        res = await client.post("/api/cases", params={"scenario": bad})
        assert res.status_code == 404 and "Unknown demo scenario" in res.json()["detail"]
    assert not orchestrator.cases
    assert (await client.get("/api/compare", params={"scenario": "nobody-here"})).status_code == 400
    with pytest.raises(examples.UnknownScenario):
        orchestrator.new_case(scenario="nobody-here")


async def test_generated_scenario_starts_offline(client):
    """Any generated scenario (demo/scenarios/<name>/) starts the same way Raj's will: the case carries that
    patient and runs to the call approval offline."""
    for name in ("raj-cpmc", "marcus-real"):
        if examples.exists(name):
            break
    case_id = (await client.post("/api/cases", params={"scenario": name})).json()["case_id"]
    case = orchestrator.cases[case_id]
    assert case["scenario"] == name
    assert case["patient"]["first_name"] == examples.load(name)["patient"]["first_name"]
    for _ in range(400):
        kinds = [e["kind"] for e in bus.history(case_id)]
        assert "error" not in kinds
        if "approval_requested" in kinds:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("case never reached the call approval")
    assert examples.sample_docs(name) == [(f"{name}-{f}", config.DEMO_DIR / "scenarios" / name / f)
                                          for f in ("bill.pdf", "eob.pdf")]


def test_entry_points_read_the_one_list():
    """New case, drop-go and the Hospitals compare picker render GET /api/examples; none hard-codes a patient."""
    for page in ("new.html", "flows/drop-go.html", "hospitals.html"):
        html = (WEB / page).read_text()
        assert '<script src="/examples.js"></script>' in html and "Examples.load()" in html, page
    assert "'freecare'" not in (WEB / "flows/drop-go.html").read_text()
    assert "sampleChip" not in (WEB / "new.html").read_text()


def test_test_bills_match_the_scenarios():
    """web/samples/test-bills/ has every generated scenario's PDFs (scripts/copy_test_bills.py)."""
    spec = importlib.util.spec_from_file_location("copy_test_bills", ROOT / "scripts" / "copy_test_bills.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for d in sorted((config.DEMO_DIR / "scenarios").glob("*/bill.pdf")):
        name = d.parent.name
        assert (WEB / "samples" / "test-bills" / f"{name}-bill.pdf").read_bytes() == d.read_bytes(), \
            f"run: python scripts/copy_test_bills.py {name}"
        eob = d.parent / "eob.pdf"
        if eob.is_file():
            assert (WEB / "samples" / "test-bills" / f"{name}-eob.pdf").read_bytes() == eob.read_bytes()
    with pytest.raises(FileNotFoundError):
        mod.copy(["nobody-here"])
