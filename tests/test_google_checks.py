"""Checking each Google API after a sign-in (checks.py) and the page a host shows for it (signin_page.py)."""

import json

import httplib2
from googleapiclient.errors import HttpError

import pytest

from ai_armory.toolsets.google import checks, common, signin_page
from ai_armory.toolsets.google.checks import Check, Outcome

CLIENT = {"web": {"client_id": "123456-abc.apps.googleusercontent.com", "client_secret": "s"}}


def google_answer(status, reason="", message="Something went wrong. More detail here."):
    body = {"error": {"code": status, "message": message, "errors": [{"reason": reason}] if reason else []}}
    return HttpError(httplib2.Response({"status": status}), json.dumps(body).encode())


class FakeApi:
    """A Google API client whose every call chains, then answers or raises at execute()."""

    def __init__(self, answer):
        self.answer = answer

    def __getattr__(self, name):
        return lambda *args, **kwargs: self

    def execute(self):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_each_api_is_tried_and_one_thats_turned_off_links_to_turning_it_on(monkeypatch):
    answers = {"gmail": {"emailAddress": "me@example.org"}, "drive": {},
               "calendar": google_answer(403, "accessNotConfigured", "Calendar API has not been used in project 123456 "
                                         "before or it is disabled. Enable it by visiting https://console…"),
               "docs": google_answer(404, "notFound"),  # the made-up document: Docs is on
               "sheets": google_answer(500, message="Backend Error. Try later.")}
    monkeypatch.setattr(checks, "service", lambda label, api, version: FakeApi(answers[api]))
    given = {common.GMAIL_READ, common.CALENDAR, common.DRIVE_METADATA, common.DOCS, common.SHEETS}  # Docs: the broader
    found, email = checks.check("personal", given, CLIENT)
    assert email == "me@example.org"
    assert [(c.name, c.state) for c in found] == [("Gmail", "ok"), ("Calendar", "off"), ("Drive", "ok"), ("Docs", "ok"),
                                                  ("Sheets", "error"), ("Slides", "denied")]  # Slides: not allowed
    assert found[1].fix == "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com?project=123456"
    assert found[4].why == "Backend Error."  # Google's first sentence, never its JSON


def test_the_summary_names_what_is_off():
    assert checks.summary("Signed the work Google account in", "me@example.com", [Check("Gmail", "ok")]) == \
        "Signed the work Google account in as me@example.com."
    assert checks.summary("Checked the work Google account", "", [Check("Gmail", "off"), Check("Docs", "off")]) == (
        "Checked the work Google account. Gmail and Docs are turned off in its Google Cloud project, so the tools "
        "can't use them yet.")


def test_a_google_error_is_one_plain_sentence():
    e = google_answer(403, "accessNotConfigured", "Gmail API has not been used in project 1 before or it is disabled. "
                      "Enable it by visiting https://console.developers.google.com/apis/api/gmail.googleapis.com")
    assert common.google_error(e) == "Gmail API has not been used in project 1 before or it is disabled."
    assert common.error_reasons(e) == {"accessNotConfigured"}
    assert common.google_error(ValueError("boom")) == "ValueError: boom" and common.error_reasons(ValueError()) == set()


def test_the_page_shows_each_api_names_the_host_and_escapes_everything():
    o = Outcome("<b>x</b>", "done", "s", "<i>@</i>", [Check("Gmail", "ok"), Check("Calendar", "off", "https://a?b=1&c=2")],
                ["<script>alert(1)</script>"])
    page = signin_page.render(o, app="JARVIS", brand="J.A.R.V.I.S.", where="JARVIS's Google accounts page")
    body = page.split("<body>", 1)[1].split("<script>\ndocument", 1)[0]
    assert "Signed in, almost ready" in body and "Back to JARVIS" in body and "J.A.R.V.I.S." in body
    assert "check again in JARVIS&#x27;s Google accounts page" in body and "Turn on</a>" in body
    assert "<b>x</b>" not in body and "&lt;b&gt;x&lt;/b&gt;" in body and "<i>@</i>" not in body
    assert 'href="https://a?b=1&amp;c=2"' in body and "<script>alert" not in body
    refused = signin_page.render(Outcome("work", "refused", "Google didn't give access."))
    assert "Not signed in" in refused and "Start the sign-in again from AI Armory." in refused


def test_the_apis_to_turn_on_open_in_the_clients_project():
    found = checks.apis(CLIENT)
    assert [a["name"] for a in found] == ["Gmail", "Calendar", "Drive", "Docs", "Sheets", "Slides", "Google Chat", "People"]
    assert found[0]["url"] == "https://console.cloud.google.com/apis/library/gmail.googleapis.com?project=123456"
    assert [a["chat"] for a in found][-2:] == [True, True] and not found[0]["chat"]
    assert checks.apis()[0]["url"].endswith("gmail.googleapis.com")  # no client yet: no project


def test_checking_again_needs_a_sign_in_and_says_what_it_found(google_accounts, monkeypatch):
    with pytest.raises(ValueError, match="not signed in"):
        checks.recheck("personal", CLIENT)
    common.write_private(google_accounts.token_path("personal"), json.dumps({"scopes": " ".join(common.SCOPES)}))
    monkeypatch.setattr(checks, "check", lambda label, given, client: ([Check("Gmail", "ok")], "me@example.org"))
    assert checks.recheck("personal", CLIENT) == Outcome("personal", "done", "Checked the personal Google account as "
                                                         "me@example.org.", "me@example.org", [Check("Gmail", "ok")])
