"""HTTP API over the policy database (mounted by remy/app.py; page: web/hospitals.html)."""
import asyncio
import json
from dataclasses import replace
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .. import config, examples, llm
from ..agents import policy as policy_agent
from ..auth import ADMIN
from ..integrations import brightdata
from . import compare as compare_mod
from . import monitor, pipeline, registry
from .crawl import HARD_CAP
from .store import PolicyStore

router = APIRouter(prefix="/api", tags=["policies"])
_locks: dict[str, asyncio.Lock] = {}
_running: set[asyncio.Task] = set()  # "Add a hospital" crawls (a reference, so they aren't garbage-collected)
ADD_TIMEOUT = policy_agent.LIVE_CRAWL_TIMEOUT
HEARTBEAT = 10  # seconds between keep-alive comments while Claude reads (keeps tunnels from closing the stream)


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
            "can_recheck": (brightdata.live() and llm.available()) or config.OFFLINE,
            "can_add": brightdata.live() and brightdata.serp_live() and llm.available()}


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
    if not examples.exists(scenario):
        raise HTTPException(400, f"unknown scenario: {scenario} (GET /api/examples lists the demo patients)")
    return compare_mod.compare(PolicyStore(), scenario)


@router.post("/hospitals/{hospital_id}/recheck", dependencies=ADMIN)
async def recheck(hospital_id: str):
    """Refetch through Bright Data Web Unlocker, compare content hashes, re-extract only if changed. A hospital
    stored with no verified tiers is crawled again from Google instead (refetching the same documents would find
    nothing new), with its URLs on record as the fallback."""
    if config.OFFLINE:  # no-wifi demo: compared with the stored copy, nothing fetched
        cur = PolicyStore().current(hospital_id)
        if not cur:
            raise HTTPException(404, "hospital not crawled yet")
        return {"hospital_id": hospital_id, "changed": False, "version": cur["version"], "requests": 0, "offline": True}
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


class NewHospital(BaseModel):
    name: str
    city: str = ""
    website: str = ""


def _domain(website: str) -> str:
    w = website.strip()
    if not w:
        return ""
    host = (urlparse(w if "//" in w else "https://" + w).hostname or "").lower().removeprefix("www.")
    if "." not in host or len(host) > 100:
        raise HTTPException(400, "That website doesn't look like a web address (for example: examplehospital.org)")
    return host


def _new_hospital(db: PolicyStore, name: str, city: str, domain: str) -> registry.Hospital:
    """A curated SEED entry when the name is one of ours (better query and domains), else one built from the
    form. Its id never takes over another hospital's."""
    seed = registry.match(name, list(registry.SEED))
    if seed and not db.hospital(seed.id):
        return seed
    base = registry.slug(name)
    taken = {h.id for h in db.hospitals()}
    hid = base if base not in taken else (f"{base}-{registry.slug(city)}" if city else f"{base}-2")
    n = 2
    while hid in taken:
        n += 1
        hid = f"{base}-{n}"
    return registry.Hospital(hid, name, city=city, domains=(domain,) if domain else ())


def _sse(kind: str, data: dict) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, default=str)}\n\n"


@router.post("/hospitals", dependencies=ADMIN)
async def add_hospital(body: NewHospital):
    """Add any US hospital: a hospital already in the database is returned as is (JSON, matched: true). Any other
    is crawled live (SERP API -> Web Unlocker -> Claude -> quote verification, at most HARD_CAP Bright Data
    requests) and the response is an event stream: `line` events (the same lines as the case page's Bright Data
    panel, same key = same line), then one `done` event (the stored result) or one `error` event."""
    name, city = " ".join(body.name.split()), " ".join(body.city.split())[:80]
    if not 3 <= len(name) <= 120:
        raise HTTPException(400, "Please enter the hospital's name (3 to 120 characters)")
    domain = _domain(body.website)
    db = PolicyStore()
    found = registry.match(name, db.hospitals())
    if found and db.current(found.id):
        return {"matched": True, "hospital_id": found.id, "name": found.name, **pipeline.summary(db.current(found.id))}
    if not (brightdata.live() and brightdata.serp_live() and llm.available()):
        raise HTTPException(503, "Adding a hospital needs Bright Data keys (not set on this server)")
    if found:  # added before, but no policy stored yet (an earlier attempt failed): try again, with what's new
        h = replace(found, city=found.city or city, domains=found.domains or ((domain,) if domain else ()))
    else:
        h = _new_hospital(db, name, city, domain)
    lock = _locks.setdefault(h.id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, f"{h.name} is already being added")
    queue: asyncio.Queue = asyncio.Queue()
    used = {"requests": 0}

    def on_event(kind: str, **e) -> None:
        if kind == "brightdata_request":
            used["requests"] = e["n"]
        ln = policy_agent.crawl_line(kind, **e)
        if ln:
            queue.put_nowait(("line", ln))

    async def run() -> None:
        """Runs to the end even if the browser goes away: the hospital is still saved."""
        async with lock:
            try:
                row = await asyncio.wait_for(pipeline.crawl_hospital(db, h, on_event=on_event, max_requests=HARD_CAP),
                                             ADD_TIMEOUT)
            except Exception as e:
                db.add_check(h.id, None, False, None, f"add hospital: crawl failed: {type(e).__name__}: {e}", None)
                queue.put_nowait(("error", {"hospital_id": h.id, "name": h.name, "requests": used["requests"],
                                            "detail": f"Couldn't add {h.name}: {_plain(e)}"}))
                return
            db.write_export()
            queue.put_nowait(("done", {"matched": False, "hospital_id": h.id, "name": h.name,
                                       "requests": used["requests"],
                                       "cap": HARD_CAP, **pipeline.summary(row)}))

    task = asyncio.create_task(run())
    _running.add(task)
    task.add_done_callback(_running.discard)

    async def stream():
        yield _sse("start", {"hospital_id": h.id, "name": h.name, "cap": HARD_CAP})
        while True:
            try:
                kind, data = await asyncio.wait_for(queue.get(), HEARTBEAT)
            except asyncio.TimeoutError:
                if task.done() and queue.empty():
                    return
                yield ": still working\n\n"
                continue
            yield _sse(kind, data)
            if kind in ("done", "error"):
                return

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _plain(e: Exception) -> str:
    """A failed crawl in words a patient can read (the full error is in the crawl log)."""
    if isinstance(e, asyncio.TimeoutError):
        return f"the crawl took longer than {ADD_TIMEOUT // 60} minutes."
    msg = str(e)
    if isinstance(e, brightdata.BrightDataError):
        return (f"Bright Data couldn't be reached ({f'HTTP {e.status}' if e.status else 'no answer'}). "
                "Please try again in a minute.")
    if "no official policy page" in msg:
        return "we couldn't find its financial-assistance policy on its own website. Try adding its website."
    if "budget" in msg:
        return f"we used all {HARD_CAP} Bright Data requests without finding a usable policy."
    if "could be fetched" in msg:
        return "Bright Data couldn't fetch its policy pages (the site may be blocking automated access)."
    return f"something went wrong while crawling ({type(e).__name__}: {msg[:200]})."
