"""One stateful agent, four tools, exactly ONE sarvam-30b call per completed turn.

Fan-out was rejected deliberately: sarvam-30b is capped at 40 req/min, so a
multi-agent design stalls a live demo. Depth on one agent, not breadth.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import sarvam
from .mediator import Negotiation, TERMS, TermState

TOOLS = [
    {"type": "function", "function": {
        "name": "update_term",
        "description": "Record what this speaker just said about ONE negotiable term.",
        "parameters": {"type": "object", "properties": {
            "term": {"type": "string", "enum": list(TERMS)},
            "value": {"type": "string", "description":
                      "Normalised value AS THIS SPEAKER UNDERSTANDS IT. Digits for "
                      "amounts. For maintenance distinguish 'fixed 500' from 'actual'."},
            "verbatim": {"type": "string", "description":
                         "Their exact words for this term, original script, unedited."},
            "stance": {"type": "string", "enum": ["propose", "accept", "reject", "hedge"]},
        }, "required": ["term", "value", "verbatim", "stance"]}}},
    {"type": "function", "function": {
        "name": "flag_divergence",
        "description": "Both parties think they agreed but meant different values.",
        "parameters": {"type": "object", "properties": {
            "term": {"type": "string", "enum": list(TERMS)},
            "note": {"type": "string"},
        }, "required": ["term", "note"]}}},
    {"type": "function", "function": {
        "name": "request_clarification",
        "description": "Ask the ONE question that settles an ambiguity. Use sparingly.",
        "parameters": {"type": "object", "properties": {
            "term": {"type": "string", "enum": list(TERMS)},
            "question": {"type": "string"},
        }, "required": ["term", "question"]}}},
    {"type": "function", "function": {
        "name": "check_readiness",
        "description": "List which terms are still undiscussed. No arguments.",
        "parameters": {"type": "object", "properties": {}}}},
]

SYSTEM = """You are a neutral mediator on a live rental negotiation between two people
who share no common language. You hear ONE speaker's utterance at a time, in their own
language, and you maintain the term sheet.

Call update_term for every term they touched. Call flag_divergence when this speaker
accepts a term but means a different value than the other party proposed. Call
request_clarification at most once per turn, and only when a real ambiguity blocks
drafting. Call check_readiness when the parties sound like they are wrapping up.

THE RULE THAT MATTERS: when a speaker ACCEPTS, set `value` to what THEY mean, never to
what the other side proposed. Two people can both say yes and mean different things -
detecting that is your entire job. Do not smooth it over.

A hedge ("we'll see", "dekhte hain", "nokkaam", "joiye chhe") is NOT an accept. Never
upgrade a hedge.

Then write a one-sentence plain-English summary of what the speaker said."""


@dataclass
class TurnResult:
    updates: list[dict] = field(default_factory=list)
    flagged: list[str] = field(default_factory=list)
    clarification: str | None = None
    summary: str = ""


def apply_tool_calls(neg: Negotiation, party: str, lang: str,
                     calls: list[dict], turn_idx: int) -> TurnResult:
    """Dispatch parsed tool calls into negotiation state.

    Pure and synchronous, which is why it is fully testable without a network.
    """
    res = TurnResult()
    updates: list[dict] = []

    for call in calls:
        name = call.get("name")
        args = call.get("arguments") or {}

        if name == "update_term":
            updates.append(args)
        elif name == "flag_divergence":
            key = args.get("term")
            if key in neg.terms:
                t = neg.terms[key]
                t.state = TermState.DIVERGED
                t.agreed_value = None
                t.divergence_note = args.get("note", "")
                res.flagged.append(key)
        elif name == "request_clarification":
            res.clarification = args.get("question")
        elif name == "check_readiness":
            undiscussed = [k for k, t in neg.terms.items() if t.state is TermState.OPEN]
            res.summary = "Still undiscussed: " + (", ".join(undiscussed) or "nothing")
        # unknown tool names are ignored on purpose - a hallucinated call must not
        # crash a live turn

    if updates:
        res.flagged.extend(neg.apply(party, lang, updates, turn_idx))
        res.updates = updates

    # dedupe, preserve order
    res.flagged = list(dict.fromkeys(res.flagged))
    return res


def _parse_tool_calls(message: dict) -> list[dict]:
    out = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function", {})
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            args = {}
        out.append({"name": fn.get("name"), "arguments": args})
    return out


async def run_turn(neg: Negotiation, party: str, lang: str,
                   transcript: str, turn_idx: int) -> TurnResult:
    """ONE sarvam-30b call per completed turn. Never called on a partial."""
    sheet = "\n".join(
        f"- {t.key}: {t.state.value}" + (f" = {t.agreed_value}" if t.agreed_value else "")
        for t in neg.terms.values() if t.state is not TermState.OPEN
    ) or "(nothing discussed yet)"

    message = await sarvam.chat_tools(
        system=SYSTEM,
        user=f"Current term sheet:\n{sheet}\n\nSpeaker ({party}, {lang}) said:\n{transcript}",
        tools=TOOLS,
    )
    res = apply_tool_calls(neg, party, lang, _parse_tool_calls(message), turn_idx)
    if not res.summary:
        res.summary = (message.get("content") or transcript).strip()
    return res
