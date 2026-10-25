"""Runtime config. Every integration falls back to a labelled demo mode when its key is missing."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL_SMART = os.getenv("BILLBACK_MODEL_SMART", "claude-sonnet-5-5")
MODEL_FAST = os.getenv("BILLBACK_MODEL_FAST", "claude-haiku-5-5")

NESSIE_API_KEY = os.getenv("NESSIE_API_KEY", "")
NESSIE_BASE_URL = os.getenv("NESSIE_BASE_URL", "https://api.nessieisreal.com")

BRIGHTDATA_API_KEY = os.getenv("BRIGHTDATA_API_KEY", "")
BRIGHTDATA_UNLOCKER_ZONE = os.getenv("BRIGHTDATA_UNLOCKER_ZONE", "")
BRIGHTDATA_SERP_ZONE = os.getenv("BRIGHTDATA_SERP_ZONE", "")

CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY", "")
CARTESIA_AGENT_ID = os.getenv("CARTESIA_AGENT_ID", "")          # from scripts/setup_cartesia.py
CARTESIA_FROM_NUMBER_ID = os.getenv("CARTESIA_FROM_NUMBER_ID", "")
# Placeholder values (public in .env.example) count as unset, and unset means the endpoints reject everything.
_PLACEHOLDERS = {"", "change-me", "change-me-too", "pick-a-long-random-string", "pick-another-long-random-string"}


def _secret(name: str) -> str:
    v = os.getenv(name, "").strip()
    return "" if v in _PLACEHOLDERS else v


CARTESIA_WEBHOOK_SECRET = _secret("CARTESIA_WEBHOOK_SECRET")
CARTESIA_TOOL_TOKEN = _secret("CARTESIA_TOOL_TOKEN")
CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID", "6ccbfb76-1fc6-48f7-b71d-91ac6298247b")
REP_PHONE_NUMBER = os.getenv("REP_PHONE_NUMBER", "")  # teammate playing hospital billing, +1XXXXXXXXXX

# Own voice pipeline (billback/voice): Twilio telephony + Cartesia streaming STT/TTS + Claude call brain.
TWILIO_ACCOUNT_SID = _secret("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = _secret("TWILIO_AUTH_TOKEN")          # also validates X-Twilio-Signature on webhooks
TWILIO_FROM_NUMBER = _secret("TWILIO_FROM_NUMBER")        # a Twilio number you own, +1XXXXXXXXXX
TWILIO_TWIML_APP_SID = _secret("TWILIO_TWIML_APP_SID")    # optional: enables the 3-way conference handoff
PATIENT_PHONE_NUMBER = _secret("PATIENT_PHONE_NUMBER")    # demo patient's phone (teammate), for verbal consent
CARTESIA_STT_MODEL = os.getenv("CARTESIA_STT_MODEL", "ink-2")
CARTESIA_STT_BATCH_MODEL = os.getenv("CARTESIA_STT_BATCH_MODEL", "ink-whisper")  # short recordings (Cases page mic)
CARTESIA_TTS_MODEL = os.getenv("CARTESIA_TTS_MODEL", "sonic-3.6")

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")

# Shared secret for routes that place a call, spend credits or wipe state (billback/auth.py). Unset: those
# routes only answer requests made directly on this machine, never through a tunnel.
ADMIN_TOKEN = _secret("BILLBACK_ADMIN_TOKEN")
# Judging mode: refuse to start without the live keys, and never use the sample policies.
REQUIRE_LIVE = os.getenv("BILLBACK_REQUIRE_LIVE", "").strip().lower() in ("1", "true", "yes", "on")
DATA_DIR = ROOT / "data"
DEMO_DIR = ROOT / "demo"
DATA_DIR.mkdir(exist_ok=True)


def mode() -> dict:
    """Which integrations are live vs demo. Shown on the dashboard so we never pass off demo data as live."""
    return {
        "claude": bool(ANTHROPIC_API_KEY),
        "nessie": bool(NESSIE_API_KEY),
        "brightdata": bool(BRIGHTDATA_API_KEY and BRIGHTDATA_UNLOCKER_ZONE),
        "cartesia": bool(CARTESIA_API_KEY),
        "twilio": bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER),
    }


def missing_live() -> list[str]:
    """What judging mode still needs: presence and format only, no API call (calls cost credits)."""
    def bad(v: str, prefix: str = "") -> bool:
        v = v.strip()
        return v in _PLACEHOLDERS or any(c.isspace() for c in v) or not v.startswith(prefix)
    need = [("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY, "sk-ant-", "Claude: reads bills and extracts policies"),
            ("BRIGHTDATA_API_KEY", BRIGHTDATA_API_KEY, "", "Bright Data: live policy crawls"),
            ("BRIGHTDATA_UNLOCKER_ZONE", BRIGHTDATA_UNLOCKER_ZONE, "", "Bright Data Web Unlocker zone"),
            ("BRIGHTDATA_SERP_ZONE", BRIGHTDATA_SERP_ZONE, "", "Bright Data SERP zone"),
            ("NESSIE_API_KEY", NESSIE_API_KEY, "", "Nessie: the patient's real bank history"),
            ("BILLBACK_ADMIN_TOKEN", ADMIN_TOKEN, "", "locks approve/reset on a public URL")]
    out = [f"{name} ({why})" for name, v, prefix, why in need if bad(v, prefix)]
    if ADMIN_TOKEN and len(ADMIN_TOKEN) < 16:
        out.append("BILLBACK_ADMIN_TOKEN (at least 16 characters)")
    return out


def check_live() -> None:
    """Judging mode only: stop at startup, listing every missing key, instead of running on sample data."""
    missing = missing_live() if REQUIRE_LIVE else []
    if missing:
        raise RuntimeError("BILLBACK_REQUIRE_LIVE is on, but these are missing or malformed:\n  - "
                           + "\n  - ".join(missing)
                           + "\nSet them in .env, or unset BILLBACK_REQUIRE_LIVE for offline development.")
