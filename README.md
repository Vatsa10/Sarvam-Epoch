# NyayBandhan

**A faithful translator still manufactures the dispute it was built to prevent.**

Two people who share no common language negotiate a rental agreement by voice. The
agent is not a translator — it tracks the state of every term and **refuses to record
an agreement that isn't one**.

**Declared Sarvam parameter: VOICE EXPERIENCE.** One parameter. Extra APIs score zero.

---

## The thesis

Vatsa says maintenance is *alag* — separate, meaning actual repair costs. Sreedev
agrees, meaning a fixed ₹500/month.

**Both said yes. Both meant different things.** A relay translates both yeses
perfectly, neither party ever learns they diverged, and six months later that is a
court case. The translation was correct and the outcome was a dispute.

So the system tracks a *state* per term:

| State | Meaning |
|---|---|
| `AGREED` | both sides, same value — safe to draft |
| `DIVERGED` | **both said yes to different values** — nobody realises |
| `HEDGED` | *"dekhte hain"* — a soft non-answer, never an agreement |
| `PROPOSED` | on the table, including open haggling |
| `REJECTED` / `OPEN` | refused / never discussed |

`DIVERGED` is a state no translation layer can produce. That is the product.

---

## Tech stack

| Layer | What we use | Why |
|---|---|---|
| **Speech in** | **Saaras v3** (`saaras:v3`, `mode=codemix`) — REST + streaming WebSocket | 23 languages, code-mix native. Word timestamps and server-side VAD (`START_SPEECH`/`END_SPEECH`) segment turns. |
| **Speech out** | **Bulbul v3** (`bulbul:v3`) | 11 languages, 30+ voices. Each party gets a distinct voice; `pace` shifts for sensitive moments. |
| **Translation** | **Mayura v1** (`mayura:v1`) | `code-mixed` for on-screen notes, `formal` for anything spoken aloud. |
| **Reasoning** | **gpt-4o-mini** via OpenAI SDK | Tool calling for the mediator agent. Swappable to `sarvam-30b` with one env var (`AGENT_PROVIDER=sarvam`). |
| **Audio capture** | `sounddevice` / Web Audio `AudioWorklet` | Raw 16 kHz `pcm_s16le` — the STT WebSocket rejects webm/opus. |
| **Backend** | FastAPI + `websockets`, `httpx` | One WS per party, proxied to Sarvam's STT socket. |
| **Frontend** | Single HTML page, no build step | |
| **State** | Append-only JSON log on disk | Survives a live server restart. No database. |
| **SDK** | `sarvamai` (`pip install sarvamai`) | Official client — typed errors, async, streaming. |

**Why the split:** `sarvam-30b` is capped at 40 req/min. Moving reasoning to
`gpt-4o-mini` frees that entire budget for speech, and `/translate` is a *separate*
60/min bucket, so live notes never eat the reasoning quota.

---

## Run

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env          # SARVAM_API_KEY + OPENAI_API_KEY
python verify.py --no-stt       # preflight both providers
python run_tests.py             # 60+ assertions, no network
uvicorn app.main:app --port 8000
```

**Terminal harness** — the whole product without a browser:

```powershell
python talk.py --listen --lang1 gu-IN --lang2 ml-IN   # speak; it replies aloud
python talk.py --stream                                # live partials + VAD signals
python talk.py --list-devices                          # if the mic is wrong
```

---

## Impact

**Who:** ~45 crore internal migrants in India, overwhelmingly renting outside their
language region. A Bihari tenant and a Malayali landlord in Kochi share no fluent
language, so a broker "translates" and both sides sign a document neither fully
understood.

**Baseline:** rental deposits run 2–10 months' rent, and deposit disputes are among the
most common consumer complaints in Indian metros. Consumer-forum resolution takes
1–3 years — long enough that most tenants simply forfeit the money. The dispute almost
never starts at signing; it starts at **exit**, when "maintenance was separate" turns
out to have meant two different things.

**Frequency:** every rental agreement, and India's urban rental stock turns over
roughly annually on the standard 11-month lease.

**The metric we move:** *undetected divergent terms per agreement signed.* Today it is
unmeasured — nobody detects them, which is exactly why they surface at exit. This
converts an invisible failure into a blocked clause **at the table**, when the
correction costs one more sentence instead of two years.

**Why >30% is defensible:** we are not claiming to reduce disputes by persuasion. Every
divergence caught is one clause that cannot be signed ambiguously — a mechanical
reduction, not a behavioural one. In our own scripted runs, 1 term in 6 reached
`DIVERGED` on a conversation both parties believed had gone fine.

**Who pays:** not the tenant. Rental platforms, brokerages and notaries carry the
dispute cost and the churn, and already sell agreement drafting — this slots into a
step they already charge for.

---

## What each part earns

| Parameter | Wt | The specific evidence — and nothing else uses it |
|---|---|---|
| **JTBD** | ×2.5 | `/replay` runs 3 scripted negotiations end to end, zero keystrokes, diffed against expected states; each emits a lawyer packet. |
| **Voice Experience** | ×2.5 | Saaras on mutually unintelligible Gujarati/Malayalam with code-mix; hedge ≠ accept; magnitude confirmation; barge-in; the agent decides *when to interject vs relay* and shifts pace when it does. |
| **Creativity** | ×1.5 | The `DIVERGED` mechanic — reframing a translation problem as an agreement-state problem, and separating *hidden* disagreement from open haggling. |
| **Impact** | ×1.5 | The section above: beneficiary, baseline, frequency, one metric, payer, adoption path. |
| **Memory** | ×1 | Append-only proposal log with per-party provenance; unanswered magnitude doubts and routing permissions survive a live server restart. |
| **Delight** | ×1 | The **drafter**: a blocked term doesn't just stop the process — it prints the unresolved question, both parties' verbatim words, and the next action, in the lawyer's own language. |

**Not** claimed: translation quality (no judge can verify it), or "realtime" (turns are
VAD-segmented; latency is measured, not asserted).

---

## Architecture

```
party A mic ─┐                                    ┌─ live notes (B's language)
             ├─→ Saaras STT (codemix, VAD) ──────→┤
party B mic ─┘         │                          └─ live notes (A's language)
                       │
              English gloss (Mayura) ── numerals read off the gloss,
                       │                  verbatim stays in native script
                       ↓
        gpt-4o-mini + 4 tools ──→ term state machine ──→ append-only log
                       │              │
                       │              ├─→ /packet  — term sheet + provenance
                       │              └─→ /draft   — clauses in the LAWYER's language
                       ↓
              Bulbul TTS → the other party, in their language
```

| File | Role |
|---|---|
| `app/mediator.py` | **The scored core.** Term state machine, divergence, magnitude doubt |
| `app/agent.py` | One tool-calling agent: `update_term`, `flag_divergence`, `request_clarification`, `check_readiness` |
| `app/drafter.py` | Lawyer-facing draft — settled clauses only; blocked terms become open questions |
| `app/llm.py` | Reasoning provider (gpt-4o-mini / sarvam-30b) |
| `app/sarvam.py` | Saaras, Bulbul, Mayura — every endpoint in one place |
| `app/stt_stream.py` | STT WebSocket URL + message classification |
| `app/session.py` | Append-only persistence; survives restart |
| `talk.py` | Terminal harness: `--listen`, `--stream`, `--mediate` |

---

## The demo

1. **The problem** — deposit disputes don't start at signing, they start at exit, when
   "maintenance was separate" turns out to have meant two things.
2. **Live** — Vatsa speaks Gujarati, Sreedev answers in Malayalam. Notes stream in each
   listener's language; the agent speaks the relay aloud.
3. **The catch** — both agree maintenance is "separate". The sheet goes **red**. The
   agent interjects in both languages with the one disambiguating question.
4. **The artifact** — `/draft` produces the agreement in the lawyer's language, with the
   diverged clause **deliberately not drafted** and both parties' exact words beneath it.

Fallback if audio fails: `POST /replay/scenario_1` exercises the entire agent → term
state → packet chain with no microphone.

---

## Verified live

- Sarvam chat, Bulbul (gu-IN + ml-IN), gpt-4o-mini tool calling — via `verify.py`
- Saaras streaming socket returns transcripts plus `START_SPEECH`/`END_SPEECH` VAD signals
- `audio/wav` is the only accepted frame encoding (`audio/x-raw` is rejected outright)
- Bulbul accepts `ratan`/`shubh`/`priya`/`pooja` across all 11 TTS languages
- `python run_tests.py` — all suites green
