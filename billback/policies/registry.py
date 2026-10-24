"""Hospitals we track, and matching a bill's provider name to one of them."""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Hospital:
    id: str
    name: str
    system: str = ""
    city: str = ""
    domains: tuple[str, ...] = ()       # official domains (preferred by discover.rank)
    aliases: tuple[str, ...] = ()       # other names a bill or user might use
    query: str = ""                     # SERP query; default built from name + city

    @property
    def search_query(self) -> str:
        return self.query or f"{self.name} {self.city} financial assistance charity care policy".strip()


SEED = (
    Hospital("sutter-cpmc", "California Pacific Medical Center", "Sutter Health", "San Francisco",
             ("sutterhealth.org",), ("Sutter CPMC", "CPMC", "CPMC Van Ness", "Sutter California Pacific Medical Center"),
             "California Pacific Medical Center San Francisco financial assistance policy"),
    Hospital("ucsf-health", "UCSF Medical Center", "UCSF Health", "San Francisco",
             ("ucsfhealth.org",), ("UCSF Health", "UCSF", "UCSF Helen Diller Medical Center"),
             "UCSF Medical Center charity care policy pdf"),
    Hospital("zsfg", "Zuckerberg San Francisco General Hospital", "San Francisco Health Network", "San Francisco",
             ("zuckerbergsanfranciscogeneral.org", "sf.gov", "sfdph.org"),
             ("ZSFG", "SF General", "San Francisco General Hospital",
              "Priscilla Chan and Mark Zuckerberg San Francisco General Hospital"),
             "Zuckerberg San Francisco General Hospital financial assistance charity care policy"),
    # added because ZSFG's own site doesn't publish its full policy (it points to HCAI, which Bright Data blocks)
    Hospital("chinese-hospital", "Chinese Hospital", "Chinese Hospital", "San Francisco", ("chinesehospital-sf.org",),
             ("Chinese Hospital San Francisco",), "Chinese Hospital San Francisco financial assistance charity care policy"),
)


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def slug(name: str) -> str:
    return norm(name).replace(" ", "-")[:60] or "hospital"


def names(h: Hospital) -> list[str]:
    return [h.name, *h.aliases]


def match(name: str, hospitals: list[Hospital]) -> Hospital | None:
    """Exact (normalized) name/alias match first, then a name that contains a hospital's full name."""
    n = norm(name)
    if not n:
        return None
    for h in hospitals:
        if n in {norm(a) for a in names(h)}:
            return h
    for h in hospitals:
        if any(len(norm(a)) > 6 and norm(a) in n for a in names(h)):
            return h
    return None
