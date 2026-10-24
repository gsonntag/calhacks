"""Stage 2 - fetch: Web Unlocker pulls the policy page as markdown, we find the links on it that point to the
actual policy documents (English PDFs), and Web Unlocker pulls those as raw bytes; pypdf turns them into text."""
import io
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import urljoin, urlparse

from pypdf import PdfReader

from ..integrations.brightdata import BrightDataError
from .crawl import BudgetExceeded
from .store import MAX_BINARY, sha256

LANGS = ("spanish", "español", "espanol", "chinese", "arabic", "armenian", "french", "german", "greek", "gujarati",
         "hebrew", "hindi", "hmong", "italian", "japanese", "korean", "lao", "khmer", "cambodian", "persian", "farsi",
         "punjabi", "russian", "tagalog", "thai", "vietnamese", "portuguese", "polish", "ukrainian", "urdu",
         "burmese", "samoan", "somali", "tongan", "mien", "amharic", "bengali", "yiddish", "nepali", "telugu")
DOC_TOPIC = (("financial assistance", 3), ("financial-assistance", 3), ("financial_assistance", 3),
             ("charity", 3), ("discount", 2), ("self-pay", 1), ("self_pay", 1))
MEDIA = (".mp3", ".mp4", ".jpg", ".jpeg", ".png", ".svg", ".gif", ".webp", ".docx", ".xlsx")
MD_LINK = re.compile(r"(?<!!)\[([^\]]{1,200})\]\((\S+?)(?:\s+\"[^\"]*\")?\)")


@dataclass
class Document:
    url: str
    kind: str                 # page | pdf
    role: str                 # policy_page | policy_doc | state_filed
    text: str                 # what extraction reads and quotes are verified against
    sha256: str               # of the bytes Bright Data returned
    nbytes: int
    via: str = "unlocker"
    title: str = ""
    pages: int | None = None
    artifacts: list[str] = field(default_factory=list)
    found_on: str = ""        # "google" or the page URL that linked to it
    why: list[str] = field(default_factory=list)

    def meta(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k != "text"}


@dataclass
class DocLink:
    text: str
    url: str
    score: float
    reasons: list[str]


def links(markdown: str, base_url: str) -> list[tuple[str, str]]:
    """[(link text, absolute url)] from Bright Data's markdown, in page order, de-duplicated."""
    out, seen = [], set()
    for text, href in MD_LINK.findall(markdown):
        url = urljoin(base_url, href.strip("<>"))
        if url.startswith("http") and url not in seen:
            seen.add(url)
            out.append((text.strip(), url))
    return out


def score_doc_link(text: str, url: str) -> DocLink:
    path = urlparse(url).path.lower()
    blob = f"{text} {path}".lower()
    s, reasons = 0.0, []

    def add(w: float, why: str) -> None:
        nonlocal s
        s += w
        reasons.append(f"{why} ({w:+g})")

    if path.endswith(".pdf") or "(pdf)" in blob:
        add(2, "PDF")
    if "polic" in blob:
        add(3, "says policy")
    for kw, w in DOC_TOPIC:
        if kw in blob:
            add(w, f"'{kw}'")
            break
    if "english" in blob:
        add(1, "English")
    if any(re.search(rf"\b{lang}\b", blob) for lang in LANGS) or re.search(r"/(es|zh|vi|ko|ru|tl)([-_][a-z]{2})?/", path):
        add(-20, "other language")
    if "application" in path or ("application" in blob and "polic" not in blob):
        add(-4, "application form")
    elif "application" in blob:
        add(-1, "policy + application packet")
    if any(w in blob for w in ("wall posting", "notice", "statement", "brochure", "summary", "flyer", "poster")):
        add(-2, "notice/summary, not the policy")
    if any(w in blob for w in ("excluded", "provider list", "list of providers", "covered providers")):
        add(-4, "provider list, not the policy")
    if "collection" in blob:
        add(-1, "collections policy")
    if path.endswith(MEDIA):
        add(-20, "media file")
    return DocLink(text, url, s, reasons)


def policy_doc_links(markdown: str, base_url: str, limit: int = 2, threshold: float = 6) -> list[DocLink]:
    scored = [score_doc_link(t, u) for t, u in links(markdown, base_url)]
    best: dict[str, DocLink] = {}
    for d in sorted(scored, key=lambda d: -d.score):
        if d.score >= threshold and d.url not in best:
            best[d.url] = d
    return list(best.values())[:limit]


def markdown_text(md: str) -> str:
    """Readable text from Bright Data markdown: images and link targets dropped, [text](url) -> text.
    Link targets go because some carry per-request tokens (e.g. Cloudflare's /cdn-cgi/l/email-protection#...),
    which would otherwise make an unchanged page hash differently on every fetch."""
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", md)
    md = re.sub(r"\]\(\S*?(?:\s+\"[^\"]*\")?\)", "]", md)
    md = re.sub(r"\\?[\[\]]", "", md)
    return re.sub(r"[ \t]+", " ", md)


def pdf_text(data: bytes) -> tuple[str, int]:
    reader = PdfReader(io.BytesIO(data))
    pages = [(p.extract_text() or "") for p in reader.pages]
    return "\n\n".join(f"[page {i + 1}]\n{t}" for i, t in enumerate(pages)), len(pages)


def check_pdf(data: bytes, url: str) -> None:
    if data[:5] != b"%PDF-":
        raise ValueError(f"expected a PDF from {url}, got {data[:40]!r}")


async def _pdf(crawl, url: str, role: str, title: str, found_on: str, why: list[str]) -> Document | None:
    try:
        resp = await crawl.document(url)
        check_pdf(resp.body, url)
        text, pages = pdf_text(resp.body)
    except BudgetExceeded:
        raise
    except Exception as e:  # one bad document (HTTP error, not a PDF, unreadable) must not sink the crawl
        crawl.note("fetch", f"could not use {url}: {type(e).__name__}: {e}")
        return None
    digest = sha256(resp.body)
    arts = [crawl.save(f"pdf-{digest[:12]}.txt", text)]
    if len(resp.body) <= MAX_BINARY:
        arts.insert(0, crawl.save(f"pdf-{digest[:12]}.pdf", resp.body))
    crawl.stage("fetch", "running", f"Read {pages}-page PDF ({len(resp.body) // 1024} KB): {title or url}")
    return Document(url, "pdf", role, text, digest, len(resp.body), title=title, pages=pages, artifacts=arts,
                    found_on=found_on, why=why)


async def run(crawl, discovery: dict, max_pages: int = 3) -> list[Document]:
    """Fetch candidate pages (best first) until one links to the policy document(s), then fetch those documents.
    Pages without document links are kept: some state the FPL rules themselves."""
    docs: list[Document] = []
    for cand in discovery["pages"][:max_pages]:
        url = cand["url"]
        if cand.get("is_pdf"):
            if d := await _pdf(crawl, url, "policy_doc", cand["title"], "google", cand["reasons"]):
                docs.append(d)
                break
            continue
        crawl.stage("fetch", "running", f"Fetching {url} via Bright Data Web Unlocker")
        try:
            resp = await crawl.page(url)
        except BrightDataError as e:
            crawl.note("fetch", f"page failed, trying next candidate: {e}")
            continue
        md = resp.text
        digest = sha256(resp.body)
        art = crawl.save(f"page-{digest[:12]}.md", md)
        found = policy_doc_links(md, url)
        docs.append(Document(url, "page", "policy_page", markdown_text(md), digest, len(resp.body),
                             title=cand["title"], artifacts=[art], found_on="google", why=cand["reasons"]))
        crawl.note("fetch", f"{url}: {len(links(md, url))} links, policy documents: "
                   + ("; ".join(f"{d.url} [{', '.join(d.reasons)}]" for d in found) or "none"))
        for link in found:
            if d := await _pdf(crawl, link.url, "policy_doc", link.text, url, link.reasons):
                docs.append(d)
        if found:
            break
    if reg := discovery.get("regulator_doc"):
        crawl.note("fetch", f"state-filed copy kept as a reference, not fetched (Bright Data blocks government "
                            f"domains): {reg['url']}")
    if not docs:
        raise RuntimeError("no policy page or document could be fetched")
    return docs
