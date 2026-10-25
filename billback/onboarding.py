"""Onboarding: build a household's profile by asking only what can change the answer.

The answer is what each hospital's own policy (verified tiers in policydb/) would give this household:
finance.charity_discount, the same function a case uses. Every unknown (household size, income, insurance,
medical bills, hospital) is a set of plausible values; the grid of all combinations is every household this
patient could still be. Its spread is the metric:

    certainty   share of that grid that agrees with the most common outcome (1.0 = decided)
    next        the unknown with the most information gain: the expected drop in the outcome's entropy once it's
                answered. (Gain in certainty alone misses questions that only settle the minority outcomes.)

So a question that can't move the outcome is never asked: with a bank-verified income far under every
limit, household size doesn't matter and onboarding ends early. Bank income (Nessie) is take-home pay;
policies test gross, so it stands for a range (take-home / 0.85 .. / 0.75), never a single number. A before-tax
income the patient gives is checked against that range (finance.stated_check): one below it counts as the bank's
lowest gross figure.

What the patient told us is kept for their next case (save_profile / find_profile): in Tiger Data when it's on,
else a small JSON file per profile under data/profiles/. Same id either way: tiger.profile_id (name + DOB)."""
import json
import math
from collections import Counter
from datetime import datetime, timezone

from . import events, finance, tiger
from .policies.store import PolicyStore

FIELDS = ("hospital_id", "household_size", "income", "insured", "medical_oop")

# Plausible values for an unknown. Income in gross dollars a year; medical bills as a share of income.
HOUSEHOLD = (1, 2, 3, 4, 5, 6)
INCOME = (12_000, 20_000, 28_000, 36_000, 45_000, 55_000, 65_000, 80_000, 95_000, 115_000, 140_000, 180_000)
# take-home is ~75-85% of gross (finance.TAKE_HOME_SHARE)
GROSS_FROM_TAKE_HOME = (finance.TAKE_HOME_HIGH, finance.TAKE_HOME_SHARE, finance.TAKE_HOME_LOW)
OOP_SHARE = (0.0, 0.05, 0.15)

QUESTIONS = {
    "hospital_id": {"kind": "select", "q": "Which hospital is the bill from?",
                    "why": "Each hospital sets its own income limits."},
    "household_size": {"kind": "number", "q": "How many people are in your household?",
                       "why": "You, a partner, and anyone you support. The poverty line grows with each person "
                              f"(${finance.FPL_BASE:,} for one, +${finance.FPL_PER_EXTRA:,} each, {finance.FPL_YEAR})."},
    "income": {"kind": "money", "q": "Roughly what does your household earn a year, before taxes?",
               "why": "Hospital policies test income before taxes, for everyone in the household."},
    "insured": {"kind": "yesno", "q": "Do you have health insurance?",
                "why": "Some tiers are only for uninsured patients; insured patients can qualify through high "
                       "medical bills."},
    "medical_oop": {"kind": "money", "q": "About how much have you paid for medical care in the last 12 months?",
                    "why": "Insured patients qualify at some hospitals when medical bills pass a share of income "
                           "(often 10%)."},
}


def hospitals_with_tiers(store: PolicyStore | None = None) -> list[dict]:
    """[{id, name, city, tiers}] for every hospital with at least one verified tier."""
    own = store is None
    store = store or PolicyStore()
    out = []
    for h in store.hospitals():
        row = store.current(h.id)
        if not row:
            continue
        ex = row["extracted"] if isinstance(row["extracted"], dict) else json.loads(row["extracted"] or "{}")
        tiers = finance.clean_tiers([t for t in ex.get("tiers") or [] if t.get("verified")])
        if tiers:
            out.append({"id": h.id, "name": h.name, "city": h.city, "tiers": tiers})
    if own:
        store.close()
    return out


def _values(field: str, a: dict, bank: dict | None, hospitals: list[dict]) -> list:
    v = a.get(field)
    if field == "hospital_id":
        known = [h for h in hospitals if h["id"] == v]
        return [known[0]["id"]] if known else [h["id"] for h in hospitals]
    if v is not None:
        return [v]
    if field == "household_size":
        return list(HOUSEHOLD)
    if field == "income":
        return ([round(bank["annual"] / s) for s in GROSS_FROM_TAKE_HOME] if bank and bank.get("annual")
                else list(INCOME))
    if field == "insured":
        return [True, False]
    return list(OOP_SHARE)  # medical_oop: shares of income until answered


def _grid(a: dict, bank: dict | None, hospitals: list[dict]) -> list[dict]:
    pts = [{}]
    for f in FIELDS:
        pts = [{**p, f: v} for p in pts for v in _values(f, a, bank, hospitals)]
    return pts


def _outcome(p: dict, a: dict, tiers: dict) -> tuple[float, float]:
    oop = p["medical_oop"] if a.get("medical_oop") is not None else p["medical_oop"] * p["income"]
    e = finance.charity_discount(p["income"], p["household_size"], tiers[p["hospital_id"]],
                                 insured=p["insured"], out_of_pocket=oop if p["insured"] else 0.0)
    return e["discount_pct"], e["fpl_pct"]


def _certainty(outcomes: list) -> float:
    return Counter(outcomes).most_common(1)[0][1] / len(outcomes) if outcomes else 1.0


def _entropy(outcomes: list) -> float:
    n = len(outcomes)
    return -sum(c / n * math.log2(c / n) for c in Counter(outcomes).values()) if n else 0.0


def _needs_oop(hospitals: list[dict]) -> bool:
    return any(t.get("min_oop_pct_income") is not None for h in hospitals for t in h["tiers"])


def assess(answers: dict, bank: dict | None = None, skipped: list[str] | None = None,
           hospitals: list[dict] | None = None) -> dict:
    """answers: any of FIELDS (income in gross $/yr, medical_oop in $). bank: finance.income() of the patient's
    account, or None. skipped: fields the patient doesn't know. Returns the certainty, the outcome it points to,
    the FPL range still possible, and the next question (None when decided or nothing is left to ask)."""
    hospitals = hospitals if hospitals is not None else hospitals_with_tiers()
    skipped = set(skipped or [])
    a = {k: answers.get(k) for k in FIELDS}
    if not hospitals:
        return {"certainty": 0.0, "decided": False, "next": None, "fpl_range": None, "outcome": None,
                "per_hospital": [], "asked_why": "No verified hospital policies on file"}
    tiers = {h["id"]: h["tiers"] for h in hospitals}
    check = (finance.stated_check(bank, a["income"]) if a.get("income") is not None and bank and bank.get("annual")
             else None)
    if check:  # the same double check a case makes: never count less than the bank shows
        a["income"] = check["used"]
    pts = _grid(a, bank, hospitals)
    res = [_outcome(p, a, tiers) for p in pts]
    discounts = [d for d, _ in res]
    certainty = _certainty(discounts)

    open_fields = [f for f in FIELDS if a.get(f) is None and f not in skipped
                   and not (f == "medical_oop" and (a.get("insured") is False or not _needs_oop(hospitals)))]
    h = _entropy(discounts)
    gains = {}
    for f in open_fields:
        groups: dict = {}
        for p, d in zip(pts, discounts):
            groups.setdefault(p[f], []).append(d)
        gains[f] = h - sum(len(g) * _entropy(g) for g in groups.values()) / len(pts)
    best = max(open_fields, key=lambda f: (round(gains[f], 9), -FIELDS.index(f))) if open_fields else None
    decided = certainty == 1.0
    nxt = None
    if best and not decided and gains[best] > 1e-9:
        # gain: share of the remaining uncertainty this answer is expected to remove
        nxt = {"field": best, **QUESTIONS[best], "gain": round(gains[best] / h, 3)}
        if best == "hospital_id":
            nxt["options"] = [{"id": h["id"], "name": h["name"], "city": h["city"]} for h in hospitals]
        if best == "income" and bank and bank.get("annual"):
            nxt["prefill"] = round(bank["annual"] / finance.TAKE_HOME_SHARE)
            nxt["prefill_note"] = f"Your bank shows about ${bank['annual']:,.0f}/yr take-home ({bank['source']})"

    per = []
    for h in hospitals:
        ds = [d for p, d in zip(pts, discounts) if p["hospital_id"] == h["id"]]
        if ds:
            top, n = Counter(ds).most_common(1)[0]
            per.append({"id": h["id"], "name": h["name"], "discount_pct": top, "certainty": round(n / len(ds), 3)})
    top = Counter(discounts).most_common(1)[0][0]
    fpls = [f for _, f in res]
    return {"certainty": round(certainty, 3), "decided": decided, "outcome": _label(top, per),
            "discount_pct": top, "fpl_range": [min(fpls), max(fpls)], "next": nxt, "per_hospital": per,
            "possibilities": len(pts), "income_check": check}


def _label(top: float, per: list[dict]) -> str:
    what = "free care" if top >= 100 else f"{top:.0f}% off" if top else "no discount"
    if len(per) == 1:
        return f"Likely {what} at {per[0]['name']}"
    n = sum(1 for p in per if p["discount_pct"] == top)
    return f"Likely {what} at {n} of {len(per)} hospitals on file"


def clean(answers: dict) -> dict:
    """Patient input -> typed answers; anything malformed is treated as not answered."""
    a = answers if isinstance(answers, dict) else {}
    out = {k: str(a[k]).strip()[:80] for k in ("first_name", "last_name", "dob", "zip", "hospital_id")
           if isinstance(a.get(k), (str, int)) and str(a[k]).strip()}

    def num(k, lo, hi, cast=float):
        try:
            v = cast(a[k])
        except (KeyError, TypeError, ValueError):
            return
        if lo <= v <= hi:
            out[k] = v
    num("household_size", 1, 20, int)
    num("income", 0, 10_000_000)
    num("medical_oop", 0, 10_000_000)
    if isinstance(a.get("insured"), bool):
        out["insured"] = a["insured"]
    return out


PROFILE_FIELDS = ("household_size", "insured", "income", "medical_oop")


def _profiles_dir():
    d = events.DATA_DIR / "profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def clear_local_profiles() -> int:
    """Demo reset: forget the onboarding answers kept on this server (Tiger Data profiles stay)."""
    d = events.DATA_DIR / "profiles"
    n = 0
    for f in d.glob("*.json") if d.exists() else []:
        f.unlink()
        n += 1
    return n


async def save_profile(answers: dict, assessment: dict, hospital: str | None) -> dict:
    """Keep what the patient told us for their next case. Tiger Data when it's on; otherwise (or if Tiger fails)
    data/profiles/<profile_id>.json, with the answers only (no name or DOB). saved: stored in Tiger Data."""
    if tiger.enabled():
        try:
            return {"profile_id": await tiger.save_onboarding(answers, assessment, hospital), "saved": True,
                    "stored": "tiger"}
        except Exception as e:
            print("Tiger onboarding save failed, keeping it locally:", type(e).__name__)
    pid = tiger.profile_id(answers)
    row = {"id": pid, **{k: answers.get(k) for k in PROFILE_FIELDS}, "hospital": hospital,
           "onboarded_at": datetime.now(timezone.utc).isoformat()}
    d = _profiles_dir()
    tmp = d / f".{pid}.json.tmp"
    tmp.write_text(json.dumps(row))
    tmp.replace(d / f"{pid}.json")
    return {"profile_id": pid, "saved": False, "stored": "local"}


async def find_profile(patient: dict) -> dict | None:
    """What this patient (name + DOB) told us at onboarding: {household_size, insured, income, medical_oop}
    (None where not answered), or None."""
    if tiger.enabled():
        try:
            row = await tiger.get_profile(patient)
        except Exception as e:
            print("profile lookup failed:", type(e).__name__)
            row = None
        if row:
            return {"household_size": row.get("household_size"), "insured": row.get("insured"),
                    "income": row.get("income_reported"), "medical_oop": row.get("medical_oop")}
    f = _profiles_dir() / f"{tiger.profile_id(patient)}.json"
    try:
        row = json.loads(f.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return {k: row.get(k) for k in PROFILE_FIELDS}
