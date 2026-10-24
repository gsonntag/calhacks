"""Stage 3 - extract + verify. Claude reads the fetched documents and returns the eligibility rules, each with a
verbatim quote. Code then checks every quote against the fetched text; a tier whose quote isn't in the
documents (or doesn't state the tier's FPL limit) is rejected. Only verified tiers reach finance.py."""
import re
import unicodedata

from .. import finance, llm
from .fetch import Document

SYSTEM = """You read a hospital's financial assistance / charity care policy documents and extract the rules
that decide how much of a patient's bill is written off, based on family income as a percent of the Federal
Poverty Level (FPL).
Return JSON:
{"tiers": [{"max_fpl_pct": number, "discount_pct": number, "applies_to": "all"|"insured"|"uninsured",
            "min_oop_pct_income": number|null, "doc": number, "quote": "verbatim sentence",
            "limit_quote": "verbatim sentence stating the FPL limit, only if it is not in quote, else empty"}],
 "other_relief": [{"what": "one plain sentence", "applies_to": "all"|"insured"|"uninsured", "max_fpl_pct": number|null,
                   "min_oop_pct_income": number|null, "doc": number, "quote": "verbatim sentence"}],
 "collections": {"no_collections_while_pending": true|false|null, "doc": number, "quote": "verbatim sentence or empty"},
 "apply": {"how": "one plain sentence", "doc": number, "quote": "verbatim sentence or empty"},
 "notes": "one sentence on anything that limits these rules (e.g. which services or patients are excluded)"}
Rules:
- Every quote must be copied character-for-character from ONE document (give its DOCUMENT number in "doc").
  No paraphrase, no ellipses, no joining sentences from different places. 15-60 words is ideal.
- A tier's quote must contain the tier's FPL limit (e.g. "400%"). If the document states the rule and its FPL
  limit in different sentences (e.g. a numbered definition), quote the rule in "quote" and the sentence with
  the FPL limit in "limit_quote".
- discount_pct = percent of the patient's balance written off (100 = free care). If a discount is not a fixed
  percent (e.g. "limited to the amount Medicare would pay"), omit that tier.
- applies_to: who the tier is for. "insured" covers high medical cost / underinsured rules.
- min_oop_pct_income: for high-medical-cost rules, the percent of family income the patient's out-of-pocket
  medical costs must exceed (e.g. 10); else null.
- Only include rules that apply to the HOSPITAL named in the request. Health-system documents often contain
  rules limited to other facilities or programs (a different hospital, rural clinics, pharmacy, physician
  groups, a county program); leave those out.
- Report each distinct rule once, preferring the hospital's own current policy document.
- other_relief: income-based help for this hospital that is NOT a fixed percent write-off (e.g. "pay no more
  than Medicare would", a sliding scale without stated percents). These are shown to the patient but not
  used in the math.
- If the documents state no FPL-based tiers, return "tiers": []."""

MIN_QUOTE_CHARS = 25
MAX_DOC_CHARS = 60_000


def squash(s: str) -> str:
    """Normalize for quote matching: unicode-fold, lowercase, keep only letters and digits. Removing spaces and
    punctuation makes matching robust to PDF line breaks, hyphenation, curly quotes and markdown markup,
    while every word and number must still appear in the same order."""
    s = unicodedata.normalize("NFKC", s or "").lower()
    return re.sub(r"[^a-z0-9]", "", s)


def prompt(docs: list[Document], hospital: str = "") -> str:
    parts = [f"HOSPITAL: {hospital}"] if hospital else []
    for i, d in enumerate(docs, 1):
        label = {"policy_page": "hospital web page", "policy_doc": "hospital policy document",
                 "state_filed": "policy as filed with California HCAI"}.get(d.role, d.role)
        parts.append(f"=== DOCUMENT {i} ({label}) {d.url} ===\n{d.text[:MAX_DOC_CHARS]}")
    return "\n\n".join(parts)


def locate(quote: str, docs: list[Document]) -> Document | None:
    q = squash(quote)
    if len(q) < MIN_QUOTE_CHARS:
        return None
    return next((d for d in docs if q in squash(d.text)), None)


def _fpl_in_quote(tier: dict) -> bool:
    n = finance._num(tier.get("max_fpl_pct"))
    said = f"{tier.get('quote') or ''} {tier.get('limit_quote') or ''}".replace(",", "")
    return n is not None and f"{n:g}" in re.findall(r"\d+(?:\.\d+)?", said)


def _tier_problem(t: dict, doc: Document | None, docs: list[Document]) -> str:
    if doc is None:
        return "quote not found in any fetched document"
    if (t.get("limit_quote") or "").strip() and locate(t["limit_quote"], docs) is None:
        return "limit_quote not found in any fetched document"
    if not _fpl_in_quote(t):
        return "quotes do not state the tier's FPL limit"
    if not finance.clean_tiers([t]):
        return "tier has no numeric FPL limit / percent discount"
    return ""


def verify(found: dict, docs: list[Document]) -> tuple[dict, dict]:
    """-> (policy, verification). policy keeps the shape finance/caller expect: tiers (verified only, cleaned),
    no_collections_while_pending, collections_quote, apply_how. verification records every check."""
    checks, tiers = [], []
    for t in found.get("tiers") if isinstance(found.get("tiers"), list) else []:
        if not isinstance(t, dict):
            continue
        doc = locate(t.get("quote", ""), docs)
        reason = _tier_problem(t, doc, docs)
        checks.append({"what": "tier", "quote": t.get("quote", ""), "limit_quote": t.get("limit_quote") or "",
                       "verified": not reason, "reason": reason,
                       "source_url": doc.url if doc else None,
                       "tier": {k: t.get(k) for k in ("max_fpl_pct", "discount_pct", "applies_to", "min_oop_pct_income")}})
        if not reason:
            tiers.append({**t, "source_url": doc.url, "source_role": doc.role, "verified": True})
    tiers = _dedupe(finance.clean_tiers(tiers))

    other = []
    for o in found.get("other_relief") if isinstance(found.get("other_relief"), list) else []:
        if not isinstance(o, dict):
            continue
        odoc = locate(o.get("quote", ""), docs)
        checks.append({"what": "other_relief", "quote": o.get("quote", ""), "verified": odoc is not None,
                       "reason": "" if odoc else "quote not found in any fetched document",
                       "source_url": odoc.url if odoc else None, "summary": o.get("what", "")})
        if odoc:
            applies = str(o.get("applies_to") or "all").lower()
            other.append({"what": o.get("what", ""), "quote": o["quote"], "source_url": odoc.url, "verified": True,
                          "applies_to": applies if applies in ("all", "insured", "uninsured") else "all",
                          "max_fpl_pct": finance._num(o.get("max_fpl_pct")),
                          "min_oop_pct_income": finance._num(o.get("min_oop_pct_income"))})

    coll = found.get("collections") if isinstance(found.get("collections"), dict) else {}
    cdoc = locate(coll.get("quote", ""), docs)
    checks.append({"what": "collections", "quote": coll.get("quote", ""), "verified": cdoc is not None,
                   "source_url": cdoc.url if cdoc else None})
    apply = found.get("apply") if isinstance(found.get("apply"), dict) else {}
    adoc = locate(apply.get("quote", ""), docs)
    checks.append({"what": "apply", "quote": apply.get("quote", ""), "verified": adoc is not None,
                   "source_url": adoc.url if adoc else None})

    policy = {"tiers": tiers, "other_relief": other,
              "no_collections_while_pending": coll.get("no_collections_while_pending") if cdoc else None,
              "collections_quote": coll.get("quote", "") if cdoc else "",
              "collections_source": cdoc.url if cdoc else None,
              "apply_how": apply.get("how", ""), "apply_quote": apply.get("quote", "") if adoc else "",
              "notes": found.get("notes", "")}
    tier_checks = [c for c in checks if c["what"] == "tier"]
    verification = {"checks": checks, "tiers_verified": sum(c["verified"] for c in tier_checks),
                    "tiers_rejected": sum(not c["verified"] for c in tier_checks), "method": "squash-substring"}
    return policy, verification


def _dedupe(tiers: list[dict]) -> list[dict]:
    seen, out = set(), []
    for t in tiers:
        key = (t["max_fpl_pct"], t["discount_pct"], t["applies_to"], t["min_oop_pct_income"])
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out


async def run(docs: list[Document], hospital: str = "", json_out=None) -> dict:
    """Ask Claude for the rules (raw, unverified). json_out is injectable for tests."""
    return await (json_out or llm.json_out)(SYSTEM, prompt(docs, hospital), max_tokens=12000)
