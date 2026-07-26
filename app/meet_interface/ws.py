"""The live relay: browser PCM <-> Sarvam STT WS <-> mediator agent <-> TTS,
generalized from the two-hardcoded-parties blueprint into a per-room, per-
language WebSocket route.

One browser WebSocket connection per participant, at /api/meet/ws/{code}.
Each connection opens its own upstream Sarvam STT WebSocket (per-party, per-
language - `stt_stream.build_ws_url`/`classify` do the heavy lifting and are
reused untouched). Partial/final transcripts become live captions to BOTH
sides (translated for the listener, verbatim for the speaker - the second
costs no extra API call). On VAD turn-end, exactly one `agent.run_turn` call
(the same one the /turn demo uses) updates the room's Negotiation, and the
resulting relay sentence is spoken via TTS to the listener only.
"""
from __future__ import annotations

import asyncio
import base64
import json as _json

import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import agent, sarvam, stt_stream
from ..mediator import Turn
from . import languages, rooms

router = APIRouter()

# room_code -> {party_id: WebSocket}
PANELS: dict[str, dict[str, WebSocket]] = {}


async def _send(code: str, party_id: str, payload: dict) -> None:
    """Best-effort send to one participant. A closed/missing socket is not an
    error - the other party's turn must not fail because this side hung up."""
    ws = PANELS.get(code, {}).get(party_id)
    if ws is None:
        return
    try:
        await ws.send_json(payload)
    except Exception:  # noqa: BLE001
        pass


async def _safe_translate(text: str, target: str, source: str) -> str:
    """Never fail silently: a 429/timeout renders a visible placeholder so a
    dropped phrase looks like a gap, not a silently-lost word."""
    try:
        return await sarvam.translate(text, target, source)
    except Exception:  # noqa: BLE001
        return "…"


@router.websocket("/meet/ws/{code}")
async def ws_room(client: WebSocket, code: str) -> None:
    name = client.query_params.get("name", "Guest")
    lang = client.query_params.get("lang", "")
    await client.accept()

    reservation = await rooms.reserve_slot(code, name, lang)
    if reservation is None:
        await client.send_json({
            "type": "error",
            "text": "Could not join: room not found, already full, or unsupported language.",
        })
        await client.close(code=4004)
        return

    room, me = reservation
    other = room.other(me.party_id)
    PANELS.setdefault(room.code, {})[me.party_id] = client

    await client.send_json({
        "type": "joined",
        "you": {"party_id": me.party_id, "name": me.name, "lang": me.lang},
        "other": {"party_id": other.party_id, "name": other.name, "lang": other.lang} if other else None,
        "sheet": room.negotiation.sheet(),
    })
    if other is not None:
        await _send(room.code, other.party_id, {
            "type": "participant_joined",
            "party_id": me.party_id, "name": me.name, "lang": me.lang,
        })

    url = stt_stream.build_ws_url(me.lang)

    try:
        async with websockets.connect(url, additional_headers=stt_stream.WS_HEADERS) as up:
            async def pump_up() -> None:
                """Browser PCM16 frames -> Sarvam STT WS."""
                while True:
                    chunk = await client.receive_bytes()
                    await up.send(_json.dumps({
                        "audio": {"data": base64.b64encode(chunk).decode(), "encoding": "audio/wav"}
                    }))

            async def pump_down() -> None:
                """Sarvam STT WS -> live captions, and on turn-end, the agent."""
                buffer: list[str] = []
                async for raw in up:
                    kind, text = stt_stream.classify(_json.loads(raw))
                    other_now = room.other(me.party_id)

                    if kind in ("partial", "final"):
                        is_final = kind == "final"
                        if is_final:
                            buffer.append(text)
                        # Speaker sees their own words verbatim - no extra API call.
                        await _send(room.code, me.party_id, {
                            "type": "note", "final": is_final,
                            "from": me.name, "lang": me.lang, "text": text,
                        })
                        if other_now is not None:
                            translated = await _safe_translate(text, other_now.lang, me.lang)
                            await _send(room.code, other_now.party_id, {
                                "type": "note", "final": is_final,
                                "from": me.name, "lang": other_now.lang, "text": translated,
                            })

                    elif kind == "turn_end" and buffer:
                        await _finish_turn(room, me.party_id, " ".join(buffer))
                        buffer = []

                    elif kind == "error":
                        await _send(room.code, me.party_id, {"type": "error", "text": text})

            await asyncio.gather(pump_up(), pump_down())

    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        await _send(room.code, me.party_id, {"type": "error", "text": f"{type(e).__name__}: {e}"})
    finally:
        PANELS.get(room.code, {}).pop(me.party_id, None)
        await rooms.release_slot(room.code, me.party_id)
        remaining = room.other(me.party_id)
        if remaining is not None:
            await _send(room.code, remaining.party_id, {
                "type": "participant_left", "party_id": me.party_id, "name": me.name,
            })


async def _finish_turn(room: rooms.Room, party_id: str, transcript: str) -> None:
    """ONE sarvam-30b call per completed turn - never on a partial, matching
    the rate-budget discipline of the /turn demo and app/agent.py."""
    other = room.other(party_id)
    idx = len(room.negotiation.turns)

    speaker_lang = room.participants[party_id].lang
    try:
        res = await agent.run_turn(room.negotiation, party_id, speaker_lang, transcript, idx)
    except Exception as e:  # noqa: BLE001
        for pid in room.participants:
            await _send(room.code, pid, {"type": "error", "text": f"agent failed: {e}"})
        return

    spoken = res.clarification or res.summary
    audio = ""
    if spoken and other is not None:
        try:
            audio = await sarvam.tts(spoken, other.lang, languages.speaker(other.lang))
        except Exception:  # noqa: BLE001
            audio = ""

    room.negotiation.turns.append(Turn(
        idx=idx, party=party_id, lang=speaker_lang, transcript=transcript,
        relay_text=spoken, interjection=res.clarification,
    ))
    await rooms.persist_sheet(room)

    sheet = room.negotiation.sheet()
    speaker_name = room.participants[party_id].name
    common = {
        "type": "turn", "speaker": party_id, "speaker_name": speaker_name,
        "transcript": transcript, "relay_text": spoken,
        "flagged": res.flagged, "sheet": sheet,
    }
    if other is not None:
        await _send(room.code, other.party_id, {**common, "audio_b64": audio})
    await _send(room.code, party_id, {**common, "audio_b64": ""})
