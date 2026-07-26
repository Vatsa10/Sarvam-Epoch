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
    PARKED = "PARKED"          # unresolvable for now, deliberately set aside so
                                # the rest of the call can proceed


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
    doubt: str | None = None   # magnitude question awaiting an answer
    attempts: int = 0          # rounds that touched this term without settling it

    def latest_by(self, party: str) -> Proposal | None:
        for p in reversed(self.proposals):
            if p.party == party:
                return p
        return None


# Plausible monthly rupee ranges. A stated amount far below the floor is almost
# never literal - people say "seventeen" and mean seventeen thousand. Recording
# rent = 17 is not a small error, it is a nonsense contract, so the system must
# ask before it writes anything down.
#
# These are TUNABLE WITHOUT EDITING CODE: drop a plausible.json next to this repo
# (or point PLAUSIBLE_JSON at one) shaped {"rent": [1500, 1000000], ...}. Editing
# Python between demo runs is how you ship a syntax error to a projector.
_DEFAULT_PLAUSIBLE: dict[str, tuple[int, int]] = {
    "rent":        (1500, 1_000_000),
    "deposit":     (3000, 5_000_000),
    "maintenance": (100, 200_000),
}


def _load_plausible() -> dict[str, tuple[int, int]]:
    import os
    import pathlib

    path = pathlib.Path(os.getenv(
        "PLAUSIBLE_JSON",
        pathlib.Path(__file__).resolve().parent.parent / "plausible.json"))
    ranges = dict(_DEFAULT_PLAUSIBLE)
    if not path.exists():
        return ranges
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        for key, pair in raw.items():
            lo, hi = int(pair[0]), int(pair[1])
            if lo > 0 and hi > lo:
                ranges[key] = (lo, hi)
    except (json.JSONDecodeError, OSError, TypeError, ValueError, KeyError, IndexError):
        # A malformed override must never take the app down mid-demo - fall back
        # to the built-in ranges and carry on.
        return dict(_DEFAULT_PLAUSIBLE)
    return ranges


PLAUSIBLE = _load_plausible()

# Below this, a bare number is read as spoken shorthand ("seventeen" = 17000).
# At or above it, the figure is taken literally even if it is under the floor.
SHORTHAND_MAX = 100


def magnitude_doubt(term_key: str, value: str) -> str | None:
    """Return a suggested reading when a bare number is implausibly small.

    "17" for rent -> "17000".

    Returns None - i.e. trust the value as stated - when it is non-numeric, has no
    configured range, or CARRIES ANY WORDS. That last rule is deliberate and load
    bearing: "fixed 500" and "actual" are real, meaningful answers for maintenance,
    and the difference between them is exactly the divergence this product exists to
    catch. Second-guessing a value with words attached would destroy that signal to
    fix a problem it does not have.
    """
    lo, _hi = PLAUSIBLE.get(term_key, (None, None))
    if lo is None:
        return None
    alnum = "".join(ch for ch in value if ch.isalnum())
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits or digits != alnum:
        return None                      # words attached (or nothing) - trust it
    n = int(digits)
    if n <= 0 or n >= lo:
        return None

    # Only SPOKEN-SHORTHAND numbers get corrected. "seventeen" (17) obviously means
    # seventeen thousand. But 1400 is just a low rent, and blindly scaling it would
    # propose 1,400,000 - turning a slightly-odd figure into an absurd one. Anything
    # at or above SHORTHAND_MAX is taken as stated.
    if n >= SHORTHAND_MAX:
        return None
    return str(n * 1000)


def scaled_without_asking(term_key: str, verbatim: str, value: str) -> str | None:
    """Catch the model quietly upgrading a shorthand instead of asking about it.

    The speaker said "17"; the model wrote 17000. That is probably right, but it is
    still a GUESS about money, and 17 could equally have meant 1700. Whether the
    model asks is not reliably steerable by prompt - observed asking on one run and
    silently normalising the identical utterance on the next - so the confirmation
    is enforced here instead.

    Returns the value the model assumed, so the question can name it.
    """
    if term_key not in PLAUSIBLE:
        return None
    spoken = "".join(ch for ch in verbatim if ch.isdigit())
    written = "".join(ch for ch in value if ch.isdigit())
    if not spoken or not written or spoken == written:
        return None
    if int(spoken) >= SHORTHAND_MAX:
        return None                      # not shorthand; some other rewrite
    if not written.startswith(spoken):
        return None                      # unrelated number, not a scaling
    return value


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
    """Append-only proposal log with a term sheet folded on top of it.

    Every proposal is kept forever - `apply` appends before it touches state - so a
    term can always name who said what, when, in which language, and what superseded
    it. The Term row itself is updated in place rather than recomputed, so the log is
    the record and the row is the current view of it.
    """

    def __init__(self, session_id: str = "default",
                 parties: tuple[str, str] | None = None) -> None:
        self.session_id = session_id
        self.turns: list[Turn] = []
        self.terms: dict[str, Term] = {k: Term(k, v) for k, v in TERMS.items()}
        # Explicit parties let a room-scoped negotiation name its own two
        # participants instead of reading the global demo's sarvam.PARTIES.
        self.parties: tuple[str, str] | None = parties

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

            # A QUESTION IS NOT A PROPOSAL. "What is the rent?" names no value, and
            # a term update carrying no value would otherwise land as a PROPOSED row
            # with a blank amount - a phantom position nobody actually took, which
            # then contaminates every later comparison. Drop it.
            #
            # EXCEPT a hedge, which is meaningful precisely BECAUSE it carries no
            # value: "dekhte hain" is a non-answer about a real term, and dropping it
            # would silently lose the thing this product refuses to treat as
            # agreement.
            if (not str(u.get("value", "")).strip()
                    and u.get("stance") != "hedge"):
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

            # Before anything is recorded: is this number even possible? "17" for a
            # monthly rent is not a cheap flat, it is a misheard "seventeen thousand".
            if prop.stance in ("propose", "counter", "accept"):
                likely = magnitude_doubt(key, prop.value)
                assumed = None if likely else scaled_without_asking(
                    key, prop.verbatim, prop.value)
                if likely or assumed:
                    # Two different doubts, and they must not share wording. When the
                    # raw value is implausible, quote what they said. When the model
                    # silently scaled it, quote the SPOKEN number and name the
                    # assumption - otherwise the question reads "you said 17000, did
                    # you mean 17000?", which is gibberish.
                    if likely:
                        term.doubt = (
                            f"{party} said '{prop.value}' for {key} — implausibly "
                            f"low. Confirm whether they mean {likely}."
                        )
                    else:
                        spoken = "".join(c for c in prop.verbatim if c.isdigit())
                        term.doubt = (
                            f"{party} only said '{spoken}' for {key}; it was read as "
                            f"{assumed}. Confirm that, or the other reading."
                        )
                    term.state = TermState.PROPOSED
                    term.agreed_value = None
                    needs_interjection.append(key)
                    continue
                term.doubt = None

            if prop.stance == "hedge":
                term.state = TermState.HEDGED
                term.agreed_value = None
                term.attempts += 1
                needs_interjection.append(key)

            elif prop.stance == "reject":
                term.state = TermState.REJECTED
                term.agreed_value = None
                term.divergence_note = None

            elif prop.stance == "counter":
                # Explicit haggling: "17 is too much, I'll give you 14". Both sides
                # KNOW they disagree, so this is emphatically NOT divergence -
                # divergence is a HIDDEN disagreement where both said yes. Treating
                # a counter-offer as DIVERGED empties the word of meaning.
                term.state = TermState.PROPOSED
                term.agreed_value = None
                term.divergence_note = None
                term.attempts += 1

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
                    term.attempts = 0
                elif _both_plain_numbers(prior.value, prop.value):
                    # Two explicit, different NUMBERS is haggling, not divergence -
                    # whatever stance the model picked. Nobody says "I agree" to
                    # 17000 while meaning 14000; stating your own figure IS the
                    # disagreement, and both sides can see it.
                    #
                    # Divergence needs the values to be describable the same way
                    # while meaning different things ("separate" = actual cost vs a
                    # fixed 500). That case survives because those are not both
                    # plain numbers. This is the guard that holds when the model
                    # labels a counter-offer as `accept`, which it does.
                    term.state = TermState.PROPOSED
                    term.agreed_value = None
                    term.divergence_note = None
                else:
                    # BOTH SAID YES. TO DIFFERENT THINGS. This is the whole product.
                    term.state = TermState.DIVERGED
                    term.agreed_value = None
                    term.divergence_note = (
                        f"{other} said '{prior.value}' ({prior.verbatim}); "
                        f"{party} accepted as '{prop.value}' ({prop.verbatim})"
                    )
                    term.attempts += 1
                    needs_interjection.append(key)

            else:  # propose
                if prior and prior.stance in ("propose", "accept") and not _same(prior.value, prop.value):
                    term.state = TermState.PROPOSED  # normal haggling, not divergence
                else:
                    term.state = TermState.PROPOSED
                term.agreed_value = None
                term.divergence_note = None

        return needs_interjection

    def copied_the_other_side(self, key: str, party: str, gloss: str = "") -> bool:
        """True when this party's recorded value looks copied from the other side.

        The model is told to record what the ACCEPTING speaker means, not what was
        proposed to them. It does not always: asked to fold "maintenance separate,
        whatever the cost" against "500 rupees a month", it recorded BOTH as
        "actual" — flattening a real divergence into agreement, which is the one
        outcome this product exists to prevent.

        Detected by evidence rather than by trusting the label: if the speaker's own
        words (or their English gloss) carry a number that is nowhere in the value
        recorded for them, the value did not come from them.
        """
        term = self.terms.get(key)
        if term is None:
            return False
        mine = term.latest_by(party)
        other = term.latest_by(self._other_party(party))
        if mine is None or other is None or not _same(mine.value, other.value):
            return False
        said = "".join(c for c in (mine.verbatim + " " + gloss) if c.isdigit())
        recorded = "".join(c for c in mine.value if c.isdigit())
        return bool(said) and said != recorded

    def values_match(self, key: str) -> bool:
        """True when both parties' latest values are the same thing.

        If they match, it is agreement — full stop. A divergence flag on identical
        values is the model narrating a disagreement that the record contradicts,
        and it was observed doing exactly that on two parties who both said 15000.
        """
        term = self.terms.get(key)
        if term is None:
            return False
        a, b = (term.latest_by(p) for p in sarvam.PARTIES)
        return a is not None and b is not None and _same(a.value, b.value)

    def is_open_haggle(self, key: str) -> bool:
        """True when the two sides' latest figures are both bare numbers that differ.

        That is visible disagreement, so it can never be a hidden divergence - and
        the model cannot be trusted to keep the two apart. It has labelled a
        counter-offer `accept`, and separately called flag_divergence on one, so
        both the state machine and the tool dispatcher check this.
        """
        term = self.terms.get(key)
        if term is None:
            return False
        ids = list(sarvam.PARTIES)
        a, b = (term.latest_by(p) for p in ids)
        if a is None or b is None:
            return False
        return _both_plain_numbers(a.value, b.value) and not _same(a.value, b.value)

    def park(self, key: str, reason: str) -> bool:
        """Set a term aside instead of letting it stall the call forever.

        Refuses an already-AGREED term - parking is for giving up on a fight, never
        for undoing a settled one. `apply()` overwrites `state` unconditionally on
        the next propose/counter/accept/hedge, so a parked term un-parks the moment
        anyone brings it up again - no special-casing needed here.
        """
        term = self.terms.get(key)
        if term is None or term.state == TermState.AGREED:
            return False
        term.state = TermState.PARKED
        term.agreed_value = None
        term.divergence_note = reason
        return True

    def deadlocked(self, key: str, cap: int = 3) -> bool:
        """True once a term has burned `cap` failed rounds without settling.

        This is the signal the relay uses to decide to park rather than keep
        looping two people through the same disagreement.
        """
        term = self.terms.get(key)
        if term is None:
            return False
        return term.attempts >= cap and term.state != TermState.AGREED

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
        pool = self.parties if self.parties is not None else tuple(sarvam.PARTIES)
        return next(p for p in pool if p != party)

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
                    "doubt": t.doubt,
                    "attempts": t.attempts,
                    "proposals": [asdict(p) for p in t.proposals],
                }
                for t in self.terms.values()
            ],
            "turns": [asdict(x) for x in self.turns],
            "blocked": [t.key for t in self.terms.values()
                        if t.state in (TermState.DIVERGED, TermState.HEDGED,
                                       TermState.PARKED)],
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


_NOISE = ("rupees", "rupee", "inr", "rs", "permonth", "monthly", "months", "month", "days", "day")


def _both_plain_numbers(a: str, b: str) -> bool:
    """True when both values are bare figures with no qualifying words.

    "17000" and "14000" -> True (open haggling).
    "actual" and "fixed 500" -> False (the words carry the disagreement, and that
    is where hidden divergence lives).
    """
    def plain(v: str) -> bool:
        alnum = "".join(c for c in v if c.isalnum())
        return bool(alnum) and alnum.isdigit()
    return plain(a) and plain(b)


def _norm(s: str) -> str:
    t = "".join(ch for ch in s.lower() if ch.isalnum())
    for w in _NOISE:
        t = t.replace(w, "")
    return t


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
