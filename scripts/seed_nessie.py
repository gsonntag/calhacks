"""Seed the synthetic demo patient into Capital One Nessie.

    uv run python scripts/seed_nessie.py            # main demo patient
    uv run python scripts/seed_nessie.py freecare   # second demo patient

Creates customer, checking account, merchants, deposits, purchases and the hospital bill,
then writes the ids to data/nessie_ids.json (the app reads the account id from there).
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from billback import config  # noqa: E402
from billback.integrations.nessie import NessieClient, ids_file  # noqa: E402
from billback.orchestrator import SCENARIOS  # noqa: E402


async def main() -> None:
    if not config.NESSIE_API_KEY:
        sys.exit("Set NESSIE_API_KEY in .env first")
    scenario = sys.argv[1] if len(sys.argv) > 1 else "default"
    case = json.loads((config.DEMO_DIR / SCENARIOS[scenario]).read_text())
    n = NessieClient(config.NESSIE_API_KEY, config.NESSIE_BASE_URL)
    p = case["patient"]

    cust = await n.create_customer(p["first_name"], p["last_name"], p["address"])
    acct = await n.create_account(cust["_id"], f"{p['first_name']} checking", 2400)
    print("customer", cust["_id"], "account", acct["_id"])

    merchants: dict[str, str] = {}
    for t in case["transactions"]:
        if t["type"] == "purchase" and t["merchant"] not in merchants:
            m = await n.create_merchant(t["merchant"])
            merchants[t["merchant"]] = m["_id"]

    for t in case["transactions"]:
        if t["type"] == "deposit":
            r = await n.create_deposit(acct["_id"], t["date"], t["amount"], t["description"])
        else:
            r = await n.create_purchase(acct["_id"], merchants[t["merchant"]], t["date"], t["amount"], t["description"])
        print(f"  {t['type']:<8} {t['date']} {t['amount']:>9.2f}  {r.get('_id')}")

    b = case["bill"]
    bill = await n.create_bill(acct["_id"], b["provider"], f"ER visit {b['service_date']} ({b['account_number']})",
                               b["amount_due"], b["due_date"])
    print("bill", bill.get("_id"))

    out = ids_file(scenario)
    out.write_text(json.dumps({"customer_id": cust["_id"], "account_id": acct["_id"],
                                    "hospital_bill_id": bill.get("_id"), "merchants": merchants}, indent=2))
    print("wrote", out)


if __name__ == "__main__":
    asyncio.run(main())
