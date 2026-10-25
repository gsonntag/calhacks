"""The Nessie track: a patient missing from Nessie (or Nessie down) falls back to sample bank data, said plainly;
12 months of bank medical spending feeds the high-medical-cost rule; the hospital bill is marked updated in the
bank once billing confirms a balance; and the sample bank histories are messy like real ones. No live calls:
NessieClient is replaced by fakes."""
import asyncio
import json

import httpx
import pytest

from remy import billcheck, config, finance, orchestrator, scenarios
from remy.agents import auditor, policy
from remy.events import bus
from remy.integrations import nessie
from remy.integrations.nessie import FallbackBank, LocalMirror, get_bank, mark_bill_corrected

CPMC = {"provider": "California Pacific Medical Center", "account_number": "CPMC-48213977",
        "service_date": "2026-08-02", "statement_date": "2026-10-10", "total_charges": 8700.0, "amount_due": 8700.0}
HIGH_COST = finance.clean_tiers([
    {"max_fpl_pct": 400, "discount_pct": 100, "applies_to": "uninsured", "quote": "Uninsured up to 400% FPL: free."},
    {"max_fpl_pct": 400, "discount_pct": 100, "applies_to": "insured", "min_oop_pct_income": 10,
     "quote": "Insured up to 400% FPL with medical expenses over 10% of income: free."}])


def txn(i, merchant, amount, date, description="", type_="purchase"):
    return {"id": f"t{i}", "type": type_, "date": date, "amount": amount, "merchant": merchant, "description": description}


def scenario(name: str) -> dict:
    return json.loads((config.DEMO_DIR / "scenarios" / name / "case.json").read_text())


# ---- 1. no Capital One account, or Nessie down: sample data, said plainly ---------------------------------

class FakeNessie:
    def __init__(self, found=None, find_delay=0.0, txns_error: Exception | None = None):
        self.found, self.find_delay, self.txns_error = found, find_delay, txns_error
        self.bills: list[dict] = []

    async def find_account(self, first, last):
        await asyncio.sleep(self.find_delay)
        return self.found

    async def transactions(self, account_id):
        if self.txns_error:
            raise self.txns_error
        return [txn(1, "Berkeley Bowl", 12.5, "2026-10-01")]

    async def create_bill(self, account_id, *a, **kw):
        self.bills.append({"account": account_id, **kw})
        return {"_id": "live-bill"}


@pytest.fixture
def live(monkeypatch):
    """A Nessie key is set, but NessieClient is a fake (no network)."""
    monkeypatch.setattr(config, "NESSIE_API_KEY", "test-key")
    fake = FakeNessie()
    monkeypatch.setattr(nessie, "NessieClient", lambda key, base: fake)
    return fake


def dana_case() -> dict:
    c = scenario("dana-altabates-refund")
    return {**c, "id": "nt-dana", "scenario": "default", "today": "2026-10-25"}


async def test_patient_missing_from_nessie_uses_their_sample_data(live):
    bank, account_id, label = await get_bank(dana_case())
    assert isinstance(bank, FallbackBank) and account_id == "mirror"
    assert bank.notice == "No Capital One account for Dana — using sample bank data"
    assert label.startswith("Sample bank data") and "Capital One" not in label
    # Dana has a generated scenario, so her own sample history is used, not Rosa's
    assert await bank.transactions(account_id) == LocalMirror(scenario("dana-altabates-refund")).txns


async def test_unknown_patient_gets_the_scenario_sample_labelled_not_theirs(live):
    case = {**orchestrator.demo_case(), "id": "nt-zoe", "scenario": "default", "today": "2026-10-25",
            "patient": {"first_name": "Zoe", "last_name": "Quinn", "household_size": 1}}
    _, _, label = await get_bank(case)
    assert label == "Sample bank data (not Zoe's: Rosa's sample)"


async def test_nessie_slow_falls_back(live, monkeypatch):
    monkeypatch.setattr(nessie, "LIVE_TIMEOUT", 0.05)
    live.find_delay = 1
    bank, account_id, label = await get_bank(dana_case())
    assert account_id == "mirror" and "didn't answer (timed out)" in bank.notice and label.startswith("Sample bank data")


async def test_nessie_down_while_pulling_history_falls_back_and_says_so(live):
    live.found = {"customer_id": "c1", "account_id": "a1"}
    live.txns_error = httpx.ConnectError("connection refused")
    case = dana_case()
    bank, account_id, label = await get_bank(case)
    assert account_id == "a1" and label == "Capital One Nessie (live)"
    loop = asyncio.get_running_loop()
    txns_ready, owed_ready = loop.create_future(), loop.create_future()
    case["id"] = "nt-down"
    await auditor.run(case, bank, account_id, label, txns_ready, owed_ready)
    assert txns_ready.result() == LocalMirror(scenario("dana-altabates-refund")).txns
    hist = bus.history(case["id"])
    step = [e for e in hist if e["kind"] == "step" and e["key"] == "auditor:bank"][-1]
    assert step["label"] == "Pull your bank history (Sample bank data)"
    assert step["result"].startswith("Capital One (Nessie) didn't answer (connection refused) — using sample bank data")
    assert any(e["kind"] == "warning" and "using sample bank data" in e["text"] for e in hist)
    # after the fallback, writes go to the sample bank: nothing claims to be saved in Capital One
    await bank.create_bill(account_id, payee="X", nickname="X plan", amount=10, payment_date="2026-11-15")
    assert live.bills == [] and any(b["nickname"] == "X plan" for b in await bank.list_bills(account_id))


async def test_live_account_writes_go_to_nessie(live):
    live.found = {"customer_id": "c1", "account_id": "a1"}
    bank, account_id, _ = await get_bank(dana_case())
    await bank.create_bill(account_id, payee="X", nickname="X plan", amount=10, payment_date="2026-11-15")
    assert live.bills and live.bills[0]["account"] == "a1"


# ---- 2. 12 months of bank medical spending decides eligibility --------------------------------------------

def test_medical_spending_counts_care_not_this_bill_premiums_or_double_charges():
    txns = [
        txn(1, "WALGREENS #4410", 23.17, "2026-01-14", "Prescription"),
        txn(2, "CVS/PHARMACY #9921", 34.50, "2026-07-11"),
        txn(3, "Elmwood Dental Group", 60.00, "2026-04-09", "Cleaning copay"),
        txn(4, "Berkeley Urgent Care", 75.00, "2026-08-02", "Visit copay"),
        txn(5, "Berkeley Urgent Care", 75.00, "2026-08-03", "Visit copay"),  # the double charge's copy
        txn(6, "SUTTER HEALTH PYMT", 500.00, "2026-09-14", "ONLINE PMT"),  # toward this bill: already in its share
        txn(7, "California Pacific Medical Center", 90.00, "2026-03-02", "Clinic visit"),  # an earlier visit
        txn(8, "Fogline Health Plan", 310.00, "2026-09-01", "Monthly premium"),
        txn(9, "Lakeshore Fitness", 39.99, "2026-09-03", "Gym membership"),
        txn(10, "WALGREENS #4410", 15.00, "2025-09-30", "Prescription"),  # more than 12 months ago
        txn(11, "Berkeley Bowl", 64.00, "2026-08-20", "Groceries"),
    ]
    med = finance.medical_spending(txns, CPMC, {}, today=finance._d("2026-10-25"))
    assert [t["id"] for t in med["txns"]] == ["t1", "t7", "t3", "t2", "t4"]
    assert med["total"] == round(23.17 + 90 + 60 + 34.50 + 75, 2) and med["since"] == "2025-10-25"


def high_cost_case(extra_medical: list[dict]) -> tuple[dict, list[dict]]:
    """Insured, household of 1 at 40,000/yr (251% FPL); this bill's share is $3,800: 9.5% of income."""
    pay = [txn(100 + k, "", 40000 / 26, f"2026-{m:02d}-{d:02d}", "PAYROLL DIRECT DEP - ACME", "deposit")
           for k, (m, d) in enumerate([(8, 7), (8, 21), (9, 4), (9, 18), (10, 2), (10, 16)])]
    case = {"id": "nt-hc", "today": "2026-10-25", "bill": {**CPMC, "amount_due": 3800.0},
            "eob": {"patient_responsibility": 3800.0, "claim_number": "C-9"},
            "patient": {"first_name": "Ana", "last_name": "Diaz", "household_size": 1}}
    return case, pay + extra_medical


def test_bank_medical_spending_decides_eligibility():
    case, txns = high_cost_case([txn(1, "WALGREENS #4410", 120.00, "2026-02-10", "Prescription"),
                                 txn(2, "Bay Physical Therapy", 195.00, "2026-05-20", "PT copay")])
    inc = finance.income(txns, today=finance._d(case["today"]))
    elig = policy.eligibility(case, txns, inc, HIGH_COST, insured=True)
    assert elig["bill_out_of_pocket"] == 3800 and elig["bank_medical"] == 315
    assert elig["discount_pct"] == 100 and elig["needs_bank_medical"] is True
    assert elig["oop_pct_income"] == round(100 * 4115 / inc["annual"], 1) > 10
    # without the bank's medical bills, this bill alone is under 10%: no write-off
    case2, txns2 = high_cost_case([])
    e2 = policy.eligibility(case2, txns2, finance.income(txns2, today=finance._d("2026-10-25")), HIGH_COST, True)
    assert e2["discount_pct"] == 0 and e2["needs_bank_medical"] is False


async def test_agents_show_medical_spending_as_step_finding_and_proof(monkeypatch):
    case, txns = high_cost_case([txn(1, "WALGREENS #4410", 120.00, "2026-02-10", "Prescription"),
                                 txn(2, "Bay Physical Therapy", 195.00, "2026-05-20", "PT copay")])
    case.update(hospital={"name": CPMC["provider"]})

    async def fixed_policy(cid, r, hosp):
        return {"found": {"tiers": HIGH_COST}, "source": "test policy", "live": False, "origin": "test",
                "fetched_at": None, "sources": []}
    monkeypatch.setattr(policy, "find_policy", fixed_policy)

    class Bank:
        async def transactions(self, account_id):
            return txns
    loop = asyncio.get_running_loop()
    txns_ready, owed_ready = loop.create_future(), loop.create_future()
    aud, pol = await asyncio.gather(auditor.run(case, Bank(), "a1", "Capital One Nessie (live)", txns_ready, owed_ready),
                                    policy.run(case, txns_ready, owed_ready))
    assert aud["medical"]["total"] == 315 and pol["eligibility"]["discount_pct"] == 100 and pol["target"] == 0
    hist = bus.history(case["id"])
    step = [e for e in hist if e["kind"] == "step" and e["key"] == "auditor:medical"][-1]
    assert step["status"] == "ok" and step["result"].startswith("$315.00 in other medical bills since 2025-10-25")
    assert [p["source"] for p in step["proof"]] == ["Capital One Nessie · txn t1", "Capital One Nessie · txn t2"]
    finding = next(e for e in hist if e["kind"] == "finding" and e["key"] == "auditor:medical")
    assert finding["value"] == "$315 · 12 months" and finding["saved"] == 0
    discount = next(e for e in hist if e["kind"] == "finding" and e["key"] == "policy:discount")
    assert any("without them you wouldn't qualify" in p for p in discount["proof"])
    free = [e for e in hist if e["kind"] == "step" and e["key"] == "policy:free"][-1]
    # the bank tips him over: that contrast is the step's headline, with this bill's share and the bank's sum
    e = pol["eligibility"]
    assert e["bill_oop_pct_income"] < 10 < e["oop_pct_income"]
    assert free["result"] == (f"Your bank history qualifies you: this bill alone is {e['bill_oop_pct_income']:.1f}% of income; "
                              f"with $315 of other medical bills it's {e['oop_pct_income']:.1f}% — over their 10% limit")
    assert discount["title"] == "Free care: your bank history qualifies you"
    assert discount["proof"][0] == free["result"] + "." and discount["value"] == "$3,800 share written off"


def test_scenarios_record_medical_spending():
    for name in scenarios.SPECS:
        exp = json.loads((config.DEMO_DIR / "scenarios" / name / "expected.json").read_text())
        ms = exp["medical_spending"]
        assert ms["out_of_pocket"] == round(ms["this_bill"] + ms["bank_total"], 2)
        assert ms["txn_ids"] and ms["bank_total"] > 0, name
        # only Raj's outcome hinges on the bank (the hero case); for the others it only adds margin
        if name == "raj-cpmc":
            assert ms["bank_decides_eligibility"] is True and ms["discount_without_bank"] == 0
            assert exp["correct"]["discount_pct"] == 100
        else:
            assert ms["bank_decides_eligibility"] is False and ms["discount_without_bank"] == exp["correct"]["discount_pct"]


# ---- 3. the hospital bill is marked updated once billing confirms -----------------------------------------

async def test_mark_bill_corrected_updates_the_hospital_bill_not_the_plan():
    bank = LocalMirror(orchestrator.demo_case())
    plan = await bank.create_bill("mirror", payee=CPMC["provider"], amount=100, payment_date="2026-11-15",
                                  nickname=f"{CPMC['provider']} plan 6x$100 ({CPMC['account_number']})", status="recurring")
    res = await mark_bill_corrected(bank, "mirror", orchestrator.demo_case()["bill"], 600.0, "2026-10-25")
    assert res["bill_id"] == "mirror-bill-hospital" and res["was"] == 8700 and res["payment_amount"] == 600
    bills = {b["_id"]: b for b in await bank.list_bills("mirror")}
    assert bills["mirror-bill-hospital"]["nickname"] == "ER visit 2026-08-02 (CPMC-48213977) · corrected by billing 2026-10-25"
    assert bills[plan["_id"]]["payment_amount"] == 100
    res = await mark_bill_corrected(bank, "mirror", orchestrator.demo_case()["bill"], 0, "2026-10-26")
    assert res["status"] == "cancelled" and res["nickname"].endswith("corrected by billing 2026-10-26")


async def test_mark_bill_corrected_without_a_hospital_bill():
    bank = LocalMirror({"transactions": []})
    assert await mark_bill_corrected(bank, "mirror", CPMC, 600, "2026-10-25") is None


async def test_confirmed_balance_after_a_call_updates_the_bank():
    case = orchestrator.new_case()
    bank = LocalMirror(orchestrator.demo_case())
    orchestrator.banks[case["id"]] = (bank, "mirror", "Nessie sandbox mirror (offline)")
    case["calls"] = [{"n": 1, "confirmed_balance": None}]
    assert await orchestrator.mark_bank_bill(case) is None  # nothing confirmed: nothing written
    case["calls"].append({"n": 2, "confirmed_balance": 600.0})
    await orchestrator.mark_bank_bill(case)
    ev = [e for e in bus.history(case["id"]) if e["kind"] == "bank_bill_updated"]
    assert len(ev) == 1 and ev[0]["payment_amount"] == 600 and ev[0]["was"] == 8700
    assert ev[0]["bank"] == "Nessie sandbox mirror (offline)"
    orchestrator.banks.pop(case["id"], None)


# ---- 4. messier sample bank data, payment matching still exact --------------------------------------------

@pytest.mark.parametrize("merchant,desc,payee,want", [
    ("SUTTER HEALTH PYMT", "ONLINE PMT", "California Pacific Medical Center", True),   # Sutter bills centrally
    ("SUTTER HEALTH PYMT", "WEB PYMT", "Alta Bates Summit Medical Center", True),
    ("SUTTER HEALTH", "Clinic visit", "California Pacific Medical Center", False),     # not a bill payment
    ("SUTTER HEALTH PLUS", "Premium pmt", "California Pacific Medical Center", False),  # an insurance premium
    ("SUTTER HEALTH PYMT", "ONLINE PMT", "UCSF Medical Center", False),                # another system
    ("KAISER PERMANENTE PYMT", "POS DEBIT", "Kaiser Permanente Oakland Medical Center", True),
    ("KAISER PERMANENTE", "Monthly premium PMT", "Kaiser Permanente Oakland Medical Center", False),
])
def test_health_system_billing_name_matches(merchant, desc, payee, want):
    assert billcheck.is_payment_to({"merchant": merchant, "description": desc}, payee) is want


@pytest.mark.parametrize("name", list(scenarios.SPECS))
def test_sample_bank_histories_are_messy(name):
    case = scenario(name)
    txns, acct = case["transactions"], case["bill"]["account_number"]
    assert not any(acct in f"{t.get('merchant')} {t.get('description')}" for t in txns)  # no account number
    pay = [t["amount"] for t in txns if t["type"] == "deposit" and "PAYROLL" in t["description"]]
    assert len(set(pay)) > len(pay) // 2  # pay varies from check to check
    assert any(t["type"] == "withdrawal" for t in txns)  # cash
    first, last = min(t["date"] for t in txns), max(t["date"] for t in txns)
    assert (finance._d(last) - finance._d(first)).days >= 300  # a year, for the 12-month medical rule


def test_demo_cases_are_messy_too():
    rosa, marcus = orchestrator.demo_case(), orchestrator.demo_case("freecare")
    for c in (rosa, marcus):
        pay = [t["amount"] for t in c["transactions"] if t["type"] == "deposit"]
        assert len(set(pay)) == len(pay)  # no two paychecks the same
        assert not any(c["bill"]["account_number"] in t.get("description", "") for t in c["transactions"])
    hospital = [t for t in rosa["transactions"] if billcheck.is_payment_to(t, rosa["bill"]["provider"])]
    assert [(t["merchant"], t["amount"]) for t in hospital] == [("SUTTER HEALTH PYMT", 500.0)]


@pytest.mark.parametrize("case", [orchestrator.demo_case("freecare"), scenario("marcus-real")],
                         ids=["case_freecare", "marcus-real"])
def test_marcus_clears_the_high_cost_bar_even_on_estimated_gross_pay(case):
    """Marcus leads the demo: his share + 12 months of bank medical bills must stay over UCSF's 10% bar even if
    the hospital counts gross pay (master's finance.gross_check estimates it as take-home / 0.80), with room."""
    txns = LocalMirror(case).txns
    today = finance._d("2026-10-25")
    inc = finance.income(txns, today=today)
    med = finance.medical_spending(txns, case["bill"], case["eob"], today=today)
    oop = float(case["eob"]["patient_responsibility"]) + med["total"]
    assert oop / (inc["annual"] / 0.80) >= 0.11
