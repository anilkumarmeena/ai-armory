"""Google Chat, with the Chat, People and Gmail APIs faked by a small workspace (`World`)."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from google_fakes import http_error, write_token

from ai_armory.toolsets.google import approve, chat, common, gmail, pending
from ai_armory.toolsets.google.common import CHAT_CREATE, CHAT_MARK, CHAT_READ, CHAT_SCOPES, SCOPES

ME = "users/1"
NOW = datetime.now(timezone.utc)


def ago(minutes: float = 0, days: float = 0) -> str:
    return chat._rfc3339(NOW - timedelta(minutes=minutes, days=days))


def message(space, n, sender, text, minutes, **extra):
    return {"name": f"{space}/messages/{n}", "sender": {"name": sender, "type": "HUMAN"}, "text": text,
            "createTime": ago(minutes), "thread": {"name": f"{space}/threads/t{n}"}, **extra}


def mention(user):
    return [{"type": "USER_MENTION", "userMention": {"user": {"name": user}, "type": "MENTION"}}]


class World:
    """A small Chat workspace: a DM with Alice, two busy spaces, a stale one and one already read."""

    def __init__(self):
        self.spaces = [
            {"name": "spaces/DM1", "spaceType": "DIRECT_MESSAGE", "lastActiveTime": ago(5)},
            {"name": "spaces/DEV", "displayName": "Platform Dev", "spaceType": "SPACE", "lastActiveTime": ago(30),
             "membershipCount": {"joinedDirectHumanUserCount": 40}},
            {"name": "spaces/OPS", "displayName": "Platform Ops", "spaceType": "SPACE", "lastActiveTime": ago(60)},
            {"name": "spaces/READ", "displayName": "Read Already", "spaceType": "SPACE", "lastActiveTime": ago(120)},
            {"name": "spaces/OLD", "displayName": "Old Project", "spaceType": "SPACE", "lastActiveTime": ago(days=10)},
        ]
        self.read = {"spaces/DM1": ago(60)[:-1] + ".123456789Z", "spaces/DEV": ago(45), "spaces/OPS": ago(90),
                     "spaces/READ": ago(100)}
        self.messages = {
            "spaces/DM1": [message("spaces/DM1", 1, "users/2", "Can you join at four?", 5)],
            "spaces/DEV": [message("spaces/DEV", 3, "users/3", "@Sam please review the player fix", 30,
                                   annotations=mention(ME), threadReply=True),
                           message("spaces/DEV", 2, ME, "On it", 31)],
            "spaces/OPS": [message("spaces/OPS", 4, "users/4", "Deploy done", 60)],
        }
        self.members = {"spaces/DM1": [ME, "users/2"]}
        self.people = {"users/1": "Sam Doe", "users/2": "Alice Carter", "users/3": "Bob Nguyen"}
        self.directory = [{"resourceName": "people/2", "names": [{"displayName": "Alice Carter"}],
                           "emailAddresses": [{"value": "alice@example.com"}]},
                          {"resourceName": "people/5", "names": [{"displayName": "Dana Reyes"}],
                           "emailAddresses": [{"value": "dana@example.com"}]}]
        self.dms = {"users/alice@example.com": "spaces/DM1", "users/2": "spaces/DM1"}
        self.emails = {"me@example.com": ME}
        self.fail: dict[str, Exception] = {}  # method → error to raise
        self.search: list[dict] | Exception = []
        self.calls: list[tuple[str, dict]] = []

    def space(self, name):
        return next(s for s in self.spaces if s["name"] == name)

    def handle(self, method, kw):
        self.calls.append((method, kw))
        key = f"{method}:{kw.get('name') or kw.get('parent') or ''}"
        if error := self.fail.get(key) or self.fail.get(method):
            raise error
        if method == "spaces.list":
            return {"spaces": [dict(s) for s in self.spaces][: kw.get("pageSize", 100)]}
        if method == "spaces.get":
            return dict(self.space(kw["name"]))
        if method == "spaces.findDirectMessage":
            if kw["name"] not in self.dms:
                raise http_error(404, "Direct message not found")
            return dict(self.space(self.dms[kw["name"]]))
        if method == "spaces.members.list":
            return {"memberships": [{"member": {"name": u, "type": "HUMAN"}} for u in self.members.get(kw["parent"], [])]}
        if method == "users.spaces.updateSpaceReadState":
            self.read[kw["name"].removeprefix("users/me/").removesuffix("/spaceReadState")] = kw["body"]["lastReadTime"]
            return {"name": kw["name"], **kw["body"]}
        if method == "users.spaces.getSpaceReadState":
            space = kw["name"].removeprefix("users/me/").removesuffix("/spaceReadState")
            return {"name": kw["name"], **({"lastReadTime": self.read[space]} if space in self.read else {})}
        if method == "spaces.messages.list":
            found = [dict(m) for m in self.messages.get(kw["parent"], [])]
            if "thread.name" in (kw.get("filter") or ""):
                found = [m for m in found if m["thread"]["name"] in kw["filter"]]
            return {"messages": found[: kw.get("pageSize", 25)]}
        if method == "spaces.messages.search":
            if isinstance(self.search, Exception):
                raise self.search
            return {"results": [{"message": m} for m in self.search]}
        if method == "spaces.messages.create":
            return {"name": f"{kw['parent']}/messages/new"}
        if method == "spaces.members.get":
            return {"name": kw["name"], "member": {"name": self.emails[kw["name"].split("/members/")[1]], "type": "HUMAN"}}
        if method == "spaces.setup":
            member = kw["body"]["memberships"][0]["member"]["name"]
            self.spaces.append({"name": "spaces/NEW", "spaceType": "DIRECT_MESSAGE", "lastActiveTime": ago()})
            self.dms[member] = "spaces/NEW"
            return dict(self.spaces[-1])
        if method == "users.getProfile":  # Gmail
            return {"emailAddress": "me@example.com"}
        if method == "people.get":
            return {"resourceName": "people/1", "names": [{"displayName": "Sam Doe"}]}
        if method == "people.listDirectoryPeople":
            return {"people": self.directory[:kw["pageSize"]]}
        if method == "people.getBatchGet":
            return {"responses": [{"requestedResourceName": r, "person": {"names": [{"displayName": n}]}}
                                  if (n := self.people.get("users/" + r.split("/")[1])) else {"requestedResourceName": r}
                                  for r in kw["resourceNames"]]}
        if method == "people.searchDirectoryPeople":
            return {"people": [p for p in self.directory if any(
                kw["query"].lower() in v.lower() for v in (p["names"][0]["displayName"], p["emailAddresses"][0]["value"]))]}
        raise AssertionError(f"unexpected call {method}")

    def called(self, method):
        return [kw for m, kw in self.calls if m == method]


class Node:
    """Stands in for a googleapiclient resource: sub-resources are called with no arguments, methods with some."""
    RESOURCES = {"spaces", "spaces.messages", "spaces.members", "users", "users.spaces", "people"}

    def __init__(self, world, path=""):
        self.world, self.path = world, path

    def __getattr__(self, name):
        full = f"{self.path}.{name}" if self.path else name
        if full in self.RESOURCES:
            return lambda: Node(self.world, full)

        def method(**kw):
            world = self.world

            class Request:
                def execute(self):
                    return world.handle(full, kw)

            return Request()

        return method


@pytest.fixture
def world(google_accounts, monkeypatch):
    """The work account signed in with every Chat scope, over a fake Chat; `.opened` records each service call."""
    w = World()
    w.opened = []
    w.settings = google_accounts

    def service(label, api, version, needs=None):
        if needs and needs not in common.granted(label):
            raise RuntimeError(common.sign_in_again(label, needs))
        w.opened.append((label, api, version, needs))
        return Node(w)

    monkeypatch.setattr(common, "service", service)
    monkeypatch.setattr(chat, "_PEOPLE", {})
    monkeypatch.setattr(chat, "_PEERS", {})
    monkeypatch.setattr(chat, "_ME", {})
    write_token(google_accounts, "work", SCOPES + CHAT_SCOPES)
    return w


# ── Set-up ──────────────────────────────────────────────────────

def test_chat_scopes_are_narrow():
    chat_scopes = [s.rsplit("/", 1)[1] for s in CHAT_SCOPES]
    assert "chat.messages" not in chat_scopes
    assert not any(s.startswith(("chat.app", "chat.admin", "chat.delete", "chat.import")) for s in chat_scopes)
    assert not any("/auth/chat" in s for s in SCOPES)


def test_an_old_token_still_does_mail_but_chat_says_sign_in_again(world):
    write_token(world.settings, "work", SCOPES)
    for call in (lambda: chat.chat_unread("work"), lambda: chat.chat_spaces("dev"),
                 lambda: chat.chat_read("spaces/DEV"), lambda: chat.chat_search("deploy"),
                 lambda: chat.chat_draft("spaces/DEV", "hi"), lambda: chat.chat_mark_read(["spaces/DEV"])):
        with pytest.raises(RuntimeError, match="Sign it in again with: sign-in work"):
            call()
    assert world.opened == []


def test_an_account_without_chat_is_refused_and_all_skips_it(world):
    write_token(world.settings, "personal", SCOPES)
    with pytest.raises(RuntimeError, match="Google Chat isn't set up for the personal account"):
        chat.chat_read("spaces/DEV", account="personal")
    assert world.opened == []
    assert {i["account"] for i in chat.chat_unread("all")["unread"]} == {"work"}


# ── Unread ──────────────────────────────────────────────────────

def test_unread_puts_dms_first_then_mentions_and_leaves_out_your_own_messages(world):
    unread = chat.chat_unread()["unread"]
    assert [(i["name"], i["kind"]) for i in unread] == [("Alice Carter", "dm"), ("Platform Dev", "space"),
                                                        ("Platform Ops", "space")]
    dm, dev, ops = unread
    assert dm["latest"] == [{"from": "Alice Carter", "time": chat._local(ago(5)), "text": "Can you join at four?"}]
    assert dev["unread"] == 1 and dev["mentions_you"] and [m["from"] for m in dev["latest"]] == ["Bob Nguyen"]
    assert not ops["mentions_you"] and ops["latest"][0]["from"] == "someone"


def test_one_failing_space_doesnt_sink_the_rest(world):
    world.fail["users.spaces.getSpaceReadState:users/me/spaces/OPS/spaceReadState"] = http_error(500, "backend error")
    result = chat.chat_unread()
    assert [i["name"] for i in result["unread"]] == ["Alice Carter", "Platform Dev"]
    assert result["notes"] == ["1 of the work account's Chat spaces couldn't be checked."]


NOTIFICATION = {"account": "work", "id": "e1", "from": "Alice Carter (via Google Chat) <chat-noreply@google.com>",
                "subject": "Alice Carter sent you a message", "date": "Mon, 28 Sep 2026 10:00:00 +0000",
                "unread": True, "snippet": "Can you join at four?"}


@pytest.fixture
def mail(monkeypatch):
    searched = []

    def gmail_search(query, account="all", max_results=10):
        searched.append((query, account))
        return [dict(NOTIFICATION)]

    monkeypatch.setattr(gmail, "gmail_search", gmail_search)
    return searched


def test_when_chat_is_blocked_unread_reads_the_notification_emails(world, mail):
    write_token(world.settings, "work", SCOPES)
    chat.save_state("work", "blocked")
    assert world.settings.chat_state_path("work").stat().st_mode & 0o777 == 0o600
    result = chat.chat_unread(days=2)
    assert mail == [("from:chat-noreply@google.com newer_than:2d", "work")]
    assert result["unread"] == [{"account": "work", "source": "email notification", "from": "Alice Carter",
                                 "name": "Alice Carter sent you a message", "time": NOTIFICATION["date"],
                                 "text": "Can you join at four?", "email_id": "e1"}]
    assert "notification emails" in result["notes"][0] and "Sam was away" in result["notes"][0]
    with pytest.raises(RuntimeError, match="doesn't let these tools read Google Chat"):
        chat.chat_read("Alice")


@pytest.mark.parametrize("error, kind", [
    (http_error(404, "Google Chat app not found. To create a Chat app..."), "app"),
    (http_error(403, "Google Chat API has not been used in project 123 before or it is disabled."), "api"),
    (http_error(403, "Request had insufficient authentication scopes."), "scope"),
    (http_error(403, "The caller does not have permission"), "blocked"),
    (http_error(500, "Internal error"), ""),
])
def test_failures_are_sorted_into_what_to_fix(error, kind):
    assert chat.problem(error) == kind


def test_the_check_passes_when_chat_works_and_remembers_it(world):
    found, text = chat.check("work")
    assert found == "ok" and text == "Google Chat works on the work account."
    assert json.loads(world.settings.chat_state_path("work").read_text())["status"] == "ok"


# ── Finding, reading, searching ─────────────────────────────────

def test_spaces_match_loosely_and_dms_are_found_through_the_directory(world):
    assert chat.chat_spaces("platform dev")["spaces"][0]["name"] == "Platform Dev"
    found = chat.chat_spaces("alice")["spaces"]
    assert found == [{"account": "work", "space": "spaces/DM1", "name": "Alice Carter", "kind": "dm",
                      "last_active": chat._local(ago(5)), "email": "alice@example.com"}]
    assert chat.chat_spaces("platform ups")["spaces"][0]["name"] == "Platform Ops"  # misheard
    nothing = chat.chat_spaces("marketing budget")
    assert nothing["spaces"] == [] and nothing["closest"]


def test_an_ambiguous_name_asks_which(world):
    with pytest.raises(ValueError, match="could be Platform Dev or Platform Ops"):
        chat.chat_read("platform")


def test_read_gives_the_messages_oldest_first_with_names_and_threads(world):
    result = chat.chat_read("Platform Dev", hours=500, max_messages=500)
    assert [(m["from"], m["text"]) for m in result["messages"]] == [("you", "On it"),
                                                                     ("Bob Nguyen", "@Sam please review the player fix")]
    assert result["messages"][1]["thread"] == "spaces/DEV/threads/t3" and result["messages"][1]["reply"]
    assert world.called("spaces.messages.list")[0]["pageSize"] == 100


def test_search_uses_chats_own_search_and_else_searches_itself(world):
    world.search = [world.messages["spaces/OPS"][0]]
    result = chat.chat_search('deploy "done"', days=100)
    assert world.called("spaces.messages.search")[0]["body"]["filter"].startswith('"deploy" "done" AND create_time >= "')
    assert [m["text"] for m in result["messages"]] == ["Deploy done"] and "notes" not in result
    world.search = http_error(400, "Invalid argument")
    result = chat.chat_search("PLAYER fix")
    assert [m["text"] for m in result["messages"]] == ["@Sam please review the player fix"]
    assert "the tool searched the last 7 days of the 20 most active spaces itself" in result["notes"][0]


def test_marking_read_sets_each_spaces_read_time(world):
    result = chat.chat_mark_read(["spaces/DEV", "alice", "spaces/DEV/threads/t3"])
    assert result["marked"] == [{"space": "spaces/DEV", "name": "Platform Dev"},
                                {"space": "spaces/DM1", "name": "Alice Carter"}]
    assert "only its whole space, spaces/DEV" in result["not_marked"][0]["error"]
    assert world.opened[0][3] == CHAT_MARK
    assert all(kw["updateMask"] == "lastReadTime" for kw in world.called("users.spaces.updateSpaceReadState"))


# ── Drafts and sending ──────────────────────────────────────────

def test_a_draft_sends_nothing_and_send_waits_for_the_hosts_approval(world):
    drafted = chat.chat_draft("Platform Dev", "  Looks good, merging  ", thread="spaces/DEV/threads/t3")
    assert world.called("spaces.messages.create") == []
    assert drafted["to"] == "Platform Dev, 40 people" and drafted["text"] == "Looks good, merging"
    assert drafted["thread"] == "@Sam please review the player fix"
    summary = ("To Platform Dev, 40 people, in the thread that starts '@Sam please review the player fix': Looks good, "
               "merging.")
    assert drafted["summary"] == summary and pending(drafted["draft_id"]).summary == summary

    with pytest.raises(PermissionError, match="only after Sam has heard"):
        chat.chat_send(drafted["draft_id"])
    assert world.called("spaces.messages.create") == []
    approve(drafted["draft_id"])
    assert chat.chat_send(drafted["draft_id"]) == "Sent to Platform Dev."
    (sent,) = world.called("spaces.messages.create")
    assert sent == {"parent": "spaces/DEV", "body": {"text": "Looks good, merging",
                                                     "thread": {"name": "spaces/DEV/threads/t3"}},
                    "messageReplyOption": "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"}
    with pytest.raises(ValueError, match="no draft"):
        chat.chat_send(drafted["draft_id"])  # sent once at most


def test_a_draft_to_someone_with_no_dm_starts_one_only_when_sent(world):
    drafted = chat.chat_draft("dana@example.com", "Hi Dana")
    assert drafted["new_dm"] and drafted["to"] == "Dana Reyes, in a new direct message"
    assert world.called("spaces.setup") == []
    approve(drafted["draft_id"])
    assert chat.chat_send(drafted["draft_id"]) == "Sent to Dana Reyes, in a new direct message."
    assert world.called("spaces.setup")[0]["body"]["memberships"][0]["member"]["name"] == "users/dana@example.com"
    assert world.called("spaces.messages.create")[0]["parent"] == "spaces/NEW"


def test_starting_a_dm_needs_its_scope(world):
    write_token(world.settings, "work", SCOPES + [s for s in CHAT_SCOPES if s != CHAT_CREATE])
    with pytest.raises(RuntimeError, match="no Chat DM with Dana Reyes .* yet.*Sign it in again"):
        chat.chat_draft("Dana Reyes", "Hi")


@pytest.mark.parametrize("text, error", [("  ", "The draft is empty"), ("x" * 1501, "too long")])
def test_bad_drafts_are_refused(world, text, error):
    with pytest.raises(ValueError, match=error):
        chat.chat_draft("Platform Dev", text)


def test_news_lists_unread_dms_and_mentions_for_a_hosts_alerts(world):
    found = chat.news("work", days=1)
    assert [(n["name"], n["kind"], n["from"], n["count"]) for n in found] == [
        ("Alice Carter", "dm", "Alice Carter", 1), ("Platform Dev", "space", "Bob Nguyen", 1)]
    write_token(world.settings, "work", SCOPES + [s for s in CHAT_SCOPES if s != CHAT_READ])
    assert chat.news("work") == []
