"""Voices BillBack can call with. Ids are Cartesia public voices (GET https://api.cartesia.ai/voices), picked
for clear customer-support delivery across accents. A case picks one with `voice_id` (an id or a key below).

`locale` is sent to Sonic as `language`, which keeps the voice's native accent; English voices also pin
`normalization` to en-US so dates and phone numbers follow US reading conventions on a US hospital call
(an Indian- or British-accented voice still reads 03/04 as March fourth).
"""
from dataclasses import asdict, dataclass

from .. import config


@dataclass(frozen=True)
class Voice:
    key: str
    id: str
    name: str
    accent: str
    locale: str
    gender: str
    description: str

    @property
    def language(self) -> str:
        return self.locale.split("-")[0]

    def tts_fields(self) -> dict:
        """Voice/language fields for a Sonic request (REST or WebSocket)."""
        fields = {"voice": self.id, "language": self.locale}
        if self.language == "en":
            fields["normalization"] = "en-US"
        return fields


CATALOG = [
    Voice("tessa", "6ccbfb76-1fc6-48f7-b71d-91ac6298247b", "Tessa", "American English", "en-US", "feminine",
          "Warm, conversational; BillBack's default caller."),
    Voice("katie", "f786b574-daa5-4673-aa0c-cbe3e8534c02", "Katie", "American English", "en-US", "feminine",
          "Enunciating young adult female for support calls; very clear on numbers."),
    Voice("gemma", "62ae83ad-4f6a-430b-af41-a9bede9286ca", "Gemma", "British English", "en-GB", "feminine",
          "Confident, emotive British female for professional assistance."),
    Voice("harrison", "df89f42f-f285-4613-adbf-14eedcec4c9e", "Harrison", "British English", "en-GB", "masculine",
          "Crisp, professional British male for efficient customer interactions."),
    Voice("priya", "f6141af3-5f94-418c-80ed-a45d450e7e2e", "Priya", "Indian English", "en-IN", "feminine",
          "Authoritative adult female for customer support."),
    Voice("sameer", "638efaaa-4d0c-442e-b701-3fae16aad012", "Sameer", "Indian English", "en-IN", "masculine",
          "Friendly male for customer support."),
    Voice("fernanda", "b4b8e2af-6139-466e-a93a-30c20d2e1fc5", "Fernanda", "Mexican Spanish", "es-MX", "feminine",
          "Approachable Mexican female for customer care (Spanish-speaking patients, roadmap)."),
]
_BY_KEY = {v.key: v for v in CATALOG} | {v.id: v for v in CATALOG}


def default() -> Voice:
    return resolve(config.CARTESIA_VOICE_ID)


def resolve(voice: str | None) -> Voice:
    """A catalog key or id; any other Cartesia voice id is used as-is with American English settings."""
    if not voice:
        voice = config.CARTESIA_VOICE_ID or CATALOG[0].id
    if voice in _BY_KEY:
        return _BY_KEY[voice]
    return Voice("custom", voice, "Custom voice", "English", "en-US", "", "Voice id from configuration.")


def catalog() -> list[dict]:
    d = default().id
    return [{**asdict(v), "language": v.language, "default": v.id == d} for v in CATALOG]
