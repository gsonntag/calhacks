"""Text normalizer applied before every TTS call, so a billing rep hears numbers and terms the way a person
reads them aloud.

Cartesia Sonic has its own normalizer, but billing calls are full of strings it can't know how to read:
claim numbers ("C-2026-0815-44917"), account numbers, CPT/HCPCS codes, drug names. We rewrite those into
spoken forms and Cartesia markup before the text leaves our server:

- money        "$8,700"      -> "eight thousand seven hundred dollars"
- dates        "2026-08-02"  -> "August second, twenty twenty-six"
- IDs, codes   "C-2026-0815-44917" -> "<spell>C 2026 0815 44917</spell>"  (Cartesia's spell tag reads
               character by character; spaces inside the tag become short pauses between groups)
- phones       "+14155551212" -> "(415) 555-1212"  (the canonical form Cartesia's normalizer groups well)
- acronyms     "EOB" -> "<spell>EOB</spell>", "FPL" -> "federal poverty level"
- read-backs   "Let me read that back: FA209-31" -> "... <spell>F A 2 0 9</spell> dash <spell>3 1</spell>"
               (every character on its own, dashes said out loud, so the rep can catch a misheard ID)
- drug and lab terms -> inline IPA phonemes "<<...>>" (Sonic 3.x phoneme syntax). A Cartesia pronunciation
               dictionary is the same search-and-replace done server side; keeping it here keeps it in
               version control and unit-tested.

Money and dates are spelled out only for English; IDs, codes and phones are handled for every language.
"""
import re

ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
        "sixteen seventeen eighteen nineteen").split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
MONTHS = ("January February March April May June July August September October November December").split()

# General American IPA, Sonic 3.x phoneme set (docs.cartesia.ai .../capability-guides/phonemes).
PHONEMES = {
    "ondansetron": "<<ɑ|n|ˈ|d|æ|n|s|ə|ˌ|t|ɹ|ɑ|n>>",
    "metabolic": "<<ˌ|m|ɛ|t|ə|ˈ|b|ɑ|l|ɪ|k>>",
    "acetaminophen": "<<ə|ˌ|s|i|t|ə|ˈ|m|ɪ|n|ə|f|ə|n>>",
    "ibuprofen": "<<ˌ|aɪ|b|j|u|ˈ|p|ɹ|oʊ|f|ə|n>>",
    "ketorolac": "<<ˈ|k|i|t|oʊ|ˌ|ɹ|oʊ|l|æ|k>>",
    "heparin": "<<ˈ|h|ɛ|p|ɝ|ɪ|n>>",
    "ceftriaxone": "<<ˌ|s|ɛ|f|t|ɹ|aɪ|ˈ|æ|k|s|oʊ|n>>",
    "lidocaine": "<<ˈ|l|aɪ|d|ə|ˌ|k|eɪ|n>>",
    "hydromorphone": "<<ˌ|h|aɪ|d|ɹ|oʊ|ˈ|m|ɔ|ɹ|f|oʊ|n>>",
    "metoclopramide": "<<ˌ|m|ɛ|t|ə|ˈ|k|l|oʊ|p|ɹ|ə|ˌ|m|aɪ|d>>",
    "levofloxacin": "<<ˌ|l|i|v|oʊ|ˈ|f|l|ɑ|k|s|ə|s|ɪ|n>>",
    "creatinine": "<<k|ɹ|i|ˈ|æ|t|ə|ˌ|n|i|n>>",
    "venipuncture": "<<ˈ|v|ɛ|n|ə|ˌ|p|ə|ŋ|k|tʃ|ɝ>>",
    "medi-cal": "<<ˈ|m|ɛ|d|ɪ|ˌ|k|æ|l>>",
}
# Read letter by letter. Matched case-sensitively so "ER" the department isn't "er" the filler word.
SPELLED = ("EOB", "CPT", "HCPCS", "ICD", "ER", "ED", "CT", "MRI", "IV", "CBC", "CMP", "FAP", "NPI", "HSA", "FSA",
           "ICU", "EKG", "ECG", "MRN", "BMP", "UA")
EXPANDED = {"FPL": "federal poverty level", "OOP": "out-of-pocket", "501(r)": "five oh one R", "Dept.": "Department",
            "DOB": "date of birth"}

_ORD = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth", "nine": "ninth",
        "twelve": "twelfth"}


def number_words(n: int) -> str:
    if n < 0:
        return "minus " + number_words(-n)
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("" if n % 10 == 0 else "-" + ONES[n % 10])
    if n < 1000:
        return ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + number_words(n % 100))
    for size, name in ((10**9, "billion"), (10**6, "million"), (1000, "thousand")):
        if n >= size:
            rest = n % size
            return number_words(n // size) + " " + name + ("" if rest == 0 else " " + number_words(rest))
    raise AssertionError


def ordinal_words(n: int) -> str:
    words = number_words(n)
    head, _, last = words.rpartition("-") if "-" in words else words.rpartition(" ")
    sep = "-" if "-" in words else " "
    last = _ORD.get(last) or (last[:-1] + "ieth" if last.endswith("y") else last + "th")
    return f"{head}{sep}{last}" if head else last


def year_words(y: int) -> str:
    if 2000 <= y < 2010 or y % 1000 == 0:
        return number_words(y)
    if 1100 <= y < 2100:
        hi, lo = divmod(y, 100)
        return number_words(hi) + (" hundred" if lo == 0 else (" oh " if lo < 10 else " ") + number_words(lo))
    return number_words(y)


def money_words(dollars: int, cents: int = 0) -> str:
    parts = []
    if dollars or not cents:
        parts.append(f"{number_words(dollars)} dollar{'' if dollars == 1 else 's'}")
    if cents:
        parts.append(f"{number_words(cents)} cent{'' if cents == 1 else 's'}")
    return " and ".join(parts)


def date_words(y: int, m: int, d: int) -> str | None:
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return None
    return f"{MONTHS[m - 1]} {ordinal_words(d)}, {year_words(y)}"


def _digit_groups(run: str) -> list[str]:
    """Long digit runs read better in chunks: 48213977 -> 4821 3977."""
    if len(run) <= 5:
        return [run]
    size = 3 if len(run) % 4 == 1 or len(run) == 6 else 4
    return [run[i:i + size] for i in range(0, len(run), size)]


def spell_id(token: str) -> str:
    """C-2026-0815-44917 -> <spell>C 2026 0815 44917</spell>; no punctuation inside the tag (it'd be read)."""
    groups = []
    for part in re.split(r"[-–./]", token):
        prefixed = re.fullmatch(r"([A-Za-z]+)(\d+)", part)  # BCH48213977 -> BCH 4821 3977; A1B2 stays whole
        if prefixed:
            groups += [prefixed[1].upper(), *_digit_groups(prefixed[2])]
        elif part:
            groups += _digit_groups(part) if part.isdigit() else [part.upper()]
    return f"<spell>{' '.join(groups)}</spell>"


def read_back_id(token: str) -> str:
    """FA209-31 -> <spell>F A 2 0 9</spell> dash <spell>3 1</spell>; "FA 20931" -> <spell>F A</spell>, <spell>2 0 9 3 1</spell>.
    Slower than spell_id on purpose: one character at a time, the written dashes and gaps kept, so a
    misheard number sounds different from the right one."""
    out = []
    for i, word in enumerate(token.split()):
        parts = [" ".join(p.upper()) for p in re.split(r"[-–]", word) if p]
        out.append((", " if i else "") + " dash ".join(f"<spell>{p}</spell>" for p in parts))
    return "".join(out)


_UNIT_SUFFIX = re.compile(r"^\d+(st|nd|rd|th|s|am|pm|mg|mcg|ml|kg|lb|lbs|k|x|h|hr|hrs|min)$", re.I)


def _is_id(tok: str) -> bool:
    if not re.search(r"\d", tok) or _UNIT_SUFFIX.match(tok):
        return False
    letters = re.findall(r"[A-Za-z]+", tok)
    if any(len(w) > 4 for w in letters):  # "COVID-19", "Section-5": words, not identifiers
        return False
    digits = re.sub(r"\D", "", tok)
    return bool(letters) or "-" in tok or len(digits) >= 5


_MONEY_RANGE = re.compile(r"\$(\d[\d,]*)\s?(?:-|–|to)\s?\$?(\d[\d,]*)(?![\d.])")
_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?(?!\d)")
_ISO_DATE = re.compile(r"(?<![\w-])(\d{4})-(\d{2})-(\d{2})(?![\w-])")
_US_DATE = re.compile(r"(?<![\w/])(\d{1,2})/(\d{1,2})/(\d{4})(?![\w/])")
# Needs separators or a +1: a bare 10-digit run is more likely an account number than a phone.
_PHONE = re.compile(r"(?<![\w-])(?:\+?1[\s.-]?)?(?:\((\d{3})\)\s?|(\d{3})[\s.-])(\d{3})[.-](\d{4})(?![\w-])"
                    r"|(?<![\w+])\+1(\d{3})(\d{3})(\d{4})(?!\d)")
_TOKEN = re.compile(r"(?<![\w\x00-])[A-Za-z0-9]+(?:[-–][A-Za-z0-9]+)*(?![\w\x00-])")
# "Let me read that back: FA-20931." / "read it back, case number FA 20931": an ID (groups with a digit, or
# short capitals like "FA") right after "read that back"; the caller's prompt asks for this wording.
_GROUP = r"(?:[A-Z]{1,4}|[A-Za-z0-9]*\d[A-Za-z0-9]*)"
_READ_BACK = re.compile(r"(\bread (?:that|it|this|the number) back\b[,:;. —-]*(?:(?:case|reference|ref|confirmation|"
                        r"claim|number|is|it's|that's)\s+)*)(" + _GROUP + r"(?:(?:[-–]| (?=" + _GROUP + r"\b))" + _GROUP
                        + r")*)(?![\w-])")
_MARKUP = re.compile(r"<<[^>]*>>|<spell>.*?</spell>|<(?:break|speed|volume|emotion)\b[^>]*/>")


def normalize(text: str, language: str = "en") -> str:
    """Spoken form of `text` for Cartesia TTS. Idempotent on its own output."""
    kept: list[str] = []

    def keep(s: str) -> str:
        kept.append(s)
        return f"\x00{len(kept) - 1}\x00"

    text = _MARKUP.sub(lambda m: keep(m[0]), re.sub(r"[*`]+", "", text or ""))
    text = _READ_BACK.sub(lambda m: m[1] + keep(read_back_id(m[2])) if re.search(r"\d", m[2]) else m[0], text)
    if language.lower().startswith("en"):
        text = _MONEY_RANGE.sub(lambda m: keep(f"{number_words(int(m[1].replace(',', '')))} to "
                                               f"{money_words(int(m[2].replace(',', '')))}"), text)
        text = _MONEY.sub(lambda m: keep(money_words(int(m[1].replace(",", "")), int((m[2] or "0").ljust(2, "0")))), text)
        text = _ISO_DATE.sub(lambda m: keep(date_words(int(m[1]), int(m[2]), int(m[3])) or m[0]), text)
        text = _US_DATE.sub(lambda m: keep(date_words(int(m[3]), int(m[1]), int(m[2])) or m[0]), text)
        for written, spoken in EXPANDED.items():
            text = re.sub(rf"(?<!\w){re.escape(written)}(?!\w)", lambda m, s=spoken: keep(s), text)
        for word, ipa in PHONEMES.items():
            text = re.sub(rf"(?<![\w-]){re.escape(word)}(?![\w-])", lambda m, p=ipa: keep(p), text, flags=re.I)
        for acr in SPELLED:
            text = re.sub(rf"(?<![\w\x00]){acr}(?:s\b)?(?![\w\x00])",
                          lambda m: keep(f"<spell>{acr}</spell>" + ("s" if m[0].endswith("s") else "")), text)
    text = _PHONE.sub(lambda m: keep("({}) {}-{}".format(*(m.group(5, 6, 7) if m[5] else (m[1] or m[2], m[3], m[4])))), text)
    text = _TOKEN.sub(lambda m: keep(spell_id(m[0])) if _is_id(m[0]) else m[0], text)
    while "\x00" in text:
        text = re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m[1])], text)
    return text
