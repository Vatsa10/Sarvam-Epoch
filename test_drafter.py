"""Plain-assert tests for app/drafter.py PARKED handling. No pytest, no live API calls."""
import asyncio

from app import drafter
from app.mediator import Negotiation, Proposal, TermState


def _neg_with(state, key="painting", attempts=2):
    neg = Negotiation(session_id="test")
    t = neg.terms[key]
    t.state = state
    t.attempts = attempts
    t.proposals = [Proposal("p1", "tenant", "tenant will paint", "en-IN", "propose", 0),
                   Proposal("p2", "owner", "no I will paint", "hi-IN", "accept", 1)]
    if state is TermState.AGREED:
        t.agreed_value = "tenant"
    return neg


async def _no_llm(*a, **kw):
    # a settled AGREED term must still draft without hitting the live API
    return {"content": '{"clauses":[{"term":"painting","text":"Tenant repaints at exit."}],"notes":""}'}


def test_parked_term_in_open_questions_with_attempts(monkeypatch):
    neg = _neg_with(TermState.PARKED, attempts=3)
    out = asyncio.run(drafter.draft(neg, "en-IN", []))
    assert any("painting" in q["reason"] and "3" in q["reason"]
               for q in out.open_questions), out.open_questions


def test_parked_term_produces_no_clause():
    neg = _neg_with(TermState.PARKED, attempts=3)
    out = asyncio.run(drafter.draft(neg, "en-IN", []))
    assert not any(c["term"] == "painting" for c in out.clauses), out.clauses
    assert not out.ready


def test_agreed_term_still_produces_clause(monkeypatch):
    monkeypatch.setattr(drafter.llm, "complete_with_tools", _no_llm)
    neg = _neg_with(TermState.AGREED)
    out = asyncio.run(drafter.draft(neg, "en-IN", []))
    assert any(c["term"] == "painting" for c in out.clauses), out.clauses


def test_quotes_survive_untranslated_when_lawyer_lang_not_en(monkeypatch):
    async def _fake_translate(text, target, source, mode="formal"):
        return f"TRANSLATED[{text}]"

    monkeypatch.setattr(drafter.sarvam, "translate", _fake_translate)
    neg = _neg_with(TermState.PARKED, attempts=1)
    out = asyncio.run(drafter.draft(neg, "hi-IN", []))
    q = next(q for q in out.open_questions if "TRANSLATED" in q["reason"])
    assert any("tenant will paint" in quote for quote in q["quotes"]), q["quotes"]
    assert any("no I will paint" in quote for quote in q["quotes"]), q["quotes"]
    assert not any("TRANSLATED" in quote for quote in q["quotes"])


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        try:
            import inspect
            if "monkeypatch" in inspect.signature(t).parameters:
                import types
                class MP:
                    def setattr(self, obj, name, value):
                        setattr(obj, name, value)
                t(MP())
            else:
                t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            print(f"FAIL {t.__name__}: {e}")
            raise SystemExit(1)
    print("ALL GREEN")
