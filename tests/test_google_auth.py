"""Signing accounts in (auth.py and the package's command), with Google's sign-in faked."""

import json

import pytest

from ai_armory.toolsets.google import __main__ as command
from ai_armory.toolsets.google import auth
from ai_armory.toolsets.google.common import CHAT_READ, CHAT_SCOPES, CHAT_SPACES, SCOPES


class Creds:
    """What a sign-in returns, holding made-up values."""

    def __init__(self, scopes):
        self.granted_scopes = scopes
        self.scopes = scopes

    def to_json(self):
        return json.dumps({"token": "made-up", "refresh_token": "made-up", "scopes": self.scopes})


def test_only_accounts_with_chat_are_asked_for_it_with_fallbacks(google_accounts):
    assert auth.attempts("personal") == [("", SCOPES)]
    steps = auth.attempts("work")
    assert [s for s, _ in steps] == ["with Google Chat", "with Google Chat, but without reading messages",
                                     "without Google Chat"]
    assert steps[0][1] == SCOPES + CHAT_SCOPES and CHAT_READ not in steps[1][1] and steps[2][1] == SCOPES


def test_the_token_keeps_the_scopes_granted_and_only_its_owner_can_read_it(google_accounts):
    given = auth.save("personal", Creds(" ".join(SCOPES[:2])))
    path = google_accounts.token_path("personal")
    assert given == set(SCOPES[:2]) and json.loads(path.read_text())["scopes"] == sorted(SCOPES[:2])
    assert path.stat().st_mode & 0o777 == 0o600


def test_a_refused_chat_sign_in_tries_less_and_records_the_block(google_accounts, capsys):
    asked = []

    def sign(scopes, secret):
        asked.append(len(scopes))
        if CHAT_SPACES in scopes:
            raise auth.Refused("blocked")
        return Creds(scopes)

    assert auth.connect("work", "client.json", ask=lambda q: "y", sign=sign) == 0
    assert asked == [len(SCOPES + CHAT_SCOPES), len(SCOPES) + len(CHAT_SCOPES) - 1, len(SCOPES)]
    state = json.loads(google_accounts.chat_state_path("work").read_text())
    assert state["status"] == "blocked"
    out = capsys.readouterr().out
    assert "Connected the work account without Google Chat." in out and "made-up" not in out


def test_a_cancelled_sign_in_changes_nothing(google_accounts, capsys):
    def sign(scopes, secret):
        raise auth.Refused("cancelled")

    assert auth.connect("personal", "client.json", ask=lambda q: "n", sign=sign) == 1
    assert not google_accounts.token_path("personal").exists()
    assert "Nothing changed" in capsys.readouterr().out


def test_the_command_lists_accounts_and_needs_a_client_secret_to_sign_in(google_accounts, capsys, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    assert command.main([]) == 0
    out = capsys.readouterr().out
    assert "work" in out and "default, Google Chat, not signed in" in out and "for work" in out
    with pytest.raises(SystemExit, match="There's no nobody account"):
        command.main(["login", "nobody"])
    with pytest.raises(SystemExit, match="OAuth client JSON"):
        command.main(["login", "work"])
