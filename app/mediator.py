"""The scored core.

The agent is NOT a translator. It is a mediator that tracks the state of every
negotiable term and refuses to record an agreement that isn't one.

The whole thesis lives in one place: two parties can both say "yes" to a term while
meaning different values. A relay translates both yeses faithfully and manufactures
the dispute it was meant to prevent. TermState.DIVERGED is the state no pure
translation layer can ever produce.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum

from . import sarvam


class TermState(str, Enum):
    OPEN = "OPEN"              # never discussed
    PROPOSED = "PROPOSED"      # one side named a value, other hasn't responded
    AGREED = "AGREED"          # both sides, same value -> safe to draft
    DIVERGED = "DIVERGED"      # both said yes TO DIFFERENT VALUES  <- the mechanic
    HEDGED = "HEDGED"          # "dekhte hain" / soft yes -> NOT agreement
    REJECTED = "REJECTED"


TERMS = {
    "rent":          "Monthly rent amount",
    "deposit":       "Security deposit amount",
    "duration":      "Lease duration",
    "maintenance":   "Who pays maintenance, and is it fixed or actual",
    "notice_period": "Notice period to vacate",
    "painting":      "Who bears repainting cost at exit",
}


@dataclass
class Proposal:
    party: str
    value: str
    verbatim: str          # their own words, in their own script - never translated
    lang: str
    stance: str            # propose | accept | reject | hedge
    turn: int
    ts: float = field(default_factory=time.time)


@dataclass
class Term:
    key: str
    description: str
    state: TermState = TermState.OPEN
    proposals: list[Proposal] = field(default_factory=list)
    agreed_value: str | None = None
    divergence_note: str | None = None

    def latest_by(self, party: str) -> Proposal | None:
        for p in reversed(self.proposals):
            if p.party == party:
                return p
        return None


@dataclass
class Turn:
    idx: int
    party: str
    lang: str
    transcript: str
    relay_text: str
    interjection: str | None
    ts: float = field(default_factory=time.time)


class Negotiation:
    """Append-only turn log; term sheet is DERIVED by replaying it.

    Deriving rather than mutating is what makes provenance free: every term can name
    who said what, when, in which language, and what superseded it.
    """

    def __init__(self, session_id: str = "default") -> None:
        self.session_id = session_id
        self.turns: list[Turn] = []
        self.terms: dict[str, Term] = {k: Term(k, v) for k, v in TERMS.items()}

    # ---------- state machine ----------

    def apply(self, party: str, lang: str, updates: list[dict], turn_idx: int) -> list[str]:
        """Fold one turn's extracted term-updates into the sheet.

        Returns the keys of terms that need the mediator to interject.
        """
        needs_interjection: list[str] = []

        for u in updates:
            key = u.get("term")
            if key not in self.terms:
                continue
            term = self.terms[key]
            other = self._other_party(party)
            prior = term.latest_by(other)

            prop = Proposal(
                party=party,
                value=str(u.get("value", "")).strip(),
                verbatim=u.get("verbatim", ""),
                lang=lang,
                stance=u.get("stance", "propose"),
                turn=turn_idx,
            )
            term.proposals.append(prop)

            if prop.stance == "hedge":
                term.state = TermState.HEDGED
                term.agreed_value = None
                needs_interjection.append(key)

            elif prop.stance == "reject":
                term.state = TermState.REJECTED
                term.agreed_value = None
                term.divergence_note = None

            elif prop.stance == "accept":
                if prior is None or prior.stance in ("hedge", "reject"):
                    # accepting something nobody proposed (or only hedged/rejected)
                    # - treat as a proposal, not an agreement.
                    term.state = TermState.PROPOSED
                    term.agreed_value = None
                elif _same(prior.value, prop.value):
                    term.state = TermState.AGREED
                    term.agreed_value = prior.value
                    term.divergence_note = None
                else:
                    # BOTH SAID YES. TO DIFFERENT THINGS. This is the whole product.
                    term.state = TermState.DIVERGED
                    term.agreed_value = None
                    term.divergence_note = (
                        f"{other} said '{prior.value}' ({prior.verbatim}); "
                        f"{party} accepted as '{prop.value}' ({prop.verbatim})"
                    )
                    needs_interjection.append(key)

            else:  # propose
                if prior and prior.stance in ("propose", "accept") and not _same(prior.value, prop.value):
                    term.state = TermState.PROPOSED  # normal haggling, not divergence
                else:
                    term.state = TermState.PROPOSED
                term.agreed_value = None
                term.divergence_note = None

        return needs_interjection

    def transcript_history(self, limit: int = 6) -> str:
        """Recent turns, each attributed to a named speaker.

        Attribution is the point: the agent must never credit one party with the
        other's words, because that becomes a wrong clause in a signed document.
        """
        from . import sarvam
        recent = self.turns[-limit:] if limit else self.turns
        lines = []
        for t in recent:
            cfg = sarvam.PARTIES.get(t.party, {})
            who = cfg.get("name", t.party)
            lang = cfg.get("label", t.lang)
            lines.append(f"[turn {t.idx}] {who} ({lang}): {t.transcript}")
        return "\n".join(lines) or "(no prior turns - this is the first)"

    def _other_party(self, party: str) -> str:
        return next(p for p in sarvam.PARTIES if p != party)

    # ---------- views ----------

    def sheet(self) -> dict:
        drafting_safe = all(
            t.state in (TermState.AGREED, TermState.OPEN) for t in self.terms.values()
        )
        return {
            "session_id": self.session_id,
            "drafting_safe": drafting_safe,
            "terms": [
                {
                    "key": t.key,
                    "description": t.description,
                    "state": t.state.value,
                    "agreed_value": t.agreed_value,
                    "divergence_note": t.divergence_note,
                    "proposals": [asdict(p) for p in t.proposals],
                }
                for t in self.terms.values()
            ],
            "turns": [asdict(x) for x in self.turns],
            "blocked": [t.key for t in self.terms.values()
                        if t.state in (TermState.DIVERGED, TermState.HEDGED)],
        }


def _same(a: str, b: str) -> bool:
    """Values match if they normalise the same. Deliberately strict: '5000' and
    '5000 fixed' are NOT the same, because that difference is exactly the dispute.

    ponytail: string normalise, not an LLM call. Upgrade to a semantic compare only
    if fixtures show real false-positives - an extra model call per term costs
    latency on every turn and buys very little on numeric values.
    """
    na, nb = _norm(a), _norm(b)
    return bool(na) and na == nb


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


# ---------- extraction ----------

EXTRACT_SYSTEM = """You are the term-extraction stage of a rental-negotiation mediator.
The speaker talks in their own Indian language. You receive the transcript verbatim.

Return STRICT JSON only, no prose, no markdown fence:
{"updates":[{"term":"<key>","value":"<normalised value>","verbatim":"<their exact words for this term, original script>","stance":"propose|accept|reject|hedge"}],
 "summary":"<one plain-English sentence of what they just said>"}

Valid term keys: rent, deposit, duration, maintenance, notice_period, painting.
Return an empty updates list if they discussed none of them.

STANCE RULES - these decide whether a contract clause gets written, so be strict:
- "propose": they name a value for the first time, or counter with a different one.
- "accept": they agree to a value already on the table. Set `value` to the value AS
  THEY UNDERSTAND IT, not the value the other side proposed. If they say "yes,
  maintenance separate" set value to what THEY mean by it. This distinction is the
  entire point of the system - do not smooth it over.
- "reject": they refuse.
- "hedge": they neither agree nor refuse - "we'll see", "let me think", "dekhte hain",
  "parkalam", "joiye chhe". A hedge is NOT an accept. Never upgrade a hedge.

VALUE NORMALISATION: amounts as plain digits ("15000", not "fifteen thousand" or
"15k"). Durations as "11 months". For maintenance distinguish "fixed 500" from
"actual" - they are different clauses and conflating them is the bug we exist to catch.
"""

INTERJECT_SYSTEM = """You are a neutral rental mediator on a live call between two
people who share no common language. You have detected a problem and must interject.

Write ONE short spoken sentence (max 25 words) in {lang_label}, in plain conversational
speech a normal person would say out loud. Devanagari/native script. No preamble.

If the problem is DIVERGENCE: both sides think they agreed but meant different values.
Name the specific difference and ask the ONE question that settles it. Do not blame
anyone - they both spoke in good faith.

If the problem is a HEDGE: they gave a soft non-answer. Ask once, plainly, and make it
socially easy to say no.
"""


async def extract(transcript: str, lang_label: str, sheet_summary: str) -> dict:
    user = (
        f"Current term sheet:\n{sheet_summary}\n\n"
        f"New utterance ({lang_label}):\n{transcript}"
    )
    raw = await sarvam.chat(EXTRACT_SYSTEM, user)
    return _loads(raw)


async def interjection(problem: str, lang_label: str) -> str:
    sys_prompt = INTERJECT_SYSTEM.format(lang_label=lang_label)
    return (await sarvam.chat(sys_prompt, problem, temperature=0.3)).strip()


def _loads(raw: str) -> dict:
    """Models fence JSON no matter what the prompt says. One guarded retry beats a
    500 on stage."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```")[1]
        if s.startswith("json"):
            s = s[4:]
    try:
        return json.loads(s.strip())
    except json.JSONDecodeError:
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            try:
                return json.loads(s[i:j + 1])
            except json.JSONDecodeError:
                pass
    return {"updates": [], "summary": ""}
