"""How Remy works: the project's own docs, split into sections, for the Ask Remy chat.

The chat answers questions about the case from the case. For "how does X work / why should I trust it /
where is X handled?" it needs the docs instead. Use it like this:

    from remy import knowledge
    if knowledge.is_how_question(q):          # a hint, not a gate
        context["how_it_works"] = knowledge.relevant(q)
        context["code_map"] = knowledge.CODE_MAP

and tell Claude: "Answer how-it-works questions only from the how_it_works sections and cite doc +
heading; if they don't cover it, say so."

Retrieval is BM25 over words (light stemming, heading words counted extra) with a small synonym map for
product terms. No dependencies, no API calls. The index is cached and rebuilt when a doc's mtime changes.
Lines that look like a real secret (an env value after KEY=, an `sk-` key) are dropped when loading.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Docs that explain how the product works. Included:
#   README.md               overview, how it works, each sponsor, real vs simulated, limitations, privacy
#   docs/voice.md           the phone pipeline: Ink, Sonic, barge-in, latency, pronunciation, authorization
#   docs/brightdata.md      the policy database, live crawl, price tables
#   docs/DEMO.md            the demo case and what's real vs simulated
#   docs/legal-check.md     the legal claims, checked against sources
#   docs/plan-format.md     the plan's data format, recalculation, events, endpoints
#   docs/feasibility.md     how bank data, hospital policies and calling work in the real world
# Left out: docs/ideas-backlog.md (internal planning, partly out of date),
# docs/devpost.md (submission copy, repeats the README), docs/competitors.md (market, not product),
# docs/research/ (outside research).
DOCS = [
    "README.md",
    "docs/voice.md",
    "docs/brightdata.md",
    "docs/DEMO.md",
    "docs/legal-check.md",
    "docs/plan-format.md",
    "docs/feasibility.md",
]

# What lives where. Each path is checked to exist (tests/test_knowledge.py).
CODE_MAP = [
    "remy/orchestrator.py — runs a case end to end: intake, Nessie agent, Bright Data agent, plan, approval, call, follow-ups",
    "remy/harness.py — tool permission tiers (read / act / consequential) and the approval gate before every call",
    "remy/events.py — per-case event bus, the append-only audit log (data/<case_id>.audit.jsonl) and the case store",
    "remy/agents/intake.py — reads the uploaded bill and insurance statement (EOB) into fields with Claude vision",
    "remy/agents/auditor.py — the Nessie agent: reconciles the bill, the insurance statement and the bank",
    "remy/billcheck.py — bill checks before the money math: payments to this hospital, lines above the posted price",
    "remy/finance.py — all money math: reconciliation, duplicates, income, % of poverty line, discount tiers, plans",
    "remy/agents/policy.py — the Bright Data agent: the hospital's assistance policy -> discount and plan terms",
    "remy/policies/ — the policy DB pipeline: discover (SERP) -> fetch (Web Unlocker) -> extract + verify quotes -> store",
    "remy/policies/crawl.py — one crawl session: every Bright Data request logged, saved, capped at 10 (HARD_CAP)",
    "remy/policies/hcai.py — the policy as filed with California HCAI, when the hospital's own site lacks it",
    "remy/prices.py — real price tables from hospitals' CMS price-transparency files (policydb/prices/)",
    "remy/agents/decide.py — the Decide agent (planner chat): findings -> plan of asks, versions, what's next",
    "remy/agents/planner.py — proposes an interest-free payment plan that adds up exactly to what's still owed",
    "remy/agents/mandate.py — guardrails in code: check_offer against the approved plan, and the speech screen",
    "remy/agents/caller.py — the Claude call brain (CallSession.turn) with record_outcome, press_keys, check_offer, end_call",
    "remy/voice/media.py — one Twilio Media Stream: Ink STT -> call brain -> Sonic TTS, with barge-in",
    "remy/voice/heard.py — what billing actually heard of an interrupted reply (Sonic word timestamps)",
    "remy/voice/desk.py — desk mode: the rep console (web/rep.html) talking to Remy without a phone line",
    "remy/voice/pronounce.py — reads money, dates, IDs, CPT codes and drug names aloud correctly",
    "remy/agents/scorecard.py — after a call, marks each ask got / refused / later from the rep's own words",
    "remy/followups.py — what billing promised, when to check back, and 'what's the status?'",
    "remy/integrations/nessie.py — Capital One Nessie client, plus the local bank mirror used offline",
    "remy/integrations/brightdata.py — Bright Data client: SERP API and Web Unlocker behind one request function",
]

# Product terms: a query word also matches the rest of its group, at the group's weight.
SYNONYMS = [(0.35, g) for g in [
    {"nessie", "bank", "capital", "deposit", "transaction", "auditor", "payroll"},
    {"brightdata", "crawl", "policy", "hospital", "serp", "unlocker", "scrape", "assistance"},
    {"cartesia", "voice", "ink", "sonic", "call", "phone", "speech", "stt", "tts", "twilio"},
    {"planner", "decide", "plan", "ask", "strategy", "version"},
    {"guardrail", "mandate", "screen", "check_offer", "refuse", "block", "safe"},
    {"price", "chargemaster", "posted", "cms", "transparency", "charge"},
    {"hcai", "california", "filed", "filing"},
    {"charity", "discount", "free", "tier", "fpl", "poverty", "eligible", "qualify"},
    {"approval", "approve", "gate", "consent", "permission", "authorization"},
    {"latency", "fast", "speed", "slow", "delay"},
    {"tiger", "timescale", "postgres", "history", "household"},
    {"claude", "anthropic", "llm", "model", "ai"},
    {"real", "simulated", "synthetic", "fake", "sample", "mock"},
    {"privacy", "hipaa", "stored", "data", "delete"},
]] + [
    (0.8, {"barge", "interrupt", "heard", "playback", "backchannel"}),
    (0.5, {"trust", "computed", "deterministic", "source", "quote", "verify", "proof", "math", "generated"}),
]
PHRASES = {"capital one": "nessie", "bright data": "brightdata", "barge-in": "barge",
           "talks over": "interrupt", "posted price": "posted", "speech screen": "screen",
           "check_offer": "check_offer", "poverty line": "fpl"}

STOP = set("""a an the and or but if of to in on at for by with from as is are was were be been being it its
this that these those i you he she we they me my your our their do does did doing done how what why when where
which who whom will would can could should may might must shall there here so not no yes than then too very just
about into out up down off also any all some each own same such only s t don isn it's what's let get got has have
had having more most other again once""".split())

SECRET = re.compile(
    r"sk-[A-Za-z0-9_-]{12,}"
    r"|\b[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|SID)\s*=\s*['\"]?(?![<$])[A-Za-z0-9+/_=-]{16,}"
    r"|\bAC[0-9a-f]{32}\b")

K1, B = 1.4, 0.7
HEADING_WEIGHT = 3
PAIR_BONUS = 1.5
LONG_SECTION = 1200

_cache: dict = {"stamp": None, "sections": [], "index": None}


def _stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    for suf, keep in (("ing", 4), ("ed", 4), ("es", 5), ("s", 3)):
        if w.endswith(suf) and len(w) - len(suf) >= keep and not w.endswith("ss"):
            return w[: -len(suf)]
    return w


def _tokens(text: str) -> list[str]:
    t = text.lower()
    for p, rep in PHRASES.items():
        t = t.replace(p, f" {rep} ")
    words = re.findall(r"[a-z][a-z0-9_]*|\d[\d,.]*", t)
    return [_stem(w.replace(",", "").rstrip(".")) for w in words if w not in STOP]


_GROUPS = [(wt, {_stem(w) for w in g}) for wt, g in SYNONYMS]


def _clean(line: str) -> bool:
    return not SECRET.search(line)


def _doc_title(rel: str) -> str:
    return "README" if rel == "README.md" else Path(rel).stem


def _split(rel: str, text: str) -> list[dict]:
    """Markdown -> sections by heading; long sections split further at bold-led paragraphs/items."""
    path: list[tuple[int, str]] = []
    out: list[dict] = []
    body: list[str] = []
    fence = False

    def flush():
        if any(l.strip() for l in body):
            # The doc's own title (its # heading) is replaced by the doc name: "README › How it works".
            heads = [_doc_title(rel)] + [h for level, h in path if level > 1]
            out.extend(_chunks(rel, heads, body))
        body.clear()

    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
        m = None if fence else re.match(r"^(#{1,6})\s+(.*?)\s*#*$", line)
        if m:
            flush()
            level = len(m.group(1))
            path = [p for p in path if p[0] < level] + [(level, m.group(2).strip())]
            continue
        if _clean(line):
            body.append(line)
    flush()
    return out


_BOLD_LEAD = re.compile(r"^(?:[-*] |\d+\. )?\*\*(.+?)\*\*")


def _chunks(rel: str, heads: list[str], lines: list[str]) -> list[dict]:
    text = "\n".join(lines).strip()
    if len(text) <= LONG_SECTION:
        return [_section(rel, heads, text)]
    parts: list[tuple[str | None, list[str]]] = [(None, [])]
    for line in lines:
        m = _BOLD_LEAD.match(line)
        if m:
            label = m.group(1).strip().strip('"“”').rstrip(".:").strip()
            parts.append((label[:80], [line]))
        else:
            parts[-1][1].append(line)
    out = []
    for label, ls in parts:
        t = "\n".join(ls).strip()
        if t:
            out.append(_section(rel, heads + ([label] if label else []), t))
    return out


def _section(rel: str, heads: list[str], text: str) -> dict:
    return {"doc": rel, "heading": " › ".join(heads), "text": text}


def sections() -> list[dict]:
    """All doc sections (cached; rebuilt when a doc's mtime changes)."""
    _ensure()
    return _cache["sections"]


def _ensure() -> None:
    files = [ROOT / d for d in DOCS if (ROOT / d).exists()]
    stamp = tuple((str(f), f.stat().st_mtime_ns) for f in files)
    if stamp == _cache["stamp"]:
        return
    secs = []
    for f in files:
        secs.extend(_split(str(f.relative_to(ROOT)), f.read_text(encoding="utf-8", errors="replace")))
    tfs, lens, df, pairs = [], [], {}, []
    for s in secs:
        body, head = _tokens(s["text"]), _tokens(s["heading"])
        pairs.append(set(zip(body, body[1:])) | set(zip(head, head[1:])))
        toks = body + head * HEADING_WEIGHT
        tf: dict[str, int] = {}
        for w in toks:
            tf[w] = tf.get(w, 0) + 1
        for w in tf:
            df[w] = df.get(w, 0) + 1
        tfs.append(tf)
        lens.append(len(toks))
    n = len(secs) or 1
    idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
    _cache.update(stamp=stamp, sections=secs,
                  index={"tf": tfs, "len": lens, "avg": (sum(lens) / n) or 1, "idf": idf, "pairs": pairs})


def _query_weights(question: str) -> dict[str, float]:
    weights: dict[str, float] = {}
    for w in _tokens(question):
        weights[w] = weights.get(w, 0) + 1.0
        for wt, g in _GROUPS:
            if w in g:
                for syn in g - {w}:
                    weights[syn] = max(weights.get(syn, 0), wt)
    return weights


def relevant(question: str, k: int = 3, max_chars: int = 6000) -> list[dict]:
    """Top-k doc sections for a question, each {doc, heading, text, score}; total text <= max_chars."""
    _ensure()
    idx, secs = _cache["index"], _cache["sections"]
    q = _query_weights(question)
    if not q or not secs:
        return []
    qt = _tokens(question)
    qpairs = set(zip(qt, qt[1:]))
    idf = idx["idf"]
    scored = []
    for i, tf in enumerate(idx["tf"]):
        norm = K1 * (1 - B + B * idx["len"][i] / idx["avg"])
        s = sum(qw * idf.get(w, 0) * tf[w] * (K1 + 1) / (tf[w] + norm)
                for w, qw in q.items() if w in tf)
        # The question's word pairs ("nessie agent", "speech screen") found as written.
        s += sum(PAIR_BONUS * (idf.get(a, 0) + idf.get(b, 0)) / 2 for a, b in qpairs & idx["pairs"][i])
        if s > 0:
            scored.append((s, i))
    scored.sort(reverse=True)
    out, budget = [], max_chars
    for s, i in scored[:k]:
        sec = secs[i]
        room = budget - len(sec["heading"]) - len(sec["doc"])
        if room <= 80:
            break
        text = sec["text"] if len(sec["text"]) <= room else sec["text"][: room - 1].rstrip() + "…"
        out.append({"doc": sec["doc"], "heading": sec["heading"], "text": text, "score": round(s, 3)})
        budget -= len(text) + len(sec["heading"]) + len(sec["doc"])
    return out


_HOW = re.compile(
    r"\bhow (?:\w+ )?(?:does|do|did|is|are|was|were|would|will|can)\b(?! (?:i|we) (?:pay|owe)\b)"
    r"|\bwhat(?:'s| is) real\b|\b(?:what|which) (?:does|do|is)\b.*\b(?:hcai|nessie|bright ?data|cartesia|"
    r"twilio|tiger|harness|mandate|planner|decide agent|chargemaster|audit log)\b"
    r"|\bwhy (?:should|would|do|does|is|can)\b|\btrust\b|\bwhat happens (?:if|when)\b"
    r"|\bwhat(?:'s| is| are)(?: the| a)? (?:nessie|bright ?data|decide|planner|caller|cartesia|ink|sonic|"
    r"tiger|harness|mandate|guardrail|speech screen|policy|approval gate)"
    r"|\bwhere (?:is|are|does|do)\b.*\b(?:handled|code|live|stored|built|implemented|come from)\b"
    r"|\b(?:built|architecture|under the hood|explain|guardrails?|pipeline|sponsors?|"
    r"real (?:or|vs\.?) simulated|is (?:this|it) (?:real|legal|safe)|hipaa|privacy|latency|barge)\b",
    re.I)
_CASE = re.compile(
    r"\b(?:do i owe|i owe|my (?:bill|balance|case|payment|plan|insurance|account)|call (?:me |them )?back|"
    r"when will|how much (?:do|will|should) i|should i (?:pay|call|apply)|did (?:they|billing)|"
    r"what did (?:they|billing)|status|call them|set up the plan|next step)\b", re.I)


def is_how_question(question: str) -> bool:
    """True for questions about how Remy works or is built; False for questions about this case."""
    how = len(_HOW.findall(question))
    case = len(_CASE.findall(question))
    return how > case
