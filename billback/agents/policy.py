"""Bright Data agent (the Policy agent): the hospital's own financial-assistance policy -> the patient's
discount and payment-plan terms. Reports in the shared format (report.py).

Where the policy comes from, in order:
  1. the policy database (policydb/, built by real Bright Data crawls: billback/policies/) - instant;
  2. a live crawl through the same pipeline (SERP API -> Web Unlocker -> Claude -> quote verification),
     stored for next time - slow, every stage shown on the dashboard;
  3. OFFLINE TEST FIXTURE (demo/policy_sample*.md) - only when Bright Data is not configured at all.
If the live crawl fails we say so and apply no discount; the fixture is never a silent stand-in.
Runs in parallel with the Auditor and waits for its transactions (income) and balance. Money math is
finance.py, not the model."""
import asyncio
from urllib.parse import urlparse

from .. import config, finance, llm
from ..events import bus
from ..integrations import brightdata
from ..policies import pipeline, registry
from ..policies.store import PolicyStore
from .report import Report

LIVE_CRAWL_TIMEOUT = 300  # seconds; SERP + page + PDFs + extraction. Slow is fine, it's shown step by step.

# ---- OFFLINE TEST FIXTURE: sample (not real) policies, used only when Bright Data isn't configured ----
FIXTURE_LABEL = "sample policy (offline test fixture, not a real hospital)"
FIXTURE_EXTRACT_SYSTEM = """You read a hospital's financial assistance / charity care policy and extract its eligibility
tiers based on income as a percent of the Federal Poverty Level (FPL).
Return JSON: {"tiers": [{"max_fpl_pct": number, "discount_pct": number, "applies_to": "all"|"insured"|"uninsured",
"min_oop_pct_income": number|null, "quote": "exact sentence from the text"}],
"no_collections_while_pending": true|false|null, "collections_quote": "exact sentence or empty",
"apply_how": "one sentence"}
Only use what the text states. Every quote must be copied verbatim. If the text has no FPL tiers, return "tiers": []."""
SAMPLE_TIERS = {"tiers": [
    {"max_fpl_pct": 200, "discount_pct": 100, "quote": "Patients whose family income is at or below 200% of the Federal Poverty Level (FPL) qualify for full charity care"},
    {"max_fpl_pct": 400, "discount_pct": 75, "quote": "above 200% and at or below 400% of the FPL qualify for discounted payment: the patient's balance is reduced by 75%"}],
    "no_collections_while_pending": True,
    "collections_quote": "The hospital will not send an account to collections while a financial assistance application is pending.",
    "apply_how": "Apply any time with 3 months of pay stubs or bank statements."}
HIGH_COST_TIERS = {"tiers": [
    {"max_fpl_pct": 400, "discount_pct": 100, "applies_to": "uninsured", "min_oop_pct_income": None,
     "quote": "Uninsured patients whose family income is at or below 400% of the Federal Poverty Level (FPL) qualify for full charity care"},
    {"max_fpl_pct": 400, "discount_pct": 100, "applies_to": "insured", "min_oop_pct_income": 10,
     "quote": "Insured patients whose family income is at or below 400% of the FPL qualify for full charity care (100% of the patient balance written off) if their out-of-pocket medical expenses in the prior 12 months exceed 10% of their family income"}],
    "no_collections_while_pending": True,
    "collections_quote": "The hospital will not send an account to collections while a financial assistance application is pending.",
    "apply_how": "Apply with proof of income and your insurance statement."}
SAMPLES = {"policy_sample.md": SAMPLE_TIERS, "policy_sample_high_cost.md": HIGH_COST_TIERS}

CRAWL_STEPS = {"discover": ("search", "Search for the policy (Bright Data SERP API)"),
               "fetch": ("fetch", "Fetch policy pages + PDFs (Bright Data Web Unlocker)"),
               "extract": ("verify", "Extract rules + verify every quote"),
               "verify": ("verify", "Extract rules + verify every quote"),
               "store": ("store", "Save to the policy database")}


def store() -> PolicyStore:
    return PolicyStore()


def _site(row: dict) -> str:
    pages = [d["url"] for d in row["documents"] if d["kind"] == "page"] or row["source_urls"]
    return urlparse(pages[0]).netloc.removeprefix("www.") if pages else ""


def _doc_name(url: str) -> str:
    """A proof line for a policy source; the link itself goes next to it."""
    last = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return f"Policy PDF: {last}" if last.lower().endswith(".pdf") else "Policy page"


def _primary_source(row: dict) -> str:
    docs = row["documents"]
    doc = next((d for d in docs if d["role"] == "policy_doc"), docs[0] if docs else None)
    return doc["url"] if doc else ""


def _from_row(row: dict, origin: str) -> dict:
    return {"found": row["extracted"], "source": _primary_source(row), "live": True, "origin": origin,
            "fetched_at": row["fetched_at"], "version": row["version"], "sources": row["source_urls"]}


def _lookup(db: PolicyStore, name: str) -> tuple[registry.Hospital | None, dict | None]:
    h = registry.match(name, db.hospitals())
    return h, (db.current(h.id) if h else None)


async def _live_crawl(cid: str, r: Report, db: PolicyStore, hosp: dict) -> dict | None:
    """Crawl a hospital that isn't in the database yet, streaming each stage to the dashboard."""
    h = registry.match(hosp["name"], list(registry.SEED)) or registry.Hospital(
        registry.slug(hosp["name"]), hosp["name"], city=hosp.get("city", ""))

    def on_event(kind: str, **e) -> None:
        if kind == "brightdata":
            bus.emit(cid, "brightdata", "policy", product=e["product"], stage=e["stage"], url=e["url"],
                     status=e["status"], bytes=e["bytes"], latency_ms=e["latency_ms"], ok=bool(e["ok"]))
        elif kind == "crawl_stage" and e["stage"] in CRAWL_STEPS:
            key, label = CRAWL_STEPS[e["stage"]]
            r.step(key, label, e["status"], e["text"])

    try:
        row = await asyncio.wait_for(pipeline.crawl_hospital(db, h, on_event=on_event), LIVE_CRAWL_TIMEOUT)
    except Exception as e:
        bus.emit(cid, "warning", "policy", text=f"Live policy crawl for {hosp['name']} failed ({type(e).__name__}: {e}). "
                                                "No verified policy, so no discount is applied.")
        return None
    db.write_export()
    return _from_row(row, "live_crawl")


async def _fixture(cid: str, hosp: dict) -> dict:
    """OFFLINE TEST FIXTURE. Only reached when Bright Data is not configured."""
    name = hosp.get("sample_policy") or "policy_sample.md"
    sample = SAMPLES.get(name, SAMPLE_TIERS)
    found = {**sample, "tiers": finance.clean_tiers(sample["tiers"])}
    if llm.available():
        try:
            text = (config.DEMO_DIR / name).read_text()
            got = await asyncio.wait_for(llm.json_out(FIXTURE_EXTRACT_SYSTEM, f"POLICY TEXT:\n{text}"), 90)
            if not isinstance(got, dict):
                raise ValueError("extraction did not return an object")
            found = {**got, "tiers": finance.clean_tiers(got.get("tiers"))}
        except Exception as e:
            bus.emit(cid, "warning", "policy", text=f"Fixture extraction failed ({type(e).__name__}); using its known tiers")
    return {"found": found, "source": FIXTURE_LABEL, "live": False, "origin": "offline_fixture",
            "fetched_at": None, "version": None, "sources": []}


async def find_policy(cid: str, r: Report, hosp: dict) -> dict:
    r.step("find", "Find assistance policy", "running", f"Looking up {hosp['name']} in the policy database")
    db = store()
    h, row = _lookup(db, hosp["name"])
    if row:  # status "no_tiers" counts too: a crawl found no fixed-percent rule, re-crawling won't change that
        got = _from_row(row, "policy_db")
        bus.emit(cid, "policy_source", "policy", origin="policy_db", hospital=h.name, hospital_id=h.id,
                 version=row["version"], fetched_at=row["fetched_at"], sources=row["source_urls"],
                 content_sha256=row["content_sha256"], tiers_verified=row["tiers_verified"])
        r.step("find", "Find assistance policy", "ok",
               f"From policy DB: fetched {row['fetched_at'][:10]} from {_site(row)} via Bright Data, "
               f"{row['tiers_verified']} verified quotes",
               [(_doc_name(u), u) for u in row["source_urls"][:3]])
        return got
    if brightdata.live() and brightdata.serp_live() and llm.available():
        r.step("find", "Find assistance policy", "running",
               f"{hosp['name']} isn't in the policy database yet: crawling live with Bright Data")
        got = await _live_crawl(cid, r, db, hosp)
        if got:
            bus.emit(cid, "policy_source", "policy", origin="live_crawl", hospital=hosp["name"],
                     version=got["version"], fetched_at=got["fetched_at"], sources=got["sources"])
            r.step("find", "Find assistance policy", "ok" if got["found"]["tiers"] else "clear",
                   f"Crawled live via Bright Data and saved to the policy DB · {urlparse(got['source']).netloc}",
                   [(_doc_name(u), u) for u in got["sources"][:3]])
            return got
        r.step("find", "Find assistance policy", "flag", "Live crawl failed: no verified policy")
        return {"found": {"tiers": []}, "source": "live crawl failed", "live": False, "origin": "failed",
                "fetched_at": None, "version": None, "sources": []}
    got = await _fixture(cid, hosp)
    bus.emit(cid, "policy_source", "policy", origin="offline_fixture", hospital=hosp["name"], sources=[])
    r.step("find", "Find assistance policy", "ok",
           "Bright Data not configured: using the OFFLINE TEST FIXTURE (sample policy, not a real hospital)")
    return got


def report(case_id: str) -> Report:
    return Report(case_id, "policy", "Bright Data agent", "Public rules: the hospital’s own financial-assistance and billing policy")


async def run(case: dict, txns_ready: asyncio.Future, owed_ready: asyncio.Future) -> dict:
    cid = case["id"]
    hosp = case["hospital"]
    r = report(cid)
    r.start(f"Checking {hosp['name']}'s financial-assistance policy")

    got = await find_policy(cid, r, hosp)
    found, source, live = got["found"], got["source"], got["live"]
    tiers = found["tiers"]
    if not tiers:
        r.step("find", "Find assistance policy", "clear", "No verified income-based discount rules")
    src = lambda t: t.get("source_url") or source  # noqa: E731

    r.step("income", "Estimate your income from payroll deposits", "running")
    txns = await txns_ready
    inc = finance.income(txns, today=finance._d(case["today"]))
    eob = case.get("eob") or {}
    elig = finance.charity_discount(inc["annual"], case["patient"]["household_size"], tiers,
                                    insured=bool(eob), out_of_pocket=float(eob.get("patient_responsibility") or 0))
    r.step("income", "Estimate your income from payroll deposits", "ok",
           f"{elig['fpl_pct']:.0f}% of the poverty line (${inc['annual']:,.0f}/yr, household of {elig['household_size']})",
           [(f"Payroll ${t['amount']:,.2f} on {t['date'][:10]}", f"bank · txn {t['id']}") for t in inc["deposits"][-3:]])
    bus.emit(cid, "evidence", "policy", label="Income vs poverty line",
             value=f"{elig['fpl_pct']}% of FPL (household {elig['household_size']}, {elig['fpl_year']} guidelines)",
             source=inc["source"])

    free = [t for t in tiers if t.get("discount_pct") == 100]
    r.step("free", "Check free care", "running")
    if elig["discount_pct"] == 100:
        why = (f" (your out-of-pocket is {elig['oop_pct_income']:.0f}% of income)"
               if elig["tier"].get("min_oop_pct_income") is not None else "")
        r.step("free", "Check free care", "win", f"You qualify for free care{why}",
               [(elig["tier"].get("quote", ""), src(elig["tier"]))])
    elif free:
        r.step("free", "Check free care", "clear", _why_not_free(elig, free),
               [(t.get("quote", ""), src(t)) for t in free[:1] if t.get("quote")])
    else:
        r.step("free", "Check free care", "clear", "This policy has no free-care tier")

    if elig["discount_pct"] != 100:
        r.step("tiers", "Check discount tiers", "running")
        r.step("tiers", "Check discount tiers", "win" if elig["discount_pct"] else "clear",
               f"You qualify for {elig['discount_pct']:.0f}% off" if elig["discount_pct"] else "No discount tier matched",
               [(elig["tier"].get("quote", ""), src(elig["tier"]))] if elig["discount_pct"] and elig["tier"].get("quote") else None)

    for t in tiers:
        bus.emit(cid, "policy_quote", "policy", quote=t.get("quote", ""), source=src(t), live=live,
                 verified=bool(t.get("verified")), origin=got["origin"],
                 matched=elig["tier"] is not None and t is elig["tier"])

    if not owed_ready.done():
        r.step("wait", "Wait for the Nessie agent", "wait", "Waiting for its balance")
    paid_info = await owed_ready
    owed = paid_info["owed"]
    r.step("wait", "Wait for the Nessie agent", "ok", f"Got it: ${owed:,.0f} owed")

    d = elig["discount_pct"]
    st = finance.settle(paid_info["responsibility"], paid_info["paid"], d)
    target = st["balance"]
    label = "Apply free care" if d == 100 else "Apply discount"
    r.step("apply", label, "running")
    paid = f" − ${paid_info['paid']:,.0f} paid" if paid_info["paid"] else ""
    end = f"you're owed ${st['refund']:,.0f} back" if st["refund"] else f"${target:,.0f}"
    if d == 100 and not paid_info["paid"]:
        text = f"${paid_info['responsibility']:,.0f} written off = $0"
    elif d:
        text = f"${paid_info['responsibility']:,.0f} share − {d:.0f}% = ${st['discounted_share']:,.0f}{paid} = {end}"
    else:
        text = f"No write-off: ${owed:,.0f} stays"
    r.step("apply", label, "ok" if d else "clear", text)
    if d:
        saved = owed - target + st["refund"]
        r.finding("discount", "Free care under their policy" if d == 100 else f"{d:.0f}% discount under their policy", saved,
                  [f"Your income is {elig['fpl_pct']:.0f}% of the poverty line ({inc['source']}).",
                   *([f"“{elig['tier']['quote']}”"] if elig["tier"].get("quote") else []),
                   *([f"You already paid ${paid_info['paid']:,.0f}, so they owe you ${st['refund']:,.0f} back."] if st["refund"] else [])],
                  src(elig["tier"]))

    terms = found.get("payment_plan") or {}
    plan = finance.payment_plan(target, inc["monthly"], policy_cap_pct=terms.get("max_pct_monthly_income"))
    if plan["months"]:
        r.step("plan", "Payment plan terms", "running")
        rules = [x for x in ("interest-free" if terms.get("interest_free") else "",
                             f"max {terms['max_pct_monthly_income']:g}% of monthly income"
                             if terms.get("max_pct_monthly_income") else "") if x]
        quotes = [(q, qs or source) for q, qs in ((terms.get("quote"), terms.get("source_url")),
                                                   (terms.get("cap_quote"), terms.get("cap_source_url"))) if q]
        r.step("plan", "Payment plan terms", "win" if rules else "ok",
               (f"Their policy: {', '.join(rules)} → " if rules else "")
               + f"${target:,.0f} as {plan['months']} × ${plan['monthly']:,.0f}/mo, no interest", quotes)
        for q, qs in quotes:
            bus.emit(cid, "policy_quote", "policy", quote=q, source=qs, live=live, verified=True,
                     origin=got["origin"], matched=True, topic="payment_plan")
        r.finding("plan", "Interest-free payment plan", 0,
                  [f"“{q}”" for q, _ in quotes] or ["Their billing policy allows an interest-free plan."],
                  quotes[0][1] if quotes else source, value=f"{plan['months']} × ${plan['monthly']:,.0f}/mo")

    result = ((f"Free care → ${target:,.0f}" if d == 100 else f"{d:.0f}% off → ${target:,.0f}") if d else "No discount")
    if st["refund"]:
        result += f" + ${st['refund']:,.0f} refund"
    if not d and plan["months"]:
        result = f"${target:,.0f}, interest-free plan"
    note = "charity-care discount" if d else _why_not_free(elig, free) if free else "no discount tier matched"
    r.finish(result, note)
    return {"policy": found, "eligibility": elig, "income": inc, "source": source, "live": live,
            "origin": got["origin"], "fetched_at": got["fetched_at"], "sources": got["sources"],
            "target": target, "refund": st["refund"], "discounted_share": st["discounted_share"],
            "plan": plan, "plan_terms": terms}


def _why_not_free(elig: dict, free: list[dict]) -> str:
    """Why a free-care tier didn't apply, in the patient's terms."""
    for t in free:
        who = t.get("applies_to") or "all"
        if (who == "insured") != elig["insured"] and who != "all":
            continue
        if elig["fpl_pct"] > t["max_fpl_pct"]:
            return f"Income is above the {t['max_fpl_pct']:.0f}% limit"
        need = t.get("min_oop_pct_income")
        if need is not None and elig["oop_pct_income"] <= need:
            return (f"Not eligible: the write-off needs medical bills over {need:g}% of income; "
                    f"yours are {elig['oop_pct_income']:.0f}%")
    return f"Income is above the {free[0]['max_fpl_pct']:.0f}% limit"
