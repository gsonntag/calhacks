"""The one format every money-checking app agent reports in. The case page reads only this, so any agent
that reports this way gets its own live panel without UI changes.

The orchestrator first sends review_started with the line-up ({agent, name, covers} each), so every panel is
on screen from the start. Then on the case's event bus (/ws/cases/{id}), each agent sends:
  agent_started   name, covers: who it is and what it looks at
  step            one check as it happens: label, status, result, and proof (the real data it looked at,
                  each line with where it came from). Re-sent with the same key when the check resolves.
                  status: running | ok | flag (found a problem) | clear (nothing found) | win (qualifies) | wait
  finding         something that changes what the patient owes or can ask for: title, money saved
                  (0 when it isn't money off the bill), value (how to show it), proof and source
  agent_finished  summary, note
"""
from ..events import bus


class Report:
    def __init__(self, case_id: str, agent: str, name: str, covers: str):
        self.case_id, self.agent, self.name, self.covers = case_id, agent, name, covers

    def card(self) -> dict:
        return {"agent": self.agent, "name": self.name, "covers": self.covers}

    def start(self, text: str = "") -> None:
        bus.emit(self.case_id, "agent_started", self.agent, name=self.name, covers=self.covers, text=text)

    def step(self, key: str, label: str, status: str, result: str = "",
             proof: list[tuple[str, str]] | None = None) -> None:
        """proof: (what was seen, where it came from) pairs."""
        bus.emit(self.case_id, "step", self.agent, key=f"{self.agent}:{key}", label=label, status=status,
                 result=result, proof=[{"text": t, "source": s} for t, s in proof or []])

    def finding(self, key: str, title: str, saved: float, proof: list[str], source: str, value: str = "") -> None:
        bus.emit(self.case_id, "finding", self.agent, key=f"{self.agent}:{key}", title=title, saved=round(saved, 2),
                 value=value or f"−${saved:,.0f}", proof=proof, source=source)

    def finish(self, summary: str, note: str = "") -> None:
        bus.emit(self.case_id, "agent_finished", self.agent, summary=summary, note=note)
