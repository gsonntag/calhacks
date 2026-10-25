"""Bright Data client: the two products BillBack uses, behind one request function.

- SERP API (zone BRIGHTDATA_SERP_ZONE): Google results as parsed JSON, to discover a hospital's policy page.
- Web Unlocker (zone BRIGHTDATA_UNLOCKER_ZONE): fetches hospital pages (as markdown) and policy PDFs (raw bytes)
  that block or throttle plain HTTP clients.

Both go through POST https://api.brightdata.com/request. Every call returns a BDResponse with status, size and
latency so billback/policies can write it to the crawl log. The policy pipeline is in billback/policies/.
"""
import asyncio
import json
import time
from dataclasses import dataclass
from urllib.parse import quote_plus

import httpx

from .. import config

REQUEST_URL = "https://api.brightdata.com/request"
SERP, UNLOCKER = "serp", "unlocker"
RETRY_STATUS = {429, 500, 502, 503, 504}


class BrightDataError(RuntimeError):
    def __init__(self, product: str, url: str, msg: str, status: int = 0, latency_ms: int = 0):
        super().__init__(f"Bright Data {product} failed for {url}: {msg}")
        self.product, self.url, self.status, self.latency_ms = product, url, status, latency_ms


@dataclass
class BDResponse:
    product: str        # "serp" | "unlocker"
    url: str            # the target URL Bright Data fetched for us
    status: int
    body: bytes
    content_type: str
    latency_ms: int

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    @property
    def is_pdf(self) -> bool:
        return self.body[:5] == b"%PDF-" or "pdf" in self.content_type


def live() -> bool:
    return bool(config.BRIGHTDATA_API_KEY and config.BRIGHTDATA_UNLOCKER_ZONE)


def serp_live() -> bool:
    return bool(config.BRIGHTDATA_API_KEY and config.BRIGHTDATA_SERP_ZONE)


def _zone(product: str) -> str:
    zone = config.BRIGHTDATA_SERP_ZONE if product == SERP else config.BRIGHTDATA_UNLOCKER_ZONE
    if not (config.BRIGHTDATA_API_KEY and zone):
        raise BrightDataError(product, "-", f"not configured (BRIGHTDATA_API_KEY / {product} zone missing)")
    return zone


async def request(product: str, url: str, *, fmt: str = "raw", data_format: str | None = None,
                  timeout: float = 60, attempts: int = 2) -> BDResponse:
    """One Bright Data request. Request body: {zone, url, format, country, data_format?}.
    Retries timeouts and 429/5xx once; raises BrightDataError with status and latency on failure."""
    body = {"zone": _zone(product), "url": url, "format": fmt, "country": "us"}
    if data_format:
        body["data_format"] = data_format
    headers = {"Authorization": f"Bearer {config.BRIGHTDATA_API_KEY}"}
    err = BrightDataError(product, url, "no attempt made")
    for attempt in range(attempts):
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.post(REQUEST_URL, headers=headers, json=body)
        except httpx.HTTPError as e:
            err = BrightDataError(product, url, f"{type(e).__name__}", 0, int((time.monotonic() - t0) * 1000))
        else:
            ms = int((time.monotonic() - t0) * 1000)
            # Bright Data answers 200 even when the target failed; the target's status is in x-brd-status-code.
            status = int(r.headers.get("x-brd-status-code") or r.status_code)
            if r.status_code < 400 and status < 400 and not r.headers.get("x-brd-error"):
                return BDResponse(product, url, status, r.content, r.headers.get("content-type", ""), ms)
            msg = r.headers.get("x-brd-error") or r.text[:200]
            err = BrightDataError(product, url, f"HTTP {status}: {msg}", status, ms)
            if status not in RETRY_STATUS or "blocked" in msg.lower() or "access denied" in msg.lower():
                raise err
        if attempt + 1 < attempts:
            await asyncio.sleep(2 * (attempt + 1))
    raise err


async def serp(query: str, attempts: int = 2) -> tuple[list[dict], BDResponse]:
    """Google via the SERP API, parsed: returns ([{title, link, description, rank}], raw response).
    With format=json the reply is {status_code, headers, body}; a failed Google fetch shows up as
    status_code >= 400 inside a 200 reply, so that is checked (and retried) here. attempts=1: no hidden retry
    (the crawl session retries visibly instead, policies/crawl.py)."""
    url = f"https://www.google.com/search?q={quote_plus(query)}&hl=en&gl=us"
    for attempt in range(attempts):
        resp = await request(SERP, url, fmt="json", data_format="parsed", timeout=60, attempts=attempts)
        try:
            wrapper = json.loads(resp.text)
            inner = int(wrapper.get("status_code") or 200) if isinstance(wrapper, dict) else 200
            if inner < 400:
                return parse_serp(resp.text), resp
            msg = f"Google returned {inner}: {str(wrapper.get('body'))[:120]}"
        except (ValueError, AttributeError) as e:
            inner, msg = 0, f"unparseable SERP payload ({e})"
        if attempt + 1 == attempts:
            raise BrightDataError(SERP, url, msg, inner, resp.latency_ms)
        await asyncio.sleep(16 if inner == 429 else 2)  # Google's 429 says "retry after 15 seconds"


def parse_serp(raw: str) -> list[dict]:
    data = json.loads(raw)
    if "body" in data and "organic" not in data:  # format=json wraps the page: {status_code, headers, body}
        data = json.loads(data["body"])
    return [{"title": o.get("title", ""), "link": o.get("link", ""), "description": o.get("description", ""),
             "rank": o.get("rank", i + 1)} for i, o in enumerate(data.get("organic") or []) if o.get("link")]


async def page(url: str, attempts: int = 2) -> BDResponse:
    """A web page through Web Unlocker, converted to markdown by Bright Data (links kept as [text](url))."""
    return await request(UNLOCKER, url, data_format="markdown", attempts=attempts)


async def document(url: str, attempts: int = 2) -> BDResponse:
    """A file (policy PDF) through Web Unlocker, raw bytes."""
    return await request(UNLOCKER, url, timeout=90, attempts=attempts)

