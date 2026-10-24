"""Thin wrapper over the Anthropic SDK."""
import json
import re

from anthropic import AsyncAnthropic

from . import config

# A hung request must not freeze a live call turn for the SDK's default 10 minutes.
_client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY, timeout=90) if config.ANTHROPIC_API_KEY else None


def available() -> bool:
    return _client is not None


def _empty(resp) -> bool:
    return not any((b.type == "text" and b.text.strip()) or b.type == "tool_use" for b in resp.content)


async def create(*, system: str, messages: list, tools: list | None = None, model: str | None = None,
                 max_tokens: int = 8000, effort: str | None = "medium", **extra):
    """Thinking tokens count against max_tokens on the 5.5 models, so budgets are generous and effort is
    explicit. If a reply hits max_tokens with no text and no tool call, retry once with double the budget."""
    if _client is None:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    kwargs = {"model": model or config.MODEL_SMART, "max_tokens": max_tokens, "system": system, "messages": messages}
    if tools:
        kwargs["tools"] = tools
    if effort:
        kwargs["output_config"] = {"effort": effort}
    kwargs.update(extra)
    resp = await _client.messages.create(**kwargs)
    if resp.stop_reason == "max_tokens" and _empty(resp):
        kwargs["max_tokens"] = max_tokens * 2
        resp = await _client.messages.create(**kwargs)
    return resp


async def text(system: str, prompt: str | list, model: str | None = None, max_tokens: int = 8000) -> str:
    resp = await create(system=system, messages=[{"role": "user", "content": prompt}], model=model, max_tokens=max_tokens)
    return "".join(b.text for b in resp.content if b.type == "text")


async def json_out(system: str, prompt: str | list, model: str | None = None, max_tokens: int = 8000) -> dict:
    """Ask for JSON only and parse the first object in the reply."""
    raw = await text(system + "\nRespond with a single JSON object and nothing else.", prompt, model, max_tokens)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError(f"no JSON in model output: {raw[:200]}")
    return json.loads(m.group(0))
