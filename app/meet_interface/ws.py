"""The live relay: browser PCM <-> Sarvam STT WS <-> mediator agent <-> TTS,
generalized from the two-hardcoded-parties blueprint into a per-room, per-
language WebSocket route.

One browser WebSocket connection per participant, at /api/meet/ws/{code}.
Each connection opens its own upstream Sarvam STT WebSocket (per-party, per-
language - `stt_stream.build_ws_url`/`classify` do the heavy lifting and are
reused untouched). Partial/final transcripts become live captions to BOTH
sides (translated for the listener, verbatim for the speaker - the second
costs no extra API call).

Turn-taking is push-to-talk, not VAD-driven: `Room.turn_holder`/`floor_open`
(rooms.py) form a strict two-state lock so the two parties can never be
transcribing at once - one talks, the other physically cannot until the
first clicks "Done". The client drives it with two JSON control frames,
"talk_start"/"talk_done"; audio bytes arriving outside an open floor held by
the sender are silently dropped (defense - the browser shouldn't send them
either). On "talk_done", exactly one `agent.run_turn` call (the same one the
/turn demo uses) updates the room's Negotiation, the resulting relay sentence
is spoken via TTS to the listener only, and the lock flips to them.
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
        "turn_holder": room.turn_holder, "floor_open": room.floor_open,
    })
    if other is not None:
        await _send(room.code, other.party_id, {
            "type": "participant_joined",
            "party_id": me.party_id, "name": me.name, "lang": me.lang,
        })

    url = stt_stream.build_ws_url(me.lang)

    try:
        async with websockets.connect(url, additional_headers=stt_stream.WS_HEADERS) as up:
            # Shared with pump_down via closure; reset on every talk_start,
            # drained on talk_done. A dict (not a bare list rebind) so both
            # tasks always see the current buffer without `nonlocal` juggling.
            state = {"buffer": []}

            async def pump_up() -> None:
                """Browser frames -> either Sarvam STT WS (audio, only while
                this party holds an open floor) or the turn-lock state
                machine (JSON control frames)."""
                while True:
                    msg = await client.receive()
                    if msg.get("type") == "websocket.disconnect":
                        raise WebSocketDisconnect(msg.get("code", 1000))
                    if msg.get("type") != "websocket.receive":
                        continue

                    raw_bytes = msg.get("bytes")
                    if raw_bytes is not None:
                        if room.turn_holder == me.party_id and room.floor_open:
                            await up.send(_json.dumps({
                                "audio": {
                                    "data": base64.b64encode(raw_bytes).decode(),
                                    "encoding": "audio/wav",
                                    "sample_rate": 16000,
                                }
                            }))
                        continue

                    raw_text = msg.get("text")
                    if raw_text is None:
                        continue
                    try:
                        ctrl = _json.loads(raw_text)
                    except ValueError:
                        continue
                    await _handle_control(room, me, ctrl.get("type", ""), state)

            async def pump_down() -> None:
                """Sarvam STT WS -> live captions for whoever currently holds
                the floor. Finals accumulate into `state["buffer"]`; talk_done
                (not VAD) is what drains it into an agent turn."""
                async for raw in up:
                    kind, text = stt_stream.classify(_json.loads(raw))
                    if kind not in ("partial", "final", "error"):
                        continue  # turn_end (VAD) is ignored - talk_done drives turns now
                    other_now = room.other(me.party_id)

                    if kind == "error":
                        await _send(room.code, me.party_id, {"type": "error", "text": text})
                        continue

                    if room.turn_holder != me.party_id or not room.floor_open:
                        continue  # stray STT frame after floor closed - drop it

                    is_final = kind == "final"
                    if is_final:
                        state["buffer"].append(text)
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

            await asyncio.gather(pump_up(), pump_down())

    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        await _send(room.code, me.party_id, {"type": "error", "text": f"{type(e).__name__}: {e}"})
    finally:
        PANELS.get(room.code, {}).pop(me.party_id, None)
        await rooms.release_slot(room.code, me.party_id)
        remaining = room.other(me.party_id)
        # A party disconnecting mid-turn must not permanently strand the
        # lock - hand it to whoever's left so the call can continue.
        if room.turn_holder == me.party_id:
            room.floor_open = False
            if remaining is not None:
                room.turn_holder = remaining.party_id
        if remaining is not None:
            await _send(room.code, remaining.party_id, {
                "type": "participant_left", "party_id": me.party_id, "name": me.name,
            })
            await _send(room.code, remaining.party_id, {
                "type": "floor", "holder": room.turn_holder, "open": room.floor_open,
            })


async def _handle_control(room: rooms.Room, me: rooms.Participant, kind: str, state: dict) -> None:
    if kind == "talk_start":
        if room.turn_holder != me.party_id:
            await _send(room.code, me.party_id, {"type": "error", "text": "Not your turn to talk."})
            return
        if not room.is_full():
            await _send(room.code, me.party_id, {"type": "error", "text": "Waiting for the other participant."})
            return
        if room.floor_open:
            return  # already open - duplicate click, ignore
        room.floor_open = True
        state["buffer"] = []
        for pid in room.participants:
            await _send(room.code, pid, {"type": "floor", "holder": room.turn_holder, "open": True})

    elif kind == "talk_done":
        if room.turn_holder != me.party_id or not room.floor_open:
            return
        room.floor_open = False
        transcript = " ".join(state["buffer"]).strip()
        state["buffer"] = []
        if transcript:
            await _finish_turn(room, me.party_id, transcript)
        # Flip the lock regardless of whether anything was said - a party
        # that clicks Done with nothing captured must not deadlock the call.
        other = room.other(me.party_id)
        room.turn_holder = other.party_id if other is not None else me.party_id
        for pid in room.participants:
            await _send(room.code, pid, {"type": "floor", "holder": room.turn_holder, "open": False})


async def _finish_turn(room: rooms.Room, party_id: str, transcript: str) -> None:
    """ONE sarvam-30b call per completed turn, triggered by talk_done."""
    other = room.other(party_id)
    idx = len(room.negotiation.turns)

    speaker_lang = room.participants[party_id].lang

    # English gloss first. Without it gpt-4o-mini misreads Indic numerals - measured,
    # it turned Malayalam "പതിനയ്യായിരം" (15000) into 17000 and invented a divergence
    # between two parties who had agreed. /translate is a separate rate bucket, so
    # this costs nothing from the reasoning budget.
    try:
        gloss = await sarvam.translate(transcript, "en-IN", speaker_lang)
    except Exception:  # noqa: BLE001
        gloss = ""

    try:
        res = await agent.run_turn(room.negotiation, party_id, speaker_lang,
                                   transcript, idx, gloss=gloss or None)
    except Exception as e:  # noqa: BLE001
        for pid in room.participants:
            await _send(room.code, pid, {"type": "error", "text": f"agent failed: {e}"})
        return

    # res.summary is plain ENGLISH by design (see agent.SYSTEM). Feeding it straight
    # to Bulbul with an Indian target language makes the listener hear English read
    # in a Malayalam voice - translate before speaking.
    spoken = res.clarification or res.summary or gloss
    audio = ""
    if spoken and other is not None:
        try:
            spoken = await sarvam.translate(spoken, other.lang, "en-IN",
                                            mode="formal") or spoken
        except Exception:  # noqa: BLE001
            pass
        try:
            # Slower when the sheet just broke or a value is in doubt - a mediator
            # that flags a divergence at the same clip as a routine relay sounds
            # like it did not notice.
            audio = await sarvam.tts(
                spoken, other.lang, languages.speaker(other.lang),
                pace=sarvam.pace_for(
                    sensitive=bool(res.flagged or res.clarification),
                    relaying=not (res.flagged or res.clarification)),
            )
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
