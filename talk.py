"""Terminal harness. Speak into your mic, see it transcribed and translated.

    python talk.py                 # batch: record -> Saaras -> translate
    python talk.py --listen        # full loop: it understands in context and SPEAKS BACK
    python talk.py --stream        # live: partial transcripts + VAD signals
    python talk.py --mediate       # full pipeline: also run the agent, print the term sheet
    python talk.py --list-devices  # find your mic if the default is wrong

Pick each side's language for --listen, either at the prompt or up front:

    python talk.py --listen --lang1 gu-IN --lang2 ml-IN
    python talk.py --listen --lang1 hi-IN --lang2 ta-IN --name1 Ravi --name2 Meena

This exists because no browser has run the capture path yet. It proves Saaras on
real Gujarati/Malayalam mic input, and --mediate proves the whole product without
a browser or a second person.

Uses the official `sarvamai` SDK rather than raw HTTP, so the VAD/barge-in knobs
below are the documented ones.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import sys
import wave

import numpy as np
import sounddevice as sd
from dotenv import load_dotenv

# Windows consoles default to cp1252, which cannot encode Devanagari/Gujarati/
# Malayalam and raises UnicodeEncodeError on the first transcript. Displaying
# Indic script is the entire point of this tool, so force UTF-8 before anything
# prints. Also run `chcp 65001` if your terminal still shows boxes.
if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

load_dotenv()

import os

from sarvamai import AsyncSarvamAI, SarvamAI

KEY = os.getenv("SARVAM_API_KEY", "")
RATE = 16000          # Saaras streaming wants 16k; matches the browser AudioWorklet
CHUNK = 1600          # 100ms

# Barge-in thresholds. Tune BARGE_LEVEL up in a loud room or the agent's own audio
# leaking through the speakers will interrupt itself; headphones make this moot.
BARGE_LEVEL = 0.12    # peak amplitude, 0-1
BARGE_BLOCKS = 3      # consecutive 100ms blocks above the level = real speech

# Per-message encoding on the streaming socket. The SDK's own default is
# "audio/wav" even though the connect URL declares input_audio_codec=pcm_s16le,
# so the codec appears to come from the URL and this field is metadata. Override
# with STREAM_ENCODING=... if empty transcripts suggest otherwise.
STREAM_ENCODING = os.getenv("STREAM_ENCODING", "audio/wav")

LANGS = {
    "1": ("gu-IN", "Gujarati"),
    "2": ("ml-IN", "Malayalam"),
    "3": ("hi-IN", "Hindi"),
    "4": ("en-IN", "English"),
    "5": ("ta-IN", "Tamil"),
    "6": ("mr-IN", "Marathi"),
    "7": ("kn-IN", "Kannada"),
    "8": ("bn-IN", "Bengali"),
}

# Bulbul speaker per language. Verified live: the API accepts ratan/shubh/priya/pooja
# for all 11 TTS languages, so these are quality picks (the docs recommend ratan for
# gu-IN and shubh for ml-IN), not hard constraints. Party 1 and party 2 are always
# given DIFFERENT voices - on a two-way call you need to hear who is speaking.
SPEAKERS = {
    "gu-IN": ("ratan", "priya"),
    "ml-IN": ("shubh", "pooja"),
}
DEFAULT_SPEAKERS = ("ratan", "shubh")


def speaker_for(lang: str, slot: int) -> str:
    """slot 0 = first party, slot 1 = second. Distinct voices even on same language."""
    return SPEAKERS.get(lang, DEFAULT_SPEAKERS)[slot]


# Documented VAD knobs on the streaming socket. high_vad_sensitivity is the one
# that matters in a loud room; interrupt_min_speech_frames is barge-in - how much
# speech must land before an in-progress agent turn is treated as interrupted.
VAD = {
    "high_vad_sensitivity": "true",
    "vad_signals": "true",
    "interrupt_min_speech_frames": "8",
}


def pick(prompt: str, default: str) -> tuple[str, str]:
    print(f"\n{prompt}")
    for k, (code, name) in LANGS.items():
        print(f"  {k}. {name:12} ({code})")
    choice = input(f"  choice [{default}]: ").strip() or default
    return LANGS.get(choice, LANGS[default])


def to_wav(pcm: np.ndarray) -> bytes:
    """int16 mono -> WAV bytes. The SDK's transcribe() wants a real container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def record() -> np.ndarray:
    """Record until Enter. Simple and predictable - no local VAD guesswork."""
    frames: list[np.ndarray] = []
    peak = 0.0

    def cb(indata, _frames, _time, status):
        nonlocal peak
        if status:
            print(f"  (audio status: {status})", file=sys.stderr)
        frames.append(indata.copy())
        peak = max(peak, float(np.abs(indata).max()) / 32768.0)

    with sd.InputStream(samplerate=RATE, channels=1, dtype="int16",
                        blocksize=CHUNK, callback=cb):
        input("  ● recording — press Enter to stop: ")

    if not frames:
        return np.zeros(0, dtype=np.int16)
    pcm = np.concatenate(frames).flatten()
    secs = len(pcm) / RATE
    print(f"  captured {secs:.1f}s, peak level {peak:.0%}")
    if peak < 0.02:
        print("  ⚠ almost silent — wrong mic? try --list-devices")
    return pcm


async def batch(src_code: str, src_name: str, dst_code: str, dst_name: str, mediate: bool) -> None:
    # One event loop for the whole session. app.sarvam holds a module-level
    # httpx.AsyncClient bound to the loop that first touches it, so an
    # asyncio.run() per turn would close that loop and every later call would fail.
    client = SarvamAI(api_subscription_key=KEY)

    neg = party = None
    if mediate:
        from app.mediator import Negotiation
        neg = Negotiation("terminal")
        party = "vatsa"

    print(f"\n{src_name} → {dst_name}. Ctrl+C to quit.\n")
    while True:
        try:
            pcm = record()
            if pcm.size < RATE // 4:
                print("  too short, try again\n")
                continue

            r = client.speech_to_text.transcribe(
                file=("turn.wav", to_wav(pcm), "audio/wav"),
                model="saaras:v3",
                language_code=src_code,
            )
            transcript = (getattr(r, "transcript", "") or "").strip()
            if not transcript:
                print("  ✗ empty transcript\n")
                continue
            print(f"\n  heard  ({src_name}): {transcript}")

            t = client.text.translate(
                input=transcript,
                source_language_code=src_code,
                target_language_code=dst_code,
                model="mayura:v1",
                mode="code-mixed",
            )
            print(f"  means  ({dst_name}): {getattr(t, 'translated_text', '')}")

            if mediate:
                await _mediate(neg, party, src_code, transcript,
                               getattr(t, "translated_text", "") or None)
                party = "sreedev" if party == "vatsa" else "vatsa"
                who = "Vatsa (Gujarati)" if party == "vatsa" else "Sreedev (Malayalam)"
                print(f"\n  next turn: {who}")
            print()
        except KeyboardInterrupt:
            print("\nbye\n")
            return


async def _mediate(neg, party: str, lang: str, transcript: str,
                   gloss: str | None = None) -> None:
    """Run the real agent and print the term sheet. This is the product.

    `gloss` is the English translation we already fetched for display - reusing it
    saves a call AND is what stops the model guessing at Indic numerals.
    """
    from app import agent
    res = await agent.run_turn(neg, party, lang, transcript, len(neg.turns), gloss=gloss)
    from app.mediator import Turn
    neg.turns.append(Turn(idx=len(neg.turns), party=party, lang=lang,
                          transcript=transcript, relay_text=res.summary,
                          interjection=res.clarification))

    print(f"  agent  : {res.summary}")
    if res.clarification:
        print(f"  ASKS   : {res.clarification}")
    _print_sheet(neg.sheet())


def _print_sheet(sheet: dict) -> None:
    mark = {"AGREED": "✓", "DIVERGED": "✗", "HEDGED": "~", "PROPOSED": "·",
            "REJECTED": "✗", "OPEN": " "}
    print("\n  ── term sheet ──")
    shown = False
    for t in sheet["terms"]:
        if t["state"] == "OPEN":
            continue
        shown = True
        print(f"   {mark.get(t['state'],' ')} {t['key']:14} {t['state']:9} {t['agreed_value'] or ''}")
        if t["divergence_note"]:
            print(f"       ! {t['divergence_note']}")
    if not shown:
        print("   (nothing discussed yet)")
    if sheet["blocked"]:
        print(f"   BLOCKED: {', '.join(sheet['blocked'])} — will not draft")
    elif sheet["drafting_safe"] and shown:
        print("   safe to draft")


def play(b64: str, barge_in: bool = True) -> bool:
    """Play Bulbul audio. Returns True if the listener barged in over it.

    Barge-in is not a nicety here: the whole product is two people talking, and a
    person who has heard enough will start replying before the agent stops. We watch
    the mic while playing and cut the audio the moment real speech lands.
    """
    import base64
    wav = base64.b64decode(b64)
    with wave.open(io.BytesIO(wav)) as w:
        rate, pcm = w.getframerate(), np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)

    sd.play(pcm, rate)
    if not barge_in:
        sd.wait()
        return False

    hot = 0                      # consecutive loud blocks
    interrupted = False

    def watch(indata, _f, _t, _s):
        nonlocal hot, interrupted
        if float(np.abs(indata).max()) / 32768.0 > BARGE_LEVEL:
            hot += 1
            if hot >= BARGE_BLOCKS:
                interrupted = True
        else:
            hot = 0

    with sd.InputStream(samplerate=RATE, channels=1, dtype="int16",
                        blocksize=CHUNK, callback=watch):
        while sd.get_stream().active:
            if interrupted:
                sd.stop()
                print("  ⟨barge-in — stopped speaking⟩")
                break
            sd.sleep(50)
    return interrupted


async def say(text: str, lang: str, speaker: str, client: SarvamAI) -> None:
    """Speak text aloud in `lang`. Never fatal — a silent turn beats a crash."""
    if not text.strip():
        return
    try:
        r = client.text_to_speech.convert(
            text=text[:2500], target_language_code=lang,
            model="bulbul:v3", speaker=speaker, speech_sample_rate=22050,
        )
        if r.audios:
            play(r.audios[0])
    except Exception as e:  # noqa: BLE001
        print(f"  (tts failed: {type(e).__name__})")


def configure_parties(lang1: str | None, lang2: str | None,
                      name1: str | None, name2: str | None) -> None:
    """Set each party's language for this session.

    Mutates sarvam.PARTIES in place rather than threading config through every call,
    because PARTIES is already the single source of truth every module reads - agent
    context, transcript attribution, TTS voice and the lawyer packet all key off it.
    """
    from app import sarvam as sv

    if not lang1:
        lang1 = pick("Party 1 speaks:", "1")[0]
    if not lang2:
        lang2 = pick("Party 2 speaks:", "2")[0]

    labels = {code: name for code, name in LANGS.values()}
    ids = list(sv.PARTIES)
    for slot, (pid, lang, nm) in enumerate(
            zip(ids, (lang1, lang2), (name1, name2))):
        sv.PARTIES[pid] = {
            "name": nm or sv.PARTIES[pid]["name"],
            "lang": lang,
            "label": labels.get(lang, lang),
            "speaker": speaker_for(lang, slot),
        }

    a, b = (sv.PARTIES[i] for i in ids)
    if a["lang"] == b["lang"]:
        print(f"\n  ⚠ both parties set to {a['label']} — there is nothing to mediate "
              f"across, so relays will be near-identical to the input.")
    print(f"\n  {a['name']}: {a['label']} (voice {a['speaker']})"
          f"   |   {b['name']}: {b['label']} (voice {b['speaker']})")


async def listen(mediate: bool = True) -> None:
    """Full conversational loop: you speak, it understands in context, it speaks back.

    You play both parties in turn. The agent always speaks to the LISTENER in the
    LISTENER's language — a clarifying question when the sheet just broke, the relay
    of what was said otherwise. That is exactly what happens on the real call.
    """
    client = SarvamAI(api_subscription_key=KEY)
    from app import agent, sarvam as sv
    from app.mediator import Negotiation, Turn

    neg = Negotiation("listen")
    party = "vatsa"

    print("\n  Two parties, no common language. You play both, one turn at a time.")
    print("  The agent replies aloud in the listener's language. Ctrl+C to quit.\n")

    while True:
        try:
            me = sv.PARTIES[party]
            other_id = next(p for p in sv.PARTIES if p != party)
            other = sv.PARTIES[other_id]
            print(f"── {me['name']} ({me['label']}) speaks — {other['name']} will hear it in {other['label']}")

            pcm = record()
            if pcm.size < RATE // 4:
                print("  too short\n")
                continue

            r = client.speech_to_text.transcribe(
                file=("turn.wav", to_wav(pcm), "audio/wav"),
                model="saaras:v3", language_code=me["lang"],
            )
            transcript = (getattr(r, "transcript", "") or "").strip()
            if not transcript:
                print("  ✗ empty transcript\n")
                continue
            print(f"  heard : {transcript}")

            gloss = await sv.translate(transcript, "en-IN", me["lang"])
            print(f"  gloss : {gloss}")

            res = await agent.run_turn(neg, party, me["lang"], transcript,
                                       len(neg.turns), gloss=gloss)

            # What the agent says, and WHO it says it to, is decided by what just
            # happened to the sheet.
            relay = (gloss or transcript).strip()
            doubt = next((t.doubt for t in neg.terms.values() if t.doubt), None)

            if doubt:
                # A magnitude question goes BACK TO THE SPEAKER, in the speaker's own
                # language, and the turn is NOT relayed - no point passing on a number
                # we do not believe. This is the "you said seventeen - do you mean
                # seventeen thousand?" case.
                spoken_en, target, why = (
                    res.clarification or f"{doubt} Ask which they mean.",
                    me, "asks back")
            elif res.clarification:
                spoken_en, target, why = res.clarification, other, "asks"
            elif res.flagged:
                # Relay the substance FIRST, then flag. Saying only "you have not
                # agreed" drops the actual offer, which is the one thing the listener
                # needed to hear.
                spoken_en, target, why = (
                    f"{relay} - but note, you two have not actually agreed on "
                    f"{', '.join(res.flagged)}.",
                    other, "relays + flags")
            else:
                spoken_en, target, why = relay, other, "relays"

            # formal, not code-mixed: this line gets SPOKEN, and stranded English
            # words mid-sentence sound wrong out of Bulbul.
            spoken = await sv.translate(spoken_en, target["lang"], "en-IN",
                                        mode="formal") or spoken_en

            neg.turns.append(Turn(idx=len(neg.turns), party=party, lang=me["lang"],
                                  transcript=transcript, relay_text=spoken,
                                  interjection=res.clarification))

            print(f"  {why} → {other['name']} ({other['label']}): {spoken}")
            await say(spoken, other["lang"], other["speaker"], client)

            _print_sheet(neg.sheet())
            party = other_id
            print()
        except KeyboardInterrupt:
            print("\n  final sheet:")
            _print_sheet(neg.sheet())
            await sv.aclose()
            print("\nbye\n")
            return
        except Exception as e:  # noqa: BLE001
            # One bad turn must not end the session. A crash mid-demo loses the whole
            # negotiation; a skipped turn loses ten seconds. The sheet is untouched,
            # so just hand the mic back and carry on.
            print(f"\n  ⚠ turn failed: {type(e).__name__}: {str(e)[:200]}")
            print("  sheet is unchanged — take that turn again.\n")


async def stream(src_code: str, src_name: str) -> None:
    """Live partials + VAD signals straight off the streaming socket."""
    client = AsyncSarvamAI(api_subscription_key=KEY)
    q: asyncio.Queue[bytes] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def cb(indata, _f, _t, status):
        if status:
            print(f"  (audio status: {status})", file=sys.stderr)
        loop.call_soon_threadsafe(q.put_nowait, bytes(indata))

    print(f"\n  ● streaming {src_name} — speak, Ctrl+C to stop\n")
    async with client.speech_to_text_streaming.connect(
        language_code=src_code, model="saaras:v3", mode="codemix",
        sample_rate=str(RATE), input_audio_codec="pcm_s16le", **VAD,
    ) as sock:

        async def send() -> None:
            # The SDK wants BASE64 TEXT, not raw bytes - AudioData.data is typed str,
            # and passing bytes fails pydantic validation before anything hits the wire.
            while True:
                chunk = await q.get()
                await sock.transcribe(
                    audio=base64.b64encode(chunk).decode(),
                    encoding=STREAM_ENCODING,
                    sample_rate=RATE,
                )

        async def recv() -> None:
            async for msg in sock:
                d = getattr(msg, "data", None) or {}
                text = (getattr(d, "transcript", None)
                        or (d.get("transcript") if isinstance(d, dict) else "") or "")
                kind = getattr(msg, "type", "")
                if kind == "events":
                    sig = (d.get("signal_type") if isinstance(d, dict)
                           else getattr(d, "signal_type", "")) or ""
                    print(f"  [VAD] {sig}")
                elif text.strip():
                    final = (d.get("is_final") if isinstance(d, dict)
                             else getattr(d, "is_final", False))
                    print(f"  {'▸' if final else '·'} {text.strip()}")

        with sd.InputStream(samplerate=RATE, channels=1, dtype="int16",
                            blocksize=CHUNK, callback=cb):
            await asyncio.gather(send(), recv())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", action="store_true",
                    help="full loop: you speak, it understands in context and SPEAKS BACK")
    ap.add_argument("--lang1", help="party 1 language code, e.g. gu-IN (skips the prompt)")
    ap.add_argument("--lang2", help="party 2 language code, e.g. ml-IN (skips the prompt)")
    ap.add_argument("--name1", help="party 1 display name")
    ap.add_argument("--name2", help="party 2 display name")
    ap.add_argument("--stream", action="store_true", help="live partials + VAD signals")
    ap.add_argument("--mediate", action="store_true", help="also run the agent and print the term sheet")
    ap.add_argument("--list-devices", action="store_true")
    a = ap.parse_args()

    if a.list_devices:
        print(sd.query_devices())
        return 0
    if not KEY:
        print("SARVAM_API_KEY not set — check .env")
        return 1

    if a.listen:
        configure_parties(a.lang1, a.lang2, a.name1, a.name2)
        try:
            asyncio.run(listen())
        except KeyboardInterrupt:
            print("\nbye\n")
        return 0

    src_code, src_name = pick("You speak:", "1")
    if a.stream:
        try:
            asyncio.run(stream(src_code, src_name))
        except KeyboardInterrupt:
            print("\nbye\n")
        return 0

    dst_code, dst_name = pick("Show it in:", "4")
    try:
        asyncio.run(batch(src_code, src_name, dst_code, dst_name, a.mediate))
    except KeyboardInterrupt:
        print("\nbye\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
