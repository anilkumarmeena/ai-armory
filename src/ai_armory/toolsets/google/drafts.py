"""Two-step sends and deletes, so the host can ask the user in between.

A drafting tool (e.g. chat_draft) works out the action, changes nothing and returns a draft_id and summary. The
confirming tool (e.g. chat_send, needs_confirmation) takes only the draft_id, so it does exactly what was drafted.
With ``host_approval`` on, it also refuses drafts the host hasn't ``approve``d after putting
``pending(draft_id).summary`` to the user. Drafts are single-use, in-memory, and lapse after ``draft_minutes``.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ai_armory.toolsets.google.settings import current

_LOCK = threading.RLock()
_clock = time.monotonic


@dataclass
class Draft:
    id: str
    kind: str  # the confirming tool's name, e.g. chat_send
    account: str
    summary: str  # what would happen, in full, to put to the user verbatim
    short: str  # a few words, for a log
    payload: dict[str, Any] = field(default_factory=dict)  # what the confirming tool needs; never shown
    made: float = 0.0
    approved: bool = False


_DRAFTS: dict[str, Draft] = {}


def _lapse() -> None:
    limit = current().draft_minutes * 60
    now = _clock()
    for key in [k for k, d in _DRAFTS.items() if now - d.made > limit]:
        del _DRAFTS[key]


def keep(kind: str, account: str, summary: str, short: str, payload: dict[str, Any]) -> Draft:
    with _LOCK:
        _lapse()
        draft = Draft(secrets.token_hex(4), kind, account, summary, short, payload, _clock())
        _DRAFTS[draft.id] = draft
        return draft


def pending(draft_id: str) -> Draft | None:
    """The draft with this id, if it hasn't lapsed or been used."""
    with _LOCK:
        _lapse()
        return _DRAFTS.get(str(draft_id).strip())


def approve(draft_id: str) -> bool:
    """Host only, after the user's clear yes: let the confirming tool act once. False if lapsed or used."""
    with _LOCK:
        if found := pending(draft_id):
            found.approved = True
        return found is not None


def discard(draft_id: str) -> None:
    with _LOCK:
        _DRAFTS.pop(str(draft_id).strip(), None)


def take(draft_id: str, kind: str, remake: str) -> Draft:
    """Remove and return the draft (single use even if acting fails); raises if missing, wrong kind or unapproved."""
    with _LOCK:
        draft = pending(draft_id)
        if draft is None or draft.kind != kind:
            raise ValueError(f"There's no draft {str(draft_id).strip()} for {kind}: it may have lapsed or been used. "
                             f"Make it again with {remake}.")
        if current().host_approval and not draft.approved:
            raise PermissionError(f"Not done: this goes ahead only after {current().user_name} has heard what it "
                                  "will do and said yes, which the host asks for itself. Don't ask or describe it "
                                  "yourself; stop here.")
        del _DRAFTS[draft.id]
        return draft


def reply(draft: Draft, confirm_with: str, **shown: Any) -> dict[str, Any]:
    """What a drafting tool returns."""
    return {"draft_id": draft.id, "account": draft.account, **shown, "summary": draft.summary,
            "next": f"Nothing has happened yet. Call {confirm_with} with this draft_id to go ahead; it waits for the "
                    "user's yes first."}
