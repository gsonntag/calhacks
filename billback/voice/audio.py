"""G.711 μ-law helpers. Twilio Media Streams carry 8 kHz mono μ-law, base64 encoded, in 20 ms frames
(160 bytes). Cartesia Ink accepts that encoding directly and Sonic can emit it, so the live path never
transcodes; decoding to PCM is only for saving recordings (simulator) and tests."""
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
