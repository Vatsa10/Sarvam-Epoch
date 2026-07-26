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

from .. import agent, memory, sarvam, stt_stream
from ..mediator import TermState, Turn
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
    # Declared once, on the way in. The joiner never picks a side - reserve_slot
    # gives them the opposite of whoever created the room.
    role = client.query_params.get("role", "")
    brief = client.query_params.get("brief", "")
    await client.accept()

    reservation = await rooms.reserve_slot(code, name, lang, out_lang, role, brief)
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
        "you": {"party_id": me.party_id, "name": me.name, "lang": me.lang,
                "out_lang": me.out_lang, "role": me.role, "brief": me.brief},
        "other": {"party_id": other.party_id, "name": other.name, "lang": other.lang,
                  "out_lang": other.out_lang, "role": other.role,
                  "brief": other.brief} if other else None,
        "sheet": room.negotiation.sheet(),
        "turn_holder": room.turn_holder, "floor_open": room.floor_open,
    })
    if other is not None:
        await _send(room.code, other.party_id, {
            "type": "participant_joined",
            "party_id": me.party_id, "name": me.name, "lang": me.lang,
            "out_lang": me.out_lang, "role": me.role, "brief": me.brief,
        })

    url = stt_stream.build_ws_url(me.lang)

    try:
        # ping_interval keeps the upstream alive through the silence between turns.
        # A negotiation has long gaps - people think before they answer - and an
        # idle-closed STT socket used to take the participant's whole session with it.
        async with websockets.connect(
            url, additional_headers=stt_stream.WS_HEADERS,
            ping_interval=20, ping_timeout=20, close_timeout=5,
        ) as up:
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
                    await _handle_control(room, me, ctrl.get("type", ""), state, ctrl, up)

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

            # pump_down ending means the UPSTREAM died, not the user. Tearing the
            # browser socket down there would eject them from the room over a
            # Sarvam-side timeout - which is exactly what was happening. Wait on
            # pump_up (the real user connection) and let a dead upstream degrade
            # to "captions stopped" instead of "you have left the call".
            up_task = asyncio.ensure_future(pump_up())
            down_task = asyncio.ensure_future(pump_down())
            try:
                done, pending = await asyncio.wait(
                    {up_task, down_task}, return_when=asyncio.FIRST_COMPLETED)
                if down_task in done and up_task not in done:
                    exc = down_task.exception()
                    await _send(room.code, me.party_id, {
                        "type": "error",
                        "text": "Speech recognition dropped. Rejoin the room to "
                                "restore captions — the term sheet is safe.",
                    })
                    if exc is not None:
                        print(f"[meet] upstream STT closed for {me.party_id}: "
                              f"{type(exc).__name__}")
                    await up_task          # keep serving the user until THEY leave
            finally:
                for task in (up_task, down_task):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(up_task, down_task, return_exceptions=True)

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
                          ctrl: dict | None = None, up=None) -> None:
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
        # Saaras emits its final AFTER the last audio frame lands, so closing
        # the floor the instant Done is pressed drops that final in pump_down
        # and the whole turn silently disappears. Hold the floor open until a
        # final arrives, then give a short grace for a trailing second one.
        # ponytail: fixed 6s ceiling; make it adaptive only if a long turn
        # is observed to time out.
        # Tell Saaras the utterance is over. The browser has stopped sending
        # frames, so without this there is no audio left to trigger its VAD and
        # the final never comes - the turn silently disappears.
        if up is not None:
            try:
                await up.send(_json.dumps({"type": "flush"}))
            except Exception:  # noqa: BLE001
                pass
        for _ in range(24):
            if state["buffer"]:
                break
            await asyncio.sleep(0.25)
        if state["buffer"]:
            await asyncio.sleep(0.6)
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


    # Who these two are, what they each came for, and what they settled the last
    # time they spoke. Best-effort by construction - no history must ever block a
    # live turn.
    # HARD TIMEOUT, not just best-effort. party_context does a pgvector query and an
    # OpenAI embedding, and it sits between the speaker finishing and the listener
    # hearing anything. Useful context is worth ~1.5s; it is never worth a stalled
    # call, so a slow lookup is dropped rather than waited on.
    try:
        preamble = await asyncio.wait_for(
            rooms.party_context(room, gloss=gloss or None), timeout=1.5)
    except (Exception, asyncio.TimeoutError):  # noqa: BLE001
        preamble = ""

    try:
        parties = {
            pid: {"name": p.name, "lang": p.lang,
                  "label": languages.label(p.lang) or p.lang}
            for pid, p in room.participants.items()
        }
        res = await agent.run_turn(room.negotiation, party_id, speaker_lang,
                                   transcript, idx, gloss=gloss or None,
                                   preamble=preamble, parties=parties)
    except Exception as e:  # noqa: BLE001
        # The agent failing must not swallow the turn. The two people are mid-call:
        # relaying what was said with no sheet update is far better than silence
        # plus an error code, which is what used to happen.
        print(f"[meet] agent failed on turn {idx}: {type(e).__name__}: {e}")
        res = agent.TurnResult(summary=gloss or transcript)

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

    async def _tell_both(term_key: str, text: str) -> None:
        """Same escalation-ladder pattern as the deadlock messages below: plain
        English drafted once, translated per listener, English on failure."""
        for pid, p_obj in room.participants.items():
            out = text
            if p_obj.out_lang != "en-IN":
                try:
                    out = await sarvam.translate(text, p_obj.out_lang, "en-IN",
                                                  mode="formal") or text
                except Exception:  # noqa: BLE001
                    pass
            await _send(room.code, pid, {"type": "resolve", "term": term_key, "text": out})

    # RE-OPENED TERM. A term that was SETTLED and just came undone is the single
    # change most likely to end up in a signed document unnoticed - both parties
    # must be told plainly, naming the value that was agreed and who reopened it.
    for key in res.flagged:
        term = room.negotiation.terms.get(key)
        if term is not None and term.reopened_from is not None:
            await _tell_both(key, (
                f"IMPORTANT: {key} had already been SETTLED at "
                f"'{term.reopened_from}', but {speaker_name} has just reopened it. "
                f"It is no longer agreed."
            ))

    # ONE-SIDED ACCEPT. `accept` with no matching offer from the other side lands
    # PROPOSED, never AGREED (mediator.py is already safe here) - but the
    # accepting party still needs telling, once, that they agreed to nothing the
    # other side actually offered.
    for key, term in room.negotiation.terms.items():
        if key in room.one_sided_warned:
            continue
        latest = term.latest_by(party_id)
        if (latest is not None and latest.turn == idx and latest.stance == "accept"
                and term.state == TermState.PROPOSED
                and room.negotiation.one_sided(key)):
            room.one_sided_warned.add(key)
            text = (
                f"You just agreed to {key}, but the other side has not actually "
                f"proposed a value for it yet - nothing is really settled there."
            )
            out = text
            if room.participants[party_id].out_lang != "en-IN":
                try:
                    out = await sarvam.translate(text, room.participants[party_id].out_lang,
                                                  "en-IN", mode="formal") or text
                except Exception:  # noqa: BLE001
                    pass
            await _send(room.code, party_id, {"type": "resolve", "term": key, "text": out})
    # Append-only turn log in the speaker's own words - see Room.transcript.
    room.transcript.append({
        "speaker_id": party_id, "speaker_name": speaker_name,
        "lang": speaker_lang, "text": transcript, "ts": time.time(),
    })
    # Durable copy, keyed by party so a LATER negotiation between the same two can
    # be handed what was actually said rather than a summary of a summary.
    await rooms.record_utterance(room, party_id, transcript)

    # Fire-and-forget: embedding is an extra network round-trip and this app's
    # rule is memory never blocks a live turn. remember() already swallows its
    # own failures, so there's nothing to await or check here.
    keys = [p.key for p in room.participants.values() if p.key]
    if len(keys) == 2 and gloss:
        pair = memory.pair_key(keys)
        term_key = res.flagged[0] if res.flagged else "general"
        asyncio.create_task(memory.remember(
            room.code, pair, term_key, speaker_name, speaker_lang, transcript, gloss))

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
    # Round 1 just lets the flag speak for itself - it may still resolve on its own.
    # Round 2 gives the three ordered options. Round 3 forces each side to name ONE
    # number. Past the cap, the mediator stops relaying the same fight and parks it
    # so the rest of the call can move.
    if res.flagged:
        for key in res.flagged[:1]:
            term = room.negotiation.terms.get(key)
            if term is None:
                continue
            mine = term.latest_by(party_id)
            theirs = term.latest_by(other.party_id) if other is not None else None
            mine_val = mine.value if mine else "nothing yet"
            theirs_val = theirs.value if theirs else "nothing yet"

            text = None
            parked = False
            if room.negotiation.deadlocked(key):
                reason = f"no agreement after {term.attempts} rounds"
                if room.negotiation.park(key, reason):
                    await rooms.persist_sheet(room)
                    parked = True
                    text = (
                        f"{key} is being set aside for now - it is NOT agreed. "
                        f"One side said {mine_val}, the other said {theirs_val}. "
                        f"Come back to it once the rest of the lease is settled."
                    )
                    if all(t.state in (TermState.AGREED, TermState.PARKED,
                                        TermState.REJECTED)
                           for t in room.negotiation.terms.values()):
                        text += " Every term is now settled or parked - the negotiation is complete and the packet is ready."
            elif term.attempts >= 3:
                text = (
                    f"You two have not actually agreed on {key}. "
                    f"You said {mine_val}; they said {theirs_val}. "
                    f"Please each state ONE number for {key}, right now, so this can close."
                )
            elif term.attempts == 2:
                text = (
                    f"You two have not actually agreed on {key}. "
                    f"You said {mine_val}; they said {theirs_val}. "
                    f"Three ways forward: 1) one of you restates a single number you both "
                    f"repeat back, 2) split the difference and say the exact figure, or "
                    f"3) park {key} and settle the other terms first."
                )
            # attempts == 1 (or 0, e.g. a fresh divergence): relay + flag only, no
            # extra message yet - the sheet already shows red, that is enough.

            if text is None:
                continue

            for pid, p_obj in room.participants.items():
                out = text
                if p_obj.out_lang != "en-IN":
                    try:
                        out = await sarvam.translate(text, p_obj.out_lang, "en-IN",
                                                      mode="formal") or text
                    except Exception:  # noqa: BLE001
                        pass
                frame = {"type": "resolve", "term": key, "text": out}
                if parked:
                    frame["parked"] = True
                await _send(room.code, pid, frame)

    # WRAP-UP. Every turn, tell both parties where the whole negotiation stands -
    # complete once nothing is left to discuss, otherwise name what's still open.
    all_settled = all(t.state in (TermState.AGREED, TermState.REJECTED, TermState.PARKED)
                       for t in room.negotiation.terms.values())
    if all_settled:
        await _tell_both("_wrapup", "The negotiation is complete - every term is "
                          "settled or parked, and the packet is ready.")
    else:
        pending = room.negotiation.undiscussed()
        if pending:
            await _tell_both("_wrapup", "Still to discuss: " + ", ".join(pending) + ".")

    if spoken and other is not None and voice:
        # DETACHED. _finish_turn is awaited by talk_done before the floor hands
        # over, so awaiting Bulbul here made the OTHER party wait on synthesis
        # before they could even press Talk. The text is already on their screen;
        # the voice catches up on its own.
        sensitive = bool(res.flagged or res.clarification)
        listener_id, listener_lang = other.party_id, other.out_lang

        async def _speak() -> None:
            try:
                audio = await sarvam.tts(
                    spoken, listener_lang, voice,
                    pace=sarvam.pace_for(sensitive=sensitive, relaying=not sensitive),
                )
            except Exception:  # noqa: BLE001
                audio = ""
            # Even an empty payload is worth sending: it clears the listener's
            # "speaking shortly" state instead of leaving it waiting forever.
            await _send(room.code, listener_id,
                        {"type": "audio", "turn_idx": idx, "audio_b64": audio})

        asyncio.create_task(_speak())
