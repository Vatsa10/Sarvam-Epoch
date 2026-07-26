"""Disk persistence. State survives a server restart, which is the Memory & Context
proof shown on stage.

A corrupt or missing snapshot returns a fresh negotiation rather than raising: losing
a session mid-demo is bad, crashing on boot is worse.
"""
from __future__ import annotations

import json
import pathlib

from .mediator import Negotiation, Proposal, TermState, Turn


def save(neg: Negotiation, path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(neg.sheet(), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load(path: pathlib.Path, session_id: str) -> Negotiation:
    neg = Negotiation(session_id)
    if not path.exists():
        return neg
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return neg

    try:
        terms_list = data.get("terms", [])
        if not isinstance(terms_list, list):
            return neg
        for t in terms_list:
            if not isinstance(t, dict):
                continue
            term = neg.terms.get(t.get("key"))
            if term is None:
                continue
            try:
                term.state = TermState(t["state"])
            except (KeyError, ValueError):
                continue
            term.agreed_value = t.get("agreed_value")
            term.divergence_note = t.get("divergence_note")
            try:
                term.proposals = [Proposal(**p) for p in t.get("proposals", [])]
            except (KeyError, TypeError, AttributeError, ValueError):
                term.proposals = []
    except (KeyError, TypeError, AttributeError, ValueError):
        pass

    try:
        turns_list = data.get("turns", [])
        if isinstance(turns_list, list):
            turns = []
            for x in turns_list:
                try:
                    turns.append(Turn(**x))
                except (KeyError, TypeError, AttributeError, ValueError):
                    continue
            neg.turns = turns
    except (KeyError, TypeError, AttributeError, ValueError):
        pass

    return neg
