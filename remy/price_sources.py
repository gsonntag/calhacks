"""Find and download each hospital's CMS price-transparency file (45 CFR 180.50).

Every hospital publishes `cms-hpt.txt` at the root of its website: blocks of `location-name`, `source-page-url`,
`mrf-url` (the machine-readable file) and contact lines. We fetch the index, pick the campus that matches our
registry entry, and download the file it points to into `data/price_files/` (gitignored) with a sidecar
`<hospital_id>.meta.json` recording where and how it was fetched.

These files are public by law and most are reachable with plain HTTPS. We request them through Bright Data's
Web Unlocker first so every request lands in the same crawl log as the policy crawls (`policydb/`, stage
"prices"); some hosts do refuse plain clients from our server (Alameda Health System's site times out, John
Muir's file host returns 403), and when the Unlocker can't return a file (size, timeout) we fall back to plain
HTTPS and log that too, without a Bright Data product.
"""
import hashlib
import io
import json
import re
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .integrations import brightdata
from .integrations.brightdata import BrightDataError
from .policies.crawl import Crawl
from .policies.store import PolicyStore
from .prices import RAW_DIR, decode

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


@dataclass(frozen=True)
class Source:
    hpt_url: str          # the hospital's cms-hpt.txt
    location: str         # regex picking the campus among the index's location-name entries


SOURCES: dict[str, Source] = {
    "sutter-cpmc": Source("https://www.sutterhealth.org/cms-hpt.txt", r"CALIFORNIA PACIFIC.*VAN NESS"),
    "sutter-alta-bates": Source("https://www.sutterhealth.org/cms-hpt.txt", r"ALTA BATES SUMMIT.*SUMMIT CAMPUS$"),
    "ucsf-health": Source("https://www.ucsfhealth.org/cms-hpt.txt", r"Parnassus"),
    "ucsf-saint-francis": Source("https://sfcommunityhospitals.ucsfhealth.org/cms-hpt.txt", r"Saint Francis"),
    "zsfg": Source("https://www.zuckerbergsanfranciscogeneral.org/cms-hpt.txt", r"SAN FRANCISCO GENERAL"),
    "chinese-hospital": Source("https://www.chinesehospital-sf.org/cms-hpt.txt", r"CHINESE HOSPITAL"),
    "stanford-health": Source("https://stanfordhealthcare.org/cms-hpt.txt", r"^Stanford Health Care$"),
    "highland-ahs": Source("https://www.alamedahealthsystem.org/cms-hpt.txt", r"Highland"),
    "john-muir-wc": Source("https://www.johnmuirhealth.com/cms-hpt.txt", r"Walnut Creek"),
    "kaiser-oakland": Source("https://healthy.kaiserpermanente.org/cms-hpt.txt", r"^Oakland Medical Center"),
    "marinhealth": Source("https://www.mymarinhealth.org/cms-hpt.txt", r"MarinHealth Medical Center"),
    "el-camino": Source("https://www.elcaminohealth.org/cms-hpt.txt", r"Mountain View"),
    "ucla-reagan": Source("https://www.uclahealth.org/cms-hpt.txt", r"^Ronald Reagan UCLA Medical Center$"),
}


def parse_hpt(text: str) -> list[dict]:
    """cms-hpt.txt -> [{location-name, source-page-url, mrf-url, ...}], one dict per blank-line block."""
    out, cur = [], {}
    for line in text.replace("\r", "").split("\n"):
        line = line.strip().strip("\ufeff")
        if not line:
            if cur:
                out.append(cur)
            cur = {}
            continue
        key, sep, val = line.partition(":")
        if not sep:
            continue
        key = key.strip().lower()
        if key == "location-name" and "location-name" in cur:  # blocks without a blank line between them
            out.append(cur)
            cur = {}
        cur[key] = val.strip()
    if cur:
        out.append(cur)
    return [e for e in out if e.get("mrf-url")]


def choose(entries: list[dict], pattern: str) -> dict:
    hits = [e for e in entries if re.search(pattern, e.get("location-name", ""), re.I)]
    if not hits:
        names = "; ".join(e.get("location-name", "?") for e in entries)[:400]
        raise LookupError(f"no location matching /{pattern}/ in cms-hpt.txt (has: {names})")
    return hits[0]


def looks_like_index(body: bytes) -> bool:
    return "mrf-url" in decode(body[:200000]).lower()


def looks_like_mrf(body: bytes) -> bool:
    if body[:2] == b"PK":
        return True
    head = decode(body[:4000]).lstrip("\ufeff \r\n\t")
    return head.startswith("{") or "hospital_name" in head.split("\n", 1)[0].lower()


def truncated(body: bytes) -> str | None:
    """Why a downloaded price file looks cut off, or None. Web Unlocker answers 200 with a partial body for big
    files (MarinHealth's 127 MB CSV came back as 2.2 MB, cut mid-row after ~64 s), so this is checked."""
    if body[:2] == b"PK":
        try:
            zipfile.ZipFile(io.BytesIO(body)).namelist()
            return None
        except zipfile.BadZipFile:
            return "zip has no central directory (cut off)"
    tail = decode(body[-4000:]) if body[:2] not in (b"\xff\xfe", b"\xfe\xff") else body[-4000:].decode("utf-16-le", "ignore")
    tail = tail.rstrip("\x00")
    if decode(body[:200]).lstrip("\ufeff \r\n\t").startswith("{"):
        return None if tail.rstrip().endswith("}") else "JSON does not end with '}'"
    if not tail.endswith("\n"):
        return "CSV does not end with a newline"
    return None


def mrf_ok(body: bytes) -> bool:
    return looks_like_mrf(body) and truncated(body) is None


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


async def _plain(crawl: Crawl, url: str, timeout: float) -> tuple[bytes, int]:
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=30), follow_redirects=True,
                                     headers={"User-Agent": UA}) as c:
            r = await c.get(url)
        ok = r.status_code < 400
        crawl.store.log(run_id=crawl.run_id, hospital_id=crawl.hospital_id, stage="prices", product=None, url=url,
                        status=r.status_code, nbytes=len(r.content), latency_ms=int((time.monotonic() - t0) * 1000),
                        ok=ok, note="plain HTTPS (not Bright Data)")
        if not ok:
            raise RuntimeError(f"plain HTTPS {r.status_code} for {url}")
        return r.content, r.status_code
    except httpx.HTTPError as e:
        crawl.store.log(run_id=crawl.run_id, hospital_id=crawl.hospital_id, stage="prices", product=None, url=url,
                        status=0, nbytes=0, latency_ms=int((time.monotonic() - t0) * 1000), ok=False,
                        note=f"plain HTTPS (not Bright Data) failed: {type(e).__name__}")
        raise RuntimeError(f"plain HTTPS failed for {url}: {type(e).__name__}") from e


async def get(crawl: Crawl, url: str, check, timeout: float, say=print) -> tuple[bytes, str]:
    """Web Unlocker first (logged as a Bright Data request), then plain HTTPS. Returns (body, via)."""
    try:
        resp = await crawl._call("prices", brightdata.UNLOCKER, url,
                                 lambda: brightdata.request(brightdata.UNLOCKER, url, timeout=timeout, attempts=1))
        if check(resp.body):
            return resp.body, "brightdata-unlocker"
        why = truncated(resp.body) if looks_like_mrf(resp.body) else f"not the expected file ({resp.body[:40]!r})"
        crawl.note("prices", f"Web Unlocker returned {len(resp.body)} B for {url} but it is unusable: {why}; "
                             f"falling back to plain HTTPS")
        say(f"  unlocker body unusable ({len(resp.body)} B: {why}); trying plain HTTPS")
    except BrightDataError as e:
        crawl.note("prices", f"Web Unlocker failed for {url}: {e}; falling back to plain HTTPS")
        say(f"  unlocker failed ({str(e)[:140]}); trying plain HTTPS")
    body, _ = await _plain(crawl, url, timeout)
    if not check(body):
        raise RuntimeError(f"{url} did not return the expected file (got {body[:80]!r})")
    return body, "https"


def unzip(body: bytes) -> tuple[bytes, str]:
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        names = [n for n in z.namelist() if n.lower().endswith((".csv", ".json"))]
        if not names:
            raise ValueError(f"zip has no CSV/JSON member: {z.namelist()}")
        return z.read(names[0]), names[0]


async def fetch(hospital_id: str, store: PolicyStore, run_id: str, raw_dir: Path = RAW_DIR, say=print) -> dict:
    """Download one hospital's price file; returns the sidecar metadata (also written next to the file)."""
    src = SOURCES[hospital_id]
    crawl = Crawl(store, run_id, hospital_id, max_requests=4)
    say(f"  index {src.hpt_url}")
    index, hpt_via = await get(crawl, src.hpt_url, looks_like_index, 60, say)
    entry = choose(parse_hpt(decode(index)), src.location)
    url = entry["mrf-url"].strip()
    crawl.note("prices", f"cms-hpt.txt ({hpt_via}): location '{entry.get('location-name')}' -> {url}")
    say(f"  {entry.get('location-name')} -> {url[:120]}")
    body, via = await get(crawl, url, mrf_ok, 600, say)
    member, download_sha = None, hashlib.sha256(body).hexdigest()
    if body[:2] == b"PK":
        body, member = unzip(body)
    ext = "json" if decode(body[:200]).lstrip("\ufeff \r\n\t").startswith("{") else "csv"
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{hospital_id}.{ext}"
    path.write_bytes(body)
    meta = {"hospital_id": hospital_id, "cms_hpt_url": src.hpt_url, "cms_hpt_via": hpt_via,
            "hpt_location_name": entry.get("location-name"), "source_page_url": entry.get("source-page-url"),
            "mrf_url": url, "via": via, "zip_member": member, "download_sha256": download_sha, "fetched_at": now(),
            "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body), "raw_file": path.name}
    (raw_dir / f"{hospital_id}.meta.json").write_text(json.dumps(meta, indent=1))
    say(f"  saved {path} ({len(body) // 1024} KB via {via})")
    return meta
