"""Intake: turns uploaded bill / EOB files into structured JSON with Claude vision."""
import base64

from .. import llm
from ..events import bus

SYSTEM = """You extract data from a hospital bill and/or an insurance Explanation of Benefits (EOB).
Return JSON: {"patient": {"first_name", "last_name", "dob", "address": {"street_number", "street_name", "city",
"state", "zip"}} | null,
"bill": {"provider", "account_number", "statement_date", "notice", "due_date", "service_date",
"total_charges", "amount_due", "billing_phone", "billing_phone_label", "line_items": [{"code", "description",
"amount"}]} | null,
"eob": {"insurer", "member_id", "claim_number", "provider", "service_date", "billed", "allowed", "plan_paid",
"patient_responsibility", "status"} | null}
Numbers as plain numbers. Dates as YYYY-MM-DD. Use null for anything not shown.
billing_phone: the phone number the bill gives for billing questions (e.g. "Questions? Call ..."), copied exactly
as printed; billing_phone_label: the office it names (e.g. "Patient Financial Services"). Never guess a number."""

MEDIA = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}


async def parse(case_id: str, files: list[tuple[str, bytes]]) -> dict:
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
    bus.emit(case_id, "agent_started", "intake", text=f"Reading {len(blocks)} document(s)")
    out = await llm.json_out(SYSTEM, blocks + [{"type": "text", "text": "Extract the bill and EOB."}])
    found = [k for k in ("bill", "eob") if isinstance(out, dict) and out.get(k)]
    bus.emit(case_id, "agent_finished", "intake", summary="Bill and EOB extracted", found=found)
    return out
