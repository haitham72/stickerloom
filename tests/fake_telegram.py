"""A stdlib fake of the Telegram Bot API sticker methods, with the real error shapes. No network, no real bot."""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "123456789:AAFakeTokenForTestsOnly_0123456789abcd"
BOT = "stickerloombot"
USER = "424242"


def parse_multipart(body: bytes, ctype: str):
    boundary = ctype.split("boundary=")[1].encode()
    fields, files = {}, {}
    for part in body.split(b"--" + boundary):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        head, _, data = part.partition(b"\r\n\r\n")
        h = head.decode("utf-8", "replace")
        name = re.search(r'name="([^"]+)"', h)[1]
        fn = re.search(r'filename="([^"]*)"', h)
        if fn:
            files[name] = data
        else:
            fields[name] = data.decode("utf-8")
    return fields, files


class Fake:
    def __init__(self):
        self.sets = {}                # name -> {"title", "kind", "stickers": [{"file_unique_id", "emoji", "bytes"}]}
        self.taken = set()            # names owned by somebody else
        self.started = {USER}         # user ids that pressed Start on the bot
        self.calls = []               # (method, summary)
        self.fail_429 = 0             # the next N calls answer 429 with retry_after 0
        self.responses = []           # every body that was sent back (the token must never be in one)
        self.counter = 0
        self.messages = []            # what the bot wrote to people: (method, chat_id, text-or-file_id)
        self.mute = False             # the owner never pressed Start / blocked the bot: sendMessage answers 403


def make_handler(f: Fake):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _reply(self, code, obj):
            raw = json.dumps(obj).encode()
            f.responses.append(raw.decode())
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _err(self, code, desc, **extra):
            self._reply(code, {"ok": False, "error_code": code, "description": desc, **extra})

        def do_POST(self):
            m = re.match(r"^/bot([^/]+)/(\w+)$", self.path)
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if not m or m[1] != TOKEN:
                return self._err(401, "Unauthorized")
            method = m[2]
            if "json" in (self.headers.get("Content-Type") or ""):
                raw = json.loads(body or b"{}")
                fields, files = {k: (json.dumps(v) if isinstance(v, (list, dict)) else str(v)) for k, v in raw.items()}, {}
            else:
                fields, files = parse_multipart(body, self.headers["Content-Type"]) if body else ({}, {})
            f.calls.append((method, {k: v for k, v in fields.items() if k != "stickers"}))
            if f.fail_429 > 0:
                f.fail_429 -= 1
                return self._err(429, "Too Many Requests: retry after 0", parameters={"retry_after": 0})
            if method == "getMe":
                return self._reply(200, {"ok": True, "result": {"id": 1, "is_bot": True, "username": BOT}})
            if method == "getStickerSet":
                s = f.sets.get(fields["name"])
                if not s:
                    return self._err(400, "Bad Request: STICKERSET_INVALID")
                return self._reply(200, {"ok": True, "result": {"name": fields["name"], "title": s["title"], "stickers": s["stickers"]}})
            if method in ("sendMessage", "sendSticker"):
                if f.mute or fields.get("chat_id") not in f.started:
                    return self._err(403, "Forbidden: bot can't initiate conversation with a user")
                f.messages.append((method, fields["chat_id"], fields.get("text") or fields.get("sticker")))
                return self._reply(200, {"ok": True, "result": {"message_id": len(f.messages)}})
            if fields.get("user_id") not in f.started:
                return self._err(400, "Bad Request: user not found")
            if method == "createNewStickerSet":
                name, items = fields["name"], json.loads(fields["stickers"])
                if name in f.taken or name in f.sets:
                    return self._err(400, "Bad Request: sticker set name is already occupied")
                if not name.lower().endswith("_by_" + BOT) or "__" in name or not name[0].isalpha():
                    return self._err(400, "Bad Request: invalid sticker set name is specified")
                if not 1 <= len(items) <= 50:
                    return self._err(400, "Bad Request: STICKERS_TOO_MUCH")
                kinds = {i["format"] for i in items}
                if len(kinds) != 1:
                    return self._err(400, "Bad Request: STICKERSET_INVALID")
                f.sets[name] = {"title": fields["title"], "kind": kinds.pop(), "stickers": []}
                for i in items:
                    self._add(name, i, files)
                return self._reply(200, {"ok": True, "result": True})
            if method == "addStickerToSet":
                s = f.sets.get(fields["name"])
                if not s:
                    return self._err(400, "Bad Request: STICKERSET_INVALID")
                i = json.loads(fields["sticker"])
                if i["format"] != s["kind"]:
                    return self._err(400, "Bad Request: STICKERSET_INVALID")
                if len(s["stickers"]) >= 120:
                    return self._err(400, "Bad Request: STICKERS_TOO_MUCH")
                self._add(fields["name"], i, files)
                return self._reply(200, {"ok": True, "result": True})
            return self._err(404, "Not Found")

        def _add(self, name, item, files):
            ref = item["sticker"].replace("attach://", "")
            f.counter += 1
            f.sets[name]["stickers"].append({"file_unique_id": f"AQAD{f.counter:05d}", "file_id": f"FILE{f.counter:05d}", "emoji": "".join(item["emoji_list"]), "bytes": len(files[ref])})
    return H


class FakeServer:
    def __enter__(self):
        self.f = Fake()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.f))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()
