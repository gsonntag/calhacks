"""Bright Data: find and fetch the hospital's public financial-assistance policy.

Live path: SERP API to find the policy page (if no URL is configured), Web Unlocker to fetch it.
Offline path: demo/policy_sample.md, labelled as a sample on the dashboard.
"""
import json
import re
from urllib.parse import quote_plus, unquote

import httpx

from .. import config

REQUEST_URL = "https://api.brightdata.com/request"


def live() -> bool:
    return bool(config.BRIGHTDATA_API_KEY and config.BRIGHTDATA_UNLOCKER_ZONE)


async def _request(zone: str, url: str, fmt: str = "raw", timeout: float = 25, markdown: bool = False) -> str:
    body = {"zone": zone, "url": url, "format": fmt, "country": "us"}
    if markdown:
        body["data_format"] = "markdown"
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(REQUEST_URL, headers={"Authorization": f"Bearer {config.BRIGHTDATA_API_KEY}"}, json=body)
    r.raise_for_status()
    return r.text


async def search(query: str) -> list[dict]:
    """Google results via the SERP zone. Returns [{title, link}]."""
    if not config.BRIGHTDATA_SERP_ZONE:
        raise RuntimeError("BRIGHTDATA_SERP_ZONE not set (Google search needs a SERP zone, not the Unlocker zone)")
    raw = await _request(config.BRIGHTDATA_SERP_ZONE, f"https://www.google.com/search?q={quote_plus(query)}&brd_json=1")
    try:
        data = json.loads(raw)
        if "body" in data and "organic" not in data:  # format=json wraps the payload
            data = json.loads(data["body"])
        return [{"title": o.get("title", ""), "link": o.get("link", "")} for o in data.get("organic", [])][:8]
    except json.JSONDecodeError:  # zone returned HTML; pull links out crudely
        links = [unquote(u) for u in re.findall(r'/url\?q=(https?://[^&"]+)', raw)]
        links += re.findall(r'href="(https?://[^"]+)"', raw)
        return [{"title": "", "link": u} for u in dict.fromkeys(links) if "google" not in u][:8]


def html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


async def fetch_text(url: str, attempts: int = 2) -> str:
    last: Exception | None = None
    for _ in range(attempts):
        try:
            raw = await _request(config.BRIGHTDATA_UNLOCKER_ZONE, url, markdown=True)
            if raw.lstrip().startswith("%PDF"):
                raise RuntimeError("got a PDF, not a web page")
            return html_to_text(raw) if "<" in raw[:500] else raw
        except Exception as e:
            last = e
    raise RuntimeError(f"Bright Data fetch failed for {url}: {last}")


def sample_policy(name: str = "policy_sample.md") -> str:
    return (config.DEMO_DIR / name).read_text()
