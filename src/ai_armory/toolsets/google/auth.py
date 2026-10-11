"""Google sign-in for ``python -m ai_armory.toolsets.google login LABEL`` and ``check-chat``.

Runs a browser OAuth flow with the client JSON (``client_secret`` setting, ``--client-secret`` or
``$GOOGLE_CLIENT_SECRET``) and writes ``<token_dir>/google-<label>.json`` owner-only. The old token is kept until a
sign-in succeeds; no token is ever printed. Chat accounts also ask for Chat scopes, and since an admin block on Chat
makes Google refuse the whole request, the user is offered retries with less of Chat.

Hosts with a web page of their own (a control panel) use the same steps without a terminal: `begin` returns Google's
consent URL for a redirect URI the host serves, and `complete` takes the code Google sends back to it.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass

from ai_armory.toolsets.google import chat, checks
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


OAUTH_ERRORS = {"access_denied": "cancelled", "admin_policy_enforced": "blocked"}  # Google's error -> REFUSED key


def refusal(error: str) -> str:
    """What an OAuth error from Google's redirect means, in a sentence."""
    return REFUSED[OAUTH_ERRORS[error]] if error in OAUTH_ERRORS else f"Google refused the sign-in ({error})."


class Refused(Exception):
    """A sign-in that gave no token: 'cancelled' (Cancel, or the Workspace refused), 'blocked' or 'timeout'."""


def attempts(label: str) -> list[tuple[str, list[str]]]:
    """Scope sets to try in order, each asking for less of Chat than the one refused before it."""
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
        if e.error in OAUTH_ERRORS:
            raise Refused(OAUTH_ERRORS[e.error]) from e
        raise


def save(label: str, creds) -> set[str]:
    """Write the token with the scopes actually granted (refreshing with ungranted ones fails) and return them."""
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
        for line in after_sign_in(label, i, scopes, given):
            print(line)
        return 0
    return 1


def after_sign_in(label: str, step: int, scopes: list[str], given: set[str]) -> list[str]:
    """What a sign-in on scope step `step` means for the tools: permissions left unticked, and for Chat accounts, any
    Chat refusal (recorded, so the Chat tools fall back instead of asking for a futile sign-in) and a Chat check."""
    lines = []
    if missing := [ADDED_SCOPES.get(s, s) for s in scopes if s not in given]:
        lines.append(f"Not allowed, so the tools can't: {'; '.join(missing)}.")
    if label not in settings().chat_labels:
        return lines
    if step == 2 and CHAT_SPACES not in given:
        chat.save_state(label, "blocked", "The Workspace refused Google Chat at sign-in.")
        return [*lines, "chat_unread will read Chat's notification emails instead. Turn those on in Google Chat's "
                        "settings."]
    if step == 1 and CHAT_READ not in given:
        chat.save_state(label, "text_blocked", "The Workspace refused reading Chat messages at sign-in.")
    if abilities := [ADDED_SCOPES[s] for s in CHAT_SCOPES if s in given]:
        lines.append(f"Google Chat: the tools can {', '.join(abilities)}.")
    if CHAT_SPACES in given:
        try:
            lines.append(chat.check(label)[1])
        except Exception as e:
            lines.append(f"Couldn't check Google Chat: {e}")
    return lines


# ── Signing in from a host's own web page ───────────────────────

@dataclass
class WebSignIn:
    """A sign-in begun by `begin`, kept by the host until Google redirects back to it."""
    label: str
    step: int  # index into attempts(label)
    scopes: list[str]
    redirect_uri: str
    verifier: str  # PKCE code verifier
    client: dict  # the OAuth client's JSON


def begin(label: str, step: int, client: dict, redirect_uri: str, login_hint: str = "") -> tuple[str, str, WebSignIn]:
    """Google's consent URL for scope step `step`, its state, and what `complete` needs. Google sends the browser to
    `redirect_uri` with the state and a code (or an error), for `finish`. Raises ValueError for a step there isn't, or
    a Web client that doesn't list `redirect_uri`."""
    from google_auth_oauthlib.flow import Flow

    steps = attempts(label)
    if not 0 <= step < len(steps):
        raise ValueError(f"There's no sign-in step {step} for the {label} account.")
    if (listed := client.get("web", {}).get("redirect_uris")) and redirect_uri not in listed:
        raise ValueError(f"Google won't send this sign-in back to {redirect_uri}: add it to the OAuth client's "
                         "authorised redirect URIs in Google Cloud, then use the client's new JSON.")
    flow = Flow.from_client_config(client, steps[step][1], redirect_uri=redirect_uri)  # makes the PKCE verifier
    url, state = flow.authorization_url(
        access_type="offline", prompt="consent",  # consent: signing in again still returns a refresh token
        **({"login_hint": login_hint} if login_hint else {}))
    return url, state, WebSignIn(label, step, steps[step][1], redirect_uri, flow.code_verifier, client)


def next_step(label: str, step: int) -> str:
    """The words for the scope step after `step` ("without Google Chat"), to offer when Google refuses; "" if none."""
    steps = attempts(label)
    return steps[step + 1][0] if step + 1 < len(steps) else ""


def complete(begun: WebSignIn, code: str) -> tuple[set[str], list[str]]:
    """Exchange Google's code for a token and save it; returns the scopes granted and what they mean
    (`after_sign_in`). Raises oauthlib's or the network's errors."""
    from google_auth_oauthlib.flow import Flow

    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"  # the user may untick boxes: `save` keeps what was granted
    flow = Flow.from_client_config(begun.client, begun.scopes, redirect_uri=begun.redirect_uri,
                                   code_verifier=begun.verifier)
    flow.fetch_token(code=code)
    given = save(begun.label, flow.credentials)
    return given, after_sign_in(begun.label, begun.step, begun.scopes, given)


def check_chat(label: str) -> int:
    """Check Google Chat works on the account and print what to fix if not; exit code 0 if it works."""
    if label not in settings().chat_labels:
        sys.exit(f"Google Chat isn't set up for the {label} account. Set chat = true for it in the Google config.")
    try:
        status, text = chat.check(label)
    except RuntimeError as e:
        sys.exit(str(e))
    print(text)
    return 0 if status == "ok" else 1


def finish(begun: WebSignIn, code: str = "", error: str = "") -> checks.Outcome:
    """Finish a sign-in from Google's redirect: save the token, check each API, and say how it went (refused or
    failed sign-ins change nothing)."""
    label = begun.label
    if error:
        return checks.Outcome(label, "refused", f"{refusal(error)} Nothing changed for the {label} account.")
    if not code:
        return checks.Outcome(label, "failed", "Google's answer had no sign-in code. Start the sign-in again.")
    try:
        given, notes = complete(begun, code)
    except Exception as e:  # oauthlib and network errors; the message never holds a token
        return checks.Outcome(label, "failed", f"Google didn't finish the sign-in ({type(e).__name__}: {e}). Start it "
                              "again.")
    return checks.outcome(label, given, begun.client, f"Signed the {label} Google account in", notes)
