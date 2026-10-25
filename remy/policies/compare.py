"""Same bill, same patient, every hospital in the database: what would each hospital's own policy give?
All money math is finance.py (the same functions the case flow uses)."""
import json
from datetime import timedelta

from .. import billcheck, config, finance
from .store import PolicyStore

SCENARIOS = {"default": "case.json", "freecare": "case_freecare.json"}


def load_case(scenario: str) -> dict:
    case = json.loads((config.DEMO_DIR / SCENARIOS.get(scenario, "case.json")).read_text())
    case["transactions"] = [{"id": f"t{i}", "merchant": "", **t} for i, t in enumerate(case["transactions"])]
    return case


def patient_facts(case: dict) -> dict:
    """Income, FPL and what was paid, computed once from the case (independent of the hospital)."""
    txns = case["transactions"]
    today = max(finance._d(t["date"]) for t in txns) + timedelta(days=1)
    inc = finance.income(txns, today=today)
    insured = billcheck.insured(case)
    rec = finance.reconcile(case["bill"], case["eob"] if insured else {}, txns,
                            corrections=None if insured else billcheck.line_corrections(case["bill"], case["hospital"]["name"]))
    med = finance.medical_spending(txns, case["bill"], case["eob"] if insured else {}, today=today)  # 12 months, from the bank
    return {"annual_income": inc["annual"], "income_source": inc["source"], "household_size": case["patient"]["household_size"],
            "other_medical": med["total"],
            "insured": insured, "patient_responsibility": rec["patient_responsibility"], "paid": rec["paid"],
            "owed_before_policy": rec["owed"], "billed_due": rec["billed_due"]}


def evaluate(facts: dict, policy: dict) -> dict:
    elig = finance.charity_discount(facts["annual_income"], facts["household_size"], policy["tiers"],
                                    insured=facts["insured"],
                                    out_of_pocket=round(facts["patient_responsibility"] + facts.get("other_medical", 0), 2))
    st = finance.settle(facts["patient_responsibility"], facts["paid"], elig["discount_pct"])
    t = elig["tier"]
    rule = None
    if t:
        rule = f"{t['applies_to']} patients <= {t['max_fpl_pct']:g}% FPL: {t['discount_pct']:g}% off" + (
            f" if out-of-pocket > {t['min_oop_pct_income']:g}% of income" if t.get("min_oop_pct_income") else "")
    t = t or {}
    return {"discount_pct": elig["discount_pct"], "fpl_pct": elig["fpl_pct"], "oop_pct_income": elig["oop_pct_income"],
            "balance": st["balance"], "refund": st["refund"], "discounted_share": st["discounted_share"],
            "rule": rule, "quote": t.get("quote"), "limit_quote": t.get("limit_quote") or None,
            "quote_source": t.get("source_url"), "why_not": None if rule else _why_not(elig, policy["tiers"]),
            "other_relief": [o for o in policy.get("other_relief", []) if _meets(o, elig)]}


def _meets(o: dict, elig: dict) -> bool:
    """Same eligibility test as finance.charity_discount, for relief that isn't a fixed percent."""
    who = o.get("applies_to") or "all"
    if (who == "insured" and not elig["insured"]) or (who == "uninsured" and elig["insured"]):
        return False
    if o.get("max_fpl_pct") is not None and elig["fpl_pct"] > o["max_fpl_pct"]:
        return False
    return o.get("min_oop_pct_income") is None or elig["oop_pct_income"] > o["min_oop_pct_income"]


def _why_not(elig: dict, tiers: list[dict]) -> str:
    if not tiers:
        return "no verified income tiers on record"
    reasons = []
    for t in tiers:
        if elig["fpl_pct"] > t["max_fpl_pct"]:
            reasons.append(f"income {elig['fpl_pct']:g}% FPL is above {t['max_fpl_pct']:g}%")
        elif t["applies_to"] == ("uninsured" if elig["insured"] else "insured"):
            reasons.append(f"tier is for {t['applies_to']} patients")
        elif t.get("min_oop_pct_income") is not None:
            reasons.append(f"out-of-pocket {elig['oop_pct_income']:g}% of income is not above {t['min_oop_pct_income']:g}%")
    return "; ".join(dict.fromkeys(reasons))


def compare(store: PolicyStore, scenario: str = "default") -> dict:
    case = load_case(scenario)
    facts = patient_facts(case)
    rows = []
    for h in store.hospitals():
        cur = store.current(h.id)
        if not cur:
            continue
        rows.append({"hospital_id": h.id, "hospital": h.name, "system": h.system, "version": cur["version"],
                     "fetched_at": cur["fetched_at"], "sources": cur["source_urls"], "status": cur["status"],
                     "notes": cur["extracted"].get("notes", ""),
                     **evaluate(facts, cur["extracted"])})
    rows.sort(key=lambda r: (r["balance"], -r["refund"]))
    return {"scenario": scenario, "patient": f"{case['patient']['first_name']} {case['patient']['last_name']}",
            "bill_hospital": case["hospital"]["name"], "facts": facts, "results": rows}
