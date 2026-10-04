"""Google Calendar across the configured accounts: list events, add events to the user's own calendar, and invite
people, which emails them, in two steps: calendar_draft_invite, then calendar_send_invite once the user says yes
(see drafts.py).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ai_armory.toolsets.google import common, drafts
from ai_armory.toolsets.google.common import (ACCOUNT, ACCOUNT_OR_ALL, CALENDAR, Specs, across, api_errors, build,
                                              noted, settings)

specs = Specs()

START = {"type": "string", "description": "ISO 8601 local time, e.g. 2026-09-27T15:00:00"}
END = {"type": "string", "description": "ISO 8601 local time"}


@specs.tool(
    "events",
    "List calendar events from the start of today through the next N days, across the user's accounts.",
    {"days": {"type": "integer", "description": "How many days to cover. 1 = today only. Default 1."},
     "account": ACCOUNT_OR_ALL,
     "query": {"type": "string", "description": "Optional text to filter events by."}},
    read_only=True,
)
def calendar_events(days: int = 1, account: str = "all", query: str = "") -> list[dict] | dict:
    start = datetime.now(settings().tz).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=max(1, min(31, days)))

    def read(label: str) -> list[dict]:
        items = common.service(label, "calendar", "v3").events().list(
            calendarId="primary", timeMin=start.isoformat(), timeMax=end.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=50, q=query or None,
        ).execute().get("items", [])
        return [{
            "account": label,
            "title": e.get("summary", "(no title)"),
            "start": e["start"].get("dateTime", e["start"].get("date")),
            "end": e["end"].get("dateTime", e["end"].get("date")),
            "location": e.get("location"),
            "meet": e.get("hangoutLink"),
            "attendees": len(e.get("attendees", [])),
        } for e in items]

    events, notes = across(account, read)
    return noted("events", sorted(events, key=lambda e: e["start"]), notes)


def _times(start: str, end: str) -> tuple[datetime, datetime]:
    try:
        begins, ends = datetime.fromisoformat(start.strip()), datetime.fromisoformat(end.strip())
    except ValueError:
        raise ValueError("start and end must be ISO 8601 times, e.g. 2026-09-27T15:00:00.") from None
    if (begins.tzinfo is None) != (ends.tzinfo is None):
        raise ValueError("Give start and end both with a time zone offset, or both without.")
    if ends <= begins:
        raise ValueError("The event must end after it starts.")
    return begins, ends


def _body(title: str, start: str, end: str, description: str, attendees: list[str]) -> dict:
    zone = settings().timezone
    if not title.strip():
        raise ValueError("The event needs a title.")
    return {
        "summary": title.strip(),
        "description": description,
        "start": {"dateTime": start.strip(), "timeZone": zone},
        "end": {"dateTime": end.strip(), "timeZone": zone},
        "attendees": [{"email": a} for a in attendees],
    }


def _insert(account: str, body: dict, invite: bool) -> dict:
    calendar = common.service(account, "calendar", "v3")
    with api_errors(account, CALENDAR):
        return calendar.events().insert(calendarId="primary", body=body,
                                        sendUpdates="all" if invite else "none").execute()


@specs.tool(
    "create_event",
    "Add an event to the user's own calendar, with nobody invited, so nobody is emailed. To invite people, use "
    "calendar_draft_invite instead.",
    {"account": ACCOUNT, "title": {"type": "string"}, "start": START, "end": END,
     "description": {"type": "string"}},
    ["title", "start", "end"],
)
def calendar_create_event(account: str, title: str, start: str, end: str, description: str = "") -> str:
    _times(start, end)
    body = _body(title, start, end, description, [])
    event = _insert(account, body, invite=False)
    return f"Created '{body['summary']}' on the {account} calendar: {event.get('htmlLink')}"


@specs.tool(
    "draft_invite",
    "Prepare a calendar event that invites people, which emails each of them an invite. It sends nothing and "
    "changes nothing: call calendar_send_invite with its draft_id, which goes ahead only after the user has heard "
    "the summary and said yes.",
    {"account": ACCOUNT, "title": {"type": "string"}, "start": START, "end": END,
     "attendees": {"type": "array", "items": {"type": "string"}, "description": "Email addresses"},
     "description": {"type": "string"}},
    ["title", "start", "end", "attendees"],
    read_only=True,
)
def calendar_draft_invite(account: str, title: str, start: str, end: str, attendees: list[str],
                          description: str = "") -> dict:
    begins, _ends = _times(start, end)
    body = _body(title, start, end, description, [])
    people = list(dict.fromkeys(a.strip() for a in attendees if a.strip()))
    if not people:
        raise ValueError("No one to invite. For an event with nobody invited, use calendar_create_event.")
    if bad := [a for a in people if "@" not in a]:
        raise ValueError(f"Not an email address: {', '.join(bad)}.")
    when = f"{begins:%A %d %B %Y at %H:%M}"
    body["attendees"] = [{"email": a} for a in people]
    summary = (f"Invite {', '.join(people)} to '{body['summary']}' on {when}, from the {account} calendar. Each of "
               "them gets an email invite.")
    draft = drafts.keep("calendar_send_invite", account, summary, f"invite to {body['summary']}", {"body": body})
    return drafts.reply(draft, "calendar_send_invite", title=body["summary"], start=start.strip(), end=end.strip(),
                        attendees=people)


@specs.tool(
    "send_invite",
    "Create a calendar event drafted with calendar_draft_invite and email its invites, exactly as drafted: it takes "
    "only the draft_id. It's held until the user has heard the draft and said yes, which the host asks for itself.",
    {"draft_id": {"type": "string", "description": "From calendar_draft_invite"}},
    ["draft_id"],
    needs_confirmation=True,
)
def calendar_send_invite(draft_id: str) -> str:
    d = drafts.take(draft_id, "calendar_send_invite", "calendar_draft_invite")
    event = _insert(d.account, d.payload["body"], invite=True)
    people = len(d.payload["body"]["attendees"])
    return (f"Created '{d.payload['body']['summary']}' on the {d.account} calendar and invited {people} "
            f"{'person' if people == 1 else 'people'}: {event.get('htmlLink')}")


def build_toolset():
    return build("calendar", "Google Calendar across the configured Google accounts: events and invites.", specs)


def __getattr__(name: str):
    if name == "toolset":
        return build_toolset()
    raise AttributeError(name)
