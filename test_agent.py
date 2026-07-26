# test_agent.py
from app.agent import TOOLS, apply_tool_calls, TurnResult
from app.mediator import Negotiation, TermState


def test_tools_are_openai_shaped():
    names = {t["function"]["name"] for t in TOOLS}
    assert names == {"update_term", "flag_divergence", "request_clarification", "check_readiness"}
    for t in TOOLS:
        assert t["type"] == "function"
        assert "parameters" in t["function"]


def test_update_term_tool_records_proposal():
    neg = Negotiation()
    calls = [{"name": "update_term", "arguments": {
        "term": "rent", "value": "15000", "verbatim": "pandar hajaar", "stance": "propose"}}]
    res = apply_tool_calls(neg, "vatsa", "gu-IN", calls, 0)
    assert isinstance(res, TurnResult)
    assert neg.terms["rent"].state is TermState.PROPOSED
    assert neg.terms["rent"].proposals[0].verbatim == "pandar hajaar"


def test_divergence_is_detected_through_the_tool_path():
    neg = Negotiation()
    apply_tool_calls(neg, "vatsa", "gu-IN", [{"name": "update_term", "arguments": {
        "term": "maintenance", "value": "actual", "verbatim": "alag", "stance": "propose"}}], 0)
    res = apply_tool_calls(neg, "sreedev", "ml-IN", [{"name": "update_term", "arguments": {
        "term": "maintenance", "value": "fixed 500", "verbatim": "500 fixed", "stance": "accept"}}], 1)
    assert neg.terms["maintenance"].state is TermState.DIVERGED
    assert "maintenance" in res.flagged
    assert neg.terms["maintenance"].agreed_value is None


def test_hedge_never_becomes_agreed():
    neg = Negotiation()
    apply_tool_calls(neg, "vatsa", "gu-IN", [{"name": "update_term", "arguments": {
        "term": "deposit", "value": "50000", "verbatim": "pachaas", "stance": "propose"}}], 0)
    res = apply_tool_calls(neg, "sreedev", "ml-IN", [{"name": "update_term", "arguments": {
        "term": "deposit", "value": "50000", "verbatim": "nokkaam", "stance": "hedge"}}], 1)
    assert neg.terms["deposit"].state is TermState.HEDGED
    assert "deposit" in res.flagged


def test_request_clarification_is_surfaced():
    neg = Negotiation()
    res = apply_tool_calls(neg, "vatsa", "gu-IN", [{"name": "request_clarification", "arguments": {
        "term": "maintenance", "question": "Fixed 500 or actual cost?"}}], 0)
    assert res.clarification == "Fixed 500 or actual cost?"


def test_check_readiness_lists_undiscussed_terms():
    neg = Negotiation()
    apply_tool_calls(neg, "vatsa", "gu-IN", [{"name": "update_term", "arguments": {
        "term": "rent", "value": "15000", "verbatim": "x", "stance": "propose"}}], 0)
    res = apply_tool_calls(neg, "vatsa", "gu-IN", [{"name": "check_readiness", "arguments": {}}], 1)
    assert "rent" not in res.summary
    assert "notice_period" in res.summary


def test_unknown_tool_is_ignored_not_crashed():
    neg = Negotiation()
    res = apply_tool_calls(neg, "vatsa", "gu-IN", [{"name": "nonexistent", "arguments": {}}], 0)
    assert res.flagged == []


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f(); print(f"  [OK] {n}")
    print("\nagent sound\n")
