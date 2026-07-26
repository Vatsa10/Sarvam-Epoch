"""The one check that matters: does the state machine catch a false agreement?

    python test_mediator.py

No pytest, no fixtures. If this passes, the scored core is sound and any remaining
bug is in the API plumbing - which verify.py covers.
"""
from app.mediator import Negotiation, TermState


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


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  [OK] {name}")
            passed += 1
    print(f"\n{passed} passed - scored core is sound.\n")
