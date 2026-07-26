"""Router for the real-time meet: room lifecycle (create/lookup), the
language picker list, and the live WebSocket relay (ws.py).

Included into the main app under /api by app/main.py, so everything here is
reachable at /api/meet/*.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import languages, rooms
from .ws import router as ws_router

router = APIRouter()
router.include_router(ws_router)


# ---------- languages ----------

@router.get("/meet/languages")
async def list_languages() -> list[dict[str, str]]:
    return languages.as_list()


# ---------- rooms ----------

class RoomOut(BaseModel):
    code: str
    participant_count: int
    is_full: bool


def _room_out(room: rooms.Room) -> RoomOut:
    return RoomOut(code=room.code, participant_count=len(room.participants), is_full=room.is_full())


@router.post("/meet/rooms")
async def create_room() -> RoomOut:
    room = await rooms.create_room()
    return _room_out(room)


@router.get("/meet/rooms/{code}")
async def get_room(code: str) -> RoomOut:
    room = await rooms.get_or_restore_room(code)
    if room is None:
        raise HTTPException(status_code=404, detail=f"No meeting with code {code.upper()}")
    return _room_out(room)
