"""BillBack agent harness.

- Tools are registered with a permission tier:
    read           runs freely (lookups, scraping)
    act            runs, but is announced on the dashboard (placing a call)
    consequential  blocks until the patient approves on their phone (payment plans, agreeing to pay)
- Every tool call, result, and approval is emitted as an event (and so lands in the audit log).
- Agent wraps a Claude tool-use loop over a ToolRegistry.
"""
import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from . import llm
from .events import bus

Tier = Literal["read", "act", "consequential"]


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    fn: Callable[..., Awaitable[Any]]
    tier: Tier = "read"
    approval_title: Callable[[dict], str] | None = None  # patient-facing title for consequential tools

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


class ApprovalGate:
    """Holds consequential actions until the patient taps Approve/Decline."""

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future] = {}
        self._requests: dict[str, dict] = {}

    async def request(self, case_id: str, agent: str, title: str, detail: dict, timeout: float = 600) -> bool:
        approval_id = uuid.uuid4().hex[:8]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[approval_id] = fut
        self._requests[approval_id] = {"approval_id": approval_id, "case_id": case_id, "title": title, "detail": detail}
        bus.emit(case_id, "approval_requested", agent, approval_id=approval_id, title=title, detail=detail)
        try:
            approved = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            approved = False
        bus.emit(case_id, "approval_resolved", agent, approval_id=approval_id, approved=approved)
        self._requests.pop(approval_id, None)
        return approved

    def resolve(self, approval_id: str, approved: bool) -> bool:
        fut = self._pending.pop(approval_id, None)
        if fut is None or fut.done():
            return False
        fut.set_result(approved)
        return True

    def pending_for(self, case_id: str) -> list[dict]:
        return [r for r in self._requests.values() if r["case_id"] == case_id]


gate = ApprovalGate()


@dataclass
class ToolRegistry:
    case_id: str
    agent: str
    tools: dict[str, Tool] = field(default_factory=dict)

    def add(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def specs(self) -> list[dict]:
        return [t.spec() for t in self.tools.values()]

    async def call(self, name: str, args: dict) -> Any:
        tool = self.tools.get(name)
        if tool is None:
            return {"error": f"unknown tool {name}"}
        bus.emit(self.case_id, "tool_call", self.agent, tool=name, tier=tool.tier, args=args)
        if tool.tier == "consequential":
            title = tool.approval_title(args) if tool.approval_title else f"{self.agent} wants to: {name}"
            ok = await gate.request(self.case_id, self.agent, title, args)
            if not ok:
                bus.emit(self.case_id, "tool_blocked", self.agent, tool=name)
                return {"blocked": True, "reason": "patient declined or did not respond"}
        try:
            result = await tool.fn(**args)
        except Exception as e:  # tool errors go back to the model, never crash the case
            result = {"error": f"{type(e).__name__}: {e}"}
        bus.emit(self.case_id, "tool_result", self.agent, tool=name, result=result)
        return result


class Agent:
    """A Claude tool-use loop. Stops when the model stops calling tools or after max_steps."""

    def __init__(self, case_id: str, name: str, system: str, registry: ToolRegistry,
                 model: str | None = None, max_steps: int = 8) -> None:
        self.case_id, self.name, self.system = case_id, name, system
        self.registry, self.model, self.max_steps = registry, model, max_steps

    async def run(self, task: str | list) -> str:
        messages: list[dict] = [{"role": "user", "content": task}]
        bus.emit(self.case_id, "agent_started", self.name)
        final_text = ""
        for _ in range(self.max_steps):
            resp = await llm.create(system=self.system, messages=messages, tools=self.registry.specs(), model=self.model)
            messages.append({"role": "assistant", "content": resp.content})
            texts = [b.text for b in resp.content if b.type == "text" and b.text.strip()]
            if texts:
                final_text = "\n".join(texts)
                bus.emit(self.case_id, "agent_thought", self.name, text=final_text)
            calls = [b for b in resp.content if b.type == "tool_use"]
            if not calls:
                break
            results = []
            for c in calls:
                out = await self.registry.call(c.name, c.input)
                results.append({"type": "tool_result", "tool_use_id": c.id,
                                "content": json.dumps(out, default=str)[:20000]})
            messages.append({"role": "user", "content": results})
        bus.emit(self.case_id, "agent_finished", self.name, summary=final_text)
        return final_text
