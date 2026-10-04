"""The Google tool sets as a whole: settings, loading, metadata, schemas, input checks, drafts and the adapters."""

import json

import pytest
from google_fakes import Services, run, tools_of, write_token
from mcp import Client

import ai_armory
from ai_armory.adapters import claude_sdk, stdio
from ai_armory.toolsets.google import approve, common, drafts, pending
from ai_armory.toolsets.google.settings import Account, GoogleSettings, configure, current

ALL = {
    "gmail_search", "gmail_read", "gmail_create_draft", "gmail_modify",
    "calendar_events", "calendar_create_event", "calendar_draft_invite", "calendar_send_invite",
    "chat_unread", "chat_spaces", "chat_read", "chat_search", "chat_mark_read", "chat_draft", "chat_send",
    "docs_search", "docs_read", "docs_beautify", "docs_format", "docs_replace_text", "docs_insert",
    "docs_draft_delete", "docs_delete", "docs_insert_image",
    "sheets_search", "sheets_read", "sheets_write", "sheets_append", "sheets_add_tab", "sheets_draft_delete_tab",
    "sheets_delete_tab", "sheets_format",
}
READ_ONLY = {"gmail_search", "gmail_read", "calendar_events", "calendar_draft_invite", "chat_unread", "chat_spaces",
             "chat_read", "chat_search", "chat_draft", "docs_search", "docs_read", "docs_draft_delete",
             "sheets_search", "sheets_read", "sheets_draft_delete_tab"}
# Everything that sends or deletes: each takes only a draft_id, and is gated by the host.
CONFIRMED = {"calendar_send_invite", "chat_send", "docs_delete", "sheets_delete_tab"}


# ── Settings ────────────────────────────────────────────────────

CONFIG = """
token_dir = "tokens"
default = "personal"
timezone = "Asia/Kolkata"
user_name = "Sam"
sign_in_command = "my-host google login {label}"

[[accounts]]
label = "work"
email = "Me@Example.com"
description = "for work"
chat = true

[[accounts]]
label = "personal"
"""


def test_settings_come_from_the_config_file(tmp_path, monkeypatch):
    path = tmp_path / "google.toml"
    path.write_text(CONFIG)
    monkeypatch.setenv("AI_ARMORY_GOOGLE_CONFIG", str(path))
    s = current()
    assert s.labels == ["work", "personal"] and s.chat_labels == ["work"] and s.default_label == "personal"
    assert s.token_path("work") == tmp_path / "tokens" / "google-work.json"  # relative to the file
    assert s.chat_state_path("work") == tmp_path / "tokens" / "google-work-chat.json"
    assert s.sign_in("work") == "my-host google login work" and str(s.tz) == "Asia/Kolkata"
    assert s.resolve("me@example.com") == "work" and s.resolve("PERSONAL") == "personal"
    assert s.resolve("nobody") == "nobody"
    assert s.describe() == 'The Google accounts, by label: "work" (Me@Example.com): for work; "personal".'


def test_without_a_config_there_are_no_accounts_but_the_tools_still_load():
    assert current().accounts == ()
    gmail = ai_armory.load("gmail")
    assert gmail.tools[0].input_schema["properties"]["account"]["description"].startswith("No Google account")


@pytest.mark.anyio
async def test_without_accounts_a_call_says_how_to_set_them_up():
    result = await run(ai_armory.load("gmail"), "gmail_search", {"query": "x"})
    assert result.is_error and "No Google account is set up" in result.text


@pytest.mark.parametrize("kwargs, error", [
    ({"accounts": [Account("Work")]}, "can't be an account label"),
    ({"accounts": [Account("all")]}, "can't be an account label"),
    ({"accounts": [Account("../x")]}, "can't be an account label"),
    ({"accounts": [Account("a"), Account("a")]}, "two a accounts"),
    ({"accounts": [Account("a", "x@y.z"), Account("b", "X@y.z")]}, "both the a and the b account"),
    ({"accounts": [Account("a")], "default": "b"}, "isn't one of the accounts"),
    ({"timezone": "Mars/Olympus"}, "Unknown time zone"),
])
def test_bad_settings_are_refused(kwargs, error):
    with pytest.raises(ValueError, match=error):
        GoogleSettings(**kwargs)


def test_nothing_personal_or_host_specific_is_built_in():
    s = GoogleSettings()
    assert s.accounts == () and s.user_name == "the user"
    assert "jarvis" not in json.dumps({t.name: [t.description, t.input_schema] for ts in ai_armory.load_many(
        ["google"]) for t in ts.tools}).lower()


# ── Loading and metadata ────────────────────────────────────────

def test_each_sub_set_loads_alone_and_the_google_group_loads_them_all(google_accounts):
    assert {"gmail", "calendar", "chat", "docs", "sheets"} <= set(ai_armory.available())
    assert "google" not in ai_armory.available()  # a group, so serving "all" never loads its tools twice
    names = [ts.name for ts in ai_armory.load_many(["google"])]
    assert names == ["gmail", "calendar", "chat", "docs", "sheets"]
    assert {t.name for ts in ai_armory.load_many(["google"]) for t in ts.tools} == ALL
    assert [ts.name for ts in ai_armory.load_many(["gmail", "google"])] == names  # each once, so no clash
    assert [ts.name for ts in ai_armory.load_many(["sheets", "google"])][:2] == ["sheets", "gmail"]
    combined = ai_armory.load("google")
    assert {t.name for t in combined.tools} == ALL
    assert {t.name for t in ai_armory.load("chat").tools} == {n for n in ALL if n.startswith("chat_")}


def test_every_tool_says_whether_it_is_read_only_and_sends_and_deletes_need_confirmation(google_accounts):
    tools = {t.name: t for ts in ai_armory.load_many(["google"]) for t in ts.tools}
    assert {n for n, t in tools.items() if t.read_only} == READ_ONLY
    assert {n for n, t in tools.items() if t.needs_confirmation} == CONFIRMED
    assert not any(t.read_only and t.needs_confirmation for t in tools.values())
    for name in CONFIRMED:
        assert tools[name].input_schema["properties"] == {"draft_id": {"type": "string", "description":
                                                          tools[name].input_schema["properties"]["draft_id"]["description"]}}
        assert tools[name].input_schema["required"] == ["draft_id"]


def test_account_fields_list_the_configured_accounts(google_accounts):
    tools = {t.name: t for ts in ai_armory.load_many(["google"]) for t in ts.tools}
    one = tools["gmail_read"].input_schema["properties"]["account"]
    assert one["enum"] == ["work", "personal"] and one["default"] == "work"
    assert '"work" (me@example.com): for work' in one["description"]
    every = tools["gmail_search"].input_schema["properties"]["account"]
    assert every["enum"] == ["work", "personal", "all"] and every["default"] == "all"
    assert tools["chat_read"].input_schema["properties"]["account"]["default"] == "work"
    upload = tools["docs_insert_image"].input_schema["properties"]["upload_account"]
    assert upload["enum"] == ["work", "personal"] and "default" not in upload
    assert all(t.input_schema["additionalProperties"] is False for t in tools.values())


def test_the_chat_default_is_the_first_account_with_chat():
    configure(GoogleSettings(accounts=(Account("home"), Account("office", chat=True))))
    chat = tools_of(ai_armory.load("chat"))
    assert chat["chat_read"].input_schema["properties"]["account"]["default"] == "office"
    assert tools_of(ai_armory.load("gmail"))["gmail_read"].input_schema["properties"]["account"]["default"] == "home"


# ── Inputs ──────────────────────────────────────────────────────

@pytest.fixture
def gmail_api(google_accounts, monkeypatch):
    def setup(api, mock):
        users = mock.users.return_value
        users.messages.return_value.get.return_value.execute.return_value = {
            "payload": {"headers": [{"name": "From", "value": "a@b.c"}]}, "snippet": "hi"}

    services = Services(setup)
    monkeypatch.setattr(common, "service", services)
    return services


@pytest.mark.anyio
async def test_inputs_are_checked_and_accounts_resolved_before_anything_runs(gmail_api):
    gmail = ai_armory.load("gmail")
    result = await run(gmail, "gmail_read", {"message_id": "m1"})
    assert not result.is_error and json.loads(result.text)["from"] == "a@b.c"
    assert gmail_api.opened == [("work", "gmail", "v1", None)]  # the default account
    await run(gmail, "gmail_read", {"account": "ME@example.org", "message_id": "m1"})
    assert gmail_api.opened[-1][0] == "personal"  # named by its email
    for args, says in (({"account": "nobody", "message_id": "m1"}, "field 'account' must be one of"),
                       ({"message_id": 5}, "field 'message_id' must be string"),
                       ({}, "missing required field 'message_id'"),
                       ({"message_id": "m1", "extra": 1}, "unknown field 'extra'")):
        result = await run(gmail, "gmail_read", args)
        assert result.is_error and says in result.text
    assert len(gmail_api.opened) == 2


# ── Drafts ──────────────────────────────────────────────────────

def test_a_draft_waits_for_the_hosts_approval_and_is_used_once(google_accounts):
    d = drafts.keep("chat_send", "work", "To Alice: hi.", "chat to Alice", {"x": 1})
    assert pending(d.id) is d
    with pytest.raises(PermissionError, match="only after Sam has heard"):
        drafts.take(d.id, "chat_send", "chat_draft")
    assert pending(d.id) is d  # still there to approve
    with pytest.raises(ValueError, match="no draft"):
        drafts.take(d.id, "docs_delete", "docs_draft_delete")  # another kind's draft can't be used
    assert approve(d.id)
    assert drafts.take(d.id, "chat_send", "chat_draft") is d
    assert pending(d.id) is None and not approve(d.id)
    with pytest.raises(ValueError, match="may have lapsed or been used"):
        drafts.take(d.id, "chat_send", "chat_draft")


def test_drafts_lapse(google_accounts, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(drafts, "_clock", lambda: now[0])
    d = drafts.keep("chat_send", "work", "s", "s", {})
    now[0] += google_accounts.draft_minutes * 60 + 1
    assert pending(d.id) is None and not approve(d.id)


def test_without_host_approval_the_clients_own_prompt_is_trusted():
    configure(GoogleSettings(accounts=(Account("work"),), host_approval=False))
    d = drafts.keep("sheets_delete_tab", "work", "s", "s", {})
    assert drafts.take(d.id, "sheets_delete_tab", "sheets_draft_delete_tab") is d


# ── Adapters ────────────────────────────────────────────────────

def test_the_sdk_adapter_names_the_tools_to_gate(google_accounts):
    gated = claude_sdk.confirmation_required(ai_armory.load_many(["google"]))
    assert gated == {f"mcp__ai-armory__{name}" for name in CONFIRMED}


@pytest.mark.anyio
async def test_over_stdio_the_tools_carry_their_hints(google_accounts):
    async with Client(stdio.build_server(ai_armory.load_many(["google"]))) as client:
        listed = {t.name: t for t in (await client.list_tools()).tools}
    assert set(listed) == ALL
    assert {n for n, t in listed.items() if t.annotations and t.annotations.read_only_hint} == READ_ONLY
    assert {n for n, t in listed.items() if t.meta == {stdio.CONFIRMATION_META_KEY: True}} == CONFIRMED


@pytest.mark.anyio
async def test_a_send_through_the_sdk_server_is_refused_until_approved(google_accounts, monkeypatch):
    write_token(google_accounts, "work", common.SCOPES)
    services = Services(lambda api, mock: mock.events.return_value.insert.return_value.execute.configure_mock(
        return_value={"htmlLink": "https://calendar.example/e1"}))
    monkeypatch.setattr(common, "service", services)
    server = claude_sdk.create_sdk_server(ai_armory.load_many(["calendar"]))["instance"]
    async with Client(server) as client:
        drafted = await client.call_tool("calendar_draft_invite", {
            "title": "Review", "start": "2026-10-05T10:00:00", "end": "2026-10-05T10:30:00",
            "attendees": ["ana@example.com"]})
        draft_id = json.loads(drafted.content[0].text)["draft_id"]
        refused = await client.call_tool("calendar_send_invite", {"draft_id": draft_id})
        assert refused.is_error and "Not done" in refused.content[0].text
        assert services.opened == []
        approve(draft_id)
        sent = await client.call_tool("calendar_send_invite", {"draft_id": draft_id})
        assert not sent.is_error and "invited 1 person" in sent.content[0].text
