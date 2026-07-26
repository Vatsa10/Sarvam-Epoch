"""Room registry for the real-time meet - no network, no DB needed.

Postgres calls inside app/meet_interface/rooms.py degrade to a no-op when
DATABASE_URL isn't set (see rooms._best_effort). Blanking it out here, even
though the real .env has a live Neon URL, keeps this file true to the
project's no-network test convention: it exercises exactly the in-memory
behaviour production falls back to if Neon is ever unreachable, and it never
writes throwaway room codes into the real database.

    python test_rooms.py
"""
import asyncio

from app.meet_interface import db as meet_db
from app.meet_interface import rooms

meet_db.DATABASE_URL = ""


def _run(coro):
    return asyncio.run(coro)


def test_create_room_has_unique_six_char_code():
    room = _run(rooms.create_room())
    assert len(room.code) == 6
    assert room.code.isalnum()
    assert room.negotiation.parties == ("p1", "p2")


def test_first_two_joiners_get_p1_and_p2():
    room = _run(rooms.create_room())
    r1, a = _run(rooms.reserve_slot(room.code, "Alice", "en-IN"))
    r2, b = _run(rooms.reserve_slot(room.code, "Bob", "hi-IN"))
    assert a.party_id == "p1"
    assert b.party_id == "p2"
    assert r1 is r2 is room
    assert room.is_full()


def test_third_joiner_is_rejected():
    room = _run(rooms.create_room())
    _run(rooms.reserve_slot(room.code, "Alice", "en-IN"))
    _run(rooms.reserve_slot(room.code, "Bob", "hi-IN"))
    assert _run(rooms.reserve_slot(room.code, "Carol", "ta-IN")) is None


def test_unsupported_language_is_rejected():
    room = _run(rooms.create_room())
    assert _run(rooms.reserve_slot(room.code, "Alice", "fr-FR")) is None


def test_unknown_code_is_rejected():
    assert _run(rooms.reserve_slot("ZZZZZZ", "Alice", "en-IN")) is None


def test_other_returns_the_other_participant():
    room = _run(rooms.create_room())
    _run(rooms.reserve_slot(room.code, "Alice", "en-IN"))
    _run(rooms.reserve_slot(room.code, "Bob", "hi-IN"))
    assert room.other("p1").name == "Bob"
    assert room.other("p2").name == "Alice"


def test_release_slot_frees_room_for_a_new_joiner():
    room = _run(rooms.create_room())
    _run(rooms.reserve_slot(room.code, "Alice", "en-IN"))
    _run(rooms.reserve_slot(room.code, "Bob", "hi-IN"))
    _run(rooms.release_slot(room.code, "p1"))
    assert not room.is_full()
    _, c = _run(rooms.reserve_slot(room.code, "Carol", "ta-IN"))
    assert c.party_id == "p1"


def test_get_or_restore_room_hits_in_memory_cache():
    room = _run(rooms.create_room())
    same = _run(rooms.get_or_restore_room(room.code))
    assert same is room


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f()
            print(f"  [OK] {n}")
    print("\nrooms sound\n")
