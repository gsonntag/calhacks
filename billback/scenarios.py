"""Patient-case generator: realistic demo cases built from REAL hospital data, with planted errors whose
correct answers are known.

What is real: the hospital (registry), its prices (policydb/prices/<id>.json, from the hospital's own CMS
price-transparency file) and its financial-assistance policy (policydb/, crawled via Bright Data).
What is synthetic: the patient, the insurer (always fictional), the insurance terms and the bank history.

    from billback import scenarios
    out = scenarios.build("rosa-real")     # {"case": {...}, "expected": {...}}
    scenarios.write("rosa-real")            # demo/scenarios/rosa-real/{case,expected}.json (+ PDFs)

The case is in the same format as demo/case.json. expected.json lists every planted error with its exact
effect, the correct balance / discount / refund / plan (computed with finance.py, exactly as the app does)
and the findings a correct audit should report.

Money is handled in integer cents so every total adds up exactly.

Planted errors (each optional, `errors` in a spec):
  insurance_not_applied   the bill asks for full charges although the insurer processed the claim
  uncredited_payment      {"amount", "date"}: a payment in the bank that the bill doesn't show
  duplicate_line          {"code"}: that bill line appears twice (the insurer denies the copy)
  price_above_posted      {"code", "billed"}: a line billed above the hospital's own posted gross charge
  duplicate_bank_charge   {"merchant", "amount", "date", "description"}: same charge twice, unrelated to the bill
  refund_owed             {"payments": [{"amount", "date"}]}: payments the bill DID credit, which exceed the
                          patient's discounted share, so the hospital owes a refund
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from statistics import median

from . import billcheck, config, finance, prices
from .policies import registry
from .policies.store import PolicyStore

OUT_DIR = config.DEMO_DIR / "scenarios"
DEFAULT_AS_OF = "2026-10-25"
GENERATOR_VERSION = 2  # 2: a year of messier bank history, medical spending for the high-medical-cost rule
BANK_DAYS = 365

ALLOWED_STATISTIC = ("median of the payer-specific dollar rates the hospital's file lists for the code "
                     "(price table field `negotiated_median_of_payers`) x billed units")
FALLBACK_STATISTIC = ("the file lists no payer rate for this code: estimated as gross x this hospital's median "
                      "(payer median / gross) ratio across every code in its table that has both")

# ---------------------------------------------------------------------------------------------- specs

ROSA_ADDRESS = {"street_number": "2210", "street_name": "Sacramento St", "city": "Berkeley", "state": "CA",
                "zip": "94702"}

SPECS: dict[str, dict] = {
    "rosa-real": {
        "seed": 1101,
        "story": "Rosa's ER visit at CPMC Van Ness (abdominal pain, CT with contrast), rebuilt from CPMC's real "
                 "price file. Insured, about 228% of the poverty line. The bill ignores her insurance, misses her "
                 "$500 payment and lists the IV push twice.",
        "hospital_id": "sutter-cpmc",
        "patient": {"first_name": "Rosa", "last_name": "Martinez", "household_size": 1, "dob": "1991-05-22",
                    "phone_label": "Rosa's phone", "address": ROSA_ADDRESS},
        "pay": {"employer": "EAST BAY SCHOOL DIST", "frequency": "biweekly", "amount": 1400.00,
                "first_payday": "2026-07-17"},
        "rent": {"merchant": "Sacramento St Apts", "amount": 1050.00, "description": "Rent (shared apt)"},
        "merchants": [("Berkeley Bowl", "Groceries", 38, 96), ("Monterey Market", "Groceries", 18, 55),
                      ("Shell Gas University Ave", "Gas", 34, 62), ("Cafe Leila", "Coffee", 5, 14),
                      ("BART Clipper Reload", "Transit", 20, 40), ("East Bay Wireless", "Phone bill", 45, 45)],
        "medical": [("WALGREENS #4410", "Prescription", 23.17, "2026-01-14"),
                    ("Elmwood Dental Group", "Cleaning copay", 60.00, "2026-04-09"),
                    ("WALGREENS #4410", "Prescription", 18.42, "2026-08-03")],
        "insurance": {"insurer": "Fogline Health Plan", "member_id": "FHP-7712093", "claim_number": "C-2026-0815-44917",
                      "deductible_remaining": 750.00, "coinsurance_pct": 20, "oop_max_remaining": 3000.00,
                      "processed": "2026-08-21"},
        "visit": {"service_date": "2026-08-02", "statement_date": "2026-10-10", "due_date": "2026-11-03",
                  "account_number": "CPMC-48213977",
                  "notice": "FINAL NOTICE - account may be referred to a collection agency if not paid by 2026-11-03"},
        "procedures": [("99284", 1), ("74177", 1), ("80053", 1), ("85025", 1), ("96374", 1), ("J2405", 1)],
        "errors": {"insurance_not_applied": True,
                   "uncredited_payment": {"amount": 500.00, "date": "2026-09-14"},
                   "duplicate_line": {"code": "96374"}},
    },
    "marcus-real": {
        "seed": 1102,
        "story": "Marcus's bike crash, seen in UCSF's emergency department (CT head, neck and abdomen, X-rays, "
                 "wound repair, splint), rebuilt from UCSF's real price file. High-deductible plan, household "
                 "of 3 at about 152% of the poverty line. His share is over 10% of his income, so UCSF's own "
                 "policy writes it off: free care.",
        "hospital_id": "ucsf-health",
        "location": "UCSF Medical Center (Parnassus)",
        "patient": {"first_name": "Marcus", "last_name": "Lee", "household_size": 3, "dob": "1984-03-17",
                    "phone_label": "Marcus's phone",
                    "address": {"street_number": "1418", "street_name": "Ashby Ave", "city": "Berkeley",
                                "state": "CA", "zip": "94703"}},
        "pay": {"employer": "ALAMEDA COUNTY TRANSIT", "frequency": "biweekly", "amount": 1560.00,
                "first_payday": "2026-07-17"},
        "rent": {"merchant": "Ashby Ave Rentals", "amount": 2100.00, "description": "Rent"},
        "merchants": [("Berkeley Bowl", "Groceries", 90, 210), ("Grocery Outlet Berkeley", "Groceries", 40, 120),
                      ("Chevron San Pablo Ave", "Gas", 40, 70), ("Golden Bear Pharmacy", "Pharmacy", 12, 30),
                      ("East Bay Wireless", "Phone bill", 85, 85)],
        "medical": [("Kids First Pediatrics", "Office visit copay", 30.00, "2026-01-22"),
                    ("Kids First Pediatrics", "Office visit copay", 30.00, "2026-05-07"),
                    ("WALGREENS #2291", "Prescription", 38.60, "2026-07-21"),
                    ("Bay Physical Therapy", "PT session copay", 45.00, "2026-08-12"),
                    ("Bay Physical Therapy", "PT session copay", 45.00, "2026-08-26"),
                    ("Bay Physical Therapy", "PT session copay", 45.00, "2026-09-09")],
        "insurance": {"insurer": "Tidewater Arc Health HDHP", "member_id": "TAH-55120847",
                      "claim_number": "C-2026-0731-90215", "deductible_remaining": 5000.00, "coinsurance_pct": 30,
                      "oop_max_remaining": 7000.00, "processed": "2026-08-04"},
        "visit": {"service_date": "2026-07-19", "statement_date": "2026-10-08", "due_date": "2026-11-07",
                  "account_number": "UCSF-30772154",
                  "notice": "PAST DUE - second notice. Please pay the balance below or call to arrange payment."},
        "procedures": [("99285", 1), ("70450", 1), ("72125", 1), ("74177", 1), ("71046", 1), ("73110", 1),
                       ("73030", 1), ("12001", 1), ("29125", 1), ("96374", 1), ("96375", 1), ("96360", 1),
                       ("85025", 1), ("80053", 1), ("85610", 1), ("36415", 1), ("J2270", 1), ("J2405", 1),
                       ("J7030", 1)],
        "errors": {},
    },
    "dana-altabates-refund": {
        "seed": 1103,
        "story": "Dana's abdominal-pain visit at Alta Bates Summit (ultrasound, then CT), real Sutter prices. "
                 "Insured, household of 2 at about 184% of the poverty line. The bill applies her insurance "
                 "and credits the $750 she has paid, but her share is over 10% of her income, so Sutter's policy "
                 "writes it off and the $750 must come back. Also a gym double charge in her bank.",
        "hospital_id": "sutter-alta-bates",
        "patient": {"first_name": "Dana", "last_name": "Okafor", "household_size": 2, "dob": "1988-11-02",
                    "phone_label": "Dana's phone",
                    "address": {"street_number": "5521", "street_name": "Telegraph Ave", "city": "Oakland",
                                "state": "CA", "zip": "94609"}},
        "pay": {"employer": "PIEDMONT UNIFIED", "frequency": "biweekly", "amount": 1500.00,
                "first_payday": "2026-07-10"},
        "rent": {"merchant": "Telegraph Flats LLC", "amount": 1650.00, "description": "Rent"},
        "merchants": [("Temescal Produce", "Groceries", 30, 95), ("Rockridge Market Hall", "Groceries", 25, 80),
                      ("Arco 51st St", "Gas", 30, 58), ("Bakesale Betty", "Lunch", 9, 18),
                      ("Bay Wireless Co", "Phone bill", 60, 60)],
        "medical": [("Oakland Urgent Care", "Visit copay", 75.00, "2026-02-03"),
                    ("Rockridge Family Dental", "Filling copay", 145.00, "2026-03-18"),
                    ("CVS/PHARMACY #9921", "Prescription", 34.50, "2026-07-11")],
        "insurance": {"insurer": "Fogline Health Plan", "member_id": "FHP-2048811", "claim_number": "C-2026-0716-31842",
                      "deductible_remaining": 4000.00, "coinsurance_pct": 25, "oop_max_remaining": 6500.00,
                      "processed": "2026-07-30"},
        "visit": {"service_date": "2026-07-10", "statement_date": "2026-10-05", "due_date": "2026-11-04",
                  "account_number": "ABSMC-77120458",
                  "notice": "Statement - please pay the balance below or call to set up a payment plan."},
        "procedures": [("99284", 1), ("76705", 1), ("74176", 1), ("80053", 1), ("85025", 1), ("83690", 1),
                       ("81003", 1), ("96360", 1), ("J2405", 1)],
        "errors": {"refund_owed": {"payments": [{"amount": 250.00, "date": "2026-08-12"},
                                                {"amount": 250.00, "date": "2026-09-12"},
                                                {"amount": 250.00, "date": "2026-10-02"}]},
                   "duplicate_bank_charge": {"merchant": "Lakeshore Fitness", "amount": 39.99, "date": "2026-09-03",
                                             "description": "Monthly membership"}},
    },
    "amara-kaiser-uninsured": {
        "seed": 1104,
        "story": "Amara, uninsured, twisted her ankle and was seen at Kaiser's Oakland emergency department "
                 "(real Kaiser prices). Household of 2 at about 300% of the poverty line: Kaiser's policy gives "
                 "50% off. The bill charges the ankle X-ray above Kaiser's own posted price, lists the foot "
                 "X-ray twice, and misses the $300 she paid at discharge. Also a double charge in her bank.",
        "hospital_id": "kaiser-oakland",
        "patient": {"first_name": "Amara", "last_name": "Nwosu", "household_size": 2, "dob": "1995-02-14",
                    "phone_label": "Amara's phone",
                    "address": {"street_number": "3120", "street_name": "Fruitvale Ave", "city": "Oakland",
                                "state": "CA", "zip": "94602"}},
        "pay": {"employer": "BAYSIDE LOGISTICS INC", "frequency": "semimonthly", "amount": 2640.00,
                "first_payday": "2026-07-15"},
        "rent": {"merchant": "Fruitvale Court Apts", "amount": 1900.00, "description": "Rent"},
        "merchants": [("Farmer Joe's Marketplace", "Groceries", 35, 120), ("Mi Pueblo Foods", "Groceries", 20, 70),
                      ("Valero MacArthur Blvd", "Gas", 32, 60), ("Peet's Coffee", "Coffee", 5, 12),
                      ("Bay Wireless Co", "Phone bill", 70, 70)],
        "medical": [("Fruitvale Walk-In Clinic", "Visit", 95.00, "2026-05-12"),
                    ("CVS/PHARMACY #3381", "Prescription", 14.99, "2026-08-23")],
        "insurance": None,
        "visit": {"service_date": "2026-08-22", "statement_date": "2026-10-12", "due_date": "2026-11-11",
                  "account_number": "KPOAK-60331972",
                  "notice": "Self-pay statement - payment due by 2026-11-11."},
        "procedures": [("99283", 1), ("73610", 1), ("73630", 1), ("96372", 1), ("J1885", 1)],
        "errors": {"price_above_posted": {"code": "73610", "billed": 1350.00},
                   "duplicate_line": {"code": "73630"},
                   "uncredited_payment": {"amount": 300.00, "date": "2026-08-22"},
                   "duplicate_bank_charge": {"merchant": "StreamBox Video", "amount": 17.99, "date": "2026-09-19",
                                             "description": "Subscription"}},
    },
    "lena-johnmuir": {
        "seed": 1105,
        "story": "Lena's chest-pain workup at John Muir Walnut Creek (CT angiogram, troponins, ECG), real John "
                 "Muir prices. Insured, single, about 515% of the poverty line: no charity discount. The bill "
                 "ignores her insurance and charges the CT angiogram above John Muir's posted price. Also a "
                 "double charge in her bank.",
        "hospital_id": "john-muir-wc",
        "location": "John Muir Health Walnut Creek Medical Center",
        "patient": {"first_name": "Lena", "last_name": "Fischer", "household_size": 1, "dob": "1979-07-30",
                    "phone_label": "Lena's phone",
                    "address": {"street_number": "88", "street_name": "Mountain View Blvd", "city": "Pleasant Hill",
                                "state": "CA", "zip": "94523"}},
        "pay": {"employer": "DIABLO VALLEY ENGINEERING", "frequency": "biweekly", "amount": 3100.00,
                "first_payday": "2026-07-10"},
        "rent": {"merchant": "Gregory Lane Residences", "amount": 2450.00, "description": "Rent"},
        "merchants": [("Pleasant Hill Safeway", "Groceries", 45, 140), ("Trader Vic Grocers", "Groceries", 20, 75),
                      ("Shell Contra Costa Blvd", "Gas", 40, 75), ("Sideboard Cafe", "Coffee", 6, 16),
                      ("Diablo Wireless", "Phone bill", 75, 75)],
        "medical": [("Diablo Valley Optometry", "Eye exam", 120.00, "2026-06-03"),
                    ("WALGREENS #7710", "Prescription", 12.99, "2026-09-06")],
        "insurance": {"insurer": "Tidewater Arc Health", "member_id": "TAH-30918477", "claim_number": "C-2026-0905-11873",
                      "deductible_remaining": 500.00, "coinsurance_pct": 20, "oop_max_remaining": 4000.00,
                      "processed": "2026-09-22"},
        "visit": {"service_date": "2026-09-05", "statement_date": "2026-10-14", "due_date": "2026-11-13",
                  "account_number": "JMH-90417735",
                  "notice": "Statement - payment due by 2026-11-13."},
        "procedures": [("99284", 1), ("71275", 1), ("84484", 2), ("93005", 1), ("85025", 1), ("80048", 1),
                       ("96374", 1), ("J2270", 1)],
        "errors": {"insurance_not_applied": True,
                   "price_above_posted": {"code": "71275", "billed": 6200.00},
                   "duplicate_bank_charge": {"merchant": "Gregory Lane Cleaners", "amount": 26.50,
                                             "date": "2026-09-28", "description": "Dry cleaning"}},
    },
}

ERROR_TYPES = ("insurance_not_applied", "uncredited_payment", "duplicate_line", "price_above_posted",
               "duplicate_bank_charge", "refund_owed")


# --------------------------------------------------------------------------------------------- helpers

def cents(x: float) -> int:
    return int(round(float(x) * 100))


def dollars(c: int) -> float:
    return round(c / 100, 2)


def _iso(d: date) -> str:
    return d.isoformat()


@lru_cache(maxsize=1)
def real_insurer_names() -> frozenset[str]:
    """Every payer / payer family named in any price table: generated EOBs must never use one."""
    names = set()
    for h in prices.hospitals():
        for e in prices.load(h).values():
            for p in e.get("payers", []):
                names.update(n.lower() for n in (p.get("family"), p.get("payer")) if n)
    return frozenset(names)


def hospital_ratio(hospital_id: str) -> float:
    """Median of (payer median / gross) across this hospital's codes that have both."""
    rs = [e["negotiated_median_of_payers"] / e["gross"] for e in prices.load(hospital_id).values()
          if e.get("gross") and e.get("negotiated_median_of_payers")]
    return round(median(rs), 4)


def policy_snapshot(hospital_id: str) -> dict:
    row = PolicyStore().current(hospital_id)
    if not row:
        return {"tiers": [], "payment_plan": {}, "version": None, "fetched_at": None, "source_urls": [], "status": None}
    ex = row["extracted"] or {}
    return {"tiers": finance.clean_tiers(ex.get("tiers")), "payment_plan": ex.get("payment_plan") or {},
            "version": row["version"], "fetched_at": row["fetched_at"], "source_urls": row["source_urls"],
            "status": row["status"]}


def _compact(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def mirror_ids(txns: list[dict]) -> list[dict]:
    """The ids the app's offline bank (integrations.nessie.LocalMirror) gives these transactions."""
    return [{"id": f"mirror-{i:03d}", "merchant": "", **t} for i, t in enumerate(txns)]


# ------------------------------------------------------------------------------------------- the bill

def price_lines(spec: dict) -> tuple[list[dict], list[dict]]:
    """Bill line items (case format) and their provenance, priced from the hospital's real table."""
    hid, errs = spec["hospital_id"], spec.get("errors", {})
    over = errs.get("price_above_posted") or {}
    dup = (errs.get("duplicate_line") or {}).get("code")
    lines, prov = [], []
    for code, units in spec["procedures"]:
        e = prices.lookup(hid, code)
        if not e or e.get("gross") is None:
            raise ValueError(f"{hid} has no posted gross charge for {code}")
        posted = cents(e["gross"]) * units
        billed = posted
        planted = None
        if over.get("code") == code:
            billed = cents(over["billed"]) * units
            if billed <= posted:
                raise ValueError(f"price_above_posted for {code} must exceed the posted ${dollars(posted):,.2f}")
            planted = "price_above_posted"
        copies = 2 if dup == code else 1
        for k in range(copies):
            lines.append({"code": code, "description": e["description"], "units": units, "amount": dollars(billed)})
            prov.append({"line": len(lines), "code": code, "units": units, "description_from_file": e["description"],
                         "common_name": e.get("common_name"), "setting": e.get("setting"),
                         "posted_gross_per_unit": e["gross"], "posted_gross": dollars(posted),
                         "billed": dollars(billed),
                         "planted_error": "duplicate_line" if k == 1 else planted,
                         "duplicate_of_line": len(lines) - 1 if k == 1 else None,
                         "negotiated_median_of_payers": e.get("negotiated_median_of_payers"),
                         "payer_dollar_rates": e.get("payer_dollar_rates", 0),
                         "file_notes": e.get("notes", [])})
    if dup and dup not in {c for c, _ in spec["procedures"]}:
        raise ValueError(f"duplicate_line code {dup} is not among the procedures")
    if over and over["code"] not in {c for c, _ in spec["procedures"]}:
        raise ValueError(f"price_above_posted code {over['code']} is not among the procedures")
    return lines, prov


def adjudicate(spec: dict, prov: list[dict]) -> dict:
    """The fictional insurer's EOB: allowed amounts from the real negotiated rates, then deductible,
    coinsurance and out-of-pocket max, line by line, in cents."""
    ins = spec["insurance"]
    if ins["insurer"].lower() in real_insurer_names() or any(
            n in ins["insurer"].lower() for n in real_insurer_names() if len(n) > 4):
        raise ValueError(f"insurer name {ins['insurer']!r} matches a real payer; use a fictional one")
    ratio = hospital_ratio(spec["hospital_id"])
    ded_left, oop_left = cents(ins["deductible_remaining"]), cents(ins["oop_max_remaining"])
    if oop_left < ded_left:
        raise ValueError("oop_max_remaining must be at least deductible_remaining")
    pct = int(ins["coinsurance_pct"])
    rows = []
    for p in prov:
        billed = cents(p["billed"])
        if p["planted_error"] == "duplicate_line":
            rows.append({"line": p["line"], "code": p["code"], "billed": billed, "allowed": 0, "deductible": 0,
                         "coinsurance": 0, "plan_paid": 0, "patient": 0, "basis": None,
                         "remark": f"Denied: duplicate of line {p['duplicate_of_line']}"})
            continue
        if p["negotiated_median_of_payers"] is not None:
            rate, basis = cents(p["negotiated_median_of_payers"]), "median of payer rates"
        else:
            rate, basis = int(round(cents(p["posted_gross_per_unit"]) * ratio)), f"estimated: gross x {ratio}"
        allowed = min(rate * p["units"], cents(p["posted_gross"]), billed)
        ded = min(allowed, ded_left)
        ded_left -= ded
        coins = ((allowed - ded) * pct + 50) // 100
        patient = ded + coins
        if patient > oop_left:
            patient = oop_left
            coins = patient - ded
        oop_left -= patient
        rows.append({"line": p["line"], "code": p["code"], "billed": billed, "allowed": allowed, "deductible": ded,
                     "coinsurance": coins, "plan_paid": allowed - patient, "patient": patient, "basis": basis,
                     "remark": ""})
    tot = {k: sum(r[k] for r in rows) for k in ("billed", "allowed", "deductible", "coinsurance", "plan_paid", "patient")}
    return {"rows": rows, "totals": tot, "ratio": ratio}


# ------------------------------------------------------------------------------------------- the bank

def _paydays(pay: dict, start: date, end: date) -> list[date]:
    """Paydays in [start, end]. `first_payday` anchors the schedule (any real payday); pay runs before it too."""
    f = pay["frequency"]
    anchor = date.fromisoformat(pay["first_payday"])
    out = []
    if f in ("weekly", "biweekly"):
        step = timedelta(days=7 if f == "weekly" else 14)
        d = anchor - step * ((anchor - start).days // step.days + 1) if anchor > start else anchor
        while d <= end:
            if d >= start:
                out.append(d)
            d += step
    elif f in ("semimonthly", "monthly"):
        y, m = start.year, start.month
        while date(y, m, 1) <= end:
            nxt = date(y + (m == 12), m % 12 + 1, 1)
            days = [nxt - timedelta(days=1)] if f == "monthly" else [date(y, m, 15), nxt - timedelta(days=1)]
            out += [x for x in days if start <= x <= end]
            y, m = nxt.year, nxt.month
    else:
        raise ValueError(f"unknown pay frequency {f}")
    return out


def _months(start: date, end: date, day: int) -> list[date]:
    out, m = [], date(start.year, start.month, day)
    while m <= end:
        if m >= start:
            out.append(m)
        m = date(m.year + (m.month == 12), m.month % 12 + 1, day)
    return out


# Unrelated noise every bank history has (spread over the year, amounts with cents): online shopping, Venmo,
# a streaming subscription. Cash (ATM withdrawals) is added in bank().
NOISE = [("Amazon Marketplace", ("AMZN Mktp US*{code}", "AMAZON.COM*{code}"), 9, 85),
         ("Venmo", ("VENMO PAYMENT {num}", "VENMO *{num}"), 12, 60),
         ("Netflix", ("NETFLIX.COM",), 15.49, 15.49)]


def _variant(rng: random.Random, name: str, store: int) -> str:
    """How a card statement prints a merchant: as named, upper case with a store number, or with a POS prefix."""
    return rng.choice([name, f"{name.upper()} #{store}", f"POS DEBIT {name.upper()}"])


def bank(spec: dict, provider: str, start: date, end: date,
         payee_descriptor: str | None = None) -> tuple[list[dict], dict]:
    """Nessie-shaped transactions (the case.json format seed_nessie.py consumes) and what was planted.

    Messy on purpose, like a real statement: pay varies a little from check to check (hours, overtime), card
    merchants print under different names, there's cash, online shopping and Venmo, a few medical bills (spec
    `medical`, which the high-medical-cost rule counts), and payments to the hospital carry the health system's
    billing name ("SUTTER HEALTH PYMT"), not the hospital account number."""
    rng = random.Random(spec["seed"])
    pay_rng = random.Random(spec["seed"] * 31 + 7)
    errs = spec.get("errors", {})
    pay = spec["pay"]
    vary = pay.get("vary_pct", 3) / 100
    txns: list[dict] = []
    for d in _paydays(pay, start, end):
        txns.append({"type": "deposit", "date": _iso(d),
                     "amount": round(pay["amount"] * (1 + pay_rng.uniform(-vary, vary)), 2),
                     "description": f"PAYROLL DIRECT DEP - {pay['employer']}"})
    r = spec["rent"]
    for m in _months(start, end, 1):  # rent goes out on the 1st or a day or two later
        txns.append({"type": "purchase", "date": _iso(m + timedelta(days=rng.choice((0, 0, 1, 2)))),
                     "amount": r["amount"], "merchant": r["merchant"], "description": r["description"]})
    # fixed-amount merchants (lo == hi) are monthly bills around the 20th; the rest are everyday purchases,
    # about two a week, with cents so nothing repeats by accident
    for merchant, desc, lo, hi in spec["merchants"]:
        if lo == hi:
            for m in _months(start, end, 20):
                txns.append({"type": "purchase", "date": _iso(m + timedelta(days=rng.randint(-2, 2))),
                             "amount": float(lo), "merchant": merchant, "description": desc})
    everyday = [x for x in spec["merchants"] if x[2] != x[3]]
    stores = {x[0]: rng.randint(2, 980) for x in everyday}
    d = start + timedelta(days=rng.randint(0, 2))
    while d <= end:
        merchant, desc, lo, hi = rng.choice(everyday)
        txns.append({"type": "purchase", "date": _iso(d), "amount": round(rng.uniform(lo, hi), 2),
                     "merchant": _variant(rng, merchant, stores[merchant]), "description": desc})
        d += timedelta(days=rng.randint(2, 5))
    for name, descs, lo, hi in NOISE:
        if lo == hi:
            for m in _months(start, end, 9):
                txns.append({"type": "purchase", "date": _iso(m), "amount": lo, "merchant": name, "description": descs[0]})
            continue
        d = start + timedelta(days=rng.randint(3, 12))
        while d <= end:
            desc = rng.choice(descs).format(code=f"{rng.randint(0, 36 ** 6 - 1):06X}", num=rng.randint(1000, 9999))
            txns.append({"type": "purchase", "date": _iso(d), "amount": round(rng.uniform(lo, hi), 2),
                         "merchant": name, "description": desc})
            d += timedelta(days=rng.randint(9, 24))
    d = start + timedelta(days=rng.randint(5, 20))
    while d <= end:  # cash
        txns.append({"type": "withdrawal", "date": _iso(d), "amount": float(rng.choice((40, 60, 60, 100, 200))),
                     "description": f"ATM WITHDRAWAL {spec['patient']['address']['city'].upper()} CA"})
        d += timedelta(days=rng.randint(14, 35))
    for merchant, desc, amount, when in spec.get("medical", []):
        if start <= date.fromisoformat(when) <= end:
            txns.append({"type": "purchase", "date": when, "amount": amount, "merchant": merchant, "description": desc})
    planted = {"hospital_payments": [], "duplicate": None}
    descriptor = payee_descriptor or f"{provider.upper()} PYMT"
    pay_desc = iter(rng.sample(["ONLINE PMT", "WEB PYMT", "BILL PAY", "POS DEBIT"], 4) * 3)
    if errs.get("uncredited_payment"):
        p = errs["uncredited_payment"]
        txns.append({"type": "purchase", "date": p["date"], "amount": p["amount"], "merchant": descriptor,
                     "description": next(pay_desc)})
        planted["hospital_payments"].append({**p, "credited": False})
    for p in (errs.get("refund_owed") or {}).get("payments", []):
        txns.append({"type": "purchase", "date": p["date"], "amount": p["amount"], "merchant": descriptor,
                     "description": next(pay_desc)})
        planted["hospital_payments"].append({**p, "credited": True})
    if errs.get("duplicate_bank_charge"):
        dc = errs["duplicate_bank_charge"]
        d0 = date.fromisoformat(dc["date"])
        for k in range(2):
            txns.append({"type": "purchase", "date": _iso(d0 + timedelta(days=k)), "amount": dc["amount"],
                         "merchant": dc["merchant"], "description": dc["description"]})
        planted["duplicate"] = dc
    txns.sort(key=lambda t: (t["date"], t["type"] != "deposit"))
    return txns, planted


# ------------------------------------------------------------------------------------------- build

def build(name: str) -> dict:
    spec = SPECS[name]
    unknown = set(spec.get("errors", {})) - set(ERROR_TYPES)
    if unknown:
        raise ValueError(f"unknown planted errors: {unknown}")
    errs = spec.get("errors", {})
    hid = spec["hospital_id"]
    hosp = next(h for h in registry.SEED if h.id == hid)
    table = prices.table(hid)
    src = table["source"]
    pol = policy_snapshot(hid)
    as_of = date.fromisoformat(spec.get("as_of", DEFAULT_AS_OF))
    v = spec["visit"]
    insured = spec["insurance"] is not None
    if errs.get("insurance_not_applied") and not insured:
        raise ValueError("insurance_not_applied needs an insured patient")

    lines, prov = price_lines(spec)
    total = sum(cents(li["amount"]) for li in lines)
    dup_c = sum(cents(p["billed"]) for p in prov if p["planted_error"] == "duplicate_line")
    over_c = sum(cents(p["billed"]) - cents(p["posted_gross"]) for p in prov if p["planted_error"] == "price_above_posted")
    correct_charges = total - dup_c - over_c

    # bank history: a year, so the high-medical-cost rule's "prior 12 months" comes from the bank. Payments to
    # the hospital carry its health system's billing name when billcheck can match that, like real statements
    descriptor = spec.get("payee_descriptor") or (
        f"{hosp.system.upper()} PYMT" if len(hosp.system.split()) > 1 else f"{hosp.name.upper()} PYMT")
    txns, planted = bank(spec, hosp.name, as_of - timedelta(days=BANK_DAYS), as_of, descriptor)
    credited = sum(cents(p["amount"]) for p in planted["hospital_payments"] if p["credited"])
    uncredited = sum(cents(p["amount"]) for p in planted["hospital_payments"] if not p["credited"])

    # insurance + what the hospital asks for
    eob, adj = None, None
    if insured:
        adj = adjudicate(spec, prov)
        resp = adj["totals"]["patient"]
        ins = spec["insurance"]
        eob = {"insurer": ins["insurer"], "member_id": ins["member_id"], "claim_number": ins["claim_number"],
               "provider": hosp.name, "service_date": v["service_date"],
               "billed": dollars(adj["totals"]["billed"]), "allowed": dollars(adj["totals"]["allowed"]),
               "plan_paid": dollars(adj["totals"]["plan_paid"]), "patient_responsibility": dollars(resp),
               "status": f"Processed {ins['processed']}" + (" (deductible applied)" if adj["totals"]["deductible"] else ""),
               "deductible_applied": dollars(adj["totals"]["deductible"]),
               "coinsurance": dollars(adj["totals"]["coinsurance"]),
               "lines": [{"line": r["line"], "code": r["code"], "billed": dollars(r["billed"]),
                          "allowed": dollars(r["allowed"]), "deductible": dollars(r["deductible"]),
                          "coinsurance": dollars(r["coinsurance"]), "plan_paid": dollars(r["plan_paid"]),
                          "patient_responsibility": dollars(r["patient"]), "remark": r["remark"]}
                         for r in adj["rows"]]}
        if errs.get("insurance_not_applied"):
            ins_adj = 0
        else:  # contractual write-off + plan payment; the insurer-denied duplicate is passed to the patient
            ins_adj = total - resp - dup_c
        share = resp
    else:
        ins_adj = 0
        share = correct_charges
    amount_due = total - ins_adj - credited
    if amount_due < 0:
        raise ValueError("amount due went negative")

    # multi-campus files list every campus ("A | B | C"); the bill names one
    loc = spec.get("location") or table["location_name"].split("|")[0].strip()
    addr = table["hospital_address"].split("|")[0].strip().strip('"')
    bill = {"provider": hosp.name, "facility": f"{loc}, {addr}",
            "account_number": v["account_number"], "statement_date": v["statement_date"], "notice": v["notice"],
            "due_date": v["due_date"], "service_date": v["service_date"],
            "total_charges": dollars(total), "insurance_adjustments": dollars(ins_adj),
            "patient_payments": dollars(credited), "amount_due": dollars(amount_due),
            "billing_phone": "", "collection_agency": "", "collection_phone": "", "line_items": lines}
    case = {"_note": f"SYNTHETIC patient generated by billback/scenarios.py ({name}): the person, insurer, "
                     f"insurance terms and bank history are fictional. The hospital, every line price "
                     f"(its CMS price file, {table['last_updated_on']}) and its financial-assistance policy are "
                     f"real. The bill is NOT a real {hosp.name} bill. See expected.json for the answers.",
            "patient": spec["patient"],
            "hospital": {"name": hosp.name, "system": hosp.system, "city": hosp.city,
                         "billing_phone_label": "Patient Financial Services"},
            "bill": bill, "eob": eob if eob else {}, "transactions": txns}

    expected = _expected(name, spec, case, prov, adj, pol, src, table, as_of, planted,
                         dict(total=total, dup=dup_c, over=over_c, correct=correct_charges, ins_adj=ins_adj,
                              credited=credited, uncredited=uncredited, share=share, amount_due=amount_due))
    return {"case": case, "expected": expected}


def _expected(name, spec, case, prov, adj, pol, src, table, as_of, planted, m) -> dict:
    errs = spec.get("errors", {})
    bill, eob, insured = case["bill"], case["eob"], billcheck.insured(case)
    txns = mirror_ids(case["transactions"])

    # --- the waterfall: hospital's amount due -> correct amount owed before any discount
    steps = []
    if m["dup"]:
        steps.append(("duplicate_line", "Duplicated line removed", -m["dup"]))
    if m["over"] and (not insured or errs.get("insurance_not_applied")):
        steps.append(("price_above_posted", "Line charged above the hospital's posted price", -m["over"]))
    if insured and errs.get("insurance_not_applied"):
        steps.append(("insurance_not_applied", "Insurance applied (EOB patient responsibility)",
                      -(m["correct"] - m["share"])))
    if m["uncredited"]:
        steps.append(("uncredited_payment", "Payment the bill doesn't show", -m["uncredited"]))
    owed_c = m["amount_due"] + sum(s[2] for s in steps)
    if owed_c != max(0, m["share"] - m["credited"] - m["uncredited"]):
        raise AssertionError(f"{name}: waterfall does not add up")

    # --- what the app computes (finance.py, same calls as agents/auditor.py and agents/policy.py)
    eob_for_finance = eob if insured else {"patient_responsibility": dollars(m["share"]), "claim_number": "self-pay"}
    rec = finance.reconcile(bill, eob_for_finance, txns)
    inc = finance.income(txns, today=as_of)
    # out-of-pocket for high-medical-cost rules: this bill's share + the bank's other medical bills (12 months)
    med = finance.medical_spending(txns, bill, eob if insured else {}, today=as_of)
    bill_oop = float(eob.get("patient_responsibility") or 0) if insured else 0.0
    elig = finance.charity_discount(inc["annual"], case["patient"]["household_size"], pol["tiers"], insured=insured,
                                    out_of_pocket=round(bill_oop + med["total"], 2))
    bill_only = finance.charity_discount(inc["annual"], case["patient"]["household_size"], pol["tiers"],
                                         insured=insured, out_of_pocket=bill_oop)
    want_med = {(w, cents(a)) for _, _, a, w in spec.get("medical", [])}
    if not want_med <= {(t["date"], cents(t["amount"])) for t in med["txns"]}:
        raise AssertionError(f"{name}: finance.medical_spending misses a planted medical bill (rename the merchant)")
    st = finance.settle(rec["patient_responsibility"], rec["paid"], elig["discount_pct"])
    terms = pol["payment_plan"]
    plan = finance.payment_plan(st["balance"], inc["monthly"], policy_cap_pct=terms.get("max_pct_monthly_income"))

    if cents(rec["owed"]) != owed_c:
        raise AssertionError(f"{name}: finance.reconcile owes {rec['owed']}, generator says {dollars(owed_c)}")
    want_pay = sorted((p["date"], cents(p["amount"])) for p in planted["hospital_payments"])
    got_pay = sorted((t["date"], cents(t["amount"])) for t in rec["payments"])
    if want_pay != got_pay:
        raise AssertionError(f"{name}: payment match picked up {got_pay}, planted {want_pay} (rename a merchant)")
    want_dup = 1 if planted["duplicate"] else 0
    if len(rec["duplicates"]) != want_dup:
        raise AssertionError(f"{name}: {len(rec['duplicates'])} bank duplicates found, planted {want_dup}")
    if errs.get("refund_owed") and not st["refund"]:
        raise AssertionError(f"{name}: refund_owed planted but no refund results")

    pay_ids = {t["id"]: t for t in rec["payments"]}
    findings, errors_out = [], []

    def err(kind: str, what: str, effect: int, **details) -> None:
        errors_out.append({"type": kind, "what": what, "effect_on_amount_owed": dollars(effect), **details})

    if m["dup"]:
        d = next(p for p in prov if p["planted_error"] == "duplicate_line")
        err("duplicate_line", f"Line {d['line']} repeats line {d['duplicate_of_line']} ({d['code']} "
                              f"{d['description_from_file']}, ${d['billed']:,.2f})", -m["dup"],
            lines=[d["duplicate_of_line"], d["line"]], code=d["code"], amount=d["billed"],
            eob_remark=f"insurer denied line {d['line']} as a duplicate" if insured else None)
        findings.append({"id": "duplicate_line", "agent": "nessie", "title": f"{d['code']} billed twice",
                         "money": dollars(m["dup"]), "proof": f"bill lines {d['duplicate_of_line']} and {d['line']}"})
    if m["over"]:
        o = next(p for p in prov if p["planted_error"] == "price_above_posted")
        effect = -m["over"] if (not insured or errs.get("insurance_not_applied")) else 0
        err("price_above_posted", f"Line {o['line']} ({o['code']} {o['description_from_file']}) billed "
                                  f"${o['billed']:,.2f}; the hospital's own posted gross charge is "
                                  f"${o['posted_gross']:,.2f}", effect,
            line=o["line"], code=o["code"], billed=o["billed"], posted=o["posted_gross"],
            overcharge=dollars(m["over"]), price_source=src["file_url"],
            note=None if effect else "Insurance was applied, so the insurer's allowed amount, not the billed "
                                     "charge, sets her share: no effect on what she owes, still worth flagging.")
        findings.append({"id": "price_above_posted", "agent": "bright_data",
                         "title": f"{o['code']} charged above the hospital's posted price",
                         "money": dollars(-effect), "overcharge": dollars(m["over"]),
                         "proof": f"bill line {o['line']} ${o['billed']:,.2f} vs posted ${o['posted_gross']:,.2f} "
                                  f"({table['hospital_name']} price file, {table['last_updated_on']})"})
    if insured and errs.get("insurance_not_applied"):
        gap = m["correct"] - m["share"]
        err("insurance_not_applied", f"The bill asks for full charges; the EOB says her share is "
                                     f"${eob['patient_responsibility']:,.2f}", -gap,
            eob_claim=eob["claim_number"], correct_charges=dollars(m["correct"]))
        findings.append({"id": "insurance_not_applied", "agent": "nessie", "title": "Insurance wasn't applied",
                         "money": dollars(gap),
                         "proof": f"EOB claim {eob['claim_number']}: patient responsibility "
                                  f"${eob['patient_responsibility']:,.2f}",
                         "note": "Excludes the duplicate line and overcharge, reported separately. The app's "
                                 "current auditor reports amount_due - responsibility as one number "
                                 f"(${rec['billed_due'] - rec['patient_responsibility']:,.2f})."
                         if (m["dup"] or m["over"]) else None})
    for t in rec["payments"]:
        p = next(p for p in planted["hospital_payments"] if p["date"] == t["date"] and cents(p["amount"]) == cents(t["amount"]))
        if not p["credited"]:
            err("uncredited_payment", f"${t['amount']:,.2f} paid on {t['date']} is not on the bill", -cents(t["amount"]),
                txn_id=t["id"], date=t["date"], amount=t["amount"])
            findings.append({"id": f"paid-{t['id']}", "agent": "nessie", "title": "A payment you made was missed",
                             "money": t["amount"], "proof": f"bank txn {t['id']} on {t['date']}"})
    if planted["duplicate"]:
        dd = rec["duplicates"][0]
        err("duplicate_bank_charge", f"{dd['duplicate']['merchant']} charged ${dd['amount']:,.2f} twice "
                                     "(not part of the hospital bill: dispute with the bank)", 0,
            txn_ids=[dd["first"]["id"], dd["duplicate"]["id"]], amount=dd["amount"])
        findings.append({"id": "dupe", "agent": "nessie", "title": f"Double charge at {dd['duplicate']['merchant']}",
                         "money": 0, "bank_amount": dd["amount"], "proof": dd["source"]})
    if elig["discount_pct"]:
        saved = rec["owed"] - st["balance"] + st["refund"]
        findings.append({"id": "discount", "agent": "bright_data",
                         "title": "Free care under their policy" if elig["discount_pct"] == 100
                         else f"{elig['discount_pct']:.0f}% discount under their policy",
                         "money": round(saved, 2), "quote": elig["tier"].get("quote"),
                         "source": elig["tier"].get("source_url")})
    if st["refund"]:
        cr = [p for p in planted["hospital_payments"] if p["credited"]]
        err("refund_owed", f"Payments of ${rec['paid']:,.2f} exceed her discounted share of "
                           f"${st['discounted_share']:,.2f}: the hospital owes ${st['refund']:,.2f} back", 0,
            refund=st["refund"], credited_payments=cr)
        findings.append({"id": "refund", "agent": "bright_data", "title": "Refund owed", "money": st["refund"],
                         "proof": "; ".join(f"bank txn {t['id']} ${t['amount']:,.2f} on {t['date']}"
                                            for t in rec["payments"])})
    if plan["months"]:
        findings.append({"id": "plan", "agent": "bright_data", "title": "Interest-free payment plan", "money": 0,
                         "value": f"{plan['months']} x ${plan['monthly']:,.0f}/mo"})

    must_not = []
    for t in rec["payments"]:
        p = next(p for p in planted["hospital_payments"] if p["date"] == t["date"] and cents(p["amount"]) == cents(t["amount"]))
        if p["credited"]:
            must_not.append({"what": f"Payment {t['id']} (${t['amount']:,.2f} on {t['date']}) as 'missed'",
                             "why": "the bill credits it under 'Patient payments'"})
    if not m["dup"]:
        must_not.append({"what": "a duplicate line on the bill", "why": "every bill line is distinct"})
    if not m["over"]:
        must_not.append({"what": "a line above the posted price", "why": "every line equals the posted gross charge"})
    if not planted["duplicate"]:
        must_not.append({"what": "a duplicate bank charge", "why": "none planted; generator checked find_duplicates"})

    app_notes = []
    if not insured:
        app_notes.append("Uninsured: case.json has \"eob\": {}. finance.reconcile here was given a self-pay "
                         "pseudo-EOB whose patient_responsibility is the corrected charges. The app's Nessie agent "
                         "(agents/auditor.py) reads eob['patient_responsibility'] directly, so it cannot run this "
                         "case until it handles a missing EOB.")
        app_notes.append("Not modeled: California caps charges for uninsured patients at or under 400% FPL at the "
                         "greater of Medicare or Medi-Cal (HSC 127405(d)); a real audit could argue more off.")
    if any(p["credited"] for p in planted["hospital_payments"]):
        app_notes.append("The app's payment match (finance._matches_payee) counts every payment to the hospital "
                         "as 'not credited'. Its money is still right here (finance uses the EOB share, not the "
                         "bill's amount due), but today it would also report the credited payments as missed.")
    if m["dup"] and insured and not errs.get("insurance_not_applied"):
        app_notes.append("finance.reconcile labels the passed-on duplicate as 'Insurance adjustment not applied'.")
    if not pol["tiers"]:
        app_notes.append("This hospital's policy DB entry has no fixed-percent tiers, so no discount applies.")

    return {
        "scenario": name, "generator": "billback/scenarios.py", "generator_version": GENERATOR_VERSION,
        "seed": spec["seed"], "as_of": _iso(as_of), "story": spec["story"],
        "real": {
            "hospital_id": spec["hospital_id"], "hospital": case["hospital"]["name"],
            "price_file": {"hospital_name": table["hospital_name"], "location": table["location_name"],
                           "address": table["hospital_address"], "last_updated_on": table["last_updated_on"],
                           "template_version": table["template_version"], "file_url": src["file_url"],
                           "fetched_at": src["fetched_at"], "sha256": src["sha256"],
                           "table": f"policydb/prices/{spec['hospital_id']}.json"},
            "policy": {"version": pol["version"], "fetched_at": pol["fetched_at"], "status": pol["status"],
                       "source_urls": pol["source_urls"],
                       "tiers": [{k: t.get(k) for k in ("max_fpl_pct", "discount_pct", "applies_to",
                                                         "min_oop_pct_income", "quote", "source_url")}
                                 for t in pol["tiers"]],
                       "payment_plan": {k: terms.get(k) for k in ("interest_free", "max_pct_monthly_income",
                                                                   "quote", "cap_quote")}},
        },
        "synthetic": ["patient", "insurer and insurance terms" if insured else "no insurer (uninsured)",
                      "bank history", "account and claim numbers", "the planted errors"],
        "planted_errors": [_compact(x) for x in errors_out],
        "bill_lines": prov,
        "eob_basis": None if not insured else {
            "insurer": eob["insurer"] + " (fictional)",
            "allowed_statistic": ALLOWED_STATISTIC, "fallback_statistic": FALLBACK_STATISTIC,
            "hospital_ratio": adj["ratio"],
            "lines_using_fallback": [r["line"] for r in adj["rows"] if r["basis"] and r["basis"].startswith("estimated")],
            "allowed_never_exceeds": "the line's posted gross charge",
            "terms": {k: spec["insurance"][k] for k in ("deductible_remaining", "coinsurance_pct", "oop_max_remaining")},
            "order": "deductible first, then coinsurance on the rest, line by line in bill order, capped at the "
                     "out-of-pocket max; coinsurance rounded half up to the cent per line",
        },
        "totals": {"total_charges": dollars(m["total"]), "correct_charges": dollars(m["correct"]),
                   "insurance_adjustments_on_bill": dollars(m["ins_adj"]),
                   "patient_payments_on_bill": dollars(m["credited"]), "amount_due": dollars(m["amount_due"]),
                   "allowed": eob.get("allowed"), "plan_paid": eob.get("plan_paid"),
                   "patient_responsibility": dollars(m["share"]), "paid_uncredited": dollars(m["uncredited"]),
                   "paid_total": dollars(m["credited"] + m["uncredited"])},
        "waterfall": {"start": {"label": "Hospital says you owe", "amount": dollars(m["amount_due"])},
                      "steps": [{"error": k, "label": lbl, "amount": dollars(a)} for k, lbl, a in steps],
                      "owed_before_discount": dollars(owed_c)},
        "income": {"annual": inc["annual"], "monthly": inc["monthly"], "source": inc["source"],
                   "deposits_counted": len(inc["deposits"])},
        "medical_spending": {"since": med["since"], "bank_total": med["total"], "this_bill": bill_oop,
                             "out_of_pocket": round(bill_oop + med["total"], 2),
                             "txn_ids": [t["id"] for t in med["txns"]],
                             "discount_without_bank": bill_only["discount_pct"],
                             "bank_decides_eligibility": elig["discount_pct"] > bill_only["discount_pct"]},
        "discount": {k: elig[k] for k in ("fpl_pct", "fpl_amount", "fpl_year", "household_size", "insured",
                                          "oop_pct_income", "discount_pct")}
                    | {"tier_quote": elig["tier"].get("quote") if elig["tier"] else None},
        "settle": st,
        "correct": {"balance": st["balance"], "refund": st["refund"], "discount_pct": elig["discount_pct"],
                    "plan_months": plan["months"], "plan_monthly": plan["monthly"]},
        "plan": plan,
        "finance_reconcile": {"billed_due": rec["billed_due"], "patient_responsibility": rec["patient_responsibility"],
                              "insurance_adjustment": rec["insurance_adjustment"], "paid": rec["paid"],
                              "owed": rec["owed"], "overbilled_by": rec["overbilled_by"],
                              "payment_txn_ids": sorted(pay_ids),
                              "duplicate_txn_ids": [[d["first"]["id"], d["duplicate"]["id"]] for d in rec["duplicates"]]},
        "findings": [_compact(x) for x in findings],
        "must_not_report": must_not,
        "app_notes": app_notes,
    }


# ------------------------------------------------------------------------------------------- output

def dumps(obj: dict) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def write(name: str, out_dir: Path | None = None, pdfs: bool = True) -> dict:
    out = build(name)
    d = Path(out_dir or OUT_DIR / name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "case.json").write_text(dumps(out["case"]))
    (d / "expected.json").write_text(dumps(out["expected"]))
    written = ["case.json", "expected.json"]
    if pdfs:
        written += render_pdfs(out["case"], d)
    return {"dir": d, "files": written, **out}


def render_pdfs(case: dict, d: Path) -> list[str]:
    """Bill + EOB PDFs through scripts/make_demo_docs.py (needs reportlab: `uv run --with reportlab ...`)."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("make_demo_docs", config.ROOT / "scripts" / "make_demo_docs.py")
        docs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(docs)
    except ImportError:
        return []
    docs.use(case)
    docs.bill(d / "bill.pdf")
    files = ["bill.pdf"]
    if case["eob"]:
        docs.eob(d / "eob.pdf")
        files.append("eob.pdf")
    return files
