"""Whether each Google API the tool sets use works for a signed-in account.

A sign-in can succeed while an API is still turned off in the OAuth client's Google Cloud project; the tools then fail
with "API has not been used in project … or it is disabled". `check` tries one cheap call per API, all at once, and
says which are ready, which are off (with the page that turns each on), and which permission wasn't allowed. A call
on a made-up id answers 404 when the API is on, and 403 (accessNotConfigured) when it's off.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ai_armory.toolsets.google.common import (BROADER, CALENDAR, DOCS_READ, DRIVE_METADATA, DRIVE_READ, GMAIL_READ,
                                              SHEETS, error_reasons, google_error, granted, service)

MADE_UP_ID = "ai-armory-check"
OFF = {"accessNotConfigured", "SERVICE_DISABLED"}
LIBRARY = "https://console.cloud.google.com/apis/library/{api}"


@dataclass(frozen=True)
class Service:
    name: str  # "Gmail"
    api: str  # discovery name
    version: str
    scope: str  # the scope a check needs (or the broader one, common.BROADER)
    api_id: str  # what Google Cloud calls it
    call: Callable  # a cheap request, given the API's client


SERVICES = (
    Service("Gmail", "gmail", "v1", GMAIL_READ, "gmail.googleapis.com", lambda s: s.users().getProfile(userId="me")),
    Service("Calendar", "calendar", "v3", CALENDAR, "calendar-json.googleapis.com",
            lambda s: s.calendarList().list(maxResults=1)),
    Service("Drive", "drive", "v3", DRIVE_METADATA, "drive.googleapis.com", lambda s: s.about().get(fields="user")),
    Service("Docs", "docs", "v1", DOCS_READ, "docs.googleapis.com", lambda s: s.documents().get(documentId=MADE_UP_ID)),
    Service("Sheets", "sheets", "v4", SHEETS, "sheets.googleapis.com",
            lambda s: s.spreadsheets().get(spreadsheetId=MADE_UP_ID)),
    Service("Slides", "slides", "v1", DRIVE_READ, "slides.googleapis.com",
            lambda s: s.presentations().get(presentationId=MADE_UP_ID)),
)
CHAT_APIS = (("Google Chat", "chat.googleapis.com"), ("People", "people.googleapis.com"))  # for Chat accounts


@dataclass
class Check:
    """Whether one Google API works for the account."""
    name: str
    state: str  # "ok"; "off" (not enabled in the client's Cloud project); "denied" (its permission wasn't allowed); "error"
    fix: str = ""  # where to turn it on, when it's off
    why: str = ""  # what went wrong, for an error


@dataclass
class Outcome:
    """How a sign-in (auth.finish) or a check (`recheck`) went, for the host's page (signin_page.render)."""
    label: str  # the account, or "" when the sign-in wasn't known
    state: str  # "done", "refused" or "failed"
    said: str  # one or two sentences
    email: str = ""
    checks: list[Check] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # unticked permissions, Chat, anything the host adds


def project(client: dict) -> str:
    """The Cloud project number in an OAuth client's JSON (its client_id is "<number>-<id>.apps.googleusercontent.com")."""
    return next(iter(client.values()), {}).get("client_id", "").split("-")[0]


def apis(client: dict | None = None) -> list[dict]:
    """The APIs to turn on, each {"name", "url", "chat"} (chat: only for Chat accounts), opening in the client's
    project when there's one."""
    where = f"?project={p}" if client and (p := project(client)) else ""
    return ([{"name": s.name, "url": LIBRARY.format(api=s.api_id) + where, "chat": False} for s in SERVICES]
            + [{"name": name, "url": LIBRARY.format(api=api) + where, "chat": True} for name, api in CHAT_APIS])


def check(label: str, given: set[str], client: dict) -> tuple[list[Check], str]:
    """Try each API on the account; returns the checks and its email (from Gmail's profile), or ""."""
    try:
        service(label, "drive", "v3")  # refreshes an expired token once, before the checks share it
    except Exception:
        pass  # each check says what's wrong
    off_at = "?project=" + project(client)
    with ThreadPoolExecutor(len(SERVICES)) as pool:
        tried = list(pool.map(lambda svc: _try(label, svc, given, off_at), SERVICES))
    return [c for c, _ in tried], next((email for _, email in tried if email), "")


def _try(label: str, svc: Service, given: set[str], off_at: str) -> tuple[Check, str]:
    from googleapiclient.errors import HttpError

    if svc.scope not in given and BROADER.get(svc.scope) not in given:
        return Check(svc.name, "denied"), ""
    try:
        answer = svc.call(service(label, svc.api, svc.version)).execute()
    except HttpError as e:
        if error_reasons(e) & OFF:
            return Check(svc.name, "off", LIBRARY.format(api=svc.api_id) + off_at), ""
        if e.status_code in (400, 404):  # the made-up id: the API is on
            return Check(svc.name, "ok"), ""
        return Check(svc.name, "error", why=google_error(e)), ""
    except Exception as e:  # network, refresh; the message never holds a token
        return Check(svc.name, "error", why=google_error(e)), ""
    return Check(svc.name, "ok"), answer.get("emailAddress", "") if svc.api == "gmail" else ""


def summary(did: str, email: str, checks: list[Check]) -> str:
    """One or two sentences: what was done ("Signed the work Google account in"), as whom, and what's off."""
    off = [c.name for c in checks if c.state == "off"]
    said = f"{did}{f' as {email}' if email else ''}."
    if off:
        names = off[0] if len(off) == 1 else f"{', '.join(off[:-1])} and {off[-1]}"
        said += (f" {names} {'is' if len(off) == 1 else 'are'} turned off in its Google Cloud project, so the tools "
                 f"can't use {'it' if len(off) == 1 else 'them'} yet.")
    return said


def outcome(label: str, given: set[str], client: dict, did: str, notes: list[str] | None = None) -> Outcome:
    """Check each API and say how it went."""
    tried, email = check(label, given, client)
    return Outcome(label, "done", summary(did, email, tried), email, tried, notes or [])


def recheck(label: str, client: dict) -> Outcome:
    """Check a signed-in account again, e.g. after turning an API on: no new sign-in needed. Raises ValueError when
    it isn't signed in."""
    try:
        given = granted(label)
    except RuntimeError as e:
        raise ValueError(str(e)) from None
    return outcome(label, given, client, f"Checked the {label} Google account")
