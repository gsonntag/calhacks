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
    hcai: str = ""                      # slug of its HCAI Hospital Fair Pricing Policy Lookup page (policies/hcai.py)

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
             "Zuckerberg San Francisco General Hospital financial assistance charity care policy",
             "priscilla-chan-mark-zuckerberg-san-francisco-general-hospital-trauma-center"),
    # added because ZSFG's own site doesn't publish its full policy (it points to HCAI, which Bright Data blocks)
    Hospital("chinese-hospital", "Chinese Hospital", "Chinese Hospital", "San Francisco", ("chinesehospital-sf.org",),
             ("Chinese Hospital San Francisco",), "Chinese Hospital San Francisco financial assistance charity care policy"),
    # Wider Bay Area: a mix of systems (Sutter, Stanford, county, Dignity/CommonSpirit, Kaiser, district hospitals)
    Hospital("sutter-alta-bates", "Alta Bates Summit Medical Center", "Sutter Health", "Oakland",
             ("sutterhealth.org",), ("Alta Bates", "Summit Medical Center", "Sutter Alta Bates Summit",
                                     "Alta Bates Summit Medical Center Berkeley", "Alta Bates Summit Medical Center Oakland"),
             "Alta Bates Summit Medical Center Sutter Health financial assistance policy"),
    Hospital("stanford-health", "Stanford Health Care", "Stanford Medicine", "Palo Alto",
             ("stanfordhealthcare.org",), ("Stanford Hospital", "Stanford Medical Center", "Stanford Health Care Palo Alto"),
             "Stanford Health Care financial assistance charity care policy", "stanford-health-care"),
    Hospital("highland-ahs", "Highland Hospital", "Alameda Health System", "Oakland",
             ("alamedahealthsystem.org",), ("Alameda Health System", "Highland Hospital Oakland", "AHS Highland"),
             "Alameda Health System Highland Hospital financial assistance charity care policy"),
    # Dignity Health sold Saint Francis Memorial to UCSF Health in 2024; Dignity's site and CommonSpirit's price
    # index no longer list it (a first crawl under the Dignity name found no official page; see docs/brightdata.md).
    Hospital("ucsf-saint-francis", "UCSF Health Saint Francis Hospital", "UCSF Health", "San Francisco",
             ("sfcommunityhospitals.ucsfhealth.org",),
             ("Saint Francis Memorial Hospital", "Saint Francis Memorial", "St. Francis Memorial Hospital",
              "Dignity Health Saint Francis", "UCSF Saint Francis"),
             "UCSF Health Saint Francis Hospital San Francisco financial assistance policy",
             "st-francis-memorial-hospital"),
    Hospital("john-muir-wc", "John Muir Medical Center, Walnut Creek", "John Muir Health", "Walnut Creek",
             ("johnmuirhealth.com",), ("John Muir Medical Center", "John Muir Health", "John Muir Walnut Creek"),
             "site:johnmuirhealth.com financial assistance policy"),
    Hospital("kaiser-oakland", "Kaiser Permanente Oakland Medical Center", "Kaiser Permanente", "Oakland",
             ("healthy.kaiserpermanente.org", "kaiserpermanente.org", "kp.org", "about.kaiserpermanente.org"),
             ("Kaiser Oakland", "Kaiser Permanente Oakland", "Kaiser Foundation Hospital Oakland"),
             "Kaiser Permanente Northern California medical financial assistance policy"),
    Hospital("marinhealth", "MarinHealth Medical Center", "MarinHealth", "Greenbrae",
             ("mymarinhealth.org",), ("Marin General Hospital", "MarinHealth", "Marin General"),
             "MarinHealth Medical Center financial assistance charity care policy"),
    Hospital("el-camino", "El Camino Hospital", "El Camino Health", "Mountain View",
             ("elcaminohealth.org",), ("El Camino Health", "El Camino Hospital Mountain View", "El Camino Hospital Los Gatos"),
             "El Camino Hospital financial assistance charity care policy"),
    # Major US hospitals outside the Bay Area (added 2026-10-25): the same pipeline, crawled live with Bright Data
    Hospital("mayo-rochester", "Mayo Clinic Hospital", "Mayo Clinic", "Rochester, MN",
             ("mayoclinic.org",), ("Mayo Clinic", "Mayo Clinic Rochester", "Saint Marys Hospital"),
             "Mayo Clinic financial assistance policy pdf"),
    Hospital("cleveland-clinic", "Cleveland Clinic", "Cleveland Clinic", "Cleveland, OH",
             ("clevelandclinic.org", "my.clevelandclinic.org"), ("Cleveland Clinic Main Campus",),
             "Cleveland Clinic financial assistance policy pdf"),
    Hospital("johns-hopkins", "The Johns Hopkins Hospital", "Johns Hopkins Medicine", "Baltimore, MD",
             ("hopkinsmedicine.org",), ("Johns Hopkins Hospital", "Johns Hopkins", "JHH"),
             "Johns Hopkins Medicine financial assistance policy pdf"),
    Hospital("mass-general", "Massachusetts General Hospital", "Mass General Brigham", "Boston, MA",
             ("massgeneral.org", "massgeneralbrigham.org"), ("Mass General", "MGH", "Massachusetts General"),
             "Mass General Brigham financial assistance policy pdf"),
    Hospital("brigham-womens", "Brigham and Women's Hospital", "Mass General Brigham", "Boston, MA",
             ("brighamandwomens.org", "massgeneralbrigham.org"), ("Brigham and Womens Hospital", "BWH", "The Brigham"),
             "Brigham and Women's Hospital financial assistance policy Mass General Brigham"),
    Hospital("nyp", "NewYork-Presbyterian Hospital", "NewYork-Presbyterian", "New York, NY",
             ("nyp.org",), ("NewYork Presbyterian", "New York Presbyterian", "NYP", "NYP Weill Cornell", "NYP Columbia"),
             "NewYork-Presbyterian financial assistance policy pdf"),
    Hospital("mount-sinai", "The Mount Sinai Hospital", "Mount Sinai Health System", "New York, NY",
             ("mountsinai.org",), ("Mount Sinai Hospital", "Mount Sinai"),
             "Mount Sinai Health System financial assistance policy pdf"),
    Hospital("nyu-langone", "NYU Langone Hospitals", "NYU Langone Health", "New York, NY",
             ("nyulangone.org",), ("NYU Langone", "NYU Langone Tisch Hospital", "Tisch Hospital"),
             "NYU Langone Hospitals financial assistance policy pdf"),
    Hospital("penn-hup", "Hospital of the University of Pennsylvania", "Penn Medicine", "Philadelphia, PA",
             ("pennmedicine.org",), ("HUP", "Penn Medicine", "Penn Presbyterian"),
             "Penn Medicine financial assistance policy pdf"),
    Hospital("northwestern-memorial", "Northwestern Memorial Hospital", "Northwestern Medicine", "Chicago, IL",
             ("nm.org",), ("Northwestern Medicine", "Northwestern Memorial"),
             "Northwestern Medicine financial assistance policy pdf"),
    Hospital("cedars-sinai", "Cedars-Sinai Medical Center", "Cedars-Sinai", "Los Angeles, CA",
             ("cedars-sinai.org",), ("Cedars Sinai", "Cedars-Sinai"),
             "Cedars-Sinai financial assistance policy pdf"),
    Hospital("ucla-reagan", "Ronald Reagan UCLA Medical Center", "UCLA Health", "Los Angeles, CA",
             ("uclahealth.org",), ("UCLA Medical Center", "UCLA Health", "Ronald Reagan UCLA"),
             "UCLA Health financial assistance policy pdf"),
    Hospital("houston-methodist", "Houston Methodist Hospital", "Houston Methodist", "Houston, TX",
             ("houstonmethodist.org",), ("Houston Methodist", "The Methodist Hospital"),
             "Houston Methodist financial assistance policy pdf"),
    Hospital("michigan-medicine", "University of Michigan Health", "Michigan Medicine", "Ann Arbor, MI",
             ("uofmhealth.org", "michiganmedicine.org", "med.umich.edu"), ("Michigan Medicine", "University of Michigan Hospital", "U of M Health"),
             "Michigan Medicine financial assistance policy pdf"),
    Hospital("barnes-jewish", "Barnes-Jewish Hospital", "BJC HealthCare", "St. Louis, MO",
             ("barnesjewish.org", "bjc.org"), ("Barnes Jewish", "BJC", "Barnes-Jewish"),
             "BJC HealthCare financial assistance policy pdf"),
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
