"""Sessions and memory.

A session is one conversation. It lives in `out/sessions/S###.json` (files are the primary store, like generations, so the chat works
without Postgres) and is mirrored best-effort into Postgres (`sessions` / `interactions` / `feedback`, migration 004) and kept hot in
Redis. Nothing here holds image bytes.

Memory is STRUCTURED, never "the whole history":
- `subjects`: for every subject the user asked for, the metadata of its PASSES (generations) in this chat: ids, prompt, grid, style,
  parent, counts, what the user liked and disliked. This is the per-subject summary every turn starts from.
- `feedback`: each statement about a sticker, TEMPORARY (applies to the next generation only) or PERSISTENT (only when the user says
  so explicitly: "I never want dark outlines"). Nothing is inferred about the user's feelings.
- `focus`: the generation / stickers "it" and "that one" mean.
- `interactions`: the raw turns, always kept. Every REDUCER_WINDOW turns a narrative summary is written; the structured part is rebuilt
  from the data every time, so ids can never be lost by a summariser.

`context(level)` is what a model sees: MINIMAL (summary + request + settings), STANDARD (+ the focused generation's stickers and one
reference), HIGH (+ prompts and lineage). Never all nine images, never the full history."""
from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
from pathlib import Path

from ..runtime import atomic

REDUCER_WINDOW = 15
MAX_MESSAGES, MAX_INTERACTIONS, MAX_FEEDBACK = 400, 300, 200       # what one session file keeps; older turns live on in the narrative, the subjects and the passes
OUTSIDE_SHOWN = 5                                                  # how many recent batches made outside the chat the summary names
DEFAULT_SETTINGS = {"grid": "2x2", "style_id": "flat_vector", "ask_before_spending": True, "ai": True, "actions": "predefined", "allow_vlm": None, "stage": "auto",
                    "creator": {"on": False, "scope": "images", "bypass": False}}      # allow_vlm: None = not asked yet (vision/consent.py); stage: agent/stages.py
_IO = threading.RLock()


class SessionError(Exception):
    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = code


def _now() -> float:
    return round(time.time(), 3)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_") or "subject"


def gid_of(g) -> str:
    """'G012' | 12 | '12' -> 'G012'."""
    s = str(g).strip().upper().lstrip("G")
    return f"G{int(s):03d}"


class SessionStore:
    def __init__(self, out: Path, cache=None, user: str = "local", see_all: bool = True):
        """`user` owns what it creates; `see_all` (owners) lists and opens every chat, a member only their own (someone else's chat is a 404, not a 403)."""
        self.out = Path(out)
        self.user, self.see_all = user, see_all
        self.dir = self.out / "sessions"
        if cache is None:
            from ..runtime import cache as _c
            cache = _c.default()
        self.cache = cache

    # ---- files ------------------------------------------------------------------------------------------------------
    def _path(self, sid: str) -> Path:
        if not re.fullmatch(r"S\d{3,}", str(sid)):
            raise SessionError(f"bad session id {sid!r}", 400)
        return self.dir / f"{sid}.json"

    # -- the person's own stage: out/sessions/_prefs.json {user: {stage}}
    def _prefs_path(self) -> Path:
        return self.dir / "_prefs.json"

    def person_stage(self) -> str | None:
        try:
            return (json.loads(atomic.read_text(self._prefs_path())).get(self.user) or {}).get("stage")
        except (OSError, ValueError):
            return None

    def set_person_stage(self, stage: str) -> None:
        with _IO:
            self.dir.mkdir(parents=True, exist_ok=True)
            try:
                d = json.loads(atomic.read_text(self._prefs_path()))
            except (OSError, ValueError):
                d = {}
            d[self.user] = {**(d.get(self.user) or {}), "stage": stage}
            atomic.write_text(self._prefs_path(), json.dumps(d, ensure_ascii=False, indent=1))

    def create(self, title: str | None = None, settings: dict | None = None) -> dict:
        ps = self.person_stage()
        if ps and not (settings or {}).get("stage"):
            settings = {**(settings or {}), "stage": ps}          # a new chat starts on the person's own stage
        with _IO:
            self.dir.mkdir(parents=True, exist_ok=True)
            n = max([int(f.stem[1:]) for f in self.dir.glob("S*.json") if f.stem[1:].isdigit()] or [0]) + 1
            s = {"id": f"S{n:03d}", "user": self.user, "title": title or "New chat", "created": _now(), "updated": _now(),
                 "settings": {**DEFAULT_SETTINGS, **(settings or {})}, "focus": {"generation": None, "stickers": []},
                 "subjects": [], "preferences": {"persistent": []}, "feedback": [], "interactions": [], "messages": [],
                 "summary": {"narrative": "", "upto": 0}, "pending": None}
            self.save(s)
            return s

    def load(self, sid: str) -> dict:
        f = self._path(sid)
        with _IO:
            if not f.is_file():
                raise SessionError(f"No session {sid}", 404)
            s = json.loads(atomic.read_text(f))
            if not self.see_all and s.get("user", "local") != self.user:
                raise SessionError(f"No session {sid}", 404)
        if s.get("user", "local") == self.user and s.get("title") != "Telegram":
            ps = self.person_stage()
            if ps:
                s.setdefault("settings", {})["stage"] = ps        # every chat of this person follows the one slider
        return s

    @staticmethod
    def _cap(s: dict) -> None:
        """Keep the newest turns. The file is rewritten at every step of a turn, so an unbounded chat made every step slower than the last. The structured
        part (subjects, passes, likes) is never trimmed; `summary.upto` indexes `interactions`, so only already-summarised ones are dropped."""
        if len(s["messages"]) > MAX_MESSAGES:
            del s["messages"][: len(s["messages"]) - MAX_MESSAGES]
        over = len(s["interactions"]) - MAX_INTERACTIONS
        drop = min(over, s["summary"].get("upto", 0)) if over > 0 else 0
        if drop > 0:
            del s["interactions"][:drop]
            s["summary"]["upto"] -= drop
        if len(s["feedback"]) > MAX_FEEDBACK:
            del s["feedback"][: len(s["feedback"]) - MAX_FEEDBACK]

    def save(self, s: dict) -> None:
        s["updated"] = _now()
        self._cap(s)
        p = self._path(s["id"])
        data = json.dumps(s, indent=1, ensure_ascii=False).encode("utf-8")
        with _IO:
            p.parent.mkdir(parents=True, exist_ok=True)
            atomic.write_bytes(p, data)             # unique temp file + atomic replace, retried on Windows while the target is open
        try:
            self.cache.set(self.cache.key("session", s["id"]), {"id": s["id"], "title": s["title"], "focus": s["focus"],
                                                                 "settings": s["settings"], "updated": s["updated"]}, 3600)
        except Exception:
            pass
        try:  # Postgres mirror: best effort, only for the real out/ (same rule as generations)
            from ..store import sync
            sync.sync_session(self.out, s)
        except Exception:
            pass

    def list(self) -> list[dict]:
        rows = []
        for f in sorted(self.dir.glob("S*.json"), reverse=True) if self.dir.is_dir() else []:
            try:
                s = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not self.see_all and s.get("user", "local") != self.user:
                continue
            rows.append({"id": s["id"], "title": s.get("title"), "updated": s.get("updated"), "created": s.get("created"),
                         "subjects": [x["name"] for x in s.get("subjects", [])], "turns": len(s.get("interactions", [])),
                         "focus": (s.get("focus") or {}).get("generation")})
        rows.sort(key=lambda r: r.get("updated") or 0, reverse=True)
        return rows

    def delete(self, sid: str) -> None:
        f = self._path(sid)
        self.load(sid)                                   # a member cannot delete someone else's chat (404)
        with _IO:
            if f.is_file():
                f.unlink()

    # ---- the turn log ----------------------------------------------------------------------------------------------------
    def add_message(self, s: dict, role: str, text: str, cards: list | None = None, steps: list | None = None, **extra) -> dict:
        n = max([int(x["id"][1:]) for x in s["messages"] if str(x.get("id", ""))[1:].isdigit()] or [0]) + 1       # not len()+1: trimmed sessions would repeat ids
        m = {"id": f"m{n}", "role": role, "text": text, "cards": cards or [], "steps": steps or [], "ts": _now(), **extra}
        s["messages"].append(m)
        return m

    def add_interaction(self, s: dict, user: str, assistant: str, intents: list, resolved: dict, generation_id: str | None) -> dict:
        it = {"seq": (s["interactions"][-1]["seq"] if s["interactions"] else 0) + 1, "ts": _now(), "user": user, "assistant": assistant, "intents": intents,
              "resolved": resolved, "generation_id": generation_id}
        s["interactions"].append(it)
        if s.get("title") in (None, "", "New chat") and user.strip():
            s["title"] = user.strip()[:48]
        return it

    # ---- subjects and passes -------------------------------------------------------------------------------------------------
    def subject_of(self, s: dict, name: str, create: bool = True) -> dict | None:
        k = slug(name)
        for x in s["subjects"]:
            if x["slug"] == k:
                return x
        if not create:
            return None
        x = {"name": name.strip(), "slug": k, "passes": []}
        s["subjects"].append(x)
        return x

    def add_pass(self, s: dict, subject: str, generation: str | None = None, job: str | None = None, prompt: str = "",
                 grid: str = "2x2", style_id: str = "", parent: str | None = None, note: str = "") -> dict:
        subj = self.subject_of(s, subject)
        p = next((q for q in subj["passes"] if (generation and q.get("generation") == generation) or (job and q.get("job") == job)), None)
        if p is None:
            p = {"generation": generation, "job": job, "prompt": prompt, "grid": grid, "style_id": style_id, "parent": parent,
                 "created": _now(), "ready": 0, "approved": 0, "rejected": 0, "liked": [], "disliked": [], "note": note}
            subj["passes"].append(p)
        if generation:
            p["generation"] = generation
        return p

    def refresh(self, s: dict) -> dict:
        """Re-read each pass's counts from its result file and resolve jobs to generations. Cheap; run before a summary."""
        from ..flow import pipeline as pl
        from ..generation import jobs as _jobs
        for subj in s["subjects"]:
            for p in subj["passes"]:
                if not p.get("generation") and p.get("job"):
                    try:
                        g = _jobs.read(self.out, p["job"]).get("generation")
                        p["generation"] = gid_of(g) if g else None
                        if p["generation"] and not p.get("undone") and (s.get("focus") or {}).get("generation") != p["generation"] and p["created"] >= (self.latest_pass(s, with_generation=True) or {"created": 0})["created"]:
                            s["focus"] = {"generation": p["generation"], "stickers": []}        # a sheet that comes back is what "it" and "number 3" mean from now on
                    except Exception:
                        pass
                if p.get("generation"):
                    try:
                        res = pl.read_result(self.out, int(p["generation"][1:]))
                    except Exception:
                        continue
                    st = res["stickers"]
                    p["ready"] = sum(1 for x in st if x["status"] == "READY")
                    p["approved"] = sum(1 for x in st if x["review"]["still"] == "APPROVED")
                    p["rejected"] = sum(1 for x in st if x["review"]["still"] == "REJECTED")
                    p["stage"] = res.get("stage")
                    p["error"] = res.get("error")
                    if res.get("parent") and not p.get("parent"):
                        p["parent"] = gid_of(res["parent"])
        return s

    def latest_pass(self, s: dict, with_generation: bool = False) -> dict | None:
        """The newest pass. `with_generation`: the newest one that has a batch (a job that is still running, or failed, is not something to edit or animate)."""
        best = None
        for subj in s["subjects"]:
            for p in subj["passes"]:
                if (with_generation and not p.get("generation")) or p.get("undone"):         # an undone refinement is no longer what "the last batch" means
                    continue
                if best is None or p["created"] >= best["created"]:
                    best = p
        return best

    def subject_for_generation(self, s: dict, gid: str) -> dict | None:
        return next((x for x in s["subjects"] if any(p.get("generation") == gid for p in x["passes"])), None)

    # ---- feedback and preferences ---------------------------------------------------------------------------------------------------
    def add_feedback(self, s: dict, polarity: str, sticker_ids: list, text: str, scope: str = "TEMPORARY") -> dict:
        fb = {"ts": _now(), "polarity": polarity, "scope": scope, "sticker_ids": sticker_ids, "text": text}
        s["feedback"].append(fb)
        for sid in sticker_ids:
            g = sid.split("/")[0]
            subj = self.subject_for_generation(s, g)
            if not subj:
                continue
            p = next(q for q in subj["passes"] if q.get("generation") == g)
            mine, other = ("liked", "disliked") if polarity == "POSITIVE" else ("disliked", "liked")
            if sid not in p[mine]:
                p[mine].append(sid)
            if sid in p[other]:
                p[other].remove(sid)
        if scope == "PERSISTENT" and text.strip() and text.strip() not in s["preferences"]["persistent"]:
            s["preferences"]["persistent"].append(text.strip())
        return fb

    def consume_temporary(self, s: dict) -> list:
        """The temporary feedback that shapes the NEXT generation only; it is marked used and never becomes a lasting preference."""
        out = [f for f in s["feedback"] if f["scope"] == "TEMPORARY" and not f.get("used")]
        for f in out:
            f["used"] = True
        return out

    def traits(self, s: dict) -> list[str]:
        """What the user has consistently asked for (>= 2 times, in their own words): shown as a note in the step trace and added to the
        next plan. Derived from what was SAID, never from feelings."""
        count: dict[str, int] = {}
        for it in s["interactions"]:
            t = it["user"].lower()
            for trait, pat in TRAIT_PATTERNS.items():
                if re.search(pat, t):
                    count[trait] = count.get(trait, 0) + 1
        return [t for t, n in count.items() if n >= 2]

    # ---- summaries and context -------------------------------------------------------------------------------------------------------
    def _info(self, gid: int, res: dict | None = None) -> dict | None:
        """What the chat needs to know of one batch on disk, or None when it does not exist or this user may not see it (an owner sees every batch)."""
        from ..flow import pipeline as pl
        try:
            res = res or pl.read_result(self.out, gid)
        except Exception:
            return None
        if not self.see_all and res.get("owner") != self.user:
            return None
        st = res.get("stickers") or []
        grid = res.get("grid") or []
        return {"generation": f"G{gid:03d}", "prompt": str(res.get("prompt") or "").strip(), "parent": res.get("parent"),
                "grid": "x".join(str(x) for x in grid) if len(grid) == 2 else "3x3", "style_id": res.get("style_id") or (res.get("slots") or {}).get("style_id") or "",
                "ready": sum(1 for x in st if x.get("status") == "READY"),
                "approved": sum(1 for x in st if (x.get("review") or {}).get("still") == "APPROVED")}

    def outside_info(self, gid: str) -> dict | None:
        """The batch `G012` if it exists and is this user's to see (see `_info`); None otherwise: a stranger's batch is never pulled into a chat."""
        try:
            return self._info(int(str(gid).upper().lstrip("G")))
        except ValueError:
            return None

    def adopt(self, s: dict, gid: str) -> dict | None:
        """Make a batch the Studio or the command line created a pass of this session, so an edit, an animation or "that one" can act on it. The subject is named after its
        prompt. Idempotent; None when the batch does not exist or is not this user's."""
        gid = gid_of(gid)
        if self.subject_for_generation(s, gid):
            return self.subject_for_generation(s, gid)
        info = self.outside_info(gid)
        if not info:
            return None
        self.add_pass(s, info["prompt"] or gid, generation=gid, prompt=info["prompt"] or gid, grid=info["grid"], style_id=info["style_id"],
                      parent=gid_of(info["parent"]) if info["parent"] else None, note="made in the Studio")
        return self.subject_for_generation(s, gid)

    def outside_batches(self, s: dict) -> list[dict]:
        """The newest batches the Studio or the command line made, i.e. generations this chat never recorded as a pass, newest first, only those this
        user may see (an owner sees all). The chat answers "what did you just create" from these too."""
        from ..flow import pipeline as pl
        mine = {p.get("generation") for subj in s["subjects"] for p in subj["passes"]}
        rows = []
        for gid in list(reversed(pl.list_ids(self.out)))[:60]:
            if f"G{gid:03d}" in mine:
                continue
            info = self._info(gid)
            if info:
                rows.append(info)
            if len(rows) >= OUTSIDE_SHOWN:
                break
        return rows

    def set_vision(self, s: dict, value) -> None:
        """The person's answer about AI vision is STATE (UI/UX spec P10): the setting, the fact that it was asked (so it is never asked again in this chat) and an acknowledgement the next turn says once.
        It makes no message, no card and no turn; the settings route and the tests call exactly this."""
        was = s["settings"].get("allow_vlm")
        s["settings"]["allow_vlm"] = bool(value)
        s["vision_asked"] = True
        if was is not bool(value):
            s["vision_ack"] = "allowed" if value else "refused"

    def focus_context(self, s: dict) -> dict:
        """The focused ids and batch metadata, independent of the capped narrative prompt."""
        self.refresh(s)
        focus = copy.deepcopy(s.get("focus") or {"generation": None, "stickers": []})
        gid = focus.get("generation")
        if gid:
            p = next((p for subj in s["subjects"] for p in subj["passes"] if p.get("generation") == gid), {})
            info = self.outside_info(gid) or p
            focus.update({k: info.get(k) for k in ("grid", "style_id", "prompt")})
            focus["items"] = [{k: item.get(k) for k in ("id", "key", "status", "still")}
                              for item in self.generation_card(gid).get("stickers", [])]
        return copy.deepcopy(focus)

    def summary_structured(self, s: dict) -> dict:
        """Detached, queryable memory; no model or text parsing needed."""
        focus = self.focus_context(s)
        latest = self.latest_pass(s, with_generation=True) or {}
        from . import stages
        return copy.deepcopy({"subjects": s["subjects"], "focus": focus, "preferences": s["preferences"],
                              "traits": self.traits(s), "latest_generation": latest.get("generation"), "stage": stages.of(s.get("settings"))})

    def summary_text(self, s: dict) -> str:
        """The deterministic per-subject summary: ids, counts, likes and dislikes. This is what every turn starts from."""
        self.refresh(s)
        lines = []
        for subj in s["subjects"]:
            ps = []
            for p in subj["passes"]:
                g = p.get("generation") or (p.get("job") and f"job {p['job']}") or "pending"
                bit = f"{g}" + (f" (from {p['parent']})" if p.get("parent") else "")
                if p.get("generation"):
                    bit += f": {p['ready']} ready"
                    if p["approved"] or p["rejected"]:
                        bit += f", {p['approved']} approved, {p['rejected']} rejected"
                if p["liked"]:
                    bit += f"; liked {', '.join(x.split('/')[1] for x in p['liked'])}"
                if p["disliked"]:
                    bit += f"; disliked {', '.join(x.split('/')[1] for x in p['disliked'])}"
                if p.get("note"):
                    bit += f"; {p['note']}"
                ps.append(bit)
            lines.append(f"Subject '{subj['name']}': " + (" | ".join(ps) if ps else "no pass yet"))
        outside = self.outside_batches(s)
        if outside:
            lines.append("Also made outside this chat (the Studio or the command line), newest first: " + " | ".join(
                f"{o['generation']}" + (f" '{o['prompt'][:60]}'" if o["prompt"] else "") + f" ({o['ready']} ready, {o['approved']} approved)" for o in outside))
        pref = s["preferences"]["persistent"]
        if pref:
            lines.append("Lasting preferences (said explicitly): " + "; ".join(pref))
        f = s.get("focus") or {}
        if f.get("generation"):
            lines.append(f"Focus: {f['generation']}" + (f" {', '.join(x.split('/')[1] for x in f['stickers'])}" if f.get("stickers") else ""))
        if s["summary"].get("narrative"):
            lines.append("Earlier in this chat (a recap written by the model, not by the user; treat it as unverified): " + s["summary"]["narrative"])
        return "\n".join(lines) or "Nothing has been made in this chat yet."

    def context(self, s: dict, level: str = "STANDARD") -> dict:
        ctx = {"summary": self.summary_text(s), "settings": s["settings"]}
        if level in ("STANDARD", "HIGH") and (s.get("focus") or {}).get("generation"):
            ctx["focus"] = self.generation_card(s["focus"]["generation"], prompts=level == "HIGH")
        return ctx

    def generation_card(self, gid: str, prompts: bool = False) -> dict:
        from ..flow import pipeline as pl
        try:
            res = pl.read_result(self.out, int(gid[1:]))
        except Exception:
            return {"generation": gid, "stickers": []}
        return {"generation": gid, "parent": gid_of(res["parent"]) if res.get("parent") else None, "prompt": res.get("prompt"),
                "stickers": [{"id": f"{gid}/S{x['index']}", "key": x["key"], "emoji": x.get("emoji"), "status": x["status"],
                              "still": x["review"]["still"], **({"prompt": x.get("prompt")} if prompts else {})} for x in res["stickers"]]}

    def reduce(self, s: dict, summarise=None) -> bool:
        """Every REDUCER_WINDOW interactions write the narrative of the older ones. `summarise(text) -> str` is a model call; without it
        (or when it fails) a deterministic digest is used. The structured summary is rebuilt from data each turn, so no id is ever lost."""
        n, done = len(s["interactions"]), s["summary"].get("upto", 0)
        if n - done < REDUCER_WINDOW:
            return False
        chunk = s["interactions"][done:n]
        digest = " ".join(f"[{i['seq']}] {i['user'].strip()[:80]}" for i in chunk)
        text = None
        if summarise is not None:
            try:
                text = summarise(digest, s["summary"].get("narrative", ""))
            except Exception:
                text = None
        text = (text or "").strip() or (s["summary"].get("narrative", "") + " " + digest).strip()
        s["summary"] = {"narrative": text[-1800:], "upto": n}
        return True


TRAIT_PATTERNS = {
    "a wider range of emotions": r"(more|wider|different|varied|vary).{0,24}(emotion|feeling|expression|mood)|(emotion|expression).{0,24}(range|variety)",
    "bolder, more expressive faces": r"(more|bigger|bolder).{0,16}(expressive|expression|dramatic|energetic|energy)",
    "no dark outlines": r"(no|never|without).{0,12}(dark|black).{0,8}outline",
    "an anime-inspired look": r"\b(?:more|want|prefer|use|make).{0,30}\banime\b",
    "a less cartoony look": r"\b(?:less|not so|too)\s+(?:cartoony|cartoonish)\b",
}
