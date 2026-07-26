# Demo script — 3 minutes

**Recording:** Vatsa's laptop screen + mic. Vatsa = LANDLORD, speaks Gujarati, reads
English (so the judge can follow every caption). Sreedev = TENANT on his phone, speaks
Malayalam. His screen is never shown — everything the judge needs is on the laptop.

**Setup before you hit record**
- Server running, ngrok up, both joined, room already created, mic tested once.
- Sreedev's phone on silent, screen not shared.
- Have `/replay-all` open in a second tab as the fallback.
- Close every stray tab holding a room slot (rooms cap at 2).

---

## 0:00–0:30 — Business context

No tech. No product name. Just the problem.

> "A migrant tenant from Bihar rents a flat in Kochi. The landlord speaks Malayalam,
> the tenant speaks Hindi. Neither speaks the other's language, so a broker translates
> and both of them sign a document neither one fully understood.
>
> The dispute never starts at signing. It starts at exit — when 'maintenance was
> separate' turns out to have meant two completely different things, and the deposit
> is gone. That's two years in a consumer forum for money most people just write off."

## 0:30–1:00 — How this works today

> "Today there are three people in that conversation: two parties and a broker who is
> not neutral — he's paid on the deal closing.
>
> He translates faithfully. That's the problem. If I say maintenance is separate and
> mean actual cost, and the tenant hears separate and means five hundred a month, a
> perfect translation passes both yeses through and nobody ever learns they disagreed.
>
> The translation was correct. The outcome is a dispute."

Then, one line to set up the demo:

> "So we didn't build a translator. We built a mediator that tracks what was actually
> agreed — and refuses to write down an agreement that isn't one."

---

## 1:00–3:00 — Live demo

Four turns. Say the Gujarati, then narrate what appears. Do NOT explain the UI.

### Turn 1 — you (Gujarati)

> **"ભાડું પંદર હજાર રૂપિયા મહિને રહેશે."**
> *(Rent will be fifteen thousand a month.)*

Narrate while it lands:
> "I'm speaking Gujarati. He's reading Malayalam on his phone."

### Turn 2 — Sreedev (Malayalam)

> **"പതിനയ്യായിരം ശരിയാണ്, സമ്മതം."**
> *(Fifteen thousand is fine, agreed.)*

Point at the sheet:
> "Rent goes green. Both said the same number — that one's safe to draft."

### Turn 3 — you (Gujarati) — set the trap

> **"મેન્ટેનન્સ અલગથી રહેશે, જે પણ ખર્ચ થાય એ પ્રમાણે."**
> *(Maintenance will be separate, whatever the actual cost is.)*

Say nothing. Let it relay.

### Turn 4 — Sreedev (Malayalam) — spring it

> **"ശരി, മെയിന്റനൻസ് വേറെ. മാസം 500 രൂപ ഫിക്സഡ്."**
> *(Okay, maintenance separate. 500 a month, fixed.)*

**This is the moment. Stop talking for a beat, then:**

> "We both just said yes. Watch the sheet go red.
>
> I meant actual cost. He meant a fixed five hundred. Neither of us noticed — and a
> translator would have passed both of those through as agreement.
>
> It's asking us the one question that settles it, in both our languages."

### Close — the artifact

Click **Draft agreement**. Pick a language if you want to show the third-party point.

> "Here's the agreement for the lawyer. Rent is drafted — we genuinely agreed on it.
>
> Maintenance is **not** drafted. It's in open questions, with both our exact words in
> our own scripts, and what still has to be decided.
>
> That's the product. It won't write a clause we didn't actually agree to."

---

## If the live run drops

Don't debug on camera. One sentence, then switch:

> "Audio's gone — here's the same thing running end to end."

`POST /replay-all` → 3/3, then open one packet. It exercises the whole chain — agent,
term state, artifact — with no microphone.

---

## Rules for the recording

- **Never say the product name in the first 30 seconds.** Lead with the problem.
- **Never say "two people, no common language" as the headline.** The rubric scores that
  as a language swap. The headline is *"a faithful translator manufactures the dispute."*
- **Don't narrate the UI.** No "here you can see the panel". Say what just happened to
  the negotiation.
- **Don't claim realtime.** Turns are push-to-talk. Say "turn by turn" if it comes up.
- Let the red moment breathe. Two seconds of silence there is worth more than a sentence.
- If a turn takes a few seconds, say the business point out loud instead of watching the
  spinner — never dead air.

## If a judge asks

**"Isn't this just translation?"**
> "Translation is the easy half and we get it free. The hard half is that a correct
> translation still lets two people agree to different things. That's the state the
> sheet tracks and a translator can't."

**"What if they just disagree?"**
> "It escalates — options, then names the exact gap, then parks the term and moves on.
> You still get an agreement covering everything you did settle."

**"Does it remember?"**
> "Same two people, next negotiation: it opens knowing what they settled last time, and
> makes them restate anything they want carried over rather than assuming it."
