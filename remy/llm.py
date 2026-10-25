"""Thin wrapper over the Anthropic SDK."""
import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator

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


def cached(system: str) -> list[dict]:
    """A system prompt as one cache breakpoint. Tools render before system, so this caches tools + system."""
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


class Stream:
    """A streamed Messages call that starts now, in the background, and buffers its text deltas.

    Built for voice turns: the call can start speculatively (before the speaker's turn is final) and be
    thrown away with cancel(), or be consumed with deltas() and then read with .final (the whole Message,
    including tool_use blocks). deltas() can be consumed once; it re-raises the call's error at the end."""

    def __init__(self, **kwargs) -> None:
        if _client is None:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        self.final = None
        self.error: BaseException | None = None
        self._q: asyncio.Queue = asyncio.Queue()
        self._task = asyncio.create_task(self._run(kwargs))

    async def _run(self, kwargs: dict) -> None:
        t0, first = time.monotonic(), None
        try:
            async with _client.messages.stream(**kwargs) as s:
                async for ev in s:
                    if ev.type == "text" and ev.text:
                        first = first or time.monotonic()
                        self._q.put_nowait(ev.text)
                self.final = await s.get_final_message()
            u = self.final.usage
            logging.getLogger("remy.latency").debug(
                "claude first_token=%sms total=%dms input=%s cache_read=%s cache_write=%s out=%s stop=%s",
                round((first - t0) * 1000) if first else "-", round((time.monotonic() - t0) * 1000), u.input_tokens,
                u.cache_read_input_tokens, u.cache_creation_input_tokens, u.output_tokens, self.final.stop_reason)
        except asyncio.CancelledError:
            self.error = asyncio.CancelledError()
            raise
        except Exception as e:
            self.error = e
        finally:
            self._q.put_nowait(None)

    async def deltas(self) -> AsyncIterator[str]:
        while (d := await self._q.get()) is not None:
            yield d
        if self.error is not None:
            raise self.error if isinstance(self.error, Exception) else RuntimeError("stream cancelled")

    def cancel(self) -> None:
        self._task.cancel()


_ABBREV = {"dr", "mr", "mrs", "ms", "st", "no", "vs", "etc", "jr", "sr", "inc", "dept", "approx", "e.g", "i.e",
           "u.s", "a.m", "p.m", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}
_END = re.compile(r"[.!?]+[\"')\]]*\s+")


class Sentences:
    """Cuts streamed text into sentences for text-to-speech, so speech can start after the first one.

    feed(delta) returns the sentences completed so far; flush() returns the rest. Each piece keeps its
    trailing space so the pieces concatenate back to the original text. A period only ends a sentence
    when whitespace follows it, so "$1,100.50", "C-2026.08" and "FA209-31." mid-stream stay whole, and
    common abbreviations ("Dr.", "St.", "e.g.") and single initials ("F. A.") don't end one."""

    def __init__(self, min_chars: int = 8) -> None:
        self.buf, self.min_chars = "", min_chars

    def feed(self, delta: str) -> list[str]:
        self.buf += delta
        out, start = [], 0
        for m in _END.finditer(self.buf):
            piece = self.buf[start:m.end()]
            word = re.search(r"([\w.]+)[.!?]+[\"')\]]*\s+$", piece)
            w = (word[1] if word else "").lower().rstrip(".")
            if m.group(0).lstrip().startswith(".") and (w in _ABBREV or (len(w) == 1 and w.isalpha())):
                continue
            if len(piece.strip()) < self.min_chars:
                continue  # "Hi." alone: wait for more
            out.append(piece)
            start = m.end()
        self.buf = self.buf[start:]
        return out

    def flush(self) -> list[str]:
        rest, self.buf = self.buf, ""
        return [rest] if rest.strip() else []
