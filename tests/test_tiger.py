"""Tiger Data storage (remy/tiger.py). The database itself isn't reachable in tests: these cover the parts that
decide what gets stored, and that a case never depends on Tiger being there."""
import httpx

from remy import app as app_module, config, tiger


def test_off_without_url():
    assert config.TIGER_DATABASE_URL == "" and not tiger.enabled()


async def test_save_is_a_no_op_when_off():
    case = {"id": "x", "patient": {"first_name": "Rosa", "last_name": "Martinez"}, "hospital": {}}
    assert await tiger.save_household(case, [], {}, {}, False) is None
    assert await tiger.households_by_need() == []


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


async def test_households_route_reports_off():
    transport = httpx.ASGITransport(app=app_module.app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/households")
    assert r.status_code == 200 and r.json() == {"enabled": False, "households": []}
