"""One-time Cartesia setup: phone number and the BillBack caller agent.

    uv run python scripts/setup_cartesia.py            # polling mode: no public URL needed
    PUBLIC_BASE_URL=https://... uv run python scripts/setup_cartesia.py   # also wires webhooks + outcome tool

Polling mode: we poll the call for its transcript and Claude extracts the outcome after the call, so
Cartesia never has to reach this server. Prints the ids to paste into .env. Re-run with --update to push
new instructions to the existing agent.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from billback import config  # noqa: E402
from billback.agents.caller import SYSTEM  # noqa: E402
from billback.integrations.cartesia import _req  # noqa: E402

WEBHOOK_INSTRUCTIONS = SYSTEM.replace("call record_outcome", "use the record_outcome tool")
POLLING_INSTRUCTIONS = SYSTEM.replace(
    "When the rep gives a case/reference number, call record_outcome. When the goals are done or the rep\n  ends it, call end_call and say a short goodbye.",
    "When the rep gives a case/reference number, repeat it back clearly to confirm it. When the goals are done\n  or the rep ends it, say a short goodbye and use end_call.")
TAIL = """
The patient is {{patient_name}}. Internal case id: {{case_id}} (never say it out loud).
CASE BRIEF (JSON): {{case_brief}}
Wait for the representative to say hello, then introduce yourself."""


async def main() -> None:
    if not config.CARTESIA_API_KEY:
        sys.exit("Set CARTESIA_API_KEY in .env")
    webhooks = config.PUBLIC_BASE_URL.startswith("https://")
    tools, hook_id = [], None
    if webhooks and not (config.CARTESIA_WEBHOOK_SECRET and config.CARTESIA_TOOL_TOKEN):
        sys.exit("Set CARTESIA_WEBHOOK_SECRET and CARTESIA_TOOL_TOKEN in .env to fresh random strings first")
    if webhooks:
        tool_id, hook_id = await _wire_webhooks(config.PUBLIC_BASE_URL.rstrip("/"))
        tools = [{"id": tool_id}]
    print("mode:", "webhooks" if webhooks else "polling (no public URL needed)")

    agent_cfg = {"instructions": (WEBHOOK_INSTRUCTIONS if webhooks else POLLING_INSTRUCTIONS) + TAIL,
                 "initial_message": None,
                 "model": {"id": "claude-haiku-5.5"},
                 "audio": {"output": {"voice_id": config.CARTESIA_VOICE_ID}},
                 "system_tools": {"end_call": {}}, "tools": tools}
    if "--update" in sys.argv and config.CARTESIA_AGENT_ID:
        agent_id = config.CARTESIA_AGENT_ID
        await _req("PATCH", f"/v1/agents/{agent_id}", {"config": agent_cfg})
    else:
        agent = await _req("POST", "/v1/agents", {"name": "BillBack Caller", "config": agent_cfg})
        agent_id = agent.get("id") or agent.get("agent_id")
    if hook_id:
        await _req("PATCH", f"/agents/{agent_id}", {"webhook_id": hook_id})
    print("agent", agent_id)

    number_id = config.CARTESIA_FROM_NUMBER_ID
    if not number_id:
        num = await _req("POST", "/agents/phone-numbers/provision", {"label": "BillBack"})
        number_id = num.get("id") or num.get("phone_number_id")
        print("number", num)

    print("\nAdd to .env:")
    print(f"CARTESIA_AGENT_ID={agent_id}\nCARTESIA_FROM_NUMBER_ID={number_id}")


async def _wire_webhooks(base: str) -> tuple[str, str]:
    tool = await _req("POST", "/v1/agents/tools", {
        "type": "webhook", "name": "record_outcome",
        "description": "Record what the hospital agreed to and the case/reference number. Call it as soon as the rep gives a case number, and again if anything changes.",
        "pre_tool_speech": "none", "execution_mode": "immediate", "response_timeout_secs": 10,
        "api_schema": {
            "url": f"{base}/api/cartesia/tools/record_outcome", "method": "POST",
            "request_body_schema": {"type": "object", "properties": {
                "case_id": {"type": "string", "dynamic_variable": "case_id"},
                "case_number": {"type": "string"}, "rep_name": {"type": "string"},
                "account_on_hold": {"type": "boolean"}, "assistance_review_opened": {"type": "boolean"},
                "adjustment_applied": {"type": "boolean"}, "payment_applied": {"type": "boolean"},
                "next_step": {"type": "string"}},
                "required": ["next_step", "account_on_hold", "assistance_review_opened"]},
            "authentication": {"mode": "bearer", "token": {"type": "secret", "secret_value": config.CARTESIA_TOOL_TOKEN}}}})
    tool_id = tool.get("id") or tool.get("tool_id")
    print("tool", tool_id)

    hook = await _req("POST", "/agents/webhooks", {"url": f"{base}/webhooks/cartesia", "secret": config.CARTESIA_WEBHOOK_SECRET})
    hook_id = hook.get("id") or hook.get("webhook_id")
    print("webhook", hook_id)
    return tool_id, hook_id


if __name__ == "__main__":
    asyncio.run(main())
