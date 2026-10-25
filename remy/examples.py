"""The demo patients the app offers to start from ("Try an example"), in demo order: Raj leads, then Marcus, then
Rosa. Every entry point (Cases > New case, web/flows/drop-go.html, the Hospitals page's compare picker) reads this
one list from GET /api/examples, so adding a patient is one line in EXAMPLES.

A scenario is a named demo case (orchestrator.SCENARIOS: default = Rosa, freecare = Marcus, raj = Raj's
generated hero case once demo/scenarios/raj-cpmc/ is on the branch) or any generated one by its folder name
(demo/scenarios/<name>/case.json, see remy/scenarios.py). An example whose scenario isn't on this server yet is
listed with available=False: the pages show it disabled with a note, and starting it fails cleanly (404).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

EXAMPLES = [
    {"scenario": "raj", "name": "Raj", "label": "Raj — his bank history qualifies him: $16,089 → $0 + $400 back"},
    {"scenario": "freecare", "name": "Marcus", "label": "Marcus — his bills qualify him for free care", "tag": "HDHP"},
    {"scenario": "default", "name": "Rosa", "label": "Rosa — her ER bill skipped insurance and a payment",
     "tag": "ER bill"},
]


class UnknownScenario(ValueError):
    pass


def case_file(scenario: str) -> Path | None:
    """The scenario's case.json, or None when this server doesn't have it."""
    from .orchestrator import SCENARIOS  # the named demo cases, paths under demo/
    if scenario in SCENARIOS:
        p = config.DEMO_DIR / SCENARIOS[scenario]
    elif isinstance(scenario, str) and _NAME.match(scenario):
        p = config.DEMO_DIR / "scenarios" / scenario / "case.json"
    else:
        return None
    return p if p.is_file() else None


def exists(scenario: str) -> bool:
    return case_file(scenario) is not None


def load(scenario: str) -> dict:
    """The scenario's demo case. Raises UnknownScenario when this server doesn't have it."""
    p = case_file(scenario)
    if p is None:
        raise UnknownScenario(f"Unknown demo scenario: {scenario!r}")
    return json.loads(p.read_text())


def sample_docs(scenario: str) -> list[tuple[str, Path]]:
    """(upload name, path) of a generated scenario's bill and insurance statement (demo/scenarios/<name>/), which a
    live server reads when the case is started without uploads. Empty for the others (app.SAMPLE_DOCS has them)."""
    p = case_file(scenario)
    if p is None or p.parent.parent != config.DEMO_DIR / "scenarios":
        return []
    return [(f"{p.parent.name}-{f}", p.parent / f) for f in ("bill.pdf", "eob.pdf") if (p.parent / f).is_file()]


def listing() -> list[dict]:
    """EXAMPLES as the pages use them: available, plus the patient's household size and amount due from the case
    (household isn't on a bill, so a live server needs it from the page)."""
    out = []
    for ex in EXAMPLES:
        item = {"scenario": ex["scenario"], "name": ex["name"], "label": ex["label"], "available": False}
        try:
            case = load(ex["scenario"])
        except (UnknownScenario, OSError, ValueError):
            item["note"] = "Not on this server yet"
        else:
            due = float((case.get("bill") or {}).get("amount_due") or 0)
            item.update(available=True, household_size=int((case.get("patient") or {}).get("household_size") or 1),
                        amount_due=due, short=f"{ex['name']} · ${due:,.0f} {ex.get('tag', 'bill')}")
        out.append(item)
    return out
