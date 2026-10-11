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


# ── Signing in from a host's web page ───────────────────────────

CLIENT = {"installed": {"client_id": "123456-abc.apps.googleusercontent.com", "client_secret": "s",
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token"}}
BACK = "http://127.0.0.1:8787/callback"


def query(url):
    from urllib.parse import parse_qs, urlparse

    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def test_a_web_sign_in_asks_for_its_step_with_pkce_and_comes_back_to_the_host(google_accounts):
    url, state, begun = auth.begin("work", 0, CLIENT, BACK, login_hint="me@example.com")
    q = query(url)
    assert q["redirect_uri"] == BACK and q["state"] == state and q["login_hint"] == "me@example.com"
    assert q["code_challenge_method"] == "S256" and q["code_challenge"] and begun.verifier
    assert q["access_type"] == "offline" and q["prompt"] == "consent"
    assert set(q["scope"].split()) == set(SCOPES + CHAT_SCOPES) and begun.scopes == SCOPES + CHAT_SCOPES
    assert auth.next_step("work", 0) == "with Google Chat, but without reading messages"
    assert auth.next_step("work", 2) == auth.next_step("personal", 0) == ""
    with pytest.raises(ValueError, match="no sign-in step 1"):
        auth.begin("personal", 1, CLIENT, BACK)


def test_completing_a_web_sign_in_saves_the_token_and_says_what_it_means(google_accounts, monkeypatch):
    from google_auth_oauthlib.flow import Flow

    seen = {}

    def fetch_token(self, code):
        seen.update(code=code, verifier=self.code_verifier, redirect=self.redirect_uri)
        self.oauth2session.token = {"access_token": "a", "refresh_token": "r", "expires_at": 4102444800,
                                    "scope": [s for s in SCOPES if "gmail" not in s]}

    monkeypatch.setattr(Flow, "fetch_token", fetch_token)
    _, _, begun = auth.begin("personal", 0, CLIENT, BACK)
    given, notes = auth.complete(begun, "4/0code")
    assert seen == {"code": "4/0code", "verifier": begun.verifier, "redirect": BACK}
    assert given == {s for s in SCOPES if "gmail" not in s} and google_accounts.token_path("personal").exists()
    assert notes == ["Not allowed, so the tools can't: read mail; write and send email; change mail."]


def test_what_a_sign_in_means_comes_back_as_lines_not_prints(google_accounts, capsys):
    lines = auth.after_sign_in("work", 2, SCOPES, set(SCOPES))  # the last step: no Chat
    assert lines == ["chat_unread will read Chat's notification emails instead. Turn those on in Google Chat's "
                     "settings."]
    assert json.loads(google_accounts.chat_state_path("work").read_text())["status"] == "blocked"
    assert capsys.readouterr().out == ""


def test_google_refusals_say_what_happened():
    assert auth.refusal("access_denied") == auth.REFUSED["cancelled"]
    assert auth.refusal("admin_policy_enforced") == auth.REFUSED["blocked"]
    assert auth.refusal("server_error") == "Google refused the sign-in (server_error)."



def test_a_web_client_must_list_the_redirect_uri(google_accounts):
    web = {"web": CLIENT["installed"] | {"redirect_uris": ["https://elsewhere.example/callback"]}}
    with pytest.raises(ValueError, match=f"won't send this sign-in back to {BACK}"):
        auth.begin("personal", 0, web, BACK)
    assert auth.begin("personal", 0, {"web": web["web"] | {"redirect_uris": [BACK]}}, BACK)[0]


def test_finishing_says_how_a_sign_in_went(google_accounts, monkeypatch):
    from ai_armory.toolsets.google import checks

    _, _, begun = auth.begin("personal", 0, CLIENT, BACK)
    refused = auth.finish(begun, error="access_denied")
    assert (refused.state, refused.said) == ("refused", auth.REFUSED["cancelled"] + " Nothing changed for the "
                                                                                     "personal account.")
    assert auth.finish(begun).state == "failed"
    monkeypatch.setattr(auth, "complete", lambda b, code: (set(SCOPES), ["a note"]))
    monkeypatch.setattr(checks, "check", lambda label, given, client: ([checks.Check("Gmail", "ok")], "me@example.org"))
    done = auth.finish(begun, "4/0code")
    assert done == checks.Outcome("personal", "done", "Signed the personal Google account in as me@example.org.",
                                  "me@example.org", [checks.Check("Gmail", "ok")], ["a note"])

    def broken(b, code):
        raise ValueError("invalid_grant: Bad Request")

    monkeypatch.setattr(auth, "complete", broken)
    failed = auth.finish(begun, "4/0code")
    assert failed.state == "failed" and "invalid_grant" in failed.said
