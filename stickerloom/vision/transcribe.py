"""Per-frame vision: one caption per cell of a sheet, grid-agnostic (2x2, 3x3, anything `result.json` says), stored with the sticker.

A caption says what is VISIBLE (who, doing what, feeling what, any text in the picture) in one sentence. It is not a verdict: the verdict shown beside it is the
judge's, already on the sticker (`judge`), so a caption costs one model call per cell and none for the verdict. Nothing here touches `review.*`: a caption never
approves or rejects anything (CLAUDE.md rule 10).

Every model call goes through `VisionJudge._structured` (the same logged, parsed, once-repaired, cached call the judge uses) and needs consent
(`vision/consent.py`); a caption that is already stored for the same picture is returned without a model and without consent. It is stored on the sticker as
`caption {text, text_visible, model, version, png_sha, ts}`; the `png_sha` ties it to the picture, so an edited sticker is captioned again."""
from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from ..services import llm
from . import consent
from .judge import CACHE_TTL, JudgeError, VisionJudge, _env, _first_json, _flatten, target

CAPTION_VERSION = "caption_v1"

CAPTION_SYSTEM = f"""You describe ONE sticker image (a cut-out character on a grey background) for a person who cannot see it.
Say what the character is, what it is doing and how it feels, in one short sentence of 6 to 18 words, and quote any text that is visible in the picture.
Describe only what you see; do not judge its quality. Reply with ONE JSON object and nothing else:
{{"caption": "<one sentence>", "text_visible": "<the text in the picture, or null>"}}
{llm.DATA_RULE}"""


@dataclass
class FrameCaption:
    generation_id: str
    index: int                     # the sticker's own number (S1..Sn), the same as everywhere else
    row: int                       # 0-based position on the sheet, read from the grid in result.json
    col: int
    grid: tuple                    # (rows, cols)
    png: str                       # relative to out/G###/
    caption: str | None
    text_visible: str | None = None
    verdict: str | None = None     # the judge's verdict if there is one (APPROVE / REJECT / UNJUDGED), never produced here
    reasons: list = field(default_factory=list)
    model: str = ""
    cached: bool = False
    error: str | None = None

    def to_dict(self) -> dict:
        return {"generation_id": self.generation_id, "index": self.index, "row": self.row, "col": self.col, "grid": list(self.grid), "png": self.png,
                "caption": self.caption, "text_visible": self.text_visible, "verdict": self.verdict, "reasons": self.reasons, "model": self.model,
                "cached": self.cached, "error": self.error}


class _Parsed:
    decision = None                # `_structured` logs a decision when the parsed value has one; a caption has none

    def __init__(self, caption: str, text_visible: str | None, model: str):
        self.caption, self.text_visible, self.model = caption, text_visible, model


def _parse(text: str, model: str) -> _Parsed:
    d = _first_json(text)
    cap = str(d.get("caption") or "").strip()
    if not cap:
        raise ValueError("caption is empty")
    tv = d.get("text_visible")
    tv = str(tv).strip()[:200] if tv is not None and str(tv).strip().lower() not in ("", "null", "none") else None
    return _Parsed(cap[:300], tv, model)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def _grid(res: dict) -> tuple:
    g = res.get("grid") or [3, 3]
    try:
        return (max(1, int(g[0])), max(1, int(g[1])))
    except (TypeError, ValueError, IndexError):
        return (3, 3)


def _frame(gid_s: str, s: dict, grid: tuple) -> FrameCaption:
    rows, cols = grid
    r, c = divmod(int(s["index"]) - 1, cols)
    j = s.get("judge") or {}
    return FrameCaption(gid_s, int(s["index"]), r, c, grid, s.get("png") or "", None, None, j.get("decision"), list(j.get("reasons") or []))


def _stored(s: dict, sha: str):
    c = s.get("caption")
    return c if isinstance(c, dict) and c.get("png_sha") == sha and c.get("version") == CAPTION_VERSION and c.get("text") else None


def captions_for(out, gid, *, force: bool = False, vlm: VisionJudge | None = None, allowed=None) -> list[FrameCaption]:
    """One FrameCaption per READY sticker of the batch, in sheet order. Stored captions of unchanged pictures are returned as they are (no model, no consent); every other cell
    needs `allowed is True` (`ConsentRequired` before anything is sent) and one model call. A cell whose answer stays unusable after the one repair round comes back with
    `caption=None` and an `error`; it is never stored."""
    from ..flow import pipeline as pl
    out = Path(out)
    gid_n = int(str(gid).upper().lstrip("G"))
    gid_s = f"G{gid_n:03d}"
    res = pl.read_result(out, gid_n)
    grid = _grid(res)
    d = pl.gen_dir(out, gid_n)
    frames, todo = [], []
    for s in res.get("stickers") or []:
        if s.get("status") != "READY" or not s.get("png"):
            continue
        fr = _frame(gid_s, s, grid)
        try:
            png = (d / s["png"]).read_bytes()
        except OSError as e:
            fr.error = f"the picture cannot be read: {e}"
            frames.append(fr)
            continue
        sha = _sha(png)
        c = None if force else _stored(s, sha)
        if c:
            fr.caption, fr.text_visible, fr.model, fr.cached = c["text"], c.get("text_visible"), c.get("model", ""), True
        else:
            todo.append((fr, png, sha))
        frames.append(fr)
    if not todo:
        return sorted(frames, key=lambda f: f.index)
    consent.require(allowed)                                            # the first image is not read for a model before this line has passed
    vlm = vlm or VisionJudge(out=out)

    def one(item):
        fr, png, sha = item
        key = vlm.cache.key("vlm", "caption", sha, target()["model"], CAPTION_VERSION)
        hit = None if force else vlm.cache.get(key, "vlm_caption")
        if hit:
            fr.caption, fr.text_visible, fr.model, fr.cached = hit["text"], hit.get("text_visible"), hit.get("model", ""), True
            return
        try:
            v, meta = vlm._structured("VLM_CAPTION", CAPTION_SYSTEM, "Describe this sticker.", [_flatten(png)], gid_s, f"{gid_s}/S{fr.index}", CAPTION_VERSION, _parse)
        except JudgeError as e:
            fr.error = str(e)[:300]
            return
        fr.caption, fr.text_visible, fr.model = v.caption, v.text_visible, meta.get("model", v.model)
        vlm.cache.set(key, {"text": fr.caption, "text_visible": fr.text_visible, "model": fr.model}, CACHE_TTL)

    with ThreadPoolExecutor(max_workers=max(1, int(_env("VISION_CONCURRENCY", 2)))) as ex:
        list(ex.map(one, todo))
    fresh = {fr.index: (fr, sha) for fr, _, sha in todo if fr.caption}
    if fresh:                                                            # store what was learned, on the CURRENT result (the judge may have written since we read it)
        res2 = pl.read_result(out, gid_n)
        now = round(time.time(), 3)
        for s in res2.get("stickers") or []:
            if s.get("index") in fresh:
                fr, sha = fresh[s["index"]]
                s["caption"] = {"text": fr.caption, "text_visible": fr.text_visible, "model": fr.model, "version": CAPTION_VERSION, "png_sha": sha, "ts": now}
        pl.write_result(out, gid_n, res2)
    return sorted(frames, key=lambda f: f.index)


def sheet_with_captions(out, gid) -> dict:
    """What is stored, for the page: {generation_id, grid: [rows, cols], cells: [{index, row, col, png, caption, text_visible, verdict, reasons, model}], missing, ready}.
    Read-only: no model, no consent. A caption whose picture has changed since is not shown (it is missing again)."""
    from ..flow import pipeline as pl
    out = Path(out)
    gid_n = int(str(gid).upper().lstrip("G"))
    gid_s = f"G{gid_n:03d}"
    res = pl.read_result(out, gid_n)
    grid = _grid(res)
    d = pl.gen_dir(out, gid_n)
    cells, missing = [], 0
    for s in res.get("stickers") or []:
        if s.get("status") != "READY" or not s.get("png"):
            continue
        fr = _frame(gid_s, s, grid)
        try:
            c = _stored(s, _sha((d / s["png"]).read_bytes()))
        except OSError:
            c = None
        if c:
            fr.caption, fr.text_visible, fr.model = c["text"], c.get("text_visible"), c.get("model", "")
        else:
            missing += 1
        cells.append(fr.to_dict())
    return {"generation_id": gid_s, "grid": list(grid), "cells": sorted(cells, key=lambda x: x["index"]), "missing": missing, "ready": len(cells)}
