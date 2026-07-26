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
