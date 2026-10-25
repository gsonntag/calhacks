"""Tiger Data storage (remy/tiger.py). The database itself isn't reachable in tests: these cover the parts that
decide what gets stored, and that a case never depends on Tiger being there."""
from remy import config, tiger


def test_off_without_url():
    assert config.TIGER_DATABASE_URL == "" and not tiger.enabled()


async def test_save_is_a_no_op_when_off():
    case = {"id": "x", "patient": {"first_name": "Rosa", "last_name": "Martinez"}, "hospital": {}}
    assert await tiger.save_household(case, [], {}, {}, False) is None


def test_profile_id_is_stable_across_cases():
    a = {"first_name": "Rosa ", "last_name": "MARTINEZ", "dob": "1991-05-22", "household_size": 1}
    b = {"first_name": "rosa", "last_name": "Martinez", "dob": "1991-05-22", "household_size": 3}
    assert tiger.profile_id(a) == tiger.profile_id(b)
    assert tiger.profile_id(a) != tiger.profile_id({**a, "dob": "1991-05-23"})


def test_income_matches_what_eligibility_counts():
    assert tiger._is_income({"type": "deposit", "description": "PAYROLL DIRECT DEP - EAST BAY SCHOOL DIST"})
    assert tiger._is_income({"type": "deposit", "description": "DoorDash payout"})
    assert not tiger._is_income({"type": "deposit", "description": "Transfer from savings"})
    assert not tiger._is_income({"type": "purchase", "description": "payroll"})
