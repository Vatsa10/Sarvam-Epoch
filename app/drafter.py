"""The drafting agent — the third party in the room.

The two negotiators speak their own languages. So does the lawyer, and it may be a
third language again. This agent reads the settled term sheet and produces a draft
rental agreement plus a review brief IN THE LAWYER'S LANGUAGE.

The rule that makes it worth having: it drafts ONLY what was actually agreed. Every
blocked term (DIVERGED, HEDGED, or a magnitude still in doubt) is refused a clause and
listed as an open question instead. A drafting tool that will not draft is the point —
inventing a clause for a term the parties never settled is precisely how a mediated
agreement becomes a dispute.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import llm, sarvam
from .mediator import Negotiation, TermState


@dataclass
class Draft:
    clauses: list[dict] = field(default_factory=list)   # {term, text, provenance}
    open_questions: list[str] = field(default_factory=list)
    lawyer_lang: str = "en-IN"
    ready: bool = False                                 # nothing blocked


SYSTEM = """You are drafting a residential rental agreement in India from a mediated
negotiation between two parties who share no common language.

You will be given only the terms that BOTH parties actually settled, each with the
agreed value and the exact words each party used in their own language. Write one
short, plain clause per settled term. Number them. Use the agreed figure exactly as
given — never round it, never fill in a value that is not there.

Do NOT draft anything for a term you were not given. Do not add standard boilerplate
clauses the parties never discussed. Do not invent a jurisdiction, a start date, or a
penalty. An agreement that quietly contains terms nobody agreed to is worse than a
short one.

Return STRICT JSON only, no prose, no markdown fence:
{"clauses":[{"term":"<key>","text":"<the clause>"}],
 "notes":"<one sentence for the lawyer on what still needs a human decision>"}"""


def _settled(neg: Negotiation) -> list[dict]:
    out = []
    for t in neg.terms.values():
        if t.state is not TermState.AGREED or not t.agreed_value:
            continue
        quotes = "; ".join(
            f"{sarvam.PARTIES.get(p.party, {}).get('name', p.party)} "
            f"({sarvam.PARTIES.get(p.party, {}).get('label', p.lang)}): "
            f"“{p.verbatim}”"
            for p in t.proposals
        )
        out.append({"term": t.key, "description": t.description,
                    "value": t.agreed_value, "provenance": quotes})
    return out


def _blocked(neg: Negotiation) -> list[dict]:
    """Everything a lawyer must NOT be handed as settled, with the reason.

    Reason and quotes are kept SEPARATE. The reason gets translated for the lawyer;
    the quotes never do. Each party's own words are the evidence a dispute would turn
    on, and round-tripping them through translation corrupts them — a Malayalam
    "അഞ്ഞൂറ്" came back from Hindi as "पचास0". Evidence is reproduced, not rendered.
    """
    out = []
    for t in neg.terms.values():
        if t.doubt:
            reason = f"{t.key}: amount unconfirmed — {t.doubt}"
        elif t.state is TermState.DIVERGED:
            reason = (f"{t.key}: both parties said yes, meaning different things. "
                      f"Their exact words are below — do not draft this clause.")
        elif t.state is TermState.HEDGED:
            reason = f"{t.key}: a soft non-answer was given, not agreement"
        elif t.state is TermState.PROPOSED:
            reason = f"{t.key}: proposed but never accepted by the other party"
        elif t.state is TermState.REJECTED:
            reason = f"{t.key}: rejected"
        else:
            continue
        quotes = [
            f"{sarvam.PARTIES.get(p.party, {}).get('name', p.party)} "
            f"({sarvam.PARTIES.get(p.party, {}).get('label', p.lang)}): "
            f"“{p.verbatim}” → {p.value}"
            for p in t.proposals
        ]
        out.append({"reason": reason, "quotes": quotes})
    return out


async def draft(neg: Negotiation, lawyer_lang: str = "en-IN") -> Draft:
    """Produce the draft in the lawyer's language. Never raises — a failed draft
    still returns the open questions, which are the part that stops a bad clause."""
    settled, blocked = _settled(neg), _blocked(neg)
    result = Draft(lawyer_lang=lawyer_lang, ready=not blocked)

    if settled:
        body = "\n".join(
            f"- {s['term']} ({s['description']}): AGREED = {s['value']}\n"
            f"    said: {s['provenance']}"
            for s in settled)
        try:
            msg = await llm.complete_with_tools(SYSTEM, f"Settled terms:\n{body}", [])
            from .mediator import _loads   # fence-tolerant JSON parse, already proven
            parsed = _loads(msg.get("content") or "")
            for c in parsed.get("clauses", []):
                key = c.get("term")
                match = next((s for s in settled if s["term"] == key), None)
                result.clauses.append({
                    "term": key,
                    "text": c.get("text", ""),
                    "provenance": match["provenance"] if match else "",
                })
            if parsed.get("notes"):
                result.open_questions.append(parsed["notes"])
        except Exception as e:  # noqa: BLE001
            result.open_questions.append(f"(drafting failed: {type(e).__name__})")

    notes = result.open_questions
    result.open_questions = []

    # Translate for the lawyer last, so a translation failure still leaves a usable
    # English draft rather than nothing. Quotes are NEVER translated.
    for b in blocked:
        reason = (await _safe(b["reason"], lawyer_lang)
                  if lawyer_lang != "en-IN" else b["reason"])
        result.open_questions.append({"reason": reason, "quotes": b["quotes"]})
    for n in notes:
        result.open_questions.append({
            "reason": await _safe(n, lawyer_lang) if lawyer_lang != "en-IN" else n,
            "quotes": []})
    if lawyer_lang != "en-IN":
        for c in result.clauses:
            c["text"] = await _safe(c["text"], lawyer_lang)
    return result


async def _safe(text: str, lang: str) -> str:
    try:
        return await sarvam.translate(text, lang, "en-IN", mode="formal") or text
    except Exception:  # noqa: BLE001
        return text


def render(d: Draft, neg: Negotiation) -> str:
    """Standalone HTML. Print to PDF and hand it over."""
    rows = "".join(
        f"<li><p>{c['text']}</p><p class=prov>{c['provenance']}</p></li>"
        for c in d.clauses) or "<li><em>No term was settled — nothing to draft.</em></li>"
    open_q = "".join(
        f"<li><p>{q['reason']}</p>"
        + "".join(f"<p class=prov>{quote}</p>" for quote in q.get("quotes", []))
        + (f"<p class=todo><b>To resolve:</b> re-ask this one question and record "
           f"a single value both parties state.</p>" if q.get("quotes") else "")
        + "</li>"
        for q in d.open_questions)
    banner = ("<div class=ok>Every discussed term is settled. This draft covers all "
              "of them.</div>" if d.ready else
              f"<div class=stop><b>NOT READY TO EXECUTE.</b> {len(d.open_questions)} "
              f"item(s) below need a human decision before this is signed.</div>")
    return f"""<!doctype html><meta charset=utf-8>
<title>Draft Rental Agreement</title><style>
body{{font:15px/1.7 Georgia,serif;margin:40px;max-width:820px}}
h1{{font-size:22px;margin:0 0 4px}} .sub{{color:#666;font-size:13px;margin-bottom:22px}}
ol{{padding-left:22px}} li{{margin-bottom:14px}}
.prov{{font-size:12px;color:#777;margin-top:2px;font-family:system-ui}}
.todo{{font-size:13px;color:#a00;margin-top:6px;font-family:system-ui}}
.stop{{padding:12px;background:#ffe9e9;border-left:4px solid #c00;margin:18px 0}}
.ok{{padding:12px;background:#e9ffe9;border-left:4px solid #0a7;margin:18px 0}}
h2{{font-size:15px;margin-top:28px}}
@media print{{body{{margin:0}}}}
</style>
<h1>Draft Rental Agreement</h1>
<div class=sub>Prepared for legal review &mdash; session <code>{neg.session_id}</code>,
{len(neg.turns)} spoken turns. Drafted in <code>{d.lawyer_lang}</code>.
Quotes are each party's own unedited words.</div>
{banner}
<h2>Agreed clauses</h2><ol>{rows}</ol>
<h2>Open questions &mdash; deliberately not drafted</h2>
<ol>{open_q or '<li><em>None.</em></li>'}</ol>
<p class=sub>Terms absent from the clause list were never settled by both parties.
They are listed above rather than drafted, because a clause the parties did not agree
to is how a mediated agreement becomes a dispute.</p>"""
