"""discover -> fetch -> extract/verify -> store, for one hospital. Used by the CLI, the HTTP router and the
Policy agent (live crawl of a hospital that isn't in the database yet)."""
from . import discover, extract, fetch, hcai
from .crawl import HARD_CAP, Crawl, OnEvent
from .registry import Hospital
from .store import PolicyStore, sha256


def content_hash(docs: list[fetch.Document]) -> str:
    """Hash of the documents' normalized text (not raw bytes), so page chrome that changes per request
    (tracking ids, timestamps in markup) doesn't count as a policy change."""
    return sha256("\n".join(f"{d.url}\n{sha256(extract.squash(d.text))}" for d in sorted(docs, key=lambda d: d.url)))


def doc_meta(d: fetch.Document) -> dict:
    return {**d.meta(), "text_sha256": sha256(extract.squash(d.text))}


async def extract_verified(crawl: Crawl, docs: list[fetch.Document], hospital: str,
                           json_out=None) -> tuple[dict, dict]:
    crawl.stage("extract", "running", f"Claude reading {len(docs)} documents for income tiers (with quotes)")
    found = await extract.run(docs, hospital, json_out)
    if not isinstance(found, dict):
        raise ValueError("extraction did not return a JSON object")
    crawl.stage("verify", "running", "Checking every quote against the fetched text")
    policy, verification = extract.verify(found, docs)
    for c in verification["checks"]:
        crawl.note("verify", f"{c['what']} {'VERIFIED in ' + str(c['source_url']) if c['verified'] else 'REJECTED: ' + (c.get('reason') or 'not found')}"
                             f" | {c['quote'][:160]!r}")
    v = verification
    proof = [(f"{'Verified' if c['verified'] else 'Rejected'} {c['what'].replace('_', ' ')}: “{c['quote'][:220]}”",
              c.get("source_url") or ("" if c["verified"] else (c.get("reason") or "not in the fetched text")))
             for c in v["checks"] if c.get("quote")]
    crawl.stage("verify", "ok" if policy["tiers"] else "flag",
                f"{v['tiers_verified']} tier quotes verified, {v['tiers_rejected']} rejected", proof[:8])
    return policy, verification


async def crawl_hospital(store: PolicyStore, hospital: Hospital, *, run_id: str | None = None,
                         on_event: OnEvent | None = None, json_out=None, max_requests: int = HARD_CAP,
                         known: dict | None = None) -> dict:
    """Full pipeline; returns the stored policy_versions row. known: the discovery on record (a re-crawl), the
    fallback when Google can't be reached. At most HARD_CAP Bright Data requests, retries included."""
    store.upsert_hospital(hospital)
    run_id = run_id or store.start_run("crawl", [hospital.id])
    crawl = Crawl(store, run_id, hospital.id, on_event, max_requests)
    found = await discover.run(crawl, hospital, known)
    if hospital.hcai:
        found["hcai_slug"] = hospital.hcai
    if not found["pages"] and not found["regulator_doc"] and not hospital.hcai:
        raise RuntimeError(f"no official policy page found for {hospital.name}")
    docs = await gather(crawl, found)
    policy, verification = await extract_verified(crawl, docs, f"{hospital.name} ({hospital.system})", json_out)
    row = store.add_version(hospital.id, content_sha256=content_hash(docs), documents=[doc_meta(d) for d in docs],
                            discovery=found, extracted=policy, verification=verification,
                            status="ok" if policy["tiers"] else "no_tiers", run_id=run_id)
    store.add_check(hospital.id, row["content_sha256"], True, row["id"], f"initial crawl: version {row['version']}", run_id)
    crawl.stage("store", "ok", f"Saved version {row['version']} ({crawl.requests} of at most {crawl.max_requests} "
                                f"Bright Data requests)")
    return row


async def gather(crawl: Crawl, found: dict) -> list[fetch.Document]:
    """fetch.run, plus the policy as filed with HCAI when the hospital's own site gave no policy document.
    Used by crawls and rechecks alike, so a recheck refetches the same kinds of sources."""
    try:
        docs = await fetch.run(crawl, found)
    except RuntimeError:  # nothing fetchable from the hospital's own site (BudgetExceeded included)
        if not found.get("hcai_slug"):
            raise
        docs = []
    if found.get("hcai_slug") and not any(d.kind == "pdf" and d.role == "policy_doc" for d in docs):
        if docs:
            crawl.fallback("The hospital's own pages link no policy document → the copy filed with California HCAI")
        docs += await hcai.run(crawl, found["hcai_slug"])
    if not docs:
        raise RuntimeError("no policy page or document could be fetched")
    crawl.stage("fetch", "ok", f"Fetched {len(docs)} documents ({sum(d.nbytes for d in docs) // 1024} KB)")
    return docs


async def add_state_filed(store: PolicyStore, hospital: Hospital, *, run_id: str | None = None,
                          on_event: OnEvent | None = None, json_out=None) -> dict:
    """New version = the stored version's documents (rebuilt from committed artifacts, no Bright Data request)
    + the policy as filed with HCAI, re-extracted and re-verified. For a hospital stored without a policy
    document before HCAI support existed."""
    cur = store.current(hospital.id)
    if cur is None or not hospital.hcai:
        raise LookupError(f"{hospital.id}: needs a stored version and an HCAI slug")
    store.upsert_hospital(hospital)
    run_id = run_id or store.start_run("hcai", [hospital.id])
    crawl = Crawl(store, run_id, hospital.id, on_event, 0)
    filed = await hcai.run(crawl, hospital.hcai)
    if not filed:
        raise RuntimeError(f"no HCAI-filed policy could be fetched for {hospital.name}")
    docs = load_documents(store, cur) + filed
    policy, verification = await extract_verified(crawl, docs, f"{hospital.name} ({hospital.system})", json_out)
    row = store.add_version(hospital.id, content_sha256=content_hash(docs), documents=[doc_meta(d) for d in docs],
                            discovery={**cur["discovery"], "hcai_slug": hospital.hcai}, extracted=policy,
                            verification=verification, status="ok" if policy["tiers"] else "no_tiers", run_id=run_id)
    store.add_check(hospital.id, row["content_sha256"], True, row["id"],
                    f"added the policy as filed with HCAI: version {row['version']}", run_id)
    return row


def summary(row: dict) -> dict:
    return {"version": row["version"], "status": row["status"], "tiers": len(row["extracted"]["tiers"]),
            "tiers_verified": row["tiers_verified"], "tiers_rejected": row["tiers_rejected"],
            "sources": row["source_urls"], "fetched_at": row["fetched_at"]}



def load_documents(store: PolicyStore, row: dict) -> list[fetch.Document]:
    """Rebuild the fetched documents of a stored version from its committed raw artifacts (no network).
    Lets anyone re-run extraction/verification on exactly what Bright Data returned."""
    docs = []
    for m in row["documents"]:
        arts = m["artifacts"]
        if m["kind"] == "page":
            text = fetch.markdown_text(store.read_artifact(next(a for a in arts if a.endswith(".md"))))
        else:
            text = store.read_artifact(next(a for a in arts if a.endswith(".txt")))
        docs.append(fetch.Document(**{k: v for k, v in m.items() if k != "text_sha256"}, text=text))
    return docs
