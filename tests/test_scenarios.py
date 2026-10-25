"""Generated scenarios (demo/scenarios/<name>/): the files on disk match the generator, every number adds up,
and finance.py reproduces expected.json the way the app runs it."""
import asyncio
import json

import pytest

from remy import billcheck, config, finance, prices, scenarios
from remy.agents import auditor, policy
from remy.events import bus
from remy.integrations.nessie import LocalMirror

NAMES = list(scenarios.SPECS)


def load(name: str) -> tuple[dict, dict]:
    d = config.DEMO_DIR / "scenarios" / name
    return json.loads((d / "case.json").read_text()), json.loads((d / "expected.json").read_text())


def c(x) -> int:
    return scenarios.cents(x)


@pytest.mark.parametrize("name", NAMES)
def test_files_are_reproducible(name):
    case, exp = load(name)
    out = scenarios.build(name)
    assert out["case"] == case, f"demo/scenarios/{name}/case.json is stale: run scripts/make_scenario.py {name}"
    assert out["expected"] == exp, f"demo/scenarios/{name}/expected.json is stale: run scripts/make_scenario.py {name}"


@pytest.mark.parametrize("name", NAMES)
def test_same_format_as_demo_case(name):
    case, _ = load(name)
    demo = json.loads((config.DEMO_DIR / "case.json").read_text())
    assert set(demo) <= set(case)
    for k in ("patient", "hospital", "bill"):
        assert set(demo[k]) <= set(case[k]), k
    assert all({"code", "description", "amount"} <= set(li) for li in case["bill"]["line_items"])
    if case["eob"]:
        assert set(demo["eob"]) <= set(case["eob"])
    assert all({"type", "date", "amount", "description"} <= set(t) for t in case["transactions"])
    assert all(t.get("merchant") for t in case["transactions"] if t["type"] == "purchase")


@pytest.mark.parametrize("name", NAMES)
def test_bill_adds_up_and_prices_are_real(name):
    case, exp = load(name)
    b = case["bill"]
    assert c(b["total_charges"]) == sum(c(li["amount"]) for li in b["line_items"])
    assert c(b["amount_due"]) == c(b["total_charges"]) - c(b["insurance_adjustments"]) - c(b["patient_payments"])
    hid = exp["real"]["hospital_id"]
    for li, prov in zip(b["line_items"], exp["bill_lines"], strict=True):
        posted = prices.lookup(hid, li["code"])
        assert li["description"] == posted["description"]
        if prov.get("planted_error") == "price_above_posted":
            assert c(li["amount"]) > c(posted["gross"]) * li["units"]
        else:
            assert c(li["amount"]) == c(posted["gross"]) * li["units"]
    assert exp["real"]["price_file"]["file_url"] == prices.table(hid)["source"]["file_url"]


@pytest.mark.parametrize("name", [n for n in NAMES if scenarios.SPECS[n]["insurance"]])
def test_eob_adds_up(name):
    case, _ = load(name)
    e, b = case["eob"], case["bill"]
    assert c(e["billed"]) == c(b["total_charges"])
    for k_tot, k_line in (("billed", "billed"), ("allowed", "allowed"), ("plan_paid", "plan_paid"),
                          ("patient_responsibility", "patient_responsibility"), ("deductible_applied", "deductible"),
                          ("coinsurance", "coinsurance")):
        assert c(e[k_tot]) == sum(c(li[k_line]) for li in e["lines"]), k_tot
    assert c(e["plan_paid"]) + c(e["patient_responsibility"]) == c(e["allowed"])
    for li in e["lines"]:
        assert c(li["deductible"]) + c(li["coinsurance"]) == c(li["patient_responsibility"])
        assert c(li["plan_paid"]) + c(li["patient_responsibility"]) == c(li["allowed"]) <= c(li["billed"])
    assert e["insurer"].lower() not in scenarios.real_insurer_names()


@pytest.mark.parametrize("name", NAMES)
def test_finance_reproduces_expected(name):
    case, exp = load(name)
    txns = LocalMirror(case).txns
    assert txns == scenarios.mirror_ids(case["transactions"])  # same ids the app's offline bank assigns
    insured = billcheck.insured(case)
    eob = case["eob"] if insured else {"patient_responsibility": exp["totals"]["patient_responsibility"],
                                       "claim_number": "self-pay"}
    rec = finance.reconcile(case["bill"], eob, txns)
    fr = exp["finance_reconcile"]
    for k in ("billed_due", "patient_responsibility", "insurance_adjustment", "paid", "owed", "overbilled_by"):
        assert rec[k] == fr[k], k
    assert sorted(t["id"] for t in rec["payments"]) == fr["payment_txn_ids"]
    assert [[d["first"]["id"], d["duplicate"]["id"]] for d in rec["duplicates"]] == fr["duplicate_txn_ids"]

    wf = exp["waterfall"]
    assert c(wf["start"]["amount"]) + sum(c(s["amount"]) for s in wf["steps"]) == c(wf["owed_before_discount"])
    assert c(wf["owed_before_discount"]) == c(rec["owed"])

    tiers = finance.clean_tiers(exp["real"]["policy"]["tiers"])
    inc = finance.income(txns, today=finance._d(exp["as_of"]))
    assert inc["annual"] == exp["income"]["annual"]
    # out-of-pocket as the policy agent counts it: this bill's share + the bank's last 12 months of medical bills
    med = finance.medical_spending(txns, case["bill"], case["eob"], today=finance._d(exp["as_of"]))
    assert med["total"] == exp["medical_spending"]["bank_total"]
    assert [t["id"] for t in med["txns"]] == exp["medical_spending"]["txn_ids"]
    oop = round(float(case["eob"].get("patient_responsibility") or 0) + med["total"], 2)
    elig = finance.charity_discount(inc["annual"], case["patient"]["household_size"], tiers, insured=insured,
                                    out_of_pocket=oop)
    assert elig["discount_pct"] == exp["discount"]["discount_pct"]
    assert elig["fpl_pct"] == exp["discount"]["fpl_pct"]
    st = finance.settle(rec["patient_responsibility"], rec["paid"], elig["discount_pct"])
    assert st == exp["settle"]
    cap = exp["real"]["policy"]["payment_plan"].get("max_pct_monthly_income")
    bills = finance.monthly_bills(txns, today=finance._d(exp["as_of"]), bill=case["bill"])
    assert bills["monthly"] == exp["income"]["bills_monthly"]
    plan = finance.payment_plan(st["balance"], inc["monthly"], policy_cap_pct=cap, monthly_bills=bills["monthly"])
    assert plan == exp["plan"]
    assert plan["months"] == 0 or round(plan["monthly"] * (plan["months"] - 1) + plan["last"], 2) == st["balance"]
    assert exp["correct"] == {"balance": st["balance"], "refund": st["refund"], "discount_pct": elig["discount_pct"],
                              "plan_months": plan["months"], "plan_monthly": plan["monthly"], "plan_last": plan["last"]}


@pytest.mark.parametrize("name", NAMES)
def test_every_planted_error_is_reported(name):
    _, exp = load(name)
    planted = set(scenarios.SPECS[name]["errors"])
    assert {e["type"] for e in exp["planted_errors"]} == planted
    ids = {f["id"].split("-")[0] for f in exp["findings"]}
    want = {"duplicate_line": "duplicate_line", "price_above_posted": "price_above_posted",
            "insurance_not_applied": "insurance_not_applied", "uncredited_payment": "paid",
            "duplicate_bank_charge": "dupe", "refund_owed": "refund"}
    assert {want[p] for p in planted} <= ids


def test_rosa_real_story():
    case, exp = load("rosa-real")
    assert case["bill"]["line_items"][1]["amount"] == 8287.00  # CPMC Van Ness's real CT gross charge
    assert exp["totals"]["paid_uncredited"] == 500.00
    assert [s["error"] for s in exp["waterfall"]["steps"]] == ["duplicate_line", "insurance_not_applied",
                                                               "uncredited_payment"]
    assert 200 < exp["discount"]["fpl_pct"] < 250 and exp["correct"]["discount_pct"] == 0


def test_marcus_real_is_free_care():
    _, exp = load("marcus-real")
    assert exp["correct"]["discount_pct"] == 100 and exp["correct"]["balance"] == 0
    assert exp["discount"]["oop_pct_income"] > 10


def test_raj_cpmc_hero_story():
    """The hero demo: every sponsor's part shows in one case, in the order the case page tells it."""
    case, exp = load("raj-cpmc")
    assert exp["real"]["hospital_id"] == "sutter-cpmc" and case["eob"] and billcheck.insured(case)
    # 1. bill mistakes on real prices: a duplicate line and a non-drug line above CPMC's posted gross charge
    found = {f["id"]: f for f in exp["findings"]}
    assert found["duplicate_line"]["money"] == 1160.00  # 96360 IV hydration billed twice
    over = next(e for e in exp["planted_errors"] if e["type"] == "price_above_posted")
    assert over["code"] == "74176" and not over["code"].startswith("J")
    assert over["posted"] == prices.lookup("sutter-cpmc", "74176")["gross"] == 6374.00
    assert found["price_above_posted"]["money"] == over["overcharge"] == 776.00
    # 2. insurance not applied: the EOB's share is far lower than the bill
    assert exp["totals"]["insurance_adjustments_on_bill"] == 0
    assert exp["totals"]["patient_responsibility"] < exp["totals"]["amount_due"] / 3
    assert found["insurance_not_applied"]["money"] > 0
    # 3. a payment in the bank, to the hospital, after the date of service, that the bill doesn't show
    paid = [f for f in exp["findings"] if f["id"].startswith("paid-")]
    assert len(paid) == 1 and paid[0]["money"] == exp["totals"]["paid_uncredited"] == 400.00
    txn = next(t for t in LocalMirror(case).txns if t["id"] == paid[0]["id"].removeprefix("paid-"))
    assert txn["date"] > case["bill"]["service_date"] and case["bill"]["patient_payments"] == 0
    # 4. the bank decides eligibility: this bill alone is under Sutter's 10% bar, with the bank's 12 months of
    #    medical bills it's over, on take-home and on estimated gross pay (so no "have pay stubs ready" warning)
    med, d, inc = exp["medical_spending"], exp["discount"], exp["income"]
    tier = next(t for t in exp["real"]["policy"]["tiers"] if t["applies_to"] == "insured")
    assert tier["min_oop_pct_income"] == 10 and tier["max_fpl_pct"] == 400 and "past 12 months" in tier["quote"]
    assert med["bank_decides_eligibility"] is True and med["discount_without_bank"] == 0
    assert 100 * med["this_bill"] / inc["annual"] <= 10 < d["oop_pct_income"]
    assert 100 * med["out_of_pocket"] / inc["gross_annual_est"] >= 11
    assert d["at_estimated_gross"] is None and d["fpl_pct"] <= 400
    assert 100 * inc["gross_annual_est"] / d["fpl_amount"] <= 400
    bank_med = {t["merchant"] for t in LocalMirror(case).txns if t["id"] in med["txn_ids"]}
    for kind in ("PHARMACY", "Urgent Care", "Physical Therapy", "Imaging"):
        assert any(kind.lower() in m.lower() for m in bank_med), kind
    # 5. ends at $0 with the missed payment refunded
    assert exp["correct"]["discount_pct"] == 100 and exp["correct"]["balance"] == 0
    assert exp["correct"]["refund"] == 400.00 > 0 and found["refund"]["money"] == 400.00
    # 6. messy bank: varying pay, gig payouts that count as income, noise
    assert inc["source"].count("gig/payment-app deposits") == 1
    gig = [t for t in case["transactions"] if t["type"] == "deposit" and "PAYROLL" not in t["description"]]
    assert len({t["amount"] for t in gig}) == len(gig) > 20
    assert {"UBER", "LYFT"} <= {t["description"].split()[0] for t in gig}
    assert any(t["type"] == "withdrawal" for t in case["transactions"])


def test_uninsured_case_has_no_eob():
    case, exp = load("amara-kaiser-uninsured")
    assert case["eob"] == {} and exp["eob_basis"] is None
    assert exp["discount"]["insured"] is False


def test_refund_case():
    _, exp = load("dana-altabates-refund")
    assert exp["correct"]["refund"] == exp["totals"]["paid_total"] > 0
    assert exp["must_not_report"] and "missed" in exp["must_not_report"][0]["what"]


@pytest.mark.parametrize("name", NAMES)
async def test_app_agents_reach_expected_balance(name):
    """The app's own Nessie + Bright Data agents, offline (LocalMirror bank, policy DB), on the generated case,
    including the uninsured one ("eob": {})."""
    case, exp = load(name)
    case = {**case, "id": f"scn-{name}", "today": exp["as_of"]}
    loop = asyncio.get_running_loop()
    txns_ready, owed_ready = loop.create_future(), loop.create_future()
    aud, pol = await asyncio.gather(
        auditor.run(case, LocalMirror(case), "mirror", "Offline bank (mirror)", txns_ready, owed_ready),
        policy.run(case, txns_ready, owed_ready))
    assert aud["reconciliation"]["owed"] == exp["finance_reconcile"]["owed"]
    assert aud["reconciliation"]["patient_responsibility"] == exp["totals"]["patient_responsibility"]
    assert pol["target"] == exp["correct"]["balance"]
    assert pol["refund"] == exp["correct"]["refund"]
    assert pol["eligibility"]["discount_pct"] == exp["correct"]["discount_pct"]
    assert pol["eligibility"]["insured"] is exp["discount"]["insured"] is billcheck.insured(case)
    assert pol["plan"] == exp["plan"]
    assert (pol["eligibility"]["gross_check"] or None) == exp["discount"]["at_estimated_gross"]

    # what the Nessie agent reported: exactly the missed payments expected.json lists, never credited ones
    found = {e["key"].split(":", 1)[1]: e for e in bus.history(case["id"])
             if e["kind"] == "finding" and e["agent"] == "auditor"}
    want_paid = {f["id"]: f["money"] for f in exp["findings"] if f["id"].startswith("paid-")}
    assert {k: f["saved"] for k, f in found.items() if k.startswith("paid-")} == want_paid
    missed = {e["source"].rsplit("txn ", 1)[1] for e in bus.history(case["id"])
              if e["kind"] == "evidence" and e["label"] == "Payment hospital may not see"}
    assert missed == {k.removeprefix("paid-") for k in want_paid}
    for m in exp["must_not_report"]:
        if "missed" in m["what"]:
            txn = m["what"].split()[1]
            assert f"paid-{txn}" not in found and txn not in missed, m
    # the bill's own line errors (duplicates and charges above the posted price) are reported with their money,
    # insured or not
    kinds = ("duplicate_line", "price_above_posted")
    lines = {k.split("-")[0]: f["saved"] for k, f in found.items() if k.split("-")[0] in kinds}
    assert lines == {f["id"]: f["money"] for f in exp["findings"] if f["id"] in kinds}
    if case["eob"]:  # insured: the insurance finding is the gap left once the line errors are taken out
        ins = next((f["money"] for f in exp["findings"] if f["id"] == "insurance_not_applied"), None)
        assert (found["insurance"]["saved"] if "insurance" in found else None) == ins
    else:  # self-pay: the line errors set the share
        assert "insurance" not in found
        assert not any(ln["label"].startswith("Insurance") for ln in aud["reconciliation"]["lines"])
    lines = aud["reconciliation"]["lines"]
    if aud["reconciliation"]["owed"] > 0:  # the reconciliation lines add up to what's owed
        assert c(sum(ln["amount"] for ln in lines if not ln.get("total"))) == c(lines[-1]["amount"])


@pytest.mark.parametrize("name", [n for n in NAMES if not scenarios.SPECS[n]["insurance"]])
def test_self_pay_reconcile_without_pseudo_eob(name):
    """No EOB at all: finance + billcheck reach the generator's numbers from the bill and the price file alone."""
    case, exp = load(name)
    txns = LocalMirror(case).txns
    fixes = billcheck.line_corrections(case["bill"], case["hospital"]["name"])
    rec = finance.reconcile(case["bill"], case["eob"], txns, corrections=fixes)
    fr = exp["finance_reconcile"]
    for k in ("billed_due", "patient_responsibility", "insurance_adjustment", "paid", "owed", "overbilled_by"):
        assert rec[k] == fr[k], k
    assert rec["insured"] is False


def test_real_insurer_names_are_rejected():
    spec = {**scenarios.SPECS["rosa-real"],
            "insurance": {**scenarios.SPECS["rosa-real"]["insurance"], "insurer": "Aetna"}}
    with pytest.raises(ValueError, match="real payer"):
        scenarios.adjudicate(spec, [])
