"""Which Google accounts the Google tool sets use, and where their sign-ins are kept.

Each account has a label (what the tools take as ``account``), optionally its email, a description of what it's
for, and whether it has Google Chat; one is the default, which gets whatever is made without naming an account.
Each account's OAuth token is ``<token_dir>/google-<label>.json``.

A host running the tools in-process calls ``configure(GoogleSettings(...))`` before loading the tool sets. Otherwise
they're read once from a TOML file: ``$AI_ARMORY_GOOGLE_CONFIG``, else ``~/.config/ai-armory/google.toml``::

    token_dir = "~/.config/ai-armory/google"   # default: beside this file
    default = "work"
    timezone = "Europe/London"

    [[accounts]]
    label = "work"
    email = "me@example.com"
    description = "for work"
    chat = true

    [[accounts]]
    label = "personal"
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CONFIG_ENV = "AI_ARMORY_GOOGLE_CONFIG"
DEFAULT_CONFIG = Path("~/.config/ai-armory/google.toml")
LABEL = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")  # it names files, so nothing that could leave the folder
NO_ACCOUNTS = ("No Google account is set up for these tools. List the accounts in the Google config file "
               f"(${CONFIG_ENV}, or {DEFAULT_CONFIG}) and sign each one in.")


@dataclass(frozen=True)
class Account:
    label: str
    email: str = ""
    description: str = ""  # what it's for, e.g. "for work", which the tools' schemas pass on to the model
    chat: bool = False  # a Workspace account whose sign-in also asks for Google Chat


@dataclass(frozen=True)
class GoogleSettings:
    accounts: tuple[Account, ...] = ()
    default: str = ""  # the label of the account that gets whatever is made without naming one; "" is the first
    token_dir: Path = Path("~/.config/ai-armory/google")
    timezone: str = "UTC"  # for calendar events and the times the tools show
    user_name: str = "the user"  # how tool results name the person they work for
    # What to tell the user when an account needs signing in (again); {label} is filled in.
    sign_in_command: str = "python -m ai_armory.toolsets.google login {label}"
    client_secret: str = ""  # the OAuth client JSON's path, used only by the sign-in command
    draft_minutes: int = 10  # how long a draft (a Chat message, an invite, a deletion) waits to be confirmed
    # True: a send or delete goes through only for a draft the host approved with approve(draft_id) after asking
    # the user. False: the MCP client's own permission prompt for the confirming tool is trusted instead.
    host_approval: bool = True
    image_folder: str = "AI Armory doc images"  # the Drive folder holding images from this machine for Google Docs
    blocked_paths: tuple[str, ...] = ()  # more folders or files docs_insert_image must never upload from
    extra: dict[str, Any] = field(default_factory=dict, compare=False)  # unknown keys from the file, ignored

    def __post_init__(self) -> None:
        object.__setattr__(self, "accounts", tuple(self.accounts))
        object.__setattr__(self, "token_dir", Path(self.token_dir).expanduser())
        object.__setattr__(self, "blocked_paths", tuple(self.blocked_paths))
        seen: set[str] = set()
        emails: dict[str, str] = {}
        for a in self.accounts:
            if not LABEL.fullmatch(a.label) or a.label == "all":
                raise ValueError(f"'{a.label}' can't be an account label: use up to 32 lowercase letters, digits, "
                                 "dashes and underscores, starting with a letter or digit, and not 'all'.")
            if a.label in seen:
                raise ValueError(f"There are two {a.label} accounts.")
            seen.add(a.label)
            if a.email and (other := emails.get(a.email.lower())):
                raise ValueError(f"{a.email} is both the {other} and the {a.label} account.")
            emails[a.email.lower()] = a.label
        if self.default and self.default not in seen:
            raise ValueError(f"The default account {self.default!r} isn't one of the accounts.")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"Unknown time zone: {self.timezone}") from None

    @property
    def labels(self) -> list[str]:
        return [a.label for a in self.accounts]

    @property
    def chat_labels(self) -> list[str]:
        return [a.label for a in self.accounts if a.chat]

    @property
    def default_label(self) -> str:
        return self.default or next(iter(self.labels), "")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def get(self, label: str) -> Account | None:
        return next((a for a in self.accounts if a.label == label), None)

    def resolve(self, name: str) -> str:
        """The label `name` means: a label in any capitals, or an account's email. Anything else comes back as is."""
        key = name.strip().lower()
        return next((a.label for a in self.accounts if key in (a.label, a.email.lower())), name)

    def token_path(self, label: str) -> Path:
        return self.token_dir / f"google-{label}.json"

    def chat_state_path(self, label: str) -> Path:
        return self.token_dir / f"google-{label}-chat.json"

    def sign_in(self, label: str) -> str:
        return self.sign_in_command.format(label=label)

    def describe(self) -> str:
        """One line on the accounts, for a host's prompt or a tool schema."""
        if not self.accounts:
            return NO_ACCOUNTS
        each = [f'"{a.label}"' + (f" ({a.email})" if a.email else "") + (f": {a.description.rstrip('.')}"
                                                                          if a.description else "")
                for a in self.accounts]
        return f"The Google accounts, by label: {'; '.join(each)}."

    @classmethod
    def from_dict(cls, data: dict[str, Any], base: Path | None = None) -> GoogleSettings:
        """Settings from a parsed config file; a relative token_dir is taken from `base`, the file's folder."""
        known = {f.name for f in fields(cls)} - {"extra", "accounts"}
        accounts = []
        for item in data.get("accounts") or []:
            if not isinstance(item, dict) or not isinstance(item.get("label"), str):
                raise ValueError("Each [[accounts]] entry needs a label.")
            accounts.append(Account(label=item["label"], email=str(item.get("email", "")).strip(),
                                    description=str(item.get("description", "")).strip(),
                                    chat=bool(item.get("chat", False))))
        values = {k: v for k, v in data.items() if k in known}
        if "token_dir" in values:
            token_dir = Path(values["token_dir"]).expanduser()
            values["token_dir"] = token_dir if token_dir.is_absolute() or base is None else base / token_dir
        elif base is not None:
            values["token_dir"] = base / "google"
        if "blocked_paths" in values:
            values["blocked_paths"] = tuple(values["blocked_paths"])
        return cls(accounts=tuple(accounts), extra={k: v for k, v in data.items() if k not in known | {"accounts"}},
                   **values)

    @classmethod
    def from_file(cls, path: Path) -> GoogleSettings:
        path = path.expanduser()
        with path.open("rb") as f:
            return cls.from_dict(tomllib.load(f), base=path.parent)


_current: GoogleSettings | None = None


def configure(settings: GoogleSettings | None) -> None:
    """Sets the accounts for every Google tool set, before they're loaded: their schemas list the accounts as they
    are when loaded. None goes back to reading the config file."""
    global _current
    _current = settings


def config_path() -> Path:
    return Path(os.environ.get(CONFIG_ENV, "") or DEFAULT_CONFIG).expanduser()


def current() -> GoogleSettings:
    """The configured settings; else the config file's, read once; else none at all, so the tools still load and
    say how to set them up."""
    global _current
    if _current is None:
        path = config_path()
        _current = GoogleSettings.from_file(path) if path.exists() else GoogleSettings()
    return _current
