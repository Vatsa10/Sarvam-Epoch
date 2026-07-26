"""One stateful agent, four tools, exactly ONE sarvam-30b call per completed turn.

Fan-out was rejected deliberately: sarvam-30b is capped at 40 req/min, so a
multi-agent design stalls a live demo. Depth on one agent, not breadth.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import sarvam
from . import llm
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
            "stance": {"type": "string",
                       "enum": ["propose", "counter", "accept", "reject", "hedge"],
                       "description":
                       "counter = they reject the number on the table and name a "
                       "different one ('17 is too much, I'll give 14'). That is open "
                       "haggling, NOT agreement and NOT divergence."},
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
drafting. Call check_readiness ONLY when the parties sound like they are wrapping up,
and never instead of update_term — it is a housekeeping checklist, not a way to handle
a turn. If the speaker named a figure or took a position, update_term is mandatory,
whether or not you also check readiness.

THE GLOSS IS A MACHINE TRANSLATION AND IT MISREADS THIS DOMAIN. Every conversation you
see is about renting a home. The translator has rendered "ભાડું"/"വാടക" (rent) as
"fare", and "I am putting it up for 15000" as a house SALE. Read those as rent and as a
monthly rent offer. Trust the native text over the gloss for what was meant; trust the
gloss over the native text only for the digits of an amount.

THE RULE THAT MATTERS: when a speaker ACCEPTS, set `value` to what THEY mean, never to
what the other side proposed. Two people can both say yes and mean different things -
detecting that is your entire job. Do not smooth it over.

A hedge ("we'll see", "dekhte hain", "nokkaam", "joiye chhe") is NOT an accept. Never
upgrade a hedge.

COUNTER-OFFER vs DIVERGENCE — do not confuse these, they are opposites:
- `counter` is OPEN disagreement. The speaker rejects the number on the table and names
  a different one: "seventeen is too much, I can give you fourteen". Both sides know
  they disagree. This is ordinary haggling. Use `counter`. NEVER call flag_divergence.
- DIVERGENCE is HIDDEN disagreement. Both sides believe they agreed, but meant different
  things: one says "maintenance separate" meaning actual cost, the other agrees meaning a
  fixed 500. Nobody realises. THAT is what flag_divergence is for.
If the speaker voices any objection to the other party's number, it is `counter`.
A counter-offer needs no clarifying question — the speaker was perfectly clear. Do not
ask them to confirm a number they just stated explicitly ("fourteen thousand" needs no
"did you mean fourteen thousand?"). Asking about something unambiguous makes the system
look deaf, and you only get one question per turn — spend it where it is needed.

A QUESTION IS NOT A PROPOSAL. "What is the rent?" / "How long is the lease?" asks for
a value, it does not offer one. Call NO tool at all for a pure question — no
update_term, and NO request_clarification either. A clear question is not ambiguous;
it just needs passing to the other party. Asking "are you asking about the rent?"
puts the question to the wrong person and wastes the turn. Only call update_term when
the speaker states a position, and only call request_clarification when a value
already on the table is genuinely unclear.

AMOUNTS AND MAGNITUDE. People state rent in shorthand: "seventeen" means seventeen
thousand, not seventeen rupees. If a stated amount would be absurd taken literally — a
monthly rent of 17, a deposit of 50 — do NOT record it. Call request_clarification and
ask which magnitude they mean ("seventeen thousand, or seventeen hundred?"). Recording a
nonsense number is worse than asking, because it silently becomes a contract clause.
This applies ONLY when the speaker actually said a number. If they asked what the rent
is and named no figure, there is no magnitude to resolve — asking "did you mean
seventeen thousand or seventeen hundred?" about a number nobody said is nonsense to
both people on the call. Never invent a figure to then ask about.

THEN WRITE THE RELAY. This sentence is SPOKEN ALOUD to the other party, translated into
their language — it is not a log entry. Address them directly as "you" and pass on what
was said, first person where natural. Keep every number exactly.
  Speaker says "હેલો હેલો હાય"  ->  "Vatsa says hello."
  Speaker says rent is 15000    ->  "Vatsa says the rent will be 15,000 a month."
Never describe the utterance from outside it. "Vatsa greeted the other party" is a
report about a conversation; the listener is IN the conversation and needs the thing
itself. Never say "the other party" — that is the person you are speaking to.

ATTRIBUTION IS NOT OPTIONAL. The utterance you are given belongs to the speaker named in
SPEAKING NOW. Never record a stance on behalf of the other party. If the speaker refers to
what the other party said earlier, that is context for interpreting THEIR words - it is not
a new statement by the other party. A speaker cannot accept their own proposal; if they
restate their own position, that is `propose`, not `accept`."""


def build_context(neg: Negotiation, party: str, transcript: str,
                  gloss: str | None = None, preamble: str = "",
                  parties: dict | None = None) -> str:
    """Everything the agent needs to attribute this utterance correctly.

    `gloss` is an English translation of the same utterance, and it is not a nicety.
    Measured on real Bulbul-synthesised speech, gpt-4o-mini misreads Indic numerals:
    it turned Malayalam "പതിനയ്യായിരം" (15000) into 17000 and "അഞ്ഞൂറ്" (500) into 800,
    inventing divergences between two parties who had actually agreed. A false
    DIVERGED is as damaging on stage as a missed one. So the model reads AMOUNTS off
    the English gloss and quotes VERBATIM from the native script.
    """
    from . import sarvam
    # A room keys its participants "p1"/"p2"; the standalone demo uses
    # sarvam.PARTIES. Indexing the global blindly raised KeyError('p1') on the
    # first real meet turn and surfaced to both users as "agent failed: 'p1'".
    # Callers that know the real participants pass them in; everyone else falls
    # back to the demo pair, and failing that to the ids themselves.
    table = parties or sarvam.PARTIES
    other_id = neg._other_party(party)

    def _who(pid: str) -> dict:
        info = table.get(pid) or sarvam.PARTIES.get(pid) or {}
        return {"name": info.get("name", pid),
                "label": info.get("label", info.get("lang", "their language"))}

    me, other = _who(party), _who(other_id)

    sheet = "\n".join(
        f"- {t.key}: {t.state.value}" + (f" = {t.agreed_value}" if t.agreed_value else "")
        for t in neg.terms.values() if t.state is not TermState.OPEN
    ) or "(nothing discussed yet)"

    gloss_block = (
        f"\n\nENGLISH GLOSS of the same utterance (authoritative for NUMBERS and "
        f"AMOUNTS - the native text above is authoritative for `verbatim`):\n{gloss}"
        if gloss else ""
    )

    return (
        f"PARTIES\n"
        f"- {me['name']} speaks {me['label']}\n"
        f"- {other['name']} speaks {other['label']}\n\n"
        f"SPEAKING NOW: {me['name']} ({me['label']}). "
        f"Everything below is {me['name']}'s words, nobody else's.\n\n"
        f"CONVERSATION SO FAR\n{neg.transcript_history()}\n\n"
        f"TERM SHEET\n{sheet}\n\n"
        f"THIS UTTERANCE ({me['name']}, {me['label']}):\n{transcript}"
        f"{gloss_block}"
    )


@dataclass
class TurnResult:
    updates: list[dict] = field(default_factory=list)
    flagged: list[str] = field(default_factory=list)
    clarification: str | None = None
    summary: str = ""
    # Separate from `summary` ON PURPOSE. summary is the relay - the thing the other
    # party actually hears. check_readiness used to write its checklist into summary
    # and destroyed the relay outright: measured on a live call, three turns in eight
    # reached the listener as "Still undiscussed: deposit, duration, ..." instead of
    # what the speaker had just said, including a 13,000 counter-offer that was
    # simply never heard. A housekeeping note must never displace speech.
    readiness: str = ""


def apply_tool_calls(neg: Negotiation, party: str, lang: str,
                     calls: list[dict], turn_idx: int,
                     gloss: str = "") -> TurnResult:
    """Dispatch parsed tool calls into negotiation state.

    Pure and synchronous, which is why it is fully testable without a network.
    """
    res = TurnResult()
    updates: list[dict] = []
    flags: list[tuple[str, str]] = []

    for call in calls:
        name = call.get("name")
        args = call.get("arguments") or {}

        if name == "update_term":
            updates.append(args)
        elif name == "flag_divergence":
            flags.append((args.get("term"), args.get("note", "")))
        elif name == "request_clarification":
            res.clarification = args.get("question")
        elif name == "check_readiness":
            undiscussed = [k for k, t in neg.terms.items() if t.state is TermState.OPEN]
            res.readiness = "Still undiscussed: " + (", ".join(undiscussed) or "nothing")
        # unknown tool names are ignored on purpose - a hallucinated call must not
        # crash a live turn

    # NOTE: do not "drop values whose digits appear in neither the verbatim nor the
    # gloss" as an invented-number guard. That was tried and it deletes the normal
    # case: people SAY numbers as words, so verbatim "pandar hajaar" legitimately
    # backs value "15000" with no digit in sight. The question-with-no-figure case
    # it was meant to catch is handled in the prompt instead.
    if updates:
        res.flagged.extend(neg.apply(party, lang, updates, turn_idx))
        res.updates = updates

    # A counter-offer can never be a divergence, and the prompt saying so is not
    # enough - observed live, the model countered 14000 against 17000 AND called
    # flag_divergence on the same turn, and since flags apply last it overwrote
    # PROPOSED back to DIVERGED. Divergence means both sides think they agreed;
    # someone who just said "that is too much" plainly does not. Enforce it here,
    # where a prompt cannot be ignored.
    countered = {u.get("term") for u in updates if u.get("stance") == "counter"}

    for key, note in flags:
        # Two ways the model gets this wrong, both seen live: it labels the
        # counter-offer `counter` and flags it anyway, or it labels it `accept` with
        # a different number and flags that. is_open_haggle catches the second by
        # looking at the figures themselves rather than at the label.
        # Three ways the model gets this wrong, all seen live: it flags a term it
        # labelled `counter`; it labels a counter `accept` with a different number
        # and flags that; or it flags a term where both parties said the SAME
        # value. The last is the worst - the record plainly shows agreement.
        if (key in countered or neg.values_match(key)
                or neg.is_open_haggle(key)):
            continue
        t = neg.terms.get(key)
        if t is not None and t.proposals:
            # Defensive, not authoritative: only escalate a term that already
            # carries provenance (proposals from at least one party). A term
            # with no proposals has nothing to diverge between, and setting
            # DIVERGED here would render with an empty quote list - the
            # demo's key evidence, gone.
            t.state = TermState.DIVERGED
            t.agreed_value = None
            t.divergence_note = note
            res.flagged.append(key)

    # dedupe, preserve order
    # Last line of defence on the core mechanic: if the model recorded this
    # speaker's value as identical to the other side's while their own words carry a
    # different number, it copied instead of listening - and a real divergence just
    # became a false agreement. Raise it as a doubt rather than silently trusting it.
    for u in updates:
        key = u.get("term")
        if key and neg.copied_the_other_side(key, party, gloss):
            term = neg.terms[key]
            term.state = TermState.PROPOSED
            term.agreed_value = None
            term.doubt = (
                f"{party}'s own words for {key} name a different amount than the "
                f"value recorded for them. Confirm what {party} actually meant."
            )
            res.flagged.append(key)

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
                   transcript: str, turn_idx: int,
                   gloss: str | None = None, preamble: str = "",
                   parties: dict | None = None) -> TurnResult:
    """ONE model call per completed turn. Never called on a partial.

    Pass `gloss` (an English translation of `transcript`) whenever you can afford the
    extra /translate call - without it the model guesses at Indic numerals and
    fabricates divergences. See build_context for the measured failure.
    """
    if gloss is None:
        gloss = await _gloss(transcript, lang)

    message = await llm.complete_with_tools(
        system=SYSTEM,
        user=build_context(neg, party, transcript, gloss, preamble, parties),
        tools=TOOLS,
    )
    res = apply_tool_calls(neg, party, lang, _parse_tool_calls(message),
                           turn_idx, gloss=gloss or "")
    if not res.summary:
        # Prefer the English gloss over echoing native script back at the operator.
        res.summary = (message.get("content") or gloss or transcript).strip()
    return res


async def _gloss(transcript: str, lang: str) -> str:
    """English gloss via /translate - its own 60/min bucket, so this does not eat
    the reasoning budget. Returns "" on failure; the turn still runs, just blind
    to numerals."""
    from . import sarvam
    try:
        return await sarvam.translate(transcript, "en-IN", lang)
    except Exception:  # noqa: BLE001
        return ""
