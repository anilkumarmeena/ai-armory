"""Google Docs, with the Docs and Drive APIs mocked. Docs are laid out with real positions (`layout`), and `apply`
plays the position-moving requests back on the text, so the tests check that each edit lands where it should."""

from unittest.mock import MagicMock

import pytest
from google_fakes import IMG, P, T, Services, apply, http_error, kinds, layout, png_bytes, text_at
from googleapiclient.errors import HttpError

from ai_armory.toolsets.google import approve, common, docs_images, docs_write, pending
from ai_armory.toolsets.google.common import DOCS, DOCS_READ, DRIVE_FILE
from ai_armory.toolsets.google.docs import docs_read, docs_search
from ai_armory.toolsets.google.docs_images import FOLDER, docs_insert_image
from ai_armory.toolsets.google.docs_write import (SHIFTING, docs_beautify, docs_delete, docs_draft_delete, docs_format,
                                                  docs_insert, docs_replace_text)

DID = "16HIkERh1u-PmO401F7yBVqBeA7XMuPd7JJISQYynqtA"
URL = f"https://docs.google.com/document/d/{DID}/edit"


def as_doc(tab, title="Install guide", tabs=None):
    return {"documentId": DID, "title": title, "revisionId": "rev-7",
            "tabs": tabs or [{"tabProperties": {"tabId": "t.0", "title": "Tab 1"}, "documentTab": tab}]}


@pytest.fixture
def api(google_accounts, monkeypatch):
    """`.serve(doc)` sets what documents.get returns; `.sent()` is the last batchUpdate body; `.uploaded` lists
    each Drive files.create as (account, body, media)."""
    services = Services()
    services.doc = as_doc(layout(P("Hello"))[0])
    services.uploaded = []

    def setup(label):
        def configure(api_name, mock):
            mock.documents.return_value.get.return_value.execute.side_effect = lambda: services.doc
            mock.documents.return_value.batchUpdate.return_value.execute.return_value = {"replies": [{}]}
            mock.files.return_value.list.return_value.execute.return_value = {"files": []}
            mock.files.return_value.create.side_effect = lambda body, fields, media_body=None: MagicMock(
                execute=MagicMock(return_value=upload(label, body, media_body)))
            mock.permissions.return_value.create.return_value.execute.return_value = {"id": "anyoneWithLink"}
        return configure

    def upload(label, body, media):
        services.uploaded.append((label, body, media))
        if body.get("mimeType") == FOLDER:
            return {"id": "folder-1"}
        n = len(services.uploaded)
        return {"id": f"img-{n}", "webContentLink": f"https://drive.google.com/uc?id=img-{n}&export=download",
                "permissionIds": ["owner"]}

    original = services.client

    def client(label, api_name):
        services.setup = setup(label)
        return original(label, api_name)

    services.client = client
    services.serve = lambda doc: setattr(services, "doc", doc)
    services.docs = lambda label="work": services.client(label, "docs").documents.return_value
    services.drive = lambda label="work": services.client(label, "drive")
    services.sent = lambda label="work": services.docs(label).batchUpdate.call_args.kwargs["body"]
    monkeypatch.setattr(common, "service", services)
    return services


def shifting_last(requests):
    order = [kind in SHIFTING for kind in kinds(requests)]
    return order == sorted(order)


# ── Finding and reading ─────────────────────────────────────────

def test_search_finds_docs_by_name(api):
    docs_search("personal", "notes")
    assert api.opened == [("personal", "drive", "v3", common.DRIVE_METADATA)]
    assert api.drive("personal").files.return_value.list.call_args.kwargs["q"] == (
        "mimeType='application/vnd.google-apps.document' and trashed=false and name contains 'notes'")


def test_read_renders_headings_lists_tables_and_every_tab(api):
    first, _ = layout(P("Plan", "TITLE"), P("Goals", "HEADING_2"), P("Ship it", bullet="l1"), T(["A", "B"], ["1", "2"]))
    second, _ = layout(P("Raw notes"))
    api.serve(as_doc(None, tabs=[{"tabProperties": {"tabId": "t.0", "title": "Notes"}, "documentTab": first},
                                 {"tabProperties": {"tabId": "t.1", "title": "Transcript"}, "documentTab": second}]))
    result = docs_read("work", URL)
    assert api.opened == [("work", "docs", "v1", DOCS_READ)]
    assert result["tabs"] == ["Notes", "Transcript"] and result["url"] == URL
    assert result["text"] == ("=== Notes ===\n\n# Plan\n## Goals\n- Ship it\n\n| A | B |\n| 1 | 2 |\n\n"
                              "=== Transcript ===\n\nRaw notes")
    assert docs_read("work", DID, tab="transcript")["text"] == "Raw notes"
    with pytest.raises(ValueError, match="No tab called 'Agenda'. The tabs: Notes, Transcript."):
        docs_read("work", DID, tab="Agenda")


def test_a_long_doc_comes_back_in_parts(api):
    api.serve(as_doc(layout(*[P("x" * 99) for _ in range(300)])[0]))
    first = docs_read("work", DID)
    assert len(first["text"]) <= 20_000 and "Read on with start=" in first["note"]
    rest = docs_read("work", DID, start=int(first["note"].split("start=")[1].rstrip(".")))
    assert "note" not in rest


# ── Changing ────────────────────────────────────────────────────

SOP = (
    P("SETUP_GUIDE"),
    P("Purpose", "HEADING_2"),
    P("This guide explains how to set the device up."),
    P("Prerequisites", "HEADING_2"),
    P("- A tablet"),
    P("- Admin password"),
    P(""),
    P("• Wi-Fi access"),
    P("Note: keep the tablet charging."),
    P("3) Installation steps", bold=True),
    P("1. Open the store"),
    P("2. Search for the app"),
    P("3. Install and open it"),
    T(["Step", "Owner"], ["Install", "IT"]),
    P("Contact", "HEADING_2"),
    P(""),
)


def test_beautify_gives_a_messy_doc_a_consistent_style(api):
    tab, text = layout(*SOP)
    api.serve(as_doc(tab))
    preview = docs_beautify("work", URL, preview=True)
    api.docs().batchUpdate.assert_not_called()
    assert preview["would"] == ("set the title, styled and numbered 4 headings, made 2 real lists, highlighted 1 note "
                                "and tidied 1 table")
    said = docs_beautify("work", URL)
    assert said.startswith("Beautified 'Install guide' (work account): set the title")
    body = api.sent()
    assert body["writeControl"] == {"requiredRevisionId": "rev-7"}
    requests = body["requests"]
    assert set(kinds(requests)) <= docs_write.BATCH_REQUESTS and shifting_last(requests)
    assert apply(text, requests) == [
        "SETUP_GUIDE", "1. Purpose", "This guide explains how to set the device up.", "2. Prerequisites",
        "[bullet 0] A tablet", "[bullet 0] Admin password", "[bullet 0] Wi-Fi access",
        "Note: keep the tablet charging.", "3. Installation steps",
        "[numbered 0] Open the store", "[numbered 0] Search for the app", "[numbered 0] Install and open it",
        "Step", "Owner", "Install", "IT", "4. Contact", ""]


def test_format_styles_paragraphs_by_their_text(api):
    tab, text = layout(P("Summary"), P("Open Settings"), P("Tap About"), P("Restart"), P(""))
    api.serve(as_doc(tab))
    said = docs_format("work", DID, [{"paragraph": "summary", "style": "heading_1"},
                                     {"paragraph": "Open", "through": "restart", "list": "numbered"},
                                     {"phrase": "about", "bold": True}])
    assert said == ("Formatted 'Install guide' (work account): 'Summary': heading 1; 3 paragraphs from "
                    "'Open Settings' to 'Restart': a numbered list; bold on 'about' (1 time) in the doc.")
    requests = api.sent()["requests"]
    assert apply(text, requests) == ["Summary", "[numbered 0] Open Settings", "[numbered 0] Tap About",
                                     "[numbered 0] Restart", ""]
    bold = next(r["updateTextStyle"] for r in requests if "updateTextStyle" in r)
    assert text_at(text, bold["range"]) == "About"


def test_a_bad_change_changes_nothing(api):
    api.serve(as_doc(layout(P("Summary"), P(""))[0]))
    with pytest.raises(ValueError, match="Change 2: No paragraph matches 'Nowhere'"):
        docs_format("work", DID, [{"paragraph": "summary", "bold": True}, {"paragraph": "Nowhere", "bold": True}])
    api.docs().batchUpdate.assert_not_called()


def test_replace_text_is_not_pinned_and_counts_what_changed(api):
    api.docs().batchUpdate.return_value.execute.return_value = {
        "replies": [{"replaceAllText": {"occurrencesChanged": 3}}]}
    said = docs_replace_text("work", DID, "colour", "color")
    assert said == "Replaced 3 occurrences of 'colour' with 'color' in 'Install guide' (work account)."
    assert "writeControl" not in api.sent()


def test_insert_adds_styled_paragraphs_after_one_picked_by_text(api):
    tab, text = layout(P("Intro"), P("Steps", "HEADING_1"), P("Contact"), P(""))
    api.serve(as_doc(tab))
    said = docs_insert("work", DID, [{"text": "Plug in\nSwitch on", "style": "numbered"}], where="after",
                       anchor="steps")
    assert said == "Added 2 paragraphs after 'Steps' in 'Install guide' (work account)."
    assert apply(text, api.sent()["requests"]) == ["Intro", "Steps", "[numbered 0] Plug in", "[numbered 0] Switch on",
                                                   "Contact", ""]


def test_a_view_only_doc_says_so(api):
    api.docs().batchUpdate.return_value.execute.side_effect = http_error(403, "The caller does not have permission")
    with pytest.raises(RuntimeError, match="can't edit this doc: it's shared with it to view or comment only"):
        docs_replace_text("work", DID, "a", "b")


# ── Deleting, in two steps ──────────────────────────────────────

SCOPE = (
    P("Guide", "TITLE"),
    P("Scope", "HEADING_1"),
    P("Two methods are covered:"),
    P("Method A: over the cable", bullet="l1"),
    P("Method B: from a stick", bullet="l1"),
    P("Buttons", "HEADING_1"),
    T(["Button", "Action"], ["Home", "Back"]),
    P(""),
    P("Contact", "HEADING_1"),
    P(""),
)
SCOPE_DELETES = [{"table": 1}, {"paragraph": "two methods", "through": "method b"}]


def test_a_delete_is_drafted_without_changing_anything_then_done_once_approved(api):
    tab, text = layout(*SCOPE)
    api.serve(as_doc(tab))
    drafted = docs_draft_delete("work", URL, SCOPE_DELETES)
    api.docs().batchUpdate.assert_not_called()
    assert drafted["would"] == "delete 3 paragraphs and 1 table"
    assert drafted["deletes"] == [
        "table 1 (2 rows, first row 'Button | Action') and the empty line after it",
        "3 paragraphs from 'Two methods are covered:' through 'Method B: from a stick': 'Two methods are covered:', "
        "'- Method A: over the cable', '- Method B: from a stick'"]
    assert pending(drafted["draft_id"]).summary.startswith("Delete 3 paragraphs and 1 table from 'Install guide'")

    with pytest.raises(PermissionError):
        docs_delete(drafted["draft_id"])
    api.docs().batchUpdate.assert_not_called()
    approve(drafted["draft_id"])
    said = docs_delete(drafted["draft_id"])
    assert said.startswith("Deleted 3 paragraphs and 1 table from 'Install guide' (work account): table 1")
    body = api.sent()
    assert body["writeControl"] == {"requiredRevisionId": "rev-7"}  # the revision the draft was worked out from
    assert kinds(body["requests"]) == ["deleteContentRange", "deleteContentRange"]
    assert apply(text, body["requests"]) == ["Guide", "Scope", "Buttons", "Contact", ""]
    with pytest.raises(ValueError, match="no draft"):
        docs_delete(drafted["draft_id"])


def test_a_doc_changed_since_the_draft_deletes_nothing(api):
    api.serve(as_doc(layout(*SCOPE)[0]))
    drafted = docs_draft_delete("work", DID, [{"paragraph": "contact"}])
    approve(drafted["draft_id"])
    api.docs().batchUpdate.return_value.execute.side_effect = http_error(
        400, "The required revision ID 'rev-7' does not match the latest revision.")
    with pytest.raises(RuntimeError, match="The doc changed since this edit was worked out, so nothing was changed"):
        docs_delete(drafted["draft_id"])


@pytest.mark.parametrize("items, error", [
    ([], "delete must be a list"),
    ([{"paragraph": "Nowhere"}], "No paragraph matches 'Nowhere'"),
    ([{"table": 2}], "The doc has only 1 table"),
    ([{"table": 1, "paragraph": "Scope"}], "Each item deletes one thing"),
    ([{"table": 1}, {"paragraph": "Nope"}], "^Item 2: No paragraph matches 'Nope'"),
])
def test_bad_deletes_draft_nothing(api, items, error):
    api.serve(as_doc(layout(*SCOPE)[0]))
    with pytest.raises(ValueError, match=error):
        docs_draft_delete("work", DID, items)
    assert pending("anything") is None


def test_images_go_by_number_or_caption(api):
    tab, text = layout(P("Remote", "HEADING_1"), IMG(), P("Figure 1: the remote"), P("Done"), P(""))
    api.serve(as_doc(tab))
    drafted = docs_draft_delete("work", DID, [{"caption": "figure 1"}])
    assert drafted["deletes"] == ["image 1 and its caption 'Figure 1: the remote'"]
    approve(drafted["draft_id"])
    docs_delete(drafted["draft_id"])
    assert apply(text, api.sent()["requests"]) == ["Remote", "Done", ""]


# ── Images ──────────────────────────────────────────────────────

LETTER = {"pageSize": {"width": {"magnitude": 612, "unit": "PT"}, "height": {"magnitude": 792, "unit": "PT"}},
          "marginLeft": {"magnitude": 72, "unit": "PT"}, "marginRight": {"magnitude": 72, "unit": "PT"}}


def test_an_image_from_this_machine_is_shared_by_link_only_while_docs_fetches_it(api, tmp_path):
    tab, text = layout(P("Intro"), P("Installation steps", "HEADING_1"), P("Open the store"))
    tab["documentStyle"] = LETTER
    api.serve(as_doc(tab))
    path = tmp_path / "install-flow.png"
    path.write_bytes(png_bytes(1600, 2000))
    unshared_before_the_edit = []
    api.docs().batchUpdate.return_value.execute.side_effect = lambda: (
        unshared_before_the_edit.append(api.drive().permissions.return_value.delete.called) or {"replies": [{}]})

    said = docs_insert_image("work", URL, [{"image": str(path), "caption": "Figure 1:  the flow"}],
                             where="after", anchor="installation steps")
    assert said == ("Added 1 image after 'Installation steps' in 'Install guide' (work account). Sized to the page "
                    "width, 468 points: install-flow.png. The uploaded copies are in the work account's Drive folder "
                    "'AI Armory doc images', shared with no one.")
    assert api.opened == [("work", "docs", "v1", DOCS), ("work", "drive", "v3", DRIVE_FILE)]
    (_, folder, _), (_, upload, media) = api.uploaded
    assert folder == {"name": "AI Armory doc images", "mimeType": FOLDER}
    assert upload == {"name": "install-flow.png", "parents": ["folder-1"]} and media.mimetype() == "image/png"
    assert unshared_before_the_edit == [False]
    api.drive().permissions.return_value.delete.assert_called_once_with(fileId="img-2", permissionId="anyoneWithLink")
    requests = api.sent()["requests"]
    assert kinds(requests)[-1] == "insertInlineImage"
    assert apply(text, requests) == ["Intro", "Installation steps", "▣", "Figure 1: the flow", "Open the store"]


def test_sharing_is_taken_off_even_when_the_edit_fails(api, tmp_path):
    path = tmp_path / "a.png"
    path.write_bytes(png_bytes(10, 10))
    api.docs().batchUpdate.return_value.execute.side_effect = http_error(500, "backend error")
    with pytest.raises(HttpError):
        docs_insert_image("work", DID, [str(path)])
    api.drive().permissions.return_value.delete.assert_called_once()


@pytest.mark.parametrize("name", ["id_rsa", "server.key", ".env", "client_secret_123.json", "token.json"])
def test_secrets_are_never_uploaded(api, tmp_path, name):
    path = tmp_path / name
    path.write_bytes(png_bytes(10, 10))
    with pytest.raises(ValueError, match="don't upload secrets"):
        docs_insert_image("work", DID, [str(path)])
    assert api.opened == [] and api.uploaded == []


def test_nothing_is_uploaded_from_the_token_folder_or_a_blocked_path(api, google_accounts, tmp_path):
    google_accounts.token_dir.mkdir(parents=True, exist_ok=True)
    inside = google_accounts.token_dir / "picture.png"
    inside.write_bytes(png_bytes(10, 10))
    assert docs_images.secret_path(inside.resolve())
    with pytest.raises(ValueError, match="don't upload secrets"):
        docs_insert_image("work", DID, [str(inside)])
    elsewhere = tmp_path / "secrets" / "x.png"
    assert docs_images.secret_path(elsewhere)
    assert not docs_images.secret_path((tmp_path / "x.png").resolve())


@pytest.mark.parametrize("images, error", [
    (["relative.png"], "isn't a full path"),
    (["ftp://example.com/a.png"], "isn't an https link"),
    ([{"image": "/a.png", "size": 3}], "Unknown size"),
])
def test_bad_images_are_refused_before_anything_is_uploaded(api, images, error):
    with pytest.raises(ValueError, match=error):
        docs_insert_image("work", DID, images)
    assert api.uploaded == []
