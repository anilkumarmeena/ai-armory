"""Signing a Google account in, for ``python -m ai_armory.toolsets.google login LABEL`` (and ``check-chat``).

The label must be one of the configured accounts. Opens a browser sign-in with the OAuth client JSON (the
``client_secret`` setting, ``--client-secret`` or ``$GOOGLE_CLIENT_SECRET``) and stores the token in
``<token_dir>/google-<label>.json``, readable only by its owner. Sign in again to replace the token, e.g. after the
tools gain a permission; the old token stays exactly as it was until a sign-in succeeds. Nothing here prints a
token.

Accounts with Chat are also asked for Google Chat. A Workspace admin can block Chat for the app, and Google then
refuses the whole request, so the sign-in offers to try again without reading messages, then without Chat at all.
Once signed in, it checks that Chat works; `check_chat` runs only that check.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable

from ai_armory.toolsets.google import chat
from ai_armory.toolsets.google.common import (ADDED_SCOPES, CHAT_READ, CHAT_SCOPES, CHAT_SPACES, SCOPES, settings,
                                              token_path, write_private)

TIMEOUT_SECONDS = 180  # Google's "Access blocked" page never comes back to the local server, so stop waiting
BLOCKED_HINT = ("If Google says 'Access blocked' or 'admin_policy_enforced', your Workspace blocks Google Chat for "
                "this app. Close the tab and come back here.")
REFUSED = {
    "cancelled": "Google didn't give access: the sign-in was cancelled, or the Workspace refused it.",
    "blocked": "Google refused the sign-in: the Workspace blocks what it asked for (admin_policy_enforced).",
    "timeout": f"No answer from the browser within {TIMEOUT_SECONDS // 60} minutes. If Google said 'Access blocked', "
               "the Workspace blocks Google Chat for this app.",
}


class Refused(Exception):
    """A sign-in that gave no token: 'cancelled' (Cancel, or the Workspace refused), 'blocked' or 'timeout'."""


def attempts(label: str) -> list[tuple[str, list[str]]]:
    """What to ask for, in order: each one after the first asks for less of Chat, tried if the one before is refused."""
    if label not in settings().chat_labels:
        return [("", list(SCOPES))]
    return [("with Google Chat", SCOPES + CHAT_SCOPES),
            ("with Google Chat, but without reading messages", SCOPES + [s for s in CHAT_SCOPES if s != CHAT_READ]),
            ("without Google Chat", list(SCOPES))]


def client_secret(given: str = "") -> str:
    return given or settings().client_secret or os.environ.get("GOOGLE_CLIENT_SECRET", "")


def sign_in(scopes: list[str], secret: str):
    """One browser sign-in. Returns the credentials, or raises Refused."""
    from google_auth_oauthlib.flow import InstalledAppFlow, WSGITimeoutError
    from oauthlib.oauth2.rfc6749.errors import OAuth2Error

    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"  # the user may untick boxes: `save` keeps what was granted
    flow = InstalledAppFlow.from_client_secrets_file(os.path.expanduser(secret), scopes)
    try:
        # prompt=consent: signing in again (e.g. for a new scope) still returns a refresh token.
        return flow.run_local_server(port=0, prompt="consent", timeout_seconds=TIMEOUT_SECONDS)
    except WSGITimeoutError as e:
        raise Refused("timeout") from e
    except OAuth2Error as e:
        if e.error == "access_denied":
            raise Refused("cancelled") from e
        if e.error == "admin_policy_enforced":
            raise Refused("blocked") from e
        raise


def save(label: str, creds) -> set[str]:
    """Writes the token with the scopes Google granted, not the ones asked for: refreshing with a scope that was
    never granted fails outright. Returns them."""
    given = creds.granted_scopes or creds.scopes or []
    given = set(given.split() if isinstance(given, str) else given)
    write_private(token_path(label), json.dumps(json.loads(creds.to_json()) | {"scopes": sorted(given)}))
    return given


def connect(label: str, secret: str, ask: Callable[[str], str] = input,
            sign: Callable[[list[str], str], object] = sign_in) -> int:
    steps = attempts(label)
    print(f"Opening the browser to sign the {label} account in. Use the same Google account as before, and allow "
          "every permission it lists.")
    for i, (words, scopes) in enumerate(steps):
        if CHAT_SPACES in scopes:
            print(BLOCKED_HINT)
        try:
            creds = sign(scopes, secret)
        except Refused as refused:
            print(REFUSED[str(refused)])
            if i + 1 < len(steps) and ask(f"Try again {steps[i + 1][0]}? [y/N] ").strip().lower().startswith("y"):
                continue
            print(f"Nothing changed: the {label} account's old sign-in, if it had one, is still in place.")
            return 1
        given = save(label, creds)
        print(f"Connected the {label} account{' ' + words if words else ''}.")
        if missing := [ADDED_SCOPES.get(s, s) for s in scopes if s not in given]:
            print(f"Not allowed, so the tools can't: {'; '.join(missing)}.")
        if label in settings().chat_labels:
            _after_chat_sign_in(label, i, given)
        return 0
    return 1


def _after_chat_sign_in(label: str, step: int, given: set[str]) -> None:
    """Records a Chat refusal for the Chat tools (so they fall back rather than ask for a sign-in that can't
    work), says what Chat can do, and checks it."""
    if step == 2 and CHAT_SPACES not in given:
        chat.save_state(label, "blocked", "The Workspace refused Google Chat at sign-in.")
        print("chat_unread will read Chat's notification emails instead. Turn those on in Google Chat's settings.")
        return
    if step == 1 and CHAT_READ not in given:
        chat.save_state(label, "text_blocked", "The Workspace refused reading Chat messages at sign-in.")
    if abilities := [ADDED_SCOPES[s] for s in CHAT_SCOPES if s in given]:
        print(f"Google Chat: the tools can {', '.join(abilities)}.")
    if CHAT_SPACES in given:
        try:
            print(chat.check(label)[1])
        except Exception as e:
            print(f"Couldn't check Google Chat: {e}")


def check_chat(label: str) -> int:
    """Checks Google Chat works on the account, and says what to fix if not. 0 if it does."""
    if label not in settings().chat_labels:
        sys.exit(f"Google Chat isn't set up for the {label} account. Set chat = true for it in the Google config.")
    try:
        status, text = chat.check(label)
    except RuntimeError as e:
        sys.exit(str(e))
    print(text)
    return 0 if status == "ok" else 1
