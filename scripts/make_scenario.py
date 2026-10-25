"""Generate a realistic demo case from real hospital prices and policy, with planted errors whose answers
are known. Writes demo/scenarios/<name>/{case.json, expected.json} and, when reportlab is available,
bill.pdf + eob.pdf (watermarked SAMPLE).

    uv run python scripts/make_scenario.py rosa-real          # one scenario
    uv run python scripts/make_scenario.py all                # every scenario
    uv run python scripts/make_scenario.py --list
    uv run --with reportlab python scripts/make_scenario.py all   # also the PDFs

Offline: reads policydb/prices/*.json and policydb/policies.sqlite3 only, no paid API calls. Fixed seeds, so
re-running gives the same files. Scenarios are defined in remy/scenarios.py (SPECS).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from remy import config, scenarios  # noqa: E402


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "--list":
        for name, spec in scenarios.SPECS.items():
            print(f"{name:26} {spec['hospital_id']:18} {', '.join(spec.get('errors', {})) or 'no planted errors'}")
        return 0
    names = list(scenarios.SPECS) if argv[0] == "all" else argv
    unknown = [n for n in names if n not in scenarios.SPECS]
    if unknown:
        print(f"unknown scenario(s): {', '.join(unknown)}. Known: {', '.join(scenarios.SPECS)}")
        return 2
    pdfs_skipped = False
    for name in names:
        out = scenarios.write(name)
        exp = out["expected"]
        c = exp["correct"]
        rel = out["dir"].relative_to(config.ROOT)
        pdfs_skipped |= "bill.pdf" not in out["files"]
        plan = exp["plan"]["text"] if c["plan_months"] else "no plan"
        refund = f", refund ${c['refund']:,.2f}" if c["refund"] else ""
        print(f"{name}: wrote {rel}/ ({', '.join(out['files'])})")
        print(f"  bill ${exp['totals']['amount_due']:,.2f} -> owed ${exp['waterfall']['owed_before_discount']:,.2f}"
              f" -> {c['discount_pct']:g}% discount -> balance ${c['balance']:,.2f}{refund}; {plan}")
    if pdfs_skipped:
        print("PDFs skipped: reportlab is not installed. Run with `uv run --with reportlab python "
              "scripts/make_scenario.py ...` to render them.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
