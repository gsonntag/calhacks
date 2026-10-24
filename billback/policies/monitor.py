"""Stage 5 - monitor: refetch a hospital's policy documents, compare content hashes with the stored version,
and re-extract only when something changed. Policies are mostly static, so a recheck is usually cheap:
no SERP query, no Claude call, just Web Unlocker fetches of the URLs already on record."""
from . import fetch
from .crawl import Crawl, OnEvent
from .pipeline import content_hash, doc_meta, extract_verified
from .store import PolicyStore


def rules(policy: dict) -> set[tuple]:
    return {(t["max_fpl_pct"], t["discount_pct"], t["applies_to"], t["min_oop_pct_income"]) for t in policy["tiers"]}


def diff(old: dict, new: dict) -> str:
    a, b = rules(old), rules(new)
    if a == b:
        return "text changed, eligibility rules unchanged"
    fmt = lambda r: f"{r[2]} <= {r[0]:g}% FPL -> {r[1]:g}% off" + (f" if OOP > {r[3]:g}% of income" if r[3] else "")
    return "; ".join([f"added: {fmt(r)}" for r in sorted(b - a)] + [f"removed: {fmt(r)}" for r in sorted(a - b)])


async def recheck(store: PolicyStore, hospital_id: str, *, run_id: str | None = None, on_event: OnEvent | None = None,
                  json_out=None, max_requests: int = 8) -> dict:
    cur = store.current(hospital_id)
    if cur is None:
        raise LookupError(f"{hospital_id} has never been crawled")
    run_id = run_id or store.start_run("recheck", [hospital_id])
    crawl = Crawl(store, run_id, hospital_id, on_event, max_requests)
    crawl.stage("recheck", "running", f"Refetching {len(cur['documents'])} documents from version {cur['version']}")
    docs = await fetch.run(crawl, cur["discovery"])
    digest = content_hash(docs)
    if digest == cur["content_sha256"]:
        store.add_check(hospital_id, digest, False, cur["id"], "unchanged", run_id)
        crawl.stage("recheck", "ok", f"Unchanged since {cur['fetched_at'][:10]} (sha256 {digest[:12]})")
        return {"hospital_id": hospital_id, "changed": False, "version": cur["version"], "content_sha256": digest,
                "requests": crawl.requests}
    h = store.hospital(hospital_id)
    policy, verification = await extract_verified(crawl, docs, f"{h.name} ({h.system})", json_out)
    row = store.add_version(hospital_id, content_sha256=digest, documents=[doc_meta(d) for d in docs],
                            discovery=cur["discovery"], extracted=policy, verification=verification,
                            status="ok" if policy["tiers"] else "no_tiers", run_id=run_id)
    what = diff(cur["extracted"], policy)
    store.add_check(hospital_id, digest, True, row["id"], f"policy changed: {what}", run_id)
    crawl.emit("policy_changed", version=row["version"], previous=cur["version"], diff=what)
    crawl.stage("recheck", "flag", f"Policy changed -> version {row['version']}: {what}")
    return {"hospital_id": hospital_id, "changed": True, "version": row["version"], "diff": what,
            "content_sha256": digest, "requests": crawl.requests}
