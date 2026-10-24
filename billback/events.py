"""Per-case event bus + append-only audit log + case store.

Everything an agent does is an event: the dashboard renders them live over WebSocket,
and the same stream is written to data/<case_id>.audit.jsonl as the audit trail.
Case state is snapshotted to data/cases/<case_id>.json; on startup both are loaded back, so cases,
their call history and follow-ups survive restarts.
"""
import asyncio
import json
import time
from collections import defaultdict
from pathlib import Path
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

    def restore(self, case_id: str) -> None:
        """Reload a case's history from its audit log (after a restart)."""
        f = DATA_DIR / f"{case_id}.audit.jsonl"
        if case_id in self._history or not f.exists():
            return
        for line in f.read_text().splitlines():
            try:
                self._history[case_id].append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a half-written last line from a crash


bus = EventBus()


def _cases_dir() -> Path:
    d = DATA_DIR / "cases"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_case(case_id: str, state: dict) -> None:
    """Atomic snapshot: write to a temp file, then rename over the old one."""
    d = _cases_dir()
    tmp = d / f".{case_id}.json.tmp"
    tmp.write_text(json.dumps(state, default=str))
    tmp.replace(d / f"{case_id}.json")


def load_cases() -> list[dict]:
    out = []
    for f in sorted(_cases_dir().glob("*.json"), key=lambda f: f.stat().st_mtime):
        try:
            out.append(json.loads(f.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def delete_cases() -> None:
    for f in _cases_dir().glob("*.json"):
        f.unlink(missing_ok=True)


def step(case_id: str, agent: str, key: str, label: str, status: str, result: str = "") -> None:
    """A checklist item in the case UI. status: running | ok | flag | clear | win | wait."""
    bus.emit(case_id, "step", agent, key=f"{agent}:{key}", label=label, status=status, result=result)
