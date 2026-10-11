"""Signing accounts in from a host's own web page (web.py), with Google's token exchange faked."""

import asyncio
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
from google_auth_oauthlib.flow import Flow

from ai_armory.toolsets.google import checks, web
from ai_armory.toolsets.google.checks import Check
from ai_armory.toolsets.google.common import SCOPES

PORT = 8787
CALLBACK = "/google/callback"
LOCAL = f"http://127.0.0.1:{PORT}"
REMOTE = "https://mac.tailnet.ts.net"
DESKTOP = {"installed": {"client_id": "123456-abc.apps.googleusercontent.com", "client_secret": "s",
                         "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                         "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["http://localhost"]}}
WEB = {"web": DESKTOP["installed"] | {"redirect_uris": [REMOTE + CALLBACK]}}


@pytest.fixture
def signins(google_accounts, monkeypatch):
    """Sign-ins with a Desktop app client (swap it with `.client_json`), and each API check passing."""
    monkeypatch.setattr(checks, "check", lambda label, given, client: ([Check("Gmail", "ok")], f"{label}@example.org"))
    s = web.WebSignIns(lambda: s.client_json, CALLBACK, PORT, wait_seconds=0.05, app="My host")
    s.client_json = DESKTOP
    return s


def query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def signs_in(monkeypatch) -> list[str]:
    """Google exchanges any code for a token with every scope asked for; returns the codes it saw."""
    codes = []

    def fetch_token(flow, code):
        codes.append(code)
        flow.oauth2session.token = {"access_token": "a", "refresh_token": "r", "expires_at": 4102444800,
                                    "scope": flow.oauth2session.scope}

    monkeypatch.setattr(Flow, "fetch_token", fetch_token)
    return codes


# ── Where a sign-in comes back ──────────────────────────────────

def test_a_desktop_client_comes_back_to_the_host_on_its_own_computer(signins):
    started = signins.start("personal", 0, LOCAL, "me@example.org")
    q = query(started["url"])
    assert q["redirect_uri"] == LOCAL + CALLBACK and q["state"] == started["state"]
    assert q["code_challenge_method"] == "S256" and q["login_hint"] == "me@example.org"
    assert set(q["scope"].split()) == set(SCOPES)
    assert started["paste"] is False and started["next"] == ""


def test_from_another_device_a_desktop_client_needs_the_address_pasted(signins):
    started = signins.start("work", 0, REMOTE)
    assert query(started["url"])["redirect_uri"] == LOCAL + CALLBACK and started["paste"] is True
    assert started["next"] == "with Google Chat, but without reading messages"


def test_a_web_client_comes_back_to_the_page_wherever_it_was_opened(signins):
    signins.client_json = WEB
    started = signins.start("personal", 0, REMOTE)
    assert query(started["url"])["redirect_uri"] == REMOTE + CALLBACK and not started["paste"]
    with pytest.raises(ValueError, match=f"won't send this sign-in back to {LOCAL}{CALLBACK}"):
        signins.start("personal", 0, LOCAL)


def test_starting_needs_a_client_and_the_pages_own_address(signins):
    with pytest.raises(ValueError, match="usual address"):
        signins.start("personal", 0, "http://evil.example")  # plain http only on this computer
    with pytest.raises(ValueError, match="usual address"):
        signins.start("personal", 0, REMOTE + "/elsewhere")
    signins.client_json = {}
    with pytest.raises(ValueError, match="no Google OAuth client yet"):
        signins.start("personal", 0, LOCAL)
    assert signins._pending == {}


def test_setup_says_what_the_client_needs(signins):
    found = signins.setup(REMOTE)
    assert (found["kind"], found["missing"], found["paste"]) == ("installed", [], True)
    assert found["apis"][0]["url"].endswith("gmail.googleapis.com?project=123456")
    assert signins.setup(LOCAL)["paste"] is False
    signins.client_json = WEB
    found = signins.setup(REMOTE)
    assert found["kind"] == "web" and found["missing"] == [f"http://localhost:{PORT}{CALLBACK}"]
    signins.client_json = {}
    assert signins.setup(REMOTE)["kind"] == ""


# ── Coming back ─────────────────────────────────────────────────

def test_coming_back_saves_the_sign_in_once_and_runs_the_hosts_hook(signins, google_accounts, monkeypatch):
    codes, seen = signs_in(monkeypatch), []
    signins.on_done = lambda o: (seen.append(o.email), o.notes.append("host note"))
    state = signins.start("personal", 0, LOCAL)["state"]
    assert signins.status(state) == {"state": "waiting"}
    page = asyncio.run(signins.callback({"state": state, "code": "the-code"}))
    assert "<h1>Signed in</h1>" in page and "personal@example.org" in page and "host note" in page
    assert codes == ["the-code"] and seen == ["personal@example.org"]
    assert json.loads(google_accounts.token_path("personal").read_text())["refresh_token"] == "r"
    assert signins.status(state)["notes"] == ["host note"]
    again = asyncio.run(signins.finish(state, "the-code"))  # a replayed callback does nothing
    assert (again.state, again.said, codes) == ("failed", web.OLD, ["the-code"])


def test_a_stray_or_late_callback_changes_nothing(signins, google_accounts, monkeypatch):
    codes = signs_in(monkeypatch)
    assert asyncio.run(signins.finish("made-up", "code")).said == web.OLD
    state = signins.start("personal", 0, LOCAL)["state"]
    signins._pending[state].started -= signins.minutes * 60 + 1
    assert asyncio.run(signins.finish(state, "code")).said == web.OLD
    assert codes == [] and not google_accounts.token_path("personal").exists()
    assert signins.status(state) == {"state": "old", "said": web.OLD} and signins._pending == {}


def test_a_failing_hook_still_answers_the_page(signins, monkeypatch):
    signs_in(monkeypatch)

    def broken(o):
        raise RuntimeError("database is locked")

    signins.on_done = broken
    state = signins.start("personal", 0, LOCAL)["state"]
    result = asyncio.run(signins.finish(state, "code"))
    assert result.state == "failed" and "database is locked" in result.said
    assert signins.status(state)["state"] == "failed"


def test_another_device_finishes_by_pasting_the_address_google_sent_it_to(signins, monkeypatch):
    codes = signs_in(monkeypatch)
    state = signins.start("personal", 0, REMOTE)["state"]
    with pytest.raises(ValueError, match="copy the whole address"):
        asyncio.run(signins.finish_pasted("it didn't load"))
    address = f"{LOCAL}{CALLBACK}?state={state}&code=4%2F0Ab&scope=x"
    asyncio.run(signins.finish_pasted(address))
    assert signins.status(state)["state"] == "done" and codes == ["4/0Ab"]
    with pytest.raises(ValueError, match="old or was already used"):
        asyncio.run(signins.finish_pasted(address))


def test_the_page_hears_the_moment_a_sign_in_comes_back(signins, monkeypatch):
    signins.wait_seconds = 5
    signs_in(monkeypatch)
    state = signins.start("personal", 0, LOCAL)["state"]

    async def asked_then_answered() -> tuple[dict, float]:
        asking, began = asyncio.create_task(signins.wait(state)), time.monotonic()
        await asyncio.sleep(0.05)
        await signins.finish(state, "code")
        return await asking, time.monotonic() - began

    found, took = asyncio.run(asked_then_answered())
    assert found["state"] == "done" and took < 1  # not the whole wait


def test_a_refusal_is_answered_on_the_page_without_a_token(signins, google_accounts):
    state = signins.start("work", 0, LOCAL)["state"]
    page = asyncio.run(signins.callback({"state": state, "error": "access_denied"}))
    assert "<h1>Not signed in</h1>" in page and "Nothing changed for the work account" in page
    assert not google_accounts.token_path("work").exists()
