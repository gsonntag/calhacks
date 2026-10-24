# BillBack — demo runbook

## The 3-minute script

**0:00 Hook** (narrator, holding the printed bill)
> "Rosa got this. Final notice: **$8,700**, collections in 10 days. Her insurance was supposed to cover
> the ER visit. Most people either pay it or give up. Hospitals know that."

**0:20 Drop it in** — drag `demo/sample-hospital-bill.pdf` + `demo/sample-insurance-eob.pdf`, click Go.
> "No forms, no questions. BillBack reads the bill and the insurance statement."

**0:35 Review** — the two agent checklists fill in side by side.
- Auditor (Capital One Nessie): finds the **$500 she already paid**, flags a **double charge**, and does
  the math: **$8,700 − $6,100 insurance − $500 = $2,100**.
- Policy (Bright Data + Claude): pulls the hospital's **own financial-assistance policy**, works out her
  income from payroll deposits (**216% of the poverty line**), and finds she qualifies for **75% off her $2,600 share → $650**, minus the $500 she already paid → **$150**.
> "Notice the Policy agent waits for the Auditor — they're working in parallel, sharing results."

**1:15 Approval** — "Can BillBack call billing for you?" lists exactly what it will ask for.
> "Nothing happens on her behalf without her OK. It will never agree to pay on a call."

**1:25 The call** (the wow) — teammate's phone rings on the table, on speaker, playing "Diane from billing".
Diane pushes back ("the full $8,700 is due", "I don't see a $500 payment"). BillBack cites the insurance
claim number, the payment date and reference, and the hospital's own policy, and asks for a hold.
Live transcript streams on screen. Diane gives **case FA-20931** and puts the account on hold.

**2:30 Resolved** — **$150** (struck-through $8,700), case number, account on hold. Approve the
interest-free plan (**3 × $50/mo**) → it's written to her bank as a recurring payment in Nessie.

**2:50 Close**
> "**$8,700 → $150.** Five minutes. Zero hold music. BillBack fights your hospital bill so you don't have to."

## Real vs simulated (say it before they ask)
- **Real:** Capital One Nessie records (seeded synthetic patient), Claude reading the PDFs and reasoning,
  the Cartesia voice agent placing a real phone call, the approval gate, the audit log, the plan written to Nessie.
- **Simulated:** the patient, bill, insurance statement and bank history are synthetic. "Billing" is a teammate.
  The policy is the sample policy until Bright Data zones are set (then it's a real hospital's live page).

## Pre-flight (T-30 min)
- [ ] `.env` has Anthropic, Nessie, Cartesia (+ agent id, number id), Bright Data (+ zones), REP_PHONE_NUMBER
- [ ] `uv run uvicorn billback.app:app --port 8000`, open http://localhost:8000/case.html
- [ ] "Under the hood" header shows each integration live
- [ ] One full rehearsal run including the real call; teammate's phone ringer ON, on speaker, charged
- [ ] Backup: if the phone call fails, unset REP_PHONE_NUMBER → desk mode (rep console in a second tab)
- [ ] Backup 2: replay mode — `case.html?replay=1` replays the last finished run (recorded automatically; a fallback recording ships in `demo/`). Do one clean rehearsal right before judging so the latest recording is a good one.
- [ ] **Cartesia agent minutes:** the free plan's agent quota ran out during overnight testing (HTTP 402). Get hackathon credits at the Cartesia booth or upgrade before the first real call. Desk mode still works without them.
- [ ] Second case ready: `POST /api/cases?scenario=freecare` (or the link on Drop & Go) — Marcus Lee, $5,600 → $0 free care.
- [ ] Demo PDFs on the desktop for drag-and-drop

## Fallbacks during the demo
| What breaks | Say / do |
|---|---|
| Call doesn't ring in 15 s | "Venue cell signal" → switch to desk mode: teammate plays billing in the rep console |
| Claude slow | Keep narrating the checklist; the agents stream step by step |
| Wi-Fi dies | Replay mode — "here's the exact run from an hour ago" |
| Judge asks to change a number | Edit `demo/case.json`, reseed Nessie (`scripts/seed_nessie.py`), rerun |
