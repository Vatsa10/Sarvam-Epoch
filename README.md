# NyayBandhan

Two people. No common language. A live translated call with a mediator that refuses
to record an agreement that isn't one.

**Declared Sarvam parameter: VOICE EXPERIENCE.** One parameter. Extra APIs score zero.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # paste the Sarvam API key + a Postgres DATABASE_URL

cd frontend
npm install
npm run build                # static export -> frontend/out/
cd ..

uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000` — it redirects straight to the meet UI (`/meet`). Create
a meeting, share the code (or the link), and join from a second tab/device.

Optional hot-reload dev mode while iterating on frontend components: `npm run dev` in
`frontend/` (port 3000) against the backend on 8000 — the one cross-origin case, which
is why CORS is enabled for `http://localhost:3000` in `app/main.py`.

## The thesis

A relay translates faithfully and *manufactures* the dispute it was built to prevent.

One party says maintenance is *alag* — separate, actual costs. The other hears
"separate" and agrees, meaning a fixed monthly amount. **Both said yes. Both meant
different things.** A translation layer passes both yeses through cleanly and neither
party ever learns they diverged. Six months later that is a court case.

So the agent is not a translator. It tracks the *state of every term* and interjects —
in both languages — the moment a mutual yes turns out to be two different yeses.

`TermState.DIVERGED` is the state no pure translation layer can produce. That is the
product.

## Architecture

```
Party A mic ──WS──┐                            ┌── notes panel (term sheet + captions)
                  ├──→  FastAPI relay  ──────→ ┤
Party B mic ──WS──┘         │                  └── notes panel (term sheet + captions)
                            │
  per VAD segment:  Sarvam STT WS ──→ /translate ──→ live captions to the OTHER party
                            │
  on VAD end-of-turn:  sarvam-30b + tools ──→ mediator state ──→ both panels
                            │
                      Bulbul TTS ──→ one spoken relay sentence to the listener
```

- `app/sarvam.py` — every Sarvam endpoint/param in one place, so `verify.py` proves them all
- `app/mediator.py` — **the scored core.** Term state machine + divergence detection, scoped per room
- `app/agent.py` — one tool-calling `sarvam-30b` call per completed turn
- `app/stt_stream.py` — Sarvam STT WebSocket URL builder + frame classifier
- `app/session.py` — snapshot persistence (file-based; also reused for the DB path)
- `app/meet_interface/` — the real-time meet:
  - `rooms.py` — in-memory room registry (create/join, max 2 participants), Postgres-backed for durability
  - `db.py` — Neon Postgres: rooms, participants, term-sheet snapshots (degrades to in-memory-only if unreachable)
  - `languages.py` — supported languages (English + Indian languages) and their TTS speakers
  - `ws.py` — the live relay: browser PCM16 → Sarvam STT WS → live captions + one agent call per turn → TTS → broadcast
  - `app.py` — router: `POST /api/meet/rooms`, `GET /api/meet/languages`, `WS /api/meet/ws/{code}`
- `frontend/` — Next.js 14 app (TypeScript, Tailwind, Zustand), built as a **static export** and served by FastAPI at `/meet`
- `verify.py` — first-hour Sarvam API preflight

## What you need to supply

- **`SARVAM_API_KEY`** — root `.env`.
- **`DATABASE_URL`** — a Postgres connection string (Neon or otherwise), root `.env`. Rooms still
  work with no DB configured (in-memory only) — a room just won't survive a server restart.

## Open before demo

- [ ] `verify.py` all green on venue wifi; note real latency
- [ ] confirm `speaker` names valid for every language in `app/meet_interface/languages.py`
      (only `gu-IN`→`ratan` and `ml-IN`→`shubh` are confirmed against live docs)
- [ ] two devices, two languages, one call: divergence still turns a term red
- [ ] restart the server mid-call once — a rejoined room's term sheet survives
