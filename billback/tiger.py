"""Tiger Data (Timescale Postgres): every household we help, and its bank history over time.

    profiles          one row per household: who, household size, insurance, latest income and eligibility;
                      onboarding (billback/onboarding.py) adds what the patient told us and the assessment
    transactions      hypertable (by time): every Nessie transaction pulled for a case, tagged income or not
    monthly_cashflow  continuous aggregate over transactions: income, spending and paychecks per household
                      per month. Real-time (materialized_only = false), so a case's own deposits show up at once.

Income counted here is the same set finance.income counts (payroll + gig/payment-app deposits), so the monthly
history and the eligibility figure agree. Eligibility math stays in finance.py; this is storage and history.
Unset TIGER_DATABASE_URL: every call is a no-op and the app runs from the case files alone."""
import asyncio
import hashlib
import re
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import config, finance

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id text PRIMARY KEY,
    first_name text, last_name text, dob text, city text, state text, zip text,
    household_size int, insured boolean, hospital text,
    income_annual numeric, income_source text, fpl_pct numeric, discount_pct numeric,
    last_case_id text, last_txn_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now());
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS income_reported numeric;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS medical_oop numeric;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS onboarding jsonb;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS onboarded_at timestamptz;
CREATE TABLE IF NOT EXISTS transactions (
    time timestamptz NOT NULL,
    profile_id text NOT NULL,
    txn_id text NOT NULL,
    type text NOT NULL,
    amount numeric NOT NULL,
    description text, merchant text,
    is_income boolean NOT NULL DEFAULT false,
    UNIQUE (profile_id, txn_id, time));
SELECT create_hypertable('transactions', by_range('time', INTERVAL '30 days'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS transactions_profile_time ON transactions (profile_id, time DESC);
CREATE MATERIALIZED VIEW IF NOT EXISTS monthly_cashflow
    WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
    SELECT profile_id, time_bucket(INTERVAL '1 month', time) AS month,
           coalesce(sum(amount) FILTER (WHERE is_income), 0) AS income,
           coalesce(sum(amount) FILTER (WHERE type <> 'deposit'), 0) AS spending,
           count(*) FILTER (WHERE is_income) AS paychecks
    FROM transactions GROUP BY profile_id, month WITH NO DATA;
SELECT add_continuous_aggregate_policy('monthly_cashflow', start_offset => INTERVAL '3 years',
    end_offset => INTERVAL '1 hour', schedule_interval => INTERVAL '15 minutes', if_not_exists => TRUE);
"""

_conn: psycopg.AsyncConnection | None = None
_lock = asyncio.Lock()
TIMEOUT = 10  # seconds per operation: Tiger is never allowed to stall a case


def enabled() -> bool:
    return bool(config.TIGER_DATABASE_URL)


async def _db() -> psycopg.AsyncConnection:
    global _conn
    async with _lock:
        if _conn is None or _conn.closed:
            _conn = await psycopg.AsyncConnection.connect(config.TIGER_DATABASE_URL, autocommit=True,
                                                          row_factory=dict_row, connect_timeout=TIMEOUT)
            await _conn.execute(SCHEMA)
        return _conn


def profile_id(patient: dict) -> str:
    """Same person, same id across cases: name + date of birth (name only when the bill has no DOB)."""
    key = "|".join(str(patient.get(k) or "").strip().lower() for k in ("first_name", "last_name", "dob"))
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def _is_income(t: dict) -> bool:
    if t.get("type") != "deposit":
        return False
    text = finance._norm(f"{t.get('description', '')} {t.get('merchant', '')}")
    return bool(re.search(finance.PAYROLL, text) or re.search(finance.OTHER_INCOME, text))


def _time(t: dict) -> datetime:
    d = datetime.fromisoformat(str(t["date"])[:19])
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


async def save_household(case: dict, txns: list[dict], inc: dict, elig: dict, insured: bool) -> list[dict] | None:
    """Upsert the household, store its transactions, return its monthly cashflow. None when Tiger is off."""
    if not enabled() or not (case["patient"].get("first_name") and case["patient"].get("last_name")):
        return None
    return await asyncio.wait_for(_save(case, txns, inc, elig, insured), TIMEOUT * 3)


async def _save(case, txns, inc, elig, insured):
    db, p = await _db(), case["patient"]
    pid, addr = profile_id(p), p.get("address") or {}
    rows = [(_time(t), pid, str(t.get("id") or f"{t['date']}|{t['amount']}|{t.get('description', '')}"), t["type"],
             float(t["amount"]), t.get("description") or "", t.get("merchant") or "", _is_income(t))
            for t in txns if t.get("date")]
    async with db.cursor() as cur:
        await cur.executemany(
            "INSERT INTO transactions (time, profile_id, txn_id, type, amount, description, merchant, is_income) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING", rows)
    await db.execute(
        """INSERT INTO profiles (id, first_name, last_name, dob, city, state, zip, household_size, insured, hospital,
                                 income_annual, income_source, fpl_pct, discount_pct, last_case_id, last_txn_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
           ON CONFLICT (id) DO UPDATE SET
             first_name = excluded.first_name, last_name = excluded.last_name, dob = excluded.dob,
             city = excluded.city, state = excluded.state, zip = excluded.zip,
             household_size = excluded.household_size, insured = excluded.insured, hospital = excluded.hospital,
             income_annual = excluded.income_annual, income_source = excluded.income_source,
             fpl_pct = excluded.fpl_pct, discount_pct = excluded.discount_pct, last_case_id = excluded.last_case_id,
             last_txn_at = greatest(profiles.last_txn_at, excluded.last_txn_at), updated_at = now()""",
        (pid, p.get("first_name"), p.get("last_name"), p.get("dob"), addr.get("city"), addr.get("state"),
         addr.get("zip"), elig["household_size"], insured, case["hospital"].get("name"), inc["annual"],
         inc["source"], elig["fpl_pct"], elig["discount_pct"], case["id"], max((r[0] for r in rows), default=None)))
    if rows:  # back-dated history lands behind the aggregate's watermark: materialize that window now
        lo, hi = min(r[0] for r in rows), max(r[0] for r in rows)
        await db.execute("CALL refresh_continuous_aggregate('monthly_cashflow', %s::timestamptz - INTERVAL '1 month', "
                         "%s::timestamptz + INTERVAL '1 month')", (lo, hi))
    return await _cashflow(db, pid)


async def _cashflow(db, pid: str, months: int = 12) -> list[dict]:
    cur = await db.execute(
        "SELECT month, income::float AS income, spending::float AS spending, paychecks FROM monthly_cashflow "
        "WHERE profile_id = %s ORDER BY month DESC LIMIT %s", (pid, months))
    return [{**r, "month": r["month"].date().isoformat()} for r in reversed(await cur.fetchall())]


async def cashflow(pid: str, months: int = 12) -> list[dict]:
    return await asyncio.wait_for(_cashflow(await _db(), pid, months), TIMEOUT) if enabled() else []


async def save_onboarding(answers: dict, assessment: dict, hospital: str | None) -> str | None:
    """Upsert what the patient told us at onboarding. Bank-derived columns (income_annual, fpl_pct from a case)
    are left alone: a later case fills them from the bank. Returns the profile id, None when Tiger is off."""
    if not enabled():
        return None
    pid = profile_id(answers)
    db = await _db()
    await asyncio.wait_for(db.execute(
        """INSERT INTO profiles (id, first_name, last_name, dob, zip, household_size, insured, hospital,
                                 income_reported, medical_oop, onboarding, onboarded_at, fpl_pct, discount_pct, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), %s, %s, now())
           ON CONFLICT (id) DO UPDATE SET
             zip = coalesce(excluded.zip, profiles.zip),
             household_size = coalesce(excluded.household_size, profiles.household_size),
             insured = coalesce(excluded.insured, profiles.insured),
             hospital = coalesce(excluded.hospital, profiles.hospital),
             income_reported = excluded.income_reported, medical_oop = excluded.medical_oop,
             onboarding = excluded.onboarding, onboarded_at = now(),
             fpl_pct = coalesce(profiles.fpl_pct, excluded.fpl_pct),
             discount_pct = coalesce(profiles.discount_pct, excluded.discount_pct), updated_at = now()""",
        (pid, answers.get("first_name"), answers.get("last_name"), answers.get("dob"), answers.get("zip"),
         answers.get("household_size"), answers.get("insured"), hospital, answers.get("income"),
         answers.get("medical_oop"), Jsonb({"answers": answers, "assessment": assessment}),
         round(sum(assessment["fpl_range"]) / 2, 1) if assessment.get("decided") and assessment.get("fpl_range") else None,
         assessment.get("discount_pct") if assessment.get("decided") else None)), TIMEOUT)
    return pid


async def get_profile(patient: dict) -> dict | None:
    """The stored profile for this patient (name + DOB), or None."""
    if not enabled():
        return None
    cur = await asyncio.wait_for((await _db()).execute(
        "SELECT id, household_size, insured, income_reported::float AS income_reported, onboarded_at "
        "FROM profiles WHERE id = %s", (profile_id(patient),)), TIMEOUT)
    return await cur.fetchone()
