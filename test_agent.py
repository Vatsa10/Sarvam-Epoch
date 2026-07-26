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


def test_flag_divergence_tool_branch():
    """Divergence needs values that are describable the same way while meaning
    different things.

    This test used to use rent 15000 vs 12000, which was wrong: two bare, different
    numbers are OPEN haggling that both sides can see, and is_open_haggle now
    correctly refuses to call that a divergence. "actual" vs "fixed 500" is the real
    shape - both parties said "separate" and each heard something else.
    """
    neg = Negotiation()
    apply_tool_calls(neg, "vatsa", "gu-IN", [{"name": "update_term", "arguments": {
        "term": "maintenance", "value": "actual", "verbatim": "alag", "stance": "propose"}}], 0)
    apply_tool_calls(neg, "sreedev", "ml-IN", [{"name": "update_term", "arguments": {
        "term": "maintenance", "value": "fixed 500", "verbatim": "500 rupa", "stance": "propose"}}], 1)
    res = apply_tool_calls(neg, "vatsa", "gu-IN", [{"name": "flag_divergence", "arguments": {
        "term": "maintenance", "note": "actual vs a fixed 500"}}], 2)
    assert neg.terms["maintenance"].state is TermState.DIVERGED
    assert neg.terms["maintenance"].agreed_value is None
    assert neg.terms["maintenance"].divergence_note == "actual vs a fixed 500"
    assert "maintenance" in res.flagged


def test_flag_divergence_unknown_term_is_ignored():
    neg = Negotiation()
    res = apply_tool_calls(neg, "vatsa", "gu-IN", [{"name": "flag_divergence", "arguments": {
        "term": "not_a_real_term", "note": "whatever"}}], 0)
    assert res.flagged == []


def test_flag_divergence_needs_existing_proposals():
    neg = Negotiation()
    res = apply_tool_calls(neg, "vatsa", "gu-IN", [{"name": "flag_divergence", "arguments": {
        "term": "rent", "note": "premature"}}], 0)
    assert neg.terms["rent"].state is TermState.OPEN
    assert "rent" not in res.flagged


def test_flag_divergence_wins_over_update_term_in_same_turn():
    """update_term is buffered and applied via neg.apply() AFTER the loop, while
    flag_divergence used to mutate state inline - letting a same-turn update_term
    silently overwrite the flag. flag_divergence must always win."""
    neg = Negotiation()
    apply_tool_calls(neg, "vatsa", "gu-IN", [{"name": "update_term", "arguments": {
        "term": "rent", "value": "15000", "verbatim": "pandar hajaar", "stance": "propose"}}], 0)
    res = apply_tool_calls(neg, "sreedev", "ml-IN", [
        {"name": "update_term", "arguments": {
            "term": "rent", "value": "15000", "verbatim": "pathinanju sari", "stance": "accept"}},
        {"name": "flag_divergence", "arguments": {
            "term": "rent", "note": "actually meant different values"}},
    ], 1)
    assert neg.terms["rent"].state is TermState.DIVERGED
    assert neg.terms["rent"].agreed_value is None
    assert "rent" in res.flagged


def test_unknown_tool_is_ignored_not_crashed():
    neg = Negotiation()
    res = apply_tool_calls(neg, "vatsa", "gu-IN", [{"name": "nonexistent", "arguments": {}}], 0)
    assert res.flagged == []


def test_context_names_the_current_speaker_and_the_listener():
    from app.agent import build_context
    from app.mediator import Negotiation
    ctx = build_context(Negotiation(), "vatsa", "bhaade pandar hajaar")
    assert "Vatsa" in ctx and "Sreedev" in ctx
    assert "Gujarati" in ctx and "Malayalam" in ctx
    assert "bhaade pandar hajaar" in ctx


def test_context_includes_prior_turns_with_attribution():
    from app.agent import build_context
    from app.mediator import Negotiation, Turn
    n = Negotiation()
    n.turns.append(Turn(idx=0, party="sreedev", lang="ml-IN",
                        transcript="pathinanju sari", relay_text="", interjection=None))
    ctx = build_context(n, "vatsa", "haan")
    assert "pathinanju sari" in ctx
    assert "Sreedev" in ctx


def test_context_marks_the_speaker_unambiguously():
    """The prompt must state whose words these are, in a way that cannot be read
    as the other party speaking."""
    from app.agent import build_context
    from app.mediator import Negotiation
    ctx = build_context(Negotiation(), "sreedev", "pathinanju sari")
    speaking = ctx.split("SPEAKING NOW:")[1].split("\n")[0]
    assert "Sreedev" in speaking
    assert "Vatsa" not in speaking


def test_context_history_does_not_leak_into_the_current_utterance_block():
    """Prior turns and the current utterance must be visibly separate sections, or
    the model can read old words as newly spoken."""
    from app.agent import build_context
    from app.mediator import Negotiation, Turn
    n = Negotiation()
    n.turns.append(Turn(idx=0, party="sreedev", lang="ml-IN",
                        transcript="OLDWORDS", relay_text="", interjection=None))
    ctx = build_context(n, "vatsa", "NEWWORDS")
    tail = ctx.split("THIS UTTERANCE")[1]
    assert "NEWWORDS" in tail
    assert "OLDWORDS" not in tail



def test_flag_divergence_is_ignored_on_a_counter_offer():
    """Observed live: the model countered 14000 against 17000 and ALSO called
    flag_divergence in the same turn. Flags apply last, so it overwrote PROPOSED
    back to DIVERGED. A counter-offer is open disagreement - the opposite of
    divergence - so the flag must be dropped in code, not just discouraged in the
    prompt."""
    from app.agent import apply_tool_calls
    from app.mediator import Negotiation, TermState
    neg = Negotiation()
    apply_tool_calls(neg, "sreedev", "ml-IN", [{"name": "update_term", "arguments": {
        "term": "rent", "value": "17000", "verbatim": "pathinezhayiram",
        "stance": "propose"}}], 0)
    res = apply_tool_calls(neg, "vatsa", "gu-IN", [
        {"name": "update_term", "arguments": {
            "term": "rent", "value": "14000", "verbatim": "chaud hajaar",
            "stance": "counter"}},
        {"name": "flag_divergence", "arguments": {
            "term": "rent", "note": "they disagree on rent"}},
    ], 1)
    t = neg.terms["rent"]
    assert t.state is TermState.PROPOSED, f"counter must not become {t.state}"
    assert t.divergence_note is None
    assert "rent" not in res.flagged


def test_flag_divergence_still_works_when_there_was_no_counter():
    """The guard must not disarm real divergence detection."""
    from app.agent import apply_tool_calls
    from app.mediator import Negotiation, TermState
    neg = Negotiation()
    apply_tool_calls(neg, "vatsa", "gu-IN", [{"name": "update_term", "arguments": {
        "term": "maintenance", "value": "actual", "verbatim": "alag",
        "stance": "propose"}}], 0)
    res = apply_tool_calls(neg, "sreedev", "ml-IN", [
        {"name": "update_term", "arguments": {
            "term": "maintenance", "value": "fixed 500", "verbatim": "500",
            "stance": "accept"}},
        {"name": "flag_divergence", "arguments": {
            "term": "maintenance", "note": "actual vs fixed 500"}},
    ], 1)
    assert neg.terms["maintenance"].state is TermState.DIVERGED
    assert "maintenance" in res.flagged



def test_flag_divergence_ignored_when_figures_are_openly_different():
    """The model labelled the counter-offer `accept` (not `counter`) AND called
    flag_divergence. The stance-based guard misses that; the figures do not."""
    from app.agent import apply_tool_calls
    from app.mediator import Negotiation, TermState
    neg = Negotiation()
    apply_tool_calls(neg, "sreedev", "en-IN", [{"name": "update_term", "arguments": {
        "term": "rent", "value": "17000", "verbatim": "seventeen thousand",
        "stance": "propose"}}], 0)
    res = apply_tool_calls(neg, "vatsa", "gu-IN", [
        {"name": "update_term", "arguments": {
            "term": "rent", "value": "14000", "verbatim": "chaud hajaar",
            "stance": "accept"}},
        {"name": "flag_divergence", "arguments": {
            "term": "rent", "note": "Vatsa accepts but means fourteen thousand"}},
    ], 1)
    t = neg.terms["rent"]
    assert t.state is TermState.PROPOSED, f"open haggling must not be {t.state}"
    assert t.divergence_note is None
    assert "rent" not in res.flagged


def test_is_open_haggle_only_fires_on_two_bare_numbers():
    from app.mediator import Negotiation
    n = Negotiation()
    assert not n.is_open_haggle("rent"), "no proposals yet"
    n.apply("vatsa", "gu-IN", [{"term": "maintenance", "value": "actual",
            "verbatim": "alag", "stance": "propose"}], 0)
    n.apply("sreedev", "ml-IN", [{"term": "maintenance", "value": "fixed 500",
            "verbatim": "500", "stance": "accept"}], 1)
    assert not n.is_open_haggle("maintenance"), "descriptive mismatch is divergence"
    n.apply("vatsa", "gu-IN", [{"term": "deposit", "value": "50000",
            "verbatim": "50k", "stance": "propose"}], 2)
    n.apply("sreedev", "ml-IN", [{"term": "deposit", "value": "50000",
            "verbatim": "50k", "stance": "accept"}], 3)
    assert not n.is_open_haggle("deposit"), "same number is agreement, not haggling"


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f(); print(f"  [OK] {n}")
    print("\nagent sound\n")
