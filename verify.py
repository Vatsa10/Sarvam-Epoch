"""FIRST-HOUR CHECK. Run this before writing any product code.

Proves the three Sarvam calls work from THIS account on THIS wifi, and prints the
latency you will actually get on stage. Endpoint paths are the one thing not
confirmed from the docs - if a 404 comes back, fix the URL in app/sarvam.py once and
everything downstream follows.

    python verify.py            # STT check needs fixtures/sample_gu.webm
    python verify.py --no-stt   # skip STT if you have no clip yet
"""
import asyncio
import pathlib
import sys
import time

from app import sarvam, llm
from app.agent import TOOLS

ROOT = pathlib.Path(__file__).resolve().parent
OK, BAD = "  [OK]", "  [FAIL]"


async def timed(label: str, coro):
    t0 = time.perf_counter()
    try:
        out = await coro
        print(f"{OK} {label}  ({time.perf_counter() - t0:.2f}s)")
        return out
    except Exception as e:  # noqa: BLE001 - we want the reason, whatever it is
        print(f"{BAD} {label}  ({time.perf_counter() - t0:.2f}s)\n       {type(e).__name__}: {e}")
        return None


async def main() -> int:
    print(f"\nNyayBandhan preflight\n  base={sarvam.BASE}\n  key={'SET' if sarvam.API_KEY else 'MISSING'}\n")
    if not sarvam.API_KEY:
        print("  SARVAM_API_KEY not set. Copy .env.example to .env and fill it in.\n")
        return 1

    fails = 0

    r = await timed("chat  (sarvam LLM, JSON discipline)", sarvam.chat(
        'Reply with strict JSON only: {"ok":true}', "ping"))
    if r is None:
        fails += 1
    else:
        print(f"       -> {r[:90]}")

    for pid, cfg in sarvam.PARTIES.items():
        a = await timed(f"tts   ({cfg['label']}, speaker={cfg['speaker']})", sarvam.tts(
            "Namaste. Testing one two three.", cfg["lang"], cfg["speaker"]))
        if a:
            print(f"       -> {len(a)} b64 chars")
        else:
            fails += 1
            print(f"       speaker '{cfg['speaker']}' may be invalid - try another from the docs list")

    if "--no-stt" not in sys.argv:
        clip = ROOT / "fixtures" / "sample_gu.webm"
        if clip.exists():
            out = await timed("stt   (Saaras, codemix, with_timestamps)",
                              sarvam.stt(clip.read_bytes(), clip.name))
            if out is None:
                fails += 1
            else:
                print(f"       -> transcript: {out.get('transcript', '')[:80]}")
                has_ts = bool(out.get("timestamps"))
                print(f"       -> word timestamps: {'YES' if has_ts else 'NO (hedge-timing read unavailable)'}")
        else:
            print(f"  [SKIP] stt - record a clip to {clip} first (10s of Gujarati)")

    print()
    if not llm.OPENAI_KEY:
        print(f"  [SKIP] openai - OPENAI_API_KEY not set")
    else:
        async def openai_check():
            message = await llm.complete_with_tools(
                system="You are a rental negotiation mediator. Parse the speaker's proposal.",
                user="The rent should be 15,000 rupees per month, fixed.",
                tools=TOOLS,
            )
            tool_names = []
            for tc in message.get("tool_calls") or []:
                fn = tc.get("function", {})
                tool_names.append(fn.get("name"))
            if tool_names:
                return f"tools: {', '.join(tool_names)}"
            return "no tool calls (content: " + (message.get("content", "")[:50] or "empty") + ")"

        out = await timed("openai (gpt-4o-mini, tool call)", openai_check())
        if out is None:
            fails += 1
        else:
            print(f"       -> {out}")

    print(f"\nProvider split: reasoning={llm.PROVIDER}/{llm.MODEL} | speech=Saaras + Bulbul (Sarvam)\n")
    print(f"{'ALL GREEN - start building.' if not fails else f'{fails} FAILED - fix before coding.'}\n")
    await sarvam.aclose()
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
