"""Patient's verbal authorization, captured on a transcribed call before Remy talks to the hospital.

HIPAA lets a hospital discuss payment with a person the patient identifies as involved in it, and the
patient's agreement can be verbal (45 CFR 164.510(b)). Remy asks the patient one scripted question
(no LLM: the wording is fixed and auditable), classifies the answer, and writes an `authorization` event
with the exact question, the patient's transcribed words, the time and the Twilio CallSid to the audit log.
The hospital call's brief then carries that record, so the agent can cite it when billing asks.
"""
import re
from datetime import datetime, timezone

from ..events import bus

YES = re.compile(r"\b(yes|yeah|yep|yup|sure|of course|i do|i authori[sz]e|i agree|go ahead|that's fine|okay|ok|"
                 r"correct|absolutely|please do|you may|you can|sí|si)\b", re.I)
NO = re.compile(r"\b(no|nope|don't|do not|not|never|stop|cancel)\b", re.I)


def classify(said: str) -> bool | None:
    """True = agrees, False = refuses, None = unclear. Refusals win ("no, don't" / "not okay")."""
    if NO.search(said):
        return False
    return True if YES.search(said) else None


class AuthorizationBrain:
    def __init__(self, case_id: str, patient_first: str, hospital: str, conference: bool = False) -> None:
        self.case_id, self.first, self.hospital, self.conference = case_id, patient_first, hospital, conference
        self.ended = False
        self.granted: bool | None = None
        self.asked = 0
        self.statement = ""
        self.call_sid = ""
        self.at: datetime | None = None
        self.question = (f"Hi {patient_first}, this is Remy, the automated assistant helping with your "
                         f"{hospital} bill. This call is transcribed for your records. Before I talk "
                         f"to {hospital} billing, do I have your permission to discuss your account with them "
                         "on your behalf? Please say yes or no.")

    async def turn(self, said: str | None) -> str:
        if not self.asked or said is None:  # "Hello?" when they pick up, or silence: ask
            self.asked += 1
            return self.question
        verdict = classify(said)
        if verdict is None and self.asked < 2:
            self.asked += 1
            return "Sorry, I didn't catch that. Do I have your permission to discuss your bill with them? Yes or no?"
        self.granted, self.statement, self.ended = bool(verdict), said, True
        self._record()
        if not self.granted:
            return "Understood. I won't call them. You can change your mind in the Remy app anytime. Goodbye."
        if self.conference:
            return (f"Thank you. I'm calling {self.hospital} billing now, please stay on the line. If they ask, "
                    "just tell them you authorize Remy to speak for you.")
        return (f"Thank you, I've recorded your permission. I'm calling {self.hospital} billing now and I'll "
                "send you the results in the app. Goodbye.")

    def _record(self) -> None:
        self.at = datetime.now(timezone.utc)
        bus.emit(self.case_id, "authorization", "caller", granted=self.granted, question=self.question,
                 statement=self.statement, at=self.at.isoformat(timespec="seconds"),
                 call_sid=self.call_sid, method="verbal, on a transcribed call",
                 basis="45 CFR 164.510(b): disclosure to a person involved in payment, with the patient's agreement")

    def record(self) -> dict:
        """What the hospital-call brief carries, so the caller can cite it."""
        return {"given": bool(self.granted), "how": "verbally, on a transcribed call with Remy",
                "patient_said": self.statement, "at": self.at.strftime("%B %d, %Y %H:%M UTC") if self.at else None,
                "patient_on_the_line": self.conference}
