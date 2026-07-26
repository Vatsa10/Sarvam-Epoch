from app.stt_stream import build_ws_url, classify


def test_url_carries_required_params():
    u = build_ws_url("gu-IN")
    assert u.startswith("wss://api.sarvam.ai/speech-to-text/ws?")
    for frag in ["language-code=gu-IN", "model=saaras%3Av3", "input_audio_codec=pcm_s16le",
                 "high_vad_sensitivity=true", "vad_signals=true"]:
        assert frag in u, f"missing {frag} in {u}"


def test_data_frame_is_always_final():
    """Captured from the live socket - there is no is_final field on the wire.

    Saaras sends ONE data frame per speech segment carrying the complete
    transcript. The old test fed a hand-made {"is_final": False} shape that the
    service never produces, which is how classify() came to label every real
    transcript a partial - so the turn buffer never filled and every turn died
    as "nothing was picked up".
    """
    real = {"type": "data", "data": {
        "request_id": "20260726_de4b0b86", "transcript": "ભાડું પંદર હજાર રૂપિયા મહિને રહેશે.",
        "timestamps": None, "diarized_transcript": None, "language_code": "gu-IN",
        "language_probability": None, "audio_hash": None, "audio_mime": None,
        "metrics": {"audio_duration": 2.368, "processing_latency": 0.14}}}
    kind, text = classify(real)
    assert kind == "final", f"a data frame with a transcript must be final, got {kind}"
    assert text == "ભાડું પંદર હજાર રૂપિયા મહિને રહેશે."


def test_final_transcript():
    kind, text = classify({"type": "data", "data": {"transcript": "pandar hajaar"}})
    assert (kind, text) == ("final", "pandar hajaar")


def test_start_speech_is_not_a_turn_end():
    """START_SPEECH arrives before the transcript; treating it as turn_end would
    end the turn before a word had been said."""
    assert classify({"type": "events",
                     "data": {"signal_type": "START_SPEECH"}})[0] == "ignore"


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
    assert classify({"type": "data", "data": {"transcript": "   "}})[0] == "ignore"


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f(); print(f"  [OK] {n}")
    print("\nstt_stream sound\n")
