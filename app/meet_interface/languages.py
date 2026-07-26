"""Supported spoken languages for a real-time meet: English + Indian languages
covered by Sarvam's Saaras STT / Bulbul v3 TTS.

Only gu-IN -> ratan and ml-IN -> shubh are confirmed against live Sarvam docs
(see docs/superpowers/specs/2026-07-26-nyaybandhan-realtime-mediator-design.md).
The rest are best-guess Bulbul v3 speaker picks and MUST be re-verified with
verify.py (or the live docs) before a real demo - a wrong speaker name 400s the
TTS call for that language, it does not silently fall back.
"""
from __future__ import annotations

LANGUAGES: dict[str, dict[str, str]] = {
    "en-IN": {"label": "English", "speaker": "priya"},
    "hi-IN": {"label": "Hindi", "speaker": "meera"},
    "bn-IN": {"label": "Bengali", "speaker": "arya"},
    "gu-IN": {"label": "Gujarati", "speaker": "ratan"},
    "kn-IN": {"label": "Kannada", "speaker": "maya"},
    "ml-IN": {"label": "Malayalam", "speaker": "shubh"},
    "mr-IN": {"label": "Marathi", "speaker": "amol"},
    "or-IN": {"label": "Odia", "speaker": "pooja"},
    "pa-IN": {"label": "Punjabi", "speaker": "kiran"},
    "ta-IN": {"label": "Tamil", "speaker": "diya"},
    "te-IN": {"label": "Telugu", "speaker": "neel"},
}


def is_supported(language_code: str) -> bool:
    return language_code in LANGUAGES


def label(language_code: str) -> str:
    return LANGUAGES.get(language_code, {}).get("label", language_code)


def speaker(language_code: str) -> str:
    return LANGUAGES.get(language_code, {}).get("speaker", "")


def route(sender_lang: str, listener_out_lang: str) -> tuple[str | None, str]:
    """Pick how one utterance reaches one listener.

    Returns (translate_target, tts_speaker). `translate_target` is None when the
    listener already reads/hears the language it was spoken in - that caption is
    verbatim and costs no /translate call. The speaker is ALWAYS the listener's
    output language's Bulbul voice, never the sender's.
    """
    target = None if sender_lang == listener_out_lang else listener_out_lang
    return target, speaker(listener_out_lang)


def as_list() -> list[dict[str, str]]:
    """Client-facing shape for the language picker dropdown."""
    return [{"code": code, "label": info["label"]} for code, info in LANGUAGES.items()]
