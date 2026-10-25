"""Render the synthetic demo bills and EOBs (demo/case.json, demo/case_freecare.json) as PDFs you can upload
on stage, into demo/ and web/samples/. The hospitals are real but the patients and bills are not, so every page
carries a diagonal "SAMPLE" watermark.

    uv run --with reportlab python scripts/make_demo_docs.py
"""
import json
import shutil
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
OUTPUTS = {"case.json": ("sample-hospital-bill.pdf", "sample-insurance-eob.pdf"),
           "case_freecare.json": ("sample-hospital-bill-freecare.pdf", "sample-insurance-eob-freecare.pdf")}
W, H = letter
p = b = e = {}
name = addr = ""


def load(case_file: str) -> None:
    use(json.loads((ROOT / "demo" / case_file).read_text()))


def use(case: dict) -> None:
    """Render from a case dict (also used by billback/scenarios.py for generated cases)."""
    global p, b, e, name, addr
    p, b, e = case["patient"], case["bill"], case["eob"]
    name = f"{p['first_name']} {p['last_name']}"
    addr = f"{p['address']['street_number']} {p['address']['street_name']}, {p['address']['city']}, {p['address']['state']} {p['address']['zip']}"


class Watermarked(canvas.Canvas):
    """Every page gets the diagonal SAMPLE stamp, drawn when the page is finished."""

    def showPage(self) -> None:
        stamp(self)
        super().showPage()

    def save(self) -> None:
        if self._code:  # unfinished last page
            stamp(self)
        super().save()


def stamp(c: canvas.Canvas) -> None:
    c.saveState()
    c.translate(W / 2, H / 2)
    c.rotate(35)
    c.setFillColor(colors.Color(0.75, 0.2, 0.22, alpha=0.16))
    c.setFont("Helvetica-Bold", 30)
    c.drawCentredString(0, 16, "SAMPLE \u2014 synthetic patient,")
    c.drawCentredString(0, -24, "not a real bill")
    c.restoreState()


def header(c: canvas.Canvas, title: str, sub: str) -> None:
    c.setFont("Helvetica-Bold", 18)
    c.drawString(50, H - 60, title)
    c.setFont("Helvetica", 10)
    c.drawString(50, H - 76, sub)
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(colors.grey)
    c.drawRightString(W - 50, H - 40, "SAMPLE DOCUMENT - synthetic patient for a hackathon demo, not a real bill")
    c.setFillColor(colors.black)


def bill(out: Path) -> None:
    c = Watermarked(str(out), pagesize=letter, invariant=1)
    header(c, b["provider"], "Patient Financial Services · Statement")
    c.setFillColor(colors.HexColor("#BE3337"))
    c.rect(50, H - 120, W - 100, 26, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(60, H - 112, b["notice"])
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 11)
    y = H - 150
    facility = [("Facility", b["facility"])] if b.get("facility") else []
    for k, v in [("Patient", name), ("Date of birth", p["dob"]), ("Address", addr), *facility,
                 ("Account number", b["account_number"]),
                 ("Statement date", b["statement_date"]), ("Date of service", b["service_date"]),
                 ("Payment due by", b["due_date"])]:
        c.drawString(50, y, f"{k}:"); c.drawString(170, y, v); y -= 16
    y -= 14
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, y, "Code"); c.drawString(110, y, "Description"); c.drawRightString(W - 50, y, "Charge")
    c.line(50, y - 4, W - 50, y - 4); y -= 20
    c.setFont("Helvetica", 11)
    for li in b["line_items"]:
        units = f"  x{li['units']}" if li.get("units", 1) != 1 else ""
        c.drawString(50, y, li["code"]); c.drawString(110, y, li["description"] + units)
        c.drawRightString(W - 50, y, f"${li['amount']:,.2f}"); y -= 16
    c.line(50, y + 6, W - 50, y + 6); y -= 10
    c.drawString(110, y, "Total charges"); c.drawRightString(W - 50, y, f"${b['total_charges']:,.2f}"); y -= 16
    adj, paid = b.get("insurance_adjustments", 0.0), b.get("patient_payments", 0.0)
    c.drawString(110, y, "Insurance payments / adjustments"); c.drawRightString(W - 50, y, minus(adj)); y -= 16
    c.drawString(110, y, "Patient payments"); c.drawRightString(W - 50, y, minus(paid)); y -= 22
    c.setFont("Helvetica-Bold", 14)
    c.drawString(110, y, "AMOUNT DUE"); c.drawRightString(W - 50, y, f"${b['amount_due']:,.2f}")
    c.save()


def minus(v: float) -> str:
    return f"-${v:,.2f}" if v else "$0.00"


def eob_lines(c: canvas.Canvas, y: float) -> float:
    """Per-line table, drawn only when the EOB has line detail (generated scenarios)."""
    xs = [50, 82, 175, 245, 315, 390, 465, W - 50]

    def row(vals: list[str]) -> None:
        for i, v in enumerate(vals):
            (c.drawString if i < 2 else c.drawRightString)(xs[i], y, v)

    c.setFont("Helvetica-Bold", 9)
    row(["Line", "Code", "Billed", "Allowed", "Deductible", "Coinsurance", "Plan paid", "You owe"])
    c.line(50, y - 4, W - 50, y - 4)
    y -= 16
    c.setFont("Helvetica", 9)
    notes = []
    for li in e["lines"]:
        mark = ""
        if li.get("remark"):
            mark = "*" * (len(notes) + 1)
            notes.append(f"{mark} Line {li['line']}: {li['remark']}")
        row([str(li["line"]), li["code"] + mark] + [f"${li[k]:,.2f}" for k in (
            "billed", "allowed", "deductible", "coinsurance", "plan_paid", "patient_responsibility")])
        y -= 13
    c.line(50, y + 6, W - 50, y + 6)
    y -= 8
    c.setFont("Helvetica-Bold", 9)
    row(["", "Total"] + [f"${v:,.2f}" for v in (e["billed"], e["allowed"], e.get("deductible_applied", 0.0),
                                                 e.get("coinsurance", 0.0), e["plan_paid"], e["patient_responsibility"])])
    y -= 14
    c.setFont("Helvetica", 8.5)
    for n in notes:
        c.drawString(50, y, n)
        y -= 12
    return y - 10


def eob(out: Path) -> None:
    c = Watermarked(str(out), pagesize=letter, invariant=1)
    header(c, e["insurer"], "Explanation of Benefits · THIS IS NOT A BILL")
    c.setFont("Helvetica", 11)
    y = H - 120
    for k, v in [("Member", name), ("Member ID", e["member_id"]), ("Claim number", e["claim_number"]),
                 ("Provider", e["provider"]), ("Date of service", e["service_date"]), ("Claim status", e["status"])]:
        c.drawString(50, y, f"{k}:"); c.drawString(170, y, v); y -= 16
    y -= 20
    c.setFont("Helvetica-Bold", 11)
    cols = [("Amount billed", e["billed"]), ("Plan allowed", e["allowed"]), ("Plan paid", e["plan_paid"]),
            ("Your responsibility", e["patient_responsibility"])]
    x = 50
    for label, _ in cols:
        c.drawString(x, y, label); x += 130
    y -= 18
    c.setFont("Helvetica", 12)
    x = 50
    for _, v in cols:
        c.drawString(x, y, f"${v:,.2f}"); x += 130
    y -= 36
    if e.get("lines"):
        y = eob_lines(c, y)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(50, y, f"What you owe the provider: ${e['patient_responsibility']:,.2f}")
    y -= 18
    c.setFont("Helvetica", 10)
    c.drawString(50, y, "The provider has agreed not to bill you for amounts above the plan-allowed amount (in-network).")
    c.save()


if __name__ == "__main__":
    for case_file, (bill_pdf, eob_pdf) in OUTPUTS.items():
        load(case_file)
        bill(ROOT / "demo" / bill_pdf)
        eob(ROOT / "demo" / eob_pdf)
        for f in (bill_pdf, eob_pdf):
            shutil.copy(ROOT / "demo" / f, ROOT / "web" / "samples" / f)
            print("wrote demo/" + f, "and web/samples/" + f)
