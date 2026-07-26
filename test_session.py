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


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f(); print(f"  [OK] {n}")
    print("\nsession sound\n")
