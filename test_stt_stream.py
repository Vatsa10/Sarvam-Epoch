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


def test_send_error_signal_does_not_misfire_as_turn_end():
    """A bare 'END' substring check would misfire on SEND_ERROR - it must not
    classify as turn_end."""
    kind, _ = classify({"type": "events", "data": {"signal_type": "SEND_ERROR"}})
    assert kind != "turn_end"


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
