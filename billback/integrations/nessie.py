"""Capital One Nessie client + a local mirror used when there's no key or Nessie is down.

The dashboard labels which one is in use. Transactions are normalised to:
  {"id", "type": purchase|deposit|withdrawal|transfer, "date", "amount", "merchant", "description"}
"""
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
    """Same interface, backed by demo/case.json. Used offline; labelled 'sandbox mirror' in the UI."""

    def __init__(self, demo_case: dict) -> None:
        self.txns = [{"id": f"mirror-{i:03d}", "merchant": "", **t} for i, t in enumerate(demo_case["transactions"])]
        self.bills: dict[str, dict] = {}

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


def get_bank(demo_case: dict) -> tuple[Any, str, str]:
    """Returns (client, account_id, label). Each demo scenario has its own seeded Nessie account."""
    ids = load_ids(demo_case.get("scenario", "default"))
    if config.NESSIE_API_KEY and ids.get("account_id"):
        return NessieClient(config.NESSIE_API_KEY, config.NESSIE_BASE_URL), ids["account_id"], "Capital One Nessie (live)"
    return LocalMirror(demo_case), "mirror", "Nessie sandbox mirror (offline)"
