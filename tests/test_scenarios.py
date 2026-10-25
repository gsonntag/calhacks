"""Generated scenarios (demo/scenarios/<name>/): the files on disk match the generator, every number adds up,
and finance.py reproduces expected.json the way the app runs it."""
import asyncio
import json

import pytest

from billback import config, finance, prices, scenarios
from billback.agents import auditor, policy
from billback.integrations.nessie import LocalMirror

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
    insured = bool(case["eob"])
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
    elig = finance.charity_discount(inc["annual"], case["patient"]["household_size"], tiers, insured=insured,
                                    out_of_pocket=float(case["eob"].get("patient_responsibility") or 0))
    assert elig["discount_pct"] == exp["discount"]["discount_pct"]
    assert elig["fpl_pct"] == exp["discount"]["fpl_pct"]
    st = finance.settle(rec["patient_responsibility"], rec["paid"], elig["discount_pct"])
    assert st == exp["settle"]
    cap = exp["real"]["policy"]["payment_plan"].get("max_pct_monthly_income")
    plan = finance.payment_plan(st["balance"], inc["monthly"], policy_cap_pct=cap)
    assert plan == exp["plan"]
    assert exp["correct"] == {"balance": st["balance"], "refund": st["refund"], "discount_pct": elig["discount_pct"],
                              "plan_months": plan["months"], "plan_monthly": plan["monthly"]}


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


def test_uninsured_case_has_no_eob():
    case, exp = load("amara-kaiser-uninsured")
    assert case["eob"] == {} and exp["eob_basis"] is None
    assert exp["discount"]["insured"] is False


def test_refund_case():
    _, exp = load("dana-altabates-refund")
    assert exp["correct"]["refund"] == exp["totals"]["paid_total"] > 0
    assert exp["must_not_report"] and "missed" in exp["must_not_report"][0]["what"]


@pytest.mark.parametrize("name", [n for n in NAMES if scenarios.SPECS[n]["insurance"]])
async def test_app_agents_reach_expected_balance(name):
    """The app's own Nessie + Bright Data agents, offline (LocalMirror bank, policy DB), on the generated case."""
    case, exp = load(name)
    case = {**case, "id": f"scn-{name}", "today": exp["as_of"]}
    loop = asyncio.get_running_loop()
    txns_ready, owed_ready = loop.create_future(), loop.create_future()
    aud, pol = await asyncio.gather(
        auditor.run(case, LocalMirror(case), "mirror", "Offline bank (mirror)", txns_ready, owed_ready),
        policy.run(case, txns_ready, owed_ready))
    assert aud["reconciliation"]["owed"] == exp["finance_reconcile"]["owed"]
    assert pol["target"] == exp["correct"]["balance"]
    assert pol["refund"] == exp["correct"]["refund"]
    assert pol["eligibility"]["discount_pct"] == exp["correct"]["discount_pct"]
    assert (pol["plan"]["months"], pol["plan"]["monthly"]) == (exp["plan"]["months"], exp["plan"]["monthly"])


def test_real_insurer_names_are_rejected():
    spec = {**scenarios.SPECS["rosa-real"],
            "insurance": {**scenarios.SPECS["rosa-real"]["insurance"], "insurer": "Aetna"}}
    with pytest.raises(ValueError, match="real payer"):
        scenarios.adjudicate(spec, [])
