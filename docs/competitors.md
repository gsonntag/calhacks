# BillBack: competitive landscape

Researched October 2026. Facts come from web searches of vendor pages, regulators and press; vendor claims are labelled as such.
"Unverified" means I could not confirm it. Pricing and traction change quickly, so re-check before quoting on stage.

Framing from our product mentor: the general idea is "when you get billed, don't get scammed, and get the most out of it." Healthcare is the sharpest version of that problem, but the category is wider.

## 1. Healthcare

| Company | What it does | How it works | Pricing | Traction (as found) | What we can steal |
|---|---|---|---|---|---|
| [Dollar For](https://dollarfor.org) | Helps patients apply for hospital charity care and get bills reduced or erased. | Software plus human advocates; guides the patient through the hospital's own financial-assistance policy. Does not pay bills itself. | Free to patients; donor-funded nonprofit. | Reported ~$55M in bills erased in 2025, and an ambitious $1B/yr goal ([KFF Health News podcast](https://kffhealthnews.org/news/podcast/an-arm-and-a-leg-podcast-charity-care-nonprofit-dollar-for-medical-bills/), [Cause Artist profile](https://www.causeartist.com/companies/dollar-for)). It reported losing two large donors in early 2026. | Our closest idea overlap (the hospital's own policy). Steal their state-by-state "does this hospital have a policy, what are the cutoffs" database framing and their plain-language eligibility explainer. They do not read bank data or phone anyone (as far as I could verify). |
| [Goodbill](https://www.goodbill.com/faq) | Negotiates hospital bills for the patient. | Human negotiators plus software. | 20% of savings, capped at $1,000, nothing if no savings ([Goodbill FAQ](https://help.goodbill.com/en/articles/254209)). | Claims 20-60% typical savings, up to 95% (vendor claim). Reportedly does not take bills already in collections (third-party comparison, so treat with caution). | Steal the "pay only on savings, capped" pricing clarity. We can accept bills in collections, since the hold request is part of our call. |
| [Resolve](https://www.resolvemedicalbills.com/about) | Medical-bill advocacy, sold largely through employers and benefits partners. | Human advocates analyse bills and negotiate. | Performance-based per third-party descriptions; exact terms unverified. | $3.3M seed in 2022 ([BusinessWire](https://www.businesswire.com/news/home/20220718005136/en/Resolve-Raises-3.3M-in-Seed-Funding)); claims 60-65% average savings and $40M+ saved (vendor claims). A partner page says it stopped accepting new patients from some partners in Sept 2024. | Steal the B2B distribution (employers, ICHRA and wellness partners). It validates our B2B line. |
| CoPatient | Medical-bill error review and advocacy. | Free estimate, then human advocates. | About 35% of savings per 2014-15 sources ([406 Ventures](https://www.406ventures.com/news/1721-copatient-saves-consumers-over-one-million-dollars-on-their-medical-bills)). Current status and pricing unverified. | Claims errors in 80%+ of bills reviewed (old vendor claim). | The free "estimated savings report" as a conversion hook. |
| [Sheer Health](https://stateline.org/2025/11/20/patients-deploy-bots-to-battle-health-insurers-that-deny-care/) | App to link insurance, upload bills and ask coverage questions. | AI plus humans. | Free basic help; paid help for denials and out-of-network reimbursement (per Stateline). | About three years old as of Nov 2025. Little else verified. | Conversational "what does this line mean" explainer next to the audit. |
| Medical Cost Advocate, Remedy | Listed in the brief. | Not verified. | Not verified. | I could not find reliable current information on either, so they are not evaluated here. | n/a |
| [Counterforce Health](https://en.wikipedia.org/wiki/Counterforce_Health) | AI-drafted appeals of insurance claim denials. | Software: uploads denial letter and records, drafts an appeal grounded in policy text and literature. | Free nonprofit tool. | Co-founder says about 20,000 people helped (CBS17 report, date unclear). A ~70% reversal figure is third-party and not confirmed. | Evidence-grounded appeal letters with citations. Our "follow-up letter" feature can reuse this pattern. Adjacent to us (denials vs billing). |
| Avelis Health, Granted Health (formerly Medbill AI), Medcostiq | 2023-2025 AI medical-bill startups. | Avelis: AI agents review bills for errors and negotiate (YC-backed, $500K pre-seed in 2025, per [People of Color in Tech](https://peopleofcolorintech.com/articles/y-combinator-invests-500k-in-ai-startup-helping-americans-save-money-on-medical-bills/)). Granted: bill negotiation and claims handling. Medcostiq: thin profile ([StartupHub](https://www.startuphub.ai/startups/medcostiq.md)). | Mostly unverified. | Early. I found no confirmed product that places phone calls to hospital billing, but the search was shallow. | These are our real direct competitors. Watch Avelis closely. |
| [Cedar](https://marketplace.aviahealth.com/compare/81150/25345) and other patient-payment platforms | Sell to hospitals: friendlier statements, payment plans, pay-by-text. | Software sold to providers. | Paid by the provider. | Vendor claim: 55+ health systems, ~30% collection-rate lift. | Their incentive is collecting more, not charging less. That conflict is our wedge: we work for the patient. Steal the pay-by-text/one-tap plan UX. |
| Hospital price-transparency tools | Use the CMS-mandated machine-readable price files. | Mostly consumer shopping or payer-negotiation analytics. | Varies. | CMS strengthened the rules for 2026 (see feasibility doc). | A future "was this charge reasonable" check. See feasibility doc section 5. |
| General chatbots (ChatGPT, Claude) | Patients paste bills in and write their own letters. | DIY. | Subscription. | A widely shared case cut a $195,000 bill to $37,000 using chatbots, via [Gigazine](https://gigazine.net/gsc_news/en/20251029-using-ai-negotiate-hospital-bill-down). | This is the real "do nothing" competitor for tech-savvy users. We beat it by doing the call and bringing bank evidence. |

## 2. Other spaces

| Company | What it does | How it works | Pricing | Traction / notes | What we can steal |
|---|---|---|---|---|---|
| [Rocket Money](https://help.rocketmoney.com/en/articles/9744474-bill-negotiation-charge) (ex-Truebill) | Subscription tracking plus bill negotiation (cable, phone, internet). | Links bank accounts, finds recurring bills, human agents negotiate. | 35-60% of first-year savings, user-chosen, charged after a 48-hour window ([help center](https://help.rocketmoney.com/en/articles/9744474-bill-negotiation-charge)). | Mass-market leader. Press in July 2026 flagged the data-access privacy trade-off ([Atlanta News First](https://www.atlantanewsfirst.com/2026/07/10/online-bill-negotiators-promise-savings-require-personal-data-access/)). | Bank-linked detection is proven, but they use it to find recurring bills, not to prove a payment. Steal the "savings counter" and "we only charge if it works" trust pattern. |
| Billshark | Human bill negotiation (telecom, utilities). | Agents call providers; needs bill copies and account details. | 40% of savings for up to two years, no fee on failure ([Billshark](https://billshark.com/blogs/bill-negotiation-fees-are-they-worth-it)); 35% via a Consumer Reports offer. | Same privacy criticism as above. | Fee-on-success model. |
| Trim | Found subscriptions through linked accounts and cancelled them. | Software plus humans. | Fee on savings. | Acquired by OneMain Financial per a PYMNTS item (2021). Current status unverified. | Little; superseded by Rocket Money. |
| [DoNotPay](https://www.ftc.gov/node/87474) | "Robot lawyer": disputes, cancellations, fee refunds. | Chatbot plus templates. | Subscription. | FTC final order (Jan 2025): $193,000 relief and a ban on unsupported "AI lawyer" claims. | A cautionary tale. Never overclaim. Our deterministic math, citations and approval gate are the antidote. Put this on a slide if asked about trust. |
| [Pine AI](https://www.19pine.ai/why-pine) | Phone-calling agent that waits on hold, negotiates bills, cancels subscriptions. | AI voice agent calls companies for you. | Free to start, pay on success; exact percentage unverified. | Vendor claims: ~$300 saved per user per year, 93% success (unverified). | The most direct technical analogue to our caller. They are generic; they have no hospital-policy knowledge and no bank evidence. Steal their "hold-wait plus handoff" UX (notify when a human answers). |
| Big-tech call agents (for example Google's call-on-your-behalf features) | Place calls to businesses. | AI voice. | Bundled. | I could not verify current 2026 products in this research. | Validates that businesses accept AI callers. Unverified. |
| [AirHelp](https://www.airhelp.com/en-int/our-fees) | Flight-delay compensation claims. | Software plus lawyers. | 35% of compensation (+15% if legal action), or a subscription (~$27/yr US page). | Established brand. | Template for a consumer-rights claim engine: eligibility check, evidence pack, fee on win. Their "check your eligibility in 30 seconds" hook is what our first screen should feel like. |
| Subscription and bank-fee dispute tools | Surface unwanted charges and file disputes. | Linked accounts plus templates. | Mixed. | Not researched individually; marked as unverified. | Duplicate-charge detection is in our auditor already; make it a visible "possible scam" card. |

## 3. Positioning (what each does, as best I could verify)

Legend: Y = yes, P = partial or varies, N = no / not found, ? = unverified.

| Capability | BillBack | Dollar For | Goodbill / Resolve (human) | Rocket Money / Billshark | Pine AI | DIY with a chatbot |
|---|---|---|---|---|---|---|
| End-to-end autonomy (find, argue, follow up) | P (call is demo-grade; follow-ups are planned) | N (guides patient) | P (humans do the work) | P | P | N |
| Uses bank data to prove payments | Y | N | N | P (finds bills, not proof of payment) | N | N |
| Uses the hospital's own published policy | Y | Y | P | N | N | P |
| Actually phones billing | Y | N | Y (humans) | Y (humans) | Y (AI) | N |
| Approval gate for every consequential action | Y | n/a | P | P (48h window) | ? | n/a |
| Price to patient | Free below poverty thresholds; success fee above (proposed, not validated) | Free | 20% capped $1,000 (Goodbill) | 35-60% / 40% | success fee | subscription |

## 4. Differentiation

- **Evidence, not just advocacy.** We prove payments the hospital missed using the patient's own bank records, so the call is "here is transaction 4411 on June 3", not "please reconsider".
- **The hospital's own rules.** We cite its published financial-assistance policy (live-fetched), and compute eligibility from payroll deposits. The ask is a right the hospital already granted, not a favour.
- **Does the call.** Most AI bill tools explain bills. We place the call and get a case number, a corrected statement request and a collections hold.
- **Patient stays in control.** Tiered tool permissions, a phone approval screen and an append-only audit log, which is exactly what DoNotPay lacked.
- **Aligned incentives.** Patient-payment platforms are paid to collect; we are paid when the patient pays less.

## 5. Honest weaknesses and risks

- **Demo is synthetic.** Patient, bill, EOB and bank history are fake and "billing" is a teammate. Say so first.
- **Real bank data is hard to get at scale.** See [feasibility.md](feasibility.md). The Nessie sandbox is not a real open-banking feed.
- **Incumbents have humans and traction.** Dollar For erased ~$55M in 2025; Rocket Money has mass distribution. We have none.
- **Hospital phone trees and identity checks.** Billing offices verify identity and may refuse an AI caller. Our reliable path is a warm handoff with the patient on the line.
- **Charity care alone is a narrow wedge.** It applies to patients under roughly 400% FPL, and the strongest federal obligation covers nonprofit hospitals only.
- **Unit economics unproven.** Success fees on small savings are thin; B2B is plausible but untested.
- **Pine AI-style generic callers could add a "hospital" mode** quickly. Our moat is data plus policy plus compliance, not the voice.

## 6. Steal list (ranked by impact / effort)

| # | Idea (from) | Impact | Effort | < 2 h? |
|---|---|---|---|---|
| 1 | "Savings so far" counter and per-finding confidence card (Rocket Money) on the dashboard | High for judges | Low | Yes |
| 2 | 30-second eligibility checker as the landing screen: enter household size and income, see your FPL % and tier (AirHelp, Dollar For) | High | Low (finance.py already computes it) | Yes |
| 3 | "Possible scam" framing: label duplicate charges, missing payments and out-of-policy fees as red-flag cards (mentor's theme) | High | Low | Yes |
| 4 | Pre-filled financial-assistance application and dispute letter PDF with cited evidence (Counterforce, Dollar For) | High | Medium | Maybe (one template, ~2 h) |
| 5 | Fee-on-success pricing slide with a cap (Goodbill) | Medium | Very low | Yes (slide only) |
| 6 | Warm-handoff UX: notify patient's phone when a human answers and offer "join call" (Pine AI) | High (also the compliance answer) | Medium | No |
| 7 | Follow-up scheduler: automatic reminder or recall if the corrected bill has not arrived in N days (all human advocates) | Medium | Medium | Partly (a stub with a timer and an event) |
| 8 | Employer/benefits B2B pitch (Resolve) | Medium | None (pitch) | Yes |

## Sources not verified

Medical Cost Advocate and Remedy (not found), big-tech call-agent products, Trim's 2026 status, Pine AI fee, Resolve and CoPatient current pricing.
