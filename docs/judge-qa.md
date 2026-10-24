# Judge Q&A prep

**"Is it legal for an AI to call a hospital for someone?"**
Yes, with the patient's OK. HIPAA lets a hospital discuss payment with someone the patient agrees to,
and the agreement can be verbal (45 CFR 164.510(b)). So the call starts with a warm handoff: the patient
says on the line that BillBack may discuss the account, then the agent continues and answers identity
checks (date of birth, member ID). At scale, a signed authorization (164.508) on file replaces the handoff.
BillBack says it's an automated assistant and that the call is transcribed at the start of every call:
California requires all-party consent to recording, and it's the honest thing to do.

**"What about HIPAA?"**
This demo uses only synthetic data. BillBack acts for the patient (it's the patient's representative,
not a vendor to the hospital), stores documents encrypted, and keeps the minimum necessary: the bill, the
EOB, and the payment records it needs. Details and sources: `docs/feasibility.md`.

**"Could a real bank actually give you this data?"**
Yes. BillBack only needs transactions (payee, amount, date) and payroll deposits, which consumer-consent
aggregators (Plaid, MX, Finicity) provide today with the user's permission, including income products.
Nessie stands in for that in the demo. We don't depend on the CFPB's 1033 open-banking rule (its
compliance dates are stayed while it's rewritten), and statement upload is the fallback.

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
