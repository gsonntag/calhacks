"""Render the synthetic demo bill and EOB from demo/case.json as PDFs you can upload on stage.

    uv run --with reportlab python scripts/make_demo_docs.py
"""
import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
case = json.loads((ROOT / "demo" / "case.json").read_text())
p, b, e = case["patient"], case["bill"], case["eob"]
name = f"{p['first_name']} {p['last_name']}"
addr = f"{p['address']['street_number']} {p['address']['street_name']}, {p['address']['city']}, {p['address']['state']} {p['address']['zip']}"
W, H = letter


def header(c: canvas.Canvas, title: str, sub: str) -> None:
    c.setFont("Helvetica-Bold", 18)
    c.drawString(50, H - 60, title)
    c.setFont("Helvetica", 10)
    c.drawString(50, H - 76, sub)
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(colors.grey)
    c.drawRightString(W - 50, H - 40, "SAMPLE DOCUMENT - synthetic patient for a hackathon demo")
    c.setFillColor(colors.black)


def bill() -> None:
    c = canvas.Canvas(str(ROOT / "demo" / "sample-hospital-bill.pdf"), pagesize=letter)
    header(c, b["provider"], "Patient Financial Services · Statement")
    c.setFillColor(colors.HexColor("#BE3337"))
    c.rect(50, H - 120, W - 100, 26, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(60, H - 112, b["notice"])
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 11)
    y = H - 150
    for k, v in [("Patient", name), ("Address", addr), ("Account number", b["account_number"]),
                 ("Statement date", b["statement_date"]), ("Date of service", b["service_date"]),
                 ("Payment due by", b["due_date"])]:
        c.drawString(50, y, f"{k}:"); c.drawString(170, y, v); y -= 16
    y -= 14
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, y, "Code"); c.drawString(110, y, "Description"); c.drawRightString(W - 50, y, "Charge")
    c.line(50, y - 4, W - 50, y - 4); y -= 20
    c.setFont("Helvetica", 11)
    for li in b["line_items"]:
        c.drawString(50, y, li["code"]); c.drawString(110, y, li["description"])
        c.drawRightString(W - 50, y, f"${li['amount']:,.2f}"); y -= 16
    c.line(50, y + 6, W - 50, y + 6); y -= 10
    c.drawString(110, y, "Total charges"); c.drawRightString(W - 50, y, f"${b['total_charges']:,.2f}"); y -= 16
    c.drawString(110, y, "Insurance payments / adjustments"); c.drawRightString(W - 50, y, "$0.00"); y -= 16
    c.drawString(110, y, "Patient payments"); c.drawRightString(W - 50, y, "$0.00"); y -= 22
    c.setFont("Helvetica-Bold", 14)
    c.drawString(110, y, "AMOUNT DUE"); c.drawRightString(W - 50, y, f"${b['amount_due']:,.2f}")
    c.save()


def eob() -> None:
    c = canvas.Canvas(str(ROOT / "demo" / "sample-insurance-eob.pdf"), pagesize=letter)
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
    c.setFont("Helvetica-Bold", 13)
    c.drawString(50, y, f"What you owe the provider: ${e['patient_responsibility']:,.2f}")
    y -= 18
    c.setFont("Helvetica", 10)
    c.drawString(50, y, "The provider has agreed not to bill you for amounts above the plan-allowed amount (in-network).")
    c.save()


if __name__ == "__main__":
    bill(); eob()
    print("wrote demo/sample-hospital-bill.pdf and demo/sample-insurance-eob.pdf")
