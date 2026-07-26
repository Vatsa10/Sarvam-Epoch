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
import time

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
    # Absent out_lang means "I read/hear what I speak" - the pre-split behaviour.
    out_lang = client.query_params.get("out_lang", "")
    await client.accept()

    reservation = await rooms.reserve_slot(code, name, lang, out_lang)
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
        "you": {"party_id": me.party_id, "name": me.name, "lang": me.lang, "out_lang": me.out_lang},
        "other": {"party_id": other.party_id, "name": other.name, "lang": other.lang,
                  "out_lang": other.out_lang} if other else None,
        "sheet": room.negotiation.sheet(),
        "turn_holder": room.turn_holder, "floor_open": room.floor_open,
    })
    if other is not None:
        await _send(room.code, other.party_id, {
            "type": "participant_joined",
            "party_id": me.party_id, "name": me.name, "lang": me.lang,
            "out_lang": me.out_lang,
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
                    await _handle_control(room, me, ctrl.get("type", ""), state, ctrl)

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
                    # Every caption is rendered in ITS OWN reader's output
                    # language - including the speaker's, who may want to read
                    # something other than what they speak. Same-language stays
                    # verbatim and costs no extra API call.
                    for listener in (me, other_now):
                        if listener is None:
                            continue
                        target, _ = languages.route(me.lang, listener.out_lang)
                        shown = text if target is None else await _safe_translate(text, target, me.lang)
                        await _send(room.code, listener.party_id, {
                            "type": "note", "final": is_final,
                            "from": me.name, "lang": listener.out_lang, "text": shown,
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


async def _handle_control(room: rooms.Room, me: rooms.Participant, kind: str, state: dict,
                          ctrl: dict | None = None) -> None:
    if kind == "set_out_lang":
        want = (ctrl or {}).get("lang", "")
        if not await rooms.set_out_lang(room, me, want):
            await _send(room.code, me.party_id, {"type": "error", "text": "Unsupported language."})
            return
        await _send(room.code, me.party_id, {"type": "out_lang", "party_id": me.party_id, "lang": me.out_lang})
        other = room.other(me.party_id)
        if other is not None:
            await _send(room.code, other.party_id,
                        {"type": "out_lang", "party_id": me.party_id, "lang": me.out_lang})

    elif kind == "talk_start":
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

        if not transcript:
            # Nothing was captured. Handing the floor to the other party here is
            # the worst option: this speaker loses their turn, the listener gets
            # silence, and nobody is told why. Keep the floor, say exactly what
            # happened and exactly what to do - and lose no negotiated state.
            room.turn_holder = me.party_id
            await _send(room.code, me.party_id, {
                "type": "recover",
                "text": ("Nothing was picked up that time. Your turn is still open — "
                         "press Talk, wait a moment, then speak. Nothing agreed so far "
                         "has been lost."),
            })
            other = room.other(me.party_id)
            if other is not None:
                await _send(room.code, other.party_id, {
                    "type": "recover",
                    "text": (f"{me.name} is still speaking — their audio did not come "
                             f"through. Nothing has been lost."),
                })
            for pid in room.participants:
                await _send(room.code, pid, {"type": "floor",
                                             "holder": room.turn_holder, "open": False})
            return

        await _finish_turn(room, me.party_id, transcript)
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
    voice = None
    if spoken and other is not None:
        # The listener hears their OUTPUT language, in that language's Bulbul
        # voice - never the sender's.
        _, voice = languages.route(speaker_lang, other.out_lang)
        try:
            spoken = await sarvam.translate(spoken, other.out_lang, "en-IN",
                                            mode="formal") or spoken
        except Exception:  # noqa: BLE001
            pass

    room.negotiation.turns.append(Turn(
        idx=idx, party=party_id, lang=speaker_lang, transcript=transcript,
        relay_text=spoken, interjection=res.clarification,
    ))
    await rooms.persist_sheet(room)

    sheet = room.negotiation.sheet()
    speaker_name = room.participants[party_id].name
    # Append-only turn log in the speaker's own words - see Room.transcript.
    room.transcript.append({
        "speaker_id": party_id, "speaker_name": speaker_name,
        "lang": speaker_lang, "text": transcript, "ts": time.time(),
    })

    # TEXT FIRST, AUDIO AFTER. Bulbul takes about a second, and holding the whole
    # turn for it leaves the listener staring at nothing while the speaker has
    # visibly finished. The reader gets the translation the moment it exists; the
    # voice catches up. `turn_idx` lets the client match the late audio to the row
    # it already rendered.
    common = {
        "type": "turn", "turn_idx": idx, "speaker": party_id,
        "speaker_name": speaker_name, "transcript": transcript,
        "relay_text": spoken, "flagged": res.flagged, "sheet": sheet,
        "speaking": bool(spoken and other is not None),
    }
    for pid in room.participants:
        await _send(room.code, pid, common)

    # A DEADLOCK IS A MOMENT OF FRICTION, NOT JUST A RED ROW. Flagging a term and
    # stopping there leaves two people who believe they agreed staring at a colour.
    # Give each side the same three ordered options, in their own language, so the
    # call has somewhere to go.
    if res.flagged:
        for key in res.flagged[:1]:
            term = room.negotiation.terms.get(key)
            if term is None:
                continue
            mine = term.latest_by(party_id)
            theirs = term.latest_by(other.party_id) if other is not None else None
            opts = (
                f"You two have not actually agreed on {key}. "
                f"You said {mine.value if mine else 'nothing yet'}; "
                f"they said {theirs.value if theirs else 'nothing yet'}. "
                f"Three ways forward: 1) one of you restates a single number you both "
                f"repeat back, 2) split the difference and say the exact figure, or "
                f"3) park {key} and settle the other terms first."
            )
            for pid, p_obj in room.participants.items():
                text = opts
                if p_obj.out_lang != "en-IN":
                    try:
                        text = await sarvam.translate(opts, p_obj.out_lang, "en-IN",
                                                      mode="formal") or opts
                    except Exception:  # noqa: BLE001
                        pass
                await _send(room.code, pid, {"type": "resolve", "term": key,
                                             "text": text})

    if spoken and other is not None and voice:
        try:
            # Slower when the sheet just broke or a value is in doubt - a mediator
            # that flags a divergence at the same clip as a routine relay sounds
            # like it did not notice.
            audio = await sarvam.tts(
                spoken, other.out_lang, voice,
                pace=sarvam.pace_for(
                    sensitive=bool(res.flagged or res.clarification),
                    relaying=not (res.flagged or res.clarification)),
            )
        except Exception:  # noqa: BLE001
            audio = ""
        # Even an empty payload is worth sending: it clears the listener's
        # "speaking shortly" state instead of leaving it waiting forever.
        await _send(room.code, other.party_id,
                    {"type": "audio", "turn_idx": idx, "audio_b64": audio})
