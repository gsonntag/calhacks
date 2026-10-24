"""Stage 1 - discover: Google (Bright Data SERP API) -> ranked candidate URLs, each with the reasons it scored.

Official hospital/health-system domains win; pages about financial assistance / charity care / help paying
are preferred; aggregators (careroute.ai, dollarfor.org ...) are demoted; California HCAI attachments are kept
as the state-filed copy of the policy (an authoritative second source)."""
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import urlparse

from .registry import Hospital, norm

AGGREGATORS = ("careroute.ai", "dollarfor.org", "coveredusa.org", "sfserviceguide.org", "achaheart.org",
               "yelp.com", "healthgrades.com", "wikipedia.org", "reddit.com", "facebook.com", "linkedin.com")
# California Department of Health Care Access and Information: every hospital files its charity-care policy here.
# Recorded as a reference only: Bright Data's Web Unlocker refuses government domains ("classified as
# Government and blocked"), seen in our first crawl, so we don't spend a request on it.
REGULATOR_DOCS = ("api.hdc.hcai.ca.gov",)
REGULATOR_PAGES = ("syfphr.hcai.ca.gov", "syfphr.oshpd.ca.gov", "hcai.ca.gov")
TOPIC = (("financial assistance", 4), ("charity care", 4), ("help paying", 3), ("financial-assistance", 4),
         ("charity", 2), ("discount", 1), ("billing", 1), ("polic", 2))
OFF_TOPIC = ("application", "job", "career", "volunteer", "price transparency", "estimate", "mychart")


@dataclass
class Candidate:
    url: str
    title: str
    serp_rank: int
    kind: str                      # official | regulator | aggregator | other
    score: float
    is_pdf: bool
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _on(h: str, domains) -> bool:
    return any(h == d or h.endswith("." + d) for d in domains)


def official(url: str, hospital: Hospital) -> bool:
    """Known hospitals: exact host match only (sfcommunityhospitals.ucsfhealth.org is a different hospital)."""
    h = host(url)
    if hospital.domains:
        return h in hospital.domains
    # unknown hospital: a domain built from its own name or system (e.g. sutterhealth.org) counts as official
    words = [w for w in norm(f"{hospital.name} {hospital.system}").split() if len(w) > 3 and w not in
             ("hospital", "medical", "center", "health", "general", "community", "francisco")]
    return bool(words) and any(w in h.replace("-", "") for w in words)


def kind_of(url: str, hospital: Hospital) -> str:
    h = host(url)
    if _on(h, REGULATOR_DOCS) or _on(h, REGULATOR_PAGES):
        return "regulator"
    if _on(h, AGGREGATORS):
        return "aggregator"
    return "official" if official(url, hospital) else "other"


def score(result: dict, hospital: Hospital) -> Candidate:
    url, title = result["link"], result.get("title", "")
    text = f"{title} {result.get('description', '')} {urlparse(url).path}".lower()
    kind = kind_of(url, hospital)
    is_pdf = urlparse(url).path.lower().endswith(".pdf") or "/attachment" in url.lower()
    reasons, s = [], {"official": 10.0, "regulator": 4.0, "other": 0.0, "aggregator": -10.0}[kind]
    reasons.append(f"{kind} domain {host(url)}")
    for kw, w in TOPIC:
        if kw in text:
            s += w
            reasons.append(f"mentions '{kw}' (+{w})")
    for kw in OFF_TOPIC:
        if kw in text:
            s -= 3
            reasons.append(f"mentions '{kw}' (-3)")
    if hospital_words := [w for w in norm(hospital.name).split() if len(w) > 3]:
        hits = sum(w in norm(text) for w in hospital_words)
        if hits:
            s += hits
            reasons.append(f"names the hospital ({hits} words)")
    rank = int(result.get("rank") or 99)
    s -= rank * 0.25
    reasons.append(f"Google rank {rank} (-{rank * 0.25:g})")
    return Candidate(url, title, rank, kind, round(s, 2), is_pdf, reasons)


def fetchable(url: str) -> bool:
    """Google 'goto' redirect links are opaque and Web Unlocker rejects google.com, so they can't be followed."""
    h = host(url)
    return url.startswith("http") and not (h == "google.com" or h.endswith(".google.com"))


def rank(results: list[dict], hospital: Hospital) -> list[Candidate]:
    seen, out = set(), []
    for r in results:
        if r.get("link") and fetchable(r["link"]) and r["link"] not in seen:
            seen.add(r["link"])
            out.append(score(r, hospital))
    return sorted(out, key=lambda c: -c.score)


def pick(candidates: list[Candidate]) -> dict:
    """Policy pages to try (official, in score order) and the state-filed policy document, if Google surfaced one."""
    pages = [c for c in candidates if c.kind == "official" and c.score > 10]
    filed = [c for c in candidates if c.kind == "regulator" and _on(host(c.url), REGULATOR_DOCS)]
    return {"pages": pages[:3], "regulator_doc": filed[0] if filed else None,
            "skipped": [c for c in candidates if c.kind == "aggregator"]}


def needs_retry(results: list[dict], hospital: Hospital) -> bool:
    return not pick(rank(results, hospital))["pages"]


def retry_query(hospital: Hospital) -> str | None:
    """Second SERP query when the first had no usable official page (e.g. all links were Google redirects)."""
    if hospital.domains:
        return f"site:{hospital.domains[0]} financial assistance policy"
    return f"\"{hospital.name}\" financial assistance policy pdf"


def why(c: Candidate) -> str:
    return f"{c.url} — score {c.score}: " + "; ".join(c.reasons)


def is_policy_text(text: str) -> bool:
    return bool(re.search(r"poverty|\bFPL\b", text, re.I))


async def run(crawl, hospital: Hospital) -> dict:
    """SERP query (plus one retry query if no official page came back) -> ranked, explained choice."""
    queries, results, artifacts = [hospital.search_query], [], []
    crawl.stage("discover", "running", f"Searching Google via Bright Data SERP API: {hospital.search_query}")
    found, _, path = await crawl.serp(hospital.search_query)
    results += found
    artifacts.append(path)
    if needs_retry(results, hospital) and (q := retry_query(hospital)):
        crawl.note("discover", f"no fetchable official page in first results; retrying with: {q}")
        queries.append(q)
        found, _, path = await crawl.serp(q)
        results += found
        artifacts.append(path)
    candidates = rank(results, hospital)
    chosen = pick(candidates)
    for c in chosen["pages"]:
        crawl.note("discover", "candidate page " + why(c))
    if chosen["regulator_doc"]:
        crawl.note("discover", "state-filed copy " + why(chosen["regulator_doc"]))
    for c in chosen["skipped"]:
        crawl.note("discover", f"skipped aggregator {c.url}")
    top = chosen["pages"][0].url if chosen["pages"] else "no official page"
    crawl.stage("discover", "ok" if chosen["pages"] else "flag",
                f"{len(results)} results; chose {top}" + (" + state-filed copy (HCAI)" if chosen["regulator_doc"] else ""))
    return {"queries": queries, "serp_artifacts": artifacts,
            "candidates": [c.as_dict() for c in candidates],
            "pages": [c.as_dict() for c in chosen["pages"]],
            "regulator_doc": chosen["regulator_doc"].as_dict() if chosen["regulator_doc"] else None}
