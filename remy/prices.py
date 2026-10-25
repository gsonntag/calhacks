"""Compact real price tables from hospitals' CMS price-transparency files (45 CFR 180).

Every hospital must publish a machine-readable file of its standard charges and point to it from
`https://<domain>/cms-hpt.txt`. Those files are huge (17 MB to 150+ MB) and list every chargeable item.
Remy keeps a curated list of ~60 common ER/outpatient CPT/HCPCS codes (`CODES`) and, for each hospital,
a small JSON table in `policydb/prices/<hospital_id>.json` built by `scripts/build_price_tables.py` from the
raw file (kept out of git in `data/price_files/`). Fetching lives in `remy/price_sources.py`.

    from remy import prices
    prices.hospitals()                       # ['chinese-hospital', 'sutter-cpmc', ...]
    prices.lookup("sutter-cpmc", "99284")     # {'description': 'HCHG ED LEVEL 4', 'gross': 5214.0, ...}

The parsers here are pure (file in, records out) and handle the three CMS template layouts: CSV "tall"
(one row per payer/plan), CSV "wide" (one column group per payer/plan) and JSON (v2/v3).
"""
import csv
import io
import json
import re
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Iterable, Iterator

from . import config

PRICES_DIR = Path(config.env("PRICES_DIR") or config.ROOT / "policydb" / "prices")
RAW_DIR = config.ROOT / "data" / "price_files"

# (code, category, plain-English name). Professional-fee-only codes are left out: hospital bills are facility charges.
CODES: tuple[tuple[str, str, str], ...] = (
    ("99281", "ed", "ED visit, level 1 (minimal)"),
    ("99282", "ed", "ED visit, level 2 (low)"),
    ("99283", "ed", "ED visit, level 3 (moderate)"),
    ("99284", "ed", "ED visit, level 4 (high-moderate)"),
    ("99285", "ed", "ED visit, level 5 (high)"),
    ("99291", "ed", "Critical care, first 30-74 minutes"),
    ("71045", "imaging", "Chest X-ray, 1 view"),
    ("71046", "imaging", "Chest X-ray, 2 views"),
    ("70450", "imaging", "CT head/brain without contrast"),
    ("72125", "imaging", "CT cervical spine without contrast"),
    ("71250", "imaging", "CT chest without contrast"),
    ("71260", "imaging", "CT chest with contrast"),
    ("71275", "imaging", "CT angiography, chest (e.g. pulmonary embolism)"),
    ("74176", "imaging", "CT abdomen and pelvis without contrast"),
    ("74177", "imaging", "CT abdomen and pelvis with contrast"),
    ("74178", "imaging", "CT abdomen and pelvis without then with contrast"),
    ("70551", "imaging", "MRI brain without contrast"),
    ("72148", "imaging", "MRI lumbar spine without contrast"),
    ("72100", "imaging", "X-ray lumbar spine, 2-3 views"),
    ("73030", "imaging", "X-ray shoulder, 2+ views"),
    ("73110", "imaging", "X-ray wrist, 3+ views"),
    ("73610", "imaging", "X-ray ankle, 3+ views"),
    ("73630", "imaging", "X-ray foot, 3+ views"),
    ("76700", "imaging", "Ultrasound abdomen, complete"),
    ("76705", "imaging", "Ultrasound abdomen, limited (e.g. gallbladder)"),
    ("76856", "imaging", "Ultrasound pelvis, complete"),
    ("93005", "cardiac", "ECG tracing (12-lead), technical"),
    ("93306", "cardiac", "Echocardiogram, complete with Doppler"),
    ("36415", "lab", "Blood draw (venipuncture)"),
    ("80048", "lab", "Basic metabolic panel"),
    ("80053", "lab", "Comprehensive metabolic panel"),
    ("80061", "lab", "Lipid panel"),
    ("85025", "lab", "Complete blood count with differential"),
    ("85027", "lab", "Complete blood count, automated"),
    ("85610", "lab", "Prothrombin time (PT/INR)"),
    ("85730", "lab", "Partial thromboplastin time (PTT)"),
    ("84484", "lab", "Troponin, quantitative"),
    ("83690", "lab", "Lipase"),
    ("83880", "lab", "BNP (natriuretic peptide)"),
    ("82550", "lab", "Creatine kinase (CK)"),
    ("84443", "lab", "TSH"),
    ("83036", "lab", "Hemoglobin A1c"),
    ("81001", "lab", "Urinalysis, automated with microscopy"),
    ("81003", "lab", "Urinalysis, automated without microscopy"),
    ("81025", "lab", "Urine pregnancy test"),
    ("87086", "lab", "Urine culture, colony count"),
    ("87040", "lab", "Blood culture"),
    ("87635", "lab", "COVID-19 PCR"),
    ("96360", "infusion", "IV hydration, first hour"),
    ("96361", "infusion", "IV hydration, each additional hour"),
    ("96365", "infusion", "IV infusion (drug), first hour"),
    ("96366", "infusion", "IV infusion (drug), each additional hour"),
    ("96372", "infusion", "Injection under skin or into muscle"),
    ("96374", "infusion", "IV push, single or first drug"),
    ("96375", "infusion", "IV push, each additional drug"),
    ("J2405", "drug", "Ondansetron (Zofran) injection, per 1 mg"),
    ("J1885", "drug", "Ketorolac (Toradol) injection, per 15 mg"),
    ("J1100", "drug", "Dexamethasone injection, per 1 mg"),
    ("J2270", "drug", "Morphine injection, up to 10 mg"),
    ("J3010", "drug", "Fentanyl injection, per 0.1 mg"),
    ("J0696", "drug", "Ceftriaxone injection, per 250 mg"),
    ("J7030", "drug", "Normal saline infusion, 1000 cc"),
    ("12001", "procedure", "Simple wound repair, up to 2.5 cm"),
    ("10060", "procedure", "Incision and drainage of abscess, simple"),
    ("29125", "procedure", "Short arm splint, static"),
    ("94640", "procedure", "Nebulizer (inhaled) treatment"),
    ("51702", "procedure", "Urinary catheter insertion, simple"),
)
CODE_SET = {c for c, _, _ in CODES}
CODE_INFO = {c: {"category": cat, "common_name": name} for c, cat, name in CODES}
CPT_TYPES = {"CPT", "HCPCS"}

# Payer families shown by name (commercial first; see pick_payers). The first matching row with a dollar amount wins.
PAYER_FAMILIES = (("Aetna", r"aetna"), ("Anthem Blue Cross", r"anthem|blue\s*cross"),
                  ("Blue Shield of California", r"blue\s*shield"), ("Cigna", r"cigna"),
                  ("UnitedHealthcare", r"united\s*health|\buhc\b|optum"), ("Health Net", r"health\s*net"),
                  ("Kaiser", r"kaiser"), ("Medi-Cal", r"medi-?cal|medicaid"), ("Medicare", r"medicare"))
MAX_PAYERS = 6
MAX_TEXT = 240


# ---------- small helpers ----------

def money(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("$", "").replace(",", "")
    try:
        return float(s) if s else None
    except ValueError:
        return None


def clip(s, n: int = MAX_TEXT) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def norm_header(h: str) -> str:
    return "|".join(p.strip().lower() for p in (h or "").lstrip("\ufeff").split("|"))


def decode(data: bytes) -> str:
    """Price files come as UTF-8 (with or without BOM), UTF-16 (Sutter's index) or Latin-1."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def open_text(path: Path) -> io.TextIOBase:
    head = path.open("rb").read(4)
    if head[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return path.open(encoding="utf-16", newline="")
    return path.open(encoding="utf-8-sig", errors="replace", newline="")


# ---------- normalized item ----------

def new_item(*, description, codes, setting, gross, cash, minimum, maximum, modifiers="", billing_class="",
             drug_unit=None, drug_type="", notes="") -> dict:
    return {"description": clip(description, 160), "codes": codes, "setting": (setting or "").strip().lower(),
            "billing_class": (billing_class or "").strip().lower(), "gross": money(gross), "discounted_cash": money(cash),
            "min": money(minimum), "max": money(maximum), "modifiers": clip(modifiers, 60),
            "drug_unit": money(drug_unit), "drug_type": (drug_type or "").strip(), "notes": clip(notes), "payers": []}


def payer(name, plan, dollar=None, pct=None, algorithm=None, methodology=None, median_amt=None, notes=None) -> dict | None:
    p = {"payer": clip(name, 80), "plan": clip(plan, 80), "dollar": money(dollar), "percentage": money(pct),
         "algorithm": clip(algorithm) or None, "methodology": clip(methodology, 60) or None,
         "median": money(median_amt), "notes": clip(notes, 160) or None}
    if not p["payer"] or (p["dollar"] is None and p["percentage"] is None and not p["algorithm"]):
        return None
    return p


def wanted(codes: list[tuple[str, str]]) -> list[str]:
    return [c for c, t in codes if t in CPT_TYPES and c in CODE_SET]


# ---------- CSV (tall and wide) ----------

def read_csv_meta(rows: Iterator[list[str]]) -> tuple[dict, list[str], list[str]]:
    """Rows 1-2 are general data element headers/values, row 3 the column headers
    (returned normalized, and raw so wide-format payer/plan names keep their case)."""
    meta_h = [norm_header(h) for h in next(rows)]
    meta_v = next(rows)
    meta = {h: v.strip() for h, v in zip(meta_h, meta_v) if h}
    raw = next(rows)
    return meta, [norm_header(h) for h in raw], raw


def csv_layout(header: list[str]) -> str:
    if "payer_name" in header:
        return "csv-tall"
    if any(h.startswith("standard_charge|") and h.count("|") == 3 for h in header):
        return "csv-wide"
    raise ValueError("not a CMS template CSV (no payer_name column and no standard_charge|payer|plan|... columns)")


def _row_codes(d: dict) -> list[tuple[str, str]]:
    out = []
    for i in range(1, 10):
        c = (d.get(f"code|{i}") or "").strip()
        if not c and f"code|{i}" not in d:
            break
        if c:
            out.append((c.upper(), (d.get(f"code|{i}|type") or "").strip().upper()))
    return out


def _row_item(d: dict, codes) -> dict:
    return new_item(description=d.get("description"), codes=codes, setting=d.get("setting"),
                    gross=d.get("standard_charge|gross"), cash=d.get("standard_charge|discounted_cash"),
                    minimum=d.get("standard_charge|min"), maximum=d.get("standard_charge|max"),
                    modifiers=d.get("modifiers", ""), billing_class=d.get("billing_class", ""),
                    drug_unit=d.get("drug_unit_of_measurement"), drug_type=d.get("drug_type_of_measurement", ""),
                    notes=d.get("additional_generic_notes", ""))


def parse_csv(f: Iterable[str]) -> tuple[dict, list[dict], str]:
    """(file metadata, items carrying a curated code, layout). Streams; keeps only matching rows."""
    rows = csv.reader(f)
    meta, header, raw_header = read_csv_meta(rows)
    layout = csv_layout(header)
    wide_cols = []
    if layout == "csv-wide":
        for i, (h, raw) in enumerate(zip(header, raw_header)):
            parts, rparts = h.split("|"), [p.strip() for p in raw.split("|")]
            if len(parts) == 4 and parts[0] == "standard_charge":
                wide_cols.append((i, rparts[1], rparts[2], parts[3]))
            elif len(parts) == 3 and parts[0] in ("median_amount", "estimated_amount", "additional_payer_notes"):
                wide_cols.append((i, rparts[1], rparts[2], parts[0]))
    items: dict[tuple, dict] = {}
    for row in rows:
        d = dict(zip(header, row))
        codes = _row_codes(d)
        if not wanted(codes):
            continue
        item = _row_item(d, codes)
        key = (item["description"], tuple(codes), item["setting"], item["modifiers"], item["billing_class"],
               item["gross"], item["drug_unit"])
        item = items.setdefault(key, item)
        if layout == "csv-tall":
            p = payer(d.get("payer_name"), d.get("plan_name"), d.get("standard_charge|negotiated_dollar"),
                      d.get("standard_charge|negotiated_percentage"), d.get("standard_charge|negotiated_algorithm"),
                      d.get("standard_charge|methodology"), d.get("median_amount") or d.get("estimated_amount"),
                      d.get("additional_payer_notes"))
            if p:
                item["payers"].append(p)
        else:
            per: dict[tuple, dict] = {}
            for i, pn, pl, field in wide_cols:
                v = row[i].strip() if i < len(row) else ""
                if v:
                    per.setdefault((pn, pl), {})[field] = v
            for (pn, pl), v in per.items():
                p = payer(pn, pl, v.get("negotiated_dollar"), v.get("negotiated_percentage"),
                          v.get("negotiated_algorithm"), v.get("methodology"),
                          v.get("median_amount") or v.get("estimated_amount"), v.get("additional_payer_notes"))
                if p:
                    item["payers"].append(p)
    return meta, list(items.values()), layout


# ---------- JSON (v2/v3) ----------

def json_meta(doc: dict) -> dict:
    loc = doc.get("location_name") or doc.get("hospital_location") or ""
    addr = doc.get("hospital_address") or ""
    return {"hospital_name": doc.get("hospital_name", ""), "last_updated_on": doc.get("last_updated_on", ""),
            "version": doc.get("version", ""), "location_name": " | ".join(loc) if isinstance(loc, list) else loc,
            "hospital_address": " | ".join(addr) if isinstance(addr, list) else addr}


def parse_json(doc: dict) -> tuple[dict, list[dict], str]:
    items = []
    for sci in doc.get("standard_charge_information") or []:
        codes = [(str(c.get("code", "")).strip().upper(), str(c.get("type", "")).strip().upper())
                 for c in sci.get("code_information") or []]
        if not wanted(codes):
            continue
        drug = sci.get("drug_information") or {}
        for sc in sci.get("standard_charges") or []:
            d = sc.get("drug_information") or drug
            mods = sc.get("modifier_code") or sc.get("modifiers") or ""
            item = new_item(description=sci.get("description"), codes=codes, setting=sc.get("setting"),
                            gross=sc.get("gross_charge"), cash=sc.get("discounted_cash"), minimum=sc.get("minimum"),
                            maximum=sc.get("maximum"), modifiers="|".join(mods) if isinstance(mods, list) else mods,
                            billing_class=sc.get("billing_class", ""), drug_unit=d.get("unit"), drug_type=d.get("type", ""),
                            notes=sc.get("additional_generic_notes", ""))
            for pi in sc.get("payers_information") or []:
                p = payer(pi.get("payer_name"), pi.get("plan_name"), pi.get("standard_charge_dollar"),
                          pi.get("standard_charge_percentage"), pi.get("standard_charge_algorithm"),
                          pi.get("methodology"), pi.get("median_amount") or pi.get("estimated_amount"),
                          pi.get("additional_payer_notes"))
                if p:
                    item["payers"].append(p)
            items.append(item)
    return json_meta(doc), items, "json"


def sniff_json(path: Path) -> bool:
    head = path.open("rb").read(64)
    if head[:2] == b"\xff\xfe":
        s = head[2:].decode("utf-16-le", "ignore")
    elif head[:2] == b"\xfe\xff":
        s = head[2:].decode("utf-16-be", "ignore")
    else:
        s = head.decode("utf-8", "ignore")
    return s.lstrip("\ufeff \r\n\t").startswith("{")


def parse_file(path: Path) -> tuple[dict, list[dict], str]:
    with open_text(path) as f:
        return parse_json(json.load(f)) if sniff_json(path) else parse_csv(f)


# ---------- compaction ----------

SETTING_RANK = {"outpatient": 0, "both": 1, "": 2, "inpatient": 3}


GOV = {"Medi-Cal": r"medi-?cal|medicaid", "Medicare": r"medicare|\bmcr\b|senior"}


def _gov(p: dict) -> str | None:
    blob = f"{p['payer']} {p['plan']}"
    return next((g for g, rx in GOV.items() if re.search(rx, blob, re.I)), None)


def pick_payers(payers: list[dict]) -> list[dict]:
    """A few named rates: one per payer family, commercial plans first. A commercial payer's Medicare Advantage or
    Medi-Cal plan is used only if it has no commercial row, and is labelled as such ("Health Net (Medicare plan)")."""
    chosen, used = [], set()
    for family, rx in PAYER_FAMILIES:
        if family in GOV:
            hits = [p for p in payers if re.search(GOV[family], p["payer"], re.I) and id(p) not in used]
        else:
            hits = [p for p in payers if re.search(rx, f"{p['payer']} {p['plan']}", re.I) and id(p) not in used]
            hits = [p for p in hits if not _gov(p)] or hits
        if not hits:
            continue
        p = next((p for p in hits if p["dollar"] is not None), hits[0])
        label = family if family in GOV or not _gov(p) else f"{family} ({_gov(p)} plan)"
        used.add(id(p))
        chosen.append({"family": label, **{k: v for k, v in p.items() if v is not None}})
        if len(chosen) >= MAX_PAYERS:
            break
    return chosen


def _rank(item: dict):
    return (SETTING_RANK.get(item["setting"], 2), item["billing_class"] == "professional", bool(item["modifiers"]))


def _dollars(item: dict) -> list[float]:
    return [p["dollar"] for p in item["payers"] if p["dollar"] is not None]


def entry(code: str, items: list[dict]) -> dict:
    """One code's entry. Some files (Sutter) put the gross charge on a chargemaster line and the payer rates
    on a separate line for the same code, so the charge line and the rate line are chosen independently:
    outpatient before inpatient, facility before professional, no modifiers, most payer rates."""
    with_gross = sorted((i for i in items if i["gross"] is not None), key=lambda i: (*_rank(i), -len(_dollars(i))))
    with_rates = sorted((i for i in items if i["payers"]), key=lambda i: (*_rank(i), -len(_dollars(i))))
    charge = with_gross[0] if with_gross else None
    rates = with_rates[0] if with_rates else None
    base = charge or rates or sorted(items, key=_rank)[0]
    code_type = next(t for c, t in base["codes"] if c == code)
    dollars = _dollars(rates) if rates else []
    mm = next((i for i in (rates, charge) if i and (i["min"] is not None or i["max"] is not None)), None)
    if mm:
        lo, hi, src = mm["min"], mm["max"], "file (standard_charge min/max)"
    elif dollars:
        lo, hi, src = min(dollars), max(dollars), "computed from the payer dollar rates listed"
    else:
        lo = hi = src = None
    cash = base["discounted_cash"] if base["discounted_cash"] is not None else (rates or base)["discounted_cash"]
    unit_src = next((i for i in (base, rates) if i and (i["drug_unit"] or i["drug_type"])), None)
    notes = []
    if unit_src:
        notes.append(f"Drug priced per {unit_src['drug_unit']:g} {unit_src['drug_type']} as listed; compare billed "
                     f"units with the HCPCS unit ({CODE_INFO[code]['common_name']})." if unit_src["drug_unit"] else
                     f"Drug unit type {unit_src['drug_type']} without a quantity.")
    elif code.startswith("J"):
        notes.append(f"Drug: no unit of measure in the file for this line; billed units are ambiguous "
                     f"(HCPCS unit: {CODE_INFO[code]['common_name']}).")
    if base["gross"] is not None and cash is not None and base["gross"] == cash:
        notes.append("Discounted cash price equals the gross charge in this file.")
    if charge and rates and charge is not rates:
        notes.append(f"Gross charge is from line '{charge['description']}'; payer rates are from line "
                     f"'{rates['description']}' (the file lists them separately).")
    if not rates:
        notes.append("No payer-specific negotiated rates listed for this code.")
    if len(items) > 1:
        notes.append(f"{len(items)} line items carry this code (settings, modifiers, package sizes); "
                     f"showing the {base['setting'] or 'unspecified'}-setting line.")
    if base["notes"]:
        notes.append(f"File note: {base['notes']}")
    others = [i for i in sorted(items, key=_rank) if i is not charge and i is not rates]
    out = {"code": code, "code_type": code_type, **CODE_INFO[code], "description": base["description"],
           "setting": base["setting"] or None, "billing_class": base["billing_class"] or None,
           "gross": base["gross"], "discounted_cash": cash,
           "negotiated_min": lo, "negotiated_max": hi, "min_max_source": src,
           "negotiated_median_of_payers": round(median(dollars), 2) if dollars else None,
           "rates_line": rates["description"] if rates and rates is not base else None,
           "payer_rates": len(rates["payers"]) if rates else 0, "payer_dollar_rates": len(dollars),
           "payers": pick_payers(rates["payers"]) if rates else [], "modifiers": base["modifiers"] or None,
           "drug_unit": unit_src["drug_unit"] if unit_src else None,
           "drug_unit_type": (unit_src["drug_type"] or None) if unit_src else None, "notes": notes,
           "line_items": len(items),
           "variants": [{"description": i["description"], "setting": i["setting"] or None, "gross": i["gross"],
                         **({"modifiers": i["modifiers"]} if i["modifiers"] else {}),
                         **({"drug_unit": i["drug_unit"]} if i["drug_unit"] else {})}
                        for i in others[:3]]}
    return {k: v for k, v in out.items() if v not in (None, [], "")}


def compact(items: list[dict]) -> dict[str, dict]:
    by_code: dict[str, list[dict]] = {}
    for it in items:
        for c in dict.fromkeys(wanted(it["codes"])):
            by_code.setdefault(c, []).append(it)
    return {c: entry(c, by_code[c]) for c, _, _ in CODES if c in by_code}


def meta_field(meta: dict, *names: str) -> str:
    for n in names:
        for k, v in meta.items():
            if k == n or k.startswith(n + "|"):
                return v
    return ""


def build_table(hospital_id: str, raw_path: Path, source: dict) -> dict:
    """Parse one raw price file into the committed compact table."""
    meta, items, layout = parse_file(raw_path)
    table = compact(items)
    return {
        "hospital_id": hospital_id,
        "hospital_name": meta_field(meta, "hospital_name"),
        "location_name": meta_field(meta, "location_name", "hospital_location"),
        "hospital_address": meta_field(meta, "hospital_address"),
        "last_updated_on": meta_field(meta, "last_updated_on"),
        "template_version": meta_field(meta, "version"),
        "layout": layout,
        "source": source,
        "codes_requested": len(CODES), "codes_found": len(table),
        "missing_codes": [c for c, _, _ in CODES if c not in table],
        "prices": table,
    }


# ---------- read API (used by the app) ----------

@lru_cache(maxsize=64)
def table(hospital_id: str, root: str | None = None) -> dict:
    path = Path(root or PRICES_DIR) / f"{hospital_id}.json"
    return json.loads(path.read_text()) if path.exists() else {}


def load(hospital_id: str, root: str | None = None) -> dict[str, dict]:
    """code -> entry for one hospital ({} if there is no table)."""
    return table(hospital_id, root).get("prices", {})


def lookup(hospital_id: str, code: str, root: str | None = None) -> dict | None:
    return load(hospital_id, root).get((code or "").strip().upper())


def hospitals(root: str | None = None) -> list[str]:
    d = Path(root or PRICES_DIR)
    return sorted(p.stem for p in d.glob("*.json")) if d.exists() else []
