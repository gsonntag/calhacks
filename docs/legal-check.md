# Legal check (2026-10-25)

A web check of the legal claims in `docs/PLAN.md` ("Later" list) and `docs/ideas-backlog.md`. Research
only, not legal advice. Statute text came from california.public.law (a mirror of the official code;
leginfo.legislature.ca.gov blocked the fetcher), plus IRS, CMS, eCFR mirrors and DHCS. Before anything
goes on screen, re-check the quoted wording on leginfo.

## Summary

| # | Claim | Verdict | One-line rule |
|---|---|---|---|
| 1a | 501(r) applies to UCSF Medical Center | **Confirmed (probably)** | UC holds 501(c)(3) recognition, and the IRS says 501(r) applies to government hospitals that hold it. UCSF's own policy cites 501(r). |
| 1b | 501(r) applies to SF General (ZSFG) | **Couldn't confirm; probably not** | ZSFG is run by the City and County of SF. No 501(c)(3) recognition found. California's Fair Pricing Act applies either way. |
| 2 | NCCI edits apply to facility claims | **Partly right** | Medicare applies NCCI to *outpatient* hospital claims (through the I/OCE), not inpatient ones. Medicaid must use NCCI. Commercial insurers don't have to. |
| 3 | 180-day appeal window | **Right for insurer appeals only** | Health plans must give at least 180 days after a denial to file an internal appeal. Hospitals have no 180-day appeal window. |
| 4 | California itemized-bill rule | **Partly right** | No California statute found that gives a general right to an itemized bill. The notice before collections must tell the patient how to get one (HSC 127425(e)(3)). HIPAA gives a right to billing records. |
| 5a | No credit reporting | **Confirmed, and stronger than stated** | Since 2025 the ban is permanent, not only for the first 180 days (HSC 127425(f)(1); Civ. Code 1785.27). |
| 5b | No lawsuit before day 180 | **Confirmed** | No lawsuit for nonpayment until 180 days after *initial billing* (HSC 127425(f)(2)). |
| 5c | Application must come with collection notices | **Partly right** | The application must be in the one notice sent *before* the account goes to collections or is sold (HSC 127425(e)(5)). The law doesn't require it on every later collection letter. |
| 5d | "AB 1020, effective 2024" | **Wrong date** | AB 1020 is Chapter 473 of 2021, in force since Jan 1, 2022. HCAI took over enforcement on Jan 1, 2024. |
| 6 | Marcus eligible for Medi-Cal | **Probably not eligible** | About 148% FPL (2026 tables), above the 138% adult limit, even counting the 5% disregard. Any children in the household probably are eligible. |

## 1. IRS 501(r) at UCSF and SF General

**Claim:** 501(r) covers nonprofits. Unsure for UCSF (UC) and SF General (city).

**UCSF: confirmed (probably).**
- The IRS says 501(r) applies to every hospital organization that is (or wants to be) a 501(c)(3), "including those that are government hospital organizations". Government hospitals that don't file a Form 990 "still must meet all section 501(r) requirements" that don't involve the 990. [IRS: Section 501(r) reporting](https://www.irs.gov/charities-non-profits/section-501r-reporting)
- UC says it is "an organization described in Section 501(c)(3)" and also a Section 115 government unit, so it files no Form 990. [UCOP tax status](https://ucop.edu/research-policy-analysis-coordination/resources-tools/about-uc/tax-status-of-the-regents-of-the-university-of-california.html). UC's Regents reports say the medical centers file no Schedule H because they are government hospitals. [Regents item, June 2023](https://regents.universityofcalifornia.edu/regmeet/june23/h5.pdf)
- UCSF's financial assistance policy says it is "intended to comply with IRS Regulations". It also makes collection agencies promise not to take any "Extraordinary Collection Activity under IRS Code section 501(r)" (`policydb/raw/ucsf-health/pdf-db570fe1a4ce.txt`).
- Uncertain: no IRS document found that says UCSF's own status in so many words. The conclusion rests on UC's 501(c)(3) recognition plus the IRS rule.

**SF General: couldn't confirm; probably not.**
- ZSFG is a licensed acute-care hospital owned and run by the City and County of SF (SFDPH). No source showed 501(c)(3) recognition for it. A city department normally has none, and then 501(r) doesn't apply. Settle it with [IRS Tax Exempt Organization Search](https://apps.irs.gov/app/eos/) if it matters.

**What applies to all four hospitals:** California's Hospital Fair Pricing Act, [HSC 127400 et seq.](https://california.public.law/codes/health_and_safety_code_section_127405). ZSFG files its charity policy with [HCAI](https://hcai.ca.gov/affordability/hospital-fair-billing-program/hospital-fair-pricing-policy-lookup/), and UCSF's policy says it complies with 127400 et seq.

**Careful wording:**
- Lead with California law, not 501(r). California is stricter on every point that matters here:
  - credit reporting is banned outright;
  - there's no lawsuit before 180 days (501(r) only requires 120 days without "extraordinary collection actions");
  - there are no deadlines for applying ([HSC 127405(e)(3)](https://california.public.law/codes/health_and_safety_code_section_127405)), while 501(r) has a 240-day application window.
- 501(r) rules, for reference: no extraordinary collection actions for 120 days after the first post-discharge statement; written notice at least 30 days before one, sent with a plain-language summary of the policy. [IRS 501(r)(6)](https://www.irs.gov/charities-non-profits/billing-and-collections-section-501r6)
- UCSF's policy asks patients to apply "within 240 days" as a "best efforts" request. California law doesn't allow the hospital to deny an application for being late. If a rep cites 240 days, the caller can answer with HSC 127405(e)(3).
- `docs/judge-qa.md` and `docs/devpost.md` say "nonprofit hospitals must publish these policies (IRS 501(r))". That's true. For a California audience, add "and California law requires every hospital, public ones included, to post them (HSC 127410(c))".

## 2. NCCI edits on facility claims

**Claim:** NCCI pair edits apply to facility claims (marked unsure).

**Verdict: partly right.**
- Medicare: NCCI pair edits are used for "practitioner services, outpatient hospital services, outpatient therapy services". They "are not applied to facility claims for inpatient services". Outpatient hospital claims get them through the Outpatient Code Editor (now the I/OCE). Some of those edits "differ from the comparable edits" in the practitioner tables. [CMS NCCI Policy Manual 2026, Introduction](https://www.cms.gov/files/document/0-introduction-ncci-medicare-policy-manual-2026-final.pdf)
- Medicaid: ACA section 6507 requires state Medicaid programs, including Medi-Cal, to use NCCI methods. They use separate Medicaid edit files (same manual).
- Commercial insurers: no federal or California rule found that makes them use NCCI. Many license NCCI-based edits anyway, but it's a contract matter. (Some other states require standard edits; no such California law was found. I didn't prove there isn't one.)

**Careful wording:**
- Marcus and Rosa are ER outpatient visits (UB-04 type 13x), so the right table is the **OPPS/I/OCE** version, not the practitioner table.
- For a commercial or self-pay bill, say "Medicare's coding rules don't allow these two codes together", not "this is illegal" or "your insurer must deny this".
- Under OPPS, many items (e.g. 36415 blood draw, some drugs) are *packaged* rather than edited. A packaged line isn't an error on a commercial bill.

## 3. The "180-day appeal window"

**Claim:** "Denied lines; 180-day appeal clock (unsure)."

**Verdict: right, but only for appeals to a health insurer.**
- Group health plans must "provide claimants at least 180 days following receipt of a notification of an adverse benefit determination within which to appeal". [29 CFR 2560.503-1(h)(3)(i)](https://www.law.cornell.edu/cfr/text/29/2560.503-1). ACA rules extend this to non-grandfathered group and individual plans ([45 CFR 147.136](https://www.law.cornell.edu/cfr/text/45/147.136)).
- California state-regulated plans: DMHC plans also allow 180 days to file a grievance ([28 CCR 1300.68](https://www.dhcs.ca.gov/formsandpubs/Documents/MMCDAPLsandPolicyLetters/PL2009/PL09-006.PDF), as cited by DHCS). After the plan's decision there are 6 months to ask the state for an Independent Medical Review ([HSC 1374.30](https://california.public.law/codes/health_and_safety_code_section_1374.30); [Ins. Code 10169](https://california.public.law/codes/insurance_code_section_10169)).
- Other deadlines: Medicare is 120 days to ask for a redetermination; Medi-Cal managed care plans allow 60 days to appeal. So 180 days is not universal.

**Careful wording:**
- The clock starts when the patient *receives the denial* (EOB or denial letter), not on the service date or the hospital bill date.
- Say "most job-based and Covered California plans give you at least 180 days".
- A hospital billing dispute has no legal deadline of this kind. Don't mix it up with the 180-day collections rule in section 5.

## 4. California itemized-bill rule

**Claim:** California has a rule giving patients an itemized bill (backlog: "no statute confirmed").

**Verdict: partly right. There is no general right in a statute, but there are two hooks.**
- [HSC 127425(e)(3)](https://california.public.law/codes/health_and_safety_code_section_127425): before sending a bill to collections or selling the debt, the hospital's notice must include "a statement informing the patient how to obtain an itemized hospital bill from the hospital". The law takes for granted that a patient can get one, but it doesn't say how fast or at what cost.
- HIPAA right of access: a provider's "billing records about individuals" are part of the designated record set ([45 CFR 164.501](https://www.law.cornell.edu/cfr/text/45/164.501)). The patient can ask for them under [45 CFR 164.524](https://www.law.cornell.edu/cfr/text/45/164.524): the hospital has 30 days and may charge only a reasonable cost-based fee.
- Medicare patients have a separate federal right to an itemized statement. That doesn't help commercial patients.
- One blog cites "HSC 1339.585, 10 business days". That's wrong: 1339.585 is about *written price estimates for uninsured patients* ([text](https://california.public.law/codes/health_and_safety_code_section_1339.585)). Don't use it.

**Careful wording:** "You can ask for an itemized bill. Hospitals have to tell you how to get one before sending a bill to collections, and federal privacy law (HIPAA) lets you get your billing records." Don't say "California law requires an itemized bill within X days."

## 5. California hospital collections (HSC 127425)

Source: [HSC 127425](https://california.public.law/codes/health_and_safety_code_section_127425) (text dated Jan 1, 2025), [Civ. Code 1785.27](https://law.justia.com/codes/california/code-civ/division-3/part-4/title-1-6/chapter-3-5/section-1785-27), [HCAI laws and regulations](https://hcai.ca.gov/affordability/hospital-fair-billing-program/laws-and-regulations/).

**a. Credit reporting: confirmed, and stronger than the backlog says.**
- Under 127425(f)(1), a hospital, anyone it assigns the debt to, or any owner of the debt "shall not ... report adverse information to a consumer credit reporting agency". Since SB 1061 (Chapter 520 of 2024, effective Jan 1, 2025), this has no time limit.
- Civ. Code 1785.27 bans reporting medical debt to credit bureaus by anyone. A debt knowingly reported in violation is void. There's a narrow exception when the insurer paid the patient directly and the patient kept the money.
- Since July 1, 2025, contracts that create medical debt must include a notice saying the debt can't be reported ([Reed Smith summary](https://www.reedsmith.com/our-insights/blogs/health-industry-washington-watch/102lu32/new-california-medical-debt-disclosure-law-now-in-effectwhat-providers-need-to-k/)).
- **Fix the wording:** don't say "no credit reporting before 180 days". Say "California hospitals and their collectors can't report medical debt to credit bureaus at all".

**b. Lawsuit wait: confirmed.**
- 127425(f)(2): no "civil action against the patient for nonpayment before 180 days after initial billing". The clock starts at the *first bill*, not the service date or the past-due notice.
- Debt sale: also not allowed until the patient is found ineligible or hasn't responded to bills or offers of assistance for 180 days (127425(a)(1)).
- Related rules:
  - eligible patients on a payment plan pay no interest;
  - a plan can be declared inoperative only after missed payments over 90 days, a phone call and a written notice (127425(i));
  - there's no lawsuit while the plan is still in effect;
  - no wage garnishment without a court order;
  - no liens on real property (127425(h)).

**c. Application with notices: partly right.**
- 127425(e): "Before assigning a bill to collections, or selling patient debt ... a hospital shall send a patient a notice" that includes:
  - the dates of service;
  - who the debt is going to;
  - how to get an itemized bill;
  - the patient's coverage;
  - "an application for the hospital's charity care and financial assistance";
  - the dates of earlier notices and applications.
- That's one notice sent *before* the hand-off, not a rule for every collection letter.
- Separately, 127410 requires a written notice of the discount and charity policies at the time of service or discharge, mailed within 72 hours if the patient left without it. That notice doesn't have to include the application.
- **Wording:** "Before a California hospital sends your bill to collections, it must send you an application for financial assistance." Rosa's day-93 "final notice" is fine to flag: no lawsuit yet, and if it threatens collections without the application, that's a problem to raise.

**d. Dates.**
- AB 1020 (Chapter 473, Statutes of 2021) made these rules: it raised the wait from 150 to 180 days and added the notice, the debt-sale limits and the record rules. It has been in force since **Jan 1, 2022**. Jan 1, 2024 is when HCAI's Hospital Fair Billing Program began enforcing it (regulations at 22 CCR 96051 et seq.).
- AB 2297 and SB 1061 took effect Jan 1, 2025.
- AB 1312 (Chapter 450 of 2025) adds required screening for assistance and presumptive eligibility from **July 1, 2027**.

## 6. Marcus and Medi-Cal

**Case data** (`billback/scenarios.py` "marcus-real"; `demo/scenarios/marcus-real/`):
- Marcus Lee, born 1984-03-17 (age 42), household of 3, Berkeley.
- Pay: $1,560 every two weeks from Alameda County Transit, about $40,560 a year or $3,380 a month.
- Insurance: a high-deductible employer-type plan ("Tidewater Arc Health HDHP", fictional). $5,000 of deductible left, then 30% coinsurance.
- UCSF ER visit on 2026-07-19.
- The story says "about 152% of the poverty line". That uses the 2025 FPL. On the 2026 FPL it's about 148%.

**Verdict: probably not eligible.**
- The 2026 FPL for 3 people is $27,320, so 138% is $37,702 a year or $3,143 a month (DHCS letter 26-01, as quoted by [coverhealthca](https://www.coverhealthca.com/learn/medi-cal-income-limits); [DHCS chart](https://www.dhcs.ca.gov/Medi-Cal/Pages/eligibility-chart.aspx)).
- Medi-Cal looks at current monthly income. With the standard 5% MAGI disregard the effective limit is 143%, about $3,257 a month. Marcus is at about $3,380, roughly $120 a month over.
- His $1,560 is a *bank deposit*, which is take-home pay. MAGI is gross pay minus pre-tax deductions (401(k), pre-tax premiums), so his MAGI is probably *higher* than $1,560 per check. That puts him further over.
- Other routes:
  - **Children:** if the other two household members include children under 19, they're probably eligible (Medi-Cal covers children up to 266% FPL). That doesn't pay Marcus's bill.
  - **Private insurance:** having it doesn't bar Medi-Cal. It just makes Medi-Cal the secondary payer.
  - **Retroactive coverage:** applying in October 2026 can still reach back 3 months, which covers the July visit. From Jan 1, 2027, H.R. 1 cuts this to 1 month for expansion adults ([DHCS ACWDL 26-07](https://www.dhcs.ca.gov/es/services/medi-cal/eligibility/letters/Documents/ACWDL-26-07.pdf)). Moot unless he qualifies.
- 2025–2026 changes, for the record ([DHCS H.R. 1 tracking](https://www.dhcs.ca.gov/federal-impacts/Pages/Medi-Cal-Eligibility.aspx)):
  - **Work requirements:** 80 hours a month for expansion adults from Jan 1, 2027. Marcus works, so he'd meet them.
  - **Renewals:** every 6 months for adults from early 2027.
  - **Immigrants:** federal limits on which immigrants qualify started Oct 1, 2026. California's state-budget freeze on new full-scope enrollment for undocumented adults was not verified here. Marcus's immigration status isn't in the data, so this isn't modeled.
- **Uncertain:**
  - who the other two household members are, and their income (a working spouse would push him further over);
  - his gross pay and pre-tax deductions;
  - any other income;
  - whether his income in the month he applies differs (Medi-Cal looks at the month).

**Careful wording:** don't tell Marcus he qualifies for Medi-Cal. Say "You're probably just over Medi-Cal's adult limit; if you have children, they may qualify." The UCSF free-care result doesn't depend on Medi-Cal. Under UCSF's policy, Medi-Cal screening is a step the hospital may require, not a reason to deny assistance ([HSC 127405(b)(2)](https://california.public.law/codes/health_and_safety_code_section_127405)).

## Other legal statements in code and docs (brief)

- `docs/ideas-backlog.md` says the HSC 127405(d) cap applies to "uninsured patients or those at or under 400% FPL". More precisely, it limits what the hospital may *expect to collect* from patients who qualify under its discount policy (uninsured or high medical costs) **and** are at or under 400% FPL. It's the greater of Medicare or Medi-Cal. The note in `billback/scenarios.py` ("uninsured ... at or under 400% FPL") is fine.
- `docs/ideas-backlog.md` #1 says "CA bans credit reporting and lawsuits before 180 days". Split it: credit reporting is banned outright; lawsuits wait 180 days from the first bill.
- `docs/ideas-backlog.md` #16, "shrinking from 2027": correct (1 month for expansion adults, 2 months for others, from Jan 1, 2027).
- `docs/ideas-backlog.md` #15 (good-faith estimate: $400 over, 120 days) and #19 (CFPB rule vacated July 2025; bureaus drop debts under $500): consistent with what I know. Not re-fetched.
- `docs/ideas-backlog.md` #20: Rumsey is about when the clock starts on a hospital debt. California's limit is 4 years for written contracts (CCP 337) but 2 years for oral ones (CCP 339). Say "often 4 years".
- `billback/price_sources.py` "public by law" is correct (45 CFR part 180).
- UCSF's policy (`policydb/raw/ucsf-health/pdf-db570fe1a4ce.txt`) says emergency physicians must give discounts up to 350% FPL (HSC 127450 et seq.). That's not something BillBack claims, but it matters for the roadmap item on separate ER physician bills.
- `billback/agents/caller.py` forbids inventing laws. Good. No code path states a deadline or statute to the rep, so no code is wrong today. The risk is in future copy.

## Sources not opened directly

The government-hospital line from the IRS page was fetched. The UC Regents Schedule H wording, the DMHC 180-day regulation (seen through a 2009 DHCS letter) and the 2026 Medi-Cal dollar figures (secondary sites quoting DHCS letter 26-01) were not read in primary form. Check those three before quoting them.
