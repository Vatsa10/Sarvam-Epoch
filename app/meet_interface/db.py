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
    joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (room_code, party_id)
);

CREATE TABLE IF NOT EXISTS meet_sheets (
    room_code TEXT PRIMARY KEY REFERENCES meet_rooms(code) ON DELETE CASCADE,
    sheet_json JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
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


async def upsert_participant(code: str, party_id: str, name: str, lang: str) -> None:
    pool = await get_pool()
    await pool.execute(
        """INSERT INTO meet_participants (room_code, party_id, name, lang)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (room_code, party_id)
           DO UPDATE SET name = EXCLUDED.name, lang = EXCLUDED.lang""",
        code, party_id, name, lang,
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
