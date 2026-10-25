# Remy: feasibility analysis

Researched October 2026. Primary sources are linked where I could open them. Several primary sites (leginfo, eCFR) blocked automated fetches, so where a claim rests on secondary summaries it says so. This is engineering diligence, not legal advice.

**Verdicts at a glance**

| Area | Verdict |
|---|---|
| Bank data (transactions + payroll deposits) | Feasible today with consumer consent through aggregators; does not depend on CFPB 1033 |
| Hospital policies | Feasible: published by law (IRS 501(r), California AB 1020) |
| Calling on the patient's behalf | Feasible with the patient on the line or a signed authorization; fully autonomous unattended calls are the shakiest part |
| Legal claims in our current docs | Two need correcting (see section 6) |

## 1. Bank data

### What we need (and nothing more)

| Need | Fields | Used for |
|---|---|---|
| Outgoing transactions | payee/merchant, amount, posted date, transaction id, memo | Prove a payment to the hospital the bill ignored; find duplicate charges; check the plan is affordable |
| Incoming deposits | amount, date, payer name | Estimate annual income (payroll deposits) to compare with the federal poverty line |

We do **not** need: account credentials, SSN, full account or routing numbers, ability to move money (read-only), investment or credit data, or history beyond ~12 months. The demo writes a recurring bill back to Nessie for the payment plan; in production that step would be a user-initiated payment through the bank or the hospital, not our write access.

### How it is obtained in the real world

- **Aggregators with consumer consent.** The patient links their bank through Plaid, MX or Finicity (Mastercard). The standard product is Transactions; Plaid also sells Income. Plaid's docs describe Bank Income (identifies deposits that represent income), Payroll Income and Document Income, and note that most new US customers are steered to its Consumer Report product instead ([Plaid Income docs](https://plaid.com/docs/income/add-to-app/)). Payroll-deposit income inference is exactly our use case. Caveat: it is an estimate (gig income, irregular pay), so we show the number and let the patient correct it, and the hospital will still require its own income documentation.
- **Capital One specifically.** Capital One routes third-party access through its Customer Transactions API using OAuth tokens rather than shared passwords, with Plaid and Finicity both under data-sharing agreements ([Capital One newsroom on Plaid](https://www.capitalone.com/about/newsroom/data-sharing-agreement/), [Finicity agreement](https://financialit.net/node/25207)). Those sources are from 2018-2021. I could not confirm current developer terms or fees, so treat Capital One's present aggregator terms as unverified. Banks have been moving to charge aggregators; JPMorgan and Plaid agreed on a fee structure in 2025 ([Banking Dive](https://www.bankingdive.com/news/plaid-pay-jpmorgan-data-open-banking-fintech/760264/)).
- **Fallbacks that need no bank API:** the patient uploads statement PDFs or CSV exports, which Claude vision can parse. Slower, but works for any bank and is a good demo-day answer.

### CFPB Section 1033 (personal financial data rights): status

- Final rule issued October 2024 (12 CFR part 1033). It would have required large banks to make consumer data available to authorized third parties, free of charge.
- Litigation: *Forcht Bank, N.A. v. CFPB*, No. 5:24-cv-304 (E.D. Ky.). Per the CFPB's own compliance page (last modified Jan. 6, 2026), the court **stayed the rule's compliance dates on October 29, 2025** ([CFPB](https://www.consumerfinance.gov/compliance/compliance-resources/other-applicable-requirements/personal-financial-data-rights/)).
- Reconsideration: the CFPB issued an Advance Notice of Proposed Rulemaking on August 22, 2025 (covering who counts as a "representative", fees for access, security and privacy), and said it planned to propose extending compliance dates ([CFPB](https://www.consumerfinance.gov/compliance/compliance-resources/other-applicable-requirements/personal-financial-data-rights/), [Consumer Finance Monitor](https://www.consumerfinancemonitor.com/2025/08/21/cfpb-invites-comments-on-new-section-1033-open-banking-rule/)). A July 2026 tracker reports no replacement rule had been published ([OpenBankingTracker](https://www.openbankingtracker.com/guides/section-1033-status)). I found nothing newer as of October 2026.
- Some trackers describe an injunction rather than a stay; the CFPB's own page says "stayed", which is the better-supported wording.
- **Implication:** do not claim 1033 gives us access today. It is a tailwind, not a dependency. Our data path is the existing aggregator ecosystem, which operates on customer consent and bilateral bank agreements regardless of 1033. Risk: banks may restrict or price aggregator access while the rule is in limbo.

### Nessie as stand-in

Capital One's Nessie is a sandbox API with synthetic customers, accounts, deposits and bills. It is a legitimate stand-in for the *shape* of the data (payee, amount, date, deposit), and the `finance.py` logic only needs those fields. It is **not** evidence that real banks expose this data through that API. The honest pitch: "Nessie for the demo; Plaid/Finicity/MX with consent in production, mapping the same fields; statement upload as fallback." The adapter boundary is `remy/integrations/nessie.py`, so the swap is contained.

## 2. Hospital side

- **Federal (IRS 501(r)).** Hospitals with 501(c)(3) status must have a written financial assistance policy (FAP), widely publicize it (including on their website with a plain-language summary), limit charges to FAP-eligible patients to "amounts generally billed", and make reasonable efforts to determine FAP eligibility before extraordinary collection actions such as liens or lawsuits ([IRS Notice 2010-39](https://www.irs.gov/pub/irs-tege/n2010_39.pdf); regulations at 26 CFR 1.501(r)-4 to -6, not opened directly). Applies to nonprofit hospitals only, not for-profit or most government hospitals. Exact timing details (120-day notification, 240-day application period) come from secondary summaries here.
- **California Hospital Fair Pricing Act (Health & Safety Code 127400 et seq.), as amended by AB 1020 (effective Jan. 1, 2024)** applies to licensed hospitals, not only nonprofits:
  - Uninsured patients and patients with "high medical costs" at or below **400% FPL** must be eligible for charity care or discounted payment; AB 1020 raised the line from 350% ([California Hospital Association summary](https://calhospital.org/wp-content/uploads/2021/12/AB-1020-Compliance_Final1223.pdf), paywalled; consistent with hospital policies such as [El Camino Health](https://www.elcaminohealth.org/sites/default/files/2026-05/discountedcharitycare-eligibilityprocedures-reviewprocess_05082026.pdf)).
  - "High medical costs" includes annual out-of-pocket hospital costs above the lesser of 10% of current family income or prior-12-month income (CHA summary).
  - Free vs discounted split (my understanding: free care at or below 200% FPL, sliding-scale discount to 400% FPL), collections restrictions (waiting periods, limits on credit reporting and sale of debt) and refunds of overpayment are in the statute, but I could not open the text. **Verify against [leginfo 127400-127446](https://leginfo.legislature.ca.gov/faces/codes_displayText.xhtml?lawCode=HSC&division=107.&title=&part=2.&chapter=2.5.&article=) before presenting exact tier numbers.** Also note AB 2297 and SB 1061 took effect Jan. 1, 2025 ([HCAI](https://hcai.ca.gov/data-and-reports/hfbp/)).
  - Compliance is overseen by California's HCAI. Its Hospital Fair Billing Program lets anyone look up hospital charity-care, discount-payment and debt-collection policies and applications; the old SyFPHR portal no longer accepts submissions and hospitals now file through HCAI's Hospital Disclosures and Compliance system ([HCAI](https://hcai.ca.gov/data-and-reports/hfbp/), [SyFPHR notice](https://syfphr.hcai.ca.gov)). I did not test the live lookup.
- **Practical limits.** Policies vary in format (PDF vs web page), each hospital sets its own sub-tiers, and the discount typically applies only to patient responsibility after insurance. Our extraction uses verbatim quotes so the patient (and the rep on the phone) can check them. Bright Data fetching is a convenience, and HCAI's repository is a stable cross-check.

## 3. Calling on a patient's behalf

### HIPAA

- **45 CFR 164.510(b)** lets a covered entity disclose information to a person the patient identifies as involved in their *payment* for care, limited to what is relevant to that role. If the patient is present, the entity may disclose if the patient agrees, is given a chance to object, or the entity can reasonably infer no objection ([eCFR 164.510, via Cornell LII](https://www.law.cornell.edu/cfr/text/45/164.510)). The text does not require a writing, so a verbal agreement during the call can suffice; whether a particular billing office accepts it is a matter of its own policy.
- **How billing offices actually verify:** they typically ask for patient name, date of birth, account number and address or phone. The AI cannot invent these; it gets them from the bill and the patient. Some offices will refuse any third party without paperwork. I did not find a published industry standard, so this is practice-based, not cited.
- **Warm or three-way handoff:** the patient joins (or is on the line and says "yes, I authorize Remy to discuss my bill") at the start of the call. That satisfies 164.510(b) agreement on the record and removes the identity problem. This is the demo-safe and legally cleanest design. Our "approval gate" screen already captures the patient's consent in writing in our audit log, which supports but does not replace the hospital's own check.
- **Scalable path: written authorization, 45 CFR 164.508** (or the hospital's own "authorized representative" form). Collected once during onboarding, uploaded or faxed to billing. This is the roadmap item.
- **Remy's own HIPAA status.** We act for the patient, not for the hospital, so we are probably not the hospital's business associate and a BAA with the hospital does not apply. Our existing Q&A says Remy "would be a business associate under a BAA", which is probably the wrong framing; get counsel on whether any covered entity or employer-plan relationship would change that (the B2B model could). Consumer health data we hold is likely governed by FTC rules (Health Breach Notification Rule) and California law (CMIA, CCPA) instead; I did not research those in depth.

### AI disclosure and telephone law

- **California SB 1001 (Bus. & Prof. Code 17941)** is a *bot* disclosure law, but it covers **online** communications (public-facing websites, web apps and social platforms) and concerns bots trying to influence a purchase or vote ([Assembly Privacy Committee analysis](https://apcp.assembly.ca.gov/media/888), [Perkins Coie](https://perkinscoie.com/insights/blog/i-am-robot-californias-new-law-requires-disclosure-use-bots)). **It does not appear to govern a voice phone call to a billing office.** Our docs currently cite it as requiring phone disclosure; they should not (see section 6). We should still disclose on every call, as a best practice and because other rules may apply.
- **California AB 2905 (2024, Pub. Util. Code 2874)** deals with automatic dialing-announcing devices and artificial-voice disclosure for prerecorded messages. A compliance guide says it was signed Sept. 20, 2024 and effective Jan. 1, 2025 ([committee analysis](https://apcp.assembly.ca.gov/system/files/2024-04/ab-2905-low-apcp-analysis.pdf)); I could not confirm the enacted text or scope. It targets robocall-style prerecorded messages, not an interactive agent calling one business, but flag it for counsel.
- **FCC Declaratory Ruling, Feb. 8, 2024:** AI-generated voices are "artificial" under the TCPA, so calls using them need prior consent where the TCPA applies ([FCC announcement](https://www.fcc.gov/document/fcc-makes-ai-generated-voices-robocalls-illegal), [Orrick](https://www.orrick.com/insights/2024/02/Did-the-FCC-Really-Ban-AI-Generated-Voice-Calls-No-But-Restrictions-Apply)). The consent regime protects *called consumers* on cell phones and residential lines. Our calls go to a hospital's business billing line to speak with staff, so the ruling is likely not triggered, but it is a reason not to let the system place AI-voice calls to *patients or individuals*. If a follow-up call to a patient is ever added, get express consent first.
- **Call recording:** California is an all-party-consent state (Penal Code 632 and 637.2; I did not open the statute text). Because our Cartesia calls are transcribed and may be recorded, **the opening of every call must say the call is automated, recorded and transcribed, and the call should end if the rep objects**, and the disclosure must be in the prompt, not left to the model's discretion. Recording rules of the hospital's state also apply when calling out of state.
- **Service terms:** Twilio and Cartesia acceptable-use policies on AI calls and recording need a read. Not researched.

## 4. What breaks, and how we handle it

| Failure | Mitigation |
|---|---|
| Bank does not support the aggregator we pick | Statement upload fallback |
| Income from deposits wrong (gig, joint household) | Show the calculation; patient corrects; hospital still requires its own documents |
| Billing refuses an AI or third party | Warm handoff with patient on line; written authorization; or generate the letter and application instead |
| Hospital has no usable online policy | Cross-check HCAI repository (California); fall back to calling billing and asking for it |
| Hospital is for-profit or outside California | 501(r) does not apply; many have voluntary policies. Say "not all hospitals qualify" |
| Voice agent promises or agrees to something | Prompt forbids it; outcomes extracted after the call; every consequential step needs approval |

## 5. Price transparency as a future data source

CMS requires hospitals to post machine-readable files of standard charges. The CY2026 OPPS final rule (CMS-1834-FC, Nov. 21, 2025) moved toward actual prices instead of estimates, added median and percentile allowed amounts, and requires a senior-official attestation; enforcement of the 2026 requirements was reported to start April 1, 2026 ([CMS fact sheet](https://www.cms.gov/newsroom/fact-sheets/cy-2026-opps-ambulatory-surgical-center-final-rule-hospital-price-transparency-policy-changes), [HFMA](https://www.hfma.org/price-transparency/cmss-latest-transparency-rule-aims-to-make-price-estimates-more-exact/); the April date is from a secondary source and I did not check it against the Federal Register). Use: compare billed charges to the hospital's own published rates for the same billing codes to flag overcharges. Limits: files are huge, inconsistent and often wrong, so this is a roadmap item, not a demo feature.

## 6. Contradictions with current repo claims

| Claim | Where | Problem | Suggested fix |
|---|---|---|---|
| Demo outcome | README, devpost, `web/pitch.html` | Numbers used to differ across materials | Aligned: $8,700 → $1,100 → $600 on a 6 × $100 interest-free plan, collections paused |
| "Nonprofit hospitals must publish" plus "California requires discounts up to 400% FPL" | `web/pitch.html` | Basically correct; California law applies to licensed hospitals generally, 501(r) to nonprofits | Optional nuance |

## 7. Skeptical judge Q&A

**"A past project assumed bank data nobody would give. Why is yours different?"**
We need only transaction payee, amount, date and payroll deposits, which consumer-permissioned aggregators already supply for apps like budgeting and lending tools, and Capital One has had API-based data-sharing agreements with Plaid and Finicity. We don't rely on the CFPB 1033 rule, which is currently stayed. Nessie is a sandbox for the demo only, and statement upload is our fallback.

**"Does 1033 apply to you?"**
Not today. The compliance dates are stayed (Oct. 29, 2025) and the CFPB is rewriting the rule. If it comes into force, it helps us; we don't need it.

**"Why would a hospital talk to your AI?"**
It might not, and we plan for that: the patient joins the call or signs an authorization, and the hospital's own policy gives the patient the right to the discount. Where the hospital refuses, we output a letter and pre-filled application.

**"Is the AI call legal?"**
We call a business line, disclose automation and recording up front, and need the patient's agreement under HIPAA 164.510(b). California's all-party recording rule is why the disclosure is mandatory. SB 1001 is an online-bot law, not a phone law. The FCC's AI-voice ruling targets calls to consumers. Counsel should review before launch.

**"Do hospitals really have to give discounts?"**
Nonprofit hospitals need a FAP to keep tax exemption (501(r)); in California, all licensed hospitals must offer charity care or discounts to eligible patients up to 400% FPL (AB 1020, effective 2024).

**"What if the income estimate is wrong?"**
We show how we computed it, ask the patient to confirm, and the hospital will verify with its own documents. A wrong estimate costs a failed application, not a wrong payment.

**"What happens with real PHI?"**
Out of scope for the hackathon (synthetic data only). Production plan: patient consent and authorization, encryption, minimum necessary, counsel on HIPAA and state health-privacy law.

**"Isn't Dollar For doing this for free?"**
Yes, for the charity-care application step. We add bank evidence, a phone call and follow-up. We could also partner with them.

## Unverified or open items

Capital One's current aggregator terms and fees; the statute text for AB 1020 tier numbers, collections and refund rules; AB 2905's enacted scope; Penal Code 632 text; 26 CFR 1.501(r) text; the live HCAI lookup; Twilio and Cartesia terms for AI calls and recording.
