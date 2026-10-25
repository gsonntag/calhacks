"""Remy's own phone pipeline: Twilio telephony + Cartesia streaming STT (Ink) and TTS (Sonic) + the
Claude call brain (agents.caller.CallSession). See docs/voice.md.

    from remy import voice
    app.include_router(voice.router)
    if voice.twilio_ready(): outcome = await voice.run_twilio_call(case, brief)
"""
from .calls import DialError, run_twilio_call, twilio_ready
from .routes import router

__all__ = ["DialError", "router", "run_twilio_call", "twilio_ready"]
