"""Each participant picks two languages: what they SPEAK and what they SEE/HEAR.
The relay must route on the LISTENER's output language, never the sender's -
that is the one thing easy to get backwards, so it is the one thing pinned here.

    pytest test_language_routing.py     (or: python test_language_routing.py)
"""
from app.meet_interface.languages import route
from app.meet_interface.rooms import Participant


def test_routes_to_listener_output_language_not_senders():
    # Sender speaks Hindi; listener wants to read/hear Tamil.
    target, voice = route("hi-IN", "ta-IN")
    assert target == "ta-IN"
    assert voice == "diya"          # Tamil's Bulbul voice, not Hindi's "meera"


def test_same_language_needs_no_translation_but_still_gets_a_voice():
    target, voice = route("ml-IN", "ml-IN")
    assert target is None           # verbatim - no /translate call
    assert voice == "shubh"


def test_listener_may_hear_a_third_language_neither_party_speaks():
    # Sender speaks Malayalam, listener speaks Gujarati but reads English.
    target, voice = route("ml-IN", "en-IN")
    assert (target, voice) == ("en-IN", "priya")


def test_output_language_defaults_to_spoken_language():
    p = Participant(party_id="p1", name="Vatsa", lang="gu-IN")
    assert p.out_lang == "gu-IN"
    assert route("hi-IN", p.out_lang) == ("gu-IN", "ratan")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")
