"""Reasoning provider for the mediator agent.

gpt-4o-mini does tool calling; Sarvam keeps every speech surface (Saaras, Bulbul,
/translate), which is what the declared Voice Experience parameter is scored on.
Moving reasoning off sarvam-30b also frees its whole 40 req/min budget for speech.

`sarvam` stays selectable via AGENT_PROVIDER so the build still runs if the OpenAI
key fails on the floor - one env var, no code change.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

from . import sarvam

load_dotenv()

PROVIDERS = ("openai", "sarvam")
PROVIDER = os.getenv("AGENT_PROVIDER", "openai").lower()
MODEL = os.getenv("AGENT_MODEL", "gpt-4o-mini")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")

_openai_client = None


def _client():
    global _openai_client
    if _openai_client is None:
        from openai import AsyncOpenAI
        _openai_client = AsyncOpenAI(api_key=OPENAI_KEY, timeout=20.0, max_retries=1)
    return _openai_client


async def _openai_call(system: str, user: str, tools: list[dict], temperature: float) -> dict:
    kwargs = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": temperature,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    r = await _client().chat.completions.create(**kwargs)
    return r.choices[0].message.model_dump()


async def _sarvam_call(system: str, user: str, tools: list[dict], temperature: float) -> dict:
    return await sarvam.chat_tools(system, user, tools, temperature)


async def _route(system: str, user: str, tools: list[dict], temperature: float) -> dict:
    if PROVIDER == "sarvam":
        return await _sarvam_call(system, user, tools, temperature)
    return await _openai_call(system, user, tools, temperature)


async def complete_with_tools(system: str, user: str, tools: list[dict],
                              temperature: float = 0.1) -> dict:
    """Returns an OpenAI-shaped assistant message dict: {content, tool_calls?}."""
    return await _route(system, user, tools, temperature)
