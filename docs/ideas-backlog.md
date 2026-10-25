# Ideas backlog: check audit (2026-10-25)

A read-only design audit of the two money-checking agents, kept for when there's time to build more.
Decisions and the current plan are in `docs/PLAN.md`. Items marked "unsure" were not verified; check them
before putting anything on screen.

## Audit of the two-agent design

**Price check.**
- The price files are reachable with plain HTTPS (each hospital's `cms-hpt.txt`); Bright Data is not
  technically required to fetch them. Use it for discovery, not as a claimed necessity.
- CPMC: Sutter's index is UTF-16, with 3 campus files (Van Ness 16.9 MB CSV, CMS v3.0.0; Davies; Mission
  Bernal). UCSF: one 91 MB JSON on Box. SF General: 3.9 MB CSV. Chinese Hospital: 46 MB CSV. All have
  CPT/HCPCS columns, and every demo code is present.
- Rosa's original made-up prices sat below CPMC Van Ness's real gross charges (99284 $3,150 vs $5,214;
  74177 $3,890 vs $8,287; 85025 $190 vs $217; 96374 $610 vs $647; only 80053 was above: $420 vs $386
  outpatient; since 2026-10-25 the sample bill has 80053 at $386 and 99284 at $3,184, same $8,700 total).
  J2405's drug units are ambiguous. Sutter lists its discounted cash price equal to gross.
- For an insured patient the insurer's negotiated rate matters, not gross. The files have negotiated rates
  (e.g. 74177: Aetna $4,717, Blue Shield $6,302), but our insurer is fictional.
- Price data pays off most for uninsured patients or those at or under 400% FPL: California caps their
  charges at the greater of Medicare or Medi-Cal ([HSC 127405(d)](https://california.public.law/codes/health_and_safety_code_section_127405)).
- Duplicate lines are a check within the bill itself and need no price file.
- Gaps: the bill doesn't name the CPMC campus and intake doesn't extract the address/NPI; real summary
  bills often have no codes (patient must request an itemized bill); no California itemized-bill statute
  confirmed (unsure).

**Code issues found.**
- `finance._matches_payee`: any payee word over 3 characters matches ("Pacific", "Medical", "Center"), no
  date filter, no account-number check.
- High-medical-cost test uses only this claim; policies (UCSF explicitly) count the prior 12 months.
- Income counts only payroll deposits (regex). Bank assets correctly not counted (AB 2297).
- `insured=bool(eob)`: no insurance statement means "uninsured".
- FPL figures are 2025; 2026 is published ($15,960 for one person, [ASPE](https://aspe.hhs.gov/sites/default/files/documents/b1bfa16b20ae9b89d525bc35de7c1643/detailed-guidelines-2026.pdf)).
  Neither demo outcome changes.
- The plan's 4% income cap is our choice; Sutter's 10% is only the statute's fallback.
- "$7,600 insurance" merges $3,660 plan paid and $3,940 network discount.
- Hospitals without fixed-percent tiers (SF General) always get no discount.
- Sutter, UCSF and Chinese Hospital exclude separately billed physicians: Rosa would realistically get a
  second bill nobody checks.

**Wrong assumptions.**
- The 240-day 501(r) window is effectively moot in California: hospitals may not impose application time
  limits ([HSC 127405(e)(3)](https://california.public.law/codes/health_and_safety_code_section_127405)).
- 501(r) covers nonprofits (Sutter, likely Chinese Hospital); unsure for UCSF (UC) and SF General (city).
  California's Fair Pricing Act applies to all four.

## Candidate checks

| # | Check | Catches | Data | Agent | Build | Demo value | Synthetic data ok? | Source |
|---|---|---|---|---|---|---|---|---|
| 1 | Collections / credit-report guard | Final notices threatening collections: CA bans credit reporting and lawsuits before 180 days; collection notices must include an assistance application | bill notice + dates; statute | Bright Data (crawl statute text, verified quotes) | S | High (Rosa's notice is at day ~93) | Yes | [HSC 127425](https://california.public.law/codes/health_and_safety_code_section_127425), [Civ 1785.27](https://law.justia.com/codes/california/code-civ/division-3/part-4/title-1-6/chapter-3-5/section-1785-27), [HCAI](https://hcai.ca.gov/affordability/hospital-fair-billing-program/laws-and-regulations) |
| 2 | Split the insurance gap | Insurer paid but not credited vs network discount not applied | bill + EOB | Nessie | S | High | Yes | plan contract (unsure of a single cite) |
| 3 | 12-month medical out-of-pocket from bank | More patients qualify for the high-medical-cost write-off | Nessie purchases | Nessie | S | High | Needs seeded txns | [HSC 127405](https://california.public.law/codes/health_and_safety_code_section_127405) |
| 4 | Medicare-rate cap | Uninsured / ≤400% FPL charged above Medicare; unlocks Chinese Hospital tiers | CMS OPPS Addendum B | Bright Data | M | Medium-high | Yes | [HSC 127405(d)](https://california.public.law/codes/health_and_safety_code_section_127405), [CMS Addendum B](https://www.cms.gov/medicare/payment/prospective-payment-systems/hospital-outpatient/addendum-a-b-updates) |
| 5 | Price check vs hospital price file | Line above posted price; allowed amount ≠ posted negotiated rate | price file | Bright Data | M | Medium; only with prices rebuilt from the real file | After rebuild | [CMS price transparency](https://www.cms.gov/priorities/key-initiatives/hospital-price-transparency) |
| 6 | Duplicate / repeated bill lines | Same code twice, units above plausible max (MUE) | bill lines; CMS MUE | Nessie (exact dupes) / Bright Data (MUE) | S | High if seeded | Add a line | [CMS NCCI](https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits) |
| 7 | NCCI pair edits / unbundling | Code pairs that shouldn't be billed together (unsure for facility claims) | CMS NCCI PTP | Bright Data | M | Medium | Yes | [CMS NCCI](https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits) |
| 8 | Upcoding | Inflated ER level | medical records | new (clinical) | L | Low | No | skip |
| 9 | Interest / late fees in violation | Interest on a plan for eligible patients | bill lines; Nessie | Nessie + policy quote | S | Medium | Add a fee line | [HSC 127425(i)](https://california.public.law/codes/health_and_safety_code_section_127425) |
| 10 | Refund owed | Payments exceed the discounted share | Nessie | Nessie (in `settle`) | S | Medium | Marcus variant | Sutter policy (policydb) |
| 11 | Affordability with real living costs | Plan respects income minus essential expenses | Nessie rent/groceries | Nessie | S | Medium | Yes | [HSC 127405(c)](https://california.public.law/codes/health_and_safety_code_section_127405) |
| 12 | Bill ↔ EOB identity match | Claim/date/provider/amount mismatch; claim never filed | bill + EOB | Nessie | S | Low-medium | Yes | — |
| 13 | Denial + appeal deadline | Denied lines; 180-day appeal clock (unsure) | EOB denial codes | Nessie + follow-ups | M | Medium | Needs variant | [CMS appeals](https://www.cms.gov/files/document/internal-claims-appeals-external-review-process-overview.pdf) |
| 14 | No Surprises Act physician bill | Out-of-network ER doctor billing more than in-network cost sharing | EOB network flag; 2nd bill | needs a 2nd document | M | Medium-high | Needs 2nd bill | [CMS No Surprises](https://www.cms.gov/nosurprises) |
| 15 | Good-faith-estimate dispute | Uninsured bill ≥$400 over estimate; 120-day window | estimate | Bright Data | S | Low | No | [CMS](https://www.cms.gov/medical-bill-rights) |
| 16 | Retroactive Medi-Cal | Coverage back up to 3 months (shrinking from 2027) | income + household | Bright Data | S | Medium (unsure for Marcus) | Yes | [DHCS ACWDL 26-07](https://www.dhcs.ca.gov/es/services/medi-cal/eligibility/letters/Documents/ACWDL-26-07.pdf) |
| 17 | Timely filing | Provider billed insurer late, passed cost on | dates; contract | — | L | Low | No | contract-dependent (unsure) |
| 18 | 501(r) collection rules | Lawsuits/liens/credit reporting too early at nonprofits | dates; nonprofit status | Bright Data (inside #1) | S | Medium | Yes | [summary](https://team-iha.org/finance/charity-care-financial-assistance/billing-and-collections/) (reg text not opened) |
| 19 | Federal credit reporting | Rule vacated July 2025; bureaus skip debts under $500 | — | Bright Data | S | Low | — | [Orrick](https://infobytes.orrick.com/2025-07-18/district-court-tosses-cfpbs-medical-debt-rule/) |
| 20 | Statute of limitations | Lawsuit on debt older than ~4 years | dates | Bright Data | S | Low | — | [Rumsey](https://scocal.stanford.edu/opinion/collection-bureau-san-jose-v-rumsey-32125) |
| 21 | Garnishment / liens | Liens or garnishment without a court order | collection letters | Bright Data | S | Low | — | [HSC 127425(h)](https://california.public.law/codes/health_and_safety_code_section_127425) |
| 22 | Insurer paid the patient directly | Reimbursement deposit that should go to the provider | Nessie deposits | Nessie | S | Low | Seedable | Civ 1785.27 exception |
| 23 | Policy freshness | A tier changed since the crawl | existing `recheck` | Bright Data | done | Low | Yes | — |
| 24 | Other-relief math | UCSF's automatic 70% self-pay discount; Sutter 40%/20% above 400% FPL | policydb `other_relief` | Bright Data | S–M | Medium (uninsured variant) | Yes | policydb quotes |

## What doesn't fit two agents

Clinical upcoding (#8, needs medical records: skip), a full denial/appeal workflow (#13, a separate
product), and the No Surprises Act physician bill (#14, needs a second document from intake). None is worth
breaking the two-agent design for; mention #14 as roadmap ("the ER physician bill is next").
