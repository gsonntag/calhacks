"""Build policydb/contacts/<hospital_id>.json: who to call about a bill at each hospital.

    uv run python scripts/build_contacts.py candidates zsfg       # regex hits (offline, no Claude)
    uv run python scripts/build_contacts.py build [hospital_id..]  # regex + Claude classification + verification
    uv run python scripts/build_contacts.py serp <hospital_id> "<google query>"   # 1 Bright Data SERP request
    uv run python scripts/build_contacts.py fetch <hospital_id> <url>             # 1 Bright Data Unlocker request

Sources are the crawled policy pages/PDFs already in policydb/raw (listed in policydb/policies.json), plus the
few contact pages fetched with `fetch` (listed in policydb/contacts/fetched.json, which also logs every Bright
Data request made for contacts). Regex finds the phone numbers near billing / financial-assistance words;
Claude says which department each one is (or that it's irrelevant, or for another facility); code keeps a
number only if it and its quote appear verbatim in the source text.
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from billback import contacts, llm  # noqa: E402
from billback.integrations import brightdata  # noqa: E402
from billback.policies import registry  # noqa: E402
from billback.policies.crawl import slim_serp  # noqa: E402
from billback.policies.fetch import check_pdf, markdown_text, pdf_text  # noqa: E402
from billback.policies.store import DEFAULT_DIR, sha256  # noqa: E402

ROOT = DEFAULT_DIR
OUT = contacts.CONTACTS_DIR
FETCHED = OUT / "fetched.json"
SEED = {h.id: h for h in registry.SEED}
BD_CAP = 15

# What a hospital's own pages say instead of a number. Each quote is checked verbatim against the named source
# artifact; "refer_to" pulls in another hospital's verified contacts (the page says that office now bills).
NOTES = {
    "kaiser-oakland": [{
        "department": "billing", "artifact_prefix": "raw/kaiser-oakland/contact-page-0e2f692f9c6f",
        "quote": "Please call the number that appears on your bill or contact Member Services.",
        "note": "None of the Kaiser pages we crawled gives a billing number; its billing FAQ sends patients to "
                "the number printed on their bill."}],
    "ucsf-saint-francis": [{
        "department": "billing", "artifact_prefix": "raw/ucsf-saint-francis/contact-page-520be87ff7eb",
        "quote": "For services provided on or after October 4, 2025, please visit: Help Paying Your Bill.",
        "note": "Saint Francis (now Hyde Hospital) moved to UCSF Health's billing system on October 4, 2025; "
                "the Dignity Health numbers on its page are for earlier services.",
        "refer_to": "ucsf-health", "refer_label": "UCSF Health, for services on or after October 4, 2025",
        "own_label": "for services before October 4, 2025"}],
}

SYSTEM = """You sort phone numbers found in a hospital's own web pages and policy documents. A patient's
advocate will phone the hospital about the patient's bill, and needs to know which number reaches which office.
For each candidate (a number and the text around it) return one item:
{"id": number, "department": "billing"|"financial_assistance"|"collections"|"advocate"|"skip",
 "label": "the office name as the document gives it, e.g. Patient Financial Services",
 "hours": "the office hours copied character-for-character from the context, or empty",
 "quote": "8-40 words copied character-for-character from the context, containing the phone number exactly as written",
 "reason": "a few words"}
Departments:
- billing: patient financial services, billing office, customer service for bills, patient accounting.
- financial_assistance: financial assistance / charity care office, financial counselors, the hospital's
  medical financial assistance program line.
- collections: the collection agency the hospital uses, or the hospital office handling accounts in collections.
- advocate: patient relations / ombudsman, or an outside consumer advocacy group the policy tells patients to
  call (e.g. Health Consumer Alliance).
- If the context says one office handles both bills and financial assistance questions, return two items
  with the same id (one "billing", one "financial_assistance").
- If the context says a number only covers some patients or dates of service, say so in "label".
- skip: anything else (fax, appointments, advice nurse, registration desks, price estimates, pre-authorization,
  general switchboard, social services, member services, language lines), and any number that is for a
  different hospital or campus than HOSPITAL (system-wide documents list many facilities).
Return JSON: {"items": [...]} with one item per candidate id. Never change a phone number."""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fetched() -> dict:
    return json.loads(FETCHED.read_text()) if FETCHED.exists() else {"requests": [], "documents": []}


def save_fetched(d: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    FETCHED.write_text(json.dumps(d, indent=1) + "\n")


def read_text(rel: str) -> str:
    raw = (ROOT / rel).read_text(errors="replace")
    return markdown_text(raw) if rel.endswith(".md") else raw


def sources(hid: str) -> list[dict]:
    """[{url, artifact, text}] for one hospital: its crawled documents (current version first, then older
    versions' documents still on disk), then the contact pages fetched for this script."""
    out, seen = [], set()
    export = json.loads((ROOT / "policies.json").read_text())
    for h in export["hospitals"]:
        if h["id"] != hid:
            continue
        for v in h["versions"]:
            for d in v["documents"]:
                rel = next((a for a in d["artifacts"] if a.endswith((".md", ".txt"))), None)
                if rel and rel not in seen and (ROOT / rel).exists():
                    seen.add(rel)
                    out.append({"url": d["url"], "artifact": rel, "text": read_text(rel)})
    for d in fetched()["documents"]:
        if d["hospital_id"] == hid and d["artifact"] not in seen and (ROOT / d["artifact"]).exists():
            seen.add(d["artifact"])
            out.append({"url": d["url"], "artifact": d["artifact"], "text": read_text(d["artifact"])})
    return out


async def classify(hospital: registry.Hospital, src: dict, cands: list[dict]) -> list[dict]:
    lines = [f"HOSPITAL: {hospital.name} ({hospital.system}, {hospital.city})", f"SOURCE: {src['url']}", ""]
    lines += [f"[{c['id']}] {c['phone']}\n    context: {c['context']}" for c in cands]
    out = await llm.json_out(SYSTEM, "\n".join(lines), max_tokens=12000)
    return out.get("items", []) if isinstance(out, dict) else []


async def build_one(hid: str) -> dict:
    hospital = SEED[hid]
    kept, rejected = [], []
    srcs = sources(hid)
    for src in srcs:
        cands = contacts.candidates(src["text"])
        if not cands:
            continue
        by_id = {c["id"]: c for c in cands}
        for item in await classify(hospital, src, cands):
            cand = by_id.get(item.get("id"))
            if not cand or item.get("department") == "skip":
                continue
            item = {**item, "phone": cand["phone"]}
            why = contacts.verify(item, src["text"])
            row = {"department": item["department"], "label": (item.get("label") or "").strip(),
                   "phone": cand["phone"], "dial": contacts.dial(cand["phone"]),
                   "hours": (item.get("hours") or "").strip() or cand["hours"], "source_url": src["url"],
                   "artifact": src["artifact"], "quote": item.get("quote", "").strip()}
            (rejected.append({**row, "reason": why}) if why else kept.append(row))
    # one entry per (department, number): the first one found, unless a later one also states the hours
    uniq, at = [], {}
    for r in kept:
        k = (r["department"], contacts.digits(r["phone"]))
        if k not in at:
            at[k] = len(uniq)
            uniq.append(r)
        elif r["hours"] and not uniq[at[k]]["hours"]:
            uniq[at[k]] = r
    notes = []
    for n in NOTES.get(hid, []):
        src = next((s for s in srcs if s["artifact"].startswith(n["artifact_prefix"])), None)
        if src is None or contacts.squash(n["quote"]) not in contacts.squash(src["text"]):
            sys.exit(f"{hid}: note quote not found in {n['artifact_prefix']}")
        notes.append({"department": n["department"], "note": n["note"], "quote": n["quote"], "source_url": src["url"]})
        if ref := n.get("refer_to"):
            # the office that bills now goes first; this page's own numbers are labelled as the older ones
            other = json.loads((OUT / f"{ref}.json").read_text())
            uniq = [{**c, "label": f"{c['label']} ({n['refer_label']})",
                     "via": {"hospital_id": ref, "quote": n["quote"], "source_url": src["url"]}}
                    for c in other["contacts"] if c["department"] in contacts.REQUIRED] + [
                {**c, "label": f"{c['label']} ({n['own_label']})"} if c["artifact"] == src["artifact"] else c
                for c in uniq]
    have = {r["department"] for r in uniq}
    note_for = {n["department"]: n for n in notes}
    gaps = []
    for d in contacts.REQUIRED:
        if d in have:
            continue
        why = (f"{note_for[d]['note']} Its page says: \"{note_for[d]['quote']}\"" if d in note_for else
               f"no {contacts.LABELS[d].lower()} phone number published in the {len(srcs)} crawled document(s) "
               f"for this hospital")
        gaps.append({"department": d, "label": contacts.LABELS[d], "phone": None, "why": why,
                     "source_url": note_for[d]["source_url"] if d in note_for else None})
    data = {"hospital_id": hid, "hospital": hospital.name, "built_at": now(),
            "method": "regex candidates + Claude department classification; phone and quote verified verbatim",
            "sources": [{"url": s["url"], "artifact": s["artifact"]} for s in srcs],
            "contacts": uniq, "gaps": gaps, "notes": notes, "rejected": rejected}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{hid}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    return data


async def build(ids: list[str]) -> None:
    if not llm.available():
        sys.exit("ANTHROPIC_API_KEY must be set in .env")
    # a hospital whose note refers to another hospital's contacts is built after it
    for hid in sorted(ids or list(SEED), key=lambda h: any(n.get("refer_to") for n in NOTES.get(h, []))):
        d = await build_one(hid)
        print(f"{hid}: {len(d['contacts'])} contacts, {len(d['rejected'])} rejected, "
              f"gaps: {[g['department'] for g in d['gaps']] or 'none'}")
        for c in d["contacts"]:
            print(f"   {c['department']:<21} {c['phone']:<16} {c['label'][:40]:<40} {c['hours'] or ''}")
        for r in d["rejected"]:
            print(f"   REJECTED {r['phone']}: {r['reason']}")


def _budget(log: dict) -> None:
    if len(log["requests"]) >= BD_CAP:
        sys.exit(f"contact Bright Data budget of {BD_CAP} requests already used")


def _record(log: dict, hid: str, product: str, url: str, resp=None, err: str = "") -> None:
    log["requests"].append({"ts": now(), "hospital_id": hid, "product": product, "url": url,
                            "status": resp.status if resp else None, "bytes": len(resp.body) if resp else 0,
                            "latency_ms": resp.latency_ms if resp else None, "ok": bool(resp), "error": err})
    save_fetched(log)


async def serp(hid: str, query: str) -> None:
    log = fetched()
    _budget(log)
    try:
        results, resp = await brightdata.serp(query)
    except brightdata.BrightDataError as e:
        _record(log, hid, "serp", f"google: {query}", err=str(e))
        sys.exit(str(e))
    path = ROOT / "raw" / hid / f"contact-serp-{sha256(resp.body)[:12]}.json"
    path.write_text(slim_serp(resp.body))
    _record(log, hid, "serp", f"google: {query}", resp)
    for r in results[:10]:
        print(f"{r['rank']:>2} {r['link']}\n   {r['title']}")


async def fetch(hid: str, url: str) -> None:
    log = fetched()
    _budget(log)
    is_pdf = url.lower().split("?")[0].endswith(".pdf")
    try:
        resp = await (brightdata.document(url) if is_pdf else brightdata.page(url))
    except brightdata.BrightDataError as e:
        _record(log, hid, "unlocker", url, err=str(e))
        sys.exit(str(e))
    _record(log, hid, "unlocker", url, resp)
    digest = sha256(resp.body)
    base = ROOT / "raw" / hid
    if is_pdf or resp.body[:5] == b"%PDF-":
        check_pdf(resp.body, url)
        text, _ = pdf_text(resp.body)
        rel = f"raw/{hid}/contact-pdf-{digest[:12]}.txt"
        (base / f"contact-pdf-{digest[:12]}.txt").write_text(text)
    else:
        rel = f"raw/{hid}/contact-page-{digest[:12]}.md"
        (base / f"contact-page-{digest[:12]}.md").write_text(resp.text)
    log["documents"].append({"hospital_id": hid, "url": url, "artifact": rel, "sha256": digest,
                             "nbytes": len(resp.body), "fetched_at": now()})
    save_fetched(log)
    hits = contacts.candidates(read_text(rel))
    print(f"saved {rel} ({len(resp.body)} B); {len(hits)} candidate numbers; "
          f"{len(log['requests'])}/{BD_CAP} contact requests used")
    for c in hits:
        print(f"  {c['phone']}: ...{c['context'][-160:]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("candidates").add_argument("hospital_id")
    sub.add_parser("build").add_argument("ids", nargs="*")
    s = sub.add_parser("serp")
    s.add_argument("hospital_id")
    s.add_argument("query")
    f = sub.add_parser("fetch")
    f.add_argument("hospital_id")
    f.add_argument("url")
    a = ap.parse_args()
    if a.cmd == "candidates":
        for src in sources(a.hospital_id):
            print(f"== {src['url']}")
            for c in contacts.candidates(src["text"]):
                print(f"  [{c['id']}] {c['phone']}: ...{c['context'][-200:]}")
    elif a.cmd == "build":
        asyncio.run(build(a.ids))
    elif a.cmd in ("serp", "fetch"):
        if not (brightdata.live() and brightdata.serp_live()):
            sys.exit("Bright Data key and zones must be set in .env")
        if a.hospital_id not in SEED:
            sys.exit(f"unknown hospital {a.hospital_id}")
        asyncio.run(serp(a.hospital_id, a.query) if a.cmd == "serp" else fetch(a.hospital_id, a.url))


if __name__ == "__main__":
    main()
