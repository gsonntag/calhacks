"""Policy agent: finds the hospital's own financial-assistance policy (Bright Data) and extracts its
eligibility tiers with exact quotes (Claude). The discount maths is done in finance.py, not by the model."""
from .. import finance, llm
from ..events import bus
from ..harness import Agent, Tool, ToolRegistry
from ..integrations import brightdata

EXTRACT_SYSTEM = """You read a hospital's financial assistance / charity care policy and extract its eligibility
tiers based on income as a percent of the Federal Poverty Level (FPL).
Return JSON: {"tiers": [{"max_fpl_pct": number, "discount_pct": number, "quote": "exact sentence from the text"}],
"no_collections_while_pending": true|false|null, "collections_quote": "exact sentence or empty",
"apply_how": "one sentence"}
Only use what the text states. Every quote must be copied verbatim. If the text has no FPL tiers, return "tiers": []."""

FIND_SYSTEM = """You find a hospital's official financial assistance policy (FAP) page. Use web_search, then
fetch_page on the most official-looking result (the hospital's or health system's own domain, ideally the
policy document or plain-language summary). If a page doesn't contain eligibility rules, try another.
When you have a page with FPL-based eligibility rules, reply with just its URL."""


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
    import re
    m = re.search(r"https?://\S+", answer or "")
    return m.group(0).rstrip(").,") if m else None


async def run(case: dict, annual_income: float) -> dict:
    cid = case["id"]
    hosp = case["hospital"]
    bus.emit(cid, "agent_started", "policy", text=f"Looking up {hosp['name']}'s financial-assistance policy")

    source, text, live = "sample policy (offline)", None, False
    if brightdata.live():
        try:
            url = hosp.get("policy_url") or await _find_policy_url(case)
            if url:
                bus.emit(cid, "evidence", "policy", label="Fetching live", value=url, source="Bright Data Web Unlocker")
                text, source, live = await brightdata.fetch_text(url), url, True
        except Exception as e:
            bus.emit(cid, "warning", "policy", text=f"Live fetch failed ({e}); using sample policy")
    if text is None:
        text = brightdata.sample_policy()

    if llm.available():
        found = await llm.json_out(EXTRACT_SYSTEM, f"POLICY TEXT:\n{text[:30000]}", model=None)
    else:  # offline: the sample policy's tiers, transcribed
        found = {"tiers": [
            {"max_fpl_pct": 200, "discount_pct": 100, "quote": "Patients whose family income is at or below 200% of the Federal Poverty Level (FPL) qualify for full charity care"},
            {"max_fpl_pct": 400, "discount_pct": 75, "quote": "above 200% and at or below 400% of the FPL qualify for discounted payment: the patient's balance is reduced by 75%"}],
            "no_collections_while_pending": True,
            "collections_quote": "The hospital will not send an account to collections while a financial assistance application is pending.",
            "apply_how": "Apply any time with 3 months of pay stubs or bank statements."}

    elig = finance.charity_discount(annual_income, case["patient"]["household_size"], found.get("tiers", []))
    for t in found.get("tiers", []):
        bus.emit(cid, "policy_quote", "policy", quote=t.get("quote", ""), source=source, live=live,
                 matched=elig["tier"] is not None and t.get("max_fpl_pct") == elig["tier"]["max_fpl_pct"])
    bus.emit(cid, "evidence", "policy", label="Income vs poverty line",
             value=f"{elig['fpl_pct']}% of FPL (household of {elig['household_size']}, {elig['fpl_year']} guidelines)",
             source="Nessie payroll deposits")
    bus.emit(cid, "agent_finished", "policy",
             summary=f"Qualifies for {elig['discount_pct']}% off" if elig["discount_pct"] else "No discount tier matched")
    return {"policy": found, "eligibility": elig, "source": source, "live": live}
