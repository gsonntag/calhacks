"""Force every integration offline before billback reads .env (load_dotenv never overrides set vars)."""
import os

for _k in ("ANTHROPIC_API_KEY", "NESSIE_API_KEY", "BRIGHTDATA_API_KEY", "BRIGHTDATA_UNLOCKER_ZONE",
           "BRIGHTDATA_SERP_ZONE", "CARTESIA_API_KEY", "CARTESIA_AGENT_ID", "CARTESIA_FROM_NUMBER_ID",
           "REP_PHONE_NUMBER", "CARTESIA_WEBHOOK_SECRET", "CARTESIA_TOOL_TOKEN", "TIGER_DATABASE_URL"):
    os.environ[_k] = ""

import pytest  # noqa: E402

from billback import config, events, llm  # noqa: E402


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    for k in ("ANTHROPIC_API_KEY", "NESSIE_API_KEY", "BRIGHTDATA_API_KEY", "BRIGHTDATA_UNLOCKER_ZONE",
              "BRIGHTDATA_SERP_ZONE", "CARTESIA_API_KEY", "CARTESIA_AGENT_ID", "CARTESIA_FROM_NUMBER_ID",
              "REP_PHONE_NUMBER", "CARTESIA_WEBHOOK_SECRET", "CARTESIA_TOOL_TOKEN", "TIGER_DATABASE_URL"):
        monkeypatch.setattr(config, k, "")
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")  # tests call from localhost, which is allowed without one
    monkeypatch.setattr(config, "REQUIRE_LIVE", False)  # judging mode is off: tests run on the fixtures
    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setattr(events, "DATA_DIR", tmp_path)  # keep audit logs out of data/
