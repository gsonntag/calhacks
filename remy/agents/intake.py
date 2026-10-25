"""Intake: turns uploaded bill / EOB files into structured JSON with Claude vision."""
import base64
import hashlib

from .. import llm
from ..events import bus

SYSTEM = """You extract data from a hospital bill and/or an insurance Explanation of Benefits (EOB).
Return JSON: {"patient": {"first_name", "last_name", "dob", "address": {"street_number", "street_name", "city",
"state", "zip"}} | null,
"bill": {"provider", "account_number", "statement_date", "notice", "due_date", "service_date",
"total_charges", "amount_due", "billing_phone", "billing_phone_label", "collection_agency", "collection_phone",
"line_items": [{"code", "description", "amount"}]} | null,
"eob": {"insurer", "member_id", "claim_number", "provider", "service_date", "billed", "allowed", "plan_paid",
"patient_responsibility", "status"} | null}
billing_phone: the phone number the bill gives for billing questions (e.g. "Questions? Call ..."), copied exactly
as printed; billing_phone_label: the office it names (e.g. "Patient Financial Services"). Never guess a number.
collection_agency / collection_phone: only if the bill says the account is with a collection agency.
Numbers as plain numbers. Dates as YYYY-MM-DD. Use null for anything not shown."""

MEDIA = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}


_read: dict[str, dict] = {}  # file contents hash -> what Claude read, so chat and the case read a file once


async def read(files: list[tuple[str, bytes]]) -> dict:
    """What the documents show (SYSTEM's JSON), or {} when there's nothing readable. Cached by file contents."""
    blocks = []
    for name, data in files:
        ext = name.rsplit(".", 1)[-1].lower()
        mt = MEDIA.get(ext)
        if not mt:
            continue
        kind = "document" if ext == "pdf" else "image"
        blocks.append({"type": kind, "source": {"type": "base64", "media_type": mt,
                                                "data": base64.b64encode(data).decode()}})
    if not blocks:
        return {}
    key = hashlib.sha256(b"".join(hashlib.sha256(d).digest() for _, d in sorted(files))).hexdigest()
    if key not in _read:
        out = await llm.json_out(SYSTEM, blocks + [{"type": "text", "text": "Extract the bill and EOB."}])
        if not isinstance(out, dict):
            return out
        _read[key] = out
    return _read[key]


def summary(out: dict) -> str:
    """One line for the chat assistant: what the documents show, so it doesn't ask for it."""
    bill, eob = out.get("bill") or {}, out.get("eob") or {}
    if not bill and not eob:
        return "no bill or insurance statement found in the attached files"
    bits = []
    if bill:
        bits.append("bill from " + str(bill.get("provider") or "an unnamed provider")
                    + (f", ${bill['amount_due']} due" if bill.get("amount_due") not in (None, "") else "")
                    + (f" by {bill['due_date']}" if bill.get("due_date") else "")
                    + (f" (notice: {bill['notice']})" if bill.get("notice") else "")
                    + (f", sent to collections ({bill['collection_agency']})" if bill.get("collection_agency") else ""))
    else:
        bits.append("no hospital bill found")
    if eob:
        bits.append("insurance statement from " + str(eob.get("insurer") or "the insurer")
                    + (f", patient share ${eob['patient_responsibility']}"
                       if eob.get("patient_responsibility") not in (None, "") else ", patient share not shown"))
    else:
        bits.append("no insurance statement (EOB) attached")
    return "; ".join(bits)


async def parse(case_id: str, files: list[tuple[str, bytes]]) -> dict:
    n = sum(1 for name, _ in files if name.rsplit(".", 1)[-1].lower() in MEDIA)
    if not n:
        return {}
    bus.emit(case_id, "agent_started", "intake", text=f"Reading {n} document(s)")
    out = await read(files)
    found = [k for k in ("bill", "eob") if isinstance(out, dict) and out.get(k)]
    bus.emit(case_id, "agent_finished", "intake", summary="Bill and EOB extracted", found=found)
    return out
