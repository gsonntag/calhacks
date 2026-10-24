"""Per-case event bus + append-only audit log.

Everything an agent does is an event: the dashboard renders them live over WebSocket,
and the same stream is written to data/<case_id>.audit.jsonl as the audit trail.
"""
import asyncio
import json
import time
from collections import defaultdict
from typing import Any

from .config import DATA_DIR


class EventBus:
    def __init__(self) -> None:
        self._history: dict[str, list[dict]] = defaultdict(list)
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def emit(self, case_id: str, kind: str, agent: str = "orchestrator", **data: Any) -> dict:
        event = {"ts": time.time(), "case_id": case_id, "kind": kind, "agent": agent, **data}
        self._history[case_id].append(event)
        with open(DATA_DIR / f"{case_id}.audit.jsonl", "a") as f:
            f.write(json.dumps(event, default=str) + "\n")
        for q in list(self._subs[case_id]):
            q.put_nowait(event)
        return event

    def history(self, case_id: str) -> list[dict]:
        return list(self._history[case_id])

    def subscribe(self, case_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs[case_id].add(q)
        return q

    def unsubscribe(self, case_id: str, q: asyncio.Queue) -> None:
        self._subs[case_id].discard(q)

    def reset(self) -> None:
        self._history.clear()


bus = EventBus()


def step(case_id: str, agent: str, key: str, label: str, status: str, result: str = "") -> None:
    """A checklist item in the case UI. status: running | ok | flag | clear | win | wait."""
    bus.emit(case_id, "step", agent, key=f"{agent}:{key}", label=label, status=status, result=result)
