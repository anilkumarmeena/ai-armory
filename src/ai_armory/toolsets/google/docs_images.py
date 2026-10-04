"""Images in Google Docs: docs_insert_image puts PNG, JPG or GIF images, from files on this machine or https links,
into a doc, each in a paragraph of its own with an optional caption under it.

Docs' insertInlineImage only takes a link Google can fetch without signing in, and the doc keeps its own copy of the
image it fetched. So an image from this machine goes to the account's Google Drive first, into the image folder
(``image_folder`` in the settings; the drive.file scope: the tools see only the files they made), is shared with
anyone who has the link just while the doc fetches it through the file's download link, and has that sharing taken
off straight after, whether the edit went through or not. The Drive copy stays there, shared with no one. An https
link goes to Docs as it is; the tool fetches it first too, to check it's an image and to size it, and if Docs can't
fetch it, holds that copy in Drive the same way instead. Files that look like secrets (keys, tokens, anything in a
secrets folder, the sign-in tokens' folder) are never uploaded. Positions and the revision pin work as in
docs_write.py.
"""

from __future__ import annotations

import re
import struct
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ai_armory.toolsets.google import common, docs_write
from ai_armory.toolsets.google.common import ACCOUNT, ANY_ACCOUNT, DRIVE_FILE, Specs, api_errors, settings

specs = Specs()

FOLDER = "application/vnd.google-apps.folder"
MAX_IMAGES = 20
MAX_BYTES = 50 * 1024 * 1024  # Docs' limits for one image
MAX_PIXELS = 25_000_000
MAX_URI = 2000  # characters of a link Docs takes
PAGE_WIDTH = 468.0  # points between the margins of a Letter page with 1-inch margins, for a doc that doesn't say
POINTS_PER_PIXEL = 0.75  # Docs shows an image at 96 pixels an inch, and a point is 1/72 of one
CAPTION_COLOUR = "#5F6368"  # grey
FETCH_TRIES = 3  # link sharing can take a moment to reach the fetcher Docs uses
FETCH_WAIT = 2  # seconds between tries
FAILED_IMAGE = re.compile(r"requests\[(\d+)\]\.insertInlineImage")

# Never uploaded: logins' folders, folders called secrets and the like, and files named like keys or tokens.
HOME_SECRETS = ["~/.ssh", "~/.aws", "~/.gnupg", "~/.config/gh", "~/.netrc", "~/.docker/config.json",
                "~/Library/Keychains", "~/.git-credentials", "~/.npmrc", "~/.pypirc"]
SECRET_DIRS = {"secrets", ".ssh", ".aws", ".gnupg", "Keychains"}
SECRET_FILES = re.compile(
    r"\.env(\..*)?|.+\.(pem|key|p8|p12|pfx|jks|keystore)|id_(rsa|dsa|ecdsa|ed25519)|\.netrc|\.npmrc"
    r"|\.pypirc|\.git-credentials|credentials(\.json)?|.*client_secret.*\.json|tokens?\.json", re.IGNORECASE)


def secret_path(path: Path) -> bool:
    """Whether `path` (resolved) looks like a secret, or is among the sign-in tokens or the blocked paths."""
    s = settings()
    folders = [*HOME_SECRETS, str(s.token_dir), *s.blocked_paths]
    if s.client_secret:
        folders.append(s.client_secret)
    if any(path.is_relative_to(Path(d).expanduser().resolve()) for d in folders):
        return True
    return bool(SECRET_DIRS.intersection(path.parts)) or bool(SECRET_FILES.fullmatch(path.name))


@dataclass
class _Image:
    """One image to put in, read or fetched and checked before anything changes."""

    name: str  # its file name, for Drive and for saying which image
    data: bytes
    mime: str
    width: int  # pixels
    height: int
    caption: str
    points: float | None  # the width asked for
    where: str
    anchor: str
    link: str | None  # an https link Docs can fetch it from as it is; None holds it in Drive
    uri: str = ""  # what Docs fetches it from
    next_to: docs_write.Para | None = None

    @property
    def wanted(self) -> float:
        """Its width in points, as asked for, else its own."""
        return float(self.points or self.width * POINTS_PER_PIXEL)


def _kind(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def _pixels(data: bytes, mime: str) -> tuple[int, int]:
    """(width, height) from the image's header."""
    if mime == "image/png":
        return struct.unpack(">II", data[16:24])
    if mime == "image/gif":
        return struct.unpack("<HH", data[6:10])
    i = 2  # JPEG: the size is in the first start-of-frame segment
    while i + 9 <= len(data):
        if data[i] != 0xFF:
            break
        marker = data[i + 1]
        if marker == 0xFF:  # padding
            i += 1
        elif marker == 0x01 or 0xD0 <= marker <= 0xD9:  # markers with no length
            i += 2
        elif 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return width, height
        else:
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    raise ValueError("its size couldn't be read: the JPG looks damaged.")


def _fetch(url: str) -> bytes:
    data = bytearray()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ai-armory"}),
                                    timeout=30) as reply:
            while chunk := reply.read(65536):
                data += chunk
                if len(data) > MAX_BYTES:
                    raise ValueError(f"the image at {url} is over Google Docs' limit of 50 MB.")
    except (urllib.error.URLError, OSError) as e:
        raise ValueError(f"couldn't fetch {url}: {e}") from None
    return bytes(data)


def _read(source: str) -> tuple[str, bytes, str | None]:
    """An image's name and bytes, and for an https link, the link."""
    text = source.strip()
    if text.lower().startswith("https://"):
        name = text.split("?")[0].split("#")[0].rstrip("/").rsplit("/", 1)[-1] or "image"
        return name, _fetch(text), text
    if re.match(r"[a-z][\w+.-]*://", text, re.I):
        raise ValueError(f"'{text}' isn't an https link. Give an https link or the image's full path.")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise ValueError(f"'{text}' isn't a full path. Give the image's whole path, e.g. ~/Desktop/steps.png.")
    path = path.resolve()
    if secret_path(path):
        raise ValueError("These tools don't upload secrets: keys, logins or anything in a secrets folder.")
    if not path.is_file():
        raise ValueError(f"There's no file at {path}.")
    if path.stat().st_size > MAX_BYTES:
        raise ValueError(f"{path.name} is over Google Docs' limit of 50 MB.")
    return path.name, path.read_bytes(), None


def _load(item: dict) -> _Image:
    name, data, link = _read(item["image"])
    mime = _kind(data)
    if not mime:
        hint = " Google Docs can't take SVG: give a PNG copy." if name.lower().endswith(".svg") else ""
        raise ValueError(f"{name} isn't a PNG, JPG or GIF image.{hint}")
    try:
        width, height = _pixels(data, mime)
    except (ValueError, struct.error) as e:
        raise ValueError(f"{name}: {e if isinstance(e, ValueError) else 'its size could not be read.'}") from None
    if width * height > MAX_PIXELS:
        raise ValueError(f"{name} is {width:,} by {height:,} pixels; Google Docs takes at most 25 megapixels.")
    return _Image(name, data, mime, width, height, item["caption"], item["width"], item["where"], item["anchor"],
                  link if link and len(link) <= MAX_URI else None)


ITEM_KEYS = {"image", "caption", "width", "where", "anchor"}


def _item(item, where: str, anchor: str) -> dict:
    if isinstance(item, str):
        item = {"image": item}
    if not isinstance(item, dict) or not isinstance(item.get("image"), str) or not item["image"].strip():
        raise ValueError("Each image is a full path or an https link, or an object with image and optionally "
                         "caption, width, where and anchor.")
    if unknown := set(item) - ITEM_KEYS:
        raise ValueError(f"Unknown {', '.join(sorted(unknown))}.")
    caption, width = item.get("caption", ""), item.get("width")
    place, spot = item.get("where", where), item.get("anchor", anchor)
    if not isinstance(caption, str) or not isinstance(spot, str):
        raise ValueError("caption and anchor must be text.")
    if width is not None and (isinstance(width, bool) or not isinstance(width, (int, float)) or width <= 0):
        raise ValueError("width is in points and more than 0, e.g. 300.")
    docs_write.check_where(place, spot, "it")
    return {"image": item["image"], "caption": " ".join(caption.split()), "width": width, "where": place,
            "anchor": spot}


def _items(images: list, where: str, anchor: str) -> list[dict]:
    if not images or not isinstance(images, list):
        raise ValueError("No images to add.")
    if len(images) > MAX_IMAGES:
        raise ValueError(f"That's more than {MAX_IMAGES} images; add them in parts.")
    return docs_write.each("Image", images, lambda item: _item(item, where, anchor))


def _page_width(doc: docs_write.Doc) -> float:
    """Points between the page's side margins."""
    style = doc.document_style
    page = style.get("pageSize", {}).get("width", {}).get("magnitude")
    if not page:
        return PAGE_WIDTH
    margins = sum(style.get(side, {}).get("magnitude", 72) for side in ("marginLeft", "marginRight"))
    return max(72.0, page - margins)


def _groups(doc: docs_write.Doc, images: list[_Image]) -> list[tuple[str, docs_write.Para, list[_Image]]]:
    """The images by where they go, in order; those going in at one place go in together."""
    groups: dict[tuple[str, int], tuple[str, docs_write.Para, list[_Image]]] = {}
    spots = docs_write.each("Image", images, lambda image: docs_write.next_to(doc, image.where, image.anchor))
    for image, beside in zip(images, spots):
        image.next_to = beside
        # "end" goes after the last paragraph unless that's an empty line to fill, and "start" before the first.
        where = {"start": "before"}.get(image.where, image.where)
        if where == "end" and not beside.empty:
            where = "after"
        groups.setdefault((where, beside.start), (where, beside, []))[2].append(image)
    return list(groups.values())


def _requests(doc: docs_write.Doc, groups: list, page: float) -> tuple[list[dict], dict[int, _Image]]:
    """The batch, and which image each insertInlineImage in it is, by its place in the batch."""
    plan, which = docs_write.Plan(), {}
    for where, beside, members in groups:
        lines = []
        for image in members:
            lines += ["", image.caption] if image.caption else [""]
        at, requests, spans = docs_write.new_paragraphs(doc, beside, where, lines)
        spots, lines_left = [], iter(spans)
        for image in members:
            start, end = next(lines_left)
            requests.append(docs_write.paragraph_style(doc, start, end, {
                "namedStyleType": "NORMAL_TEXT", "alignment": "CENTER", "keepWithNext": bool(image.caption)},
                docs_write.PARAGRAPH_RESET))
            spots.append((start, image))
            if image.caption:
                start, end = next(lines_left)
                requests.append(docs_write.paragraph_style(doc, start, end, {
                    "namedStyleType": "NORMAL_TEXT", "alignment": "CENTER"}, docs_write.PARAGRAPH_RESET))
                requests.append(docs_write.text_style(doc, start, end - 1, {
                    "italic": True, "foregroundColor": docs_write.rgb(CAPTION_COLOUR)}, "italic,foregroundColor"))
        for start, image in reversed(spots):  # an image moves what follows it, so the last goes in first
            requests.append(request := {"insertInlineImage": {
                "location": doc.location(start), "uri": image.uri,
                "objectSize": {"width": {"magnitude": round(min(image.wanted, page), 1), "unit": "PT"}}}})
            which[id(request)] = image
        plan.edit(at, requests)
    batch = plan.requests()
    return batch, {number: which[id(request)] for number, request in enumerate(batch) if id(request) in which}


class _Drive:
    """One account's Google Drive, holding images from this machine while a doc fetches them."""

    def __init__(self, account: str):
        self.account = account
        self.service = common.service(account, "drive", "v3", needs=DRIVE_FILE)
        self.folder = ""
        self.shared: list[tuple[str, str, str]] = []  # (name, file id, permission id): sharing to take off again

    def hold(self, image: _Image) -> str:
        """Uploads the image and shares it with anyone who has the link. Returns the link Docs fetches it from."""
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaInMemoryUpload

        files = self.service.files()
        folder_name = settings().image_folder.replace("\\", "\\\\").replace("'", "\\'")
        with api_errors(self.account, DRIVE_FILE):
            if not self.folder:
                found = files.list(q=f"name='{folder_name}' and mimeType='{FOLDER}' and trashed=false",
                                   spaces="drive", fields="files(id)", pageSize=1).execute().get("files", [])
                self.folder = found[0]["id"] if found else files.create(
                    body={"name": settings().image_folder, "mimeType": FOLDER}, fields="id").execute()["id"]
            made = files.create(body={"name": image.name, "parents": [self.folder]},
                                media_body=MediaInMemoryUpload(image.data, mimetype=image.mime),
                                fields="id,webContentLink,permissionIds").execute()
            try:
                permission = self.service.permissions().create(
                    fileId=made["id"], body={"type": "anyone", "role": "reader", "allowFileDiscovery": False},
                    fields="id").execute()
            except HttpError as e:
                if e.resp.status in (400, 403) and "insufficient" not in str(e).lower():
                    raise RuntimeError(
                        f"The {self.account} account can't share a file with anyone who has the link, which Google "
                        "Docs needs to fetch an image from this machine; its Workspace probably doesn't allow it. "
                        "Nothing in the doc changed. Give an https link to the image instead, or, if the user agrees, "
                        "hold it in another account's Drive with upload_account.") from e
                raise
        # A file that took link sharing from its folder was shared already, so that sharing stays as it was.
        if permission["id"] not in made.get("permissionIds", []):
            self.shared.append((image.name, made["id"], permission["id"]))
        return made.get("webContentLink") or f"https://drive.google.com/uc?id={made['id']}&export=download"

    def unshare(self) -> list[str]:
        """Takes off the link sharing `hold` added. Returns the names of any it couldn't take off."""
        stuck = []
        for name, file_id, permission_id in self.shared:
            for tries in range(2):
                try:
                    self.service.permissions().delete(fileId=file_id, permissionId=permission_id).execute()
                    break
                except Exception:
                    if tries:
                        stuck.append(name)
        self.shared.clear()
        return stuck


def _failed_image(error: Exception, images: dict[int, _Image]) -> _Image | None:
    """The image a batch failed on because Docs couldn't fetch it, if that's why it failed."""
    text = str(error)
    found = FAILED_IMAGE.search(text)
    if getattr(getattr(error, "resp", None), "status", None) != 400 or not found or "retriev" not in text.lower():
        return None
    return images.get(int(found[1]))


IMAGE_ITEM = {"type": "object", "required": ["image"], "properties": {
    "image": {"type": "string", "description": "A PNG, JPG or GIF: its full path on this machine, e.g. "
                                               "~/Desktop/steps.png, or an https link"},
    "caption": {"type": "string", "description": "Optional: a line under the image, centred, in grey italics"},
    "width": {"type": "number", "description": "Optional: its width in points (72 to an inch; a page is about 468 "
                                               "wide). The height keeps the image's shape. Default: its own size, or "
                                               "the page width if it's wider."},
    "where": {"type": "string", "enum": list(docs_write.WHERE), "description": "Where this image goes, if not where "
                                                                              "the call's where says."},
    "anchor": {"type": "string", "description": "For after and before: this image's paragraph, if not the call's "
                                               "anchor."}}}


@specs.tool(
    "insert_image",
    "Put images into a Google Doc: PNG, JPG or GIF files on this machine (by their full path) or https links, each "
    "in a centred paragraph of its own with an optional caption under it, at the end, at the start, or just after "
    "or before a paragraph picked by its text. Several images go in one call, each where it's asked for. An image "
    "keeps its shape: it's the width asked for, else its own size, shrunk to the page width if it's wider. Google "
    "Docs only fetches images from public links, so an image from this machine is uploaded to the account's Google "
    "Drive (the image folder), shared with anyone who has the link just while the doc copies it, then unshared; "
    "the doc keeps its own copy. Nothing already in the doc changes. " + docs_write.CONFIRM + " "
    + docs_write.UNTRUSTED,
    {"account": ACCOUNT, "document": docs_write.DOCUMENT_ARG, "tab": docs_write.TAB_ARG,
     "images": {"type": "array", "items": IMAGE_ITEM,
                "description": "In order, e.g. [{\"image\": \"~/Desktop/install-flow.png\", \"caption\": \"Figure 1: "
                               "the install flow\", \"where\": \"after\", \"anchor\": \"Installation steps\"}]."},
     "where": {"type": "string", "enum": list(docs_write.WHERE), "description": "Default end."},
     "anchor": {"type": "string", "description": "For after and before: the paragraph's text as docs_read shows it, "
                                                "or its start. The first match is used."},
     "upload_account": {**ANY_ACCOUNT,
                        "description": "Whose Google Drive holds images from this machine while the doc copies "
                                       "them. Default: the doc's account. Only when the user asks, e.g. if the doc's "
                                       "account can't share files by link."}},
    ["document", "images"],
)
def docs_insert_image(account: str, document: str, images: list, where: str = "end", anchor: str = "",
                      tab: str = "", upload_account: str = "") -> str:
    from googleapiclient.errors import HttpError

    items = _items(images, where, anchor)
    if upload_account and upload_account not in settings().labels:
        raise ValueError(f"upload_account must be one of: {', '.join(settings().labels)}.")
    # Everything is read, fetched and checked, and every paragraph found, before anything is uploaded or changed.
    loaded = docs_write.each("Image", items, _load)
    doc = docs_write.Doc(account, document, tab)
    groups = _groups(doc, loaded)
    page = _page_width(doc)
    holder = upload_account or account
    folder = settings().image_folder
    drive: _Drive | None = None
    try:
        for image in loaded:
            if image.link:
                image.uri = image.link
            else:
                drive = drive or _Drive(holder)
                image.uri = drive.hold(image)
        for tries in range(1, FETCH_TRIES + 1):
            batch, placed = _requests(doc, groups, page)
            try:
                doc.send(batch)
                break
            except HttpError as e:
                if not (failed := _failed_image(e, placed)):
                    raise
                if tries == FETCH_TRIES:
                    raise RuntimeError(f"Google Docs couldn't fetch {failed.name}, so nothing in the doc changed. "
                                       "Try again in a minute.") from e
                if failed.link:  # Docs can't fetch the link, so it gets the copy fetched here, from Drive
                    failed.link = None
                    drive = drive or _Drive(holder)
                    failed.uri = drive.hold(failed)
                else:
                    time.sleep(FETCH_WAIT)
    except Exception as e:
        if drive and (stuck := drive.unshare()):
            raise RuntimeError(f"{e} Also, link sharing couldn't be taken off {', '.join(stuck)} in the {holder} "
                               f"account's Drive folder '{folder}': turn it off in Drive.") from e
        raise
    stuck = drive.unshare() if drive else []

    places = list(dict.fromkeys(docs_write.placed_words(image.where, image.next_to) for image in loaded))
    count = docs_write.plural(len(loaded), "image")
    said = f"Added {count} {places[0]} {doc.where}." if len(places) == 1 else \
        f"Added {count} in {len(places)} places in {doc.where}."
    if shrunk := [image.name for image in loaded if image.wanted > page]:
        said += f" Sized to the page width, {page:g} points: {', '.join(shrunk)}."
    if stuck:
        said += (f" Link sharing couldn't be taken off {', '.join(stuck)} in the {holder} account's Drive folder "
                 f"'{folder}': turn it off in Drive.")
    elif drive:
        said += f" The uploaded copies are in the {holder} account's Drive folder '{folder}', shared with no one."
    return said
