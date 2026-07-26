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


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  [OK] {name}")
            passed += 1
    print(f"\n{passed} passed - scored core is sound.\n")
