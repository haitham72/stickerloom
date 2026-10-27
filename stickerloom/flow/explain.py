"""Plain words for a sheet Python could not use (2026-10-02, the falcon sheet G094).

A sheet that arrives and fails a BLOCK check of the sheet stage leaves a batch whose nine stickers are all FAILED with the same reason. To a person that looked like "the task was
never retrieved" (the sheet WAS downloaded and paid for); this module says what happened, in words, and what to do. Pure: it reads a `result.json` dict and (optionally) a job
dict, it never decides anything. Since 2026-10-02 a layout problem no longer stops a sheet (it is cut anyway, `pipeline.SHEET_PROCEED`); what still stops one is a file that does not open
or a sheet with no key screen, and for the latter the card offers one free click: "Cut it anyway" (`pipeline.recut`)."""
from __future__ import annotations

import re

HINTS = {
    "grid_detected": {
        "title": "The sheet came back, but Python could not find the grid",
        "why": "Two or more characters touch or cross the gap between their cells (or the model drew a different number of cells), so Python found {value} cells where the plan has {limit}. "
               "It will not guess where one sticker ends and the next begins.",
        "fix": "A new sheet usually fixes it: the model draws the characters differently each time.",
    },
    "background_is_key": {
        "title": "The sheet came back without a clean green screen",
        "why": "The edge of the picture is not the flat key colour ({value} against a minimum of {limit}), so the background cannot be removed cleanly.",
        "fix": "Make the sheet again; if it keeps happening for this subject, a subject without green in it helps.",
    },
    "sheet_size": {
        "title": "The sheet came back too small",
        "why": "Its shortest side is {value}px and at least {limit}px is needed to cut good stickers.",
        "fix": "Make the sheet again at 2K.",
    },
    "sheet_decodes": {
        "title": "The downloaded file is not a readable picture",
        "why": "The file was received but does not open as an image.",
        "fix": "Make the sheet again.",
    },
}
GENERIC = {"title": "The sheet came back, but Python blocked it", "why": "It failed the check '{check}' ({value} against {limit}).", "fix": "A new sheet usually fixes it."}


def _fill(text: str, c: dict) -> str:
    return text.format(check=c.get("check"), value=c.get("value"), limit=c.get("limit"))


def sheet_problem(res: dict, job: dict | None = None) -> dict | None:
    """None for a healthy batch. For a batch the sheet stage blocked: {check, value, limit, title, why, fix, received: {job, cost} | None, retry: True}."""
    st = res.get("stickers") or []
    if not st or res.get("stage") not in ("sliced", "stills_reviewed") or not all((s.get("metrics") or {}).get("sheet_blocked") for s in st):
        return None
    first = next((s for s in st if s.get("report")), st[0])
    rep = (first.get("report") or [{}])[0]
    check = str(rep.get("name") or first.get("reason") or "")
    c = {"check": check, "value": rep.get("value"), "limit": rep.get("limit")}
    h = HINTS.get(check, GENERIC)
    src = str((res.get("source") or {}).get("sheet_path") or "")
    m = re.search(r"jobs[\\/](J\d+)[\\/]", src)
    received = None
    if m or job:
        received = {"job": (job or {}).get("id") or (m.group(1) if m else None), "cost": (job or {}).get("cost")}
    return {**c, "title": h["title"], "why": _fill(h["why"], c), "fix": h["fix"], "received": received, "retry": True,
            "cut_anyway": check != "sheet_decodes"}


# What a blocked sticker (a cell that was cut, or an animation) says in plain words, one line each: the reason Python stopped it, for the tile. `final` ones are Telegram's own limits (or nothing
# usable came out of the cell): they cannot be allowed (flow/gates.py `allow_info`); the others are judgement calls a person may "use anyway".
BLOCK_WORDS = {
    "no_outline_on_sheet": "a white outline was found around a subject on the video sheet",
    "video_specs": "the returned video is shorter than the expected minimum",
    "layout_match": "the first video frame does not match the sheet's slot layout",
    "video_decodes": "the returned video file cannot be decoded",
    "slots_match_approved": "the video sheet's filled slots do not match the approved stickers",
    "blank_cell": "almost nothing was found in this cell",
    "empty_subject": "almost nothing was found in this cell",
    "foreground": "almost nothing is left of the picture",
    "inside_cell": "the character touches the edge of its cell, so it may be cut off",
    "no_spill": "green-screen colour is left on the edge of the character",
    "holes": "a big part of the character was cut away (a hole inside it)",
    "inside_slot": "the character leaves its own slot on the sheet",
    "cross_slot": "the character reaches into a neighbour's slot",
    "loop_seam": "the loop does not close: it jumps when it starts again",
    "dimensions": "the picture is not the size Telegram needs",
    "transparent_corners": "the corners are not transparent",
    "static_file": "the file is over Telegram's size limit for a still",
    "size_budget": "the file is over Telegram's size limit for an animation",
    "codec_vp9": "the video is not VP9, which Telegram requires",
    "fps": "the frame rate is above Telegram's limit",
    "duration": "the animation is longer than Telegram allows",
    "no_audio": "the animation has a sound track, which Telegram refuses",
    "alpha_mode_tag": "the transparency tag is missing from the video file",
    "alpha_decoded": "the video has no real transparency",
    "wrong_chroma_key": "the green screen of this cell is not the colour expected",
    "no_vp9_encoder": "this computer's ffmpeg cannot encode VP9",
    "probe_failed": "the video could not be read",
    "no_video_source": "there is no video for this cell",
    "exception": "something went wrong while cutting it",
    "verifier_error": "the check itself crashed",
}


def block_words(check: str | None) -> str:
    """One plain line for a check id or a reason (the id itself when it is unknown)."""
    c = str(check or "")
    return BLOCK_WORDS.get(c) or (c.replace("_", " ") if c else "blocked")
