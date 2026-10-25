"""The policy as filed with California HCAI: every licensed hospital must file its charity care and discount
payment policies with the state (Health & Safety Code 127435), and HCAI's Hospital Fair Pricing Policy Lookup
publishes them, one page per hospital, each linking the filed PDFs by effective date.

Used when a hospital's own site gives the crawl no policy document (ZSFG points patients to HCAI; Stanford's
pages link none). Bright Data's Web Unlocker refuses government domains, so these are plain HTTPS requests,
logged in crawl_log like every other request (product NULL, note "plain HTTPS (not Bright Data)")."""
import html
import re
import time

import httpx

from .crawl import Crawl
from .fetch import Document, check_pdf, pdf_text
from .store import MAX_BINARY, sha256

LOOKUP = "https://hcai.ca.gov/affordability/hospital-billing-policies/{slug}/"
SECTIONS = ("Charity Care Policy", "Discount Payment Policy")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
DATE = re.compile(r'<span class="date">\s*(\d\d)/(\d\d)/(\d{4})\s*</span>')
LINK = re.compile(r'<a href="(https://api\.hdc\.hcai\.ca\.gov/Public/Extract/Attachment\?id=[^"]+)"')


def page_url(slug: str) -> str:
    return LOOKUP.format(slug=slug)


def filed_policies(page: str) -> list[dict]:
    """The newest filed PDF in each policy section of a lookup page: [{section, effective, url}].
    Only the "Policies" list of a section counts (not its application form); a PDF filed as both policies
    is listed once."""
    out, seen = [], set()
    for block in re.split(r'<h2 class="policy-section__title">', page)[1:]:
        title = " ".join(html.unescape(block.split("</h2>", 1)[0]).split())
        if title not in SECTIONS:
            continue
        policies = block.split('class="policy-section__application"', 1)[0]
        items = []
        for li in policies.split("<li")[1:]:  # date and link are paired within one list item, never across
            if (date := DATE.search(li)) and (link := LINK.search(li)):
                m, d, y = date.groups()
                items.append((f"{y}-{m}-{d}", link.group(1)))
        if not items:
            continue
        effective, url = max(items)
        if url not in seen:
            seen.add(url)
            out.append({"section": title, "effective": effective, "url": url})
    return out


async def _get(crawl: Crawl, url: str, timeout: float = 60) -> bytes:
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=20), follow_redirects=True,
                                     headers={"User-Agent": UA}) as c:
            r = await c.get(url)
    except httpx.HTTPError as e:
        crawl.store.log(run_id=crawl.run_id, hospital_id=crawl.hospital_id, stage="fetch", product=None, url=url,
                        status=0, nbytes=0, latency_ms=int((time.monotonic() - t0) * 1000), ok=False,
                        note=f"plain HTTPS (not Bright Data) failed: {type(e).__name__}")
        raise RuntimeError(f"plain HTTPS failed for {url}: {type(e).__name__}") from e
    ok = r.status_code < 400
    crawl.store.log(run_id=crawl.run_id, hospital_id=crawl.hospital_id, stage="fetch", product=None, url=url,
                    status=r.status_code, nbytes=len(r.content), latency_ms=int((time.monotonic() - t0) * 1000),
                    ok=ok, note="plain HTTPS (not Bright Data)")
    if not ok:
        raise RuntimeError(f"plain HTTPS {r.status_code} for {url}")
    return r.content


async def run(crawl: Crawl, slug: str) -> list[Document]:
    """Fetch the lookup page and the newest filed policy PDF(s) on it. Failures are fallbacks, never fatal."""
    url = page_url(slug)
    crawl.stage("fetch", "running", f"Fetching the policy as filed with California HCAI: {url}")
    try:
        page = (await _get(crawl, url)).decode("utf-8", "replace")
    except RuntimeError as e:
        crawl.fallback(f"HCAI lookup page: {e} → no state-filed copy")
        return []
    crawl.save(f"hcai-page-{sha256(page)[:12]}.html", page)
    found = filed_policies(page)
    crawl.note("fetch", f"HCAI {url}: " + ("; ".join(f"{f['section']} effective {f['effective']}: {f['url']}"
                                                     for f in found) or "no filed policy links"))
    docs = []
    for f in found:
        try:
            body = await _get(crawl, f["url"])
            check_pdf(body, f["url"])
            text, pages = pdf_text(body)
        except (RuntimeError, ValueError) as e:
            crawl.fallback(f"HCAI {f['section']} ({f['effective']}): {e} → skipped")
            continue
        digest = sha256(body)
        arts = [crawl.save(f"pdf-{digest[:12]}.txt", text)]
        if len(body) <= MAX_BINARY:
            arts.insert(0, crawl.save(f"pdf-{digest[:12]}.pdf", body))
        crawl.stage("fetch", "running", f"Read {pages}-page HCAI-filed {f['section']} (effective {f['effective']})")
        docs.append(Document(f["url"], "pdf", "state_filed", text, digest, len(body), via="https",
                             title=f"{f['section']} (filed with HCAI, effective {f['effective']})", pages=pages,
                             artifacts=arts, found_on=url, why=[f"newest {f['section']} on the HCAI lookup page"]))
    return docs
