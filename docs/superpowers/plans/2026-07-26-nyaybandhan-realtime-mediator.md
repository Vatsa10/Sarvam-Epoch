# NyayBandhan Realtime Mediator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two people with no common language negotiate a rental agreement by voice; live notes stream in each other's language, and a stateful agent blocks any clause both sides "agreed" to while meaning different things.

**Architecture:** Browser captures raw 16kHz PCM and streams over a WebSocket to FastAPI, which proxies each party's audio to Sarvam's STT WebSocket. Partial transcripts go to `/translate` (cheap, 60/min bucket) and render as live notes on the *other* party's panel. On VAD end-of-turn, one `sarvam-30b` tool-calling agent (40/min bucket, exactly one call per turn) folds the utterance into an append-only log; the term sheet is derived by replay.

**Tech Stack:** Python 3.11+, FastAPI, `httpx`, `websockets`, vanilla JS + AudioWorklet. No build step, no database, no auth.

## Global Constraints

- **Model IDs:** `sarvam-30b` only. **`sarvam-m` is DEPRECATED — never use it.**
- **TTS speakers are language-dependent:** gu-IN → `ratan`; ml-IN → `shubh`. The names `anushka`/`abhilash` are **v2-only and invalid on `bulbul:v3`**.
- **STT WebSocket accepts only `wav` / `pcm_s16le` / `pcm_l16` / `pcm_raw`.** Never send webm/opus.
- **Rate buckets are separate and must not be crossed:** partial transcripts hit `/translate` (60/min) ONLY; `sarvam-30b` (40/min) is called **exactly once per completed turn**; TTS `bulbul:v3` is 30/min.
- **Auth header:** `api-subscription-key`. Auth failure returns **403, not 401**.
- **Never fail silently.** A dropped phrase renders a visible `…` placeholder and the turn stays retryable.
- **State is append-only.** Never overwrite a proposal; the term sheet is always derived by replay.
- All tests run with `python test_mediator.py` / `python test_agent.py` style `__main__` blocks — no pytest dependency.

---

### Task 1: Fix the three verified scaffold bugs

**Files:**
- Modify: `app/sarvam.py`
- Test: `test_config.py` (create)

**Interfaces:**
- Consumes: nothing
- Produces: `sarvam.CHAT_MODEL == "sarvam-30b"`, `sarvam.PARTIES` with valid v3 speakers, `sarvam.STT_WS_URL`, `sarvam.TRANSLATE_URL`

- [ ] **Step 1: Write the failing test**

```python
# test_config.py
from app import sarvam


def test_chat_model_is_not_deprecated():
    assert sarvam.CHAT_MODEL == "sarvam-30b", "sarvam-m is deprecated"


def test_speakers_are_bulbul_v3_valid():
    v2_only = {"anushka", "abhilash", "arya", "hitesh", "karun", "manisha", "vidya"}
    for pid, cfg in sarvam.PARTIES.items():
        assert cfg["speaker"] not in v2_only, f"{pid}: {cfg['speaker']} is v2-only"


def test_speakers_match_documented_language_picks():
    assert sarvam.PARTIES["vatsa"]["speaker"] == "ratan"      # gu-IN male
    assert sarvam.PARTIES["sreedev"]["speaker"] == "shubh"    # ml-IN male


def test_urls_present():
    assert sarvam.STT_WS_URL == "wss://api.sarvam.ai/speech-to-text/ws"
    assert sarvam.TRANSLATE_URL == "https://api.sarvam.ai/translate"


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f(); print(f"  [OK] {n}")
    print("\nconfig sound\n")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_config.py`
Expected: FAIL — `AssertionError: sarvam-m is deprecated`

- [ ] **Step 3: Write minimal implementation**

In `app/sarvam.py` replace the model/URL constants and `PARTIES`:

```python
CHAT_MODEL = os.getenv("SARVAM_CHAT_MODEL", "sarvam-30b")

STT_URL = f"{BASE}/speech-to-text"
STT_WS_URL = "wss://api.sarvam.ai/speech-to-text/ws"
TTS_URL = f"{BASE}/text-to-speech"
TRANSLATE_URL = f"{BASE}/translate"
CHAT_URL = f"{BASE}/v1/chat/completions"

# Speaker choice is language-dependent per Sarvam docs. These are the documented
# picks; anushka/abhilash are v2-only and 400 on bulbul:v3.
PARTIES = {
    "vatsa":   {"name": "Vatsa",   "lang": "gu-IN", "label": "Gujarati",  "speaker": "ratan"},
    "sreedev": {"name": "Sreedev", "lang": "ml-IN", "label": "Malayalam", "speaker": "shubh"},
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_config.py`
Expected: PASS, 4 lines of `[OK]`

- [ ] **Step 5: Commit**

```bash
git add app/sarvam.py test_config.py
git commit -m "fix: use sarvam-30b and bulbul v3 language-specific speakers"
```

---

### Task 2: Add the translate call

**Files:**
- Modify: `app/sarvam.py`
- Test: `test_translate.py` (create)

**Interfaces:**
- Consumes: `sarvam.TRANSLATE_URL`, `sarvam._client` from Task 1
- Produces: `async def translate(text: str, target_language_code: str, source_language_code: str = "auto") -> str`

- [ ] **Step 1: Write the failing test**

Offline test with a stubbed transport — no network, no API key needed.

```python
# test_translate.py
import asyncio
import httpx
from app import sarvam


def test_translate_posts_expected_payload_and_returns_text():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"translated_text": "അതെ"})

    original = sarvam._client
    sarvam._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        out = asyncio.run(sarvam.translate("haan", "ml-IN", "gu-IN"))
    finally:
        asyncio.run(sarvam._client.aclose())
        sarvam._client = original

    assert out == "അതെ"
    assert captured["input"] == "haan"
    assert captured["target_language_code"] == "ml-IN"
    assert captured["source_language_code"] == "gu-IN"
    assert captured["model"] == "mayura:v1"
    assert captured["mode"] == "code-mixed"


def test_translate_returns_empty_string_on_missing_field():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    original = sarvam._client
    sarvam._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        out = asyncio.run(sarvam.translate("x", "ml-IN"))
    finally:
        asyncio.run(sarvam._client.aclose())
        sarvam._client = original
    assert out == ""


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f(); print(f"  [OK] {n}")
    print("\ntranslate sound\n")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_translate.py`
Expected: FAIL — `AttributeError: module 'app.sarvam' has no attribute 'translate'`

- [ ] **Step 3: Write minimal implementation**

Append to `app/sarvam.py`:

```python
async def translate(text: str, target_language_code: str,
                    source_language_code: str = "auto") -> str:
    """Live-notes path. Deliberately uses /translate, not the chat model: it is a
    separate 60/min quota bucket, so partials never eat the 40/min sarvam-30b budget.

    mayura:v1 caps at 1000 chars; a single VAD segment is far below that.
    """
    r = await _client.post(
        TRANSLATE_URL,
        headers={**HEADERS, "Content-Type": "application/json"},
        json={
            "input": text[:1000],
            "source_language_code": source_language_code,
            "target_language_code": target_language_code,
            "model": "mayura:v1",
            "mode": "code-mixed",
        },
    )
    r.raise_for_status()
    return r.json().get("translated_text", "")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_translate.py`
Expected: PASS, 2 lines of `[OK]`

- [ ] **Step 5: Commit**

```bash
git add app/sarvam.py test_translate.py
git commit -m "feat: add /translate call for the live-notes path"
```

---

### Task 3: Tool-calling agent

**Files:**
- Create: `app/agent.py`
- Create: `test_agent.py`

**Interfaces:**
- Consumes: `mediator.Negotiation`, `mediator.TERMS`, `sarvam.chat_tools`
- Produces:
  - `TOOLS: list[dict]` — OpenAI-shaped tool schemas
  - `async def run_turn(neg: Negotiation, party: str, lang: str, transcript: str, turn_idx: int) -> TurnResult`
  - `@dataclass TurnResult(updates: list[dict], flagged: list[str], clarification: str | None, summary: str)`

- [ ] **Step 1: Write the failing test**

The agent's tool-dispatch logic is tested with a fake tool-call payload — no network.

```python
# test_agent.py
from app.agent import TOOLS, apply_tool_calls, TurnResult
from app.mediator import Negotiation, TermState


def test_tools_are_openai_shaped():
    names = {t["function"]["name"] for t in TOOLS}
    assert names == {"update_term", "flag_divergence", "request_clarification", "check_readiness"}
    for t in TOOLS:
        assert t["type"] == "function"
        assert "parameters" in t["function"]


def test_update_term_tool_records_proposal():
    neg = Negotiation()
    calls = [{"name": "update_term", "arguments": {
        "term": "rent", "value": "15000", "verbatim": "pandar hajaar", "stance": "propose"}}]
    res = apply_tool_calls(neg, "vatsa", "gu-IN", calls, 0)
    assert isinstance(res, TurnResult)
    assert neg.terms["rent"].state is TermState.PROPOSED
    assert neg.terms["rent"].proposals[0].verbatim == "pandar hajaar"


def test_divergence_is_detected_through_the_tool_path():
    neg = Negotiation()
    apply_tool_calls(neg, "vatsa", "gu-IN", [{"name": "update_term", "arguments": {
        "term": "maintenance", "value": "actual", "verbatim": "alag", "stance": "propose"}}], 0)
    res = apply_tool_calls(neg, "sreedev", "ml-IN", [{"name": "update_term", "arguments": {
        "term": "maintenance", "value": "fixed 500", "verbatim": "500 fixed", "stance": "accept"}}], 1)
    assert neg.terms["maintenance"].state is TermState.DIVERGED
    assert "maintenance" in res.flagged
    assert neg.terms["maintenance"].agreed_value is None


def test_hedge_never_becomes_agreed():
    neg = Negotiation()
    apply_tool_calls(neg, "vatsa", "gu-IN", [{"name": "update_term", "arguments": {
        "term": "deposit", "value": "50000", "verbatim": "pachaas", "stance": "propose"}}], 0)
    res = apply_tool_calls(neg, "sreedev", "ml-IN", [{"name": "update_term", "arguments": {
        "term": "deposit", "value": "50000", "verbatim": "nokkaam", "stance": "hedge"}}], 1)
    assert neg.terms["deposit"].state is TermState.HEDGED
    assert "deposit" in res.flagged


def test_request_clarification_is_surfaced():
    neg = Negotiation()
    res = apply_tool_calls(neg, "vatsa", "gu-IN", [{"name": "request_clarification", "arguments": {
        "term": "maintenance", "question": "Fixed 500 or actual cost?"}}], 0)
    assert res.clarification == "Fixed 500 or actual cost?"


def test_check_readiness_lists_undiscussed_terms():
    neg = Negotiation()
    apply_tool_calls(neg, "vatsa", "gu-IN", [{"name": "update_term", "arguments": {
        "term": "rent", "value": "15000", "verbatim": "x", "stance": "propose"}}], 0)
    res = apply_tool_calls(neg, "vatsa", "gu-IN", [{"name": "check_readiness", "arguments": {}}], 1)
    assert "rent" not in res.summary
    assert "notice_period" in res.summary


def test_unknown_tool_is_ignored_not_crashed():
    neg = Negotiation()
    res = apply_tool_calls(neg, "vatsa", "gu-IN", [{"name": "nonexistent", "arguments": {}}], 0)
    assert res.flagged == []


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f(); print(f"  [OK] {n}")
    print("\nagent sound\n")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_agent.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agent'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/agent.py
"""One stateful agent, four tools, exactly ONE sarvam-30b call per completed turn.

Fan-out was rejected deliberately: sarvam-30b is capped at 40 req/min, so a
multi-agent design stalls a live demo. Depth on one agent, not breadth.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import sarvam
from .mediator import Negotiation, TERMS, TermState

TOOLS = [
    {"type": "function", "function": {
        "name": "update_term",
        "description": "Record what this speaker just said about ONE negotiable term.",
        "parameters": {"type": "object", "properties": {
            "term": {"type": "string", "enum": list(TERMS)},
            "value": {"type": "string", "description":
                      "Normalised value AS THIS SPEAKER UNDERSTANDS IT. Digits for "
                      "amounts. For maintenance distinguish 'fixed 500' from 'actual'."},
            "verbatim": {"type": "string", "description":
                         "Their exact words for this term, original script, unedited."},
            "stance": {"type": "string", "enum": ["propose", "accept", "reject", "hedge"]},
        }, "required": ["term", "value", "verbatim", "stance"]}}},
    {"type": "function", "function": {
        "name": "flag_divergence",
        "description": "Both parties think they agreed but meant different values.",
        "parameters": {"type": "object", "properties": {
            "term": {"type": "string", "enum": list(TERMS)},
            "note": {"type": "string"},
        }, "required": ["term", "note"]}}},
    {"type": "function", "function": {
        "name": "request_clarification",
        "description": "Ask the ONE question that settles an ambiguity. Use sparingly.",
        "parameters": {"type": "object", "properties": {
            "term": {"type": "string", "enum": list(TERMS)},
            "question": {"type": "string"},
        }, "required": ["term", "question"]}}},
    {"type": "function", "function": {
        "name": "check_readiness",
        "description": "List which terms are still undiscussed. No arguments.",
        "parameters": {"type": "object", "properties": {}}}},
]

SYSTEM = """You are a neutral mediator on a live rental negotiation between two people
who share no common language. You hear ONE speaker's utterance at a time, in their own
language, and you maintain the term sheet.

Call update_term for every term they touched. Call flag_divergence when this speaker
accepts a term but means a different value than the other party proposed. Call
request_clarification at most once per turn, and only when a real ambiguity blocks
drafting. Call check_readiness when the parties sound like they are wrapping up.

THE RULE THAT MATTERS: when a speaker ACCEPTS, set `value` to what THEY mean, never to
what the other side proposed. Two people can both say yes and mean different things -
detecting that is your entire job. Do not smooth it over.

A hedge ("we'll see", "dekhte hain", "nokkaam", "joiye chhe") is NOT an accept. Never
upgrade a hedge.

Then write a one-sentence plain-English summary of what the speaker said."""


@dataclass
class TurnResult:
    updates: list[dict] = field(default_factory=list)
    flagged: list[str] = field(default_factory=list)
    clarification: str | None = None
    summary: str = ""


def apply_tool_calls(neg: Negotiation, party: str, lang: str,
                     calls: list[dict], turn_idx: int) -> TurnResult:
    """Dispatch parsed tool calls into negotiation state.

    Pure and synchronous, which is why it is fully testable without a network.
    """
    res = TurnResult()
    updates: list[dict] = []

    for call in calls:
        name = call.get("name")
        args = call.get("arguments") or {}

        if name == "update_term":
            updates.append(args)
        elif name == "flag_divergence":
            key = args.get("term")
            if key in neg.terms:
                t = neg.terms[key]
                t.state = TermState.DIVERGED
                t.agreed_value = None
                t.divergence_note = args.get("note", "")
                res.flagged.append(key)
        elif name == "request_clarification":
            res.clarification = args.get("question")
        elif name == "check_readiness":
            undiscussed = [k for k, t in neg.terms.items() if t.state is TermState.OPEN]
            res.summary = "Still undiscussed: " + (", ".join(undiscussed) or "nothing")
        # unknown tool names are ignored on purpose - a hallucinated call must not
        # crash a live turn

    if updates:
        res.flagged.extend(neg.apply(party, lang, updates, turn_idx))
        res.updates = updates

    # dedupe, preserve order
    res.flagged = list(dict.fromkeys(res.flagged))
    return res


def _parse_tool_calls(message: dict) -> list[dict]:
    out = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function", {})
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            args = {}
        out.append({"name": fn.get("name"), "arguments": args})
    return out


async def run_turn(neg: Negotiation, party: str, lang: str,
                   transcript: str, turn_idx: int) -> TurnResult:
    """ONE sarvam-30b call per completed turn. Never called on a partial."""
    sheet = "\n".join(
        f"- {t.key}: {t.state.value}" + (f" = {t.agreed_value}" if t.agreed_value else "")
        for t in neg.terms.values() if t.state is not TermState.OPEN
    ) or "(nothing discussed yet)"

    message = await sarvam.chat_tools(
        system=SYSTEM,
        user=f"Current term sheet:\n{sheet}\n\nSpeaker ({party}, {lang}) said:\n{transcript}",
        tools=TOOLS,
    )
    res = apply_tool_calls(neg, party, lang, _parse_tool_calls(message), turn_idx)
    if not res.summary:
        res.summary = (message.get("content") or transcript).strip()
    return res
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_agent.py`
Expected: PASS, 7 lines of `[OK]`

- [ ] **Step 5: Add the `chat_tools` transport to `app/sarvam.py`**

```python
async def chat_tools(system: str, user: str, tools: list[dict],
                     temperature: float = 0.1) -> dict:
    """Tool-calling chat. Returns the raw assistant message dict so the caller can
    read both `tool_calls` and `content`."""
    r = await _client.post(
        CHAT_URL,
        headers={**HEADERS, "Content-Type": "application/json"},
        json={
            "model": CHAT_MODEL,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "tools": tools,
            "tool_choice": "auto",
            "temperature": temperature,
        },
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]
```

- [ ] **Step 6: Re-run tests**

Run: `python test_agent.py && python test_mediator.py`
Expected: both PASS

- [ ] **Step 7: Commit**

```bash
git add app/agent.py app/sarvam.py test_agent.py
git commit -m "feat: tool-calling mediator agent, one model call per turn"
```

---

### Task 4: Session persistence with replay

**Files:**
- Create: `app/session.py`
- Create: `test_session.py`

**Interfaces:**
- Consumes: `mediator.Negotiation`, `mediator.Proposal`, `mediator.Turn`, `mediator.TermState`
- Produces: `def save(neg: Negotiation, path: Path) -> None`, `def load(path: Path, session_id: str) -> Negotiation`

- [ ] **Step 1: Write the failing test**

```python
# test_session.py
import pathlib
import tempfile

from app import session
from app.mediator import Negotiation, TermState


def _sample() -> Negotiation:
    neg = Negotiation("demo")
    neg.apply("vatsa", "gu-IN", [{"term": "rent", "value": "15000",
              "verbatim": "pandar hajaar", "stance": "propose"}], 0)
    neg.apply("sreedev", "ml-IN", [{"term": "rent", "value": "15000",
              "verbatim": "pathinanju", "stance": "accept"}], 1)
    neg.apply("vatsa", "gu-IN", [{"term": "maintenance", "value": "actual",
              "verbatim": "alag", "stance": "propose"}], 2)
    neg.apply("sreedev", "ml-IN", [{"term": "maintenance", "value": "fixed 500",
              "verbatim": "500", "stance": "accept"}], 3)
    return neg


def test_roundtrip_preserves_states():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "s.json"
        session.save(_sample(), p)
        back = session.load(p, "demo")
    assert back.terms["rent"].state is TermState.AGREED
    assert back.terms["rent"].agreed_value == "15000"
    assert back.terms["maintenance"].state is TermState.DIVERGED


def test_roundtrip_preserves_verbatim_provenance():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "s.json"
        session.save(_sample(), p)
        back = session.load(p, "demo")
    quotes = [pr.verbatim for pr in back.terms["rent"].proposals]
    assert quotes == ["pandar hajaar", "pathinanju"]


def test_load_missing_file_returns_empty_negotiation():
    with tempfile.TemporaryDirectory() as d:
        back = session.load(pathlib.Path(d) / "nope.json", "fresh")
    assert back.session_id == "fresh"
    assert all(t.state is TermState.OPEN for t in back.terms.values())


def test_load_corrupt_file_returns_empty_negotiation():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        back = session.load(p, "fresh")
    assert all(t.state is TermState.OPEN for t in back.terms.values())


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f(); print(f"  [OK] {n}")
    print("\nsession sound\n")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_session.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.session'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/session.py
"""Disk persistence. State survives a server restart, which is the Memory & Context
proof shown on stage.

A corrupt or missing snapshot returns a fresh negotiation rather than raising: losing
a session mid-demo is bad, crashing on boot is worse.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import asdict

from .mediator import Negotiation, Proposal, TermState, Turn


def save(neg: Negotiation, path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(neg.sheet(), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load(path: pathlib.Path, session_id: str) -> Negotiation:
    neg = Negotiation(session_id)
    if not path.exists():
        return neg
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return neg

    for t in data.get("terms", []):
        term = neg.terms.get(t.get("key"))
        if term is None:
            continue
        try:
            term.state = TermState(t["state"])
        except (KeyError, ValueError):
            continue
        term.agreed_value = t.get("agreed_value")
        term.divergence_note = t.get("divergence_note")
        term.proposals = [Proposal(**p) for p in t.get("proposals", [])]

    neg.turns = [Turn(**x) for x in data.get("turns", [])]
    return neg
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_session.py`
Expected: PASS, 4 lines of `[OK]`

- [ ] **Step 5: Commit**

```bash
git add app/session.py test_session.py
git commit -m "feat: session persistence surviving restart"
```

---

### Task 5: PCM capture in the browser

**Files:**
- Create: `static/pcm-worklet.js`
- Modify: `static/index.html`

**Interfaces:**
- Consumes: nothing
- Produces: browser global `startCapture(onChunk: (Int16Array) => void): Promise<{stop: () => void}>` — emits 16kHz mono `pcm_s16le` frames

**Why this task exists:** `MediaRecorder` emits webm/opus and the Sarvam STT WebSocket **rejects it**. This is the single most likely thing to burn an hour, so it is proven standalone before any socket work.

- [ ] **Step 1: Write the worklet**

```javascript
// static/pcm-worklet.js
// Downsamples the browser's native rate to 16kHz mono and emits Int16 PCM frames.
// The STT WebSocket accepts wav/pcm only - webm/opus from MediaRecorder is rejected.
class PCMWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this.target = 16000;
    this.ratio = sampleRate / this.target;   // sampleRate is a worklet global
    this.pos = 0;
    this.buf = [];
  }
  process(inputs) {
    const ch = inputs[0][0];
    if (!ch) return true;
    // linear decimation - adequate for speech, and cheap
    for (let i = 0; i < ch.length; i++) {
      this.pos += 1;
      if (this.pos >= this.ratio) {
        this.pos -= this.ratio;
        const s = Math.max(-1, Math.min(1, ch[i]));
        this.buf.push(s < 0 ? s * 0x8000 : s * 0x7fff);
      }
    }
    if (this.buf.length >= 1600) {          // ~100ms at 16kHz
      this.port.postMessage(Int16Array.from(this.buf));
      this.buf = [];
    }
    return true;
  }
}
registerProcessor('pcm-worklet', PCMWorklet);
```

- [ ] **Step 2: Add the capture helper to `static/index.html`**

Insert inside the existing `<script>` block, above the mic wiring:

```javascript
async function startCapture(onChunk){
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}
  });
  const ctx = new AudioContext();
  await ctx.audioWorklet.addModule('/pcm-worklet.js');
  const src = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, 'pcm-worklet');
  node.port.onmessage = e => onChunk(e.data);
  src.connect(node);
  return {stop(){
    node.port.onmessage = null;
    node.disconnect(); src.disconnect();
    stream.getTracks().forEach(t => t.stop());
    ctx.close();
  }};
}
```

- [ ] **Step 3: Prove it in the browser manually**

Run: `uvicorn app.main:app --reload --port 8000`, open `http://localhost:8000`, then in DevTools console:

```javascript
let n = 0;
const cap = await startCapture(c => { n += c.length; console.log('samples', n); });
// speak for 3 seconds
setTimeout(() => { cap.stop(); console.log('total', n); }, 3000);
```

Expected: `total` is roughly 48000 (3s × 16000). If it is ~144000 the decimation ratio is wrong; if 0, the worklet failed to load — check the `/pcm-worklet.js` path resolves.

- [ ] **Step 4: Commit**

```bash
git add static/pcm-worklet.js static/index.html
git commit -m "feat: 16kHz PCM capture via AudioWorklet for STT WebSocket"
```

---

### Task 6: STT WebSocket proxy

**Files:**
- Create: `app/stt_stream.py`
- Create: `test_stt_stream.py`

**Interfaces:**
- Consumes: `sarvam.STT_WS_URL`, `sarvam.API_KEY`
- Produces: `def build_ws_url(language_code: str) -> str`, `def classify(msg: dict) -> tuple[str, str]` returning `(kind, text)` where `kind ∈ {"partial", "final", "turn_end", "error", "ignore"}`

- [ ] **Step 1: Write the failing test**

Message classification is pure, so it is tested without a socket.

```python
# test_stt_stream.py
from app.stt_stream import build_ws_url, classify


def test_url_carries_required_params():
    u = build_ws_url("gu-IN")
    assert u.startswith("wss://api.sarvam.ai/speech-to-text/ws?")
    for frag in ["language-code=gu-IN", "model=saaras%3Av3", "input_audio_codec=pcm_s16le",
                 "high_vad_sensitivity=true", "vad_signals=true"]:
        assert frag in u, f"missing {frag} in {u}"


def test_partial_transcript():
    kind, text = classify({"type": "data", "data": {"transcript": "pandar", "is_final": False}})
    assert (kind, text) == ("partial", "pandar")


def test_final_transcript():
    kind, text = classify({"type": "data", "data": {"transcript": "pandar hajaar", "is_final": True}})
    assert (kind, text) == ("final", "pandar hajaar")


def test_vad_end_of_turn_event():
    kind, _ = classify({"type": "events", "data": {"signal_type": "END_SPEECH"}})
    assert kind == "turn_end"


def test_error_message():
    kind, text = classify({"type": "error", "error": {"message": "bad codec"}})
    assert kind == "error"
    assert "bad codec" in text


def test_unknown_message_is_ignored():
    assert classify({"type": "pong"})[0] == "ignore"
    assert classify({})[0] == "ignore"


def test_empty_transcript_is_ignored_not_emitted():
    assert classify({"type": "data", "data": {"transcript": "   ", "is_final": True}})[0] == "ignore"


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f(); print(f"  [OK] {n}")
    print("\nstt_stream sound\n")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_stt_stream.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.stt_stream'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/stt_stream.py
"""Sarvam STT WebSocket: URL construction and message classification.

Kept pure and I/O-free so the message contract is testable without a socket. The
actual connection lives in main.py where it can be wired to a browser WebSocket.
"""
from __future__ import annotations

from urllib.parse import urlencode

from . import sarvam


def build_ws_url(language_code: str) -> str:
    """Codec MUST be pcm_s16le - the WS rejects webm/opus, which is why the browser
    captures raw PCM via AudioWorklet rather than MediaRecorder."""
    q = urlencode({
        "language-code": language_code,
        "model": "saaras:v3",
        "input_audio_codec": "pcm_s16le",
        "sample_rate": "16000",
        "high_vad_sensitivity": "true",
        "vad_signals": "true",
    })
    return f"{sarvam.STT_WS_URL}?{q}"


def classify(msg: dict) -> tuple[str, str]:
    """Map a server frame to (kind, text).

    kind: partial | final | turn_end | error | ignore
    """
    kind = (msg or {}).get("type")

    if kind == "data":
        text = ((msg.get("data") or {}).get("transcript") or "").strip()
        if not text:
            return ("ignore", "")
        return ("final" if (msg.get("data") or {}).get("is_final") else "partial", text)

    if kind == "events":
        signal = ((msg.get("data") or {}).get("signal_type") or "").upper()
        if "END" in signal:
            return ("turn_end", "")
        return ("ignore", "")

    if kind == "error":
        return ("error", ((msg.get("error") or {}).get("message") or "unknown STT error"))

    return ("ignore", "")


WS_HEADERS = {"api-subscription-key": sarvam.API_KEY}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_stt_stream.py`
Expected: PASS, 7 lines of `[OK]`

- [ ] **Step 5: Commit**

```bash
git add app/stt_stream.py test_stt_stream.py
git commit -m "feat: STT WebSocket url builder and message classifier"
```

---

### Task 7: Wire the live relay

**Files:**
- Modify: `app/main.py`
- Modify: `static/index.html`
- Modify: `requirements.txt` (add `websockets==14.1`)

**Interfaces:**
- Consumes: `agent.run_turn`, `session.save/load`, `stt_stream.build_ws_url/classify/WS_HEADERS`, `sarvam.translate/tts`
- Produces: WebSocket route `/ws/{party}`; broadcast frames `{type: "note"|"turn"|"state"|"error", ...}`

- [ ] **Step 1: Add the WebSocket route to `app/main.py`**

```python
import asyncio
import json as _json

import websockets
from fastapi import WebSocket, WebSocketDisconnect

from . import agent, session, stt_stream

PANELS: dict[str, set[WebSocket]] = {"vatsa": set(), "sreedev": set()}


async def _broadcast(party: str, payload: dict) -> None:
    """Send to one party's panel. Dead sockets are dropped, never raised - a closed
    tab must not kill a live turn."""
    dead = []
    for ws in PANELS.get(party, set()):
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
                    await up.send(_json.dumps({
                        "audio": {"data": _b64(chunk), "encoding": "audio/wav"}
                    }))

            async def pump_down() -> None:
                """Sarvam -> notes, and on turn end, the agent."""
                buffer: list[str] = []
                async for raw in up:
                    kind, text = stt_stream.classify(_json.loads(raw))

                    if kind == "partial":
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

            await asyncio.gather(pump_up(), pump_down())

    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        await _broadcast(party, {"type": "error", "text": f"{type(e).__name__}: {e}"})
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
    idx = len(NEG.turns)

    try:
        res = await agent.run_turn(NEG, party, me["lang"], transcript, idx)
    except Exception as e:  # noqa: BLE001
        for p in sarvam.PARTIES:
            await _broadcast(p, {"type": "error", "text": f"agent failed: {e}"})
        return

    spoken = res.clarification or res.summary
    audio = ""
    if spoken:
        try:
            audio = await sarvam.tts(spoken, other_cfg["lang"], other_cfg["speaker"])
        except Exception:  # noqa: BLE001
            audio = ""

    NEG.turns.append(Turn(idx=idx, party=party, lang=me["lang"], transcript=transcript,
                          relay_text=spoken, interjection=res.clarification))
    session.save(NEG, SESSIONS / f"{NEG.session_id}.json")

    sheet = NEG.sheet()
    await _broadcast(other, {"type": "turn", "from": me["name"], "spoken": spoken,
                             "audio_b64": audio, "flagged": res.flagged, "sheet": sheet})
    await _broadcast(party, {"type": "state", "flagged": res.flagged, "sheet": sheet})
```

Add `import base64` at the top, and replace the existing `_persist`/`_restore` bodies with `session.save` / `session.load`.

- [ ] **Step 2: Add the client side to `static/index.html`**

```javascript
function connect(party){
  const ws = new WebSocket(`ws://${location.host}/ws/${party}`);
  ws.binaryType = 'arraybuffer';
  ws.onmessage = e => {
    const d = JSON.parse(e.data);
    if (d.type === 'note')  showNote(d);
    if (d.type === 'turn')  { showNote({from:d.from, text:d.spoken, final:true});
                              if (d.audio_b64) playB64(d.audio_b64);
                              paint(d.sheet); }
    if (d.type === 'state') paint(d.sheet);
    if (d.type === 'error') setStatus('⚠ ' + d.text);
  };
  return ws;
}

function showNote({from, text, final}){
  const id = 'live-note';
  let el = document.getElementById(id);
  if (final || !el){
    el = document.createElement('div');
    el.className = 't';
    if (!final) el.id = id;
    $('#log').prepend(el);
  }
  el.innerHTML = `<div class="who">${esc(from)}</div><div class="said">${esc(text)}</div>`;
  if (final && el.id === id) el.removeAttribute('id');
}

function playB64(b64){ $('#player').src = 'data:audio/wav;base64,' + b64; $('#player').play().catch(()=>{}); }

async function goLive(party){
  const ws = connect(party);
  await new Promise(r => ws.onopen = r);
  const cap = await startCapture(chunk => {
    if (ws.readyState === 1) ws.send(chunk.buffer);
  });
  return {stop(){ cap.stop(); ws.close(); }};
}
```

- [ ] **Step 3: Install the dependency**

```bash
echo websockets==14.1 >> requirements.txt
pip install websockets==14.1
```

- [ ] **Step 4: Verify the server boots and routes exist**

Run: `python -c "from app.main import app; print([r.path for r in app.routes])"`
Expected: output includes `/ws/{party}`

- [ ] **Step 5: Commit**

```bash
git add app/main.py static/index.html requirements.txt
git commit -m "feat: live bilingual relay over websockets"
```

---

### Task 8: `/replay` for unassisted evidence runs

**Files:**
- Modify: `app/main.py`
- Create: `fixtures/scenario_1.json`, `fixtures/scenario_2.json`, `fixtures/scenario_3.json`
- Create: `test_replay.py`

**Interfaces:**
- Consumes: `agent.run_turn`, `mediator.Negotiation`
- Produces: `POST /replay/{scenario}` → `{"session": ..., "expected_ok": bool, "mismatches": [...]}`

**Why this task exists:** JTBD's top band needs 3+ repeated cases running end-to-end with zero builder intervention. This is also how the team rehearses without a microphone or burning quota.

- [ ] **Step 1: Write the fixtures**

```json
// fixtures/scenario_1.json  - clean agreement, then a divergence
{
  "name": "maintenance divergence",
  "turns": [
    {"party": "vatsa",   "transcript": "Bhaade pandar hajaar rupiya rahese."},
    {"party": "sreedev", "transcript": "Sari, pathinanju ayiram OK aanu."},
    {"party": "vatsa",   "transcript": "Maintenance alag thi aapvanu rahese."},
    {"party": "sreedev", "transcript": "Sari, maintenance separate, apo 500 rupa."}
  ],
  "expected": {"rent": "AGREED", "maintenance": "DIVERGED"}
}
```

```json
// fixtures/scenario_2.json  - hedge must not become agreement
{
  "name": "polite non-answer on deposit",
  "turns": [
    {"party": "vatsa",   "transcript": "Deposit pachaas hajaar joiye chhe."},
    {"party": "sreedev", "transcript": "Aa... nammal nokkaam, parayaam."}
  ],
  "expected": {"deposit": "HEDGED"}
}
```

```json
// fixtures/scenario_3.json  - fully clean, nothing invented
{
  "name": "clean agreement, no conflict",
  "turns": [
    {"party": "vatsa",   "transcript": "Bhaade bar hajaar, agiyar mahina mate."},
    {"party": "sreedev", "transcript": "Pannirayiram, pathinonnu masam, sammatham."}
  ],
  "expected": {"rent": "AGREED", "duration": "AGREED"}
}
```

- [ ] **Step 2: Write the failing test**

```python
# test_replay.py
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent


def test_all_scenarios_are_wellformed():
    files = sorted((ROOT / "fixtures").glob("scenario_*.json"))
    assert len(files) >= 3, "JTBD L4/L5 needs at least 3 repeated cases"
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        assert d["turns"] and d["expected"]
        for t in d["turns"]:
            assert t["party"] in {"vatsa", "sreedev"}
            assert t["transcript"].strip()


def test_scenarios_cover_the_three_scored_behaviours():
    states = set()
    for f in (ROOT / "fixtures").glob("scenario_*.json"):
        states |= set(json.loads(f.read_text(encoding="utf-8"))["expected"].values())
    assert {"AGREED", "DIVERGED", "HEDGED"} <= states


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f(); print(f"  [OK] {n}")
    print("\nreplay fixtures sound\n")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python test_replay.py`
Expected: FAIL — `AssertionError: JTBD L4/L5 needs at least 3 repeated cases` (or the glob is empty)

- [ ] **Step 4: Add the endpoint to `app/main.py`**

```python
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
    global NEG
    NEG = Negotiation(f"replay_{scenario}")

    for i, t in enumerate(spec["turns"]):
        res = await agent.run_turn(NEG, t["party"], sarvam.PARTIES[t["party"]]["lang"],
                                   t["transcript"], i)
        NEG.turns.append(Turn(idx=i, party=t["party"],
                              lang=sarvam.PARTIES[t["party"]]["lang"],
                              transcript=t["transcript"],
                              relay_text=res.summary, interjection=res.clarification))

    session.save(NEG, SESSIONS / f"{NEG.session_id}.json")
    sheet = NEG.sheet()
    actual = {t["key"]: t["state"] for t in sheet["terms"]}
    mismatches = [
        {"term": k, "expected": v, "actual": actual.get(k)}
        for k, v in spec["expected"].items() if actual.get(k) != v
    ]
    return JSONResponse({"scenario": spec["name"], "expected_ok": not mismatches,
                         "mismatches": mismatches, "sheet": sheet})
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python test_replay.py`
Expected: PASS, 2 lines of `[OK]`

- [ ] **Step 6: Commit**

```bash
git add app/main.py fixtures/ test_replay.py
git commit -m "feat: /replay endpoint and three evidence scenarios"
```

---

### Task 9 (SUPERSEDED - UI work moved to Sreedev; runner survives as Task 14): Hidden mute and full-suite green

**Files:**
- Modify: `static/index.html`
- Create: `run_tests.py`

**Interfaces:**
- Consumes: all prior tests
- Produces: `python run_tests.py` runs every suite and exits non-zero on failure

- [ ] **Step 1: Add the hidden mute shortcut**

Arena-noise insurance. No visible UI, so it costs nothing on stage unless needed.

```javascript
// Ctrl+Shift+M toggles mute on both live mics. Deliberately invisible - it exists
// only for the case where room noise starts triggering phantom VAD turns.
let MUTED = false;
addEventListener('keydown', e => {
  if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'm'){
    MUTED = !MUTED;
    setStatus(MUTED ? '⏸ mics muted' : 'live');
  }
});
```

Then in `goLive`, gate the send:

```javascript
  const cap = await startCapture(chunk => {
    if (!MUTED && ws.readyState === 1) ws.send(chunk.buffer);
  });
```

- [ ] **Step 2: Write the suite runner**

```python
# run_tests.py
"""Every suite, one command. Exits non-zero if anything fails."""
import subprocess
import sys

SUITES = ["test_config.py", "test_translate.py", "test_mediator.py",
          "test_agent.py", "test_session.py", "test_stt_stream.py", "test_replay.py"]

failed = []
for s in SUITES:
    print(f"\n=== {s} ===")
    if subprocess.run([sys.executable, s]).returncode != 0:
        failed.append(s)

print("\n" + ("ALL GREEN" if not failed else f"FAILED: {', '.join(failed)}"))
sys.exit(1 if failed else 0)
```

- [ ] **Step 3: Run the full suite**

Run: `python run_tests.py`
Expected: `ALL GREEN`

- [ ] **Step 4: Commit**

```bash
git add static/index.html run_tests.py
git commit -m "feat: hidden mute shortcut and full test runner"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §4 corrected model ID, speakers | Task 1 |
| §5 rate budget — translate bucket | Task 2 |
| §6 one agent, four tools | Task 3 |
| §7 append-only, replay, restart | Task 4 |
| §8 risk 2 (PCM not webm) | Task 5 |
| §5 STT WebSocket, VAD turn-end | Tasks 6, 7 |
| §5 live notes to other party | Task 7 |
| §5 one spoken summary per turn | Task 7 (`_finish_turn`) |
| §8 risk 3 (never fail silently) | Task 7 (`_safe_translate`, error frames) |
| §8 risk 1 (VAD misfire) | Task 9 (hidden mute) |
| §9 `/replay`, 3 scenarios | Task 8 |
| §10 definition of done | Tasks 1–9 + `verify.py` (exists) |

**Placeholder scan:** none — every code step carries real content.

**Type consistency:** `TurnResult(updates, flagged, clarification, summary)` produced in Task 3 is consumed unchanged in Tasks 7 and 8. `session.save/load` signatures in Task 4 match their call sites. `classify` returns `(kind, text)` in Task 6 and is destructured that way in Task 7. `startCapture` from Task 5 is called in Task 7's `goLive` and re-gated in Task 9.

**Ordering note:** Tasks 1–4 are pure and testable offline; 5–7 need a browser and a live key. If `verify.py` is not green, do 1–4 anyway — they are the scored core and they do not touch the network.

---

## Addendum — 12:55, direction change

Sreedev owns UI and backend wiring from here. The remaining work is the **AI layer only**.
Task 9's hidden-mute (UI) is dropped; its test runner survives into Task 14.

**New global constraints:**
- **Agent reasoning and tool calling run on `gpt-4o-mini`** via the OpenAI SDK. Sarvam keeps every
  speech surface — Saaras STT, Bulbul TTS, `/translate`. The declared scored parameter is Voice
  Experience and Saaras/Bulbul carry it; the reasoner is a supporting model, which the build rules
  permit ("Other models and APIs may support it").
- Side benefit: moving agent calls off `sarvam-30b` frees its entire 40/min budget.
- Both keys live in a gitignored `.env`: `SARVAM_API_KEY`, `OPENAI_API_KEY`. Never commit it, never
  print a key value.
- **The agent must never confuse who said what.** Every turn carries explicit speaker identity and
  attributed conversation history.

---

### Task 12: Pluggable LLM provider — gpt-4o-mini for reasoning

**Files:**
- Create: `app/llm.py`
- Create: `test_llm.py`
- Modify: `app/agent.py` (swap `sarvam.chat_tools` to `llm.complete_with_tools`)
- Modify: `requirements.txt` (add `openai==1.59.6`)

**Interfaces:**
- Consumes: env `AGENT_PROVIDER` (default `openai`), `OPENAI_API_KEY`, `AGENT_MODEL` (default `gpt-4o-mini`)
- Produces: `async def complete_with_tools(system, user, tools, temperature=0.1) -> dict` returning an
  OpenAI-shaped assistant **message dict** with `content` and optional `tool_calls` — the exact shape
  `agent._parse_tool_calls` already consumes.

- [ ] **Step 1: Write the failing test**

```python
# test_llm.py
import asyncio

from app import llm


def test_provider_defaults_to_openai_gpt4o_mini():
    assert llm.PROVIDER == "openai"
    assert llm.MODEL == "gpt-4o-mini"


def test_complete_with_tools_returns_message_dict():
    async def fake(system, user, tools, temperature):
        return {"content": "ok", "tool_calls": [
            {"function": {"name": "update_term", "arguments": '{"term":"rent"}'}}]}

    original = llm._route
    llm._route = fake
    try:
        out = asyncio.run(llm.complete_with_tools("sys", "usr", [], 0.1))
    finally:
        llm._route = original
    assert out["content"] == "ok"
    assert out["tool_calls"][0]["function"]["name"] == "update_term"


def test_sarvam_provider_is_still_selectable():
    assert "sarvam" in llm.PROVIDERS


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f(); print(f"  [OK] {n}")
    print("\nllm sound\n")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_llm.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.llm'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/llm.py
"""Reasoning provider for the mediator agent.

gpt-4o-mini does tool calling; Sarvam keeps every speech surface (Saaras, Bulbul,
/translate), which is what the declared Voice Experience parameter is scored on.
Moving reasoning off sarvam-30b also frees its whole 40 req/min budget for speech.

`sarvam` stays selectable via AGENT_PROVIDER so the build still runs if the OpenAI
key fails on the floor - one env var, no code change.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

from . import sarvam

load_dotenv()

PROVIDERS = ("openai", "sarvam")
PROVIDER = os.getenv("AGENT_PROVIDER", "openai").lower()
MODEL = os.getenv("AGENT_MODEL", "gpt-4o-mini")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")

_openai_client = None


def _client():
    global _openai_client
    if _openai_client is None:
        from openai import AsyncOpenAI
        _openai_client = AsyncOpenAI(api_key=OPENAI_KEY)
    return _openai_client


async def _openai_call(system: str, user: str, tools: list[dict], temperature: float) -> dict:
    kwargs = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": temperature,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    r = await _client().chat.completions.create(**kwargs)
    return r.choices[0].message.model_dump()


async def _sarvam_call(system: str, user: str, tools: list[dict], temperature: float) -> dict:
    return await sarvam.chat_tools(system, user, tools, temperature)


async def _route(system: str, user: str, tools: list[dict], temperature: float) -> dict:
    if PROVIDER == "sarvam":
        return await _sarvam_call(system, user, tools, temperature)
    return await _openai_call(system, user, tools, temperature)


async def complete_with_tools(system: str, user: str, tools: list[dict],
                              temperature: float = 0.1) -> dict:
    """Returns an OpenAI-shaped assistant message dict: {content, tool_calls?}."""
    return await _route(system, user, tools, temperature)
```

In `app/agent.py`, add `from . import llm` and replace the `sarvam.chat_tools(...)` call inside
`run_turn` with `await llm.complete_with_tools(system=SYSTEM, user=..., tools=TOOLS)`.
Leave `sarvam.chat_tools` in place — it is the fallback path.

- [ ] **Step 4: Run tests**

Run: `python test_llm.py && python test_agent.py`
Expected: both PASS

- [ ] **Step 5: Commit**

```bash
git add app/llm.py app/agent.py test_llm.py requirements.txt
git commit -m "feat: run agent reasoning on gpt-4o-mini, Sarvam keeps speech"
```

---

### Task 13: Turn-aware stateful context

**Files:**
- Modify: `app/mediator.py` (add `Negotiation.transcript_history`)
- Modify: `app/agent.py` (add `build_context`, extend `SYSTEM`)
- Modify: `test_agent.py`, `test_mediator.py`

**Interfaces:**
- Produces: `Negotiation.transcript_history(limit: int = 6) -> str`
- Produces: `agent.build_context(neg, party, transcript) -> str`

**Why:** the agent currently sees only the current utterance and a bare term-sheet summary. It has
no idea who spoke previously, so it can misattribute a stance — recording one party's acceptance
against their own proposal, or crediting A with B's words. In a two-party negotiation where the
entire product is "who agreed to what," a misattribution becomes a wrong clause in a signed document.

- [ ] **Step 1: Write the failing tests**

```python
# append to test_mediator.py
def test_transcript_history_attributes_each_speaker():
    from app.mediator import Negotiation, Turn
    n = Negotiation()
    n.turns.append(Turn(idx=0, party="vatsa", lang="gu-IN",
                        transcript="bhaade pandar hajaar", relay_text="", interjection=None))
    n.turns.append(Turn(idx=1, party="sreedev", lang="ml-IN",
                        transcript="pathinanju sari", relay_text="", interjection=None))
    h = n.transcript_history()
    assert "Vatsa" in h and "Sreedev" in h
    assert "pandar hajaar" in h and "pathinanju" in h
    assert h.index("Vatsa") < h.index("Sreedev"), "history must preserve turn order"


def test_transcript_history_respects_limit():
    from app.mediator import Negotiation, Turn
    n = Negotiation()
    for i in range(10):
        n.turns.append(Turn(idx=i, party="vatsa", lang="gu-IN",
                            transcript=f"utterance{i}", relay_text="", interjection=None))
    h = n.transcript_history(limit=3)
    assert "utterance9" in h and "utterance6" not in h


def test_transcript_history_empty_is_safe():
    from app.mediator import Negotiation
    assert isinstance(Negotiation().transcript_history(), str)


def test_cannot_agree_with_your_own_proposal():
    """A speaker accepting their OWN prior proposal is not agreement - there is no
    counterparty. Guards the single worst misattribution the product can make."""
    from app.mediator import Negotiation, TermState
    n = Negotiation()
    n.apply("vatsa", "gu-IN", [{"term": "rent", "value": "15000",
            "verbatim": "pandar", "stance": "propose"}], 0)
    n.apply("vatsa", "gu-IN", [{"term": "rent", "value": "15000",
            "verbatim": "haan theek", "stance": "accept"}], 1)
    assert n.terms["rent"].state is not TermState.AGREED
    assert n.terms["rent"].agreed_value is None
```

```python
# append to test_agent.py
def test_context_names_the_current_speaker_and_the_listener():
    from app.agent import build_context
    from app.mediator import Negotiation
    ctx = build_context(Negotiation(), "vatsa", "bhaade pandar hajaar")
    assert "Vatsa" in ctx and "Sreedev" in ctx
    assert "Gujarati" in ctx and "Malayalam" in ctx
    assert "bhaade pandar hajaar" in ctx


def test_context_includes_prior_turns_with_attribution():
    from app.agent import build_context
    from app.mediator import Negotiation, Turn
    n = Negotiation()
    n.turns.append(Turn(idx=0, party="sreedev", lang="ml-IN",
                        transcript="pathinanju sari", relay_text="", interjection=None))
    ctx = build_context(n, "vatsa", "haan")
    assert "pathinanju sari" in ctx
    assert "Sreedev" in ctx
```

- [ ] **Step 2: Run to verify they fail**

Run: `python test_mediator.py` then `python test_agent.py`
Expected: FAIL — `AttributeError: 'Negotiation' object has no attribute 'transcript_history'` and
`ImportError: cannot import name 'build_context'`

- [ ] **Step 3: Implement**

Add to `Negotiation` in `app/mediator.py`:

```python
    def transcript_history(self, limit: int = 6) -> str:
        """Recent turns, each attributed to a named speaker.

        Attribution is the point: the agent must never credit one party with the
        other's words, because that becomes a wrong clause in a signed document.
        """
        from . import sarvam
        recent = self.turns[-limit:] if limit else self.turns
        lines = []
        for t in recent:
            cfg = sarvam.PARTIES.get(t.party, {})
            who = cfg.get("name", t.party)
            lang = cfg.get("label", t.lang)
            lines.append(f"[turn {t.idx}] {who} ({lang}): {t.transcript}")
        return "\n".join(lines) or "(no prior turns - this is the first)"
```

Add above `run_turn` in `app/agent.py`:

```python
def build_context(neg: Negotiation, party: str, transcript: str) -> str:
    """Everything the agent needs to attribute this utterance correctly."""
    from . import sarvam
    me = sarvam.PARTIES[party]
    other_id = next(p for p in sarvam.PARTIES if p != party)
    other = sarvam.PARTIES[other_id]

    sheet = "\n".join(
        f"- {t.key}: {t.state.value}" + (f" = {t.agreed_value}" if t.agreed_value else "")
        for t in neg.terms.values() if t.state is not TermState.OPEN
    ) or "(nothing discussed yet)"

    return (
        f"PARTIES\n"
        f"- {me['name']} speaks {me['label']}\n"
        f"- {other['name']} speaks {other['label']}\n\n"
        f"SPEAKING NOW: {me['name']} ({me['label']}). "
        f"Everything in THIS UTTERANCE is {me['name']}'s words, nobody else's.\n\n"
        f"CONVERSATION SO FAR\n{neg.transcript_history()}\n\n"
        f"TERM SHEET\n{sheet}\n\n"
        f"THIS UTTERANCE ({me['name']}):\n{transcript}"
    )
```

`run_turn` then passes `build_context(neg, party, transcript)` as its `user` argument instead of
assembling the string inline.

Append to `SYSTEM` in `app/agent.py`:

```
ATTRIBUTION IS NOT OPTIONAL. The utterance you are given belongs to the speaker named in
SPEAKING NOW. Never record a stance on behalf of the other party. If the speaker refers to
what the other party said earlier, that is context for interpreting THEIR words - it is not
a new statement by the other party. A speaker cannot accept their own proposal; if they
restate their own position, that is `propose`, not `accept`.
```

- [ ] **Step 4: Run tests**

Run: `python test_mediator.py && python test_agent.py`
Expected: both PASS

- [ ] **Step 5: Commit**

```bash
git add app/mediator.py app/agent.py test_mediator.py test_agent.py
git commit -m "feat: attributed turn history so the agent never mixes up speakers"
```

---

### Task 14: Preflight and suite runner for both providers

**Files:**
- Create: `run_tests.py`
- Modify: `verify.py` (add an OpenAI check)

- [ ] **Step 1: Write `run_tests.py`**

```python
# run_tests.py
"""Every suite, one command. Exits non-zero if anything fails."""
import subprocess
import sys

SUITES = ["test_config.py", "test_translate.py", "test_mediator.py", "test_agent.py",
          "test_session.py", "test_stt_stream.py", "test_replay.py", "test_llm.py"]

failed = []
for s in SUITES:
    print(f"\n=== {s} ===")
    if subprocess.run([sys.executable, s]).returncode != 0:
        failed.append(s)

print("\n" + ("ALL GREEN" if not failed else f"FAILED: {', '.join(failed)}"))
sys.exit(1 if failed else 0)
```

- [ ] **Step 2: Add the OpenAI preflight to `verify.py`**

Add a check that `OPENAI_API_KEY` is set and that a `gpt-4o-mini` tool call round-trips, printing
latency. **Never print a key value.** Keep every existing Sarvam check unchanged.

- [ ] **Step 3: Run**

Run: `python run_tests.py`
Expected: `ALL GREEN`

- [ ] **Step 4: Commit**

```bash
git add run_tests.py verify.py
git commit -m "feat: suite runner and dual-provider preflight"
```
