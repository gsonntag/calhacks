"""Capital One Nessie client + a local mirror used when there's no key or Nessie is down.

The dashboard labels which one is in use. Transactions are normalised to:
  {"id", "type": purchase|deposit|withdrawal|transfer, "date", "amount", "merchant", "description"}
"""
import asyncio
import json
import uuid
from typing import Any

import httpx

from .. import config

IDS_FILE = config.DATA_DIR / "nessie_ids.json"


class NessieError(RuntimeError):
    pass


class NessieClient:
    def __init__(self, key: str, base: str) -> None:
        self.key, self.base = key, base.rstrip("/")
        self._merchant_names: dict[str, str] = {}

    async def _req(self, method: str, path: str, body: dict | None = None) -> Any:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.request(method, f"{self.base}{path}", params={"key": self.key}, json=body)
        if r.status_code >= 400:
            raise NessieError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else None

    async def _create(self, post_path: str, body: dict, list_path: str, match: dict) -> dict:
        """The rewritten Nessie may answer a create with a bare string and no id, so if the
        response has no object we list the collection and pick the newest record matching `match`."""
        resp = await self._req("POST", post_path, body)
        if isinstance(resp, dict):
            obj = resp.get("objectCreated", resp)
            if isinstance(obj, dict) and obj.get("_id"):
                return obj
        items = await self._req("GET", list_path) or []
        hits = [i for i in items if all(i.get(k) == v for k, v in match.items())]
        if not hits:
            raise NessieError(f"created via {post_path} but could not find it in {list_path}")
        return hits[-1]

    # --- setup (used by scripts/seed_nessie.py) ---
    async def create_customer(self, first: str, last: str, address: dict) -> dict:
        return await self._create("/customers", {"first_name": first, "last_name": last, "address": address},
                                  "/customers", {"first_name": first, "last_name": last})

    async def create_account(self, customer_id: str, nickname: str, balance: int) -> dict:
        return await self._create(f"/customers/{customer_id}/accounts",
                                  {"type": "Checking", "nickname": nickname, "rewards": 0, "balance": balance},
                                  f"/customers/{customer_id}/accounts", {"nickname": nickname})

    async def create_merchant(self, name: str, category: str = "health") -> dict:
        return await self._create("/merchants", {"name": name, "category": category}, "/merchants", {"name": name})

    async def create_purchase(self, account_id: str, merchant_id: str, date: str, amount: float, description: str) -> dict:
        return await self._create(f"/accounts/{account_id}/purchases", {
            "merchant_id": merchant_id, "medium": "balance", "purchase_date": date,
            "amount": amount, "status": "completed", "description": description},
            f"/accounts/{account_id}/purchases", {"purchase_date": date, "description": description})

    async def create_deposit(self, account_id: str, date: str, amount: float, description: str) -> dict:
        return await self._create(f"/accounts/{account_id}/deposits", {
            "medium": "balance", "transaction_date": date, "status": "completed",
            "amount": amount, "description": description},
            f"/accounts/{account_id}/deposits", {"transaction_date": date, "description": description})

    async def create_withdrawal(self, account_id: str, date: str, amount: float, description: str) -> dict:
        return await self._create(f"/accounts/{account_id}/withdrawals", {
            "medium": "balance", "transaction_date": date, "status": "completed",
            "amount": amount, "description": description},
            f"/accounts/{account_id}/withdrawals", {"transaction_date": date, "description": description})

    # --- used by agents ---
    async def create_bill(self, account_id: str, payee: str, nickname: str, amount: float, payment_date: str,
                          status: str = "pending", recurring_date: int | None = None) -> dict:
        # Nessie's list endpoint errors on any bill created without recurring_date (the server only derives
        # upcoming_payment_date when it's present), so always send it, even for one-off bills.
        body = {"status": status, "payee": payee, "nickname": nickname, "payment_date": payment_date,
                "payment_amount": amount, "recurring_date": recurring_date or int(payment_date[8:10])}
        return await self._create(f"/accounts/{account_id}/bills", body, f"/accounts/{account_id}/bills",
                                  {"nickname": nickname})

    async def update_bill(self, bill_id: str, **fields: Any) -> Any:
        return await self._req("PUT", f"/bills/{bill_id}", fields)

    async def list_bills(self, account_id: str) -> list[dict]:
        return await self._req("GET", f"/accounts/{account_id}/bills") or []

    async def find_account(self, first: str, last: str) -> dict | None:
        """The patient's checking account, found by the name on their bill: the newest Nessie customer with
        that name that has an account. Stands in for the patient linking their bank (Plaid/MX in production)."""
        norm = lambda x: " ".join(str(x or "").lower().split())
        custs = [c for c in await self._req("GET", "/customers") or []
                 if norm(c.get("first_name")) == norm(first) and norm(c.get("last_name")) == norm(last)]
        for c in reversed(custs):
            accts = await self._req("GET", f"/customers/{c['_id']}/accounts") or []
            if accts:
                return {"customer_id": c["_id"], "account_id": accts[-1]["_id"]}
        return None

    async def delete_account(self, account_id: str) -> None:
        await self._req("DELETE", f"/accounts/{account_id}")

    async def _merchant_name(self, merchant_id: str) -> str:
        if merchant_id not in self._merchant_names:
            try:
                m = await self._req("GET", f"/merchants/{merchant_id}")
                self._merchant_names[merchant_id] = m.get("name", merchant_id)
            except NessieError:
                self._merchant_names[merchant_id] = merchant_id
        return self._merchant_names[merchant_id]

    async def transactions(self, account_id: str) -> list[dict]:
        out = []
        for p in await self._req("GET", f"/accounts/{account_id}/purchases") or []:
            out.append({"id": p["_id"], "type": "purchase", "date": p.get("purchase_date", ""),
                        "amount": float(p["amount"]), "merchant": await self._merchant_name(p.get("merchant_id", "")),
                        "description": p.get("description", "")})
        for kind in ("deposits", "withdrawals"):
            for d in await self._req("GET", f"/accounts/{account_id}/{kind}") or []:
                out.append({"id": d["_id"], "type": kind[:-1], "date": d.get("transaction_date", ""),
                            "amount": float(d["amount"]), "merchant": "", "description": d.get("description", "")})
        return sorted(out, key=lambda t: t["date"])


class LocalMirror:
    """Same interface, backed by a sample case's transactions (demo/case.json or a generated scenario). Used
    offline ('sandbox mirror' in the UI), and as the fallback when Nessie has no account for the patient or
    doesn't answer. Holds the hospital bill the way scripts/seed_nessie.py writes it, so bill updates work too."""

    def __init__(self, demo_case: dict) -> None:
        self.txns = [{"id": f"mirror-{i:03d}", "merchant": "", **t} for i, t in enumerate(demo_case["transactions"])]
        self.bills: dict[str, dict] = {}
        b = demo_case.get("bill") or {}
        if b.get("provider") and b.get("account_number"):
            self.bills["mirror-bill-hospital"] = {
                "_id": "mirror-bill-hospital", "payee": b["provider"], "nickname": hospital_bill_nickname(b),
                "payment_amount": b.get("amount_due"), "payment_date": b.get("due_date"), "status": "pending"}

    async def transactions(self, account_id: str) -> list[dict]:
        return list(self.txns)

    async def create_bill(self, account_id: str, payee: str, nickname: str, amount: float, payment_date: str,
                          status: str = "pending", recurring_date: int | None = None) -> dict:
        bid = f"mirror-bill-{uuid.uuid4().hex[:6]}"
        self.bills[bid] = {"_id": bid, "payee": payee, "nickname": nickname, "payment_amount": amount,
                           "payment_date": payment_date, "status": status, "recurring_date": recurring_date}
        return self.bills[bid]

    async def update_bill(self, bill_id: str, **fields: Any) -> dict:
        self.bills.setdefault(bill_id, {"_id": bill_id}).update(fields)
        return self.bills[bill_id]

    async def list_bills(self, account_id: str) -> list[dict]:
        return list(self.bills.values())


def ids_file(scenario: str = "default"):
    return IDS_FILE if scenario == "default" else config.DATA_DIR / f"nessie_ids_{scenario}.json"


def load_ids(scenario: str = "default") -> dict:
    f = ids_file(scenario)
    return json.loads(f.read_text()) if f.exists() else {}


LIVE_TIMEOUT = 20  # seconds to find the account before using sample data
HISTORY_TIMEOUT = 60  # seconds to pull its history (one request per merchant name, so it takes longer)
LIVE_ERRORS = (NessieError, httpx.HTTPError, asyncio.TimeoutError, KeyError, TypeError, ValueError)
LIVE_LABEL = "Capital One Nessie (live)"
SAMPLE_LABEL = "Sample bank data"


class FallbackBank:
    """Live Nessie while it answers; the sample transactions (LocalMirror) once it doesn't, with `label` and
    `notice` saying so. After a fallback, bill writes go to the sample bank too: nothing claims to be saved in
    Capital One when it isn't."""

    def __init__(self, live: NessieClient, sample: dict, sample_whose: str) -> None:
        self.live: NessieClient | None = live
        self.live_account = ""
        self.mirror = LocalMirror(sample)
        self.sample_whose = sample_whose
        self.label, self.notice = LIVE_LABEL, ""

    def fall_back(self, notice: str) -> None:
        self.live, self.notice = None, notice
        self.label = f"{SAMPLE_LABEL} ({self.sample_whose})"

    async def transactions(self, account_id: str) -> list[dict]:
        if self.live:
            try:
                return await asyncio.wait_for(self.live.transactions(self.live_account), HISTORY_TIMEOUT)
            except LIVE_ERRORS as e:
                self.fall_back(f"Capital One (Nessie) didn't answer ({_why(e)}) — using sample bank data")
        return await self.mirror.transactions("mirror")

    async def create_bill(self, account_id: str, *args: Any, **kw: Any) -> dict:
        if self.live:
            return await self.live.create_bill(self.live_account, *args, **kw)
        return await self.mirror.create_bill("mirror", *args, **kw)

    async def update_bill(self, bill_id: str, **fields: Any) -> Any:
        return await (self.live or self.mirror).update_bill(bill_id, **fields)

    async def list_bills(self, account_id: str) -> list[dict]:
        return await (self.live.list_bills(self.live_account) if self.live else self.mirror.list_bills("mirror"))


def _why(e: BaseException) -> str:
    return "timed out" if isinstance(e, asyncio.TimeoutError) else (str(e)[:80] or type(e).__name__)


def hospital_bill_nickname(bill: dict) -> str:
    """How scripts/seed_nessie.py names the hospital bill in Nessie; the account number finds it again."""
    return f"ER visit {bill.get('service_date', '')} ({bill['account_number']})"


def find_hospital_bill(bills: list[dict], bill: dict) -> dict | None:
    """The hospital bill among the account's Nessie bills: its nickname carries the bill's account number.
    Payment plans (agents/planner.py) carry it too, so they're skipped."""
    acct = bill.get("account_number")
    hits = [b for b in bills if acct and acct in str(b.get("nickname") or "")
            and " plan " not in f" {b.get('nickname')} "]
    return hits[-1] if hits else None


async def mark_bill_corrected(bank: Any, account_id: str, bill: dict, confirmed: float, when: str) -> dict | None:
    """Billing confirmed a corrected balance on a call: update the hospital bill in the patient's bank to it
    (cancelled when nothing is left to pay). Returns what changed, or None when the account has no such bill."""
    found = find_hospital_bill(await bank.list_bills(account_id), bill)
    if not found:
        return None
    was = found.get("payment_amount")
    base = str(found.get("nickname") or hospital_bill_nickname(bill)).split(" · ")[0]
    fields: dict[str, Any] = {"payment_amount": round(float(confirmed), 2), "nickname": f"{base} · corrected by billing {when}"}
    if confirmed <= 0:
        fields["status"] = "cancelled"
    await bank.update_bill(found["_id"], **fields)
    return {"bill_id": found["_id"], "was": was, **fields}


def sample_for(case: dict) -> tuple[dict, bool]:
    """Sample bank data for this case: the demo case or generated scenario (demo/scenarios/) with the same
    patient name when there is one, else the case's own scenario. Returns (sample case, same patient?)."""
    from ..orchestrator import demo_case
    norm = lambda x: " ".join(str(x or "").lower().split())  # noqa: E731
    p = case.get("patient") or {}
    who = (norm(p.get("first_name")), norm(p.get("last_name")))
    files = sorted(config.DEMO_DIR.glob("case*.json")) + sorted((config.DEMO_DIR / "scenarios").glob("*/case.json"))
    for f in files:
        try:
            c = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        cp = c.get("patient") or {}
        if who[0] and (norm(cp.get("first_name")), norm(cp.get("last_name"))) == who and c.get("transactions"):
            return c, True
    return demo_case(case.get("scenario", "default")), False


async def get_bank(case: dict) -> tuple[Any, str, str]:
    """Returns (client, account_id, label). Live: the Nessie account of the patient named on the bill.
    Offline (no Nessie key): the scenario's sample transactions, labelled as a mirror.
    Live, but Nessie has no account for the patient or doesn't answer: the sample transactions (the patient's
    own sample when demo/ has one), labelled as sample data; the bank's `notice` says why, and the Nessie agent
    shows it on the case page."""
    if not config.NESSIE_API_KEY:
        from ..orchestrator import demo_case  # offline only: sample transactions
        return LocalMirror(demo_case(case.get("scenario", "default"))), "mirror", "Nessie sandbox mirror (offline)"
    client = NessieClient(config.NESSIE_API_KEY, config.NESSIE_BASE_URL)
    p = case["patient"]
    sample, same = sample_for(case)
    first = p.get("first_name") or "you"
    bank = FallbackBank(client, sample, "sample history" if same
                        else f"not {first}'s: {(sample.get('patient') or {}).get('first_name', 'another patient')}'s sample")
    try:
        found = await asyncio.wait_for(client.find_account(p["first_name"], p["last_name"]), LIVE_TIMEOUT)
    except LIVE_ERRORS as e:
        bank.fall_back(f"Capital One (Nessie) didn't answer ({_why(e)}) — using sample bank data")
        return bank, "mirror", bank.label
    if not found:
        bank.fall_back(f"No Capital One account for {first} — using sample bank data")
        return bank, "mirror", bank.label
    bank.live_account = found["account_id"]
    return bank, found["account_id"], bank.label
