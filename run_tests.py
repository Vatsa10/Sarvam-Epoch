"""Every suite, one command. Exits non-zero if anything fails."""
import subprocess
import sys

SUITES = ["test_config.py", "test_translate.py", "test_mediator.py", "test_agent.py",
          "test_session.py", "test_stt_stream.py", "test_replay.py", "test_llm.py",
          "test_memory.py"]

failed = []
for s in SUITES:
    print(f"\n=== {s} ===")
    if subprocess.run([sys.executable, s]).returncode != 0:
        failed.append(s)

print("\n" + ("ALL GREEN" if not failed else f"FAILED: {', '.join(failed)}"))
sys.exit(1 if failed else 0)
