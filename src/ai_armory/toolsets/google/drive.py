"""Google Drive across the configured accounts: find any file or folder by its name or by what's in it, in My Drive,
shared with the user and shared drives, with its type, owner, last change and link. Reading only: nothing here
opens, changes or shares a file.

Searching by name needs only Drive metadata (drive.metadata.readonly); searching inside files may need
drive.readonly, and an account whose sign-in doesn't allow it is told to sign in again.
"""

from __future__ import annotations

import re

from ai_armory.toolsets.google import common
from ai_armory.toolsets.google.common import (ACCOUNT_OR_ALL, DRIVE_METADATA, DRIVE_READ, Specs, across, api_errors,
                                              build, drive_quoted, noted)

specs = Specs()

GOOGLE = "application/vnd.google-apps."
# The types drive_search takes, by the name the model gives, and how a file's type is shown.
TYPES = {"doc": GOOGLE + "document", "sheet": GOOGLE + "spreadsheet", "slides": GOOGLE + "presentation",
         "folder": GOOGLE + "folder", "form": GOOGLE + "form", "pdf": "application/pdf"}
PREFIXES = {"image": "image/", "video": "video/", "audio": "audio/"}
SHOWN = {
    GOOGLE + "document": "Google Doc", GOOGLE + "spreadsheet": "Google Sheet", GOOGLE + "presentation": "Google Slides",
    GOOGLE + "folder": "folder", GOOGLE + "form": "Google Form", GOOGLE + "drawing": "Google Drawing",
    GOOGLE + "shortcut": "shortcut", GOOGLE + "site": "Google Site", "application/pdf": "PDF",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word document",
    "application/msword": "Word document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "Excel spreadsheet",
    "application/vnd.ms-excel": "Excel spreadsheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "PowerPoint deck",
    "application/vnd.ms-powerpoint": "PowerPoint deck",
    "text/plain": "text file", "text/csv": "CSV file", "application/zip": "zip file",
}
FOLDER_URL = re.compile(r"/folders/([\w-]+)")


def kind(mime: str) -> str:
    """A file's type in words."""
    if mime in SHOWN:
        return SHOWN[mime]
    for word, prefix in PREFIXES.items():
        if mime.startswith(prefix):
            return word
    return mime.removeprefix(GOOGLE).split("/")[-1] or "file"


def folder_id(folder: str) -> str:
    text = folder.strip()
    if found := FOLDER_URL.search(text):
        return found[1]
    if re.fullmatch(r"[\w-]{10,}", text):
        return text
    raise ValueError(f"'{text}' isn't a folder's id or link. Find the folder with drive_search, type folder, first.")


def query(name: str = "", text: str = "", type: str = "any", shared_with_me: bool = False, folder: str = "") -> str:
    """The Drive search for these, never including the bin."""
    terms = ["trashed=false"]
    if name.strip():
        terms.append(f"name contains {drive_quoted(name)}")
    if text.strip():
        terms.append(f"fullText contains {drive_quoted(text)}")
    if type in TYPES:
        terms.append(f"mimeType={drive_quoted(TYPES[type])}")
    elif type in PREFIXES:
        terms.append(f"mimeType contains {drive_quoted(PREFIXES[type])}")
    if shared_with_me:
        terms.append("sharedWithMe=true")
    if folder.strip():
        terms.append(f"{drive_quoted(folder_id(folder))} in parents")
    return " and ".join(terms)


def _owner(f: dict) -> str | None:
    owners = f.get("owners") or []
    if not owners:
        return "a shared drive" if f.get("driveId") else None
    o = owners[0]
    return "you" if o.get("me") else o.get("displayName") or o.get("emailAddress")


@specs.tool(
    "search",
    "Find files and folders in Google Drive, of any type, by name or by words inside them, most recently changed "
    "first (or best match first when searching inside files): in My Drive, shared with the user and shared drives. "
    "Returns each one's name, type, owner, when it last changed and its link; a doc's, sheet's or deck's id is what "
    "docs_read, sheets_read and slides_read take. Give name, text or both; leave both empty for the latest files.",
    {"account": ACCOUNT_OR_ALL,
     "name": {"type": "string", "description": "Part of the file's name. Not case-sensitive."},
     "text": {"type": "string", "description": "Words inside the file, e.g. a phrase from a doc or PDF."},
     "type": {"type": "string", "enum": ["any", *TYPES, *PREFIXES], "default": "any",
              "description": "Only this kind of file. Default any."},
     "shared_with_me": {"type": "boolean", "description": "Only files other people shared with the user. "
                                                         "Default false."},
     "folder": {"type": "string", "description": "Only what's directly in this folder: its id or link, from a "
                                                "search with type folder. Optional."},
     "max_results": {"type": "integer", "description": "Per account. Default 10."}},
    read_only=True,
)
def drive_search(account: str = "all", name: str = "", text: str = "", type: str = "any",
                 shared_with_me: bool = False, folder: str = "", max_results: int = 10) -> list[dict] | dict:
    q = query(name, text, type, shared_with_me, folder)
    words = bool(text.strip())

    def read(label: str) -> list[dict]:
        drive = common.service(label, "drive", "v3", needs=DRIVE_METADATA)
        options = {"orderBy": "modifiedTime desc"} if not words else {}  # Drive ranks a search inside files itself
        with api_errors(label, DRIVE_READ if words else DRIVE_METADATA):
            files = drive.files().list(
                q=q, pageSize=max(1, min(25, max_results)), corpora="allDrives", includeItemsFromAllDrives=True,
                supportsAllDrives=True, **options,
                fields="files(id,name,mimeType,modifiedTime,webViewLink,driveId,owners(displayName,emailAddress,me))",
            ).execute().get("files", [])
        return [{"account": label, "id": f["id"], "name": f["name"], "type": kind(f.get("mimeType", "")),
                 "owner": _owner(f), "modified": f.get("modifiedTime"), "url": f.get("webViewLink")} for f in files]

    return noted("files", *across(account, read))


def build_toolset():
    return build("drive", "Google Drive across the configured Google accounts: find any file or folder by name or "
                          "content.", specs)


def __getattr__(name: str):
    if name == "toolset":
        return build_toolset()
    raise AttributeError(name)
