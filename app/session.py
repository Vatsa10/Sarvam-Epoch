"""Disk persistence. State survives a server restart, which is the Memory & Context
proof shown on stage.

A corrupt or missing snapshot returns a fresh negotiation rather than raising: losing
a session mid-demo is bad, crashing on boot is worse.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import asdict

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

    for t in data.get("terms", []):
        term = neg.terms.get(t.get("key"))
        if term is None:
            continue
        try:
            term.state = TermState(t["state"])
        except (KeyError, ValueError):
            continue
        term.agreed_value = t.get("agreed_value")
        term.divergence_note = t.get("divergence_note")
        term.proposals = [Proposal(**p) for p in t.get("proposals", [])]

    neg.turns = [Turn(**x) for x in data.get("turns", [])]
    return neg
