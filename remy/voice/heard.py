"""What the other side actually heard of a reply that got interrupted.

When billing talks over Remy we stop the audio, but the call brain already wrote (and remembered) the
whole reply. `Spoken` records, for one reply, each Sonic request's original text, where its audio starts in
the reply's audio, and Sonic's word timestamps (`add_timestamps`). Given how many seconds were actually
played (Twilio marks on the phone, the Web Audio clock in desk mode), `heard(played_s)` returns the reply
cut at the last word that was fully spoken. The brain then rewrites its history to that
(`CallSession.interrupted`), so Claude doesn't believe the rep heard a sentence that never played.

Sonic's timestamps are for the spoken form (after `pronounce.normalize`: "$8,700" is five words). We map
them back to the written text by normalizing prefixes of it and counting spoken words. Without timestamps
(an older socket, a fake) we fall back to the played fraction of that request's audio.
"""
import re
from dataclasses import dataclass, field

from . import pronounce


def spoken_words(text: str) -> list[str]:
    """Words in a normalized TTS transcript: markup tags dropped, an IPA phoneme block counts as one word."""
    text = re.sub(r"<<[^>]*>>", " w ", text)
    text = re.sub(r"</?spell>|<[^>]+/>", " ", text)
    return text.split()


@dataclass
class Segment:
    text: str                    # what the brain wrote (before the normalizer)
    start: int                   # byte offset of its first audio byte in the reply's audio
    nbytes: int = 0
    words: list[str] = field(default_factory=list)   # Sonic word timestamps, seconds from this segment's start
    ends: list[float] = field(default_factory=list)


class Spoken:
    def __init__(self, bytes_per_s: float, language: str = "en") -> None:
        self.bytes_per_s, self.language = bytes_per_s, language
        self.segments: list[Segment] = []
        self.total = 0  # bytes of audio produced for this reply

    # ---- recording (called by TTS.stream_many / MediaSession._tts_audio)

    def begin(self, text: str) -> None:
        if self.segments and not self.segments[-1].nbytes:
            self.segments.pop()  # a retried request that produced no audio
        self.segments.append(Segment(text, self.total))

    def audio(self, nbytes: int) -> None:
        if self.segments:
            self.segments[-1].nbytes += nbytes
        self.total += nbytes

    def timestamps(self, words: list[str], ends: list[float]) -> None:
        if self.segments:
            self.segments[-1].words += list(words)
            self.segments[-1].ends += [float(e) for e in ends]

    @property
    def seconds(self) -> float:
        return self.total / self.bytes_per_s

    def boundaries(self) -> list[float]:
        """Seconds into the reply where each request after the first starts (where the phone path puts marks)."""
        return [s.start / self.bytes_per_s for s in self.segments[1:]]

    # ---- reading

    def text(self) -> str:
        return "".join(s.text for s in self.segments)

    def heard(self, played_s: float) -> str:
        """The written text of this reply up to the last word fully played after `played_s` seconds."""
        played = max(0.0, played_s) * self.bytes_per_s
        out = []
        for seg in self.segments:
            if seg.nbytes and seg.start + seg.nbytes <= played + 1:
                out.append(seg.text)
                continue
            into = (played - seg.start) / self.bytes_per_s
            if into > 0 and seg.nbytes:
                out.append(self._cut(seg, into))
            break
        return "".join(out).strip()

    def _cut(self, seg: Segment, into_s: float) -> str:
        written = list(re.finditer(r"\S+", seg.text))
        if not written:
            return ""
        prefix_counts = [len(spoken_words(pronounce.normalize(seg.text[:w.end()], self.language))) for w in written]
        total = prefix_counts[-1] or 1
        if seg.words and seg.ends:
            done = sum(1 for e in seg.ends if e <= into_s + 0.02)  # words Sonic had finished saying
            target = done * total / len(seg.ends)
        else:
            target = total * min(1.0, into_s / (seg.nbytes / self.bytes_per_s))
        keep = 0
        for i, n in enumerate(prefix_counts):
            if n <= target + 1e-9:
                keep = i + 1
        return seg.text[:written[keep - 1].end()] if keep else ""


def is_echo(partial: str, said: str) -> bool:
    """True if the speech-to-text is most likely Remy's own voice coming back through the rep's mic
    (desk mode on laptop speakers): two or more words, nearly all of them words of what Remy is saying."""
    heard = re.findall(r"[a-z0-9']+", partial.lower())
    spoken = " ".join(spoken_words(pronounce.normalize(said)))  # "$8,700" is heard as "eight thousand…"
    ours = set(re.findall(r"[a-z0-9']+", f"{said} {spoken}".lower()))
    if len(heard) < 2 or not ours:
        return False
    return sum(w in ours for w in heard) / len(heard) >= 0.8
