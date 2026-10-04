"""What the Google tool sets share: OAuth scopes, building API clients from each account's token, turning the API
errors only a fix outside the tools can clear into what to do, reading several accounts at once, and declaring
tools whose schemas list the configured accounts.

The Google client libraries are imported here, so a tool set whose extra isn't installed fails to load with an
ImportError and the others are unaffected.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import re
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, TypeVar

import anyio.to_thread
import googleapiclient  # noqa: F401  (a missing extra shows up when the tool set is loaded)

from ai_armory.core import ToolError, ToolSet
from ai_armory.toolsets.google.settings import NO_ACCOUNTS, GoogleSettings, current

log = logging.getLogger("ai_armory.google")
T = TypeVar("T")

CALENDAR = "https://www.googleapis.com/auth/calendar"
GMAIL_READ = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"  # drafts; nothing here sends mail
GMAIL_MODIFY = "https://www.googleapis.com/auth/gmail.modify"
SHEETS = "https://www.googleapis.com/auth/spreadsheets"
DRIVE_METADATA = "https://www.googleapis.com/auth/drive.metadata.readonly"  # only to find sheets and docs by name
DOCS_READ = "https://www.googleapis.com/auth/documents.readonly"
DOCS = "https://www.googleapis.com/auth/documents"  # changing docs (docs_write.py); covers reading too
# Only the files the tools make themselves: images from this machine, held in Drive while a doc fetches them.
DRIVE_FILE = "https://www.googleapis.com/auth/drive.file"
SCOPES = [CALENDAR, GMAIL_READ, GMAIL_COMPOSE, GMAIL_MODIFY, SHEETS, DRIVE_METADATA, DOCS_READ, DOCS, DRIVE_FILE]
# Google Chat, asked for only by accounts marked for Chat: read-only, plus create-only for sending and for starting
# a DM, and the user's own read state for marking spaces read. Never chat.messages (full read and write),
# chat.delete, chat.import or the admin-approved chat.app.* scopes.
CHAT_SPACES = "https://www.googleapis.com/auth/chat.spaces.readonly"
CHAT_READ = "https://www.googleapis.com/auth/chat.messages.readonly"  # the one a Workspace admin is likeliest to block
CHAT_MEMBERS = "https://www.googleapis.com/auth/chat.memberships.readonly"
CHAT_READSTATE = "https://www.googleapis.com/auth/chat.users.readstate.readonly"
CHAT_SEND = "https://www.googleapis.com/auth/chat.messages.create"
CHAT_CREATE = "https://www.googleapis.com/auth/chat.spaces.create"  # spaces.setup, only to start a DM
CHAT_MARK = "https://www.googleapis.com/auth/chat.users.readstate"  # changes only the user's own read state
DIRECTORY = "https://www.googleapis.com/auth/directory.readonly"  # Chat names people only by id; People names them
CHAT_SCOPES = [CHAT_SPACES, CHAT_READ, CHAT_MEMBERS, CHAT_READSTATE, CHAT_SEND, CHAT_CREATE, CHAT_MARK, DIRECTORY]
# What a token without each scope can't do, for the sign-in-again message.
ADDED_SCOPES = {
    CALENDAR: "use Google Calendar", GMAIL_READ: "read mail", GMAIL_COMPOSE: "write email drafts",
    GMAIL_MODIFY: "change mail", SHEETS: "read and edit Google Sheets",
    DRIVE_METADATA: "find Google Sheets and Docs by name", DOCS_READ: "read Google Docs", DOCS: "edit Google Docs",
    DRIVE_FILE: "put images from this machine into Google Docs",
    CHAT_SPACES: "see Google Chat", CHAT_READ: "read Google Chat messages",
    CHAT_MEMBERS: "see who's in a Google Chat space", CHAT_READSTATE: "tell what's unread in Google Chat",
    CHAT_SEND: "send Google Chat messages", CHAT_CREATE: "start new Google Chat conversations",
    CHAT_MARK: "mark Google Chat spaces as read", DIRECTORY: "name people in Google Chat",
}
# A token granted the broader scope needs no sign-in again for the narrower one.
BROADER = {DOCS_READ: DOCS, GMAIL_READ: GMAIL_MODIFY}
MAX_PARALLEL = 6  # accounts read at once by an "all" search


def settings() -> GoogleSettings:
    return current()


# ── Tokens and clients ──────────────────────────────────────────

def token_path(label: str) -> Path:
    return settings().token_path(label)


def write_private(path: Path, text: str) -> None:
    """Replaces `path` atomically with `text`, readable only by its owner: tokens and what sign-in found."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def granted(label: str) -> set[str]:
    """The scopes the account's token holds, read without building a client."""
    path = token_path(label)
    if not path.exists():
        raise RuntimeError(not_connected(label))
    scopes = json.loads(path.read_text()).get("scopes") or []
    return set(scopes.split() if isinstance(scopes, str) else scopes)


def not_connected(label: str) -> str:
    return f"The {label} Google account is not signed in yet. Sign it in with: {settings().sign_in(label)}"


def has_scope(scopes: set[str], needs: str) -> bool:
    return needs in scopes or (needs in BROADER and BROADER[needs] in scopes)


# Built per call: googleapiclient clients are not thread-safe and the tools run in threads.
def service(label: str, api: str, version: str, needs: str | None = None):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    path = token_path(label)
    if not path.exists():
        raise RuntimeError(not_connected(label))
    # The token's own scopes, not SCOPES: refreshing with scopes it was never granted fails outright.
    creds = Credentials.from_authorized_user_file(str(path))
    if needs and not creds.has_scopes([needs]) and not (needs in BROADER and creds.has_scopes([BROADER[needs]])):
        raise RuntimeError(sign_in_again(label, needs))
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        write_private(path, creds.to_json())
    return build(api, version, credentials=creds, cache_discovery=False)


def sign_in_again(label: str, scope: str = GMAIL_MODIFY) -> str:
    return (f"The {label} Google account's sign-in doesn't let these tools {ADDED_SCOPES.get(scope, 'do that')}. "
            f"Sign it in again with: {settings().sign_in(label)}")


@contextmanager
def api_errors(label: str, scope: str):
    """Turns the two API errors only a fix outside the tools clears, a missing scope or an API left off, into what
    to do."""
    from googleapiclient.errors import HttpError

    try:
        yield
    except HttpError as e:
        text = str(e)
        if e.resp.status == 403 and "insufficient" in text.lower():
            raise RuntimeError(sign_in_again(label, scope)) from e
        if e.resp.status == 403 and (api := re.search(r"(\w[\w ]* API) has not been used", text)):
            raise RuntimeError(f"The {api[1]} is turned off in the Google Cloud project these tools sign in with. "
                               "Turn it on in the Cloud console, under APIs and Services, then try again.") from e
        raise


def only(requests: list[dict], allowed: set[str], product: str) -> None:
    """Refuses a batchUpdate with any request kind outside `allowed`, the few that tool is meant to send."""
    if extra := {kind for request in requests for kind in request} - allowed:
        raise ValueError(f"These tools don't send {', '.join(sorted(extra))} to {product}.")


# ── Several accounts ────────────────────────────────────────────

def accounts_for(account: str) -> list[str]:
    """The labels `account` names: every configured account for "all", signed in or not."""
    return settings().labels if account == "all" else [account]


def connected(labels: list[str] | None = None) -> list[str]:
    """Those of `labels` (default: every account) that are signed in."""
    return [a for a in (settings().labels if labels is None else labels) if token_path(a).exists()]


def each(labels: list[str], read: Callable[[str], T]) -> tuple[list[T], list[str]]:
    """`read(label)` for each label, at most MAX_PARALLEL at once, and the notes. With several, one that fails (not
    signed in, its access withdrawn, Google down for it) is left out and said in the notes, so the others still
    answer; if they all fail, that's raised. One label's failure is raised as it is."""
    if len(labels) < 2:
        return [read(label) for label in labels], []

    def safely(label: str) -> T | Exception:
        try:
            return read(label)
        except Exception as e:
            log.warning("the %s Google account failed: %s", label, e)
            return e

    with ThreadPoolExecutor(min(MAX_PARALLEL, len(labels))) as pool:
        results = list(pool.map(safely, labels))
    failed = [(label, r) for label, r in zip(labels, results) if isinstance(r, Exception)]
    if len(failed) == len(labels):
        raise RuntimeError("Every Google account failed. " + " ".join(f"{label}: {e}" for label, e in failed))
    notes = [f"Left out the {label} account: {e}" for label, e in failed]
    return [r for r in results if not isinstance(r, Exception)], notes


def across(account: str, read: Callable[[str], list[dict]]) -> tuple[list[dict], list[str]]:
    """`read(label)` for each account `account` names, as one list, and the notes on any left out (see `each`)."""
    found, notes = each(accounts_for(account), read)
    return [item for items in found for item in items], notes


def noted(key: str, items: list[dict], notes: list[str]) -> list[dict] | dict:
    """The items as they are, or with notes on accounts left out, {key: items, "notes": notes}."""
    return {key: items, "notes": notes} if notes else items


def account_email(label: str) -> str:
    """The address the account is signed in as, from its Gmail profile: no permission beyond reading mail."""
    return service(label, "gmail", "v1").users().getProfile(userId="me").execute().get("emailAddress", "")


def drive_find(account: str, mime_type: str, name: str, max_results: int) -> list[dict]:
    """Files of one Google type whose name contains `name`, newest change first, from My Drive, shared with the
    user and shared drives. Drive metadata only: names and details, never contents."""
    query = [f"mimeType='{mime_type}'", "trashed=false"]
    if name.strip():
        query.append("name contains '" + name.strip().replace("\\", "\\\\").replace("'", "\\'") + "'")
    drive = service(account, "drive", "v3", needs=DRIVE_METADATA)
    with api_errors(account, DRIVE_METADATA):
        files = drive.files().list(
            q=" and ".join(query), orderBy="modifiedTime desc", pageSize=max(1, min(25, max_results)),
            corpora="allDrives", includeItemsFromAllDrives=True, supportsAllDrives=True,
            fields="files(id,name,modifiedTime,webViewLink,owners(displayName,me))",
        ).execute().get("files", [])
    return [{
        "account": account, "id": f["id"], "name": f["name"], "modified": f.get("modifiedTime"),
        "owner": next(("you" if o.get("me") else o.get("displayName") for o in f.get("owners", [])), None),
        "url": f.get("webViewLink"),
    } for f in files]


# ── Declaring tools ─────────────────────────────────────────────

# Placeholders for an account field in a tool's properties, filled in from the settings when the tool set is
# built, so the schema lists the configured accounts. Other keys given with one (a description) are kept.
ACCOUNT = {"$account": "one"}  # one account, defaulting to the default account
ACCOUNT_OR_ALL = {"$account": "all"}  # one account or "all", defaulting to "all"
CHAT_ACCOUNT = {"$account": "chat"}  # one account, defaulting to the first with Chat
ANY_ACCOUNT = {"$account": "any"}  # one account, no default


def chat_default(s: GoogleSettings) -> str:
    return next(iter(s.chat_labels or s.labels), "")


def _account_schema(marker: dict, s: GoogleSettings) -> dict:
    extra = {k: v for k, v in marker.items() if k != "$account"}
    if not s.accounts:
        return {"type": "string", "description": NO_ACCOUNTS, **extra}
    kind, labels = marker["$account"], s.labels
    if kind == "all":
        base = {"type": "string", "enum": [*labels, "all"], "default": "all", "description": "Default: all."}
    elif kind == "any":
        base = {"type": "string", "enum": labels}
    else:
        default = chat_default(s) if kind == "chat" else s.default_label
        base = {"type": "string", "enum": labels, "default": default, "description": f"Default: {default}."}
    if len(labels) > 1 and "description" in base:
        base["description"] += " " + s.describe()
    return {**base, **extra}


@dataclass
class Spec:
    name: str  # without the tool set's prefix
    description: str
    properties: dict[str, dict]
    required: list[str]
    fn: Callable[..., Any]
    read_only: bool = False
    needs_confirmation: bool = False


@dataclass
class Specs:
    """The tools of one Google tool set, declared at import and built into a ToolSet once the accounts are known."""

    items: list[Spec] = field(default_factory=list)

    def tool(self, name: str, description: str, properties: dict[str, dict] | None = None,
             required: list[str] | None = None, *, read_only: bool = False, needs_confirmation: bool = False):
        def register(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.items.append(Spec(name, description, properties or {}, required or [], fn, read_only,
                                   needs_confirmation))
            return fn

        return register


_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}


def _problem(properties: dict[str, dict], required: list[str], args: dict) -> str | None:
    """Clients don't all check inputs against the schema, so the basics are checked before anything runs."""
    for key in required:
        if key not in args:
            return f"missing required field '{key}'"
    for key, value in args.items():
        spec = properties.get(key)
        if spec is None:
            return f"unknown field '{key}'"
        expected = _TYPES.get(spec.get("type", "")) if isinstance(spec.get("type"), str) else None
        if expected and (not isinstance(value, expected) or (spec["type"] in ("integer", "number")
                                                             and isinstance(value, bool))):
            return f"field '{key}' must be {spec['type']}"
        if "enum" in spec and value not in spec["enum"]:
            return f"field '{key}' must be one of {spec['enum']}"
    return None


def _handler(spec: Spec, properties: dict[str, dict], s: GoogleSettings):
    accounts = {k for k, v in spec.properties.items() if "$account" in v}
    sync = not inspect.iscoroutinefunction(spec.fn)

    async def handler(args: dict[str, Any]) -> Any:
        if accounts and not s.accounts:
            raise ToolError(NO_ACCOUNTS)
        # An account may be named by its email or its label in other capitals; one left out gets its default.
        given = {k: s.resolve(v) if k in accounts and isinstance(v, str) else v for k, v in (args or {}).items()}
        given = {k: p["default"] for k, p in properties.items() if "default" in p and k not in spec.required} | given
        if problem := _problem(properties, spec.required, given):
            raise ToolError(f"Invalid input: {problem}.")
        if sync:
            return await anyio.to_thread.run_sync(partial(spec.fn, **given))
        return await spec.fn(**given)

    return handler


def build(name: str, description: str, specs: Specs | list[Specs]) -> ToolSet:
    """The tool set, with each account field's schema listing the accounts as they're configured now."""
    s = settings()
    toolset = ToolSet(name, description)
    for spec in [i for group in (specs if isinstance(specs, list) else [specs]) for i in group.items]:
        properties = {k: _account_schema(v, s) if "$account" in v else v for k, v in spec.properties.items()}
        schema: dict[str, Any] = {"properties": properties, "additionalProperties": False}
        if spec.required:
            schema["required"] = spec.required
        toolset.tool(spec.name, spec.description, schema, read_only=spec.read_only,
                     needs_confirmation=spec.needs_confirmation)(_handler(spec, properties, s))
    return toolset
