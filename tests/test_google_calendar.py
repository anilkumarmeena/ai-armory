"""Google Calendar, with the Calendar API mocked: listing, private events, and invites in two steps."""

import pytest
from google_fakes import Services

from ai_armory.toolsets.google import approve, common, pending
from ai_armory.toolsets.google.calendar import (calendar_create_event, calendar_draft_invite, calendar_events,
                                                calendar_send_invite)


def _setup(api, mock):
    events = mock.events.return_value
    events.insert.return_value.execute.return_value = {"htmlLink": "https://calendar.example/e1"}


@pytest.fixture
def calendar(google_accounts, monkeypatch):
    services = Services(_setup)
    services.events = lambda label="work": services.client(label, "calendar").events.return_value
    monkeypatch.setattr(common, "service", services)
    return services


def _event(title, start):
    return {"summary": title, "start": {"dateTime": start}, "end": {"dateTime": start}, "attendees": [{}, {}]}


def test_events_from_every_account_come_back_in_order(calendar):
    calendar.events("work").list.return_value.execute.return_value = {"items": [_event("Standup", "2026-10-05T10:00:00Z")]}
    calendar.events("personal").list.return_value.execute.return_value = {"items": [_event("Gym", "2026-10-05T07:00:00Z")]}
    found = calendar_events(days=99, query="")
    assert [(e["account"], e["title"], e["attendees"]) for e in found] == [("personal", "Gym", 2), ("work", "Standup", 2)]
    asked = calendar.events("work").list.call_args.kwargs
    assert asked["calendarId"] == "primary" and asked["singleEvents"] and asked["q"] is None
    assert asked["timeMin"].endswith("+00:00") and asked["timeMin"][11:19] == "00:00:00"  # today, in the set zone


def test_a_private_event_invites_nobody(calendar):
    said = calendar_create_event("personal", "Dentist", "2026-10-06T09:00:00", "2026-10-06T09:30:00")
    assert said == "Created 'Dentist' on the personal calendar: https://calendar.example/e1"
    insert = calendar.events("personal").insert.call_args.kwargs
    assert insert["sendUpdates"] == "none" and insert["body"]["attendees"] == []
    assert insert["body"]["start"] == {"dateTime": "2026-10-06T09:00:00", "timeZone": "UTC"}


@pytest.mark.parametrize("start, end, error", [
    ("tomorrow", "2026-10-06T10:00:00", "ISO 8601"),
    ("2026-10-06T10:00:00", "2026-10-06T09:00:00", "end after it starts"),
    ("2026-10-06T09:00:00+05:30", "2026-10-06T10:00:00", "both with a time zone offset"),
])
def test_bad_times_are_refused_before_any_call(calendar, start, end, error):
    with pytest.raises(ValueError, match=error):
        calendar_create_event("work", "x", start, end)
    assert calendar.opened == []


def test_an_invite_is_drafted_then_sent_only_once_approved(calendar):
    drafted = calendar_draft_invite("work", " Review ", "2026-10-05T10:00:00", "2026-10-05T10:30:00",
                                    ["ana@example.com", " bo@example.com", "ana@example.com"])
    assert calendar.opened == []  # drafting touches nothing
    assert drafted["attendees"] == ["ana@example.com", "bo@example.com"]
    assert drafted["summary"] == ("Invite ana@example.com, bo@example.com to 'Review' on Monday 05 October 2026 at "
                                  "10:00, from the work calendar. Each of them gets an email invite.")
    assert pending(drafted["draft_id"]).summary == drafted["summary"]

    with pytest.raises(PermissionError):
        calendar_send_invite(drafted["draft_id"])
    assert calendar.opened == []
    approve(drafted["draft_id"])
    said = calendar_send_invite(drafted["draft_id"])
    assert said == "Created 'Review' on the work calendar and invited 2 people: https://calendar.example/e1"
    insert = calendar.events().insert.call_args.kwargs
    assert insert["sendUpdates"] == "all"
    assert insert["body"]["attendees"] == [{"email": "ana@example.com"}, {"email": "bo@example.com"}]
    with pytest.raises(ValueError, match="no draft"):
        calendar_send_invite(drafted["draft_id"])  # once only


@pytest.mark.parametrize("attendees, error", [([], "No one to invite"), (["  "], "No one to invite"),
                                              (["ana"], "Not an email address: ana")])
def test_an_invite_needs_real_addresses(calendar, attendees, error):
    with pytest.raises(ValueError, match=error):
        calendar_draft_invite("work", "x", "2026-10-05T10:00:00", "2026-10-05T11:00:00", attendees)
