# BillBack — demo runbook

## The 3-minute script

**0:00 Hook** (narrator, holding the printed bill)
> "Rosa got this. Final notice from California Pacific Medical Center: **$8,700**, collections in 10 days.
> Her insurance was supposed to cover the ER visit. Most people either pay it or give up."

**0:20 Drop it in**: drag `demo/sample-hospital-bill.pdf` + `demo/sample-insurance-eob.pdf`, click Go.
> "No forms, no questions. BillBack reads the bill and the insurance statement."

**0:35 Review**: the two agent checklists fill in side by side.
- Auditor (Capital One Nessie): finds the **$500 she already paid**, flags a **double charge**, and does
  the math: **$8,700 − $6,100 insurance − $500 = $2,100**.
- Policy (Bright Data): pulls **Sutter Health's own financial-assistance policy** from our policy database
  (crawled from sutterhealth.org and its policy PDF, every quote verified against the source). Her payroll
  deposits put her at **150% of the poverty line**, and her $2,600 share is 11% of her income, so the policy
  writes off her whole share → **$0, and the $500 she paid comes back**.
> "Notice the Policy agent waits for the Auditor; they're working in parallel, sharing results."

**1:15 Approval**: "Can BillBack call billing for you?" lists exactly what it will ask for. Pick the caller
voice (e.g. Indian English) and hit ▶ to hear it.
> "Nothing happens on her behalf without her OK. It will never agree to pay on a call."

**1:25 The call** (the wow): teammate's phone rings (Twilio) or the rep console opens (desk mode). Diane
pushes back ("the full $8,700 is due", "I don't see a $500 payment", "can you pay by card today?").
BillBack cites the claim number, the payment date and reference, and the hospital's own policy, refuses to
pay, and asks for a hold. Diane gives **case FA 20931** and puts the account on hold.

**2:20 Resolved**: **$0** (struck-through $8,700), **+$500 back**, case number, account on hold, and
"I'll check back by Fri Oct 30 if the corrected statement hasn't arrived."

**2:35 Follow-up**: open **All cases** (dashboard) → "What's the status?" → click **+1 week**: the case
goes overdue → **Call again**: the follow-up call opens with "On the October 24th call, Diane opened case
FA 20931…".

**2:50 Close**
> "**$8,700 → $0, and $500 back.** BillBack fights your hospital bill so you don't have to."

Optional for the Bright Data judge: `/hospitals.html`: the policy DB (4 SF hospitals, verified quotes,
sources, recheck button) and **the same bill at each hospital** (CPMC and UCSF: $0; Chinese Hospital and
ZSFG: $2,100).

## Real vs simulated (say it before they ask)
- **Real:** Bright Data crawls of real hospital policies, Nessie records (seeded synthetic patient), Claude
  reading the PDFs and reasoning, Cartesia speech, the approval gate, the audit log.
- **Simulated:** the patient, bill, insurance statement and bank history are synthetic (watermarked SAMPLE).
  "Billing" is a teammate.

## Pre-flight (T-30 min)
- [ ] `.env` has Anthropic, Nessie, Bright Data (+ zones), Cartesia; Twilio (+ `PUBLIC_BASE_URL` tunnel) if doing a real call
- [ ] `uv run uvicorn billback.app:app --port 8000`, open http://localhost:8000/
- [ ] One full rehearsal run; if phone: teammate's ringer ON, on speaker, number verified on the Twilio trial
- [ ] Backup: no Twilio → desk mode (rep console opens from the case page)
- [ ] Backup 2: replay mode: `case.html?replay=1` (or `&scenario=freecare`) replays the last finished run. Do one clean rehearsal right before judging so the latest recording is a good one.
- [ ] Second case: the free-care link on Drop & Go: Marcus Lee, UCSF, $5,600 → $0.
- [ ] Demo PDFs on the desktop for drag-and-drop (`/samples/` serves them)

## Fallbacks during the demo
| What breaks | Say / do |
|---|---|
| Call doesn't ring in 15 s | "Venue cell signal" → desk mode: teammate plays billing in the rep console |
| Claude slow | Keep narrating the checklist; the agents stream step by step |
| Wi-Fi dies | Replay mode: "here's the exact run from an hour ago" |
| Judge asks about another hospital | `/hospitals.html` comparison, or add one: `scripts/crawl_policies.py crawl` |
