"""Drafts: everything that sends or deletes is done in two steps, so the host can ask the user in between.

A drafting tool (chat_draft, calendar_draft_invite, docs_draft_delete, sheets_draft_delete_tab) works out exactly
what would happen, changes nothing, keeps it here and returns a draft_id with a summary. The confirming tool
(chat_send, calendar_send_invite, docs_delete, sheets_delete_tab) takes only that draft_id, so what's done is
exactly what was drafted, and is declared needs_confirmation for the host to gate.

With ``host_approval`` on (the default), the confirming tool also refuses any draft the host hasn't approved: the
host looks the draft up with ``pending(draft_id)``, puts its ``summary`` to the user, and on a clear yes calls
``approve(draft_id)`` before letting the call through. A draft is used once at most, and lapses after
``draft_minutes``. Drafts live in this process's memory only.
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
    summary: str  # what would happen, in full, to put to the user word for word
    short: str  # the same in a few words, for a log
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
    """Lets the confirming tool act on this draft once. Only the host calls it, after the user's clear yes. False
    if there's no such draft (it lapsed, or was used)."""
    with _LOCK:
        if found := pending(draft_id):
            found.approved = True
        return found is not None


def discard(draft_id: str) -> None:
    with _LOCK:
        _DRAFTS.pop(str(draft_id).strip(), None)


def take(draft_id: str, kind: str, remake: str) -> Draft:
    """The draft, removed so it's used once at most even if acting on it fails. Raises if there's none of this
    kind, or the host hasn't approved it while host approval is on."""
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
