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

import re
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
        elif t.state is TermState.PARKED:
            reason = (f"{t.key}: the parties attempted this term ({t.attempts} "
                      f"attempt(s)) and could not agree, so it was set aside. "
                      f"Needs a human decision before signing.")
        elif t.state is TermState.OPEN:
            # Never raised by either party - a hole in the contract, not a
            # partial agreement. No quotes exist because nobody spoke.
            reason = (f"{t.key}: never discussed by either party. This is not a "
                      f"disagreement, it is a missing clause — the agreement is "
                      f"silent on {t.description} and needs a human decision "
                      f"before signing.")
        else:
            continue
        if t.reopened_from:
            reason += f" (was AGREED at {t.reopened_from}, since re-opened)"
        quotes = [
            f"{sarvam.PARTIES.get(p.party, {}).get('name', p.party)} "
            f"({sarvam.PARTIES.get(p.party, {}).get('label', p.lang)}): "
            f"“{p.verbatim}” → {p.value}"
            for p in t.proposals
        ]
        out.append({"reason": reason, "quotes": quotes,
                    "parked": t.state is TermState.PARKED})
    return out


_NUM = re.compile(r"\d[\d,]*")


def _tx_lines(transcript: list[dict], key: str) -> list[dict]:
    """Transcript entries that mention this term by key. Cheap substring match —
    ponytail: no embeddings, no LLM call, just the word the term is named after."""
    k = key.lower()
    return [e for e in transcript if k in (e.get("text") or "").lower()]


def _tx_split(transcript: list[dict], settled: list[dict]) -> list[dict]:
    """Attach transcript evidence to settled terms, and pull out any term whose
    agreed value the transcript contradicts.

    A contradiction is only claimed on numbers: the term is mentioned in the call and
    every figure spoken alongside it differs from the value on the sheet. Non-numeric
    talk is never treated as a contradiction — the transcript is in the parties' own
    languages and scripts, and guessing at disagreement there would either invent
    conflicts or, worse, silently drop a clause the parties really did settle.
    Returns the conflicting entries; `settled` is mutated in place.
    """
    conflicts = []
    for s in list(settled):
        lines = _tx_lines(transcript, s["term"])
        if not lines:
            continue
        want = set(_NUM.findall(str(s["value"]).replace(",", "")))
        said = {n.replace(",", "") for ln in lines for n in _NUM.findall(ln["text"])}
        quotes = [f"{ln.get('speaker_name', ln.get('speaker_id', '?'))} "
                  f"({ln.get('lang', '')}): “{ln['text']}”" for ln in lines]
        if want and said and not (want & said):
            settled.remove(s)
            conflicts.append({
                "reason": f"{s['term']}: the term sheet says {s['value']}, but the call "
                          f"transcript records a different figure. Do not draft this "
                          f"clause until one value is confirmed.",
                "quotes": quotes,
                "parked": False,
            })
        else:
            s["provenance"] = "; ".join(filter(None, [s["provenance"]] + quotes))
    return conflicts


async def draft(neg: Negotiation, lawyer_lang: str = "en-IN",
                transcript: list[dict] | None = None) -> Draft:
    """Produce the draft in the lawyer's language. Never raises — a failed draft
    still returns the open questions, which are the part that stops a bad clause.

    `transcript` is the raw call log (`{speaker_id, speaker_name, lang, text, ts}`).
    It is supporting evidence only: it can add provenance to a settled term, or move
    one into the open questions, but it can never promote anything into a clause."""
    settled, blocked = _settled(neg), _blocked(neg)
    blocked += _tx_split(transcript or [], settled)
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
        result.open_questions.append({"reason": reason, "quotes": b["quotes"],
                                      "parked": b.get("parked", False)})
    for n in notes:
        result.open_questions.append({
            "reason": await _safe(n, lawyer_lang) if lawyer_lang != "en-IN" else n,
            "quotes": [], "parked": False})
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
        f"<li>{'<span class=parked>PARKED &mdash; deadlocked</span> ' if q.get('parked') else ''}"
        f"<p>{q['reason']}</p>"
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
.parked{{display:inline-block;font-size:11px;font-weight:700;color:#fff;background:#555;
padding:1px 6px;border-radius:3px;font-family:system-ui}}
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


if __name__ == "__main__":   # python -m app.drafter — the rule, asserted.
    import asyncio

    from .mediator import Proposal

    neg = Negotiation(session_id="test")
    d = neg.terms["deposit"]
    d.state, d.agreed_value = TermState.AGREED, "50000"
    d.proposals = [Proposal("p1", "50000", "fifty thousand", "en-IN", "propose", 0)]
    m = neg.terms["maintenance"]
    m.state = TermState.DIVERGED
    m.proposals = [Proposal("p1", "actual", "alag", "hi-IN", "propose", 1),
                   Proposal("p2", "500", "five hundred fixed", "ml-IN", "accept", 1)]

    tx = [{"speaker_id": "p2", "speaker_name": "B", "lang": "en-IN", "ts": 0.0,
           "text": "the deposit is 40000, not what the sheet says"}]

    # 1. a DIVERGED term is an open question, never a clause.
    out = asyncio.run(draft(neg, "en-IN", []))
    assert not any(c["term"] == "maintenance" for c in out.clauses), out.clauses
    assert any("maintenance" in q["reason"] for q in out.open_questions), out.open_questions
    assert not out.ready

    # 2. a transcript that contradicts a settled figure demotes it too.
    out2 = asyncio.run(draft(neg, "en-IN", tx))
    assert not any(c["term"] == "deposit" for c in out2.clauses), out2.clauses
    assert any("deposit" in q["reason"] and "50000" in q["reason"]
               for q in out2.open_questions), out2.open_questions
    print("ok — blocked and contradicted terms become open questions, not clauses")
