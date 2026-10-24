"""discover -> fetch -> extract/verify -> store, for one hospital. Used by the CLI, the HTTP router and the
Policy agent (live crawl of a hospital that isn't in the database yet)."""
from . import discover, extract, fetch
from .crawl import Crawl, OnEvent
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
    crawl.stage("verify", "ok" if policy["tiers"] else "flag",
                f"{v['tiers_verified']} tier quotes verified, {v['tiers_rejected']} rejected")
    return policy, verification


async def crawl_hospital(store: PolicyStore, hospital: Hospital, *, run_id: str | None = None,
                         on_event: OnEvent | None = None, json_out=None, max_requests: int = 20) -> dict:
    """Full pipeline; returns the stored policy_versions row."""
    store.upsert_hospital(hospital)
    run_id = run_id or store.start_run("crawl", [hospital.id])
    crawl = Crawl(store, run_id, hospital.id, on_event, max_requests)
    found = await discover.run(crawl, hospital)
    if not found["pages"] and not found["regulator_doc"]:
        raise RuntimeError(f"no official policy page found for {hospital.name}")
    docs = await fetch.run(crawl, found)
    crawl.stage("fetch", "ok", f"Fetched {len(docs)} documents ({sum(d.nbytes for d in docs) // 1024} KB)")
    policy, verification = await extract_verified(crawl, docs, f"{hospital.name} ({hospital.system})", json_out)
    row = store.add_version(hospital.id, content_sha256=content_hash(docs), documents=[doc_meta(d) for d in docs],
                            discovery=found, extracted=policy, verification=verification,
                            status="ok" if policy["tiers"] else "no_tiers", run_id=run_id)
    store.add_check(hospital.id, row["content_sha256"], True, row["id"], f"initial crawl: version {row['version']}", run_id)
    crawl.stage("store", "ok", f"Saved version {row['version']} ({crawl.requests} Bright Data requests)")
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
