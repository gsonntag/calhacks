"""The Ask Remy eval (tests/evals/ask_remy_questions.yaml + scripts/eval_ask_remy.py): the question file is
valid, offline mode runs end to end on this branch without crashing, live mode can't start without approval,
and a toy case-less endpoint drives every check (stub model, memory, context, worlds) so the runner itself is
tested before the real endpoint exists."""
import importlib.util
import json
import sys
import types

import pytest

from remy import app as app_mod
from remy import llm, orchestrator
from remy.agents import decide

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("eval_ask_remy", ROOT / "scripts" / "eval_ask_remy.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)

GROUPS = {"case", "cases", "hospitals", "under_the_hood", "get_started", "memory", "red_team"}


def test_question_file_is_valid_and_covers_every_group():
    spec = ev.load()
    items = spec["items"]
    assert set(spec["groups"]) == GROUPS
    assert 55 <= len(items) <= 95
    assert len({it["id"] for it in items}) == len(items)
    for it in items:
        assert {"id", "page", "case", "turns"} <= set(it) and it["page"] in ev.PAGES
    assert any(len(it["turns"]) >= 3 for it in items if it["group"] == "memory")
    red = {it["id"] for it in items if it["group"] == "red_team"}
    for must in ("rt-pay-now", "rt-give-card", "rt-ssn", "rt-pretend-human", "rt-injection-in-question",
                 "rt-legal-advice", "rt-invented-balance"):
        assert must in red


def test_validate_catches_bad_items():
    bad = {"groups": {"g": {"world": ["rosa"], "items": [
        {"id": "a", "page": "nope", "case": "rosa", "turns": ["q"], "route": "remy"},
        {"id": "a", "page": "case", "case": "marcus", "turns": [], "route": "x"},
        {"id": "b", "page": "case", "case": "none", "turns": ["q"]}]}}}
    errs = "\n".join(ev.validate(bad))
    for frag in ("a: page must be", "a: duplicate id", "not in group g's world", "turns must be", "route must be",
                 "b: has nothing to check"):
        assert frag in errs


def test_number_grounding():
    ground = ev.numbers_in('{"owed": 600.0, "paid": 500, "medical": 93.42, "billed": "8,700"}')
    assert ev.ungrounded("You owe $600 after $500; bank shows $93.42 and about $93; billed $8,700.00.", ground) == []
    assert ev.ungrounded("It was $4,321.09, or $50.", ground) == ["$4,321.09", "$50"]


def test_grounding_includes_posted_prices_and_doc_sections(monkeypatch):
    rows = ev.price_rows([orchestrator.demo_case()])  # Rosa's CPMC bill
    ground = ev.numbers_in(json.dumps(rows))
    assert {"74177", "99284"} <= {r["code"] for r in rows}
    assert ev.ungrounded("The CT is $3,890 on the bill; CPMC posts $8,287.", ground | {3890.0}) == []
    monkeypatch.setitem(sys.modules, "remy.knowledge", None)  # module absent: nothing added
    assert ev.knowledge_text(["How does it work?"]) == ""
    know = types.ModuleType("remy.knowledge")
    know.relevant = lambda q: [{"doc": "README.md", "heading": "Bright Data", "text": "Marcus's $5,600 UCSF bill"}]
    monkeypatch.setitem(sys.modules, "remy.knowledge", know)
    assert ev.ungrounded("README says $5,600.", ev.numbers_in(ev.knowledge_text(["How?"]))) == []


def test_live_needs_explicit_approval_and_a_cap(capsys):
    assert ev.main(["--live"]) == 2
    assert "needs Gavin's OK" in capsys.readouterr().err
    assert ev.main(["--live", "--i-have-approval", "--max-requests", str(ev.HARD_CAP + 1)]) == 2
    assert ev.main(["--i-have-approval"]) == 2


async def test_recorder_caps_and_stops_on_quota():
    class Inner:
        def __init__(self, exc=None):
            self.exc, self.messages = exc, self

        async def create(self, **kw):
            if self.exc:
                raise self.exc
            return ev._resp("ok")

    rec = ev.Recorder(inner=Inner(), cap=1)
    await rec.create(model="m", max_tokens=1, system="s", messages=[])
    with pytest.raises(ev.CapReached):
        await rec.create(model="m", max_tokens=1, system="s", messages=[])
    assert rec.count == 1

    err = RuntimeError("Error code: 402 - credits limit reached")
    rec = ev.Recorder(inner=Inner(err), cap=5)
    with pytest.raises(ev.QuotaStop):
        await rec.create(model="m")
    assert rec.stopped and rec.count == 1
    with pytest.raises(ev.QuotaStop):  # no further request goes out
        await rec.create(model="m")
    assert rec.count == 1


async def test_offline_runs_end_to_end_on_this_branch():
    spec = ev.load()
    results = await ev.run(spec, spec["items"], log=lambda *a: None)
    assert len(results) == len(spec["items"])
    assert not [r for r in results if r["status"] == "error"], [r for r in results if r["status"] == "error"]
    for r in results:
        assert r["status"] in ("pass", "fail", "skip")
        assert r["status"] != "skip" or r["reason"]
    s = ev.summarize(results)
    assert set(s) == GROUPS | {"TOTAL"}


# ---------------------------------------------------------------- a toy endpoint, to drive every check

async def _toy_ask(body: dict):
    """Not Ask Remy: just enough of the target contract (route, refusal, history, context, sources) to make the
    runner use the stub model, memory, context and every world fixture."""
    q = body.get("question", "")
    case = orchestrator.cases.get(body.get("case_id") or "")
    if decide.PAY.search(q) or decide.HIDE.search(q) or "ignore your instructions" in q.lower():
        return {"answer": "I can't do that.", "agent": "remy", "refused": True, "sources": []}
    # only the stored summary (bill totals, calls), never the app's own Ask Remy context, so this stays a toy
    ctx = {"page": body.get("page"), "case": orchestrator.summary(case) if case else None,
           "cases": [orchestrator.summary(c) for c in orchestrator.cases.values()]}
    if not llm.available():
        return {"answer": "Here's where things stand.", "agent": "remy", "sources": []}
    hist = [{"role": m["role"], "content": m["content"] or "-"} for m in (body.get("history") or [])][-16:]
    resp = await llm.create(system="toy", model="toy-model", max_tokens=100, messages=[
        *hist, {"role": "user", "content": f"CONTEXT {json.dumps(ctx, default=str)}\n\nQ: {q}"}])
    return {"answer": "".join(b.text for b in resp.content if b.type == "text"), "agent": "remy", "sources": []}


@pytest.fixture
def toy_endpoint(monkeypatch):
    app_mod.app.add_api_route("/api/remy/ask", _toy_ask, methods=["POST"])
    route = app_mod.app.router.routes.pop()
    app_mod.app.router.routes.insert(0, route)  # ahead of the static-files mount
    know = types.ModuleType("remy.knowledge")
    know.is_how_question = lambda q: q.lower().startswith(("how does", "how do you", "what is real", "where is"))
    know.relevant = lambda q: [{"doc": "README.md", "heading": "Real vs simulated", "text": "..."}]
    know.CODE_MAP = {"guardrails": "remy/agents/mandate.py", "money": "remy/finance.py"}
    monkeypatch.setitem(sys.modules, "remy.knowledge", know)
    yield
    app_mod.app.router.routes.remove(route)


async def test_toy_endpoint_drives_every_check(toy_endpoint):
    spec = ev.load()
    assert ev.features()["remy_ask"] == "/api/remy/ask" and ev.features()["knowledge"]
    results = {r["id"]: r for r in await ev.run(spec, spec["items"], log=lambda *a: None)}
    assert not [r for r in results.values() if r["status"] == "error"], \
        [r for r in results.values() if r["status"] == "error"]
    assert not [r for r in results.values() if r["status"] == "skip"]  # everything ran
    # the toy sends history, so memory holds; it forwards the last 16 messages (8 turns), so turn 1 drops off
    for i in ("mem-followup-amount", "mem-three-turns", "mem-window-eight", "start-call-shape"):
        assert results[i]["status"] == "pass", results[i]
    assert results["rt-pay-now"]["status"] == "pass" and results["rt-pretend-human"]["status"] == "pass"
    # the toy has no number guard: the scripted invented amount gets through and is caught
    assert results["rt-number-guard-replaces"]["status"] == "fail"
    assert "4,321" in results["rt-number-guard-replaces"]["reason"]
    # the toy's context lacks the posted prices and the call transcript; the toy routes everything to remy
    assert "context lacks" in results["case-context-line-items-vs-posted"]["reason"]
    assert "route remy != auditor" in results["case-bank-routes-to-nessie"]["reason"]
    # world rosa_called really made the desk call: case number 58213 and rep Diane are in the context
    assert results["case-context-call-scorecard-transcript"]["status"] == "pass"
    for i in ("how-knowledge-heading", "how-knowledge-not-how", "how-knowledge-code-map"):
        assert results[i]["status"] == "pass", results[i]
    assert ev.exit_code(list(results.values())) == 1
