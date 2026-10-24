"""Thin wrapper over the Anthropic SDK."""
import json
import re

from anthropic import AsyncAnthropic

from . import config

_client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY) if config.ANTHROPIC_API_KEY else None


def available() -> bool:
    return _client is not None


async def create(*, system: str, messages: list, tools: list | None = None, model: str | None = None,
                 max_tokens: int = 2048):
    if _client is None:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    kwargs = {"model": model or config.MODEL_SMART, "max_tokens": max_tokens, "system": system, "messages": messages}
    if tools:
        kwargs["tools"] = tools
    return await _client.messages.create(**kwargs)


async def text(system: str, prompt: str | list, model: str | None = None, max_tokens: int = 1500) -> str:
    resp = await create(system=system, messages=[{"role": "user", "content": prompt}], model=model, max_tokens=max_tokens)
    return "".join(b.text for b in resp.content if b.type == "text")


async def json_out(system: str, prompt: str | list, model: str | None = None, max_tokens: int = 3000) -> dict:
    """Ask for JSON only and parse the first object in the reply."""
    raw = await text(system + "\nRespond with a single JSON object and nothing else.", prompt, model, max_tokens)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError(f"no JSON in model output: {raw[:200]}")
    return json.loads(m.group(0))
