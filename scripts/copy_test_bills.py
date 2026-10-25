"""Copy each generated scenario's bill and insurance statement into web/samples/test-bills/ (served at
/samples/test-bills/), named <scenario>-bill.pdf and <scenario>-eob.pdf:

    demo/scenarios/raj-cpmc/bill.pdf  ->  web/samples/test-bills/raj-cpmc-bill.pdf
    demo/scenarios/raj-cpmc/eob.pdf   ->  web/samples/test-bills/raj-cpmc-eob.pdf

    python scripts/copy_test_bills.py              # every scenario that has PDFs
    python scripts/copy_test_bills.py raj-cpmc     # just these

The PDFs come from scripts/make_scenario.py (with reportlab). A scenario without an EOB (uninsured) gets only
its bill. Offline, no API calls. Re-running is safe: unchanged files are left alone.
"""
import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "demo" / "scenarios"
DST = ROOT / "web" / "samples" / "test-bills"


def copy(names: list[str] | None = None) -> list[str]:
    """Returns what was written (paths relative to the repo). Raises FileNotFoundError for a named scenario
    without a bill.pdf."""
    dirs = [SRC / n for n in names] if names else sorted(d for d in SRC.iterdir() if (d / "bill.pdf").is_file())
    DST.mkdir(parents=True, exist_ok=True)
    wrote = []
    for d in dirs:
        if not (d / "bill.pdf").is_file():
            raise FileNotFoundError(f"{d.relative_to(ROOT)}/bill.pdf not found: is the scenario on this branch?")
        for kind in ("bill", "eob"):
            src, dst = d / f"{kind}.pdf", DST / f"{d.name}-{kind}.pdf"
            if src.is_file() and not (dst.is_file() and filecmp.cmp(src, dst, shallow=False)):
                shutil.copyfile(src, dst)
                wrote.append(str(dst.relative_to(ROOT)))
    return wrote


if __name__ == "__main__":
    try:
        out = copy(sys.argv[1:] or None)
    except FileNotFoundError as e:
        sys.exit(str(e))
    print("\n".join(f"wrote {p}" for p in out) or "test bills already up to date")
