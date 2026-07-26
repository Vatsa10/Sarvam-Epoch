# NyayBandhan

Two people. No common language. A mediator that refuses to record an agreement that isn't one.

**Declared Sarvam parameter: VOICE EXPERIENCE.** One parameter. Extra APIs score zero.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # paste the event API key
python verify.py --no-stt   # PREFLIGHT - do this before writing any code
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000

## The thesis

A relay translates faithfully and *manufactures* the dispute it was built to prevent.

Vatsa says maintenance is *alag* — separate, actual costs. Sreedev hears "separate" and
agrees, meaning a fixed ₹500/month. **Both said yes. Both meant different things.** A
translation layer passes both yeses through cleanly and neither party ever learns they
diverged. Six months later that is a court case.

So the agent is not a translator. It tracks the *state of every term* and interjects —
in both languages — the moment a mutual yes turns out to be two different yeses.

`TermState.DIVERGED` is the state no pure translation layer can produce. That is the
product.

## Why this scores

| Line | Wt | What earns it |
|---|---|---|
| JTBD | ×2.5 | The lawyer packet is emitted, every term AGREED or explicitly blocked, across 3 repeated negotiations |
| **Voice Experience** | ×2.5 | Real Gujarati + Malayalam, mutually unintelligible, code-switched numbers; agent decides *when to interject vs relay*; hedge ≠ accept |
| Creativity | ×1.5 | Mediator tracks agreement, not language. A tool that refuses to write the clause. |
| Impact | ×1.5 | Rental/deposit disputes; inter-state migration makes no-common-language negotiation routine |
| Memory | ×1 | Append-only turn log, sheet derived by replay, provenance per proposal, survives restart |
| Delight | ×1 | Catches what both humans missed; blocks the clause instead of drafting a fake one |

**Not** claimed: translation quality (no judge can verify it), realtime (turn-based
push-to-talk — measure latency before ever saying "realtime").

## Architecture

```
mic → Saaras STT → extract terms (Sarvam-30B, strict JSON)
                       ↓
                  state machine  ── DIVERGED / HEDGED? ──→ interject in BOTH languages
                       ↓ no                                        ↓
                  relay to other party ───────────────────→ Bulbul TTS → listener
                       ↓
              append-only turn log → /packet (print → PDF → lawyer)
```

- `app/sarvam.py` — every endpoint and param in one place, so `verify.py` proves them all
- `app/mediator.py` — **the scored core.** Term state machine + divergence detection
- `app/main.py` — `/turn`, `/state`, `/packet`, `/reset`
- `static/index.html` — whole UI, no build step
- `verify.py` — first-hour preflight

## Demo (3 min)

- **0:00–0:30** Migrant tenant, local landlord, no shared language. Deposit disputes are
  the single most common rental complaint in India.
- **0:30–1:00** Today: a broker "translates" and both sides sign a doc neither fully
  understood. Show the empty sheet.
- **1:00–2:30** Live. Vatsa in Gujarati, Sreedev in Malayalam. Rent agrees cleanly →
  green. Then maintenance: **both say yes, sheet goes red.** Agent interjects in both
  languages with the one disambiguating question. Judge sees `DIVERGED` and both
  verbatim quotes side by side — verifiable without speaking either language.
- **2:30–3:00** Emit the lawyer packet. `DO NOT DRAFT THESE CLAUSES` at the top.

## Open before demo

- [ ] `verify.py` all green on venue wifi; note real latency
- [ ] confirm `speaker` names valid for gu-IN and ml-IN (see Bulbul docs list)
- [ ] record `fixtures/sample_gu.webm` and re-run `verify.py` with STT
- [ ] 3 scripted negotiations rehearsed end-to-end, zero keystrokes
- [ ] restart the server mid-demo once — state must survive
