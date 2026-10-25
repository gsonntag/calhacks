"""Build the compact real price tables in policydb/prices/<hospital_id>.json from hospitals' CMS price files.

    uv run python scripts/build_price_tables.py                  # rebuild every table from data/price_files/
    uv run python scripts/build_price_tables.py sutter-cpmc      # one hospital
    uv run python scripts/build_price_tables.py --fetch [ids]    # download via cms-hpt.txt first (costs requests)
    uv run python scripts/build_price_tables.py --list           # show what's built

Without --fetch the build reads only the raw files already in data/price_files/ (gitignored) and their
.meta.json sidecars, makes no network request, and the same raw file always gives the same JSON. --fetch
downloads first (Bright Data Web Unlocker, plain HTTPS as fallback; every request is logged in policydb/).
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from remy import prices  # noqa: E402
from remy.price_sources import SOURCES, fetch  # noqa: E402
from remy.policies.store import PolicyStore  # noqa: E402

LIMIT = 200 * 1024


def raw_file(hid: str) -> tuple[Path, dict] | None:
    meta_path = prices.RAW_DIR / f"{hid}.meta.json"
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text())
    path = prices.RAW_DIR / meta["raw_file"]
    return (path, meta) if path.exists() else None


def fit(table: dict) -> str:
    """Serialize; if over ~200 KB, drop per-code variants, then trim named payers to 4."""
    out = json.dumps(table, indent=1, ensure_ascii=False)
    if len(out.encode()) > LIMIT:
        for e in table["prices"].values():
            e.pop("variants", None)
        out = json.dumps(table, indent=1, ensure_ascii=False)
    if len(out.encode()) > LIMIT:
        for e in table["prices"].values():
            e["payers"] = e.get("payers", [])[:4]
        out = json.dumps(table, indent=1, ensure_ascii=False)
    return out + "\n"


def build(hid: str, path: Path, meta: dict) -> dict:
    source = {"cms_hpt_url": meta["cms_hpt_url"], "cms_hpt_via": meta.get("cms_hpt_via"),
              "hpt_location_name": meta.get("hpt_location_name"), "source_page_url": meta.get("source_page_url"),
              "file_url": meta["mrf_url"], "via": meta["via"], "zip_member": meta.get("zip_member"),
              "fetched_at": meta["fetched_at"], "sha256": meta["sha256"], "bytes": meta["bytes"]}
    table = prices.build_table(hid, path, {k: v for k, v in source.items() if v is not None})
    prices.PRICES_DIR.mkdir(parents=True, exist_ok=True)
    out = prices.PRICES_DIR / f"{hid}.json"
    out.write_text(fit(table))
    print(f"  {out.relative_to(prices.config.ROOT)}: {table['codes_found']}/{table['codes_requested']} codes, "
          f"{out.stat().st_size // 1024} KB, {table['layout']} v{table['template_version']}, "
          f"last_updated_on {table['last_updated_on']}; missing {table['missing_codes'] or 'none'}")
    return table


async def main_async(ids: list[str], force_fetch: bool) -> None:
    store, run_id = None, None
    for hid in ids:
        print(f"\n== {hid}")
        have = raw_file(hid)
        if not have and not force_fetch:
            print("  no raw file in data/price_files/; run with --fetch to download it")
            continue
        if force_fetch:
            if store is None:
                store = PolicyStore()
                run_id = store.start_run("prices", ids)
            try:
                await fetch(hid, store, run_id)
            except Exception as e:
                store.log(run_id=run_id, hospital_id=hid, stage="prices", product=None, url="", status=None,
                          nbytes=None, latency_ms=None, ok=False, note=f"price file fetch failed: {type(e).__name__}: {e}")
                print(f"  FETCH FAILED: {type(e).__name__}: {e}")
                if not have:
                    continue
            have = raw_file(hid)
        try:
            build(hid, *have)
        except Exception as e:
            print(f"  BUILD FAILED: {type(e).__name__}: {e}")
    if store is not None:
        store.finish_run(run_id, {"prices": ids})
        print("\nwrote", store.write_export())
        print(f"Bright Data requests logged (all crawls): {store.request_counts()}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--fetch", action="store_true", help="download the price file even if one is cached")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    if args.list:
        for hid in prices.hospitals():
            t = prices.table(hid)
            print(f"{hid:<20} {t['codes_found']:>3}/{t['codes_requested']} codes  {t['location_name'][:50]:<50} "
                  f"updated {t['last_updated_on']}  via {t['source']['via']}")
        return
    unknown = [i for i in args.ids if i not in SOURCES]
    if unknown:
        sys.exit(f"unknown hospital id(s) {unknown}; known: {sorted(SOURCES)}")
    asyncio.run(main_async(args.ids or list(SOURCES), args.fetch))


if __name__ == "__main__":
    main()
