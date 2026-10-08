"""Google Workspace tool sets: gmail, calendar, chat, docs, sheets, slides and drive, each loadable on its own, or
all of them as the ``google`` group.

    from ai_armory.toolsets.google import Account, GoogleSettings, configure

    configure(GoogleSettings(accounts=[Account("work", chat=True), Account("personal")], token_dir="~/tokens"))
    toolsets = ai_armory.load_many(["google"])

Without ``configure`` the accounts are read from the Google config file (see settings.py). Sends and deletes are
drafted first and confirmed by a second tool the host gates; ``pending`` and ``approve`` are for the host (see
drafts.py). This module itself needs no Google libraries.
"""

from ai_armory.toolsets.google.drafts import Draft, approve, discard, pending
from ai_armory.toolsets.google.settings import Account, GoogleSettings, configure, current

SUBSETS = ("gmail", "calendar", "chat", "docs", "sheets", "slides", "drive")

__all__ = ["SUBSETS", "Account", "Draft", "GoogleSettings", "approve", "configure", "current", "discard", "pending"]
