"""remy/knowledge.py: doc sections, retrieval, the how-question hint, CODE_MAP. Offline."""

import os
import re

from remy import knowledge as k


def top(q):
    hits = k.relevant(q)
    assert hits, q
    return hits[0]


def test_sections_have_heading_paths():
    secs = k.sections()
    assert len(secs) > 50
    docs = {s["doc"] for s in secs}
    assert {"README.md", "docs/voice.md", "docs/brightdata.md"} <= docs
    assert not any("PLAN.md" in d or "devpost" in d or "research" in d for d in docs)
    heads = {s["heading"] for s in secs}
    assert any(h.startswith("README › Sponsors") and "Bright Data" in h for h in heads)
    assert all(s["text"].strip() for s in secs)


def test_bright_data_policy_question():
    hit = top("How does the Bright Data agent find a hospital's policy?")
    assert hit["doc"] == "docs/brightdata.md" or (hit["doc"] == "README.md" and "Bright Data" in hit["heading"])


def test_interrupt_hits_barge_in():
    hit = top("What happens if billing interrupts Remy?")
    assert hit["doc"] == "docs/voice.md" or "Barge-in" in hit["heading"]
    assert "barge" in hit["text"].lower() or "Barge" in hit["heading"]


def test_trust_hits_money_is_computed():
    heads = [h["heading"] for h in k.relevant("Why should I trust the $600?", k=2)]
    assert any("Money is computed" in h or "What if the AI is wrong" in h for h in heads), heads


def test_legal_question():
    assert "legal" in top("Is it legal for an AI to call the hospital?")["heading"].lower()


def test_max_chars_and_shape():
    for n in (500, 2000, 6000):
        hits = k.relevant("How does the voice pipeline work with Cartesia Ink and Sonic?", k=5, max_chars=n)
        assert hits
        assert sum(len(h["text"]) + len(h["heading"]) + len(h["doc"]) for h in hits) <= n
        assert set(hits[0]) == {"doc", "heading", "text", "score"}
    assert k.relevant("") == []


def test_cache_rebuilds_on_mtime(monkeypatch, tmp_path):
    (tmp_path / "README.md").write_text("# Remy\n\n## Zebras\nRemy herds zebras.\n")
    monkeypatch.setattr(k, "ROOT", tmp_path)
    monkeypatch.setattr(k, "_cache", {"stamp": None, "sections": [], "index": None})
    assert top("zebras")["heading"] == "README › Zebras"
    (tmp_path / "README.md").write_text("# Remy\n\n## Giraffes\nRemy herds giraffes.\n")
    st = os.stat(tmp_path / "README.md")
    os.utime(tmp_path / "README.md", ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert top("giraffes")["heading"] == "README › Giraffes"


def test_is_how_question():
    for q in ["How does the Bright Data agent work?", "Why should I trust the $600?",
              "What is the Nessie agent?", "How is this built?", "What happens if billing interrupts?",
              "Where is the speech screen handled in the code?", "What's real and what's simulated?"]:
        assert k.is_how_question(q), q
    for q in ["What do I owe?", "When will they call back?", "How much do I owe UCSF?",
              "Call them", "What did billing agree to?"]:
        assert not k.is_how_question(q), q


def test_code_map_paths_exist():
    assert 10 <= len(k.CODE_MAP) <= 25
    for line in k.CODE_MAP:
        path = line.split(" — ")[0]
        assert (k.ROOT / path).exists(), path


def test_no_secrets_in_sections():
    live = re.compile(r"sk-ant-|\b[A-Z_]*(KEY|TOKEN|SECRET)\s*=\s*['\"]?[A-Za-z0-9+/_=-]{16,}")
    for s in k.sections():
        assert not live.search(s["text"]), s["heading"]


def test_secret_lines_dropped():
    text = "# T\n\n## Setup\nANTHROPIC_API_KEY=sk-ant-api03-abcdefghijklmnop\nNESSIE_API_KEY=\nkeep this\n"
    (sec,) = k._split("README.md", text)
    assert "sk-ant" not in sec["text"]
    assert "NESSIE_API_KEY=" in sec["text"] and "keep this" in sec["text"]
