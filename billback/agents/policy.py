"""Policy agent: finds the hospital's own financial-assistance policy (Bright Data) and extracts its
eligibility tiers with exact quotes (Claude). Runs in parallel with the Auditor: it fetches the policy
right away, then waits for the Auditor's transactions (income) and balance. The discount math is in
finance.py, not done by the model."""
import asyncio
import re
from urllib.parse import urlparse

from .. import finance, llm
from ..events import bus, step
from ..harness import Agent, Tool, ToolRegistry
from ..integrations import brightdata

EXTRACT_SYSTEM = """You read a hospital's financial assistance / charity care policy and extract its eligibility
tiers based on income as a percent of the Federal Poverty Level (FPL).
Return JSON: {"tiers": [{"max_fpl_pct": number, "discount_pct": number, "quote": "exact sentence from the text"}],
"no_collections_while_pending": true|false|null, "collections_quote": "exact sentence or empty",
"apply_how": "one sentence"}
Only use what the text states. Every quote must be copied verbatim. If the text has no FPL tiers, return "tiers": [].
discount_pct = percent of the balance written off (100 = free). Omit tiers without a numeric FPL upper limit and
a numeric percent discount."""

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


async def _extract(cid: str, text: str) -> tuple[dict, bool]:
    """Policy text -> ({"tiers": [clean numeric tiers], ...}, extracted_ok). Never raises: on failure it
    returns the sample tiers and False so the caller labels them as the sample policy."""
    if not llm.available():
        return {**SAMPLE_TIERS, "tiers": finance.clean_tiers(SAMPLE_TIERS["tiers"])}, True
    try:
        found = await asyncio.wait_for(llm.json_out(EXTRACT_SYSTEM, f"POLICY TEXT:\n{text[:30000]}"), 90)
        if not isinstance(found, dict):
            raise ValueError("extraction did not return an object")
    except Exception as e:
        bus.emit(cid, "warning", "policy", text=f"Policy extraction failed ({type(e).__name__}); using sample policy")
        return {**SAMPLE_TIERS, "tiers": finance.clean_tiers(SAMPLE_TIERS["tiers"])}, False
    return {**found, "tiers": finance.clean_tiers(found.get("tiers"))}, True


async def run(case: dict, txns_ready: asyncio.Future, owed_ready: asyncio.Future) -> dict:
    cid = case["id"]
    hosp = case["hospital"]
    bus.emit(cid, "agent_started", "policy", text=f"Checking {hosp['name']}'s financial-assistance policy")

    step(cid, "policy", "find", "Find assistance policy", "running")
    source, text, live = "sample policy (offline)", None, False
    if brightdata.live():
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
    if text is None:
        text = brightdata.sample_policy()
    found, ok = await _extract(cid, text)
    if live and ok and not found["tiers"]:
        bus.emit(cid, "warning", "policy", text="Live policy had no FPL tiers; using sample policy")
        found, ok = await _extract(cid, brightdata.sample_policy())
    if not ok or not found["tiers"]:
        source, live = "sample policy (offline)", False
    tiers = found["tiers"]
    step(cid, "policy", "find", "Find assistance policy", "ok" if tiers else "clear",
         ("Found on the hospital's website (live)" if live else "Using the sample policy (offline)")
         if tiers else "No income-based discount rules found")

    step(cid, "policy", "income", "Estimate income", "running")
    txns = await txns_ready
    inc = finance.income(txns, today=finance._d(case["today"]))
    elig = finance.charity_discount(inc["annual"], case["patient"]["household_size"], tiers)
    step(cid, "policy", "income", "Estimate income", "ok",
         f"{elig['fpl_pct']:.0f}% of the poverty line (${inc['annual']:,.0f}/yr from payroll deposits)")
    bus.emit(cid, "evidence", "policy", label="Income vs poverty line",
             value=f"{elig['fpl_pct']}% of FPL (household {elig['household_size']}, {elig['fpl_year']} guidelines)",
             source=inc["source"])

    free = [t for t in tiers if t.get("discount_pct") == 100]
    step(cid, "policy", "free", "Check free care", "running")
    await asyncio.sleep(0.4)
    if elig["discount_pct"] == 100:
        step(cid, "policy", "free", "Check free care", "win", "You qualify for free care")
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
    owed = await owed_ready
    step(cid, "policy", "wait", "Wait for Auditor", "ok", f"Got it: ${owed:,.0f} owed")

    target = finance.apply_discount(owed, elig["discount_pct"])
    label = "Apply free care" if elig["discount_pct"] == 100 else "Apply discount"
    step(cid, "policy", "apply", label, "running")
    await asyncio.sleep(0.3)
    step(cid, "policy", "apply", label, "ok",
         f"${owed:,.0f} − {elig['discount_pct']:.0f}% = ${target:,.0f}" if elig["discount_pct"] else f"${owed:,.0f} stays")

    result = ("Free care → $0" if elig["discount_pct"] == 100
              else f"{elig['discount_pct']:.0f}% off → ${target:,.0f}" if elig["discount_pct"] else "No discount")
    note = "charity-care discount" if elig["discount_pct"] else "income is above the policy's limits"
    bus.emit(cid, "agent_finished", "policy", summary=result, note=note)
    return {"policy": found, "eligibility": elig, "income": inc, "source": source, "live": live, "target": target}
