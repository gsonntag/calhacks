"""Bright Data agent (the Policy agent): the hospital's own financial-assistance policy -> the patient's
discount and payment-plan terms. Reports in the shared format (report.py).

Where the policy comes from, in order:
  1. the policy database (policydb/, built by real Bright Data crawls: billback/policies/) - instant. A stored
     policy that is stale (`staleness`: "no tiers" older than NO_TIERS_RETRY_HOURS, or anything older than
     STALE_DAYS) is first refreshed live (`refresh`); if that fails, the stored copy is used and labelled so;
  2. a live crawl through the same pipeline (SERP API -> Web Unlocker -> Claude -> quote verification),
     stored for next time - slow, every request streamed to the case page (`streamer`);
  3. OFFLINE TEST FIXTURE (demo/policy_sample*.md) - only when Bright Data is not configured at all,
     and never in judging mode (BILLBACK_REQUIRE_LIVE=1).
If the live crawl fails we say so and apply no discount; the fixture is never a silent stand-in.
"Recheck now" on the case page (`recheck_case`) refreshes the case's hospital on demand. Every live crawl or
recheck from a case is capped at LIVE_MAX_REQUESTS Bright Data requests, retries included.
Runs in parallel with the Auditor and waits for its transactions (income) and balance. Money math is
finance.py, not the model."""
import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from .. import billcheck, config, finance, llm
from ..events import bus
from ..integrations import brightdata
from ..policies import monitor, pipeline, registry
from ..policies.crawl import HARD_CAP
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
               "store": ("store", "Save to the policy database"),
               "recheck": ("recheck", "Refetch the stored policy documents (Bright Data Web Unlocker)")}
LIVE_MAX_REQUESTS = HARD_CAP  # per live crawl or recheck from a case, retries included (policies/crawl.py)
STALE_DAYS = 30               # a stored policy older than this is rechecked before a case uses it
NO_TIERS_RETRY_HOURS = 6      # a stored "no tiers" result is re-crawled at most this often
OUTCOME_TEXT = {"found": "Found", "empty": "Empty: nothing usable came back", "blocked": "Blocked",
                "timeout": "Timed out", "rate_limited": "Rate-limited", "not_found": "Not found",
                "server_error": "Server error", "error": "Failed"}


def store() -> PolicyStore:
    return PolicyStore()


def can_crawl() -> bool:
    return brightdata.live() and brightdata.serp_live() and llm.available()


def _when(ts: str) -> datetime:
    t = datetime.fromisoformat(ts)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def staleness(db: PolicyStore, row: dict, now: datetime | None = None) -> str | None:
    """Why the stored policy should be refreshed before a case relies on it, or None. A crawl that found no
    verified tiers is retried (Google's results and the ranking change; Saint Francis's only hit was a Spanish
    form), but at most every NO_TIERS_RETRY_HOURS so every case doesn't pay for it; any policy is rechecked
    once it is STALE_DAYS old. "Last tried" counts failed attempts and unchanged rechecks too."""
    now = now or datetime.now(timezone.utc)
    tries = [row["fetched_at"], *[c["checked_at"] for c in db.checks(row["hospital_id"])[:1]]]
    last = max(_when(t) for t in tries)
    age = now - last
    if row["status"] == "no_tiers" and age >= timedelta(hours=NO_TIERS_RETRY_HOURS):
        return f"no verified discount tiers on file (last tried {last:%Y-%m-%d})"
    if age >= timedelta(days=STALE_DAYS):
        return f"last checked {age.days} days ago"
    return None


def _short(url: str) -> str:
    if url.startswith("google: "):
        return url
    u = urlparse(url)
    tail = u.path.rstrip("/").rsplit("/", 1)[-1]
    return u.netloc.removeprefix("www.") + (f"/…/{tail}" if tail else "")


def streamer(cid: str, r: Report):
    """Crawl events -> lines in the Bright Data agent's panel: one line per request (which tool, which URL, what
    came back), one per fallback, and the pipeline stages with their proof (choices, verified quotes)."""
    def on_event(kind: str, **e) -> None:
        if kind == "brightdata_request":
            tool = "SERP API" if e["product"] == brightdata.SERP else "Web Unlocker"
            r.step(f"bd-{e['n']}", f"Bright Data {tool} · {_short(e['url'])}", "running",
                   f"Request {e['n']} of at most {e['cap']}" + (" (retry)" if e.get("attempt", 1) > 1 else ""))
        elif kind == "brightdata":
            bus.emit(cid, "brightdata", "policy", product=e["product"], stage=e["stage"], url=e["url"],
                     status=e["status"], bytes=e["bytes"], latency_ms=e["latency_ms"], ok=bool(e["ok"]),
                     outcome=e.get("outcome"), n=e.get("n"), cap=e.get("cap"), retrying=e.get("retrying"))
            if "n" not in e:
                return
            tool = "SERP API" if e["product"] == brightdata.SERP else "Web Unlocker"
            out = e.get("outcome") or ("found" if e["ok"] else "error")
            secs = (e.get("latency_ms") or 0) / 1000
            text = OUTCOME_TEXT.get(out, out)
            if out == "found":
                text += f": {(e.get('bytes') or 0) / 1024:,.0f} KB in {secs:.1f} s"
            elif out == "timeout":
                text += f" after {secs:.0f} s"
            elif out != "empty" and e.get("status"):
                text += f" (HTTP {e['status']})"
            if e.get("retrying"):
                text += " → retrying once"
            r.step(f"bd-{e['n']}", f"Bright Data {tool} · {_short(e['url'])}", "ok" if out == "found" else "flag",
                   f"{text} · request {e['n']} of at most {e['cap']}",
                   [(e.get("error") or e["url"], e["url"] if e["url"].startswith("http") else "")])
        elif kind == "crawl_fallback":
            r.step(f"fallback-{e['n']}", "Fallback", "flag", e["text"])
        elif kind == "budget":
            r.step("budget", "Request cap", "flag", f"Stopped at the cap of {e['cap']} Bright Data requests")
        elif kind == "crawl_stage" and e["stage"] in CRAWL_STEPS:
            key, label = CRAWL_STEPS[e["stage"]]
            r.step(key, label, e["status"], e["text"], [tuple(p) for p in e.get("proof") or []])
        elif kind == "policy_changed":
            bus.emit(cid, "policy_changed", "policy", version=e["version"], previous=e["previous"], diff=e["diff"])
    return on_event


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
    """Crawl a hospital that isn't in the database yet, streaming each request and stage to the case page."""
    h = registry.match(hosp["name"], list(registry.SEED)) or registry.Hospital(
        registry.slug(hosp["name"]), hosp["name"], city=hosp.get("city", ""))
    try:
        row = await asyncio.wait_for(pipeline.crawl_hospital(db, h, on_event=streamer(cid, r),
                                                             max_requests=LIVE_MAX_REQUESTS), LIVE_CRAWL_TIMEOUT)
    except Exception as e:
        db.add_check(h.id, None, False, None, f"live crawl failed: {type(e).__name__}: {e}", None)
        bus.emit(cid, "warning", "policy", text=f"Live policy crawl for {hosp['name']} failed ({_why(e)}). "
                                                "No verified policy, so no discount is applied.")
        return None
    db.write_export()
    return _from_row(row, "live_crawl")


def _why(e: Exception) -> str:
    if isinstance(e, asyncio.TimeoutError):
        return f"no answer within {LIVE_CRAWL_TIMEOUT} s"
    return f"{type(e).__name__}: {e}"


async def refresh(cid: str, r: Report, db: PolicyStore, h: registry.Hospital, row: dict) -> dict | None:
    """Bring a stored policy up to date, live, streaming every request. A "no tiers" policy is crawled again from
    Google (the URLs on record found nothing; they're the fallback if Google can't be reached); any other is
    rechecked: the same documents refetched and hashed, re-extracted only if they changed. Returns the policy
    to use, or None when the live attempt failed (the caller falls back to the stored copy and says so)."""
    on_event = streamer(cid, r)
    try:
        if row["status"] == "no_tiers":
            new = await asyncio.wait_for(pipeline.crawl_hospital(db, h, on_event=on_event, known=row["discovery"],
                                                                 max_requests=LIVE_MAX_REQUESTS), LIVE_CRAWL_TIMEOUT)
            origin = "live_crawl"
        else:
            await asyncio.wait_for(monitor.recheck(db, h.id, on_event=on_event, max_requests=LIVE_MAX_REQUESTS),
                                   LIVE_CRAWL_TIMEOUT)
            new, origin = db.current(h.id), "live_recheck"
    except Exception as e:
        db.add_check(h.id, None, False, None, f"live refresh failed: {type(e).__name__}: {e}", None)
        r.step("refresh", "Refresh the stored policy", "flag",
               f"Live check failed ({_why(e)}): using the stored copy from {row['fetched_at'][:10]}")
        return None
    db.write_export()
    return _from_row(new, origin)


async def _fixture(cid: str, hosp: dict) -> dict:
    """OFFLINE TEST FIXTURE. Only reached when Bright Data is not configured, never in judging mode."""
    if config.REQUIRE_LIVE:
        raise RuntimeError("sample policies are off in judging mode (BILLBACK_REQUIRE_LIVE)")
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
    stale = staleness(db, row) if row else None
    if stale and can_crawl():
        r.step("find", "Find assistance policy", "running",
               f"{h.name} is in the policy database, but {stale}: checking live with Bright Data")
        got = await refresh(cid, r, db, h, row)
        if got:
            bus.emit(cid, "policy_source", "policy", origin=got["origin"], hospital=h.name, hospital_id=h.id,
                     version=got["version"], fetched_at=got["fetched_at"], sources=got["sources"])
            r.step("find", "Find assistance policy", "ok" if got["found"]["tiers"] else "clear",
                   f"Checked live via Bright Data ({stale}): version {got['version']}, "
                   f"{len(got['found']['tiers'])} verified tier{'s' if len(got['found']['tiers']) != 1 else ''}",
                   [(_doc_name(u), u) for u in got["sources"][:3]])
            return got
    if row:
        got = _from_row(row, "policy_db")
        bus.emit(cid, "policy_source", "policy", origin="policy_db", hospital=h.name, hospital_id=h.id,
                 version=row["version"], fetched_at=row["fetched_at"], sources=row["source_urls"],
                 content_sha256=row["content_sha256"], tiers_verified=row["tiers_verified"])
        old = (f" · stored copy ({stale}; a live check needs Bright Data keys)" if stale and not can_crawl()
               else " · stored copy: the live check failed" if stale else "")
        r.step("find", "Find assistance policy", "flag" if stale and can_crawl() else "ok",
               f"From policy DB: fetched {row['fetched_at'][:10]} from {_site(row)} via Bright Data, "
               f"{row['tiers_verified']} verified quotes{old}",
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
    if config.REQUIRE_LIVE:  # judging mode: a sample policy must never stand in for a real one
        bus.emit(cid, "warning", "policy", text=f"Judging mode: Bright Data can't crawl {hosp['name']} right now, "
                                                "and sample policies are off. No discount is applied.")
        r.step("find", "Find assistance policy", "flag", "Judging mode: no live policy, sample policies are off")
        return {"found": {"tiers": []}, "source": "no live policy (judging mode)", "live": False, "origin": "failed",
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
    insured = billcheck.insured(case)  # explicit: an empty or missing insurance statement means self-pay
    oop = float((case.get("eob") or {}).get("patient_responsibility") or 0) if insured else 0.0
    elig = finance.charity_discount(inc["annual"], case["patient"]["household_size"], tiers,
                                    insured=insured, out_of_pocket=oop)
    bills = finance.monthly_bills(txns, today=finance._d(case["today"]), bill=case["bill"])
    inc.update(bills_monthly=bills["monthly"], bills_source=bills["source"])
    gross = finance.gross_check(inc, case["patient"]["household_size"], tiers, insured, oop, elig)
    elig["gross_check"] = gross
    r.step("income", "Estimate your income from payroll deposits", "flag" if gross else "ok",
           f"{elig['fpl_pct']:.0f}% of the poverty line (${inc['annual']:,.0f}/yr take-home, household of "
           f"{elig['household_size']})" + (f". {gross['text']}" if gross else ""),
           [(f"Payroll ${t['amount']:,.2f} on {t['date'][:10]}", f"bank · txn {t['id']}") for t in inc["deposits"][-3:]]
           + [(f"{t.get('description') or 'Deposit'} ${t['amount']:,.2f} on {t['date'][:10]}", f"bank · txn {t['id']}")
              for t in inc["other_deposits"][-3:]])
    if gross:
        bus.emit(cid, "warning", "policy", text=gross["text"])
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
    plan = finance.payment_plan(target, inc["monthly"], policy_cap_pct=terms.get("max_pct_monthly_income"),
                                monthly_bills=inc["bills_monthly"])
    if plan["months"]:
        r.step("plan", "Payment plan terms", "running")
        rules = [x for x in ("interest-free" if terms.get("interest_free") else "",
                             f"max {terms['max_pct_monthly_income']:g}% of monthly income"
                             if terms.get("max_pct_monthly_income") else "") if x]
        quotes = [(q, qs or source) for q, qs in ((terms.get("quote"), terms.get("source_url")),
                                                   (terms.get("cap_quote"), terms.get("cap_source_url"))) if q]
        r.step("plan", "Payment plan terms", "win" if rules else "ok",
               (f"Their policy: {', '.join(rules)} → " if rules else "")
               + f"${target:,.2f} as {plan['text']}, no interest", quotes)
        for q, qs in quotes:
            bus.emit(cid, "policy_quote", "policy", quote=q, source=qs, live=live, verified=True,
                     origin=got["origin"], matched=True, topic="payment_plan")
        r.finding("plan", "Interest-free payment plan", 0,
                  [f"“{q}”" for q, _ in quotes] or ["Their billing policy allows an interest-free plan."],
                  quotes[0][1] if quotes else source, value=plan["text"])

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


# ---- "Recheck now" on the case page ----

class RecheckRefused(RuntimeError):
    pass


class _Run(Report):
    """The same panel, with this recheck's lines kept apart from earlier runs' (request 1 of a recheck must
    not overwrite request 1 of the first crawl)."""
    def __init__(self, base: Report, prefix: str):
        super().__init__(base.case_id, base.agent, base.name, base.covers)
        self.prefix = prefix

    def step(self, key: str, *a, **k) -> None:
        super().step(self.prefix + key, *a, **k)


def _rules(tiers: list[dict]) -> set[tuple]:
    return {(t.get("max_fpl_pct"), t.get("discount_pct"), t.get("applies_to") or "all", t.get("min_oop_pct_income"))
            for t in tiers or []}


async def recheck_case(case: dict) -> dict:
    """Check the case's hospital live, now, and stream it into the Bright Data agent's panel. A stored policy is
    rechecked (or crawled again from Google if it has no tiers); a hospital not in the database is crawled.
    The result says whether the rules this case's plan uses still hold. It never changes the plan by itself:
    a changed policy is shown, and a new case applies it (the numbers come only from a full Review)."""
    if not can_crawl():
        raise RecheckRefused("Live recheck needs Bright Data (API key, SERP and Unlocker zones) and Claude keys "
                             "on this server")
    if not case.get("facts"):
        raise RecheckRefused("Wait for the Review to finish first")
    cid, hosp = case["id"], case["hospital"]
    r = _Run(report(cid), f"re{len(case.get('policy_rechecks') or []) + 1}-")
    bus.emit(cid, "agent_reopened", "policy", text=f"Rechecking {hosp['name']} live")
    r.step("recheck-start", "Recheck now", "running",
           f"Checking {hosp['name']} live with Bright Data (at most {LIVE_MAX_REQUESTS} requests)")
    db = store()
    h, row = _lookup(db, hosp["name"])
    got = await (refresh(cid, r, db, h, row) if row else _live_crawl(cid, r, db, hosp))
    used = ((case["facts"].get("pol") or {}).get("policy") or {}).get("tiers") or []
    if not got:
        summary = "Live recheck failed: the plan keeps the policy it used"
        r.step("recheck-start", "Recheck now", "flag", summary)
        out = {"ok": False, "changed": None}
    else:
        new = got["found"]["tiers"]
        changed = _rules(new) != _rules(used)
        summary = (f"Policy changed (version {got['version']}): start a new case to apply it; this plan still "
                   f"uses the rules it was built on" if changed
                   else f"Still holds: version {got['version']} has the same rules this plan uses")
        r.step("recheck-start", "Recheck now", "flag" if changed else "ok", summary,
               [(f"“{t.get('quote', '')}”", t.get("source_url") or got["source"]) for t in new[:3] if t.get("quote")])
        out = {"ok": True, "changed": changed, "version": got["version"], "origin": got["origin"],
               "tiers": len(new)}
    case.setdefault("policy_rechecks", []).append({**out, "at": datetime.now(timezone.utc).isoformat()})
    before = next((e["summary"] for e in reversed(bus.history(cid))
                   if e["kind"] == "agent_finished" and e["agent"] == "policy"), summary)
    r.finish(before, summary)
    return out
