"""HTTP API over the policy database (mounted by billback/app.py; page: web/hospitals.html)."""
import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import llm
from ..auth import ADMIN
from ..integrations import brightdata
from . import compare as compare_mod
from . import monitor, pipeline
from .store import PolicyStore

router = APIRouter(prefix="/api", tags=["policies"])
_locks: dict[str, asyncio.Lock] = {}


def _summary(db: PolicyStore, hospital_id: str) -> dict | None:
    cur = db.current(hospital_id)
    if not cur:
        return None
    checks = db.checks(hospital_id)
    return {"version": cur["version"], "fetched_at": cur["fetched_at"], "status": cur["status"],
            "content_sha256": cur["content_sha256"], "sources": cur["source_urls"],
            "tiers": cur["extracted"]["tiers"], "tiers_verified": cur["tiers_verified"],
            "tiers_rejected": cur["tiers_rejected"], "notes": cur["extracted"].get("notes", ""),
            "last_checked": checks[0]["checked_at"] if checks else None}


@router.get("/hospitals")
async def hospitals():
    db = PolicyStore()
    return {"hospitals": [{"id": h.id, "name": h.name, "system": h.system, "city": h.city,
                           "versions": len(db.versions(h.id)), "current": _summary(db, h.id)} for h in db.hospitals()],
            "bright_data_requests": db.request_counts(),
            "can_recheck": brightdata.live() and llm.available()}


@router.get("/hospitals/{hospital_id}")
async def hospital(hospital_id: str):
    db = PolicyStore()
    h = db.hospital(hospital_id)
    if not h:
        raise HTTPException(404, "unknown hospital")
    versions = db.versions(hospital_id)
    return {"id": h.id, "name": h.name, "system": h.system, "city": h.city, "domains": h.domains,
            "aliases": h.aliases, "current": versions[0] if versions else None,
            "versions": [{k: v[k] for k in ("id", "version", "fetched_at", "content_sha256", "status", "tiers_verified",
                                            "tiers_rejected", "run_id", "source_urls")} for v in versions],
            "checks": db.checks(hospital_id), "crawl_log": db.crawl_log(hospital_id)}


@router.get("/hospitals/{hospital_id}/artifacts/{name}")
async def artifact(hospital_id: str, name: str):
    """Raw crawl evidence: what Bright Data returned (SERP JSON, page markdown, PDF and its extracted text)."""
    db = PolicyStore()
    base = (db.raw / hospital_id).resolve()
    path = (base / name).resolve()
    if base not in path.parents or not path.is_file():
        raise HTTPException(404, "no such artifact")
    media = {".md": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8", ".json": "application/json",
             ".pdf": "application/pdf"}.get(path.suffix, "application/octet-stream")
    return FileResponse(path, media_type=media)


@router.get("/compare")
async def compare(scenario: str = "default"):
    if scenario not in compare_mod.SCENARIOS:
        raise HTTPException(400, f"scenario must be one of {sorted(compare_mod.SCENARIOS)}")
    return compare_mod.compare(PolicyStore(), scenario)


@router.post("/hospitals/{hospital_id}/recheck", dependencies=ADMIN)
async def recheck(hospital_id: str):
    """Refetch through Bright Data Web Unlocker, compare content hashes, re-extract only if changed. A hospital
    stored with no verified tiers is crawled again from Google instead (refetching the same documents would find
    nothing new), with its URLs on record as the fallback."""
    if not (brightdata.live() and llm.available()):
        raise HTTPException(503, "Bright Data / Anthropic keys not configured on this server")
    db = PolicyStore()
    cur = db.current(hospital_id)
    if not cur:
        raise HTTPException(404, "hospital not crawled yet")
    if cur["status"] == "no_tiers" and not brightdata.serp_live():
        raise HTTPException(503, "a hospital with no tiers is crawled again from Google: SERP zone not configured")
    lock = _locks.setdefault(hospital_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, "a recheck is already running for this hospital")
    async with lock:
        try:
            if cur["status"] == "no_tiers":
                row = await pipeline.crawl_hospital(db, db.hospital(hospital_id), known=cur["discovery"])
                out = {"hospital_id": hospital_id, "recrawled": True, "changed": row["version"] != cur["version"],
                       "version": row["version"], "status": row["status"], "tiers": len(row["extracted"]["tiers"])}
            else:
                out = await monitor.recheck(db, hospital_id)
        except Exception as e:
            raise HTTPException(502, f"recheck failed: {type(e).__name__}: {e}")
        db.write_export()
    return out
