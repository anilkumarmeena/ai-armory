"""Google Sheets, with the Sheets and Drive APIs mocked."""

import json

import pytest
from google_fakes import Services

from ai_armory.toolsets.google import approve, common, sheets
from ai_armory.toolsets.google.common import DRIVE_METADATA, SHEETS
from ai_armory.toolsets.google.sheets import (sheets_add_tab, sheets_append, sheets_delete_tab,
                                              sheets_draft_delete_tab, sheets_format, sheets_read, sheets_search,
                                              sheets_write)

SID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
URL = f"https://docs.google.com/spreadsheets/d/{SID}/edit#gid=7"
META = {"properties": {"title": "Budget 2026"}, "spreadsheetUrl": f"https://docs.google.com/spreadsheets/d/{SID}/edit",
        "sheets": [{"properties": {"sheetId": 0, "title": "Summary", "index": 0}},
                   {"properties": {"sheetId": 7, "title": "Q3 Spend", "index": 1}},
                   {"properties": {"sheetId": 9, "title": "Tom's", "index": 2}}]}


def _setup(api, mock):
    book = mock.spreadsheets.return_value
    book.get.return_value.execute.return_value = json.loads(json.dumps(META))
    book.values.return_value.get.return_value.execute.return_value = {
        "range": "'Summary'!A1:B2", "values": [["Item", "Cost"], ["Rent", "$400"]]}
    book.values.return_value.update.return_value.execute.return_value = {
        "updatedRange": "'Q3 Spend'!B2:C3", "updatedCells": 4}
    book.values.return_value.append.return_value.execute.return_value = {
        "updates": {"updatedRange": "'Summary'!A3:B3"}}
    mock.files.return_value.list.return_value.execute.return_value = {"files": [
        {"id": SID, "name": "Budget 2026", "modifiedTime": "2026-09-26T10:00:00Z",
         "webViewLink": URL, "owners": [{"displayName": "Me", "me": True}]},
        {"id": "shared1", "name": "Team budget", "webViewLink": "https://x", "owners": [{"displayName": "Riley"}]}]}


@pytest.fixture
def api(google_accounts, monkeypatch):
    services = Services(_setup)
    services.book = lambda label="work": services.client(label, "sheets").spreadsheets.return_value
    services.values = lambda label="work": services.book(label).values.return_value
    services.drive = lambda label="work": services.client(label, "drive").files.return_value
    monkeypatch.setattr(common, "service", services)
    return services


def batch_requests(api):
    return api.book().batchUpdate.call_args.kwargs["body"]["requests"]


def test_search_lists_spreadsheets_by_name_with_metadata_only(api):
    found = sheets_search("work", " Tom's \\ plan ", max_results=500)
    assert api.opened == [("work", "drive", "v3", DRIVE_METADATA)]
    call = api.drive().list.call_args.kwargs
    assert call["q"] == ("mimeType='application/vnd.google-apps.spreadsheet' and trashed=false and "
                         "name contains 'Tom\\'s \\\\ plan'")
    assert call["pageSize"] == 25 and call["corpora"] == "allDrives"
    assert [(f["name"], f["owner"]) for f in found] == [("Budget 2026", "you"), ("Team budget", "Riley")]


@pytest.mark.parametrize("given, expected", [
    (SID, (SID, None)), (URL, (SID, 7)),
    (f"https://docs.google.com/spreadsheets/u/1/d/{SID}/edit?usp=sharing&gid=42", (SID, 42)),
])
def test_an_id_or_a_full_link_is_accepted(given, expected):
    assert sheets._spreadsheet_id(given) == expected


def test_anything_else_is_refused_before_any_api_call(api):
    with pytest.raises(ValueError, match="isn't a spreadsheet id or link"):
        sheets_read("work", "budget")
    assert api.opened == []


def test_read_defaults_to_the_tab_the_link_points_at(api):
    result = sheets_read("work", URL)
    assert api.opened == [("work", "sheets", "v4", SHEETS)]
    assert api.values().get.call_args.kwargs == {"spreadsheetId": SID, "range": "'Q3 Spend'!1:200"}
    assert result["tabs"] == ["Summary", "Q3 Spend", "Tom's"] and result["values"][1] == ["Rent", "$400"]
    sheets_read("work", SID, range="tom's!A1:B2", formulas=True)
    assert api.values().get.call_args.kwargs == {"spreadsheetId": SID, "range": "'Tom''s'!A1:B2",
                                                 "valueRenderOption": "FORMULA"}


def test_write_and_append_take_values_as_typed(api):
    said = sheets_write("work", SID, "Q3 Spend!B2", [["Rent", 400], ["Food", None]])
    assert said == "Wrote 4 cells to 'Q3 Spend'!B2:C3 in 'Budget 2026' (work account)."
    assert api.values().update.call_args.kwargs["body"] == {"values": [["Rent", 400], ["Food", ""]]}
    assert api.values().update.call_args.kwargs["valueInputOption"] == "USER_ENTERED"
    said = sheets_append("work", SID, ["2026-10-05", 12])  # one row, given flat
    assert said == "Added 1 row at 'Summary'!A3:B3 in 'Budget 2026' (work account)."
    assert api.values().append.call_args.kwargs["insertDataOption"] == "INSERT_ROWS"


def test_add_tab_refuses_a_name_already_there(api):
    with pytest.raises(ValueError, match="already a tab called 'Summary'"):
        sheets_add_tab("work", SID, "summary")
    sheets_add_tab("work", SID, "Q4")
    assert batch_requests(api) == [{"addSheet": {"properties": {"title": "Q4"}}}]


def test_format_checks_everything_before_opening_the_sheet(api):
    with pytest.raises(ValueError, match="Unknown colour 'teal'"):
        sheets_format("work", SID, "A1", background="teal")
    assert api.opened == []
    said = sheets_format("work", SID, "Q3 Spend!B:B", bold=True, background="yellow", number_format="currency",
                         pattern="$#,##0")
    assert said == ("Formatted 'Q3 Spend'!B:B in 'Budget 2026' (work account): bold, yellow background, currency "
                    "format $#,##0.")
    (request,) = batch_requests(api)
    assert request["repeatCell"]["range"] == {"sheetId": 7, "startColumnIndex": 1, "endColumnIndex": 2}
    assert request["repeatCell"]["fields"] == ("userEnteredFormat.textFormat.bold,userEnteredFormat.backgroundColor,"
                                               "userEnteredFormat.numberFormat")


def test_deleting_a_tab_is_drafted_then_done_only_once_approved(api):
    drafted = sheets_draft_delete_tab("work", URL, "q3 spend")
    assert drafted["summary"] == ("Delete the tab 'Q3 Spend' in 'Budget 2026' (work account), with everything on it. "
                                  "Only the sheet's version history can bring it back, and a shared sheet changes "
                                  "for everyone.")
    api.book().batchUpdate.assert_not_called()
    with pytest.raises(PermissionError):
        sheets_delete_tab(drafted["draft_id"])
    api.book().batchUpdate.assert_not_called()
    approve(drafted["draft_id"])
    said = sheets_delete_tab(drafted["draft_id"])
    assert said.startswith("Deleted the tab 'Q3 Spend' in 'Budget 2026' (work account).")
    assert batch_requests(api) == [{"deleteSheet": {"sheetId": 7}}]


def test_a_tab_gone_since_the_draft_is_left_alone(api):
    drafted = sheets_draft_delete_tab("work", SID, "Tom's")
    api.book().get.return_value.execute.return_value = {**META, "sheets": META["sheets"][:2]}
    approve(drafted["draft_id"])
    assert sheets_delete_tab(drafted["draft_id"]) == ("The tab 'Tom's' is no longer in 'Budget 2026' (work account): "
                                                      "nothing deleted.")
    api.book().batchUpdate.assert_not_called()


def test_the_last_tab_is_never_deleted(api):
    api.book().get.return_value.execute.return_value = {**META, "sheets": META["sheets"][:1]}
    with pytest.raises(ValueError, match="only tab"):
        sheets_draft_delete_tab("work", SID, "Summary")


def test_only_the_three_known_batch_requests_are_ever_sent():
    with pytest.raises(ValueError, match="don't send deleteDimension to Google Sheets"):
        sheets._batch_update(None, SID, [{"deleteDimension": {}}])
