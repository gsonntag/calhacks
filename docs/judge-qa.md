# Judge Q&A prep

**"Is it legal for an AI to call a hospital for someone?"**
Yes, with the patient's OK. HIPAA lets a hospital discuss payment with someone the patient agrees to,
and the agreement can be verbal (45 CFR 164.510(b)). So the call starts with a warm handoff: the patient
says on the line that Remy may discuss the account, then the agent continues and answers identity
checks (date of birth, member ID). At scale, a signed authorization (164.508) on file replaces the handoff.
Remy says it's an automated assistant and that the call is transcribed at the start of every call:
California requires all-party consent to recording, and it's the honest thing to do.

**"What about HIPAA?"**
This demo uses only synthetic data. Remy acts for the patient (it's the patient's representative,
not a vendor to the hospital), stores documents encrypted, and keeps the minimum necessary: the bill, the
EOB, and the payment records it needs. Details and sources: `docs/feasibility.md`.

**"Could a real bank actually give you this data?"**
Yes. Remy only needs transactions (payee, amount, date) and payroll deposits, which consumer-consent
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
No. In live mode the case is built only from the uploaded bill + EOB: Claude reads the patient's name,
date of birth, the bill and the EOB. The bank account is found in Nessie by the patient's name, and the
policy comes from our Bright Data policy database. "Use the sample bill" sends the sample PDFs through the
same reading; the built-in sample data only feeds the offline mock (no API keys).
(Second demo case: Marcus at UCSF, where the bill is already correct but he qualifies for free care → $0.)

**"Do hospitals really have to give discounts?"**
Nonprofit hospitals must have and publish a financial-assistance policy to keep their tax exemption
(IRS 501(r)). California's Hospital Fair Pricing law requires charity care or discounts for patients up to
400% of the federal poverty level. Many eligible patients never apply because nobody tells them.

**"How is this different from existing companies?"**
Advocates (human) charge a share of savings or hourly fees; AI bill tools mostly *explain* bills.
Remy does the whole job: proves what you owe from your actual bank records, finds the discount the
hospital's own policy requires, and makes the phone call.

**"How do you make money?"**
Free for patients below the poverty-line thresholds. Success fee on savings for others, or B2B: employers
and benefits platforms offering it as a perk, and hospitals' own patient-financial-services teams using it
to clear charity-care backlogs.

**Sponsor questions**
- *Cartesia:* our own voice pipeline (`remy/voice/`, `docs/voice.md`): Twilio Media Streams audio goes
  to Cartesia Ink streaming STT, Claude decides what to say, Sonic TTS speaks it back in μ-law, with
  barge-in. Voice/accent picker; a pronunciation normalizer reads money, claim numbers, CPT codes and drug
  names correctly. Follow-up calls remember the last call.
- *Capital One Nessie:* the patient's account is the source of truth: payroll deposits for income, the
  $500 payment the hospital missed, the double charge, and a plan written back as a recurring bill when
  something is still owed.
- *Bright Data:* a real policy database (`remy/policies/`, `docs/brightdata.md`, evidence in
  `policydb/`): SERP API discovers each hospital's policy, Web Unlocker fetches pages and policy PDFs,
  extracted rules must quote text that the code finds in the source, versions are hashed and re-checked.
  Same bill at 4 SF hospitals gives different answers.
- *Microsoft "Built for the Real World":* human approval gate, tiered tool permissions, append-only audit
  log of every tool call, deterministic money math, honest real-vs-simulated labels.

**"What's next?"**
Real Gmail/portal intake, signed authorization flow, written follow-ups (pre-filled assistance
application + dispute letter), Spanish-language patient side, and follow-up calls until the corrected
statement arrives.
