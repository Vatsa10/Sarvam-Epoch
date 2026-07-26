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


# Bulbul v3 has no emotion parameter - `pace` and voice choice are the whole
# expressive surface. So pace carries the emotional read: a mediator that says
# "you two have not actually agreed" at the same clip as a routine relay sounds
# like it did not notice. Slower on the sensitive beat, brisker on a plain relay.
PACE_SENSITIVE = 0.85   # a divergence, a hedge, a question about money
PACE_RELAY     = 1.05   # routine pass-through, keep the call moving
PACE_NORMAL    = 1.0


def pace_for(sensitive: bool, relaying: bool = False) -> float:
    """One policy, used by both the terminal harness and the meet relay."""
    if sensitive:
        return PACE_SENSITIVE
    return PACE_RELAY if relaying else PACE_NORMAL


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


async def chat_tools(system: str, user: str, tools: list[dict],
                     temperature: float = 0.1) -> dict:
    """Tool-calling chat. Returns the raw assistant message dict so the caller can
    read both `tool_calls` and `content`."""
    r = await _client.post(
        CHAT_URL,
        headers={**HEADERS, "Content-Type": "application/json"},
        json={
            "model": CHAT_MODEL,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "tools": tools,
            "tool_choice": "auto",
            "temperature": temperature,
        },
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]


async def translate(text: str, target_language_code: str,
                    source_language_code: str = "auto",
                    mode: str = "code-mixed") -> str:
    """Live-notes path. Deliberately uses /translate, not the chat model: it is a
    separate 60/min quota bucket, so partials never eat the 40/min sarvam-30b budget.

    mayura:v1 caps at 1000 chars; a single VAD segment is far below that.

    mode: "code-mixed" mirrors how people actually type and is right for on-screen
    notes. Use "formal" for anything Bulbul will SPEAK - code-mixed output leaves
    English words stranded mid-sentence, which sounds wrong read aloud.
    """
    # Same language in and out is a no-op, and Mayura 400s on it. This is reachable
    # in normal use - the moment either party picks English, the English gloss and
    # the relay both become en-IN -> en-IN.
    if source_language_code == target_language_code:
        return text

    r = await _client.post(
        TRANSLATE_URL,
        headers={**HEADERS, "Content-Type": "application/json"},
        json={
            "input": text[:1000],
            "source_language_code": source_language_code,
            "target_language_code": target_language_code,
            "model": "mayura:v1",
            "mode": mode,
        },
    )
    if r.status_code >= 400:
        # Surface what the API actually objected to. A bare "400 Bad Request" during
        # a live demo tells you nothing about which field was wrong.
        raise httpx.HTTPStatusError(
            f"translate {source_language_code}->{target_language_code} failed "
            f"[{r.status_code}]: {r.text[:300]}",
            request=r.request, response=r,
        )
    return r.json().get("translated_text", "")


async def aclose() -> None:
    await _client.aclose()
