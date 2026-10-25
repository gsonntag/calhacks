"""Force every integration offline before remy reads .env (load_dotenv never overrides set vars).

The phone and admin settings are blanked too, so a full .env can never place a real call from a test."""
import os

KEYS = ("ANTHROPIC_API_KEY", "NESSIE_API_KEY", "BRIGHTDATA_API_KEY", "BRIGHTDATA_UNLOCKER_ZONE",
        "BRIGHTDATA_SERP_ZONE", "CARTESIA_API_KEY", "CARTESIA_AGENT_ID", "CARTESIA_FROM_NUMBER_ID",
        "REP_PHONE_NUMBER", "CARTESIA_WEBHOOK_SECRET", "CARTESIA_TOOL_TOKEN", "TIGER_DATABASE_URL",
        "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "TWILIO_TWIML_APP_SID",
        "PATIENT_PHONE_NUMBER")
LOCAL_URL = "http://localhost:8000"  # not https, so the Twilio path is never ready

for _k in KEYS + ("REMY_ADMIN_TOKEN", "REMY_REQUIRE_LIVE"):
    os.environ[_k] = ""
os.environ["PUBLIC_BASE_URL"] = LOCAL_URL

import pytest  # noqa: E402

from remy import config, events, llm  # noqa: E402


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    for k in KEYS:
        monkeypatch.setattr(config, k, "")
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", LOCAL_URL)
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")  # tests call from localhost, which is allowed without one
    monkeypatch.setattr(config, "REQUIRE_LIVE", False)  # judging mode is off: tests run on the fixtures
    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setattr(events, "DATA_DIR", tmp_path)  # keep audit logs out of data/
