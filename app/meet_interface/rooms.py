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

from .. import memory, session
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
    lang: str              # what they SPEAK - the STT source language
    out_lang: str = ""     # what they want to SEE/HEAR - captions + TTS target.
    role: str = ""         # "landlord" | "tenant" - who they are in this deal
    brief: str = ""        # what they came for, in their own words
    key: str = ""          # identity ACROSS rooms, so a rematch can be resumed
                           # Empty means "same as lang" (backward compat).

    def __post_init__(self) -> None:
        if not self.out_lang:
            self.out_lang = self.lang


@dataclass
class Room:
    code: str
    negotiation: Negotiation
    participants: dict[str, Participant] = field(default_factory=dict)
    # Push-to-talk lock: only `turn_holder` may open the floor and stream audio.
    # `floor_open` is true only between that party's "talk_start" and "talk_done" -
    # both flip together, so the two parties can never be transcribing at once.
    turn_holder: str = "p1"
    floor_open: bool = False
    # Append-only record of every finalized turn, in the speaker's own words.
    # CONTRACT (consumed by the drafter): each entry is exactly
    # {"speaker_id": str, "speaker_name": str, "lang": str, "text": str, "ts": float}
    transcript: list[dict] = field(default_factory=list)

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


OPPOSITE = {"landlord": "tenant", "tenant": "landlord"}


async def reserve_slot(code: str, name: str, lang: str, out_lang: str = "",
                       role: str = "", brief: str = "") -> tuple[Room, Participant] | None:
    """Claim a party_id in the room, or None if full/missing/unsupported language.

    `out_lang` (captions + TTS target) defaults to `lang` when absent."""
    out_lang = out_lang or lang
    if not languages.is_supported(lang) or not languages.is_supported(out_lang):
        return None
    room = await get_or_restore_room(code)
    if room is None or room.is_full():
        return None
    party_id = "p1" if "p1" not in room.participants else "p2"

    # The second person never picks a side. Whoever created the room declared
    # theirs, so the joiner is the other one by construction - two landlords
    # cannot negotiate a lease, and asking twice invites exactly that.
    role = (role or "").strip().lower()
    if party_id == "p2":
        first = room.participants.get("p1")
        if first is not None and first.role:
            role = OPPOSITE.get(first.role, role)

    participant = Participant(party_id=party_id, name=name.strip()[:40] or "Guest",
                              lang=lang, out_lang=out_lang, role=role,
                              brief=(brief or "").strip()[:400],
                              key=db.party_key(name))
    room.participants[party_id] = participant
    await _best_effort(
        db.upsert_participant(room.code, party_id, participant.name,
                              participant.lang, participant.out_lang), None)
    await _best_effort(
        db.upsert_role(room.code, party_id, participant.role,
                       participant.brief, participant.key), None)
    return room, participant


async def set_out_lang(room: Room, participant: Participant, out_lang: str) -> bool:
    """Mid-call change of the language this participant reads/hears."""
    if not languages.is_supported(out_lang):
        return False
    participant.out_lang = out_lang
    await _best_effort(
        db.upsert_participant(room.code, participant.party_id, participant.name,
                              participant.lang, participant.out_lang), None)
    return True


async def release_slot(code: str, party_id: str) -> None:
    room = _ROOMS.get(code.upper())
    if room is not None:
        room.participants.pop(party_id, None)
    await _best_effort(db.remove_participant(code.upper(), party_id), None)


async def persist_sheet(room: Room) -> None:
    await _best_effort(db.save_sheet(room.code, room.negotiation.sheet()), None)


async def party_context(room: "Room", gloss: str | None = None) -> str:
    """Who these two are, what they came for, and what they settled last time.

    Prepended to every agent turn. Without it the mediator is generic: it cannot
    tell which side "maintenance separate" is being argued FROM, and two people who
    negotiated last week start from zero. Every lookup is best-effort - missing
    history must never block a live turn.
    """
    lines = []
    for p in room.participants.values():
        who = f"- {p.name} ({p.lang})"
        if p.role:
            who += f" is the {p.role.upper()}"
        if p.brief:
            who += f". They came for: {p.brief}"
        lines.append(who)
    block = "PARTIES\n" + ("\n".join(lines) if lines else "- (unknown)")

    keys = [p.key for p in room.participants.values() if p.key]
    if len(keys) < 2:
        return block

    prior = await _best_effort(db.prior_sessions(keys, room.code), [])
    if prior:
        settled = []
        for term in (prior[0].get("sheet") or {}).get("terms", []):
            if term.get("state") == "AGREED" and term.get("agreed_value"):
                settled.append(f"{term['key']} = {term['agreed_value']}")
            elif term.get("state") in ("DIVERGED", "HEDGED"):
                settled.append(f"{term['key']} left UNRESOLVED ({term['state']})")
        if settled:
            block += (
                "\n\nWHEN THESE TWO LAST SPOKE they had: " + "; ".join(settled[:6])
                + ".\nTreat that as the starting point, NOT as already re-agreed - "
                  "anything they want carried over they must restate now."
            )

    said = await _best_effort(db.prior_transcript(keys, room.code, limit=6), [])
    if said:
        block += "\n\nTHEIR LAST FEW WORDS LAST TIME\n" + "\n".join(
            f"- {r['speaker_name']}: {r['text']}" for r in said)

    # Semantic recall: what this SAME pair said about this SAME topic in a
    # DIFFERENT room, found by meaning rather than exact wording - exact-match
    # history above misses it whenever the topic resurfaces mid-call, not just
    # at the top of the next session.
    if gloss:
        pair = memory.pair_key(keys)
        rows = await _best_effort(memory.recall(pair, gloss, room.code), [])
        recalled = memory.format_recall(rows)
        if recalled:
            block += "\n\n" + recalled
    return block


async def record_utterance(room: "Room", party_id: str, text: str) -> None:
    """Persist one spoken turn. Never raises - the call outranks the archive."""
    p = room.participants.get(party_id)
    if p is None or not text.strip():
        return
    await _best_effort(
        db.append_transcript(room.code, party_id, p.key, p.name, p.lang, text), None)
