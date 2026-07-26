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
        # Lets us force finalisation on Done. Without it the browser simply
        # stops sending frames, Saaras sees no audio at all - not even
        # silence - so its VAD never fires end-of-speech and no final ever
        # arrives. The turn then vanishes with "nothing was picked up".
        "flush_signal": "true",
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
        # VERIFIED against real socket output, not assumed: Saaras sends ONE data
        # frame per speech segment and it carries the complete transcript. There is
        # no `is_final` field on the wire at all.
        #
        # This module used to read data.is_final, get None, and label EVERY
        # transcript a partial - so nothing was ever appended to the turn buffer,
        # talk_done always drained empty, and every turn died as "nothing was picked
        # up" while a perfectly good transcript sat on screen greyed out as pending.
        # A data frame with text IS the final.
        return ("final", text)

    if kind == "events":
        signal = ((msg.get("data") or {}).get("signal_type") or "").upper()
        allowlist = ("END_OF_TURN", "SPEECH_END", "TURN_END", "END_SPEECH")
        # Known signal names first (exact or substring match against the
        # allowlist). Only if none of those match do we fall back to the old
        # bare "END" in signal check - and even then, exclude signals that
        # plainly aren't about turn-ending, like *_ERROR, to stop the fallback
        # from misfiring on things such as SEND_ERROR.
        if any(a == signal or a in signal for a in allowlist):
            return ("turn_end", "")
        if "END" in signal and "ERROR" not in signal:
            return ("turn_end", "")
        return ("ignore", "")

    if kind == "error":
        return ("error", ((msg.get("error") or {}).get("message") or "unknown STT error"))

    return ("ignore", "")


WS_HEADERS = {"api-subscription-key": sarvam.API_KEY}
