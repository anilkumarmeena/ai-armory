"""Google Drive search and Google Slides, with the Drive and Slides APIs mocked: any file by name or content, and a
deck read slide by slide with its speaker notes."""

import pytest
from google_fakes import Services, http_error, write_token

from ai_armory.toolsets.google import common, slides
from ai_armory.toolsets.google.common import DRIVE_METADATA, DRIVE_READ, SCOPES
from ai_armory.toolsets.google.drive import drive_search, query
from ai_armory.toolsets.google.slides import slides_read, slides_search

FILES = [
    {"id": "f1", "name": "Q3 plan", "mimeType": "application/vnd.google-apps.presentation",
     "modifiedTime": "2026-10-05T10:00:00Z", "webViewLink": "https://docs.google.com/presentation/d/f1",
     "owners": [{"displayName": "Ana Rao", "me": False}]},
    {"id": "f2", "name": "Invoice.pdf", "mimeType": "application/pdf", "modifiedTime": "2026-10-04T10:00:00Z",
     "webViewLink": "https://drive.google.com/file/d/f2", "owners": [{"displayName": "Me", "me": True}]},
    {"id": "f3", "name": "Team", "mimeType": "application/vnd.google-apps.folder", "driveId": "d1",
     "modifiedTime": "2026-10-03T10:00:00Z", "webViewLink": "https://drive.google.com/drive/folders/f3"},
    {"id": "f4", "name": "photo.jpg", "mimeType": "image/jpeg", "modifiedTime": "2026-10-02T10:00:00Z",
     "owners": [{"emailAddress": "bo@example.com"}]},
]


@pytest.fixture
def api(google_accounts, monkeypatch):
    services = Services()
    services.files = lambda label="work": services.client(label, "drive").files.return_value
    services.decks = lambda: services.client("work", "slides").presentations.return_value
    monkeypatch.setattr(common, "service", services)
    return services


# ── Drive ───────────────────────────────────────────────────────

def test_any_file_is_found_with_its_type_owner_change_and_link(api):
    api.files("work").list.return_value.execute.return_value = {"files": FILES}
    api.files("personal").list.return_value.execute.return_value = {"files": []}
    found = drive_search(name="plan")
    assert [(f["name"], f["type"], f["owner"]) for f in found] == [
        ("Q3 plan", "Google Slides", "Ana Rao"), ("Invoice.pdf", "PDF", "you"), ("Team", "folder", "a shared drive"),
        ("photo.jpg", "image", "bo@example.com")]
    assert found[0] == {"account": "work", "id": "f1", "name": "Q3 plan", "type": "Google Slides",
                        "owner": "Ana Rao", "modified": "2026-10-05T10:00:00Z",
                        "url": "https://docs.google.com/presentation/d/f1"}
    asked = api.files("work").list.call_args.kwargs
    assert asked["q"] == "trashed=false and name contains 'plan'" and asked["orderBy"] == "modifiedTime desc"
    assert asked["corpora"] == "allDrives" and asked["includeItemsFromAllDrives"] and asked["supportsAllDrives"]
    assert ("work", "drive", "v3", DRIVE_METADATA) in api.opened


def test_searching_inside_files_is_ranked_by_drive(api):
    api.files().list.return_value.execute.return_value = {"files": []}
    drive_search(account="work", text="O'Brien budget", type="pdf", shared_with_me=True,
                 folder="https://drive.google.com/drive/folders/abc123DEF456")
    asked = api.files().list.call_args.kwargs
    assert asked["q"] == ("trashed=false and fullText contains 'O\\'Brien budget' and mimeType='application/pdf' "
                          "and sharedWithMe=true and 'abc123DEF456' in parents")
    assert "orderBy" not in asked  # Drive doesn't sort a search inside files


def test_kinds_of_files_by_name():
    assert query(type="image") == "trashed=false and mimeType contains 'image/'"
    assert query(type="folder") == "trashed=false and mimeType='application/vnd.google-apps.folder'"
    with pytest.raises(ValueError, match="isn't a folder's id or link"):
        query(folder="my stuff")


def test_a_sign_in_that_cant_search_inside_files_says_to_sign_in_again(api):
    api.files().list.return_value.execute.side_effect = http_error(403, "Request had insufficient authentication "
                                                                        "scopes.")
    with pytest.raises(RuntimeError, match="search inside Google Drive files .* Sign it in again with: sign-in work"):
        drive_search(account="work", text="budget")


# ── Slides ──────────────────────────────────────────────────────

def _text(*paragraphs):
    """A shape's text: each paragraph a string, or (level, string) for a bullet."""
    elements = []
    for p in paragraphs:
        level, words = p if isinstance(p, tuple) else (None, p)
        elements.append({"paragraphMarker": {"bullet": {"nestingLevel": level}} if level is not None else {}})
        elements.append({"textRun": {"content": words + "\n"}})
    return {"textElements": elements}


def _shape(y, *paragraphs, placeholder=""):
    shape = {"text": _text(*paragraphs)}
    if placeholder:
        shape["placeholder"] = {"type": placeholder}
    return {"objectId": f"s{y}", "transform": {"translateY": y * 100_000, "translateX": 0}, "shape": shape}


def _slide(*elements, notes=(), skipped=False):
    page = {"notesProperties": {"speakerNotesObjectId": "n1"},
            "pageElements": [{"objectId": "n0", "shape": {"text": _text("not the notes")}},
                             {"objectId": "n1", "shape": {"text": _text(*notes)}}]}
    return {"pageElements": list(elements), "slideProperties": {"notesPage": page, "isSkipped": skipped}}


DECK = {"title": "Q3 plan", "slides": [
    _slide(_shape(5, "Revenue up", (0, "EMEA"), (1, "Germany")), _shape(1, "Where we are", placeholder="TITLE"),
           _shape(9, "7", placeholder="SLIDE_NUMBER"),
           {"transform": {"translateY": 800_000}, "table": {"tableRows": [
               {"tableCells": [{"text": _text("Region")}, {"text": _text("Growth")}]},
               {"tableCells": [{"text": _text("EMEA")}, {"text": _text("12%")}]}]}},
           {"transform": {"translateY": 900_000}, "image": {}, "description": "Revenue chart"},
           {"transform": {"translateY": 950_000}, "image": {}},
           notes=("Mention the hiring freeze.",)),
    _slide({"elementGroup": {"children": [_shape(1, "Next steps", placeholder="CENTERED_TITLE"),
                                          _shape(2, "Ship v2")]}}, skipped=True),
]}


def test_a_deck_is_read_slide_by_slide_with_speaker_notes(api):
    api.decks().get.return_value.execute.return_value = DECK
    read = slides_read("work", "https://docs.google.com/presentation/d/1AbCdEfGhIjKlMnOpQrStUv/edit#slide=id.p")
    assert api.decks().get.call_args.kwargs == {"presentationId": "1AbCdEfGhIjKlMnOpQrStUv"}
    assert ("work", "slides", "v1", DRIVE_READ) in api.opened
    assert read["title"] == "Q3 plan" and read["slides"] == 2 and "note" not in read
    assert read["url"] == "https://docs.google.com/presentation/d/1AbCdEfGhIjKlMnOpQrStUv/edit"
    assert read["text"] == "\n".join([
        "=== Slide 1: Where we are ===",
        "Revenue up",
        "- EMEA",
        "  - Germany",
        "| Region | Growth |",
        "| EMEA | 12% |",
        "[image: Revenue chart]",
        "Speaker notes:",
        "Mention the hiring freeze.",
        "",
        "=== Slide 2: Next steps (skipped in the slideshow) ===",
        "Ship v2",
    ])


def test_a_long_deck_comes_back_in_parts(api, monkeypatch):
    monkeypatch.setattr(slides, "MAX_CHARS", 60)
    api.decks().get.return_value.execute.return_value = {"title": "Long", "slides": [
        _slide(_shape(1, f"Point number {n} of the deck")) for n in range(1, 6)]}
    first = slides_read("work", "1AbCdEfGhIjKlMnOpQrStUv")
    assert first["text"].count("=== Slide") == 1
    assert first["note"] == "Truncated: slides 1 to 1 of 5. Read on with from_slide=2."
    rest = slides_read("work", "1AbCdEfGhIjKlMnOpQrStUv", from_slide=5)
    assert rest["text"].startswith("=== Slide 5 ===") and "note" not in rest
    assert slides_read("work", "1AbCdEfGhIjKlMnOpQrStUv", from_slide=9)["note"] == "The deck has only 5 slides."


def test_slides_need_a_sign_in_that_reads_drive_files(google_accounts):
    write_token(google_accounts, "work", [s for s in SCOPES if s != DRIVE_READ])
    with pytest.raises(RuntimeError, match="read Google Slides. Sign it in again with: sign-in work"):
        slides_read("work", "1AbCdEfGhIjKlMnOpQrStUv")


def test_decks_are_found_by_name_and_bad_ids_refused(api):
    api.files().list.return_value.execute.return_value = {"files": []}
    assert slides_search("work", "plan") == []
    assert "mimeType='application/vnd.google-apps.presentation'" in api.files().list.call_args.kwargs["q"]
    with pytest.raises(ValueError, match="isn't a Google Slides id or link"):
        slides_read("work", "my deck")
