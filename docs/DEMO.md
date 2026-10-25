# Remy — demo runbook

## The 3-minute script

**0:00 Hook** (narrator, holding the printed bill)
> "Rosa got this. Final notice from California Pacific Medical Center: **$8,700**, collections in 10 days.
> Her insurance was supposed to cover the ER visit. Most people either pay it or give up."

**0:20 Drop it in**: drag `demo/sample-hospital-bill.pdf` + `demo/sample-insurance-eob.pdf`, click Go.
> "No forms, no questions. Remy reads the bill and the insurance statement."

**0:35 Review**: the two agent checklists fill in side by side.
- Auditor (Capital One Nessie): reads the insurance statement (claim C-2026-0815-44917): the plan allowed
  $4,760 and paid $3,660, so her share is **$1,100**. The hospital never applied it. Then it finds the
  **$500 she already paid** on Sep 14, which the bill doesn't show, and flags a **duplicate $75 copay**:
  **$1,100 − $500 = $600**, 93% less than the bill.
- Policy (Bright Data): pulls **Sutter Health's own policies** from our policy database (crawled from
  sutterhealth.org and its policy PDFs, every quote verified against the source). Her payroll deposits
  ($1,400 every two weeks, $36,400/yr) put her at 228% of the poverty line. Sutter writes off an insured
  patient's share only if medical bills are over 10% of income; hers are 3%. It says so plainly: **not
  eligible for the write-off**. But Sutter's billing and collections policy says **"All payment plans shall
  be interest-free"**, capped at 10% of monthly family income → **6 × $100/month, 0% interest**.
> "Notice the Policy agent waits for the Auditor; they're working in parallel, sharing results."

**1:15 Approval**: "Can Remy call billing for you?" lists exactly what it will ask for: apply the
insurance, credit the $500, an interest-free plan for the $600, pause collections. Pick the caller voice
(e.g. Indian English) and hit ▶ to hear it.
> "Nothing happens on her behalf without her OK. It will never agree to pay on a call."

**1:25 The call** (the wow): teammate's phone rings (Twilio) or the rep console opens (desk mode). Diane
pushes back ("the full $8,700 is due", "I don't see a $500 payment", "can you pay by card today?").
Remy cites the claim number, the payment date and reference, and Sutter's own billing policy, refuses
to pay on the call, and asks for the interest-free plan and a pause on collections. Diane gives **case FA
20931**, sets up the plan and pauses collections.

**2:20 Resolved**: **$600** (struck-through $8,700), **6 × $100/month, 0% interest**, collections paused,
case FA 20931. With her OK, the plan is saved to her Capital One (Nessie) account as a recurring bill.
"I'll check back by Fri Oct 30 if the corrected statement hasn't arrived."

**2:35 Follow-up**: open **All cases** (dashboard) → "What's the status?" → click **+1 week**: the case
goes overdue → **Call again**: the follow-up call opens with "On the October 24th call, Diane opened case
FA 20931…".

**2:50 Close**
> "**$8,700 → $600, interest-free, collections stopped.** Remy fights your hospital bill so you don't
> have to."

Optional for the Bright Data judge: `/hospitals.html`: the policy DB (4 SF hospitals, verified quotes,
sources, recheck button) and **the same bill at each hospital**. It defaults to Marcus's bill: CPMC and
UCSF: $0; Chinese Hospital and ZSFG: $5,600. (For Rosa every hospital gives $600.)

## Real vs simulated (say it before they ask)
- **Real:** Bright Data crawls of real hospital policies, Nessie records (seeded synthetic patient), Claude
  reading the PDFs and reasoning, Cartesia speech, the approval gate, the audit log.
- **Simulated:** the patient, bill, insurance statement and bank history are synthetic (watermarked SAMPLE).
  "Billing" is a teammate.
- **Live mode** builds the case only from the uploaded bill + EOB (Claude reads name, DOB, bill, EOB); the
  bank account is found in Nessie by her name; the policy comes from the Bright Data policy DB. "Use the
  sample bill" sends the sample PDFs through the same reading. The sample data only feeds the offline mock.

## Pre-flight (T-30 min)
- [ ] `.env` has Anthropic, Nessie, Bright Data (+ zones), Cartesia; Twilio (+ `PUBLIC_BASE_URL` tunnel) if doing a real call
- [ ] `uv run uvicorn remy.app:app --port 8000`, open http://localhost:8000/
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
