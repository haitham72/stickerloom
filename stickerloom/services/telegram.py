"""Send a Library pack to Telegram (checkpoint 1H, docs/architecture.md).

Bot API (createNewStickerSet / addStickerToSet / getStickerSet), stdlib only. Every sticker is judged by the verifier's `telegram`
stage BEFORE any network call. A Telegram set holds one kind of sticker, so a mixed pack becomes two sets (video + static). Sending
again adds only what is new. The token is read from the environment or out/telegram.json, only ever sent to Telegram, never logged and
never returned by the API. STICKERLOOM_TELEGRAM_API points the client at a fake server in the tests."""
from __future__ import annotations

import io
import json
import os
import queue
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from ..engine import ffmpeg as ff
from ..engine import verify
from ..engine.config import EngineConfig

API_DEFAULT = "https://api.telegram.org"
TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")
BATCH = 50                    # createNewStickerSet takes at most 50 stickers; the rest go through addStickerToSet


class TelegramError(Exception):
    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = code


def api_base() -> str:
    return os.environ.get("STICKERLOOM_TELEGRAM_API", API_DEFAULT).rstrip("/")


# ---------- configuration (the token never leaves this module except to Telegram) ----------
def _cfg_path(out: Path) -> Path:
    return Path(out) / "telegram.json"


def load_config(out: Path) -> dict:
    cfg = {}
    try:
        cfg = json.loads(_cfg_path(out).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    token = os.environ.get("STICKERLOOM_TELEGRAM_TOKEN") or cfg.get("token")
    user = os.environ.get("STICKERLOOM_TELEGRAM_USER") or cfg.get("user_id")
    return {"token": token, "user_id": str(user) if user else None, "bot": cfg.get("bot"), "from_env": bool(os.environ.get("STICKERLOOM_TELEGRAM_TOKEN"))}


def status(out: Path) -> dict:
    """What the page may know: connected or not, which bot, whose user id. Never the token."""
    c = load_config(out)
    return {"configured": bool(c["token"] and c["user_id"]), "bot": c["bot"], "user_id": c["user_id"], "from_env": c["from_env"], "api": api_base()}


def save_config(out: Path, token: str, user_id: str) -> dict:
    token, user_id = (token or "").strip(), (user_id or "").strip()
    if not TOKEN_RE.match(token):
        raise TelegramError("That does not look like a bot token. It looks like 123456789:AAH… and comes from @BotFather.")
    if not user_id.isdigit():
        raise TelegramError("Your Telegram user id is a number (not @name). Message @userinfobot in Telegram to see it.")
    me = _call(token, "getMe", {})                       # proves the token before anything is saved
    p = _cfg_path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"token": token, "user_id": user_id, "bot": me["username"]}), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return status(out)


def disconnect(out: Path) -> dict:
    _cfg_path(out).unlink(missing_ok=True)
    return status(out)


# ---------- HTTP ----------
_CTX = None


def _ssl_context() -> ssl.SSLContext:
    """The system trust store, tolerant of a malformed certificate. On some Windows PCs one bad entry in the store makes
    ssl.create_default_context() raise [ASN1: NOT_ENOUGH_DATA] and nothing can connect; load the store one certificate at a time and skip the bad ones.
    Corporate root certificates in the store are kept, so a network that inspects TLS still works."""
    global _CTX
    if _CTX is not None:
        return _CTX
    try:
        _CTX = ssl.create_default_context()
        return _CTX
    except ssl.SSLError:
        pass
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.verify_mode, ctx.check_hostname = ssl.CERT_REQUIRED, True
    loaded = 0
    if hasattr(ssl, "enum_certificates"):                      # Windows
        for store in ("ROOT", "CA"):
            try:
                certs = ssl.enum_certificates(store)
            except OSError:
                continue
            for der, enc, _trust in certs:
                if enc == "x509_asn":
                    try:
                        ctx.load_verify_locations(cadata=der)
                        loaded += 1
                    except ssl.SSLError:
                        continue
    try:
        ctx.load_default_certs()                               # other platforms (or whatever else loads cleanly)
    except ssl.SSLError:
        pass
    if not loaded:
        try:
            import certifi
            ctx.load_verify_locations(cafile=certifi.where())
        except Exception:
            pass
    _CTX = ctx
    return ctx


def _scrub(text: str, token: str | None) -> str:
    return text.replace(token, "<token>") if token else text


MAP = [("can't initiate conversation", "Open your bot in Telegram and press Start, so it is allowed to message you."),
       ("bot was blocked", "You blocked the bot in Telegram: unblock it so it can message you."),
       ("user not found", "Telegram does not know that user id yet: open your bot in Telegram, press Start, then try again."),
       ("chat not found", "Telegram does not know that user id yet: open your bot in Telegram, press Start, then try again."),
       ("peer_id_invalid", "Telegram does not know that user id yet: open your bot in Telegram, press Start, then try again."),
       ("already occupied", "That pack name is already taken on Telegram. Choose another name."),
       ("name is already", "That pack name is already taken on Telegram. Choose another name."),
       ("stickerset_invalid", "Telegram does not accept that pack name or it does not exist."),
       ("file is too big", "A file is over Telegram's size limit."),
       ("unauthorized", "The bot token is wrong or was revoked. Connect the bot again in Settings.")]


def explain(description: str) -> str:
    low = (description or "").lower()
    return next((m for k, m in MAP if k in low), description or "Telegram refused the request")


def _multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    b = "----stickerloom" + uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, (fname, data, mime) in files.items():
        out.write(f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
        out.write(data)
        out.write(b"\r\n")
    out.write(f"--{b}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={b}"


def _call(token: str, method: str, fields: dict, files: dict | None = None, timeout: float = 60.0):
    """One Bot API call. 429 waits retry_after (up to 3 tries); every failure becomes a plain TelegramError without the token in it."""
    if files:
        body, ctype = _multipart({k: (json.dumps(v) if isinstance(v, (list, dict)) else str(v)) for k, v in fields.items()}, files)
    else:                                                    # no files: plain JSON (an empty multipart body got HTTP 400 from the real service)
        body, ctype = json.dumps(fields).encode("utf-8"), "application/json"
    url = f"{api_base()}/bot{token}/{method}"
    for attempt in range(3):
        req = urllib.request.Request(url, data=body, headers={"Content-Type": ctype}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context() if url.startswith("https") else None) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                data = json.loads(e.read().decode("utf-8"))
            except ValueError:
                data = {"ok": False, "description": f"HTTP {e.code}", "error_code": e.code}
        except (urllib.error.URLError, OSError) as e:
            raise TelegramError("Cannot reach Telegram: " + _scrub(str(getattr(e, "reason", e)), token) + ". Check the internet connection.", 502)
        if data.get("ok"):
            return data["result"]
        wait = (data.get("parameters") or {}).get("retry_after")
        if data.get("error_code") == 429 and wait is not None and attempt < 2:
            time.sleep(min(float(wait), 30.0))
            continue
        raise TelegramError(_scrub(explain(data.get("description", "")), token), 409 if data.get("error_code") in (400, 403) else 502)
    raise TelegramError("Telegram is busy. Try again in a minute.", 429)


# ---------- names and emoji ----------
def split_emoji(s: str) -> list[str]:
    """A sticker's emoji field as Telegram's emoji_list: ZWJ sequences, variation selectors, skin tones and keycaps stay together."""
    s = (s or "").strip()
    if not s:
        return []
    out, cur, join = [], "", False
    for ch in s:
        o = ord(ch)
        if ch.isspace() or ch in ",;":
            if cur:
                out.append(cur)
            cur, join = "", False
            continue
        glue = o in (0x200D, 0xFE0F, 0x20E3) or 0x1F3FB <= o <= 0x1F3FF or 0xE0020 <= o <= 0xE007F
        if cur and not glue and not join and not (0x1F1E6 <= o <= 0x1F1FF and len(cur) == 1 and 0x1F1E6 <= ord(cur) <= 0x1F1FF):
            out.append(cur)
            cur = ""
        cur += ch
        join = o == 0x200D
    if cur:
        out.append(cur)
    return out


def set_name(base: str, suffix: str, bot: str) -> str:
    """<base><suffix>_by_<bot> within Telegram's rules: lowercase letters/digits/single underscores, starts with a letter, <= 64 chars."""
    base = re.sub(r"[^a-z0-9]+", "_", (base or "").lower()).strip("_") or "stickers"
    if not base[0].isalpha():
        base = "p_" + base
    tail = f"{suffix}_by_{bot}"
    return (base[: max(1, 64 - len(tail))].rstrip("_") + tail).replace("__", "_")


# ---------- reading a Library sticker the way Telegram will judge it ----------
def inspect(path: Path, kind: str, emoji: list[str], cfg: EngineConfig) -> dict:
    data = path.read_bytes()
    if kind == "video":
        info = ff.probe(path)
        al = ff.decode_alpha(path, 2)
        alpha = len(al) > 0 and al[..., 3].min() < 250
        return {"kind": "video", "bytes": len(data), "info": info, "alpha": bool(alpha), "w": info["width"], "h": info["height"], "emoji": emoji}
    im = Image.open(io.BytesIO(data))
    rgba = np.array(im.convert("RGBA"))
    return {"kind": "static", "bytes": len(data), "alpha": bool(rgba[..., 3].min() < 250), "w": im.size[0], "h": im.size[1], "emoji": emoji,
            "stroke": verify.has_white_stroke(rgba)}


def plan(lib, pid: str, bot: str | None, base_name: str | None = None, cfg: EngineConfig | None = None, fresh: bool = False) -> dict:
    """What would be created, and every problem, with no network call. `bot` None = not connected yet (names are shown with a placeholder)."""
    cfg = cfg or EngineConfig()
    pack = next((p for p in lib.snapshot()["packs"] if p["id"] == pid), None)
    if not pack:
        raise TelegramError("No such pack", 404)
    base = base_name or pack["name"]
    kinds = {"video": [s for s in pack["stickers"] if s["type"] == "animated"], "static": [s for s in pack["stickers"] if s["type"] != "animated"]}
    both = all(kinds.values())
    sets, blocked, warns = [], [], []
    for kind, sts in kinds.items():
        if not sts:
            continue
        name = set_name(base, ("_v" if kind == "video" else "_s") if both else "", bot or "yourbot")
        items = []
        for s in sts:
            emo = split_emoji(s.get("emoji"))
            try:
                ins = inspect(lib.files / s["file"], kind, emo, cfg)
                checks = verify.run("telegram", ins, cfg)
            except Exception as e:                      # an unreadable file is a problem of that sticker, not a crash
                checks = [verify.Check("telegram_sticker", "telegram", verify.BLOCK, False, None, None, {"problems": [f"cannot read the file: {e}"]}, f"cannot read the file: {e}")]
            probs = [c.note for c in checks if not c.ok and c.severity == verify.BLOCK]
            ws = [c.note for c in checks if not c.ok and c.severity == verify.WARN]
            items.append({"id": s["id"], "name": s["name"], "key": s["id"], "file": s["file"], "emoji": emo, "kb": s["kb"], "problems": probs, "warnings": ws})
            blocked += [f"{s['name']}: {x}" for x in probs]
            warns += [f"{s['name']}: {x}" for x in ws]
        have = None if fresh else next((t for t in (pack.get("telegram") or {}).get("sets", []) if t["kind"] == kind), None)
        eff = have["name"] if have else name
        sc = verify.run("telegram_set", {"stickers": [{"key": i["key"]} for i in items], "name": eff, "bot": bot, "title": base[:64]}, cfg)[0]
        if not sc.ok:
            blocked += sc.detail.get("problems", [sc.note])
        known = {i["sticker_id"] for i in (have or {}).get("items", [])}
        sets.append({"kind": kind, "name": eff, "title": base[:64], "items": items, "link": f"https://t.me/addstickers/{eff}",
                     "new": [i["id"] for i in items if i["id"] not in known], "exists": bool(have), "have_items": (have or {}).get("items", [])})
    return {"pack": pack["name"], "sets": sets, "blocked": blocked, "warnings": warns, "bot": bot}


# ---------- never the same pack twice ----------
MODES = ("once", "replace", "new_set")


def fingerprint(lib, pid: str) -> str:
    """What a pack IS, for the purpose of not sending it twice: sha256 over its stickers, sorted, each as (the generator's file name, the SHA-256 of the file's bytes, static / animated).
    Not the pack's name (people retype names) and not its id (an accidentally duplicated pack must be recognised as the same pack)."""
    import hashlib
    pack = next((p for p in lib.snapshot()["packs"] if p["id"] == pid), None)
    if not pack:
        raise TelegramError("No such pack", 404)
    rows = []
    for s in pack["stickers"]:
        try:
            digest = hashlib.sha256((lib.files / s["file"]).read_bytes()).hexdigest()
        except OSError:
            digest = "missing"
        rows.append(f"{s.get('file_name') or s['name']}|{digest}|{s['type']}")
    return hashlib.sha256("\n".join(sorted(rows)).encode("utf-8")).hexdigest()


def already_sent(lib, pid: str) -> dict | None:
    """The export record of this exact pack content, or None. `pack.telegram.exports` keeps the last ten sends."""
    fp = fingerprint(lib, pid)
    pack = next(p for p in lib.snapshot()["packs"] if p["id"] == pid)
    return next((e for e in reversed((pack.get("telegram") or {}).get("exports", [])) if e.get("fingerprint") == fp), None)


# ---------- sending ----------
def _mime(path: Path) -> str:
    return {".webm": "video/webm", ".png": "image/png", ".webp": "image/webp"}.get(path.suffix.lower(), "application/octet-stream")


def _input(lib, item: dict, kind: str, retime: bool = False, cfg: EngineConfig | None = None):
    """One sticker for the Bot API. `retime`: a video shorter than 3 s is sent with its same frames played over 3 s (library.retime_webm); the stored file is untouched."""
    path = lib.files / item["file"]
    ref = "s_" + item["id"]
    data = path.read_bytes()
    if retime and kind == "video" and path.suffix.lower() == ".webm":
        from ..media.library import LibraryError, retime_webm
        try:
            rt = retime_webm(path, cfg or EngineConfig())
            if rt:
                data = rt[0]
        except LibraryError:
            pass                                   # unreadable: sent as stored, Telegram judges it
    return {"sticker": f"attach://{ref}", "format": kind, "emoji_list": item["emoji"]}, {ref: (path.name, data, _mime(path))}


def require_connected(out: Path) -> dict:
    """The saved bot config, or the 409 send() raises when Telegram was never connected."""
    c = load_config(out)
    if not (c["token"] and c["user_id"]):
        raise TelegramError("Telegram is not connected. Add your bot token and user id first.", 409)
    return c


def send(out: Path, lib, pid: str, base_name: str | None = None, cfg: EngineConfig | None = None, mode: str = "once", retime: bool = False) -> dict:
    """Send a pack. `mode`: `once` (the default) answers with the earlier export when this exact content was already sent and calls Telegram NOT AT ALL; `replace` sends on purpose
    (only what is new is added to the existing set); `new_set` sends the whole pack again as a second set with a numbered name. Both escape hatches are recorded.
    `retime`: videos shorter than 3 s go up stretched to 3 s (the person chose it when the Studio asked; Telegram's limit is 3 s at most)."""
    if mode not in MODES:
        raise TelegramError(f"mode must be one of {', '.join(MODES)}", 400)
    if mode == "once":
        prev = already_sent(lib, pid)
        if prev:
            return {"already": True, "sets": [dict(x, added=0) for x in prev["sets"]], "sent_at": prev["sent_at"], "bot": prev.get("bot"), "warnings": [], "notified": False, "notify_error": None,
                    "note": "This pack was already sent to Telegram; nothing was sent again. Use Replace or Send as a new set on purpose."}
    c = require_connected(out)
    token, user = c["token"], c["user_id"]
    bot = c["bot"] or _call(token, "getMe", {})["username"]
    if mode == "new_set":
        base_name = f"{base_name or next(x['name'] for x in lib.snapshot()['packs'] if x['id'] == pid)} {len((next(x for x in lib.snapshot()['packs'] if x['id'] == pid).get('telegram') or {}).get('exports', [])) + 1}"
    p = plan(lib, pid, bot, base_name, cfg, fresh=(mode == "new_set"))
    if p["blocked"]:
        raise TelegramError("Not sent. Fix this first: " + "; ".join(p["blocked"][:6]) + (" …" if len(p["blocked"]) > 6 else ""), 409)
    report = []
    for st in p["sets"]:
        new = [i for i in st["items"] if i["id"] in st["new"]]
        if not new:
            report.append({"kind": st["kind"], "name": st["name"], "link": st["link"], "added": 0, "total": len(st["items"])})
            continue
        if not st["exists"]:
            first, rest = new[:BATCH], new[BATCH:]
            inputs, files = [], {}
            for it in first:
                i, f = _input(lib, it, st["kind"], retime, cfg)
                inputs.append(i)
                files.update(f)
            _call(token, "createNewStickerSet", {"user_id": user, "name": st["name"], "title": st["title"], "stickers": inputs, "sticker_type": "regular"}, files)
        else:
            rest = new
        for it in rest:
            i, f = _input(lib, it, st["kind"], retime, cfg)
            _call(token, "addStickerToSet", {"user_id": user, "name": st["name"], "sticker": i}, f)
        got = _call(token, "getStickerSet", {"name": st["name"]})
        ids = [i["sticker_id"] for i in st["have_items"]] + [it["id"] for it in new]      # Telegram keeps the order they were added in
        items = [{"sticker_id": sid, "file_unique_id": tg.get("file_unique_id")} for sid, tg in zip(ids, got.get("stickers", []))]
        lib.set_telegram(pid, st["kind"], {"kind": st["kind"], "name": st["name"], "link": f"https://t.me/addstickers/{st['name']}", "items": items})
        report.append({"kind": st["kind"], "name": st["name"], "link": f"https://t.me/addstickers/{st['name']}", "added": len(new), "total": len(st["items"])})
    notified, notify_error = False, None
    for st in report:
        if st["added"]:
            try:
                notify(token, user, st)
                notified = True
            except TelegramError as e:           # the pack exists either way; only the message to the owner failed
                notify_error = str(e)
    import time as _t
    lib.record_export(pid, {"fingerprint": fingerprint(lib, pid), "sent_at": round(_t.time(), 3), "mode": mode, "bot": bot, "sets": report})
    return {"sets": report, "warnings": p["warnings"], "bot": bot, "notified": notified, "notify_error": notify_error, "already": False, "mode": mode}


def notify(token: str, user: str, st: dict) -> None:
    """Show the pack inside Telegram itself: the bot writes the owner the 'Add stickers' link and sends the pack's first sticker.
    (A set made by a bot is not installed for anyone until its link is opened; this puts the link and a sticker in the owner's chat.)"""
    _call(token, "sendMessage", {"chat_id": user, "text": f"Your sticker pack is ready ({st['total']} stickers): {st['link']}\nOpen the link and press Add Stickers."})
    got = _call(token, "getStickerSet", {"name": st["name"]})
    if got.get("stickers"):
        _call(token, "sendSticker", {"chat_id": user, "sticker": got["stickers"][0]["file_id"]})


# ---------- the no-credentials fallback: files to upload by hand to @stickers ----------
def zip_for_stickers_bot(lib, pid: str) -> tuple[bytes, str]:
    pack = next((p for p in lib.snapshot()["packs"] if p["id"] == pid), None)
    if not pack:
        raise TelegramError("No such pack", 404)
    buf, lines = io.BytesIO(), []
    kinds = {"video": 0, "static": 0}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for n, s in enumerate(pack["stickers"], 1):
            kind = "video" if s["type"] == "animated" else "static"
            kinds[kind] += 1
            name = f"{n:02d}-{re.sub(r'[^a-z0-9]+', '_', s['name'].lower()).strip('_') or 'sticker'}{Path(s['file']).suffix}"
            z.write(lib.files / s["file"], name)
            lines.append(f"{name}\t{''.join(split_emoji(s.get('emoji')) or ['🙂'])}\t{kind}")
        z.writestr("stickers.txt", "file\temoji\tkind\n" + "\n".join(lines) + "\n")
        z.writestr("HOW-TO.txt", "Upload to Telegram by hand with @stickers\n\n"
                   "1. Open @stickers in Telegram.\n"
                   "2. Send /newvideo for the .webm files (video stickers) or /newpack for the .png / .webp files (static stickers). A pack holds one kind.\n"
                   "3. Send the pack title, then each file as a FILE (not a photo), and after each one the emoji listed in stickers.txt.\n"
                   "4. Send /publish, then /skip for the icon, then a short link name.\n\n"
                   "Limits: video 512 px, up to 3 s, 30 fps, 256 KB; static 512 px, 512 KB; at least one emoji per sticker.\n")
    return buf.getvalue(), pack["name"]


# ---------- queued sends: the POST answers 202 at once, one worker sends in order ----------
_TQ: queue.Queue = queue.Queue()
_TLIVE: set = set()                 # the send ids this process will still run (queued or running)
_TQLOCK = threading.Lock()
_TWORKER: list = []
FINAL_SEND = ("done", "failed")

def _tsend_path(out: Path, tid: str) -> Path:
    return Path(out) / "telegram_sends" / f"{tid}.json"

def _tsave(out: Path, st: dict) -> None:
    from ..runtime import atomic
    p = _tsend_path(out, st["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(p, json.dumps(st, ensure_ascii=False, indent=1))

def enqueue_send(out: Path, pid: str, make_send, by: str = "human") -> dict:
    """Queue one send; `make_send()` runs it when its turn comes. Answers at once: {id, state: queued, position, …}."""
    tid = f"T{int(time.time() * 1000):x}{uuid.uuid4().hex[:4]}"
    st = {"id": tid, "pack": pid, "what": f"pack {pid}", "by": by, "state": "queued", "at": round(time.time(), 3)}
    _tsave(out, st)
    with _TQLOCK:
        _TLIVE.add(tid)
        position = len(_TLIVE)
        _TQ.put((Path(out), tid, make_send, by))
        if not _TWORKER or not _TWORKER[0].is_alive():
            _TWORKER[:] = [threading.Thread(target=_twork, name="telegram-send", daemon=True)]
            _TWORKER[0].start()
    return {**st, "position": position}

def send_state(out: Path, tid: str) -> dict | None:
    """The send's state, None when there is no such send. One an earlier server run left unfinished reads as failed."""
    if not re.fullmatch(r"T[0-9a-f]+", str(tid or "")):
        return None
    from ..runtime import atomic
    try:
        st = json.loads(atomic.read_text(_tsend_path(out, tid)))  # retrying: a plain read can catch the replace instant on Windows (PermissionError) and report a live send as missing
    except (OSError, ValueError):
        return None
    with _TQLOCK:
        live = tid in _TLIVE
    if st.get("state") not in FINAL_SEND and not live:
        st.update(state="failed", error="The server restarted before this send finished; send it again")
    return st

def _twork() -> None:
    from ..runtime import activity
    while True:
        out, tid, make_send, by = _TQ.get()
        st = send_state(out, tid) or {"id": tid, "by": by}
        try:
            st["state"] = "sending"; _tsave(out, st)
            st["result"] = make_send()
            st["state"] = "done"
            activity.say(f"sent {st.get('what')} to Telegram")
        except TelegramError as e:
            st.update(state="failed", error=str(e), code=e.code)
            activity.say(f"Telegram send of {st.get('what')} failed: {e}", error=True)
        except Exception as e:                                     # never a silent stop: whatever broke is the send's answer
            st.update(state="failed", error=f"The send broke: {e}")
            activity.say(f"Telegram send of {st.get('what')} broke: {e}", error=True)
        st["finished"] = round(time.time(), 3)
        try:
            _tsave(out, st)
        finally:
            with _TQLOCK:
                _TLIVE.discard(tid)

def wait(timeout: float = 60.0) -> bool:
    """For tests: True once every queued send has finished."""
    end = time.time() + timeout
    while time.time() < end:
        with _TQLOCK:
            if not _TLIVE:
                return True
        time.sleep(0.05)
    return False
