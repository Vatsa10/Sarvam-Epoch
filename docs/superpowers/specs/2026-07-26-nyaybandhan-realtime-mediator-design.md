# NyayBandhan — Realtime Cross-Language Negotiation Mediator

**Date:** 2026-07-26 · **Status:** approved, implementing
**Context:** Sarvam Epoch Buildathon. Build window closes 16:30. Spec written 12:05.
**Declared Sarvam parameter:** VOICE EXPERIENCE (one parameter; extra APIs score zero).

---

## 1. Problem

Two people negotiate a rental agreement and share no common language. Vatsa speaks
Gujarati, Sreedev speaks Malayalam. Today a human broker "translates" and both sides
sign a document neither fully understood.

A translation layer does not fix this — it makes it worse in one specific way. When
Vatsa says maintenance is *alag* (separate, meaning actual repair costs) and Sreedev
agrees, meaning a fixed ₹500/month, a faithful relay passes **both yeses through
cleanly** and neither party ever learns they diverged. Six months later that is a
court case.

**The insight:** the agent is not a translator. It tracks the state of every
negotiable term and refuses to record an agreement that isn't one.

`TermState.DIVERGED` — both parties accepted, the values differ — is a state a pure
translation layer cannot produce. That is the product.

## 2. Users and outcome

**Users:** two parties on a call with no shared language. Neither is technical.

**Outcome:** a lawyer packet (HTML → print → PDF) where every term is either
**AGREED**, carrying both parties' verbatim words in their own scripts, or explicitly
**BLOCKED** with the reason. The blocked section is the point: it stops a lawyer
drafting a clause the parties never actually settled.

**Definition of done for one session:** all six terms are AGREED, REJECTED, or
explicitly blocked (DIVERGED/HEDGED); no term silently defaults; the packet renders
and is forwardable without editing.

## 3. Scope decisions (locked)

| Decision | Choice | Why |
|---|---|---|
| Agent topology | ONE stateful agent, 4 tools | 40 req/min cap on `sarvam-30b` makes fan-out stall the demo; rubric does not reward agent count |
| Turn-taking | Always-on VAD, hands-free | User choice. Mitigation in §8 |
| Domain | Rental-locked, 6 fixed terms | Fixed schema means the agent can say "notice period undiscussed" and block drafting; judge-verifiable |
| Live notes | Running transcript of X, rendered in Y's language | Streams while X still speaking |
| Relay audio | Read live, hear one summary sentence per turn | Text is realtime; TTS per turn only |

**Explicitly out of scope:** Pipecat/LiveKit (shaped for one-human-one-bot, wrong for
two humans + mediator), multi-agent fan-out, streamed TTS, generic call types,
database, auth.

## 4. Verified Sarvam surface

Confirmed against live docs 2026-07-26. Facts, not assumptions.

- **No Sarvam Agents API.** No sessions, threads, or checkpoints; the beta page states
  there are no beta APIs. **All statefulness is ours to build.**
- Chat: `POST /v1/chat/completions`, models **`sarvam-30b`** (64K) / `sarvam-105b`
  (128K). **`sarvam-m` is DEPRECATED — do not use.** Supports `tools` / `tool_choice`,
  OpenAI-shaped; the `openai` Python client works against `base_url=https://api.sarvam.ai/v1`.
- **STT WebSocket:** `wss://api.sarvam.ai/speech-to-text/ws`. Params `language-code`,
  `model=saaras:v3`, `high_vad_sensitivity`, `vad_signals`, `flush_signal`,
  `input_audio_codec`. **WS accepts only `wav`/`pcm_s16le`/`pcm_l16`/`pcm_raw` — not
  webm/opus.** Server emits `data` / `events` / `error`.
- Translate: `POST /translate`, `mayura:v1` (1000 char limit, auto-detect) with
  `mode: code-mixed`.
- TTS: `POST /text-to-speech`, `bulbul:v3`, `pace` 0.5–2.0. No emotion/style param;
  `pitch`/`loudness` are v2-only. **Speakers are language-dependent:**
  **gu-IN → `ratan`/`priya`/`ritu`; ml-IN → `shubh`/`pooja`.**
- Auth: header `api-subscription-key`. **Failures return 403, not 401.**
- **Rate limits (Starter): `sarvam-30b` 40/min · translate 60/min · TTS REST 60/min
  (`bulbul:v3` capped 30/min) · STT WS 20 concurrent.**

## 5. Architecture

```
Party A mic ──WS──┐                            ┌── notes panel A (Gujarati)
                  ├──→  FastAPI relay  ──────→ ┤
Party B mic ──WS──┘         │                  └── notes panel B (Malayalam)
                            │
  per VAD segment:  Sarvam STT WS ──→ /translate ──→ live notes to the OTHER party
                            │
  on VAD end-of-turn:  sarvam-30b + tools ──→ mediator state ──→ both panels
                            │
                      Bulbul TTS ──→ one spoken summary sentence to the listener
```

**Rate-budget design.** The two quota buckets are separate, so work is routed by cost:

| Path | Endpoint | Bucket | Frequency |
|---|---|---|---|
| Live notes | `/translate` | 60/min | ~2–3 per turn (per VAD segment) |
| Agent reasoning | `sarvam-30b` + tools | **40/min** | **exactly 1 per completed turn** |
| Spoken summary | `/text-to-speech` | 30/min (v3) | 1 per turn |

Never call `sarvam-30b` on a partial. Partials only ever hit `/translate`.

### Modules

| Module | Purpose | Depends on |
|---|---|---|
| `app/sarvam.py` | Every endpoint/param in one place | httpx |
| `app/mediator.py` | Term state machine, divergence + hedge detection. **The scored core.** | — |
| `app/agent.py` | Tool definitions + one tool-calling loop against `sarvam-30b` | sarvam, mediator |
| `app/stt_stream.py` | Per-party STT WebSocket proxy; emits partials and turn-end | sarvam |
| `app/session.py` | Append-only turn log, replay, disk snapshot | mediator |
| `app/main.py` | FastAPI: `/ws/{party}`, `/state`, `/packet`, `/replay`, `/reset` | all |
| `static/index.html` | Two panels, term sheet, no build step | — |

Each module is independently testable; `mediator.py` has no I/O at all, which is why
its tests run with no network.

## 6. The agent

**One agent, four tools.** Called once per completed turn with the current sheet and
the finished transcript.

| Tool | Signature | Effect |
|---|---|---|
| `update_term` | `(term, value, verbatim, stance)` | Appends a proposal. `stance ∈ propose\|accept\|reject\|hedge` |
| `flag_divergence` | `(term, note)` | Both accepted different values → blocks the clause |
| `request_clarification` | `(term, question)` | The single disambiguating question |
| `check_readiness` | `()` | Returns which of the six terms are still undiscussed |

**The stance rule that carries the product:** on `accept`, `value` must be recorded
**as the accepting speaker understands it**, never the value the other side proposed.
Smoothing that over destroys the only signal that matters.

**Hedges never upgrade.** `hedge` is not a weak accept; it is a non-answer, and the
term stays blocked.

## 7. Data model

State is an **append-only log**; the term sheet is **derived by replay**. Provenance
is therefore free and nothing is ever overwritten.

```python
Proposal(party, value, verbatim, lang, stance, turn, ts)
Term(key, description, state, proposals[], agreed_value, divergence_note)
Turn(idx, party, lang, transcript, relay_text, interjection, ts)

TermState = OPEN | PROPOSED | AGREED | DIVERGED | HEDGED | REJECTED
TERMS     = rent, deposit, duration, maintenance, notice_period, painting
```

`AGREED` requires both parties' latest values to normalise equal. `"500"` and
`"fixed 500"` deliberately do **not** compare equal — that gap is the dispute.

Persistence: JSON snapshot to `sessions/<id>.json` after every turn; restored on
boot. Survives a live server restart, which is the Memory & Context proof.

## 8. Risks and mitigations

1. **Hands-free VAD misfires on arena noise.** Highest risk; user chose hands-free
   knowingly. Mitigations: `high_vad_sensitivity` tuned against a recording made *in
   the venue*, not a quiet room; a hidden keyboard shortcut mutes a mic without any
   visible UI change; per-turn transcripts are individually retryable.
2. **Audio format mismatch.** Browser `MediaRecorder` emits webm/opus; **the STT
   WebSocket accepts only wav/PCM.** Capture raw PCM via `AudioWorklet` at 16kHz and
   send `pcm_s16le`. This is the single most likely thing to burn an hour — prove it
   in the first 20 minutes.
3. **Rate limit or empty STT mid-demo.** Never fail silently: the notes panel renders
   a visible `…` placeholder and the turn stays retryable. A dropped phrase that
   looks like success is worse than a visible gap.

## 9. Testing

- `test_mediator.py` — 6 assertions over the state machine, no network. **Passing.**
- Extend with: divergence via the agent's tool path, hedge non-upgrade, replay
  reconstruction after restart.
- **`/replay` endpoint** feeds scripted transcripts through the full agent path with
  no microphone. This is how the three unassisted repeated runs (JTBD top band) are
  produced and rehearsed without burning quota.

## 10. Definition of done

- [ ] `verify.py` green on venue wifi; real latency recorded
- [ ] PCM capture proven end-to-end against the STT WebSocket
- [ ] Live notes appear in the other party's language while the speaker is still talking
- [ ] A staged divergence turns the sheet red and triggers one clarifying question
- [ ] Hedge never becomes AGREED
- [ ] `/replay` runs 3 scripted negotiations end-to-end with zero keystrokes
- [ ] Server restarted mid-session; state intact
- [ ] Lawyer packet renders with verbatim quotes in both scripts and a blocked section
