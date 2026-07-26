"""Semantic recall across negotiations, keyed on the ENGLISH GLOSS.

Exact-match recall (db.prior_transcript) hands the agent the last few lines
chronologically, which misses the point when a term resurfaces: what matters is
what was said about THAT term last time, not whatever happened to be said last.
We embed the gloss - never the native Gujarati/Malayalam text - because the two
parties speak different languages and embedding native text would scatter
identical meaning across the vector space instead of clustering it. The gloss is
already computed every turn elsewhere, so this costs nothing extra.

Like every DB/memory lookup in this app, embed/remember/recall are best-effort:
a failure here must never block a live turn.
"""
from __future__ import annotations

import json
import os

from dotenv import load_dotenv

from .meet_interface import db

load_dotenv()

EMBED_MODEL = "text-embedding-3-small"
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")

_client = None


def _get_client():
    global _client
    if _client is None:
        from openai import AsyncOpenAI
        _client = AsyncOpenAI(api_key=OPENAI_KEY, timeout=20.0, max_retries=1)
    return _client


def pair_key(keys: list[str]) -> str:
    """Order-independent identity for a pair, so either speaker's turn matches."""
    return "|".join(sorted(k for k in keys if k))


async def embed(text: str) -> list[float] | None:
    """Sarvam documents no embeddings endpoint, hence the one OpenAI call here."""
    try:
        r = await _get_client().embeddings.create(model=EMBED_MODEL, input=text)
        return r.data[0].embedding
    except Exception:
        return None


async def remember(room_code: str, pair: str, term: str, speaker_name: str,
                    lang: str, text: str, gloss: str) -> None:
    vec = await embed(gloss)
    if vec is None:
        return
    try:
        pool = await db.get_pool()
        await pool.execute(
            """INSERT INTO meet_memory
               (room_code, pair_key, term, speaker_name, lang, text, gloss, embedding)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8::vector)""",
            room_code, pair, term, speaker_name, lang, text, gloss, json.dumps(vec))
    except Exception:
        pass


async def recall(pair: str, gloss: str, exclude_room: str, limit: int = 4) -> list[dict]:
    """Nearest neighbours by cosine distance, restricted to this pair and other rooms."""
    vec = await embed(gloss)
    if vec is None:
        return []
    try:
        pool = await db.get_pool()
        rows = await pool.fetch(
            """SELECT speaker_name, lang, text, gloss, term, room_code, said_at
                 FROM meet_memory
                WHERE pair_key = $1 AND room_code <> $2
                ORDER BY embedding <=> $3::vector
                LIMIT $4""",
            pair, exclude_room, json.dumps(vec), limit)
        return [dict(r) for r in rows]
    except Exception:
        return []


def format_recall(rows: list[dict]) -> str:
    if not rows:
        return ""
    lines = "\n".join(f"- {r['speaker_name']}: {r['gloss']}" for r in rows)
    return f"WHEN THIS CAME UP BEFORE\n{lines}"
