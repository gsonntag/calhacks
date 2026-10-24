# Judge Q&A prep

**"Is it legal for an AI to call a hospital for someone?"**
The patient authorizes it, and BillBack says it's an automated assistant at the start of every call
(California's bot-disclosure law requires that). Hospitals already talk to authorized representatives:
family members, patient advocates. In production we'd collect a signed HIPAA authorization naming
BillBack as the patient's representative before any call.

**"What about HIPAA?"**
This demo uses only synthetic data. In production, BillBack would be a business associate under a BAA,
store documents encrypted, and keep the minimum necessary: the bill, the EOB, and the payment records it
needs. Patients connect their bank read-only.

**"What if the AI is wrong?"**
The money math is deterministic code, not the model: reconciliation, income vs. poverty line, discount
tiers and payment plan are all computed and every number cites its source (bill line, EOB field, bank
transaction id). Claude reads documents, extracts the policy with verbatim quotes, and does the talking.
Anything consequential — placing the call, agreeing to a payment plan — waits for the patient's approval,
and the call agent is instructed never to agree to pay or give card details.

**"Does this only work on your example?"**
Upload any bill + EOB: Claude extracts the fields; the auditor works from whatever bank history exists.
(Second demo case: a clinic bill where the patient qualifies for free care → $0.)

**"Do hospitals really have to give discounts?"**
Nonprofit hospitals must have and publish a financial-assistance policy to keep their tax exemption
(IRS 501(r)). California's Hospital Fair Pricing law requires charity care or discounts for patients up to
400% of the federal poverty level. Many eligible patients never apply because nobody tells them.

**"How is this different from existing companies?"**
Advocates (human) charge a share of savings or hourly fees; AI bill tools mostly *explain* bills.
BillBack does the whole job: proves what you owe from your actual bank records, finds the discount the
hospital's own policy requires, and makes the phone call.

**"How do you make money?"**
Free for patients below the poverty-line thresholds. Success fee on savings for others, or B2B: employers
and benefits platforms offering it as a perk, and hospitals' own patient-financial-services teams using it
to clear charity-care backlogs.

**Sponsor questions**
- *Cartesia:* the caller is a Cartesia Managed Agent (Claude Haiku 5.5 on Cartesia), outbound call from a
  Cartesia number, per-call case brief as dynamic variables, live transcript by polling the call. Voice is
  the product's core moment, not decoration.
- *Capital One Nessie:* the patient's account is the source of truth — payroll deposits for income,
  the $500 payment the hospital missed, the double charge, the hospital bill, and the approved plan written
  back as a recurring bill.
- *Bright Data:* SERP API finds the hospital's financial-assistance policy, Web Unlocker fetches the live
  page; without it there's no "the hospital's own rules say…".
- *Microsoft "Built for the Real World":* human approval gate, tiered tool permissions, append-only audit
  log of every tool call, deterministic money math, honest real-vs-simulated labels.

**"What's next?"**
Real Gmail/portal intake, signed authorization flow, written follow-ups (pre-filled assistance
application + dispute letter), Spanish-language patient side, and follow-up calls until the corrected
statement arrives.
