import json
import pathlib
import tempfile

from app import session
from app.mediator import Negotiation, TermState


def _sample() -> Negotiation:
    neg = Negotiation("demo")
    neg.apply("vatsa", "gu-IN", [{"term": "rent", "value": "15000",
              "verbatim": "pandar hajaar", "stance": "propose"}], 0)
    neg.apply("sreedev", "ml-IN", [{"term": "rent", "value": "15000",
              "verbatim": "pathinanju", "stance": "accept"}], 1)
    neg.apply("vatsa", "gu-IN", [{"term": "maintenance", "value": "actual",
              "verbatim": "alag", "stance": "propose"}], 2)
    neg.apply("sreedev", "ml-IN", [{"term": "maintenance", "value": "fixed 500",
              "verbatim": "500", "stance": "accept"}], 3)
    return neg


def test_roundtrip_preserves_states():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "s.json"
        session.save(_sample(), p)
        back = session.load(p, "demo")
    assert back.terms["rent"].state is TermState.AGREED
    assert back.terms["rent"].agreed_value == "15000"
    assert back.terms["maintenance"].state is TermState.DIVERGED


def test_roundtrip_preserves_verbatim_provenance():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "s.json"
        session.save(_sample(), p)
        back = session.load(p, "demo")
    quotes = [pr.verbatim for pr in back.terms["rent"].proposals]
    assert quotes == ["pandar hajaar", "pathinanju"]


def test_load_missing_file_returns_empty_negotiation():
    with tempfile.TemporaryDirectory() as d:
        back = session.load(pathlib.Path(d) / "nope.json", "fresh")
    assert back.session_id == "fresh"
    assert all(t.state is TermState.OPEN for t in back.terms.values())


def test_load_corrupt_file_returns_empty_negotiation():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        back = session.load(p, "fresh")
    assert all(t.state is TermState.OPEN for t in back.terms.values())


def test_load_wrong_shape_terms_returns_empty():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "wrong.json"
        p.write_text('{"terms": "not a list", "turns": []}', encoding="utf-8")
        back = session.load(p, "fresh")
    assert back.session_id == "fresh"
    assert all(t.state is TermState.OPEN for t in back.terms.values())


def test_load_malformed_proposal_is_skipped():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "bad_proposal.json"
        # Proposal missing required "party" field
        data = {
            "session_id": "demo",
            "terms": [
                {
                    "key": "rent",
                    "description": "Monthly rent amount",
                    "state": "PROPOSED",
                    "proposals": [{"value": "15000", "verbatim": "test"}],
                    "agreed_value": None,
                    "divergence_note": None
                }
            ],
            "turns": []
        }
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        back = session.load(p, "fresh")
    # Should not raise and should return a valid Negotiation
    assert back.session_id == "fresh"
    assert back.terms["rent"].proposals == []


def test_load_malformed_turn_does_not_raise():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "bad_turn.json"
        data = {"terms": [], "turns": [{"idx": 0}]}
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        back = session.load(p, "fresh")
    # Should not raise
    assert back.session_id == "fresh"
    assert back.turns == []


def test_roundtrip_preserves_native_script():
    neg = Negotiation("demo")
    neg.apply("vatsa", "gu-IN", [{"term": "rent", "value": "15000",
              "verbatim": "પંદર હજાર", "stance": "propose"}], 0)
    neg.apply("sreedev", "ml-IN", [{"term": "rent", "value": "15000",
              "verbatim": "പതിനഞ്ച്", "stance": "accept"}], 1)

    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "native.json"
        session.save(neg, p)
        back = session.load(p, "demo")

    quotes = [pr.verbatim for pr in back.terms["rent"].proposals]
    assert quotes == ["પંદર હજાર", "പതിനഞ്ച്"]


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f(); print(f"  [OK] {n}")
    print("\nsession sound\n")
