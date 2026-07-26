"""In-memory room registry for the real-time meet, backed by Postgres (db.py)
for durability.

A room is a short code mapping to a per-room Negotiation (reusing the same
mediator state machine the /turn demo uses) plus up to two connected
participants. No accounts, no auth - the code IS the access control. Room
objects live in memory for the hot WebSocket audio path; every join and every
completed turn is also written to Postgres so a room's term sheet survives a
server restart even though its live WebSocket connections obviously cannot -
a client that reconnects with the same code gets the sheet back via
`get_or_restore_room`.
"""
from __future__ import annotations

import random
import string
from dataclasses import dataclass, field
from typing import Awaitable, TypeVar

from .. import session
from ..mediator import Negotiation
from . import db, languages

MAX_PARTICIPANTS = 2
_CODE_ALPHABET = string.ascii_uppercase + string.digits
_CODE_LEN = 6

T = TypeVar("T")


async def _best_effort(coro: Awaitable[T], default: T) -> T:
    """Postgres is a durability nice-to-have, not the source of truth for a
    live call - a room must still be creatable/joinable with no DATABASE_URL
    configured (e.g. tests) or a momentarily-unreachable Neon endpoint. Only
    the "survives a restart" guarantee degrades; the call itself doesn't."""
    try:
        return await coro
    except Exception as e:  # noqa: BLE001
        print(f"[meet_interface.db] degraded (in-memory only): {type(e).__name__}: {e}")
        return default


@dataclass
class Participant:
    party_id: str          # "p1" | "p2" - stable identity within the room
    name: str
    lang: str


@dataclass
class Room:
    code: str
    negotiation: Negotiation
    participants: dict[str, Participant] = field(default_factory=dict)

    def other(self, party_id: str) -> Participant | None:
        for pid, p in self.participants.items():
            if pid != party_id:
                return p
        return None

    def is_full(self) -> bool:
        return len(self.participants) >= MAX_PARTICIPANTS


_ROOMS: dict[str, Room] = {}


def _generate_code() -> str:
    while True:
        code = "".join(random.choices(_CODE_ALPHABET, k=_CODE_LEN))
        if code not in _ROOMS:
            return code


async def create_room() -> Room:
    code = _generate_code()
    room = Room(code=code, negotiation=Negotiation(session_id=code, parties=("p1", "p2")))
    _ROOMS[code] = room
    await _best_effort(db.insert_room(code), None)
    return room


async def get_or_restore_room(code: str) -> Room | None:
    """In-memory hit first; otherwise check Postgres for a room that existed
    before a restart and rebuild it (empty of live participants - those can
    only ever be in-memory) with its last saved sheet folded back in."""
    code = code.upper()
    room = _ROOMS.get(code)
    if room is not None:
        return room

    if not await _best_effort(db.room_exists(code), False):
        return None

    neg = Negotiation(session_id=code, parties=("p1", "p2"))
    sheet = await _best_effort(db.load_sheet(code), None)
    if sheet:
        session.apply_snapshot(neg, sheet)
    room = Room(code=code, negotiation=neg)
    _ROOMS[code] = room
    return room


async def reserve_slot(code: str, name: str, lang: str) -> tuple[Room, Participant] | None:
    """Claim a party_id in the room, or None if full/missing/unsupported language."""
    if not languages.is_supported(lang):
        return None
    room = await get_or_restore_room(code)
    if room is None or room.is_full():
        return None
    party_id = "p1" if "p1" not in room.participants else "p2"
    participant = Participant(party_id=party_id, name=name.strip()[:40] or "Guest", lang=lang)
    room.participants[party_id] = participant
    await _best_effort(db.upsert_participant(room.code, party_id, participant.name, participant.lang), None)
    return room, participant


async def release_slot(code: str, party_id: str) -> None:
    room = _ROOMS.get(code.upper())
    if room is not None:
        room.participants.pop(party_id, None)
    await _best_effort(db.remove_participant(code.upper(), party_id), None)


async def persist_sheet(room: Room) -> None:
    await _best_effort(db.save_sheet(room.code, room.negotiation.sheet()), None)
