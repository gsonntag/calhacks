"""Policy agent: finds the hospital's own financial-assistance policy (Bright Data) and extracts its
eligibility tiers with exact quotes (Claude). Runs in parallel with the Auditor: it fetches the policy
right away, then waits for the Auditor's transactions (income) and balance. The discount math is in
finance.py, not done by the model."""
import asyncio
import re
from urllib.parse import urlparse

from .. import config, finance, llm
from ..events import bus, step
from ..harness import Agent, Tool, ToolRegistry
from ..integrations import brightdata

EXTRACT_SYSTEM = """You read a hospital's financial assistance / charity care policy and extract its eligibility
tiers based on income as a percent of the Federal Poverty Level (FPL).
Return JSON: {"tiers": [{"max_fpl_pct": number, "discount_pct": number, "applies_to": "all"|"insured"|"uninsured",
"min_oop_pct_income": number|null, "quote": "exact sentence from the text"}],
"no_collections_while_pending": true|false|null, "collections_quote": "exact sentence or empty",
"apply_how": "one sentence"}
Only use what the text states. Every quote must be copied verbatim. If the text has no FPL tiers, return "tiers": [].
discount_pct = percent of the balance written off (100 = free). applies_to = who the tier is for (default "all").
min_oop_pct_income = for "high medical cost" rules, the percent of family income the patient's out-of-pocket
medical costs must exceed (e.g. 10), else null. Omit tiers without a numeric FPL upper limit and a numeric
percent discount."""

FIND_SYSTEM = """You find a hospital's official financial assistance policy (FAP) page. Use web_search, then
fetch_page on the most official-looking result (the hospital's or health system's own domain, ideally the
policy document or plain-language summary). If a page doesn't contain eligibility rules, try another.
When you have a page with FPL-based eligibility rules, reply with just its URL."""

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


async def _find_policy_url(case: dict) -> str | None:
    cid = case["id"]
    reg = ToolRegistry(cid, "policy")

    async def web_search(query: str) -> list:
        return await brightdata.search(query)

    async def fetch_page(url: str) -> str:
        return (await brightdata.fetch_text(url))[:6000]

    reg.add(Tool("web_search", "Google search via Bright Data SERP API", {
        "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}, web_search))
    reg.add(Tool("fetch_page", "Fetch a public web page via Bright Data Web Unlocker (returns text)", {
        "type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}, fetch_page))
    answer = await Agent(cid, "policy", FIND_SYSTEM, reg, max_steps=6).run(
        f"Find the financial assistance policy for: {case['hospital']['policy_search_query']}")
    m = re.search(r"https?://\S+", answer or "")
    return m.group(0).rstrip(").,") if m else None


LIVE_TIMEOUT = 45  # seconds for search + fetch before falling back to the sample policy


async def _extract(cid: str, text: str, sample: dict = SAMPLE_TIERS) -> tuple[dict, bool]:
    """Policy text -> ({"tiers": [clean numeric tiers], ...}, extracted_ok). Never raises: on failure it
    returns the sample tiers and False so the caller labels them as the sample policy."""
    if not llm.available():
        return {**sample, "tiers": finance.clean_tiers(sample["tiers"])}, True
    try:
        found = await asyncio.wait_for(llm.json_out(EXTRACT_SYSTEM, f"POLICY TEXT:\n{text[:30000]}"), 90)
        if not isinstance(found, dict):
            raise ValueError("extraction did not return an object")
    except Exception as e:
        bus.emit(cid, "warning", "policy", text=f"Policy extraction failed ({type(e).__name__}); using sample policy")
        return {**sample, "tiers": finance.clean_tiers(sample["tiers"])}, False
    return {**found, "tiers": finance.clean_tiers(found.get("tiers"))}, True


async def run(case: dict, txns_ready: asyncio.Future, owed_ready: asyncio.Future) -> dict:
    cid = case["id"]
    hosp = case["hospital"]
    bus.emit(cid, "agent_started", "policy", text=f"Checking {hosp['name']}'s financial-assistance policy")

    step(cid, "policy", "find", "Find assistance policy", "running")
    source, text, live = "sample policy (offline)", None, False
    # without a configured URL, finding the page needs the SERP zone too
    if brightdata.live() and (hosp.get("policy_url") or config.BRIGHTDATA_SERP_ZONE):
        async def _fetch():
            url = hosp.get("policy_url") or await _find_policy_url(case)
            if url:
                step(cid, "policy", "find", f"Reading {urlparse(url).netloc}…", "running")
                return await brightdata.fetch_text(url), url
            return None, None
        try:
            text, url = await asyncio.wait_for(_fetch(), LIVE_TIMEOUT)
            if text is not None:
                source, live = url, True
        except Exception as e:
            bus.emit(cid, "warning", "policy", text=f"Live fetch failed ({type(e).__name__}: {e}); using sample policy")
            text = None
    sample_file = hosp.get("sample_policy") or "policy_sample.md"
    sample = SAMPLES.get(sample_file, SAMPLE_TIERS)
    if text is None:
        text = brightdata.sample_policy(sample_file)
    found, ok = await _extract(cid, text, sample)
    if live and ok and not found["tiers"]:
        bus.emit(cid, "warning", "policy", text="Live policy had no FPL tiers; using sample policy")
        found, ok = await _extract(cid, brightdata.sample_policy(sample_file), sample)
    if not ok or not found["tiers"]:
        source, live = "sample policy (offline)", False
    tiers = found["tiers"]
    step(cid, "policy", "find", "Find assistance policy", "ok" if tiers else "clear",
         ((f"{hosp['policy_owner']}'s published policy (live)" if hosp.get("policy_owner")
           else "Found on the hospital's website (live)") if live else "Using the sample policy (offline)")
         if tiers else "No income-based discount rules found")

    step(cid, "policy", "income", "Estimate income", "running")
    txns = await txns_ready
    inc = finance.income(txns, today=finance._d(case["today"]))
    eob = case.get("eob") or {}
    elig = finance.charity_discount(inc["annual"], case["patient"]["household_size"], tiers,
                                    insured=bool(eob), out_of_pocket=float(eob.get("patient_responsibility") or 0))
    step(cid, "policy", "income", "Estimate income", "ok",
         f"{elig['fpl_pct']:.0f}% of the poverty line (${inc['annual']:,.0f}/yr from payroll deposits)")
    bus.emit(cid, "evidence", "policy", label="Income vs poverty line",
             value=f"{elig['fpl_pct']}% of FPL (household {elig['household_size']}, {elig['fpl_year']} guidelines)",
             source=inc["source"])

    free = [t for t in tiers if t.get("discount_pct") == 100]
    step(cid, "policy", "free", "Check free care", "running")
    await asyncio.sleep(0.4)
    if elig["discount_pct"] == 100:
        why = (f" (your out-of-pocket is {elig['oop_pct_income']:.0f}% of income)"
               if elig["tier"].get("min_oop_pct_income") is not None else "")
        step(cid, "policy", "free", "Check free care", "win", f"You qualify for free care{why}")
    elif free:
        step(cid, "policy", "free", "Check free care", "clear",
             f"Nothing found, income is above the {free[0]['max_fpl_pct']:.0f}% limit")
    else:
        step(cid, "policy", "free", "Check free care", "clear", "This policy has no free-care tier")

    if elig["discount_pct"] != 100:
        step(cid, "policy", "tiers", "Check discount tiers", "running")
        await asyncio.sleep(0.4)
        step(cid, "policy", "tiers", "Check discount tiers", "win" if elig["discount_pct"] else "clear",
             f"You qualify for {elig['discount_pct']:.0f}% off" if elig["discount_pct"] else "No discount tier matched")

    for t in tiers:
        bus.emit(cid, "policy_quote", "policy", quote=t.get("quote", ""), source=source, live=live,
                 matched=elig["tier"] is not None and t.get("max_fpl_pct") == elig["tier"]["max_fpl_pct"])

    if not owed_ready.done():
        step(cid, "policy", "wait", "Wait for Auditor", "wait", "Waiting for the Auditor's balance")
    got = await owed_ready
    owed = got["owed"]
    step(cid, "policy", "wait", "Wait for Auditor", "ok", f"Got it: ${owed:,.0f} owed")

    d = elig["discount_pct"]
    st = finance.settle(got["responsibility"], got["paid"], d)
    target = st["balance"]
    label = "Apply free care" if d == 100 else "Apply discount"
    step(cid, "policy", "apply", label, "running")
    await asyncio.sleep(0.3)
    paid = f" − ${got['paid']:,.0f} paid" if got["paid"] else ""
    end = f"you're owed ${st['refund']:,.0f} back" if st["refund"] else f"${target:,.0f}"
    if d == 100 and not got["paid"]:
        text = f"${got['responsibility']:,.0f} written off = $0"
    elif d:
        text = f"${got['responsibility']:,.0f} share − {d:.0f}% = ${st['discounted_share']:,.0f}{paid} = {end}"
    else:
        text = f"${owed:,.0f} stays"
    step(cid, "policy", "apply", label, "ok", text)

    result = ((f"Free care → ${target:,.0f}" if d == 100 else f"{d:.0f}% off → ${target:,.0f}") if d else "No discount")
    if st["refund"]:
        result += f" + ${st['refund']:,.0f} refund"
    note = "charity-care discount" if d else "income is above the policy's limits"
    bus.emit(cid, "agent_finished", "policy", summary=result, note=note)
    return {"policy": found, "eligibility": elig, "income": inc, "source": source, "live": live,
            "target": target, "refund": st["refund"], "discounted_share": st["discounted_share"]}
