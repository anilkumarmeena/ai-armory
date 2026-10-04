"""Stand-ins for Google's API clients and helpers shared by the Google tool set tests. Nothing here touches the
network."""

import json
import struct
from unittest.mock import MagicMock

import httplib2
from googleapiclient.errors import HttpError

from ai_armory import call


def http_error(status, message):
    return HttpError(httplib2.Response({"status": status}),
                     json.dumps({"error": {"code": status, "message": message}}).encode())


def write_token(settings, label, scopes):
    """A token file with made-up values, as sign-in would leave it."""
    path = settings.token_path(label)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"token": "t", "refresh_token": "r", "client_id": "id", "client_secret": "s",
                                "scopes": scopes, "expiry": "2099-01-01T00:00:00Z"}))


class Services:
    """One MagicMock client per (account, API); `.opened` records each common.service call. `setup(api, mock)`
    gives a new client its canned answers."""

    def __init__(self, setup=None):
        self.clients = {}
        self.opened = []
        self.setup = setup

    def __call__(self, label, api, version, needs=None):
        self.opened.append((label, api, version, needs))
        return self.client(label, api)

    def client(self, label, api):
        if (label, api) not in self.clients:
            self.clients[label, api] = mock = MagicMock()
            if self.setup:
                self.setup(api, mock)
        return self.clients[label, api]


def tools_of(toolset):
    return {t.name: t for t in toolset.tools}


async def run(toolset, name, args):
    """A tool called the way a host calls it: through core.call, so inputs are checked and defaults filled."""
    return await call(tools_of(toolset)[name], args)


# ── Docs laid out with real positions, and position-moving requests played back ──

def P(text, style="NORMAL_TEXT", bold=False, bullet=None, shading=None):
    return ("p", text, style, bold, bullet, shading)


def T(*rows, header_fill=None):
    return ("t", rows, header_fill)


def IMG(text=""):
    """A paragraph with an inline image (▣) after `text`: alone on its line if there's no text."""
    return ("i", text)


def u16(text):
    return len(text.encode("utf-16-le")) // 2


def layout(*specs):
    """(a tab's content with Docs' positions, the body as text: § for the section break, ⊤ ⊢ ⊡ for where a
    table, row and cell start, each taking one position as in Docs, and ▣ for an image)."""
    chars = ["§"]
    content = [{"startIndex": 0, "endIndex": 1, "sectionBreak": {}}]

    def paragraph(text, style="NORMAL_TEXT", bold=False, bullet=None, shading=None, image=False):
        start = len(chars)
        chars.extend(text + ("▣" if image else "") + "\n")
        elements, at = [], start
        for piece, kind in ((text, "text"), ("▣" if image else "", "image"), ("\n", "text")):
            if piece and kind == "image":
                elements.append({"startIndex": at, "endIndex": at + 1,
                                 "inlineObjectElement": {"inlineObjectId": f"kix.{at}", "textStyle": {}}})
            elif piece and elements and "textRun" in elements[-1]:
                elements[-1]["textRun"]["content"] += piece
                elements[-1]["endIndex"] += u16(piece)
            elif piece:
                elements.append({"startIndex": at, "endIndex": at + u16(piece),
                                 "textRun": {"content": piece, "textStyle": {}}})
            at += u16(piece)
        if bold:
            elements[0]["textRun"]["textStyle"]["bold"] = True
        para = {"elements": elements, "paragraphStyle": {"namedStyleType": style}}
        if bullet:
            para["bullet"] = {"listId": bullet}
        if shading:
            para["paragraphStyle"]["shading"] = {"backgroundColor": {"color": {"rgbColor": {"red": 1}}}}
        return {"startIndex": start, "endIndex": at, "paragraph": para}

    for spec in specs:
        if spec[0] == "p":
            content.append(paragraph(*spec[1:]))
            continue
        if spec[0] == "i":
            content.append(paragraph(spec[1], image=True))
            continue
        _t, rows, fill = spec
        table = {"startIndex": len(chars), "table": {"rows": len(rows), "columns": len(rows[0]), "tableRows": []}}
        chars.append("⊤")
        for r, row in enumerate(rows):
            chars.append("⊢")
            cells = []
            for text in row:
                cell_start = len(chars)
                chars.append("⊡")
                cell = {"startIndex": cell_start, "content": [paragraph(line) for line in text.split("\n")],
                        "tableCellStyle": {}}
                if fill and r == 0:
                    cell["tableCellStyle"]["backgroundColor"] = {"color": {"rgbColor": {"red": 1}}}
                cell["endIndex"] = len(chars)
                cells.append(cell)
            table["table"]["tableRows"].append({"tableCells": cells})
        table["endIndex"] = len(chars)
        content.append(table)
    return {"body": {"content": content}, "lists": {}}, "".join(chars)


def apply(text, requests):
    """The body after the requests that move text, as lines; list items as '[bullet 0] text' with their level, and
    an image as ▣."""
    chars = [[c, None] for c in text]
    for request in requests:
        (kind, body), = request.items()
        if kind == "insertText":
            at = body["location"]["index"]
            assert 0 < at < len(chars), request
            chars[at:at] = [[c, None] for c in body["text"]]
        elif kind == "insertInlineImage":
            at = body["location"]["index"]
            assert 0 < at < len(chars), request
            chars[at:at] = [["▣", None]]
        elif kind == "deleteContentRange":
            start, end = body["range"]["startIndex"], body["range"]["endIndex"]
            assert 0 < start < end <= len(chars) - 1, request  # never the body's last newline
            del chars[start:end]
        elif kind == "createParagraphBullets":
            start, end = body["range"]["startIndex"], body["range"]["endIndex"]
            assert 0 < start < end <= len(chars), request
            kind_ = "numbered" if body["bulletPreset"].startswith("NUMBERED") else "bullet"
            i, starts = 0, []
            for j, (c, _f) in enumerate(chars):  # paragraph starts overlapping the range
                if j >= end:
                    break
                if j == 0 or chars[j - 1][0] == "\n":
                    i = j
                if c == "\n" and j >= start:
                    starts.append(i)
            for para_start in reversed(starts):
                tabs = 0
                while chars[para_start + tabs][0] == "\t":
                    tabs += 1
                del chars[para_start:para_start + tabs]
                end_ = next(k for k in range(para_start, len(chars)) if chars[k][0] == "\n")
                chars[end_][1] = f"[{kind_} {tabs}] "
    lines, line = [], ""
    for c, flag in chars:
        if c == "\n":
            lines.append((flag or "") + line.lstrip("§⊤⊢⊡"))
            line = ""
        else:
            line += c
    return lines


def text_at(text, rng):
    return text[rng["startIndex"]:rng["endIndex"]]


def kinds(requests):
    return [next(iter(r)) for r in requests]


def png_bytes(width, height):
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I4sIIBBBBB", 13, b"IHDR", width, height, 8, 2, 0, 0, 0) + b"\0" * 4


def jpeg_bytes(width, height):
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\0\x01\x01\0\0\x01\0\x01\0\0"
    frame = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\0"
    return b"\xff\xd8" + app0 + frame + b"\xff\xd9"
