# Generated scenarios

Demo patients built from **real hospital data**, with **planted errors whose correct answers are known**.
Generator: `remy/scenarios.py` (specs in `SPECS`). Command: `scripts/make_scenario.py`.

```bash
uv run python scripts/make_scenario.py --list
uv run python scripts/make_scenario.py rosa-real            # or: all
uv run --with reportlab python scripts/make_scenario.py all # also bill.pdf / eob.pdf (SAMPLE watermark)
uv run pytest -q tests/test_scenarios.py
```

Each folder has `case.json` (the same format as `demo/case.json`, with a few extra fields: line `units`,
bill `facility` / `insurance_adjustments` / `patient_payments`, EOB `lines`), `expected.json` (the answers),
and the PDFs when reportlab is available. Runs offline with fixed seeds; re-running gives the same files.

| Scenario | Hospital | Patient | Planted errors | Correct result |
|---|---|---|---|---|
| `rosa-real` | CPMC Van Ness | insured, 1 person, 229% FPL | insurance not applied, $500 payment not credited, IV push billed twice | $15,414.33 billed → $1,574.98, no discount, 18 × $88/mo |
| `marcus-real` | UCSF | HDHP, 3 people, 149% FPL | none (the policy is the win) | $4,174.43 → $0 free care (out-of-pocket 12.2% of income: this bill + $1,682 of pediatrician, pharmacy, orthopedic, imaging and PT bills in his bank; 11.5% even on estimated gross pay) |
| `dana-altabates-refund` | Alta Bates Summit | insured, 2 people, 181% FPL | refund owed ($750 credited payments), gym double charge | $3,646.20 → $0 free care + $750 refund |
| `amara-kaiser-uninsured` | Kaiser Oakland | uninsured, 2 people, 290% FPL | ankle X-ray above posted price, foot X-ray twice, $300 not credited, streaming double charge | $6,678.57 → $5,098.57 → 50% off → $2,399.28, 12 × $200/mo |
| `lena-johnmuir` | John Muir Walnut Creek | insured, 1 person, 508% FPL | insurance not applied, CT angiogram above posted price, dry-cleaner double charge | $27,619.57 → $967.52, no discount, 6 × $162/mo |
| `jordan-ucla-wrist` | Ronald Reagan UCLA | UCLA student, student plan, 1 person, 95% FPL | insurance not applied, wrist X-ray billed twice, splint above posted price | $4,990.00 → $1,690.00 → $0 free care (out-of-pocket 14.9% of income: this bill + $569 of cash follow-up care in his bank; paychecks dip after the injury) |

## What's real
- **Hospital**: the registry entry (`remy/policies/registry.py`); the bill's facility line comes from the price file.
- **Prices**: every bill line is the hospital's posted **gross charge** for that code (the outpatient line
  where the file has one), with the file's own description, from `policydb/prices/<id>.json`. That table was
  built from the hospital's CMS price-transparency file. `expected.json → real.price_file` records the file
  URL, its `last_updated_on`, fetch time and sha256. `bill_lines` traces each line.
- **Allowed amounts** (insured): the **median of the payer-specific dollar rates** the file lists for the code.
  When the file lists none (CPMC posts payer rates for only 19 of its 67 codes; ED level 4 has none), the
  allowed amount is estimated as gross × the hospital's median (payer median / gross) ratio. `eob_basis` says
  which lines used the estimate. The allowed amount never exceeds the posted gross charge.
- **Policy**: the hospital's current financial-assistance policy from `policydb/` (crawled through Bright Data):
  tiers, quotes and payment-plan cap, snapshotted in `expected.json → real.policy`.
- **Money math**: `finance.py`, called exactly as the app's agents call it (`reconcile`, `income`,
  `charity_discount`, `settle`, `payment_plan`).

## What's synthetic
- The **patient** (name, address, household, pay), account and claim numbers.
- The **insurer**: always fictional (the generator refuses any payer named in the price files), plus its
  deductible / coinsurance / out-of-pocket max. Deductible first, then coinsurance, line by line, to the cent.
- The **bank history**: a year of Nessie-shaped transactions (payroll, rent, monthly bills, everyday purchases,
  the planted payments and duplicates), messy like a real statement: pay varies ±3% from check to check, card
  merchants print under different names ("BERKELEY BOWL #212", "POS DEBIT …"), there's cash (ATM), Amazon,
  Venmo and a subscription, a few medical bills (spec `medical`), and payments to the hospital carry the health
  system's billing name ("SUTTER HEALTH PYMT", "ONLINE PMT"), never the hospital account number. Not seeded into
  Nessie (`scripts/seed_nessie.py <name>` can; it's paid, see docs/PLAN.md). The app's offline bank
  (`LocalMirror`) gives them ids `mirror-000…`, which `expected.json` uses.

## Reading expected.json
- `planted_errors`: each error with its exact effect on what the patient owes.
- `waterfall`: hospital's amount due → each correction → `owed_before_discount`. It adds up to the cent.
- `discount`, `settle`, `plan`, `correct`: FPL %, discount %, balance, refund and plan.
- `medical_spending`: the out-of-pocket the high-medical-cost rules use: this bill's share plus the bank's other
  medical bills in the 12 months before `as_of` (`finance.medical_spending`, with the transaction ids), and
  whether the bank is what decides eligibility (today it isn't for any of these patients: it adds margin).
- `finance_reconcile`: what the app's reconciliation gives today, including matched payment and duplicate ids.
- `findings`: what a correct audit should report (the money per finding follows the waterfall, so nothing is
  counted twice). `must_not_report`: false positives to avoid.
- `app_notes`: where today's app falls short on this case. The uninsured case has `"eob": {}`, and the Nessie
  agent can't run it yet. In the refund case the app would call credited payments "missed".

Income is annualised from the 90 days before `as_of` (2026-10-25). The app uses the real date, so live runs
long after that will count fewer paychecks (the same applies to `demo/case.json`).

## Add a scenario
1. Add an entry to `SPECS` in `remy/scenarios.py`: `hospital_id` (one with a price table), `procedures`
   (codes in that table, with units), `patient`, `pay`, `rent`, `merchants`, `insurance` (or `None`), `visit`
   dates and `errors`. Give it its own `seed`.
2. `uv run --with reportlab python scripts/make_scenario.py <name>`. The generator refuses inconsistent specs.
   That includes a bank merchant that `finance._matches_payee` would mistake for a hospital payment (avoid words
   from the hospital's name), an accidental bank duplicate, or a real insurer's name.
3. `uv run pytest -q tests/test_scenarios.py` (new specs are picked up automatically).

If a price table or the policy DB changes, `test_files_are_reproducible` fails until you regenerate.
