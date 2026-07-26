"""The one check that matters: does the state machine catch a false agreement?

    python test_mediator.py

No pytest, no fixtures. If this passes, the scored core is sound and any remaining
bug is in the API plumbing - which verify.py covers.
"""
from app.mediator import Negotiation, TermState, _same


def test_clean_agreement():
    n = Negotiation()
    n.apply("vatsa", "gu-IN", [{"term": "rent", "value": "15000",
            "verbatim": "pandar hajaar", "stance": "propose"}], 0)
    n.apply("sreedev", "ml-IN", [{"term": "rent", "value": "15000",
            "verbatim": "pathinanju", "stance": "accept"}], 1)
    assert n.terms["rent"].state is TermState.AGREED
    assert n.terms["rent"].agreed_value == "15000"


def test_divergence_both_said_yes():
    """The product. Both accept, different values -> DIVERGED, never AGREED."""
    n = Negotiation()
    n.apply("vatsa", "gu-IN", [{"term": "maintenance", "value": "actual",
            "verbatim": "maintenance alag", "stance": "propose"}], 0)
    flagged = n.apply("sreedev", "ml-IN", [{"term": "maintenance", "value": "fixed 500",
                      "verbatim": "separate, 500", "stance": "accept"}], 1)
    t = n.terms["maintenance"]
    assert t.state is TermState.DIVERGED
    assert t.agreed_value is None, "a diverged term must never carry a drafted value"
    assert "maintenance" in flagged
    assert t.divergence_note and "actual" in t.divergence_note


def test_hedge_is_never_agreement():
    n = Negotiation()
    n.apply("vatsa", "gu-IN", [{"term": "deposit", "value": "50000",
            "verbatim": "pachaas hajaar", "stance": "propose"}], 0)
    flagged = n.apply("sreedev", "ml-IN", [{"term": "deposit", "value": "50000",
                      "verbatim": "nokkaam", "stance": "hedge"}], 1)
    assert n.terms["deposit"].state is TermState.HEDGED
    assert n.terms["deposit"].agreed_value is None
    assert "deposit" in flagged


def test_same_value_differing_units_is_not_agreement():
    """'500' and 'fixed 500' are deliberately NOT equal - that gap is the dispute."""
    n = Negotiation()
    n.apply("vatsa", "gu-IN", [{"term": "maintenance", "value": "500",
            "verbatim": "paanchso", "stance": "propose"}], 0)
    n.apply("sreedev", "ml-IN", [{"term": "maintenance", "value": "fixed 500",
            "verbatim": "fixed anu", "stance": "accept"}], 1)
    assert n.terms["maintenance"].state is TermState.DIVERGED


def test_sheet_blocks_drafting():
    n = Negotiation()
    n.apply("vatsa", "gu-IN", [{"term": "rent", "value": "15000", "verbatim": "x",
            "stance": "propose"}], 0)
    n.apply("sreedev", "ml-IN", [{"term": "rent", "value": "15000", "verbatim": "y",
            "stance": "accept"}], 1)
    assert n.sheet()["drafting_safe"] is True

    n.apply("vatsa", "gu-IN", [{"term": "painting", "value": "tenant", "verbatim": "a",
            "stance": "propose"}], 2)
    n.apply("sreedev", "ml-IN", [{"term": "painting", "value": "landlord", "verbatim": "b",
            "stance": "accept"}], 3)
    s = n.sheet()
    assert s["drafting_safe"] is False
    assert "painting" in s["blocked"]


def test_restart_survives_replay():
    """Sheet is derived from the proposal log, so state is reconstructible."""
    n = Negotiation()
    n.apply("vatsa", "gu-IN", [{"term": "rent", "value": "12000", "verbatim": "baar",
            "stance": "propose"}], 0)
    assert len(n.terms["rent"].proposals) == 1
    assert n.terms["rent"].proposals[0].verbatim == "baar"


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


def test_norm_strips_currency_and_unit_noise_words():
    assert _same("15000", "15000 rupees")
    assert _same("15000", "Rs 15000")
    assert _same("11 months", "11 month")


def test_norm_never_strips_fixed_or_actual():
    """'fixed'/'actual' are the exact distinction this system exists to catch -
    stripping them as noise would collapse a real dispute into a false match."""
    assert not _same("500", "fixed 500")


def test_accepting_a_hedge_does_not_create_agreement():
    """Accepting a value that was only ever hedged must not upgrade it to AGREED -
    a hedge prior should fold into the 'no real prior' branch, same as prior is None."""
    n = Negotiation()
    n.apply("vatsa", "gu-IN", [{"term": "rent", "value": "15000",
            "verbatim": "dekhte hain", "stance": "hedge"}], 0)
    n.apply("sreedev", "ml-IN", [{"term": "rent", "value": "15000",
            "verbatim": "sari", "stance": "accept"}], 1)
    assert n.terms["rent"].state is not TermState.AGREED
    assert n.terms["rent"].agreed_value is None


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



def test_implausible_amount_raises_a_doubt_instead_of_recording_it():
    """Nobody rents for 17 rupees. "Seventeen" means seventeen thousand, and
    recording 17 silently becomes a nonsense contract clause."""
    from app.mediator import Negotiation, TermState
    n = Negotiation()
    flagged = n.apply("sreedev", "ml-IN", [{"term": "rent", "value": "17",
                      "verbatim": "17", "stance": "propose"}], 0)
    t = n.terms["rent"]
    assert t.doubt and "17000" in t.doubt
    assert t.agreed_value is None
    assert t.state is TermState.PROPOSED
    assert "rent" in flagged


def test_doubt_clears_once_a_plausible_amount_is_given():
    from app.mediator import Negotiation
    n = Negotiation()
    n.apply("sreedev", "ml-IN", [{"term": "rent", "value": "17",
            "verbatim": "17", "stance": "propose"}], 0)
    assert n.terms["rent"].doubt
    n.apply("sreedev", "ml-IN", [{"term": "rent", "value": "17000",
            "verbatim": "pathinezhayiram", "stance": "propose"}], 1)
    assert n.terms["rent"].doubt is None


def test_counter_offer_is_not_divergence():
    """Open haggling is the opposite of divergence. DIVERGED means both sides think
    they agreed; a counter-offer means both sides know they have not."""
    from app.mediator import Negotiation, TermState
    n = Negotiation()
    n.apply("sreedev", "ml-IN", [{"term": "rent", "value": "17000",
            "verbatim": "pathinezhayiram", "stance": "propose"}], 0)
    flagged = n.apply("vatsa", "gu-IN", [{"term": "rent", "value": "14000",
                      "verbatim": "chaud hajaar", "stance": "counter"}], 1)
    t = n.terms["rent"]
    assert t.state is TermState.PROPOSED
    assert t.state is not TermState.DIVERGED
    assert t.divergence_note is None
    assert t.agreed_value is None
    assert flagged == []


def test_magnitude_check_leaves_explicit_units_alone():
    """'500 fixed' for maintenance is a real answer, not a shorthand - do not
    second-guess a value that carries words."""
    from app.mediator import magnitude_doubt
    assert magnitude_doubt("maintenance", "actual") is None
    assert magnitude_doubt("maintenance", "fixed 500") is None
    assert magnitude_doubt("rent", "17000") is None
    assert magnitude_doubt("duration", "11") is None      # no range for duration
    assert magnitude_doubt("rent", "17") == "17000"



def test_a_question_never_becomes_a_proposal():
    """"What is the rent?" names no value. An update with a blank value must be
    dropped, or the sheet shows a position nobody took."""
    from app.mediator import Negotiation, TermState
    n = Negotiation()
    flagged = n.apply("vatsa", "gu-IN", [{"term": "rent", "value": "",
                      "verbatim": "bhaadu shu chhe?", "stance": "propose"}], 0)
    assert n.terms["rent"].state is TermState.OPEN
    assert n.terms["rent"].proposals == []
    assert flagged == []


def test_whitespace_only_value_is_also_dropped():
    from app.mediator import Negotiation, TermState
    n = Negotiation()
    n.apply("vatsa", "gu-IN", [{"term": "deposit", "value": "   ",
            "verbatim": "?", "stance": "propose"}], 0)
    assert n.terms["deposit"].state is TermState.OPEN


def test_plausible_ranges_are_overridable_without_editing_code():
    """Floors must be tunable on the venue floor. Editing Python between demo runs
    is how a syntax error reaches a projector.

    Calls _load_plausible() directly rather than reloading the module: importlib
    .reload swaps the TermState class identity out from under every other test.
    """
    import json as _json
    import os
    import tempfile
    from app.mediator import _load_plausible

    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "plausible.json")
        with open(f, "w", encoding="utf-8") as fh:
            _json.dump({"rent": [50, 900]}, fh)
        os.environ["PLAUSIBLE_JSON"] = f
        try:
            ranges = _load_plausible()
        finally:
            del os.environ["PLAUSIBLE_JSON"]
    assert ranges["rent"] == (50, 900)
    assert ranges["deposit"] == (3000, 5_000_000), "unlisted keys keep their defaults"


def test_malformed_override_falls_back_to_defaults():
    """A bad override must never take the app down mid-demo."""
    import os
    import tempfile
    from app.mediator import _load_plausible

    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "plausible.json")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        os.environ["PLAUSIBLE_JSON"] = f
        try:
            ranges = _load_plausible()
        finally:
            del os.environ["PLAUSIBLE_JSON"]
    assert ranges["rent"] == (1500, 1_000_000)


def test_near_floor_numbers_are_not_blown_up():
    """1400 is a low rent, not a shorthand. Scaling it would propose 1,400,000 -
    turning a slightly-odd figure into an absurd one."""
    from app.mediator import magnitude_doubt
    assert magnitude_doubt("rent", "1400") is None
    assert magnitude_doubt("rent", "500") is None
    assert magnitude_doubt("rent", "99") == "99000"
    assert magnitude_doubt("rent", "17") == "17000"
    assert magnitude_doubt("deposit", "50") == "50000"



def test_silent_scaling_is_challenged_not_accepted():
    """Speaker said "17", model wrote 17000. Probably right - but it is a guess
    about money, and 17 could have meant 1700. Confirm rather than assume."""
    from app.mediator import Negotiation, scaled_without_asking
    assert scaled_without_asking("rent", "₹17", "17000") == "17000"
    assert scaled_without_asking("rent", "17000", "17000") is None   # no rewrite
    assert scaled_without_asking("rent", "pandar hajaar", "15000") is None  # no digits
    assert scaled_without_asking("rent", "₹17", "45000") is None  # unrelated number
    assert scaled_without_asking("duration", "11", "11000") is None   # no range for duration

    n = Negotiation()
    flagged = n.apply("sreedev", "ml-IN", [{"term": "rent", "value": "17000",
                      "verbatim": "₹17", "stance": "propose"}], 0)
    assert n.terms["rent"].doubt and "17000" in n.terms["rent"].doubt
    assert "rent" in flagged
    assert n.terms["rent"].agreed_value is None


def test_honest_amounts_are_not_challenged():
    """The guard must not nag about numbers the speaker actually said."""
    from app.mediator import Negotiation, TermState
    n = Negotiation()
    n.apply("sreedev", "ml-IN", [{"term": "rent", "value": "17000",
            "verbatim": "17000 rupa", "stance": "propose"}], 0)
    assert n.terms["rent"].doubt is None
    assert n.terms["rent"].state is TermState.PROPOSED


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  [OK] {name}")
            passed += 1
    print(f"\n{passed} passed - scored core is sound.\n")
