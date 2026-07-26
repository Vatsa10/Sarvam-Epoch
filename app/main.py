"""FastAPI app: one turn endpoint, the term sheet, the lawyer packet.

Flow per turn:
  audio -> Saaras STT -> extract terms -> fold into sheet -> either RELAY to the other
  party or INTERJECT if the sheet went DIVERGED/HEDGED -> Bulbul TTS in the listener's
  language.
"""
from __future__ import annotations

import base64
import json
import pathlib
from typing import Any

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import sarvam
from .mediator import Negotiation, Turn, TermState

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
SESSIONS = ROOT / "sessions"
SESSIONS.mkdir(exist_ok=True)

app = FastAPI(title="NyayBandhan")

# ponytail: one in-memory negotiation + a JSON snapshot on every turn. That IS the
# persistence story - it survives a server restart on stage, which is the only
# durability the demo has to prove. A DB buys nothing before then.
NEG = Negotiation()


def _persist() -> None:
    (SESSIONS / f"{NEG.session_id}.json").write_text(
        json.dumps(NEG.sheet(), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _restore() -> None:
    """Rebuild from the snapshot so `uvicorn --reload` (and a live restart) keep state."""
    f = SESSIONS / f"{NEG.session_id}.json"
    if not f.exists():
        return
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    from .mediator import Proposal
    for t in data.get("terms", []):
        term = NEG.terms.get(t["key"])
        if not term:
            continue
        term.state = TermState(t["state"])
        term.agreed_value = t.get("agreed_value")
        term.divergence_note = t.get("divergence_note")
        term.proposals = [Proposal(**p) for p in t.get("proposals", [])]
    NEG.turns = [Turn(**x) for x in data.get("turns", [])]


_restore()


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
        if not blocked else
        "<div class='stop'><b>DO NOT DRAFT THESE CLAUSES.</b> Not agreed: "
        + ", ".join(blocked) +
        ". Both parties said yes to different things, or gave a soft non-answer.</div>"
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


@app.post("/reset")
async def reset() -> JSONResponse:
    global NEG
    NEG = Negotiation()
    _persist()
    return JSONResponse({"ok": True})


app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")
