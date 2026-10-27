"""Prompts of the particle effects: the text Kling gets for a text-only burst video, built by code from a small plan (template-locked: a model or a person fills the JSON, `lint_plan`
checks it, `video_prompt` writes the text, so the prompt can never drift or smuggle in anything else).

A burst effect is what Telegram does when you press an emoji: it starts from NOTHING, the particles burst out of the centre, grow a little, then gravity takes them down until the
screen is empty again. So the video has no start image (nothing to start from) and every burst must begin and end empty. The screen colour is the engine's key colour: green,
unless a particle of the effect is itself green (leaves, grass, a frog): then blue, so the keyer does not eat the particles.

The rule of the video text (version 2, after Kling drew boxes, walls and panels from the words "cell", "grid" and "square frame"): the layout is only ever said as
POSITION NAMES (top-left, top-right, bottom-left, bottom-right), the whole picture is ONE seamless flat key-colour background described once and positively, and no word that names
a shape or a division is used: square, frame, poster, screen, box, cell, grid, panel, section, tile, border, divider, split, layout, invisible (`BANNED_WORDS`, a test builds every
template text and looks). Aspect ratio and size are request parameters, never words. The clip that comes back is measured for exactly that (`engine/effect_checks.py`, `key_is_seamless`).
These words are forbidden in the TEMPLATE text only: a person's own particle names are not policed for them.

`TEMPLATE_ID` / `VERSION` are stored with every effect and every job (`request.effect`); a new wording is a new version, a used one is never edited: version 1 stays buildable
(`video_prompt(..., version=1)`) so a stored job can still be read and rebuilt."""
from __future__ import annotations

import re

TEMPLATE_ID = "effect_video"
VERSION = 2
VERSIONS = (1, 2)

MAX_ELEMENTS = 9
MAX_WORDS = 5
MAX_GRID = 3
GREENISH = re.compile(r"\b(green|leaf|leaves|grass|lime|mint|clover|frog|pickle|emerald|cactus|basil|herb|moss|fern|jade|olive)\b", re.I)
FORBIDDEN = re.compile(r"\b(stickers?|text|letters?|words?|logo|watermark|hands?|fingers?|faces?|people|person)\b", re.I)
# the words that make an image / video model draw boxes, walls, panels or a poster of faces: never in the template text of a version-2 prompt (a test builds every layout and looks)
BANNED_WORDS = re.compile(r"\b(squares?|frames?|posters?|screens?|box(?:es)?|cells?|grids?|panels?|sections?|tiles?|tiled|borders?|dividers?|splits?|splitting|layouts?|invisible)\b", re.I)

# one entry per position, so the bursts of one video are different takes of the same effect
VARIATIONS = [
    "a tight fast vortex", "a wide gentle burst", "a slow lazy swirl", "a sharp pop that rises high",
    "a clockwise spiral", "a counter-clockwise spiral", "a heavy fall with large particles", "a light floaty fall with small particles", "a fountain that shoots straight up",
]
# what version 1 said for the same entries ("pieces": the wording of the first template, kept so a stored job's prompt can be rebuilt)
_VARIATIONS_V1 = [
    "a tight fast vortex", "a wide gentle burst", "a slow lazy swirl", "a sharp pop that rises high first",
    "a clockwise spiral", "a counter-clockwise spiral", "a heavy fall with large pieces", "a light floaty fall with small pieces", "a fountain that shoots straight up",
]
_COUNT = {1: "One", 2: "Two", 3: "Three", 4: "Four", 6: "Six", 9: "Nine"}


def key_colour_for(elements: list[str]) -> str:
    """The screen colour: green, unless a particle of the effect is green (then blue)."""
    return "blue" if any(GREENISH.search(e) for e in elements) else "green"


def lint_plan(plan: dict) -> dict:
    """The plan the template is filled from: {subject, elements[1..9], style?}. Returns the cleaned plan; raises ValueError with the reason (a human can fix and retry)."""
    subject = " ".join(str(plan.get("subject") or "").split())[:60]
    if not subject:
        raise ValueError("the effect needs a subject (what the emoji is)")
    els: list[str] = []
    for e in plan.get("elements") or []:
        e = " ".join(str(e).split())[:60].strip(" .,;")
        if not e or e.lower() in {x.lower() for x in els}:
            continue
        if len(e.split()) > MAX_WORDS:
            raise ValueError(f"'{e}' is too long: at most {MAX_WORDS} words per particle")
        if FORBIDDEN.search(e):
            raise ValueError(f"'{e}' cannot be a particle of a burst (no text, logos, hands, faces, people, and never the word sticker)")
        els.append(e)
    if not els:
        raise ValueError("name at least one particle that bursts out (for Batman: bat signals, bats)")
    if len(els) > MAX_ELEMENTS:
        raise ValueError(f"at most {MAX_ELEMENTS} different particles")
    style = " ".join(str(plan.get("style") or "glossy cartoon look, bold clean shapes, vivid colours").split())[:120]
    if FORBIDDEN.search(style):
        raise ValueError("the style line cannot contain text, logos, people or the word sticker")
    return {"subject": subject, "elements": els, "style": style}


def _list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _grid(rows: int, cols: int) -> None:
    if not (1 <= rows <= MAX_GRID and 1 <= cols <= MAX_GRID):
        raise ValueError(f"the layout is at most {MAX_GRID}x{MAX_GRID}")


def positions(rows: int, cols: int) -> list[str]:
    """The name of every position of a rows x cols layout, reading order: 2x2 = top-left, top-right, bottom-left, bottom-right; 3x3 = top-left, top-centre, top-right,
    middle-left, centre, middle-right, bottom-left, bottom-centre, bottom-right. This is the ONLY way a version-2 text speaks of the layout."""
    _grid(rows, cols)
    rs = {1: [""], 2: ["top", "bottom"], 3: ["top", "middle", "bottom"]}[rows]
    cs = {1: [""], 2: ["left", "right"], 3: ["left", "centre", "right"]}[cols]
    out = []
    for r in rs:
        for c in cs:
            out.append(("centre" if (r, c) == ("middle", "centre") else f"{r}-{c}") if r and c else r or c or "centre")
    return out


def _screen_name(key: str) -> str:
    return "#00FF00 green" if key == "green" else "#0000FF blue"


def _takes(rows: int, cols: int, cells: list[str] | None, table: list[str]) -> list[str]:
    n = rows * cols
    takes = list(cells or table)[:n]
    while len(takes) < n:
        takes.append(table[len(takes) % len(table)])
    return takes


def _video_prompt_v1(plan: dict, rows: int, cols: int, cells: list[str] | None) -> str:
    """Version 1 (2026-10-02, used by J037-J039), unchanged: it said 'cell' ten times and Kling drew every cell as a box. Kept only so a stored job can be rebuilt."""
    p = lint_plan(plan)
    key = key_colour_for(p["elements"])
    screen = _screen_name(key)
    n = rows * cols
    takes = _takes(rows, cols, cells, _VARIATIONS_V1)
    layout = "; ".join(f"cell {i + 1} ({['top', 'middle', 'bottom'][min(i // cols, 2)] if rows == 3 else ['top', 'bottom'][i // cols]} row, "
                       f"{['left', 'middle', 'right'][i % cols] if cols == 3 else ['left', 'right'][i % cols]}): {takes[i]}" for i in range(n))
    return (
        f"A flat, perfectly even, solid {screen} screen with no shadows, no floor, no gradient, no text and no borders. Static camera, square frame, 3 seconds. "
        f"The frame is an invisible grid of {rows} by {cols} equal square cells and nothing ever crosses from one cell into another. "
        f"In EVERY cell at the same time, the same small burst effect plays: {_list(p['elements'])}, all related to {p['subject']}. "
        f"Timeline for each cell: from 0.0 to 0.4 seconds the cell is completely empty {key} screen. At 0.4 seconds the pieces appear at the exact centre of the cell and burst outward "
        f"in a spiralling vortex, growing a little as they fly. From about 1.2 seconds gravity takes over: the pieces slow down, arc and fall downward out of the cell. "
        f"By 2.6 seconds every piece has left the cell and the cell is completely empty {key} screen again until the end at 3.0 seconds. "
        f"The pieces are small and separate, never touching the cell edge; only the pieces listed are drawn, never one large central {p['subject']}. "
        f"Look: {p['style']}. Each cell is a different take of the same effect: {layout}."
    )


def _video_prompt_v2(plan: dict, rows: int, cols: int, cells: list[str] | None) -> str:
    """Version 2: position names only (they carry the per-position take too), ONE seamless flat background said once, static camera, no shape or division word, no aspect ratio."""
    p = lint_plan(plan)
    key = key_colour_for(p["elements"])
    n = rows * cols
    pos = positions(rows, cols)
    takes = _takes(rows, cols, cells, VARIATIONS)
    by_pos = _list([f"{pos[i]} {takes[i]}" for i in range(n)])
    if n == 1:
        where, centre = "One small burst plays in the centre.", "the centre"
    elif (rows, cols) == (2, 2):
        where, centre = f"Four small bursts play at the same time, one in each quarter, each centred in its quarter: {by_pos}.", "the centre of its quarter"
    else:
        where, centre = f"{_COUNT.get(n, str(n))} small bursts play at the same time, one at each position, each centred on its position: {by_pos}.", "its own centre"
    return (
        f"{where} "
        f"Everywhere the background is one seamless, flat, uniform, pure {_screen_name(key)}: no texture, no shading or shadows, no pattern, no vignette. "
        f"Static camera. "
        f"Timeline: for the first 0.4 seconds there is only the {key} background. At 0.4 seconds each burst starts from {centre}: {_list(p['elements'])}, "
        f"spiralling outward in a vortex and growing a little. From 1.2 seconds gravity pulls them down in arcs. "
        f"From 2.6 seconds there is only the {key} background again until 3.0 seconds. "
        f"Only the particles listed are drawn, small, never one large central {p['subject']}. "
        f"Look: {p['style']}."
    )


def video_prompt(plan: dict, rows: int = 2, cols: int = 2, cells: list[str] | None = None, version: int | None = None) -> str:
    """The Kling prompt for a rows x cols layout of bursts (2x2 or 3x3). `cells` overrides the per-position takes. `version` is the template version (default: the current one);
    version 1 is the first wording, kept so a stored job can be rebuilt."""
    _grid(rows, cols)
    version = VERSION if version is None else int(version)
    if version not in VERSIONS:
        raise ValueError(f"template {TEMPLATE_ID} has no version {version}")
    return (_video_prompt_v1 if version == 1 else _video_prompt_v2)(plan, rows, cols, cells)


def describe(plan: dict, rows: int, cols: int, version: int | None = None) -> dict:
    """What the page shows next to the price: the screen colour, the particles and the prompt that will be sent (template id + version are stored with the job)."""
    p = lint_plan(plan)
    v = VERSION if version is None else int(version)
    return {"template": TEMPLATE_ID, "version": v, "key": key_colour_for(p["elements"]), "grid": [rows, cols], "plan": p, "prompt": video_prompt(p, rows, cols, version=v)}


# ---------- the particles sheet: an AI-drawn sheet of the burst's own particles (the sprites of the simulated burst) ----------
# The template id stays `effect_pieces` (stored plans and jobs carry it); the wording of version 2 says "particles".
PIECES_TEMPLATE_ID = "effect_pieces"
PIECES_VERSION = 2
PIECES_VERSIONS = (1, 2)

# one entry per "round" over the particles: when there are fewer particles than positions the same particle is drawn again in another size / angle, so no two match
PIECE_VARIANTS = ["", "large", "small and tilted", "tiny", "medium, turned a little", "small and upright", "large and tilted", "medium, lying flat", "small, leaning"]


def _where(i: int, rows: int, cols: int) -> str:
    """Version 1's wording of a position ('top row, left'); kept for stored prompts."""
    row = ["top", "middle", "bottom"][min(i // cols, 2)] if rows == 3 else ["top", "bottom"][i // cols] if rows == 2 else "only"
    col = ["left", "middle", "right"][i % cols] if cols == 3 else ["left", "right"][i % cols] if cols == 2 else "only"
    return f"{row} row, {col}"


def pieces_cells(plan: dict, rows: int = 2, cols: int = 2) -> list[dict]:
    """The label of every place of the particles sheet: the effect's particles cycled to fill all places, each round in another size / angle. `[{pos, label}]`, labels unique."""
    _grid(rows, cols)
    els = lint_plan(plan)["elements"]
    out = []
    for i in range(rows * cols):
        var = PIECE_VARIANTS[(i // len(els)) % len(PIECE_VARIANTS)]
        out.append({"pos": i + 1, "label": els[i % len(els)] + (f", {var}" if var else "")})
    return out


def _theme(subject: str) -> str:
    """The subject as the particles sheet may say it: the word sticker is never sent to the image model (it draws a white die-cut border)."""
    return " ".join(re.sub(r"\bstickers?\b", " ", subject, flags=re.I).split()) or "the emoji"


def _pieces_prompt_v1(plan: dict, rows: int, cols: int) -> str:
    """Version 1 (2026-10-03, used by J040), unchanged: kept so a stored plan's prompt can be rebuilt."""
    p = lint_plan(plan)
    theme = _theme(p["subject"])
    key = key_colour_for(p["elements"])
    screen = _screen_name(key)
    cells = pieces_cells(p, rows, cols)
    n = rows * cols
    lines = "\n".join(f"Piece {c['pos']} ({_where(c['pos'] - 1, rows, cols)}): {c['label']}" for c in cells)
    return (
        f"One illustration of {n} separate small objects arranged in {rows} rows of {cols}, evenly spaced across a single seamless background.\n"
        f"Theme: pieces that burst out of {theme}. Draw only the pieces listed, never {theme} itself and never a character, a person, a face, a hand, writing or a logo.\n"
        f"Style: {p['style']}.\n"
        f"Outline: none. Draw every piece directly on the background with no outline, border, stroke, die-cut edge or halo around it.\n"
        f"Each cell holds exactly ONE piece, isolated and centred in its own cell, a different piece or a different size and angle from every other cell:\n{lines}\n"
        f"Consistency: the same drawing style, lighting and colour quality for all {n} pieces.\n"
        f"Background: a single, seamless, solid pure {screen} background covering the whole image in exactly one shade, with no tiled panels, no shading variations, no tint, gradient, vignette or glow, "
        f"no shadows, no floor, no noise, and no objects other than the pieces.\n"
        f"Spacing: every piece has a generous empty margin on every side (at least 20% of its own size), nothing touches the edge of its cell, and no piece touches or crosses another."
    )


def _pieces_prompt_v2(plan: dict, rows: int, cols: int) -> str:
    """Version 2: 'particles' for the model and the person, the layout only as positions, one seamless flat backdrop said once."""
    p = lint_plan(plan)
    theme = _theme(p["subject"])
    key = key_colour_for(p["elements"])
    cells = pieces_cells(p, rows, cols)
    pos = positions(rows, cols)
    n = rows * cols
    lines = "\n".join(f"Particle {c['pos']} ({pos[c['pos'] - 1]}): {c['label']}" for c in cells)
    where = f"one at each position: {_list(pos)}, evenly spaced" if n > 1 else "one, in the middle"
    return (
        f"One illustration of {n} separate small particles, {where}.\n"
        f"Particles of {theme}: small things that fly out when the {theme} emoji is pressed. Draw only the particles listed, never {theme} itself and never a character, a person, a face, a hand, writing or a logo.\n"
        f"Style: {p['style']}.\n"
        f"Outline: none. Draw every particle directly on the backdrop with no outline, stroke, die-cut edge or halo around it.\n"
        f"Each particle is alone at its position and centred there, and differs from every other one in kind, size or angle:\n{lines}\n"
        f"Consistency: the same drawing style, lighting and colour quality for all {n} particles.\n"
        f"Background: one seamless, flat, uniform pure {_screen_name(key)} backdrop from edge to edge in exactly one shade, with no texture, no shading, no tint, no gradient, no vignette, no glow, "
        f"no shadows, no noise, and nothing on it but the particles.\n"
        f"Spacing: every particle has a generous empty margin on every side (at least 20% of its own size), and no particle touches another."
    )


def pieces_prompt(plan: dict, rows: int = 2, cols: int = 2, version: int | None = None) -> str:
    """The sheet prompt for a rows x cols layout of DIFFERENT small particles of the effect (2x2 by default, 3x3 allowed), built from the linted plan only (template `effect_pieces`,
    current version 2). Each particle is drawn alone, centred at its position, on the engine's key colour with no outline: the sheet is cut into sprites of the simulated burst.
    Version 1 (the 'pieces' wording) stays buildable for stored plans."""
    _grid(rows, cols)
    version = PIECES_VERSION if version is None else int(version)
    if version not in PIECES_VERSIONS:
        raise ValueError(f"template {PIECES_TEMPLATE_ID} has no version {version}")
    return (_pieces_prompt_v1 if version == 1 else _pieces_prompt_v2)(plan, rows, cols)


def describe_pieces(plan: dict, rows: int = 2, cols: int = 2, version: int | None = None) -> dict:
    """What the page shows next to the price of the particles sheet: the screen colour, the places and the prompt that will be sent."""
    p = lint_plan(plan)
    v = PIECES_VERSION if version is None else int(version)
    return {"template": PIECES_TEMPLATE_ID, "version": v, "key": key_colour_for(p["elements"]), "grid": [rows, cols], "plan": p,
            "cells": pieces_cells(p, rows, cols), "prompt": pieces_prompt(p, rows, cols, version=v)}


# the same things under their new name (the wording the person and the model see is "particles"); the old names stay importable
PARTICLES_TEMPLATE_ID, PARTICLES_VERSION = PIECES_TEMPLATE_ID, PIECES_VERSION
particles_cells, particles_prompt, describe_particles = pieces_cells, pieces_prompt, describe_pieces
