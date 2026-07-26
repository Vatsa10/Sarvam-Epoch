"""Thin Sarvam API wrappers. Every endpoint/param lives here so verify.py can prove
them all before product code runs.

VERIFIED from live docs 2026-07-26:
  Saaras v3 STT  : mode(transcribe|translate|verbatim|translit|codemix), language_code
                   optional (auto-detect), with_timestamps -> word-level timestamps.
                   NO emotion field. No per-word confidence.
  Bulbul v3 TTS  : speaker (30+ voices), pace 0.5-2.0.
                   pitch/loudness are v2-ONLY. NO emotion/style param.
Endpoint paths are the one thing NOT confirmed from docs - run verify.py first.
"""
import os
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("SARVAM_API_KEY", "")
BASE = os.getenv("SARVAM_BASE", "https://api.sarvam.ai")

STT_URL = f"{BASE}/speech-to-text"
STT_WS_URL = "wss://api.sarvam.ai/speech-to-text/ws"
TTS_URL = f"{BASE}/text-to-speech"
TRANSLATE_URL = f"{BASE}/translate"
CHAT_URL = f"{BASE}/v1/chat/completions"

STT_MODEL = "saaras:v3"
TTS_MODEL = "bulbul:v3"
CHAT_MODEL = os.getenv("SARVAM_CHAT_MODEL", "sarvam-30b")

HEADERS = {"api-subscription-key": API_KEY}

# Speaker choice is language-dependent per Sarvam docs. These are the documented
# picks; anushka/abhilash are v2-only and 400 on bulbul:v3.
PARTIES = {
    "vatsa":   {"name": "Vatsa",   "lang": "gu-IN", "label": "Gujarati",  "speaker": "ratan"},
    "sreedev": {"name": "Sreedev", "lang": "ml-IN", "label": "Malayalam", "speaker": "shubh"},
}

_client = httpx.AsyncClient(timeout=45.0)


async def stt(audio: bytes, filename: str = "turn.webm", language_code: str | None = None) -> dict[str, Any]:
    """Speech -> text. Omit language_code to let Saaras auto-detect.

    Returns {transcript, language_code, timestamps?}. `with_timestamps` is on because
    word timings are what a hedge/pause read needs later - they cost nothing here.
    """
    data = {"model": STT_MODEL, "mode": "codemix", "with_timestamps": "true"}
    if language_code:
        data["language_code"] = language_code
    r = await _client.post(
        STT_URL,
        headers=HEADERS,
        files={"file": (filename, audio, "audio/webm")},
        data=data,
    )
    r.raise_for_status()
    return r.json()


async def tts(text: str, target_language_code: str, speaker: str, pace: float = 1.0) -> str:
    """Text -> base64 wav. Bulbul v3 exposes only speaker + pace; there is no emotion
    knob, so tone has to live in the wording of `text` itself."""
    r = await _client.post(
        TTS_URL,
        headers=HEADERS,
        json={
            "text": text,
            "target_language_code": target_language_code,
            "model": TTS_MODEL,
            "speaker": speaker,
            "pace": pace,
        },
    )
    r.raise_for_status()
    audios = r.json().get("audios") or []
    return audios[0] if audios else ""


async def chat(system: str, user: str, temperature: float = 0.1) -> str:
    r = await _client.post(
        CHAT_URL,
        headers={**HEADERS, "Content-Type": "application/json"},
        json={
            "model": CHAT_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        },
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


async def aclose() -> None:
    await _client.aclose()
