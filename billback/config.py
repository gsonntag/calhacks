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

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")
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
    }
