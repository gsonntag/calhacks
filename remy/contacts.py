"""Who to call: the departments a patient's advocate phones about a hospital bill, with numbers and hours.

Data lives in policydb/contacts/<hospital_id>.json, built by scripts/build_contacts.py from the crawled policy
pages and PDFs in policydb/raw/. Every phone number there was found by regex in a source document, classified
by Claude, and kept only if the number and its quote appear verbatim in that document (same idea as the
policy quotes in remy/policies/extract.py). Departments with no published number are listed with
phone null and the reason, never filled in.

    for_hospital(hospital_id) -> [{department, label, phone, hours, source_url, quote, why?}]
    for_case(case)            -> the bill's own "questions? call ..." number first, then the hospital's
"""
import json
import re
import unicodedata
from pathlib import Path

from . import config
from .policies import registry

CONTACTS_DIR = Path(config.env("CONTACTS") or config.ROOT / "policydb" / "contacts")

# Order matters: this is the order the plan shows them in (who the agent calls first).
DEPARTMENTS = ("billing", "financial_assistance", "collections", "advocate")
LABELS = {"billing": "Patient financial services / billing", "financial_assistance": "Financial assistance office",
          "collections": "Collections agency", "advocate": "Patient advocate"}
REQUIRED = ("billing", "financial_assistance")  # a gap here is reported explicitly

# 415-353-3333, (415) 353-3333, 1-866-433-4035, 855.398.1633, +1 628 206 8000
PHONE = re.compile(r"(?<![\d-])(?:\+?1[\s.-]?)?\(?[2-9]\d{2}\)?[\s.-]?\d{3}[\s.-]\d{4}(?![\d-])")
FAX_BEFORE = re.compile(r"(?<![a-z])fax[^.\n\d]{0,64}$", re.I)  # "Fax: 415-...", "Fax your application to 1-800-..."
RELEVANT = re.compile(r"billing|bill\b|financial|charity|customer service|patient accounting|business office|"
                      r"counsel|collection|consumer|ombuds|advocate|patient relations|help paying|\bmfa\b|"
                      r"pay(ment)?s?\b|statement", re.I)


_DAY, _FRI = r"(?:Mon(?:day)?|M)\.?", r"(?:Fri(?:day)?|F)\.?"
_TIME = r"\d{1,2}(?::\d{2})?\s*[ap]\.?\s?m\.?"
_RANGE = rf"{_TIME}\s*(?:[-–—]|to)\s*{_TIME}"
HOURS = re.compile(
    rf"{_DAY}\s*(?:[-–—]|to|through)\s*{_FRI}[,:]?\s*(?:from\s+)?{_RANGE}"
    rf"|(?:between\s+{_TIME}\s+and\s+{_TIME}|{_RANGE}),?\s+{_DAY}\w*\s+(?:through|to|[-–—])\s+{_FRI}\w*"
    rf"|Monday:\s*{_RANGE}(?:\s+\w+day:\s*{_RANGE})*", re.I)


def hours_near(after: str) -> str | None:
    """Office hours stated right after a phone number, before the next number or sentence, verbatim, or None
    (so "... 947-3336, excluding holidays. Contact a Financial Counselor Monday - Friday ..." gives None)."""
    ends = [m.start() for m in (PHONE.search(after), re.search(r"(?<=\.)\s+[A-Z]", after)) if m]
    m = HOURS.search(after[:min(ends)] if ends else after)
    if not m:
        return None
    hours = re.sub(r"\s+", " ", m.group()).strip()
    return hours if re.search(r"[ap]\.?m\.$", hours, re.I) else hours.rstrip(".")


def squash(s: str) -> str:
    """Letters and digits only, lowercased: matching survives PDF line breaks and markdown markup."""
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKC", s or "").lower())


def digits(phone: str) -> str:
    d = re.sub(r"\D", "", phone or "")
    return d[1:] if len(d) == 11 and d.startswith("1") else d


def dial(phone: str) -> str | None:
    d = digits(phone)
    return f"+1{d}" if len(d) == 10 else None


# ---- extraction (used by scripts/build_contacts.py; pure functions, tested offline) ----

def candidates(text: str, before: int = 320, after: int = 140) -> list[dict]:
    """Every phone number in the text that isn't a fax and sits near billing / financial-help words.
    -> [{id, phone, context}] in document order; one entry per distinct number + context."""
    out, seen = [], set()
    for m in PHONE.finditer(text):
        pre = text[max(0, m.start() - before):m.start()]
        if FAX_BEFORE.search(pre[-72:]):
            continue
        ctx = (pre + m.group() + text[m.end():m.end() + after]).strip()
        if not RELEVANT.search(ctx):
            continue
        key = (digits(m.group()), squash(ctx[-200:]))
        if key in seen:
            continue
        seen.add(key)
        out.append({"id": len(out) + 1, "phone": m.group().strip(), "context": re.sub(r"\s+", " ", ctx),
                    "hours": hours_near(text[m.end():m.end() + 300])})
    return out


def verify(item: dict, text: str) -> str:
    """'' if the classified contact is backed by the source text, else why not."""
    phone, quote = (item.get("phone") or "").strip(), (item.get("quote") or "").strip()
    if item.get("department") not in DEPARTMENTS:
        return f"unknown department {item.get('department')!r}"
    if not phone or len(digits(phone)) != 10:
        return "no 10-digit phone number"
    if phone not in text:
        return "phone number not verbatim in the source"
    if phone not in quote:
        return "quote does not contain the phone number"
    if len(squash(quote)) < 20 or squash(quote) not in squash(text):
        return "quote not found in the source"
    hours = (item.get("hours") or "").strip()
    if hours and squash(hours) not in squash(text):
        return "hours not found in the source"
    return ""


# ---- lookup ----

def load(hospital_id: str) -> dict | None:
    path = CONTACTS_DIR / f"{hospital_id}.json"
    return json.loads(path.read_text()) if path.exists() else None


def _entry(c: dict) -> dict:
    return {"department": c["department"], "label": c.get("label") or LABELS.get(c["department"], c["department"]),
            "phone": c.get("phone"), "hours": c.get("hours") or None, "source_url": c.get("source_url"),
            "quote": c.get("quote") or None, **({"why": c["why"]} if c.get("why") else {}),
            **({"via": c["via"]} if c.get("via") else {})}


def for_hospital(hospital_id: str) -> list[dict]:
    """Departments with verified numbers (billing first), then explicit gaps (phone None + why)."""
    data = load(hospital_id)
    if data is None:
        return [{"department": d, "label": LABELS[d], "phone": None, "hours": None, "source_url": None,
                 "quote": None, "why": f"no contact data built for {hospital_id!r}"} for d in REQUIRED]
    rank = {d: i for i, d in enumerate(DEPARTMENTS)}
    found = sorted(data.get("contacts", []), key=lambda c: rank.get(c["department"], 99))
    return [_entry(c) for c in found] + [_entry(g) for g in data.get("gaps", [])]


BILL_PHONE_FIELDS = ("billing_phone", "customer_service_phone", "phone")
QUESTIONS = re.compile(r"(?:questions?|call|contact|customer service|billing)[^.\n]{0,80}?(" + PHONE.pattern + ")", re.I)


def from_bill(bill: dict | None, label: str = "") -> dict | None:
    """The billing number printed on the patient's own bill: an extracted field, or a "questions? call ..."
    line in the bill's notice text."""
    bill = bill or {}
    for f in BILL_PHONE_FIELDS:
        v = bill.get(f)
        if isinstance(v, str) and len(digits(v)) == 10:
            return {"department": "billing", "label": bill.get("billing_phone_label") or label
                    or "Billing (number printed on your bill)", "phone": v.strip(), "hours": None,
                    "source_url": "bill", "quote": None}
    for f in ("notice", "message", "questions"):
        m = QUESTIONS.search(str(bill.get(f) or ""))
        if m:
            return {"department": "billing", "label": label or "Billing (number printed on your bill)",
                    "phone": m.group(1).strip(), "hours": None, "source_url": "bill", "quote": m.group(0).strip()}
    return None


def hospital_id_for(case: dict) -> str | None:
    h, b = case.get("hospital") or {}, case.get("bill") or {}
    if h.get("id"):
        return h["id"]
    for name in (b.get("provider"), h.get("name")):
        if name and (hit := registry.match(name, list(registry.SEED))):
            return hit.id
    return None


def for_case(case: dict) -> list[dict]:
    """What the plan shows under "Who we'll call": the bill's printed number first (it is the account's own
    line), then the hospital's published departments, minus any number already listed."""
    label = (case.get("hospital") or {}).get("billing_phone_label") or ""
    out = []
    if own := from_bill(case.get("bill"), label):
        out.append(own)
    hid = hospital_id_for(case)
    rest = for_hospital(hid) if hid else [{"department": "billing", "label": LABELS["billing"], "phone": None,
                                           "hours": None, "source_url": None, "quote": None,
                                           "why": "hospital not in the policy database"}]
    have = {digits(c["phone"]) for c in out if c.get("phone")}
    for c in rest:
        if c.get("phone") and digits(c["phone"]) in have:
            continue
        if c.get("phone") is None and c["department"] == "billing" and own:
            continue  # the bill filled this gap
        out.append(c)
    return out
