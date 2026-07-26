"""Sarvam STT WebSocket: URL construction and message classification.

Kept pure and I/O-free so the message contract is testable without a socket. The
actual connection lives in main.py where it can be wired to a browser WebSocket.
"""
from __future__ import annotations

from urllib.parse import urlencode

from . import sarvam


def build_ws_url(language_code: str) -> str:
    """Codec MUST be pcm_s16le - the WS rejects webm/opus, which is why the browser
    captures raw PCM via AudioWorklet rather than MediaRecorder."""
    q = urlencode({
        "language-code": language_code,
        "model": "saaras:v3",
        "input_audio_codec": "pcm_s16le",
        "sample_rate": "16000",
        "high_vad_sensitivity": "true",
        "vad_signals": "true",
    })
    return f"{sarvam.STT_WS_URL}?{q}"


def classify(msg: dict) -> tuple[str, str]:
    """Map a server frame to (kind, text).

    kind: partial | final | turn_end | error | ignore
    """
    kind = (msg or {}).get("type")

    if kind == "data":
        text = ((msg.get("data") or {}).get("transcript") or "").strip()
        if not text:
            return ("ignore", "")
        return ("final" if (msg.get("data") or {}).get("is_final") else "partial", text)

    if kind == "events":
        signal = ((msg.get("data") or {}).get("signal_type") or "").upper()
        if "END" in signal:
            return ("turn_end", "")
        return ("ignore", "")

    if kind == "error":
        return ("error", ((msg.get("error") or {}).get("message") or "unknown STT error"))

    return ("ignore", "")


WS_HEADERS = {"api-subscription-key": sarvam.API_KEY}
