"""Three agents, one live call. Two language agents negotiate THROUGH our mediator
over the real meet WebSocket.

    python eval_suite.py                    # every case
    python eval_suite.py --case divergence  # one
    python eval_suite.py --port 8000        # against an already-running server

Nothing here is scripted turn-by-turn. Two LLM agents — one Gujarati, one Malayalam —
hold personas and private targets, and speak whenever the server hands them the floor.
Each line is synthesised by Bulbul, streamed as PCM16 over the same socket the browser
uses, transcribed by Saaras server-side, and relayed by our mediator. The agents only
ever see what the mediator chose to relay to them, exactly like the humans would.

So this exercises the whole realtime surface — floor handover, VAD-free turn locking,
ASR drift on synthesised speech, numerals surviving a round trip — rather than a clean
string handed to `agent.run_turn`.

Everything lands in evals/<case>.jsonl: intended line, audio size, transcript heard,
gloss, tool calls, relay, and the term sheet after each turn. When a case fails the log
says at which stage it went wrong.

STATUS, HONESTLY: the harness runs. Two agents connect, hold personas, generate real
Gujarati and Malayalam, synthesise it and stream PCM16 over the live socket — the logs
show 37-69 frames per turn landing. What it has NOT yet produced is a single mediator
turn: `state["buffer"]` in ws.py stays empty, so `talk_done` drains nothing and
`_finish_turn` never fires. Either Saaras is not emitting a `final` for synthesised
speech before the floor closes, or the drive pattern needs a longer drain than the 4s
here.

That is the finding, not a footnote. THE REALTIME MEET PATH HAS NOT BEEN OBSERVED
PRODUCING A TURN END-TO-END. Test it with two real browsers before demoing it, and keep
/replay-all (100%, 3/3) as the evidence path — that one exercises agent -> term state ->
artifact and is proven.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import pathlib
import sys
import time
import wave

import httpx
import websockets
from dotenv import load_dotenv

if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

load_dotenv()

from app import llm, sarvam

OUT = pathlib.Path(__file__).resolve().parent / "evals"
OUT.mkdir(exist_ok=True)

PERSONA = """You are {name}, in a rental negotiation by phone. The other party does not
speak your language — a mediator relays between you.

SPEAK ONLY IN {label}, native script. One short natural sentence, the way a real person
on a call would speak. No translation, no explanation, no quotes, no stage directions.
Never restate what the other person said back to them.

YOUR SITUATION: {brief}

{context}"""

CASES = {
    "magnitude": {
        "why": "'seventeen' means 17000. Recording 17 is a nonsense contract.",
        "a": "You are the tenant. Ask what the monthly rent is, then react to the answer.",
        "b": "You are the landlord. You want 17000 a month, but you SAY only "
             "'seventeen' — never the full number, exactly as people really speak.",
        "turns": 4,
        "check": lambda s: (
            (s.get("rent", {}).get("doubt") is not None
             or (s.get("rent", {}).get("value") or "").replace(",", "") in ("17000", "")),
            "rent must never be recorded as 17 — raise a doubt or resolve to 17000"),
    },
    "counter": {
        "why": "Open haggling is NOT divergence — both sides can see they disagree.",
        "a": "You are the tenant. If the landlord asks 17000, say that is too much and "
             "offer 14000. Be direct.",
        "b": "You are the landlord. Open by asking 17000 rupees a month, stating the "
             "full number clearly.",
        "turns": 4,
        "check": lambda s: (
            s.get("rent", {}).get("state") != "DIVERGED",
            f"a counter-offer must never be DIVERGED (got {s.get('rent', {}).get('state')})"),
    },
    "divergence": {
        "why": "THE product. Both say 'separate' and mean different things.",
        "a": "You are the landlord. Say maintenance is separate from rent and the "
             "tenant pays whatever the ACTUAL cost is. Never name a number.",
        "b": "You are the tenant. Agree maintenance is separate, and say you will pay "
             "500 rupees a month for it, fixed.",
        "turns": 4,
        "check": lambda s: (
            s.get("maintenance", {}).get("state") != "AGREED",
            "actual-cost vs fixed-500 must never be AGREED "
            f"(got {s.get('maintenance', {}).get('state')})"),
    },
    "hedge": {
        "why": "'we'll see' is a non-answer and must never become agreement.",
        "a": "You are the landlord. Ask for a deposit of 50000 rupees.",
        "b": "You are the tenant. Do NOT agree and do NOT refuse — give a polite "
             "non-answer like 'we'll see' or 'let me think'.",
        "turns": 4,
        "check": lambda s: (
            s.get("deposit", {}).get("state") != "AGREED",
            f"a hedge must never be AGREED (got {s.get('deposit', {}).get('state')})"),
    },
    "clean": {
        "why": "The happy path must actually close, or nothing is draftable.",
        "a": "You are the landlord. Ask for 12000 rupees a month, full number, clearly.",
        "b": "You are the tenant. Accept 12000 rupees a month, clearly, without "
             "changing the number.",
        "turns": 4,
        "check": lambda s: (
            s.get("rent", {}).get("state") == "AGREED",
            f"a clean acceptance must reach AGREED (got {s.get('rent', {}).get('state')})"),
    },
    "question": {
        "why": "A question names no value and must not become a phantom proposal.",
        "a": "You are the tenant. Ask ONLY questions — how long is the lease, what is "
             "the notice period. Never state a number yourself.",
        "b": "You are the landlord. Answer briefly but vaguely, committing to no number.",
        "turns": 4,
        "check": lambda s: (
            all(v.get("value") or v.get("state") in ("PROPOSED", "HEDGED", "DIVERGED")
                for v in s.values()),
            "no term may be settled off the back of a question alone"),
    },
}


class Log:
    def __init__(self, case: str) -> None:
        import threading
        self.path = OUT / f"{case}.jsonl"
        self.f = self.path.open("w", encoding="utf-8")
        self._lock = threading.Lock()

    def __call__(self, stage: str, **kw) -> None:
        self.f.write(json.dumps({"t": round(time.time(), 3), "stage": stage, **kw},
                                ensure_ascii=False) + "\n")
        self.f.flush()

    def close(self) -> None:
        self.f.close()


def pcm_frames(wav_bytes: bytes, chunk: int = 1600):
    """Bulbul wav -> raw PCM16 frames, exactly what the browser worklet emits."""
    with wave.open(io.BytesIO(wav_bytes)) as w:
        raw = w.readframes(w.getnframes())
    for i in range(0, len(raw), chunk * 2):
        yield raw[i:i + chunk * 2]


class Agent:
    """One language agent driving one WebSocket."""

    def __init__(self, base: str, code: str, name: str, lang: str, label: str,
                 speaker: str, brief: str, log: Log) -> None:
        self.base, self.code = base, code
        self.name, self.lang, self.label = name, lang, label
        self.speaker, self.brief, self.log = speaker, brief, log
        self.party_id: str | None = None
        self.heard: list[str] = []          # only what the mediator relayed to us
        self.spoken = 0
        self.ws = None
        self.sheet: dict = {}
        self.holds_floor = False
        self.done = asyncio.Event()

    async def connect(self):
        url = (f"ws://{self.base}/api/meet/ws/{self.code}"
               f"?name={self.name}&lang={self.lang}")
        self.ws = await websockets.connect(url, max_size=8 * 1024 * 1024)
        return self

    async def line(self) -> str:
        ctx = ("Relayed to you so far:\n" + "\n".join(self.heard[-4:])
               if self.heard else "You speak first — open the conversation.")
        msg = await llm.complete_with_tools(
            PERSONA.format(name=self.name, label=self.label, brief=self.brief,
                           context=ctx),
            "Your line:", [], temperature=0.6)
        text = (msg.get("content") or "").strip().strip('"')
        self.log("party_line", party=self.name, lang=self.lang, line=text)
        return text

    async def take_turn(self, limit: int) -> None:
        """We hold the floor. Say one thing, over real audio."""
        if self.spoken >= limit:
            self.done.set()
            return
        if not self.holds_floor:
            self.log("skip_turn", party=self.name, reason="does not hold the floor")
            return
        self.spoken += 1
        text = await self.line()
        if not text:
            await self.ws.send(json.dumps({"type": "talk_done"}))
            return

        wav = base64.b64decode(await sarvam.tts(text, self.lang, self.speaker))
        self.log("tts", party=self.name, chars=len(text), wav_bytes=len(wav))

        await self.ws.send(json.dumps({"type": "talk_start"}))
        await asyncio.sleep(0.8)           # let the server open the upstream socket
        n = 0
        for frame in pcm_frames(wav):
            await self.ws.send(frame)
            n += 1
            await asyncio.sleep(0.05)      # ~realtime pacing, so VAD behaves
        self.log("streamed", party=self.name, frames=n)
        await asyncio.sleep(4.0)           # let trailing STT finals land; a short
                                           # wait drains an empty buffer and the
                                           # turn silently never happens
        await self.ws.send(json.dumps({"type": "talk_done"}))

    async def pump(self, limit: int) -> None:
        try:
            async for raw in self.ws:
                if isinstance(raw, bytes):
                    continue
                d = json.loads(raw)
                kind = d.get("type")

                if kind == "joined":
                    self.party_id = (d.get("you") or {}).get("party_id")
                    self.log("joined", party=self.name, party_id=self.party_id,
                             turn_holder=d.get("turn_holder"),
                             other=(d.get("other") or {}).get("name"))
                    # The floor may already be ours and idle at join time; the
                    # server only emits a `floor` frame on a transition, so a
                    # first speaker would otherwise wait forever.
                    if (d.get("turn_holder") == self.party_id
                            and not d.get("floor_open") and d.get("other")):
                        asyncio.create_task(self.take_turn(limit))
                elif kind == "participant_joined":
                    self.log("peer_joined", party=self.name, peer=d.get("name"))
                    # We were first in and hold the floor: the other side has now
                    # arrived, so we can actually start.
                    if self.party_id == d.get("turn_holder") or self.spoken == 0:
                        await asyncio.sleep(0.4)
                        if self.holds_floor:
                            asyncio.create_task(self.take_turn(limit))
                elif kind == "note":
                    if d.get("final"):
                        self.log("note", party=self.name, text=d.get("text"))
                elif kind == "turn":
                    self.sheet = _flat(d.get("sheet") or {})
                    self.log("turn", listener=self.name, speaker=d.get("speaker_name"),
                             spoken=d.get("spoken"), flagged=d.get("flagged"),
                             sheet=self.sheet)
                    if d.get("speaker") != self.party_id and d.get("spoken"):
                        self.heard.append(f"They said: {d['spoken']}")
                elif kind == "error":
                    self.log("error", party=self.name, text=d.get("text"))
                elif kind == "floor":
                    self.holds_floor = d.get("holder") == self.party_id
                    if self.holds_floor and not d.get("open"):
                        if self.spoken >= limit:
                            self.done.set()
                        else:
                            asyncio.create_task(self.take_turn(limit))
        except Exception as e:  # noqa: BLE001
            self.log("ws_closed", party=self.name, reason=f"{type(e).__name__}: {e}")
        finally:
            self.done.set()


def _flat(sheet: dict) -> dict:
    return {t["key"]: {"state": t.get("state"), "value": t.get("agreed_value"),
                       "doubt": t.get("doubt"), "note": t.get("divergence_note")}
            for t in sheet.get("terms", []) if t.get("state") != "OPEN"}


async def run_case(name: str, spec: dict, base: str) -> dict:
    log = Log(name)
    log("case_start", case=name, why=spec["why"])

    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"http://{base}/api/meet/rooms")
        code = r.json().get("code")
    log("room", code=code)

    a = await Agent(base, code, "Vatsa", "gu-IN", "Gujarati", "ratan",
                    spec["a"], log).connect()
    b = await Agent(base, code, "Sreedev", "ml-IN", "Malayalam", "shubh",
                    spec["b"], log).connect()

    limit = spec["turns"] // 2
    pumps = [asyncio.create_task(a.pump(limit)), asyncio.create_task(b.pump(limit))]
    try:
        await asyncio.wait_for(
            asyncio.gather(a.done.wait(), b.done.wait()), timeout=240)
    except asyncio.TimeoutError:
        log("timeout", note="case hit the 240s ceiling")
    for p in pumps:
        p.cancel()
    for ws in (a.ws, b.ws):
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass

    sheet = a.sheet or b.sheet
    ok, why = spec["check"](sheet)
    log("verdict", passed=bool(ok), expectation=why, final_sheet=sheet)
    log.close()
    return {"case": name, "passed": bool(ok), "expectation": why, "sheet": sheet}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case")
    ap.add_argument("--port", default="8000")
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    base = f"{a.host}:{a.port}"

    try:
        async with httpx.AsyncClient(timeout=5) as c:
            await c.get(f"http://{base}/api/meet/languages")
    except Exception:  # noqa: BLE001
        print(f"no server on {base} — start it with:\n"
              f"  python -m uvicorn app.main:app --port {a.port}\n")
        return 2

    names = [a.case] if a.case else list(CASES)
    results = []
    for n in names:
        if n not in CASES:
            print(f"unknown case {n}; have: {', '.join(CASES)}")
            return 2
        print(f"\n=== {n} — {CASES[n]['why']}")
        r = await run_case(n, CASES[n], base)
        results.append(r)
        print(f"  {'PASS' if r['passed'] else 'FAIL'}  "
              f"{ {k: v['state'] for k, v in r['sheet'].items()} }")
        if not r["passed"]:
            print(f"        expected: {r['expectation']}")
        print(f"        log: evals/{n}.jsonl")

    passed = sum(1 for r in results if r["passed"])
    print(f"\n{passed}/{len(results)} passed ({passed / len(results) * 100:.0f}%)\n")
    (OUT / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    await sarvam.aclose()
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
