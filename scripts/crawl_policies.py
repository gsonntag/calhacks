"""Build and maintain the hospital policy database (policydb/) with real Bright Data crawls.

    uv run python scripts/crawl_policies.py crawl                # seed SF hospitals not yet in the DB
    uv run python scripts/crawl_policies.py crawl zsfg --force   # re-run the full pipeline for one hospital
    uv run python scripts/crawl_policies.py recheck              # refetch, compare hashes, re-extract on change
    uv run python scripts/crawl_policies.py show [hospital_id]   # print what's stored
    uv run python scripts/crawl_policies.py compare [default|freecare]

Needs BRIGHTDATA_API_KEY, BRIGHTDATA_SERP_ZONE, BRIGHTDATA_UNLOCKER_ZONE and ANTHROPIC_API_KEY in .env.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from billback import llm, prices  # noqa: E402
from billback.integrations import brightdata  # noqa: E402
from billback.policies import compare, monitor, pipeline, registry  # noqa: E402
from billback.policies.store import PolicyStore  # noqa: E402


def printer(kind: str, **e) -> None:
    if kind == "brightdata":
        mark = "ok " if e["ok"] else "ERR"
        print(f"  [{e['product']:<8}] {mark} {e['status']} {e['bytes'] or 0:>8} B {e['latency_ms'] or 0:>6} ms  {e['url'][:110]}")
    elif kind == "crawl_stage":
        print(f"  {e['stage']:<8} {e['status']:<7} {e['text']}")
    else:
        print(f"  {kind}: {e}")


def need_live() -> None:
    if not (brightdata.live() and brightdata.serp_live() and llm.available()):
        sys.exit("Bright Data (key + SERP and Unlocker zones) and ANTHROPIC_API_KEY must be set in .env")


async def crawl(store: PolicyStore, ids: list[str], force: bool) -> None:
    need_live()
    targets = [h for h in registry.SEED if not ids or h.id in ids]
    todo = [h for h in targets if force or not store.current(h.id)]
    run_id = store.start_run("crawl", [h.id for h in todo])
    results = {}
    for h in todo:
        print(f"\n== {h.name} ({h.id})")
        try:
            row = await pipeline.crawl_hospital(store, h, run_id=run_id, on_event=printer)
            results[h.id] = pipeline.summary(row)
        except Exception as e:
            store.log(run_id=run_id, hospital_id=h.id, stage="pipeline", product=None, url="", status=None,
                      nbytes=None, latency_ms=None, ok=False, note=f"{type(e).__name__}: {e}")
            results[h.id] = {"error": f"{type(e).__name__}: {e}"}
            print(f"  FAILED: {type(e).__name__}: {e}")
    store.finish_run(run_id, results)
    print(json.dumps(results, indent=1))


async def recheck(store: PolicyStore, ids: list[str]) -> None:
    need_live()
    targets = [h.id for h in store.hospitals() if (not ids or h.id in ids) and store.current(h.id)]
    run_id = store.start_run("recheck", targets)
    results = {}
    for hid in targets:
        print(f"\n== recheck {hid}")
        try:
            results[hid] = await monitor.recheck(store, hid, run_id=run_id, on_event=printer)
        except Exception as e:
            results[hid] = {"error": f"{type(e).__name__}: {e}"}
            print(f"  FAILED: {type(e).__name__}: {e}")
    store.finish_run(run_id, results)
    print(json.dumps(results, indent=1))


def no_tier_reason(cur: dict) -> str:
    """Plain summary of why a stored version has no fixed-percent tier (from what was fetched and extracted)."""
    docs = cur["documents"]
    kinds = ", ".join(f"{d['kind']} {d['url'].split('/')[-1][:60] or d['url']}" for d in docs)
    other = [o for o in cur["extracted"].get("other_relief") or [] if o]
    if not any(d["kind"] == "pdf" and d["role"] == "policy_doc" for d in docs):
        return f"no policy document was found/fetched (sources: {kinds}); extraction had only web pages"
    return (f"the fetched documents ({kinds}) state no fixed-percent FPL tier"
            + (f"; {len(other)} non-percentage relief item(s) stored" if other else ""))


def show(store: PolicyStore, ids: list[str]) -> None:
    for h in store.hospitals():
        if ids and h.id not in ids:
            continue
        cur = store.current(h.id)
        print(f"\n== {h.name} ({h.id}) — {len(store.versions(h.id))} version(s), {len(store.checks(h.id))} check(s)")
        if not cur:
            fail = next((r for r in reversed(store.crawl_log(h.id)) if r["stage"] == "pipeline" and not r["ok"]), None)
            print(f"  not crawled{': last crawl FAILED: ' + fail['note'] if fail else ''}")
            continue
        print(f"  fetched {cur['fetched_at']}  sha256 {cur['content_sha256'][:16]}  status {cur['status']}")
        for d in cur["documents"]:
            print(f"  source [{d['role']}] {d['url']}  ({d['nbytes']} B, sha256 {d['sha256'][:12]})")
        for t in cur["extracted"]["tiers"]:
            oop = f", OOP > {t['min_oop_pct_income']:g}% of income" if t.get("min_oop_pct_income") else ""
            print(f"  tier: {t['applies_to']} <= {t['max_fpl_pct']:g}% FPL -> {t['discount_pct']:g}% off{oop}")
            print(f"        \"{t['quote']}\"")
        v = cur["verification"]
        print(f"  quotes: {v['tiers_verified']} tiers verified, {v['tiers_rejected']} rejected")
        if not cur["extracted"]["tiers"]:
            print(f"  why no tiers: {no_tier_reason(cur)}")
        if pt := prices.table(h.id):
            print(f"  price table: {pt['codes_found']}/{pt['codes_requested']} codes from {pt['location_name'][:60]!r}"
                  f" (file updated {pt['last_updated_on']}, CMS template v{pt['template_version']})")
        else:
            print("  price table: none (scripts/build_price_tables.py; see docs/brightdata.md)")
    print(f"\nBright Data requests logged: {store.request_counts()}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["crawl", "recheck", "show", "compare", "export"])
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    store = PolicyStore()
    if args.command == "crawl":
        asyncio.run(crawl(store, args.ids, args.force))
    elif args.command == "recheck":
        asyncio.run(recheck(store, args.ids))
    elif args.command == "show":
        show(store, args.ids)
    elif args.command == "compare":
        print(json.dumps(compare.compare(store, args.ids[0] if args.ids else "default"), indent=1))
    if args.command in ("crawl", "recheck", "export"):
        print("wrote", store.write_export())


if __name__ == "__main__":
    main()
