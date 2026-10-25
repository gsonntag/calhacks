"""A crawl session: every Bright Data request goes through here so it is logged (crawl_log), saved (raw/),
budgeted, and reported to the caller's event callback (the case UI shows it live).

Each request is reported twice: `brightdata_request` when it starts (n of the cap, product, URL) and `brightdata`
when it ends, with an outcome: found | empty | blocked | timeout | rate_limited | not_found | error. A timeout,
a 429 or a 5xx is retried once, in the open (a second numbered request), never inside the client; blocks and
empty answers are not retried, the stages fall back instead (next candidate page, a second Google query, the
URLs already on record). `HARD_CAP` bounds every crawl, retries included, whatever the caller asks for."""
import asyncio
import json
from typing import Callable

from ..integrations import brightdata
from ..integrations.brightdata import BDResponse, BrightDataError
from .store import PolicyStore, sha256

OnEvent = Callable[..., None]


HARD_CAP = 10  # Bright Data requests per crawl or recheck, retries included (a crawl normally uses 4-6)
pause = asyncio.sleep  # between a failure and its retry (tests replace it)
RETRYABLE = ("timeout", "rate_limited", "server_error")


class BudgetExceeded(RuntimeError):
    pass


def classify(e: BrightDataError) -> str:
    """What went wrong, in the words the case page shows."""
    msg = str(e).lower()
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if e.status in (401, 403, 451) or "blocked" in msg or "access denied" in msg or "captcha" in msg:
        return "blocked"
    if e.status == 429:
        return "rate_limited"
    if e.status == 404 or e.status == 410:
        return "not_found"
    if e.status >= 500:
        return "server_error"
    return "error"


def slim_serp(body: bytes) -> str:
    """SERP JSON without the base64 favicons (most of its bytes) or the request id (it names our account)."""
    data = json.loads(body)
    inner = json.loads(data["body"]) if isinstance(data.get("body"), str) else data

    def strip(o):
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if k not in ("icon", "image", "image_base64", "request_id")}
        return [strip(v) for v in o] if isinstance(o, list) else o
    return json.dumps(strip(inner), indent=1)


class Crawl:
    def __init__(self, store: PolicyStore, run_id: str, hospital_id: str, on_event: OnEvent | None = None,
                 max_requests: int = HARD_CAP):
        self.store, self.run_id, self.hospital_id = store, run_id, hospital_id
        self.on_event = on_event or (lambda kind, **data: None)
        self.max_requests, self.requests = min(max_requests, HARD_CAP), 0
        self.fallbacks = 0

    def emit(self, kind: str, **data) -> None:
        self.on_event(kind, **{"hospital_id": self.hospital_id, **data})

    def stage(self, stage: str, status: str, text: str, proof: list[tuple[str, str]] | None = None) -> None:
        self.emit("crawl_stage", stage=stage, status=status, text=text, proof=proof or [])

    def fallback(self, text: str) -> None:
        """A visible change of plan (a block, a timeout or an empty answer sent the crawl elsewhere)."""
        self.fallbacks += 1
        self.note("fallback", text)
        self.emit("crawl_fallback", n=self.fallbacks, text=text)

    def note(self, stage: str, text: str) -> None:
        """A decision worth keeping (why a URL was chosen, a rejected quote): logged without a request."""
        self.store.log(run_id=self.run_id, hospital_id=self.hospital_id, stage=stage, product=None, url="",
                       status=None, nbytes=None, latency_ms=None, ok=True, note=text)

    def save(self, name: str, data: bytes | str) -> str:
        return self.store.save_artifact(self.hospital_id, name, data)

    @property
    def left(self) -> int:
        return self.max_requests - self.requests

    async def _call(self, stage: str, product: str, url: str, fn, empty=None, retry: bool = False) -> BDResponse:
        """One logged request (two when a retryable failure is retried). empty(resp) -> True marks a reply that
        came back with nothing usable; the caller decides where to go next."""
        for attempt in (1, 2):
            if self.requests >= self.max_requests:
                self.emit("budget", cap=self.max_requests, url=url, product=product)
                raise BudgetExceeded(f"crawl budget of {self.max_requests} Bright Data requests reached")
            self.requests += 1
            n = self.requests
            self.emit("brightdata_request", n=n, cap=self.max_requests, product=product, stage=stage, url=url,
                      attempt=attempt)
            try:
                resp = await fn()
            except BrightDataError as e:
                kind = classify(e)
                again = retry and attempt == 1 and kind in RETRYABLE and self.requests < self.max_requests
                entry = self.store.log(run_id=self.run_id, hospital_id=self.hospital_id, stage=stage, product=product,
                                       url=url, status=e.status, nbytes=0, latency_ms=e.latency_ms, ok=False,
                                       note=f"{kind}: {e}" + (" (retrying)" if again else ""))
                self.emit("brightdata", **entry, n=n, cap=self.max_requests, outcome=kind, retrying=again,
                          error=str(e))
                if again:
                    await pause(16 if e.status == 429 and product == brightdata.SERP else 2)
                    continue
                raise
            kind = "empty" if empty and empty(resp) else "found"
            entry = self.store.log(run_id=self.run_id, hospital_id=self.hospital_id, stage=stage, product=product,
                                   url=url, status=resp.status, nbytes=len(resp.body), latency_ms=resp.latency_ms,
                                   ok=True, note=resp.content_type + (" (empty)" if kind == "empty" else ""))
            self.emit("brightdata", **entry, n=n, cap=self.max_requests, outcome=kind, retrying=False)
            return resp
        raise AssertionError("unreachable")

    async def serp(self, query: str) -> tuple[list[dict], BDResponse, str]:
        """Google results, the response, and the saved artifact path."""
        holder: dict = {}

        async def go():
            holder["results"], resp = await brightdata.serp(query, attempts=1)
            return resp
        resp = await self._call("discover", brightdata.SERP, f"google: {query}", go,
                                empty=lambda r: not holder.get("results"), retry=True)
        path = self.save(f"serp-{sha256(resp.body)[:12]}.json", slim_serp(resp.body))
        return holder["results"], resp, path

    async def page(self, url: str, stage: str = "fetch") -> BDResponse:
        return await self._call(stage, brightdata.UNLOCKER, url, lambda: brightdata.page(url, attempts=1),
                                empty=lambda r: len(r.body.strip()) < 200, retry=True)

    async def document(self, url: str, stage: str = "fetch") -> BDResponse:
        return await self._call(stage, brightdata.UNLOCKER, url, lambda: brightdata.document(url, attempts=1),
                                empty=lambda r: len(r.body) < 500, retry=True)
