"""A crawl session: every Bright Data request goes through here so it is logged (crawl_log), saved (raw/),
budgeted, and reported to the caller's event callback (the case UI shows it live)."""
import json
from typing import Callable

from ..integrations import brightdata
from ..integrations.brightdata import BDResponse, BrightDataError
from .store import PolicyStore, sha256

OnEvent = Callable[..., None]


class BudgetExceeded(RuntimeError):
    pass


def slim_serp(body: bytes) -> str:
    """SERP JSON without the base64 favicons, which are most of its bytes."""
    data = json.loads(body)
    inner = json.loads(data["body"]) if isinstance(data.get("body"), str) else data

    def strip(o):
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if k not in ("icon", "image", "image_base64")}
        return [strip(v) for v in o] if isinstance(o, list) else o
    return json.dumps(strip(inner), indent=1)


class Crawl:
    def __init__(self, store: PolicyStore, run_id: str, hospital_id: str, on_event: OnEvent | None = None,
                 max_requests: int = 20):
        self.store, self.run_id, self.hospital_id = store, run_id, hospital_id
        self.on_event = on_event or (lambda kind, **data: None)
        self.max_requests, self.requests = max_requests, 0

    def emit(self, kind: str, **data) -> None:
        self.on_event(kind, **{"hospital_id": self.hospital_id, **data})

    def stage(self, stage: str, status: str, text: str) -> None:
        self.emit("crawl_stage", stage=stage, status=status, text=text)

    def note(self, stage: str, text: str) -> None:
        """A decision worth keeping (why a URL was chosen, a rejected quote): logged without a request."""
        self.store.log(run_id=self.run_id, hospital_id=self.hospital_id, stage=stage, product=None, url="",
                       status=None, nbytes=None, latency_ms=None, ok=True, note=text)

    def save(self, name: str, data: bytes | str) -> str:
        return self.store.save_artifact(self.hospital_id, name, data)

    async def _call(self, stage: str, product: str, url: str, fn) -> BDResponse:
        if self.requests >= self.max_requests:
            raise BudgetExceeded(f"crawl budget of {self.max_requests} Bright Data requests reached")
        self.requests += 1
        try:
            resp = await fn()
        except BrightDataError as e:
            entry = self.store.log(run_id=self.run_id, hospital_id=self.hospital_id, stage=stage, product=product,
                                   url=url, status=e.status, nbytes=0, latency_ms=e.latency_ms, ok=False, note=str(e))
            self.emit("brightdata", **entry)
            raise
        entry = self.store.log(run_id=self.run_id, hospital_id=self.hospital_id, stage=stage, product=product, url=url,
                               status=resp.status, nbytes=len(resp.body), latency_ms=resp.latency_ms, ok=True,
                               note=resp.content_type)
        self.emit("brightdata", **entry)
        return resp

    async def serp(self, query: str) -> tuple[list[dict], BDResponse, str]:
        """Google results, the response, and the saved artifact path."""
        holder: dict = {}

        async def go():
            holder["results"], resp = await brightdata.serp(query)
            return resp
        resp = await self._call("discover", brightdata.SERP, f"google: {query}", go)
        path = self.save(f"serp-{sha256(resp.body)[:12]}.json", slim_serp(resp.body))
        return holder["results"], resp, path

    async def page(self, url: str, stage: str = "fetch") -> BDResponse:
        return await self._call(stage, brightdata.UNLOCKER, url, lambda: brightdata.page(url))

    async def document(self, url: str, stage: str = "fetch") -> BDResponse:
        return await self._call(stage, brightdata.UNLOCKER, url, lambda: brightdata.document(url))
