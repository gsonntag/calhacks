"""SQLite policy database + raw crawl artifacts. Both live in policydb/ and are committed as evidence.

    policydb/policies.sqlite3          hospitals, policy_versions, policy_checks, crawl_runs, crawl_log
    policydb/raw/<hospital>/...        what Bright Data returned: SERP JSON, page markdown, PDF bytes/text
    policydb/policies.json             readable export of the database (regenerated after every crawl)
"""
import hashlib
import json
import os
import sqlite3
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from .registry import Hospital

DEFAULT_DIR = Path(os.getenv("BILLBACK_POLICYDB", config.ROOT / "policydb"))
MAX_BINARY = 2 * 1024 * 1024  # PDFs above this keep only extracted text + sha256

SCHEMA = """
CREATE TABLE IF NOT EXISTS hospitals (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, system TEXT, city TEXT,
  domains TEXT NOT NULL DEFAULT '[]', aliases TEXT NOT NULL DEFAULT '[]', query TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS policy_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, hospital_id TEXT NOT NULL REFERENCES hospitals(id),
  version INTEGER NOT NULL, fetched_at TEXT NOT NULL, content_sha256 TEXT NOT NULL,
  source_urls TEXT NOT NULL, documents TEXT NOT NULL, discovery TEXT, extracted TEXT NOT NULL,
  verification TEXT NOT NULL, tiers_verified INTEGER NOT NULL, tiers_rejected INTEGER NOT NULL,
  status TEXT NOT NULL, run_id TEXT, is_current INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS policy_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, hospital_id TEXT NOT NULL, checked_at TEXT NOT NULL,
  content_sha256 TEXT, changed INTEGER NOT NULL, version_id INTEGER, note TEXT, run_id TEXT);
CREATE TABLE IF NOT EXISTS crawl_runs (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, hospitals TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS crawl_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, hospital_id TEXT, ts TEXT NOT NULL, stage TEXT NOT NULL,
  product TEXT, url TEXT, status INTEGER, bytes INTEGER, latency_ms INTEGER, ok INTEGER NOT NULL, note TEXT);
"""
JSON_COLS = {"domains", "aliases", "source_urls", "documents", "discovery", "extracted", "verification",
             "hospitals", "summary"}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    return {k: (json.loads(r[k]) if k in JSON_COLS and r[k] is not None else r[k]) for k in r.keys()}


class PolicyStore:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root or DEFAULT_DIR)
        self.raw = self.root / "raw"
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "policies.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # hospitals
    def upsert_hospital(self, h: Hospital) -> None:
        self.db.execute(
            "INSERT INTO hospitals (id, name, system, city, domains, aliases, query, created_at) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, system=excluded.system, city=excluded.city, "
            "domains=excluded.domains, aliases=excluded.aliases, query=excluded.query",
            (h.id, h.name, h.system, h.city, json.dumps(list(h.domains)), json.dumps(list(h.aliases)), h.query, now()))
        self.db.commit()

    def hospitals(self) -> list[Hospital]:
        rows = [_row(r) for r in self.db.execute("SELECT * FROM hospitals ORDER BY id")]
        return [Hospital(r["id"], r["name"], r["system"] or "", r["city"] or "", tuple(r["domains"]),
                         tuple(r["aliases"]), r["query"] or "") for r in rows]

    def hospital(self, hospital_id: str) -> Hospital | None:
        return next((h for h in self.hospitals() if h.id == hospital_id), None)

    # raw artifacts
    def save_artifact(self, hospital_id: str, name: str, data: bytes | str) -> str:
        """Write under raw/<hospital>/ and return the path relative to the store root."""
        path = self.raw / hospital_id / name
        path.parent.mkdir(parents=True, exist_ok=True)
        (path.write_bytes if isinstance(data, bytes) else path.write_text)(data)
        return str(path.relative_to(self.root))

    def read_artifact(self, rel: str) -> str:
        return (self.root / rel).read_text()

    # versions
    def current(self, hospital_id: str) -> dict | None:
        return _row(self.db.execute("SELECT * FROM policy_versions WHERE hospital_id=? AND is_current=1 "
                                    "ORDER BY version DESC LIMIT 1", (hospital_id,)).fetchone())

    def versions(self, hospital_id: str) -> list[dict]:
        return [_row(r) for r in self.db.execute(
            "SELECT * FROM policy_versions WHERE hospital_id=? ORDER BY version DESC", (hospital_id,))]

    def add_version(self, hospital_id: str, *, content_sha256: str, documents: list[dict], discovery: dict | None,
                    extracted: dict, verification: dict, status: str, run_id: str | None) -> dict:
        prev = self.current(hospital_id)
        version = (prev["version"] + 1) if prev else 1
        self.db.execute("UPDATE policy_versions SET is_current=0 WHERE hospital_id=?", (hospital_id,))
        cur = self.db.execute(
            "INSERT INTO policy_versions (hospital_id, version, fetched_at, content_sha256, source_urls, documents, "
            "discovery, extracted, verification, tiers_verified, tiers_rejected, status, run_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (hospital_id, version, now(), content_sha256, json.dumps([d["url"] for d in documents]),
             json.dumps(documents), json.dumps(discovery), json.dumps(extracted), json.dumps(verification),
             verification.get("tiers_verified", 0), verification.get("tiers_rejected", 0), status, run_id))
        self.db.commit()
        return _row(self.db.execute("SELECT * FROM policy_versions WHERE id=?", (cur.lastrowid,)).fetchone())

    def add_check(self, hospital_id: str, content_sha256: str | None, changed: bool, version_id: int | None,
                  note: str, run_id: str | None) -> None:
        self.db.execute("INSERT INTO policy_checks (hospital_id, checked_at, content_sha256, changed, version_id, note, "
                        "run_id) VALUES (?,?,?,?,?,?,?)",
                        (hospital_id, now(), content_sha256, int(changed), version_id, note, run_id))
        self.db.commit()

    def checks(self, hospital_id: str) -> list[dict]:
        return [_row(r) for r in self.db.execute(
            "SELECT * FROM policy_checks WHERE hospital_id=? ORDER BY id DESC", (hospital_id,))]

    # crawl runs + request log
    def start_run(self, kind: str, hospital_ids: list[str]) -> str:
        run_id = f"{kind}-{time.strftime('%Y%m%dT%H%M%S')}-{sha256(str(time.time_ns()))[:6]}"
        self.db.execute("INSERT INTO crawl_runs (id, kind, started_at, hospitals) VALUES (?,?,?,?)",
                        (run_id, kind, now(), json.dumps(hospital_ids)))
        self.db.commit()
        return run_id

    def finish_run(self, run_id: str, summary: dict) -> None:
        self.db.execute("UPDATE crawl_runs SET finished_at=?, summary=? WHERE id=?", (now(), json.dumps(summary), run_id))
        self.db.commit()

    def log(self, *, run_id: str | None, hospital_id: str | None, stage: str, product: str | None, url: str,
            status: int | None, nbytes: int | None, latency_ms: int | None, ok: bool, note: str = "") -> dict:
        entry = {"run_id": run_id, "hospital_id": hospital_id, "ts": now(), "stage": stage, "product": product,
                 "url": url, "status": status, "bytes": nbytes, "latency_ms": latency_ms, "ok": int(ok), "note": note}
        self.db.execute(f"INSERT INTO crawl_log ({', '.join(entry)}) VALUES ({', '.join('?' * len(entry))})",
                        tuple(entry.values()))
        self.db.commit()
        return entry

    def crawl_log(self, hospital_id: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM crawl_log", ()
        if hospital_id:
            q, args = q + " WHERE hospital_id=?", (hospital_id,)
        return [_row(r) for r in self.db.execute(q + " ORDER BY id", args)]

    def runs(self) -> list[dict]:
        return [_row(r) for r in self.db.execute("SELECT * FROM crawl_runs ORDER BY started_at")]

    def request_counts(self) -> dict:
        return {r["product"]: r["n"] for r in self.db.execute(
            "SELECT product, COUNT(*) AS n FROM crawl_log WHERE product IS NOT NULL GROUP BY product")}

    # export
    def export(self) -> dict:
        out = {"exported_at": now(), "bright_data_requests": self.request_counts(), "runs": self.runs(),
               "hospitals": []}
        for h in self.hospitals():
            out["hospitals"].append({**asdict(h), "versions": self.versions(h.id), "checks": self.checks(h.id),
                                     "crawl_log": self.crawl_log(h.id)})
        return out

    def write_export(self) -> Path:
        path = self.root / "policies.json"
        path.write_text(json.dumps(self.export(), indent=1, default=str))
        return path

    def close(self) -> None:
        self.db.close()
