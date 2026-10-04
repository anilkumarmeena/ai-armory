"""Google Sheets in the configured accounts: find by name, read, write, append, add tabs, format, and delete a tab.

Uses the spreadsheets scope, and read-only Drive metadata only for finding sheets by name. The one delete is a
tab, one at a time and never the last, in two steps: sheets_draft_delete_tab, then sheets_delete_tab once the user
says yes (see drafts.py). Nothing here deletes a spreadsheet or a row.
"""

from __future__ import annotations

import re

from ai_armory.toolsets.google import common, drafts
from ai_armory.toolsets.google.common import ACCOUNT, SHEETS, Specs, api_errors, build, only

specs = Specs()

SPREADSHEET = "application/vnd.google-apps.spreadsheet"
MAX_ROWS = 200  # rows sheets_read returns at once
URL_ID = re.compile(r"/spreadsheets/(?:u/\d+/)?d/([\w-]+)")
URL_GID = re.compile(r"[#?&]gid=(\d+)")
META_FIELDS = "properties.title,spreadsheetUrl,sheets.properties(sheetId,title,index)"
# The only batchUpdate requests ever sent: deleteSheet only from sheets_delete_tab; deleteDimension,
# deleteRange and the like never are.
BATCH_REQUESTS = {"addSheet", "deleteSheet", "repeatCell"}

SPREADSHEET_ARG = {"type": "string", "description": "The sheet's id from sheets_search, or its full link"}
CONFIRM = ("A change the user asked for needs no further yes. Ask first, in one sentence, only if you chose the sheet "
           "or the cells yourself.")
VALUES = {"type": "array", "items": {"type": "array", "items": {"type": ["string", "number", "boolean", "null"]}},
          "description": "Rows, each a list of cells, e.g. [[\"Date\", \"Amount\"], [\"2026-09-27\", 450]]. "
                         "Cells are taken as if typed into Sheets, so =SUM(B2:B9) is a formula and 450 a number."}
# Named colours are the light shades of Sheets' own palette, so text on them stays readable.
COLOURS = {"red": "#F4CCCC", "orange": "#FCE5CD", "yellow": "#FFF2CC", "green": "#D9EAD3", "blue": "#CFE2F3",
           "purple": "#D9D2E9", "grey": "#EFEFEF", "gray": "#EFEFEF", "white": "#FFFFFF"}
NUMBER_FORMATS = {"number": "NUMBER", "currency": "CURRENCY", "percent": "PERCENT", "date": "DATE", "time": "TIME",
                  "date_time": "DATE_TIME", "scientific": "SCIENTIFIC", "text": "TEXT", "automatic": None}


def _spreadsheet_id(spreadsheet: str) -> tuple[str, int | None]:
    """The id in an id or link, and the tab (gid) the link points at, if any."""
    text = spreadsheet.strip()
    if found := URL_ID.search(text):
        gid = URL_GID.search(text)
        return found[1], int(gid[1]) if gid else None
    if re.fullmatch(r"[\w-]{20,}", text):
        return text, None
    raise ValueError(f"'{text}' isn't a spreadsheet id or link. Find the sheet with sheets_search first.")


def _open(account: str, spreadsheet: str):
    """(the spreadsheets() resource, the id, the tab the link points at, the sheet's title, url and tabs)."""
    sid, gid = _spreadsheet_id(spreadsheet)
    sheets = common.service(account, "sheets", "v4", needs=SHEETS).spreadsheets()
    meta = sheets.get(spreadsheetId=sid, fields=META_FIELDS).execute()
    return sheets, sid, gid, meta


def _tabs(meta: dict) -> list[dict]:
    return [s["properties"] for s in meta.get("sheets", [])]


def _find_tab(meta: dict, name: str) -> dict | None:
    """A tab by name, ignoring case and the quotes A1 notation puts round names."""
    key = name.strip()
    if len(key) > 1 and key[0] == key[-1] == "'":
        key = key[1:-1].replace("''", "'")
    return next((t for t in _tabs(meta) if t["title"].lower() == key.strip().lower()), None)


def _tab(meta: dict, name: str) -> dict:
    if tab := _find_tab(meta, name):
        return tab
    raise ValueError(f"No tab called '{name.strip()}'. The tabs: {', '.join(t['title'] for t in _tabs(meta))}.")


def _default_tab(meta: dict, gid: int | None) -> dict:
    """The tab the link points at, or else the first."""
    tabs = sorted(_tabs(meta), key=lambda t: t.get("index", 0))
    return next((t for t in tabs if t["sheetId"] == gid), tabs[0])


def _target(meta: dict, gid: int | None, range_: str) -> tuple[dict, str]:
    """The tab `range_` is on and its cells in A1 notation ('' for the whole tab)."""
    text = range_.strip()
    if "!" in text:
        name, cells = text.rsplit("!", 1)
        return _tab(meta, name), cells.strip()
    if text and (tab := _find_tab(meta, text)):
        return tab, ""
    return _default_tab(meta, gid), text


def _a1(tab: dict, cells: str = "") -> str:
    """Always quotes the tab name, so names with spaces or punctuation work."""
    name = "'" + tab["title"].replace("'", "''") + "'"
    return f"{name}!{cells}" if cells else name


def _column(letters: str) -> int:
    index = 0
    for ch in letters:
        index = index * 26 + ord(ch) - ord("A") + 1
    return index - 1


def _grid_range(tab: dict, cells: str) -> dict:
    """A1 cells (B2, A1:C10, A:C, 2:5, A2:C) as the GridRange batchUpdate takes; '' is the whole tab."""
    grid = {"sheetId": tab["sheetId"]}
    if not cells:
        return grid
    found = re.fullmatch(r"([A-Z]*)(\d*)(?::([A-Z]*)(\d*))?", cells.upper().replace("$", ""))
    if not found or not (found[1] or found[2]) or "0" in (found[2][:1], (found[4] or "")[:1]):
        raise ValueError(f"'{cells}' isn't a cell range. Use A1 notation such as A1:C10, B:B or 2:2.")
    start_col, start_row, end_col, end_row = found.groups()
    if ":" not in cells:  # one cell, column or row
        end_col, end_row = start_col, start_row
    if start_col:
        grid["startColumnIndex"] = _column(start_col)
    if end_col:
        grid["endColumnIndex"] = _column(end_col) + 1
    if start_row:
        grid["startRowIndex"] = int(start_row) - 1
    if end_row:
        grid["endRowIndex"] = int(end_row)
    return grid


def _rows(values: list) -> list[list]:
    if not values:
        raise ValueError("No values given.")
    if not any(isinstance(v, list) for v in values):
        values = [values]  # one row, given flat
    if not all(isinstance(row, list) for row in values):
        raise ValueError("values must be a list of rows, each a list of cells.")
    return [["" if cell is None else cell for cell in row] for row in values]


def colour(name: str) -> dict | None:
    """A named colour or #RRGGBB as a Sheets (or Docs) colour; None for none, which clears the fill."""
    key = name.strip().lower()
    if key in ("none", "clear", "no colour", "no color"):
        return None
    hexcode = COLOURS.get(key, key).lstrip("#").lower()
    if len(hexcode) == 3:
        hexcode = "".join(ch * 2 for ch in hexcode)
    if not re.fullmatch(r"[0-9a-f]{6}", hexcode):
        raise ValueError(f"Unknown colour '{name}'. Use {', '.join(c for c in COLOURS if c != 'gray')}, none, "
                         "or a hex code such as #FFD966.")
    return {part: int(hexcode[i:i + 2], 16) / 255 for part, i in (("red", 0), ("green", 2), ("blue", 4))}


def _batch_update(sheets, sid: str, requests: list[dict]) -> dict:
    only(requests, BATCH_REQUESTS, "Google Sheets")
    return sheets.batchUpdate(spreadsheetId=sid, body={"requests": requests}).execute()


def _in(meta: dict, account: str) -> str:
    return f"in '{meta['properties']['title']}' ({account} account)"


@specs.tool(
    "search",
    "Find the user's Google Sheets by name in one account's Google Drive, most recently changed first. Returns "
    "each sheet's id and link, which the other sheets_ tools take. Leave name empty to list the latest sheets. "
    "Try another account if nothing turns up.",
    {"account": ACCOUNT,
     "name": {"type": "string", "description": "Part of the sheet's name, e.g. budget. Not case-sensitive."},
     "max_results": {"type": "integer", "description": "Default 10."}},
    read_only=True,
)
def sheets_search(account: str, name: str = "", max_results: int = 10) -> list[dict]:
    return common.drive_find(account, SPREADSHEET, name, max_results)


@specs.tool(
    "read",
    "Read a Google Sheet: its title and tab names, and the values in a range. Without a range it reads the top of "
    f"the first tab (or the tab the link points at), up to {MAX_ROWS} rows. Values come back as shown in Sheets, "
    "unless formulas is true: then cells with a formula give the formula itself, e.g. =IMPORTRANGE(...) or "
    "=SUM(B2:B9), for when the user asks how cells are worked out or where a tab's data comes from.",
    {"account": ACCOUNT, "spreadsheet": SPREADSHEET_ARG,
     "range": {"type": "string", "description": "A tab name, or A1 notation such as Budget!A1:D20 or A1:D20 "
                                                "(first tab). Optional."},
     "formulas": {"type": "boolean", "description": "true gives formulas instead of their results, and other "
                                                    "cells unformatted (450 rather than $450). Default false."}},
    ["spreadsheet"],
    read_only=True,
)
def sheets_read(account: str, spreadsheet: str, range: str = "", formulas: bool = False) -> dict:
    render = {"valueRenderOption": "FORMULA"} if formulas else {}
    with api_errors(account, SHEETS):
        sheets, sid, gid, meta = _open(account, spreadsheet)
        tab, cells = _target(meta, gid, range)
        a1 = _a1(tab, cells or f"1:{MAX_ROWS}")
        got = sheets.values().get(spreadsheetId=sid, range=a1, **render).execute()
    values = got.get("values", [])
    result = {"title": meta["properties"]["title"], "url": meta.get("spreadsheetUrl"),
              "tabs": [t["title"] for t in sorted(_tabs(meta), key=lambda t: t.get("index", 0))],
              "range": got.get("range", a1), "values": values[:MAX_ROWS]}
    if len(values) > MAX_ROWS or (not cells and len(values) == MAX_ROWS):
        result["note"] = f"Only the first {MAX_ROWS} rows. Read on with a range such as {_a1(tab, '201:400')}."
    return result


@specs.tool(
    "write",
    "Write values into a Google Sheet, starting at a cell or filling a range. It overwrites what's there, so read "
    "the cells first if you don't know what's in them. " + CONFIRM,
    {"account": ACCOUNT, "spreadsheet": SPREADSHEET_ARG,
     "range": {"type": "string", "description": "Where to write, in A1 notation: a starting cell such as "
                                                "Budget!B2, or a range such as Budget!B2:D5. No tab means the first."},
     "values": VALUES},
    ["spreadsheet", "range", "values"],
)
def sheets_write(account: str, spreadsheet: str, range: str, values: list) -> str:
    rows = _rows(values)
    with api_errors(account, SHEETS):
        sheets, sid, gid, meta = _open(account, spreadsheet)
        tab, cells = _target(meta, gid, range)
        done = sheets.values().update(spreadsheetId=sid, range=_a1(tab, cells or "A1"),
                                      valueInputOption="USER_ENTERED", body={"values": rows}).execute()
    count = done.get("updatedCells", sum(len(r) for r in rows))
    return f"Wrote {count} cell{'s' if count != 1 else ''} to {done.get('updatedRange')} {_in(meta, account)}."


@specs.tool(
    "append",
    "Add rows to the end of the table on one tab of a Google Sheet, below the last row with data. Nothing already "
    "there changes. " + CONFIRM,
    {"account": ACCOUNT, "spreadsheet": SPREADSHEET_ARG, "values": VALUES,
     "tab": {"type": "string", "description": "The tab's name. Default: the first tab, or the one the link points at."}},
    ["spreadsheet", "values"],
)
def sheets_append(account: str, spreadsheet: str, values: list, tab: str = "") -> str:
    rows = _rows(values)
    with api_errors(account, SHEETS):
        sheets, sid, gid, meta = _open(account, spreadsheet)
        target = _tab(meta, tab) if tab.strip() else _default_tab(meta, gid)
        done = sheets.values().append(spreadsheetId=sid, range=_a1(target), valueInputOption="USER_ENTERED",
                                      insertDataOption="INSERT_ROWS", body={"values": rows}).execute()
    where = done.get("updates", {}).get("updatedRange", _a1(target))
    return f"Added {len(rows)} row{'s' if len(rows) != 1 else ''} at {where} {_in(meta, account)}."


@specs.tool(
    "add_tab",
    "Add a new, empty tab to a Google Sheet. " + CONFIRM,
    {"account": ACCOUNT, "spreadsheet": SPREADSHEET_ARG, "title": {"type": "string", "description": "The new tab's name"}},
    ["spreadsheet", "title"],
)
def sheets_add_tab(account: str, spreadsheet: str, title: str) -> str:
    name = title.strip()
    if not name:
        raise ValueError("The new tab needs a name.")
    with api_errors(account, SHEETS):
        sheets, sid, _gid, meta = _open(account, spreadsheet)
        if existing := _find_tab(meta, name):
            raise ValueError(f"There's already a tab called '{existing['title']}'.")
        _batch_update(sheets, sid, [{"addSheet": {"properties": {"title": name}}}])
    return f"Added a tab called '{name}' {_in(meta, account)}."


@specs.tool(
    "draft_delete_tab",
    "Prepare deleting one tab of a Google Sheet, with everything on it. It deletes nothing: call sheets_delete_tab "
    "with its draft_id, which goes ahead only after the user has heard which tab of which sheet and said yes. The "
    "last tab of a sheet can't be deleted.",
    {"account": ACCOUNT, "spreadsheet": SPREADSHEET_ARG,
     "tab": {"type": "string", "description": "The name of the tab to delete"}},
    ["spreadsheet", "tab"],
    read_only=True,
)
def sheets_draft_delete_tab(account: str, spreadsheet: str, tab: str) -> dict:
    if not tab.strip():
        raise ValueError("Say which tab to delete.")
    with api_errors(account, SHEETS):
        _sheets, sid, _gid, meta = _open(account, spreadsheet)
    target = _tab(meta, tab)
    if len(_tabs(meta)) == 1:
        raise ValueError(f"'{target['title']}' is the only tab {_in(meta, account)}, and a sheet needs at least one.")
    summary = (f"Delete the tab '{target['title']}' {_in(meta, account)}, with everything on it. Only the sheet's "
               "version history can bring it back, and a shared sheet changes for everyone.")
    draft = drafts.keep("sheets_delete_tab", account, summary, f"delete tab {target['title']}",
                        {"spreadsheet": sid, "sheet_id": target["sheetId"], "title": target["title"]})
    return drafts.reply(draft, "sheets_delete_tab", tab=target["title"], sheet=meta["properties"]["title"])


@specs.tool(
    "delete_tab",
    "Delete the tab drafted with sheets_draft_delete_tab, exactly as drafted: it takes only the draft_id. It's held "
    "until the user has heard the draft and said yes, which the host asks for itself.",
    {"draft_id": {"type": "string", "description": "From sheets_draft_delete_tab"}},
    ["draft_id"],
    needs_confirmation=True,
)
def sheets_delete_tab(draft_id: str) -> str:
    d = drafts.take(draft_id, "sheets_delete_tab", "sheets_draft_delete_tab")
    account, sheet_id = d.account, d.payload["sheet_id"]
    with api_errors(account, SHEETS):
        sheets, sid, _gid, meta = _open(account, d.payload["spreadsheet"])
        # Checked again by its id, which a rename doesn't change: the tab is still there, and isn't the last.
        target = next((t for t in _tabs(meta) if t["sheetId"] == sheet_id), None)
        if target is None:
            return f"The tab '{d.payload['title']}' is no longer {_in(meta, account)}: nothing deleted."
        if len(_tabs(meta)) == 1:
            raise ValueError(f"'{target['title']}' is now the only tab {_in(meta, account)}, so it stays.")
        _batch_update(sheets, sid, [{"deleteSheet": {"sheetId": sheet_id}}])
    return (f"Deleted the tab '{target['title']}' {_in(meta, account)}. "
            "It can only be brought back from the sheet's version history.")


@specs.tool(
    "format",
    "Format cells in a Google Sheet: bold, background colour and number format, any of them in one go. Other "
    "formatting is left as it is. " + CONFIRM,
    {"account": ACCOUNT, "spreadsheet": SPREADSHEET_ARG,
     "range": {"type": "string", "description": "A tab name, or A1 notation such as Budget!A1:D1, Budget!B:B "
                                                "(a column) or Budget!1:1 (a row). No tab means the first."},
     "bold": {"type": "boolean", "description": "true makes the text bold, false not. Omit to leave as is."},
     "background": {"type": "string", "description": "red, orange, yellow, green, blue, purple, grey or white (the "
                                                     "light shades from Sheets' palette), a hex code such as #FFD966, "
                                                     "or none to clear the fill. Omit to leave as is."},
     "number_format": {"type": "string", "enum": list(NUMBER_FORMATS),
                       "description": "automatic clears it. Omit to leave as is."},
     "pattern": {"type": "string", "description": "Optional custom pattern for number_format, e.g. #,##0.00, "
                                                 "0.0%, dd mmm yyyy or $#,##0"}},
    ["spreadsheet", "range"],
)
def sheets_format(account: str, spreadsheet: str, range: str, bold: bool | None = None,
                  background: str | None = None, number_format: str | None = None, pattern: str = "") -> str:
    # Everything is checked before the sheet is opened, so a bad colour or format changes nothing.
    style, fields, done = {}, [], []
    if bold is not None:
        style["textFormat"] = {"bold": bold}
        fields.append("userEnteredFormat.textFormat.bold")
        done.append("bold" if bold else "not bold")
    if background is not None:
        if fill := colour(background):
            style["backgroundColor"] = fill
        fields.append("userEnteredFormat.backgroundColor")
        done.append(f"{background.strip()} background" if fill else "no background colour")
    if number_format or pattern.strip():
        kind = (number_format or "number").strip().lower()
        if kind not in NUMBER_FORMATS:
            raise ValueError(f"Unknown number format '{number_format}'. Use one of: {', '.join(NUMBER_FORMATS)}.")
        if NUMBER_FORMATS[kind]:
            style["numberFormat"] = {"type": NUMBER_FORMATS[kind], **({"pattern": pattern.strip()} if pattern.strip() else {})}
        elif pattern.strip():
            raise ValueError("A pattern needs a number format other than automatic.")
        fields.append("userEnteredFormat.numberFormat")
        done.append(f"{kind.replace('_', ' ')} format" + (f" {pattern.strip()}" if pattern.strip() else ""))
    if not fields:
        raise ValueError("Nothing to change: set bold, background, number_format or pattern.")

    with api_errors(account, SHEETS):
        sheets, sid, gid, meta = _open(account, spreadsheet)
        tab, cells = _target(meta, gid, range)
        _batch_update(sheets, sid, [{"repeatCell": {
            "range": _grid_range(tab, cells), "cell": {"userEnteredFormat": style}, "fields": ",".join(fields)}}])
    return f"Formatted {_a1(tab, cells)} {_in(meta, account)}: {', '.join(done)}."


def build_toolset():
    return build("sheets", "Google Sheets in the configured Google accounts: find, read, write, format and tabs.",
                 specs)


def __getattr__(name: str):
    if name == "toolset":
        return build_toolset()
    raise AttributeError(name)
