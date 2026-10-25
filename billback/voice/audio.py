"""G.711 μ-law helpers. Twilio Media Streams carry 8 kHz mono μ-law, base64 encoded, in 20 ms frames
(160 bytes). Cartesia Ink accepts that encoding directly and Sonic can emit it, so the live path never
transcodes; decoding to PCM is only for saving recordings (simulator) and tests. The one thing we
synthesize ourselves is keypad tones (DTMF) for phone menus."""
import math
import struct

RATE = 8000
FRAME = 160            # 20 ms
SILENCE = 0xFF         # μ-law code for 0


def seconds(n_bytes: int) -> float:
    return n_bytes / RATE


def silence(ms: int) -> bytes:
    return bytes([SILENCE]) * (RATE * ms // 1000)


def frames(audio: bytes, size: int = FRAME) -> list[bytes]:
    return [audio[i:i + size] for i in range(0, len(audio), size)]


def _decode(b: int) -> int:
    b = ~b & 0xFF
    exp, mant = (b >> 4) & 0x07, b & 0x0F
    s = (((mant << 3) + 0x84) << exp) - 0x84
    return -s if b & 0x80 else s


_TABLE = [_decode(i) for i in range(256)]


def ulaw_to_pcm16(data: bytes) -> bytes:
    return struct.pack(f"<{len(data)}h", *(_TABLE[b] for b in data))


def wav(ulaw: bytes) -> bytes:
    """μ-law audio as a 16-bit PCM WAV file (plays anywhere)."""
    pcm = ulaw_to_pcm16(ulaw)
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 2, 2, 16) + b"data" + struct.pack("<I", len(pcm)) + pcm)


def _encode(s: int) -> int:
    """16-bit PCM sample -> μ-law byte (G.711)."""
    sign = 0x80 if s < 0 else 0
    s = min(abs(s), 32635) + 0x84
    exp = max(0, s.bit_length() - 8)
    return ~(sign | (exp << 4) | ((s >> (exp + 3)) & 0x0F)) & 0xFF


# Keypad tones (DTMF): each key is a low and a high frequency played together.
DTMF = {k: (lo, hi) for row, lo in zip(("123A", "456B", "789C", "*0#D"), (697, 770, 852, 941))
        for k, hi in zip(row, (1209, 1336, 1477, 1633))}


def dtmf(keys: str, tone_ms: int = 120, gap_ms: int = 100) -> bytes:
    """μ-law 8 kHz keypad tones for `keys` ("1", "2#"; "w" = a half-second pause), sent in-band down the media
    stream: phone menus on the PSTN detect DTMF in G.711 audio, so no extra Twilio API call is needed."""
    out = bytearray()
    for k in keys.upper():
        if k == "W":
            out += silence(500)
        elif k in DTMF:
            lo, hi = DTMF[k]
            out += bytes(_encode(int(8000 * (math.sin(2 * math.pi * lo * i / RATE) + math.sin(2 * math.pi * hi * i / RATE))))
                         for i in range(RATE * tone_ms // 1000))
            out += silence(gap_ms)
    return bytes(out)
