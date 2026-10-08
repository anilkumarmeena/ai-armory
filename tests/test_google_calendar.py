"""Google Calendar, with the Calendar API mocked: listing, private events, invites in two steps, changing,
deleting and answering events (in two steps whenever other people are emailed), and free time."""

import pytest
from google_fakes import Services, http_error, write_token

from ai_armory.toolsets.google import approve, common, pending
from ai_armory.toolsets.google.calendar import (calendar_create_event, calendar_delete_event, calendar_draft_delete,
                                                calendar_draft_invite, calendar_draft_respond, calendar_draft_update,
                                                calendar_event, calendar_events, calendar_free_time,
                                                calendar_send_invite, calendar_send_response, calendar_send_update,
                                                calendar_update_event)
from ai_armory.toolsets.google.common import SCOPES


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


def test_events_carry_their_id_organiser_and_the_users_answer(calendar):
    invite = _held(organizer={"email": "priya@example.com", "displayName": "Priya"},
                   attendees=[{"email": "me@example.com", "self": True, "responseStatus": "needsAction"},
                              {"email": "priya@example.com", "organizer": True}], recurringEventId="r1")
    calendar.events("work").list.return_value.execute.return_value = {"items": [invite]}
    calendar.events("personal").list.return_value.execute.return_value = {"items": []}
    found = calendar_events(start_date="2026-10-01", days=3)
    assert found == [{"account": "work", "id": "e1", "title": "Sync", "start": "2026-10-09T15:00:00+00:00",
                      "end": "2026-10-09T15:30:00+00:00", "location": None, "meet": None, "attendees": 2,
                      "organizer": "Priya", "your_response": "not answered", "recurring": True}]
    asked = calendar.events("work").list.call_args.kwargs
    assert asked["timeMin"].startswith("2026-10-01T00:00:00") and asked["timeMax"].startswith("2026-10-04T00")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        calendar_events(start_date="next week")


# ── Changing, deleting and answering ────────────────────────────

ME = {"email": "me@example.com", "self": True}
GUESTS = [{**ME, "organizer": True, "responseStatus": "accepted"},
          {"email": "ana@example.com", "displayName": "Ana", "responseStatus": "accepted"},
          {"email": "bo@example.com", "responseStatus": "needsAction"},
          {"email": "room@resource.example", "resource": True}]


def _held(**extra):
    """An event on the user's calendar, as events.get returns it: theirs and private unless `extra` says not."""
    return {"id": "e1", "etag": '"1"', "status": "confirmed", "summary": "Sync", "organizer": {**ME},
            "start": {"dateTime": "2026-10-09T15:00:00+00:00", "timeZone": "UTC"},
            "end": {"dateTime": "2026-10-09T15:30:00+00:00", "timeZone": "UTC"}, **extra}


def _event_is(calendar, event, label="work"):
    calendar.events(label).get.return_value.execute.return_value = event


def test_an_event_with_nobody_else_on_it_moves_straight_away_and_keeps_its_length(calendar):
    _event_is(calendar, _held())
    said = calendar_update_event("work", "e1", start="2026-10-09T16:00:00", location="Room 2")
    assert said == ("Changed 'Sync' on Friday 09 October 2026 at 15:00 on the work calendar: move it to Friday 09 "
                    "October 2026, 16:00 to 16:30; change its place to Room 2.")
    patch = calendar.events().patch.call_args.kwargs
    assert patch["eventId"] == "e1" and patch["sendUpdates"] == "none"
    assert patch["body"] == {"start": {"dateTime": "2026-10-09T16:00:00", "timeZone": "UTC"},
                             "end": {"dateTime": "2026-10-09T16:30:00", "timeZone": "UTC"}, "location": "Room 2"}


def test_an_all_day_event_moves_by_date(calendar):
    _event_is(calendar, _held(start={"date": "2026-10-09"}, end={"date": "2026-10-10"}))
    calendar_update_event("work", "e1", start="2026-10-12", title="Offsite")
    body = calendar.events().patch.call_args.kwargs["body"]
    assert body == {"start": {"date": "2026-10-12"}, "end": {"date": "2026-10-13"}, "summary": "Offsite"}


@pytest.mark.parametrize("changes, error", [
    ({}, "Nothing to change"),
    ({"start": "2026-10-09T16:00:00", "end": "2026-10-09T15:00:00"}, "end after it starts"),
    ({"start": "2026-10-12"}, "Give end too"),
    ({"start": "at four"}, "isn't an ISO 8601 time"),
])
def test_bad_changes_change_nothing(calendar, changes, error):
    _event_is(calendar, _held())
    with pytest.raises(ValueError, match=error):
        calendar_update_event("work", "e1", **changes)
    assert not calendar.events().patch.called


def test_an_event_with_guests_changes_only_after_a_yes_and_emails_them(calendar):
    _event_is(calendar, _held(attendees=GUESTS))
    with pytest.raises(ValueError, match="calendar_draft_update"):
        calendar_update_event("work", "e1", start="2026-10-09T16:00:00")
    drafted = calendar_draft_update("work", "e1", start="2026-10-09T16:00:00", title="Budget sync")
    assert drafted["summary"] == (
        "Change 'Sync' on Friday 09 October 2026 at 15:00, on the work calendar: move it to Friday 09 October 2026, "
        "16:00 to 16:30; rename it 'Budget sync'. Its 2 guests, Ana and bo@example.com, each get an email about it.")
    assert pending(drafted["draft_id"]).kind == "calendar_send_update"
    with pytest.raises(PermissionError):
        calendar_send_update(drafted["draft_id"])
    approve(drafted["draft_id"])
    assert calendar_send_update(drafted["draft_id"]) == ("Changed 'Sync' on Friday 09 October 2026 at 15:00 on the "
                                                         "work calendar and emailed its guests.")
    patch = calendar.events().patch.call_args.kwargs
    assert patch["sendUpdates"] == "all" and patch["body"]["summary"] == "Budget sync"
    assert not calendar.events().update.called


def test_a_change_drafted_before_the_event_changed_does_nothing(calendar):
    _event_is(calendar, _held(attendees=GUESTS))
    drafted = calendar_draft_update("work", "e1", description="Agenda: numbers")
    _event_is(calendar, _held(attendees=GUESTS, etag='"2"'))
    approve(drafted["draft_id"])
    with pytest.raises(ValueError, match="changed since the draft"):
        calendar_send_update(drafted["draft_id"])
    assert not calendar.events().patch.called


def test_a_private_event_is_not_drafted_as_an_update(calendar):
    _event_is(calendar, _held())
    with pytest.raises(ValueError, match="calendar_update_event"):
        calendar_draft_update("work", "e1", title="x")


def test_someone_elses_event_cant_be_changed_or_deleted_only_answered(calendar):
    _event_is(calendar, _held(organizer={"email": "priya@example.com", "displayName": "Priya"},
                              attendees=[{**ME, "responseStatus": "needsAction"},
                                         {"email": "priya@example.com", "organizer": True}]))
    with pytest.raises(ValueError, match="Priya organises 'Sync', so only they can change it"):
        calendar_update_event("work", "e1", title="x")
    with pytest.raises(ValueError, match="decline it with calendar_draft_respond"):
        calendar_draft_delete("work", "e1")

    drafted = calendar_draft_respond("work", "e1", "declined", note=" Clash with the board ")
    assert drafted["summary"] == ("Answer no to 'Sync' on Friday 09 October 2026 at 15:00, from the work calendar, "
                                  "with the note: Clash with the board. Priya gets an email saying Sam won't go.")
    with pytest.raises(PermissionError):
        calendar_send_response(drafted["draft_id"])
    approve(drafted["draft_id"])
    assert calendar_send_response(drafted["draft_id"]) == "Answered no to 'Sync'; Priya is told."
    patch = calendar.events().patch.call_args.kwargs
    assert patch["sendUpdates"] == "all"
    assert patch["body"]["attendees"][0] == {**ME, "responseStatus": "declined", "comment": "Clash with the board"}
    assert patch["body"]["attendees"][1] == {"email": "priya@example.com", "organizer": True}


def test_the_users_own_event_has_no_invite_to_answer(calendar):
    _event_is(calendar, _held(attendees=GUESTS))
    with pytest.raises(ValueError, match="no invite to answer"):
        calendar_draft_respond("work", "e1", "accepted")


def test_an_answer_that_isnt_yes_no_or_maybe_is_refused_before_any_call(calendar):
    with pytest.raises(ValueError, match="response must be one of accepted, declined, tentative"):
        calendar_draft_respond("work", "e1", "accept")
    assert not calendar.events().get.called


def test_answering_for_a_whole_series_answers_the_recurring_event(calendar):
    occurrence = _held(id="e1_20261009", recurringEventId="e1", organizer={"email": "priya@example.com"},
                       attendees=[{**ME}])
    series = _held(organizer={"email": "priya@example.com"}, attendees=[{**ME}])
    calendar.events().get.return_value.execute.side_effect = [occurrence, series, series]
    drafted = calendar_draft_respond("work", "e1_20261009", "tentative", series=True)
    assert "Answer maybe to 'Sync' on Friday 09 October 2026 at 15:00, every occurrence" in drafted["summary"]
    approve(drafted["draft_id"])
    calendar_send_response(drafted["draft_id"])
    assert calendar.events().patch.call_args.kwargs["eventId"] == "e1"


def test_deleting_waits_for_a_yes_and_tells_guests_only_if_there_are_any(calendar):
    _event_is(calendar, _held())
    drafted = calendar_draft_delete("work", "e1")
    assert drafted["summary"] == ("Delete 'Sync' on Friday 09 October 2026 at 15:00, from the work calendar. Nobody "
                                  "else is on it.")
    assert not calendar.events().delete.called
    with pytest.raises(PermissionError):
        calendar_delete_event(drafted["draft_id"])
    approve(drafted["draft_id"])
    assert calendar_delete_event(drafted["draft_id"]) == ("Deleted 'Sync' on Friday 09 October 2026 at 15:00 from "
                                                          "the work calendar.")
    assert calendar.events().delete.call_args.kwargs == {"calendarId": "primary", "eventId": "e1",
                                                         "sendUpdates": "none"}

    _event_is(calendar, _held(attendees=GUESTS))
    drafted = calendar_draft_delete("work", "e1")
    assert drafted["summary"].endswith("Its 2 guests, Ana and bo@example.com, each get an email saying it's "
                                       "cancelled.")
    approve(drafted["draft_id"])
    assert calendar_delete_event(drafted["draft_id"]).endswith("and told its guests.")
    assert calendar.events().delete.call_args.kwargs["sendUpdates"] == "all"


def test_a_missing_or_deleted_event_says_to_find_it_again(calendar):
    calendar.events().get.return_value.execute.side_effect = http_error(404, "Not Found")
    with pytest.raises(ValueError, match="no event e9 on the work calendar"):
        calendar_event("work", "e9")
    calendar.events().get.return_value.execute.side_effect = None
    _event_is(calendar, _held(status="cancelled"))
    with pytest.raises(ValueError, match="or it was deleted"):
        calendar_draft_delete("work", "e1")


def test_one_event_in_full(calendar):
    _event_is(calendar, _held(attendees=GUESTS, description="Agenda", htmlLink="https://calendar.example/e1"))
    found = calendar_event("work", "e1")
    assert found["organizer"] == "you" and found["description"] == "Agenda"
    assert found["guests"] == [{"email": "ana@example.com", "name": "Ana", "response": "accepted"},
                               {"email": "bo@example.com", "response": "not answered"}]


# ── Free time ───────────────────────────────────────────────────

def _busy(calendar, label, calendars):
    calendar.client(label, "calendar").freebusy.return_value.query.return_value.execute.return_value = {
        "calendars": calendars}


def test_free_time_is_when_everyone_known_is_free(calendar):
    _busy(calendar, "work", {
        "primary": {"busy": [{"start": "2099-01-05T10:00:00Z", "end": "2099-01-05T11:00:00Z"}]},
        "ana@example.com": {"busy": [{"start": "2099-01-05T13:00:00Z", "end": "2099-01-05T14:30:00Z"},
                                     {"start": "2099-01-05T13:30:00Z", "end": "2099-01-05T14:00:00Z"}]},
        "bo@example.com": {"errors": [{"domain": "calendar", "reason": "notFound"}]},
    })
    found = calendar_free_time("work", people=["ana@example.com", "bo@example.com"], start_date="2099-01-05")
    assert [(f["start"], f["end"], f["minutes"]) for f in found["free"]] == [
        ("2099-01-05T09:00", "2099-01-05T10:00", 60), ("2099-01-05T11:00", "2099-01-05T13:00", 120),
        ("2099-01-05T14:30", "2099-01-05T18:00", 210)]
    assert found["busy"] == {"you": [{"start": "2099-01-05T10:00", "end": "2099-01-05T11:00"}],
                             "ana@example.com": [{"start": "2099-01-05T13:00", "end": "2099-01-05T14:30"}]}
    assert "bo@example.com" in found["unknown"] and "leave out bo@example.com" in found["notes"][0]
    body = calendar.client("work", "calendar").freebusy.return_value.query.call_args.kwargs["body"]
    assert body["items"] == [{"id": "primary"}, {"id": "ana@example.com"}, {"id": "bo@example.com"}]
    assert body["timeMin"] == "2099-01-05T09:00:00+00:00" and body["timeMax"] == "2099-01-05T18:00:00+00:00"


def test_free_time_counts_the_users_other_calendars_and_skips_weekends(calendar, google_accounts):
    write_token(google_accounts, "work", SCOPES)
    write_token(google_accounts, "personal", SCOPES)
    _busy(calendar, "work", {"primary": {"busy": []}})
    _busy(calendar, "personal", {"me@example.org": {"busy": [
        {"start": "2099-01-05T12:00:00Z", "end": "2099-01-05T13:00:00Z"}]}})  # answered under its address
    found = calendar_free_time("work", start_date="2099-01-03", days=3, minutes=60, day_end="17:00")
    assert [(f["day"], f["start"], f["end"]) for f in found["free"]] == [
        ("Monday", "2099-01-05T09:00", "2099-01-05T12:00"), ("Monday", "2099-01-05T13:00", "2099-01-05T17:00")]
    assert found["busy"]["you"] == [{"start": "2099-01-05T12:00", "end": "2099-01-05T13:00"}]


def test_free_time_refuses_bad_input(calendar):
    with pytest.raises(ValueError, match="Not an email address: ana"):
        calendar_free_time("work", people=["ana"])
    with pytest.raises(ValueError, match="day_end must be after day_start"):
        calendar_free_time("work", day_start="18:00", day_end="09:00")
    assert calendar.opened == []
