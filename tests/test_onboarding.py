"""Onboarding asks only what can change the outcome (remy/onboarding.py)."""
import httpx

from remy import app as app_module, onboarding

HOSPITALS = [
    {"id": "a", "name": "A", "city": "X", "tiers": [{"max_fpl_pct": 200, "discount_pct": 100, "applies_to": "all"},
                                                     {"max_fpl_pct": 400, "discount_pct": 50, "applies_to": "all"}]},
    {"id": "b", "name": "B", "city": "Y", "tiers": [{"max_fpl_pct": 400, "discount_pct": 100, "applies_to": "uninsured"},
                                                     {"max_fpl_pct": 400, "discount_pct": 100, "applies_to": "insured",
                                                      "min_oop_pct_income": 10}]},
]


def test_known_everything_is_decided():
    r = onboarding.assess({"hospital_id": "a", "household_size": 3, "income": 40_000}, hospitals=HOSPITALS)
    assert r["decided"] and r["certainty"] == 1.0 and r["next"] is None
    assert r["discount_pct"] == 100 and r["fpl_range"][0] == r["fpl_range"][1]


def test_asks_the_question_that_narrows_most():
    r = onboarding.assess({"hospital_id": "a", "household_size": 2}, hospitals=HOSPITALS)
    assert r["next"]["field"] == "income" and r["next"]["gain"] > 0


def test_stops_when_no_answer_can_change_the_outcome():
    # bank income far under every limit at hospital A: household size and insurance can't matter
    r = onboarding.assess({"hospital_id": "a"}, bank={"annual": 9_000, "source": "test"}, hospitals=HOSPITALS)
    assert r["decided"] and r["next"] is None and r["discount_pct"] == 100


def test_bank_prefills_income_as_gross():
    r = onboarding.assess({"hospital_id": "a", "household_size": 1}, bank={"annual": 40_000, "source": "t"},
                          hospitals=HOSPITALS)
    assert r["next"] is None or r["next"]["field"] != "income" or r["next"]["prefill"] == 50_000
    lo, hi = r["fpl_range"]
    assert lo < hi  # take-home stands for a range of gross incomes, never one number


def test_medical_bills_only_asked_when_they_matter():
    r = onboarding.assess({"hospital_id": "b", "household_size": 1, "income": 50_000, "insured": True},
                          hospitals=HOSPITALS)
    assert r["next"]["field"] == "medical_oop"
    r = onboarding.assess({"hospital_id": "b", "household_size": 1, "income": 50_000, "insured": False},
                          hospitals=HOSPITALS)
    assert r["decided"] and r["discount_pct"] == 100


def test_skipped_questions_are_not_asked_again():
    r = onboarding.assess({}, skipped=["hospital_id", "income"], hospitals=HOSPITALS)
    assert r["next"]["field"] not in ("hospital_id", "income")


def test_clean_drops_bad_input():
    a = onboarding.clean({"first_name": " Rosa ", "household_size": "40", "income": "abc", "insured": "yes",
                          "medical_oop": -5, "zip": 94702})
    assert a == {"first_name": "Rosa", "zip": "94702"}


async def test_assess_route_works_offline():
    transport = httpx.ASGITransport(app=app_module.app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/api/onboarding/assess", json={"answers": {"first_name": "Rosa", "last_name": "M"}})
        assert r.status_code == 200 and r.json()["bank"] is None and "certainty" in r.json()
        r = await c.post("/api/onboarding", json={"answers": {"first_name": "Rosa", "last_name": "M"}})
        assert r.status_code == 200 and r.json()["saved"] is False
