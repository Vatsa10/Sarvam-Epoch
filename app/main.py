"""FastAPI app: the real-time cross-language negotiation mediator, plus the real-time meet.

Flow per turn:
  audio -> Saaras STT -> extract terms -> fold into sheet -> either RELAY to the other
  party or INTERJECT if the sheet went DIVERGED/HEDGED -> Bulbul TTS in the listener's
  language. The mediator UI is served at /mediator.

Room lifecycle, language list, and the live WebSocket relay for the video meet live
under app/meet_interface/ (mounted at /api/meet/*); the Next.js meet UI is served
as a static export at /meet.
"""
from __future__ import annotations

import asyncio
import base64
import json
import pathlib
import time
from typing import Any

import websockets
from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import agent, sarvam, session, stt_stream
from .mediator import Negotiation, Turn, TermState
from .meet_interface import db as meet_db
from .meet_interface.app import router as meet_router

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
SESSIONS = ROOT / "sessions"
SESSIONS.mkdir(exist_ok=True)
MEET_STATIC = ROOT / "frontend" / "out"

app = FastAPI(title="NyayBandhan")
app.include_router(meet_router, prefix="/api")

# The Next.js meet UI runs on :3000 in dev (npm run dev) against this API on
# :8000 - the only cross-origin case in the app, since the built static
# export is normally served by this same process at /meet. Regex (not a
# fixed list) because "localhost" and "127.0.0.1" are different origins to
# the browser, and dev falls back to :3001+ if :3000 is already taken.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ponytail: one in-memory negotiation + a JSON snapshot on every turn. That IS the
# persistence story - it survives a server restart on stage, which is the only
# durability the demo has to prove. A DB buys nothing before then.
NEG = Negotiation()


@app.on_event("startup")
async def _meet_db_startup() -> None:
    if meet_db.DATABASE_URL:
        await meet_db.get_pool()


@app.on_event("shutdown")
async def _meet_db_shutdown() -> None:
    await meet_db.close_pool()


def _persist() -> None:
    session.save(NEG, SESSIONS / f"{NEG.session_id}.json")


def _restore() -> None:
    """Rebuild from the snapshot so `uvicorn --reload` (and a live restart) keep state."""
    global NEG
    NEG = session.load(SESSIONS / f"{NEG.session_id}.json", NEG.session_id)


_restore()


@app.get("/", include_in_schema=False)
async def root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/meet/")

PANELS: dict[str, set[WebSocket]] = {"vatsa": set(), "sreedev": set()}

# The browser sends raw pcm_s16le with no container (no RIFF header) - it is NOT a
# wav file. This literal MUST be confirmed against a live Sarvam socket during
# preflight, and is the first thing to change if STT comes back with empty
# transcripts.
AUDIO_FRAME_ENCODING = "audio/x-raw"

# Guards the read-idx -> append -> save critical section of _finish_turn. Both
# parties have independent sockets and can enter _finish_turn concurrently; without
# this, both can read the same len(NEG.turns) and append two turns sharing one idx,
# corrupting the ordering the lawyer packet depends on.
TURN_LOCK = asyncio.Lock()


async def _broadcast(party: str, payload: dict) -> None:
    """Send to one party's panel. Dead sockets are dropped, never raised - a closed
    tab must not kill a live turn."""
    dead = []
    # Snapshot before iterating: a second tab connecting for this party mid-broadcast
    # mutates PANELS[party] across our await points, which raises "Set changed size
    # during iteration" if we iterate the live set directly.
    for ws in list(PANELS.get(party, set())):
        try:
            await ws.send_json(payload)
        except Exception:  # noqa: BLE001
            dead.append(ws)
    for ws in dead:
        PANELS[party].discard(ws)


@app.websocket("/ws/{party}")
async def ws_party(client: WebSocket, party: str) -> None:
    if party not in sarvam.PARTIES:
        await client.close(code=4004)
        return
    await client.accept()
    PANELS[party].add(client)

    me = sarvam.PARTIES[party]
    other = next(p for p in sarvam.PARTIES if p != party)
    other_cfg = sarvam.PARTIES[other]
    url = stt_stream.build_ws_url(me["lang"])

    try:
        async with websockets.connect(url, additional_headers=stt_stream.WS_HEADERS) as up:
            async def pump_up() -> None:
                """Browser PCM -> Sarvam."""
                while True:
                    chunk = await client.receive_bytes()
                    await up.send(json.dumps({
                        "audio": {"data": _b64(chunk), "encoding": AUDIO_FRAME_ENCODING}
                    }))

            async def pump_down() -> None:
                """Sarvam -> notes, and on turn end, the agent."""
                buffer: list[str] = []
                last_partial = 0.0
                try:
                    async for raw in up:
                        kind, text = stt_stream.classify(json.loads(raw))

                        if kind == "partial":
                            # Every STT partial otherwise fires a /translate call and
                            # exhausts the 60/min rate bucket in ~40s, blocking this
                            # read loop. Finals stay unthrottled.
                            now = time.monotonic()
                            if now - last_partial < 1.5:
                                continue
                            last_partial = now
                            note = await _safe_translate(text, other_cfg["lang"], me["lang"])
                            await _broadcast(other, {"type": "note", "final": False,
                                                     "from": me["name"], "text": note})

                        elif kind == "final":
                            buffer.append(text)
                            note = await _safe_translate(text, other_cfg["lang"], me["lang"])
                            await _broadcast(other, {"type": "note", "final": True,
                                                     "from": me["name"], "text": note})

                        elif kind == "turn_end" and buffer:
                            await _finish_turn(party, " ".join(buffer))
                            buffer = []

                        elif kind == "error":
                            await _broadcast(party, {"type": "error", "text": text})
                finally:
                    # The loop can exit via a disconnect/exception with finals already
                    # buffered and no turn_end yet. That transcript must not vanish:
                    # best-effort flush it as a completed turn. Guarded on its own so a
                    # failing flush during teardown never masks the original error.
                    if buffer:
                        try:
                            await _finish_turn(party, " ".join(buffer))
                        except Exception:  # noqa: BLE001
                            pass

            up_task = asyncio.ensure_future(pump_up())
            down_task = asyncio.ensure_future(pump_down())
            try:
                done, pending = await asyncio.wait(
                    {up_task, down_task}, return_when=asyncio.FIRST_EXCEPTION
                )
                for t in pending:
                    t.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                for t in done:
                    exc = t.exception()
                    if exc is not None and not isinstance(exc, WebSocketDisconnect):
                        raise exc
            except WebSocketDisconnect:
                pass

    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        await _broadcast(party, {"type": "error", "text": f"{type(e).__name__}: {str(e)[:80]}"})
    finally:
        PANELS[party].discard(client)


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


async def _safe_translate(text: str, target: str, source: str) -> str:
    """Never fail silently: a 429 or timeout renders a visible placeholder so the
    operator can see a phrase was dropped rather than believing it landed."""
    try:
        return await sarvam.translate(text, target, source)
    except Exception:  # noqa: BLE001
        return "…"


async def _finish_turn(party: str, transcript: str) -> None:
    """ONE sarvam-30b call. Never on a partial."""
    me = sarvam.PARTIES[party]
    other = next(p for p in sarvam.PARTIES if p != party)
    other_cfg = sarvam.PARTIES[other]

    # Both parties have independent sockets and can call _finish_turn concurrently.
    # Hold the lock only across idx assignment -> append -> save: that is the only
    # section whose ordering the lawyer packet depends on. TTS happens outside it so
    # a slow Bulbul call on one turn can't stall the other party's turn.
    async with TURN_LOCK:
        idx = len(NEG.turns)
        try:
            res = await agent.run_turn(NEG, party, me["lang"], transcript, idx)
        except Exception as e:  # noqa: BLE001
            for p in sarvam.PARTIES:
                await _broadcast(p, {"type": "error", "text": f"agent failed: {e}"})
            return

        spoken = res.clarification or res.summary
        if spoken:
            spoken = await _safe_translate(spoken, other_cfg["lang"], "en-IN") or spoken
        NEG.turns.append(Turn(idx=idx, party=party, lang=me["lang"], transcript=transcript,
                              relay_text=spoken, interjection=res.clarification))
        session.save(NEG, SESSIONS / f"{NEG.session_id}.json")

    audio = ""
    if spoken:
        try:
            audio = await sarvam.tts(spoken, other_cfg["lang"], other_cfg["speaker"])
        except Exception:  # noqa: BLE001
            audio = ""

    sheet = NEG.sheet()
    await _broadcast(other, {"type": "turn", "from": me["name"], "spoken": spoken,
                             "audio_b64": audio, "flagged": res.flagged, "sheet": sheet})
    await _broadcast(party, {"type": "state", "flagged": res.flagged, "sheet": sheet})


def _sheet_summary() -> str:
    lines = []
    for t in NEG.terms.values():
        if t.state == TermState.OPEN:
            continue
        v = t.agreed_value or (t.proposals[-1].value if t.proposals else "?")
        lines.append(f"- {t.key}: {t.state.value} ({v})")
    return "\n".join(lines) or "(nothing agreed yet)"


@app.post("/turn")
async def turn(
    audio: UploadFile = File(...),
    party: str = Form(...),
) -> JSONResponse:
    if party not in sarvam.PARTIES:
        return JSONResponse({"error": f"unknown party {party}"}, status_code=400)

    speaker_cfg = sarvam.PARTIES[party]
    listener = next(p for p in sarvam.PARTIES if p != party)
    listener_cfg = sarvam.PARTIES[listener]

    raw = await audio.read()
    stt_out = await sarvam.stt(raw, filename=audio.filename or "turn.webm",
                               language_code=speaker_cfg["lang"])
    transcript = (stt_out.get("transcript") or "").strip()
    if not transcript:
        return JSONResponse({"error": "empty transcript", "stt": stt_out}, status_code=422)

    idx = len(NEG.turns)
    parsed = await mediator_extract(transcript, speaker_cfg["label"])
    updates = parsed.get("updates", [])
    summary = parsed.get("summary", transcript)

    flagged = NEG.apply(party, speaker_cfg["lang"], updates, idx)

    # Interjection beats relay. If the sheet just broke, saying it in BOTH languages
    # matters more than passing the message along.
    interjection_text = None
    speak_to = listener
    if flagged:
        key = flagged[0]
        term = NEG.terms[key]
        problem = (
            f"Term: {term.key} ({term.description}). State: {term.state.value}. "
            f"{term.divergence_note or 'A soft non-answer was given instead of a decision.'}"
        )
        from .mediator import interjection as make_interjection
        interjection_text = await make_interjection(problem, listener_cfg["label"])
        relay_text = interjection_text
    else:
        relay_text = await translate_for(summary, transcript, listener_cfg["label"])

    audio_b64 = await sarvam.tts(
        relay_text, listener_cfg["lang"], listener_cfg["speaker"]
    )

    NEG.turns.append(Turn(
        idx=idx, party=party, lang=speaker_cfg["lang"], transcript=transcript,
        relay_text=relay_text, interjection=interjection_text,
    ))
    _persist()

    return JSONResponse({
        "turn": idx,
        "speaker": speaker_cfg["name"],
        "transcript": transcript,
        "summary": summary,
        "updates": updates,
        "flagged": flagged,
        "interjection": interjection_text,
        "relay_text": relay_text,
        "relay_to": listener_cfg["name"],
        "audio_b64": audio_b64,
        "sheet": NEG.sheet(),
    })


async def mediator_extract(transcript: str, lang_label: str) -> dict[str, Any]:
    from .mediator import extract
    return await extract(transcript, lang_label, _sheet_summary())


async def translate_for(summary: str, transcript: str, lang_label: str) -> str:
    """Relay in the listener's language. Sarvam-30B rather than the Translate API so
    the relay can stay conversational and keep numbers intact."""
    sys = (
        f"Relay this rental-negotiation utterance to the other party in {lang_label}. "
        "Speak naturally, first person, as if passing on what they said. Keep every "
        "number exactly. One or two sentences. Native script. Output only the relay."
    )
    return (await sarvam.chat(sys, f"They said: {transcript}\nMeaning: {summary}", 0.2)).strip()


@app.get("/state")
async def state() -> JSONResponse:
    return JSONResponse(NEG.sheet())


@app.get("/packet")
async def packet() -> HTMLResponse:
    """The artifact. Print-to-PDF and hand it to the lawyer.

    Every clause carries both parties' own words in their own script, and the blocked
    section names exactly what was NOT agreed - which is the part that stops the
    lawyer drafting a clause the parties never actually settled.
    """
    s = NEG.sheet()
    rows = []
    for t in s["terms"]:
        if t["state"] == "OPEN":
            continue
        quotes = "<br>".join(
            f"<span class=q><b>{sarvam.PARTIES[p['party']]['name']}</b> "
            f"({p['lang']}): &ldquo;{p['verbatim']}&rdquo; &rarr; <code>{p['value']}</code> "
            f"<i>[{p['stance']}]</i></span>"
            for p in t["proposals"]
        )
        note = f"<div class=note>{t['divergence_note']}</div>" if t.get("divergence_note") else ""
        rows.append(
            f"<tr class='{t['state']}'><td><b>{t['key']}</b><br><small>{t['description']}</small></td>"
            f"<td class=st>{t['state']}</td><td>{t['agreed_value'] or '&mdash;'}</td>"
            f"<td>{quotes}{note}</td></tr>"
        )
    blocked = s["blocked"]
    banner = (
        "<div class='ok'>All discussed terms AGREED &mdash; safe to draft.</div>"
        if s["drafting_safe"] else
        "<div class='stop'><b>DO NOT DRAFT THESE CLAUSES.</b> Not agreed: "
        + ", ".join(blocked or [t["key"] for t in s["terms"] if t["state"] not in ("AGREED", "OPEN")]) +
        ". Both parties said yes to different things, gave a soft non-answer, or a "
        "term is still only proposed by one side.</div>"
    )
    return HTMLResponse(f"""<!doctype html><meta charset=utf-8>
<title>NyayBandhan &mdash; Lawyer Packet</title><style>
body{{font:14px/1.6 system-ui;margin:40px;max-width:900px}}
h1{{margin:0 0 4px}} .sub{{color:#666;margin-bottom:24px}}
table{{border-collapse:collapse;width:100%}} td,th{{border:1px solid #ddd;padding:10px;vertical-align:top}}
th{{background:#f5f5f5;text-align:left}} .st{{font-weight:700;white-space:nowrap}}
tr.AGREED .st{{color:#0a7}} tr.DIVERGED .st{{color:#c00}} tr.HEDGED .st{{color:#c60}}
tr.DIVERGED{{background:#fff5f5}} tr.HEDGED{{background:#fffaf0}}
.q{{display:block;margin:2px 0}} code{{background:#eee;padding:1px 4px}}
.note{{margin-top:6px;padding:6px;background:#ffe9e9;border-left:3px solid #c00}}
.stop{{padding:12px;background:#ffe9e9;border-left:4px solid #c00;margin-bottom:20px}}
.ok{{padding:12px;background:#e9ffe9;border-left:4px solid #0a7;margin-bottom:20px}}
@media print{{body{{margin:0}}}}
</style>
<h1>Rental Term Sheet</h1>
<div class=sub>NyayBandhan &mdash; session <code>{s['session_id']}</code> &mdash;
{len(s['turns'])} turns. Verbatim quotes are unedited, in each speaker's own language.</div>
{banner}
<table><tr><th>Term</th><th>State</th><th>Agreed value</th><th>What each party actually said</th></tr>
{''.join(rows) or '<tr><td colspan=4>Nothing discussed yet.</td></tr>'}</table>
<p class=sub>Generated for legal review. Clauses marked DIVERGED or HEDGED were
deliberately not resolved by the system &mdash; they need a human decision first.</p>""")


@app.post("/replay/{scenario}")
async def replay(scenario: str) -> JSONResponse:
    """Run a scripted negotiation end-to-end with no microphone.

    This is the JTBD evidence path: three of these, zero keystrokes, diffed against
    the scenario's own expected states.
    """
    f = ROOT / "fixtures" / f"{scenario}.json"
    if not f.exists():
        return JSONResponse({"error": f"no scenario {scenario}"}, status_code=404)

    spec = json.loads(f.read_text(encoding="utf-8"))
    neg = Negotiation(f"replay_{scenario}")

    for i, t in enumerate(spec["turns"]):
        res = await agent.run_turn(neg, t["party"], sarvam.PARTIES[t["party"]]["lang"],
                                   t["transcript"], i)
        spoken = res.clarification or res.summary
        neg.turns.append(Turn(idx=i, party=t["party"],
                              lang=sarvam.PARTIES[t["party"]]["lang"],
                              transcript=t["transcript"],
                              relay_text=spoken, interjection=res.clarification))

    session.save(neg, SESSIONS / f"{neg.session_id}.json")
    sheet = neg.sheet()
    actual = {t["key"]: t["state"] for t in sheet["terms"]}
    mismatches = [
        {"term": k, "expected": v, "actual": actual.get(k)}
        for k, v in spec["expected"].items() if actual.get(k) != v
    ]
    return JSONResponse({"scenario": spec["name"], "expected_ok": not mismatches,
                         "mismatches": mismatches, "sheet": sheet})


@app.post("/reset")
async def reset() -> JSONResponse:
    global NEG
    NEG = Negotiation()
    _persist()
    return JSONResponse({"ok": True})


if MEET_STATIC.exists():
    @app.get("/meet", include_in_schema=False)
    async def meet_redirect() -> RedirectResponse:
        # StaticFiles only mounts on the "/meet/..." prefix - the bare path
        # (no trailing slash) otherwise 404s instead of serving the meet UI.
        return RedirectResponse(url="/meet/")

    app.mount("/meet", StaticFiles(directory=str(MEET_STATIC), html=True), name="meet")
else:
    print(f"[meet_interface] {MEET_STATIC} not built yet - run `npm run build` in frontend/ "
          "to serve the meet UI at /meet")

app.mount("/mediator", StaticFiles(directory=str(STATIC), html=True), name="static")
