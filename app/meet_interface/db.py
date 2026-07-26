"""Postgres (Neon) durability layer under the in-memory room registry.

Rooms live in memory for the hot WebSocket audio path (see rooms.py) - a DB
round-trip has no place inside a per-audio-chunk loop. This module is what
survives a server restart: room codes, who joined with what name/language,
and the latest negotiation sheet per room, so a term sheet isn't lost the
same way the original single-session /turn demo's file snapshot survives one.

Connection kwargs are parsed by hand from DATABASE_URL rather than handed to
asyncpg as a raw DSN, because Neon's connection string carries
`channel_binding=require`, a libpq-only query param asyncpg's DSN parser does
not recognise - passing it through raises "invalid DSN" before a connection
is ever attempted.
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlparse

import asyncpg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "")

_pool: asyncpg.Pool | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS meet_rooms (
    code TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS meet_participants (
    room_code TEXT NOT NULL REFERENCES meet_rooms(code) ON DELETE CASCADE,
    party_id TEXT NOT NULL,
    name TEXT NOT NULL,
    lang TEXT NOT NULL,
    out_lang TEXT NOT NULL DEFAULT '',
    joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (room_code, party_id)
);

-- Tables predating the speak/hear language split are missing out_lang.
ALTER TABLE meet_participants ADD COLUMN IF NOT EXISTS out_lang TEXT NOT NULL DEFAULT '';

-- Role and brief make the agent situated rather than generic: it knows which side
-- each speaker is on and what they came for, so "separate maintenance" reads
-- differently from the landlord than from the tenant.
ALTER TABLE meet_participants ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT '';
ALTER TABLE meet_participants ADD COLUMN IF NOT EXISTS brief TEXT NOT NULL DEFAULT '';
-- party_key is identity ACROSS rooms (a normalised name), which is what makes the
-- next negotiation between the same two people resumable.
ALTER TABLE meet_participants ADD COLUMN IF NOT EXISTS party_key TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS meet_participants_key_idx ON meet_participants (party_key);

-- Append-only spoken record, in each speaker's own words. Kept per room so a later
-- session between the same parties can be handed what was actually said last time
-- rather than a summary of a summary.
CREATE TABLE IF NOT EXISTS meet_transcripts (
    id BIGSERIAL PRIMARY KEY,
    room_code TEXT NOT NULL REFERENCES meet_rooms(code) ON DELETE CASCADE,
    party_id TEXT NOT NULL,
    party_key TEXT NOT NULL DEFAULT '',
    speaker_name TEXT NOT NULL,
    lang TEXT NOT NULL,
    text TEXT NOT NULL,
    said_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS meet_transcripts_room_idx ON meet_transcripts (room_code, id);

CREATE TABLE IF NOT EXISTS meet_sheets (
    room_code TEXT PRIMARY KEY REFERENCES meet_rooms(code) ON DELETE CASCADE,
    sheet_json JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# Semantic recall over the ENGLISH GLOSS only (see app/memory.py) - the two parties
# speak different languages, so embedding native text would scatter identical meaning
# across the vector space instead of letting it cluster.
VECTOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS meet_memory (
    id BIGSERIAL PRIMARY KEY,
    room_code TEXT,
    pair_key TEXT NOT NULL,
    term TEXT NOT NULL DEFAULT '',
    speaker_name TEXT NOT NULL,
    lang TEXT NOT NULL,
    text TEXT NOT NULL,
    gloss TEXT NOT NULL,
    embedding vector(1536),
    said_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS meet_memory_pair_idx ON meet_memory (pair_key);
CREATE INDEX IF NOT EXISTS meet_memory_embedding_idx ON meet_memory
    USING hnsw (embedding vector_cosine_ops);
"""


def _connect_kwargs() -> dict[str, Any]:
    u = urlparse(DATABASE_URL)
    return {
        "host": u.hostname,
        "port": u.port or 5432,
        "user": u.username,
        "password": u.password,
        "database": (u.path or "/").lstrip("/"),
        "ssl": "require",
    }


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not set - add it to .env before using the meet DB.")
        _pool = await asyncpg.create_pool(min_size=1, max_size=5, **_connect_kwargs())
        async with _pool.acquire() as conn:
            await conn.execute(SCHEMA)
            # A managed/shared Postgres role may lack CREATEEXTENSION rights - semantic
            # recall is best-effort, so its absence must not break the base schema apply.
            try:
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                await conn.execute(VECTOR_SCHEMA)
            except Exception:
                pass
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def insert_room(code: str) -> None:
    pool = await get_pool()
    await pool.execute("INSERT INTO meet_rooms (code) VALUES ($1) ON CONFLICT DO NOTHING", code)


async def room_exists(code: str) -> bool:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT 1 FROM meet_rooms WHERE code = $1", code)
    return row is not None


async def upsert_participant(code: str, party_id: str, name: str, lang: str,
                             out_lang: str = "") -> None:
    pool = await get_pool()
    await pool.execute(
        """INSERT INTO meet_participants (room_code, party_id, name, lang, out_lang)
           VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (room_code, party_id)
           DO UPDATE SET name = EXCLUDED.name, lang = EXCLUDED.lang,
                         out_lang = EXCLUDED.out_lang""",
        code, party_id, name, lang, out_lang or lang,
    )


async def remove_participant(code: str, party_id: str) -> None:
    pool = await get_pool()
    await pool.execute(
        "DELETE FROM meet_participants WHERE room_code = $1 AND party_id = $2", code, party_id
    )


async def save_sheet(code: str, sheet: dict[str, Any]) -> None:
    pool = await get_pool()
    await pool.execute(
        """INSERT INTO meet_sheets (room_code, sheet_json, updated_at)
           VALUES ($1, $2::jsonb, now())
           ON CONFLICT (room_code)
           DO UPDATE SET sheet_json = EXCLUDED.sheet_json, updated_at = now()""",
        code, json.dumps(sheet, ensure_ascii=False),
    )


async def load_sheet(code: str) -> dict[str, Any] | None:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT sheet_json FROM meet_sheets WHERE room_code = $1", code)
    if row is None:
        return None
    return json.loads(row["sheet_json"])


def party_key(name: str) -> str:
    """Identity across rooms. A normalised name is crude but it is what the two
    people actually re-enter, and asking them to remember an ID would defeat the
    point. Swap for a real account id the moment there is one."""
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


async def upsert_role(code: str, party_id: str, role: str, brief: str, key: str) -> None:
    pool = await get_pool()
    await pool.execute(
        """UPDATE meet_participants SET role = $3, brief = $4, party_key = $5
           WHERE room_code = $1 AND party_id = $2""",
        code, party_id, role, brief, key)


async def append_transcript(code: str, party_id: str, key: str, name: str,
                            lang: str, text: str) -> None:
    pool = await get_pool()
    await pool.execute(
        """INSERT INTO meet_transcripts
           (room_code, party_id, party_key, speaker_name, lang, text)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        code, party_id, key, name, lang, text)


async def prior_sessions(keys: list[str], exclude_code: str, limit: int = 2
                         ) -> list[dict[str, Any]]:
    """What these same people settled last time, most recent first.

    Matched on BOTH party keys so an unrelated negotiation by someone with the
    same name cannot leak in.
    """
    if len(keys) < 2:
        return []
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT s.room_code, s.sheet_json, s.updated_at
             FROM meet_sheets s
            WHERE s.room_code <> $3
              AND (SELECT COUNT(DISTINCT p.party_key) FROM meet_participants p
                    WHERE p.room_code = s.room_code AND p.party_key = ANY($1::text[])) = 2
            ORDER BY s.updated_at DESC
            LIMIT $2""",
        keys, limit, exclude_code)
    return [{"code": r["room_code"], "sheet": r["sheet_json"],
             "at": r["updated_at"]} for r in rows]


async def prior_transcript(keys: list[str], exclude_code: str, limit: int = 12
                           ) -> list[dict[str, Any]]:
    """The last thing actually said between these two, in their own words."""
    if len(keys) < 2:
        return []
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT speaker_name, lang, text, said_at, room_code
             FROM meet_transcripts
            WHERE party_key = ANY($1::text[]) AND room_code <> $3
            ORDER BY id DESC LIMIT $2""",
        keys, limit, exclude_code)
    return [dict(r) for r in reversed(rows)]
