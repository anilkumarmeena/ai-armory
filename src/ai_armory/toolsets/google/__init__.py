"""Google Workspace tool sets (gmail, calendar, chat, docs, sheets, slides, drive), loadable singly or as ``google``.

    from ai_armory.toolsets.google import Account, GoogleSettings, configure

    configure(GoogleSettings(accounts=[Account("work", chat=True), Account("personal")], token_dir="~/tokens"))
    toolsets = ai_armory.load_many(["google"])

Without ``configure``, accounts come from the config file (settings.py). Sends and deletes are drafted, then confirmed
by a host-gated tool; ``pending`` and ``approve`` are the host's side (drafts.py). Needs no Google libraries itself.
"""

from ai_armory.toolsets.google.drafts import Draft, approve, discard, pending
from ai_armory.toolsets.google.settings import Account, GoogleSettings, configure, current

__all__ = ["Account", "Draft", "GoogleSettings", "approve", "configure", "current", "discard", "pending"]
