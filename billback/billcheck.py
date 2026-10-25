"""Checks on the patient's own documents that the Nessie agent needs before the money math in finance.py:

- which bank payments went to *this* hospital (its own name or aliases, or the bill's account number, on or
  after the date of service), not to anything that merely shares a word like "Medical" or "Pacific";
- which of those payments the bill already credits ("Patient payments"), so only the rest are reported as
  payments the hospital may not see;
- the bill's own line errors, insured or not: a line billed twice, and a line above the hospital's posted gross
  charge (its CMS price file, policydb/prices), with drug lines compared only when the file's unit converts to
  the HCPCS unit. Self-pay, they set the share; insured, they are reported apart from the insurance gap.
"""
import re
from datetime import date, datetime

from . import prices
from .policies import registry


def _words(s: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip() + " "


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _day(s: str | None) -> date | None:
    try:
        return datetime.fromisoformat(str(s)[:10]).date() if s else None
    except ValueError:
        return None


def _cents(x) -> int:
    return round(float(x or 0) * 100)


def hospital(name: str) -> registry.Hospital | None:
    return registry.match(name, list(registry.SEED))


def payee_names(payee: str) -> list[str]:
    """The bill's provider name plus the registry's names/aliases for that hospital, normalized. Matched as
    whole phrases, never word by word."""
    h = hospital(payee)
    out = []
    for n in [payee, *(registry.names(h) if h else [])]:
        w = _words(n).strip()
        if w and w not in out:
            out.append(w)
    return out


def is_payment_to(txn: dict, payee: str, account: str | None = None) -> bool:
    text = f"{txn.get('merchant') or ''} {txn.get('description') or ''}"
    hay = _words(text)
    if any(f" {n} " in hay for n in payee_names(payee)):
        return True
    acct = _compact(account)
    return len(acct) >= 6 and acct in _compact(text)


def service_date(bill: dict, eob: dict | None = None) -> date | None:
    return _day(bill.get("service_date")) or _day((eob or {}).get("service_date"))


def on_or_after_service(txn: dict, bill: dict, eob: dict | None = None) -> bool:
    """A payment made before the date of service can't be for this bill."""
    start, when = service_date(bill, eob), _day(txn.get("date"))
    return start is None or when is None or when >= start


def bill_credits(bill: dict) -> float:
    """Patient payments the bill itself shows: its itemized payments if it lists them, else its total."""
    listed = bill.get("payments")
    if isinstance(listed, list) and listed:
        return round(sum(float(p.get("amount") or 0) for p in listed if isinstance(p, dict)), 2)
    return round(float(bill.get("patient_payments") or 0), 2)


def split_credited(bill: dict, payments: list[dict]) -> tuple[list[dict], list[dict]]:
    """Which bank payments the bill already reflects. Returns (credited, not_on_bill); each not_on_bill entry is
    the transaction plus "not_on_bill": the part of it the bill doesn't show.

    If the bill itemizes its payments (bill["payments"]: [{date, amount}]), a bank payment is credited when a
    listed payment has the same amount within a week. Otherwise the bill's "Patient payments" total is applied
    to the bank payments oldest first (only those made by the statement date can be on the statement)."""
    pays = sorted(payments, key=lambda t: t.get("date") or "")
    credited, missing = [], []
    listed = bill.get("payments")
    if isinstance(listed, list) and listed:
        left = [p for p in listed if isinstance(p, dict)]
        for t in pays:
            hit = next((p for p in left if _cents(p.get("amount")) == _cents(t["amount"])
                        and (_day(p.get("date")) is None or _day(t.get("date")) is None
                             or abs((_day(p["date"]) - _day(t["date"])).days) <= 7)), None)
            if hit:
                left.remove(hit)
                credited.append(t)
            else:
                missing.append({**t, "not_on_bill": round(float(t["amount"]), 2)})
        return credited, missing
    pool = _cents(bill.get("patient_payments"))
    stmt = _day(bill.get("statement_date"))
    for t in pays:
        amt = _cents(t["amount"])
        when = _day(t.get("date"))
        use = min(pool, amt) if (stmt is None or when is None or when <= stmt) else 0
        pool -= use
        if use == amt:
            credited.append(t)
        else:
            missing.append({**t, "not_on_bill": (amt - use) / 100})
    return credited, missing


# A drug's HCPCS code is billed per a fixed dose (J2405: 1 mg of ondansetron), but a price file lists the drug
# per whatever the hospital stocks: a 2 mL vial, 12.25 mL, "1 each". Its drug_unit / drug_unit_type
# (CMS: ME milligram, GR gram, ML milliliter, UN unit, EA each, F2 international unit) say which. A posted drug
# price is compared only when it converts to the HCPCS dose without knowing the concentration: mass to mass,
# volume to volume. Anything else (a mg dose priced per mL, per "each") is skipped and said so, never guessed.
HCPCS_DOSE = {"J2405": (1, "mg"), "J1885": (15, "mg"), "J1100": (1, "mg"), "J2270": (10, "mg"),
              "J3010": (0.1, "mg"), "J0696": (250, "mg"), "J7030": (1000, "ml")}
FILE_UNITS = {"ME": (1, "mg"), "GR": (1000, "mg"), "ML": (1, "ml")}  # file unit type -> (factor, measure)
UNIT_NAMES = {"ME": "mg", "GR": "g", "ML": "mL", "UN": "unit", "EA": "each", "F2": "international unit"}


def posted_unit_price(code: str, entry: dict) -> tuple[float | None, str]:
    """The posted gross charge for one HCPCS unit of this code, and how it was worked out. None (with the reason)
    when the file's unit can't be converted to the code's unit."""
    gross = entry.get("gross")
    if gross is None:
        return None, "no posted gross charge"
    dose = HCPCS_DOSE.get(code)
    if not dose and not code.startswith("J"):
        return float(gross), "per service"
    common = entry.get("common_name") or code
    qty, typ = entry.get("drug_unit"), (entry.get("drug_unit_type") or "").upper()
    per = f"{qty:g} {UNIT_NAMES.get(typ, typ.lower() or 'unit')}" if qty else (UNIT_NAMES.get(typ) or "an unstated unit")
    if not dose or not qty or typ not in FILE_UNITS:
        return None, f"the price file prices {code} per {per}; HCPCS {code} is {common.split(', ', 1)[-1]}"
    factor, measure = FILE_UNITS[typ]
    amount, want = dose
    if measure != want:
        return None, (f"the price file prices {code} per {per} but HCPCS {code} is per {amount:g} {want}: "
                      f"converting needs the concentration")
    per_unit = float(gross) * amount / (float(qty) * factor)
    how = "per HCPCS unit" if abs(amount - float(qty) * factor) < 1e-9 else (
        f"${float(gross):,.2f} per {per} = ${per_unit:,.2f} per {amount:g} {want} (the HCPCS unit)")
    return round(per_unit, 2), how


def line_review(bill: dict, hospital_name: str | None = None) -> dict:
    """Every line of the bill against itself and the hospital's posted prices. Returns
    {corrections, compared, skipped}: the errors (as line_corrections), how many lines were compared with a
    posted price, and the lines that couldn't be (each {line, code, why})."""
    h = hospital(hospital_name or bill.get("provider", "")) or hospital(bill.get("provider", ""))
    table = prices.table(h.id) if h else {}
    posted_prices = table.get("prices", {})
    acct = f"bill · acct {bill.get('account_number', '?')}"
    out, seen, skipped, compared = [], {}, [], 0
    for i, li in enumerate(bill.get("line_items") or [], 1):
        code = str(li.get("code") or "").strip().upper()
        units = li.get("units") or 1
        amt = round(float(li.get("amount") or 0), 2)
        key = (code, units, _cents(amt))
        if code and key in seen:
            out.append({"kind": "duplicate_line", "line": i, "code": code, "amount": amt,
                        "label": f"{code} billed twice", "source": f"{acct} · lines {seen[key]} and {i}",
                        "detail": f"Line {i} repeats line {seen[key]}: {li.get('description', code)}, ${amt:,.2f}."})
            continue
        seen.setdefault(key, i)
        posted = posted_prices.get(code)
        if not posted:
            continue
        unit_price, how = posted_unit_price(code, posted)
        if unit_price is None:
            skipped.append({"line": i, "code": code, "why": how})
            continue
        compared += 1
        cap = round(unit_price * units, 2)
        if _cents(amt) > _cents(cap):
            src = f"{table.get('hospital_name') or h.name} price file, {table.get('last_updated_on', '?')}"
            note = "" if how in ("per service", "per HCPCS unit") else f" ({how})"
            each = f" ({units:g} × ${unit_price:,.2f})" if units != 1 else ""
            out.append({"kind": "price_above_posted", "line": i, "code": code, "amount": round(amt - cap, 2),
                        "billed": amt, "posted": cap, "label": f"{code} charged above the posted price",
                        "source": src, "url": (table.get("source") or {}).get("file_url", ""),
                        "detail": f"Line {i} charges ${amt:,.2f}; the hospital's posted price is ${cap:,.2f}"
                                  f"{each}{note}."})
    return {"corrections": out, "compared": compared, "skipped": skipped,
            "table": table.get("hospital_name") or (h.name if h and table else None)}


def line_corrections(bill: dict, hospital_name: str | None = None) -> list[dict]:
    """Errors on the bill's own lines, each with the amount to take off and its proof: a line identical to an
    earlier one (same code, units and amount), and a line above the hospital's posted gross charge x units
    (drug lines only when the file's unit converts to the HCPCS unit: posted_unit_price)."""
    return line_review(bill, hospital_name)["corrections"]


def insured(case: dict) -> bool:
    """Explicit: a case is insured when it says so, or when it has an insurance statement with a patient share.
    An empty or missing EOB means self-pay."""
    if isinstance(case.get("insured"), bool):
        return case["insured"]
    eob = case.get("eob") or {}
    return eob.get("patient_responsibility") not in (None, "")
