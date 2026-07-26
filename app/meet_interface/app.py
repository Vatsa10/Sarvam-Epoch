"""Router for the real-time meet: room lifecycle (create/lookup), the
language picker list, and the live WebSocket relay (ws.py).

Included into the main app under /api by app/main.py, so everything here is
reachable at /api/meet/*.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .. import drafter
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


@router.get("/meet/rooms/{code}/draft", response_class=HTMLResponse)
async def draft_room(code: str, lang: str = "en-IN") -> HTMLResponse:
    """The lawyer's draft for a live room, in the LAWYER's language (`?lang=`),
    which may be a third language neither party in the call speaks.

    Drafted from the room's term sheet, with the call transcript as supporting
    evidence: it adds each party's own words as provenance, and a figure in the
    transcript that contradicts the sheet turns that term into an open question.
    Settled terms still get clauses; nothing else ever does.

    HTML rather than JSON on purpose — the frontend is one `window.open`, and the
    page is already print-to-PDF ready for the lawyer.
    """
    room = await rooms.get_or_restore_room(code)
    if room is None or getattr(room, "negotiation", None) is None:
        raise HTTPException(status_code=404, detail=f"No meeting with code {code.upper()}")
    d = await drafter.draft(room.negotiation, lawyer_lang=lang,
                            transcript=getattr(room, "transcript", []))
    return HTMLResponse(drafter.render(d, room.negotiation))
