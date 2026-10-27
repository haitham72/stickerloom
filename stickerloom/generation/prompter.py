"""Stub of the AI prompter agent (Phase 1 = deterministic, plain JSON, no LLM/pydantic/langgraph).
Expands one user task into one prompt per grid cell (2x2 default, 3x3, 1x1). Phase 3 swaps this for a real agent with the SAME JSON shape."""
from __future__ import annotations

import re
from pathlib import Path

from . import actions, emotions, spelling, styles, video_sequences

TEMPLATES = Path(__file__).resolve().parent.parent / "prompts" / "templates"
MARGIN = ("full body, centred, generous empty margin on every side (at least 20% of the cell), "
          "nothing touching or crossing the cell edge")
MARGIN_FACE = ("face and head alone, large and centred, generous empty margin on every side (at least 20% of the cell), "
               "nothing touching or crossing the cell edge")
EMOJI_WORDS = {"emoji", "emojis", "emoticon", "emoticons", "smiley", "smileys"}
STYLES = dict(styles.PHRASE)           # id -> phrase (v2 templates); styles.phrase() gives the v1 wording for plans saved with v1
TEMPLATE_VERSION = 3                   # new plans use v3; a saved plan keeps its own version
KEYS = {"green": ("green", "#00FF00"), "blue": ("blue", "#0000FF")}
TAG_RE = re.compile(r"[^a-z0-9]+")
GUIDELINES = {
    "sheet": "{rows}x{cols} sticker sheet, {n} separate characters, equal cells, wide gaps between characters, no character touches its cell edge",
    "style": "flat vector sticker illustration, bold clean shapes, vibrant colors, friendly proportions",
    "background": "flat pure green (#00FF00) background, no shadows, no floor, no gradients",
    "consistency": "same character design, proportions and colors in all {n} cells",
    "motion": "each character moves in place, no interaction between cells, static camera, seamless loop",
}

# (key suffix, phrase, emoji) x 9 per context. Row-major cell order.
ACTIONS = {
    "school": [
        ("with_a_book", "hugging a thick book to the chest with a proud smile", "📚", "flips the book open and shut, bobs happily"),
        ("raising_hand", "raising a hand eagerly, bouncing, mouth open to answer", "✋", "arm shoots up, bounces on tiptoes, waves for attention"),
        ("with_a_backpack", "wearing an oversized backpack with a first-day grin", "🎒", "bounces, the straps jiggle, the backpack swings"),
        ("writing", "writing in a notebook, tongue out in concentration", "📝", "pencil scribbles, head follows the line, tongue wiggles"),
        ("with_a_pencil", "holding a giant pencil like a sword, determined face", "✏️", "twirls the pencil and strikes a determined pose"),
        ("thinking", "thinking hard, chin in hand, one eyebrow raised", "🤔", "taps the chin, eyes look up, slow nod"),
        ("with_a_star", "proudly holding up a shiny gold star, beaming", "⭐", "the star sparkles, held high, hops with pride"),
        ("sleeping_at_desk", "dozing off at a desk, head drooping, drool bubble", "😴", "head nods down and jerks up, the bubble inflates"),
        ("waving_goodbye", "waving goodbye with a big grin, backpack on", "👋", "waves in a wide arc, bounces, blows a kiss"),
    ],
    "birthday": [
        ("with_a_cake", "carrying a birthday cake with lit candles, delighted face", "🎂", "candles flicker, the cake wobbles, sways happily"),
        ("with_a_balloon", "holding a bunch of floating balloons, giddy", "🎈", "balloons bob, the feet lift slightly off the ground"),
        ("with_a_gift", "hugging a big gift box with a bow, bursting with joy", "🎁", "shakes the box, the ribbon bounces, hops with excitement"),
        ("party_hat", "wearing a party hat, cheering with sparkling eyes", "🥳", "the hat wobbles, cheers and claps"),
        ("blowing_a_horn", "blowing a party horn, cheeks puffed", "🎉", "the horn unrolls and rolls back, cheeks puff"),
        ("making_a_wish", "eyes closed making a wish, hands clasped, glowing smile", "🌟", "sways gently, sparkles pulse, eyes open with a gasp"),
        ("laughing", "laughing out loud with tears of joy", "😂", "shoulders shake, doubles over laughing"),
        ("with_confetti", "arms up in a shower of confetti, ecstatic", "🎊", "confetti falls, jumps and spins"),
        ("waving", "waving hello in a party hat", "👋", "waves wildly and bounces"),
    ],
    "default": None,        # built from emotions.pick(): a wide, expressive mix per request
}
VERBS = {"create", "make", "generate", "draw", "design", "build", "give", "me"}
STOP = {"a", "an", "the", "of", "and", "to", "my", "some"}
COLORS = {"yellow", "red", "blue", "green", "pink", "purple", "orange", "brown", "black", "white", "gray", "grey",
          "golden", "cute", "little", "big", "small", "happy"}
SPLIT = re.compile(r"\b(for|in|at|during|with|on)\b")
SCENE_WORDS = {"in", "at", "with", "on"}               # what follows is drawn (a place, a vehicle, a prop); "for" / "during" name a theme


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


REFERENCE_CLAUSE = ("Reference: the attached image shows the subject to draw. Keep its design, colours and proportions for every character; "
                    "change only the expression and the pose.")
GRIDS = {(3, 3), (2, 2), (1, 1)}     # 2x2 default; explicit 3x3; 1x1 = regenerate one sticker
TEMPLATE_OF = {(3, 3): "sheet_3x3", (2, 2): "sheet_2x2", (1, 1): "single_1x1"}


def tag(text: str) -> str:
    return TAG_RE.sub("_", str(text).lower()).strip("_")


def clean_tags(key: str, tags=None) -> list:
    """1-5 unique [a-z0-9_]+ tags; the first one is the key (the file-name tail), the rest are search terms."""
    out = [key]
    for t in tags or []:
        t = tag(t)
        if t and t not in out:
            out.append(t)
    return out[:5]


def load_template(template_id: str, version: int) -> str:
    f = TEMPLATES / f"{template_id}_v{int(version)}.txt"
    if not f.is_file():
        raise ValueError(f"unknown prompt template {template_id} v{version}")
    return f.read_text(encoding="utf-8")


def cell_prompt(slots: dict, cell: dict) -> str:
    """The prompt of one cell: subject + pose + the margin clause that keeps the character off the cell edge
    (the face clause in face mode: emoji-style packs draw faces, never bodies)."""
    margin = MARGIN_FACE if slots.get("face") else MARGIN
    return f"{slots['subject_description']} {cell['label']}, {margin}"


def render_plan(slots: dict, template_id: str, version: int = TEMPLATE_VERSION) -> dict:
    """Rebuild the final prompts from the saved template file + the slot JSON. Deterministic, so a plan is never only free text:
    Phase 3's LLM fills `slots`, this function turns them into prompts."""
    rows, cols = TEMPLATE_GRID[template_id]
    key_name, key_hex = KEYS[slots.get("key_colour", "green")]
    cells = sorted(slots["cells"], key=lambda c: c["pos"])
    lab = (lambda c: f"Character {c['pos']}: {c['label']}") if int(version) >= 2 else (lambda c: f"{c['pos']}. {c['label']}")   # v2 avoids list formatting (image models read it as tiled panels)
    lines = "\n".join(lab(c) for c in cells) if len(cells) > 1 else cells[0]["label"]
    motions = "\n".join(f"{c['pos']}. {c.get('motion') or c['label'] + ', with big lively expressive movement in place'}" for c in cells)
    common = dict(rows=rows, cols=cols, n=rows * cols, subject_description=slots["subject_description"], style=styles.phrase(slots.get("style_id"), version),
                  cells=lines, key_name=key_name, key_hex=key_hex, motions=motions,
                  clip="seamless loop" if slots.get("loop") else "clip",           # v2 video: the word "loop" is only there when the user chose Loop (the engine closes loops itself)
                  ending="End on the starting pose so the clip loops." if slots.get("loop") else "")
    sheet = load_template(template_id, version).format(**common).strip()
    face = bool(slots.get("face"))
    video_template = slots.get("video_template") or {"id": "video_short", "version": 1}
    narrative = video_template["id"] == "video_independent" and int(video_template["version"]) >= 2
    sequences = (video_sequences.narratives(cells, rows, cols, face, bool(slots.get("loop"))) if narrative
                 else local_sequences(cells, rows, cols, face))
    faces = video_sequences.face_policy(cells) if narrative and face else " Faces only: no bodies, no hands." if face else ""
    video = load_template(video_template["id"], video_template["version"]).format(seconds=3, units="emojis" if face else "characters", unit="emoji" if face else "character",
                                                  expressive=expressive(face), faces=faces,
                                                  **{**common, "motions": "\n".join(f"{c['pos']}. {c.get('motion') or c['label']}" for c in cells),
                                                     "local_sequences": sequences}).strip()
    video = re.sub(r"\n{2,}", "\n", video)
    return {"sheet_prompt": sheet, "video_prompt": video, "prompts": {c["pos"]: cell_prompt(slots, c) for c in cells}}


CUSTOM_PROMPTS = ("sheet_prompt", "video_prompt")
VIDEO_TEMPLATE = ("video_independent", 2)  # saved per new plan; earlier video templates keep their exact wording
EXPRESSIVE = "Create a very expressive animation of each {unit}, each having its own personality with a wide range of emotions."     # in every video prompt


def expressive(face: bool) -> str:
    return EXPRESSIVE.format(unit="emoji" if face else "character")


def local_sequences(cells: list, rows: int, cols: int, face: bool) -> str:
    """Bind state and two actions to a spatial cell, never a sheet-wide storyboard."""
    vertical = {1: ("Centre",), 2: ("Top", "Bottom"), 3: ("Top", "Middle", "Bottom")}[rows]
    horizontal = {1: ("centre",), 2: ("left", "right"), 3: ("left", "centre", "right")}[cols]
    lines = []
    for cell in cells:
        r, c = divmod(cell['pos'] - 1, cols)
        actions = [p.strip(' .') for p in re.split(r",|\bthen\b|\bwhile\b", str(cell.get('motion') or ''), flags=re.I) if p.strip(' .')]
        if not actions:
            actions = ["animates its stated expression clearly in place"]
        if len(actions) < 2:
            actions.append("tilts its head slightly and blinks" if face else "leans slightly and settles in place")
        lines.append(f"{vertical[r]}-{horizontal[c]} (cell {cell['pos']}): State: {cell['label']}. Action 1: {actions[0]}. Then Action 2: {actions[1]}.")
    return '\n'.join(lines)


def with_expressive(prompt: str, face: bool = False) -> str:
    """A video prompt the person wrote keeps their text and gets the expressive line once, at the end, when it does not say it already."""
    p = str(prompt or "").strip()
    return p if "very expressive animation of each" in p.lower() else (p + "\n" + expressive(face)).strip()


MAX_PROMPT = 6000


def apply_custom(plan: dict) -> dict:
    """A prompt the user wrote by hand (Studio, Prompt tab) is kept in `plan["custom"]` and wins over the template's text every time the plan's prompts are rebuilt:
    the slot JSON and the template stay as they were (the cells, tags and emoji are unchanged), only the final text sent to the model is the user's."""
    custom = {k: v.strip() for k, v in (plan.get("custom") or {}).items() if k in CUSTOM_PROMPTS and isinstance(v, str) and v.strip()}
    if custom:
        plan.update(custom)
        plan["custom"] = custom
    else:
        plan.pop("custom", None)
    return plan


def clean_custom(text, what: str) -> str | None:
    """The text of a hand-edited prompt: None when it was not sent, ValueError when it is empty or absurdly long."""
    if text is None:
        return None
    if not isinstance(text, str):
        raise ValueError(f"the {what} must be text")
    text = text.strip()
    if not text:
        raise ValueError(f"the {what} is empty: write it, or press Reset to get the template's text back")
    if len(text) > MAX_PROMPT:
        raise ValueError(f"the {what} is {len(text)} characters long; the limit is {MAX_PROMPT}")
    return text


TEMPLATE_GRID = {"sheet_3x3": (3, 3), "sheet_2x2": (2, 2), "single_1x1": (1, 1)}


def expand(task: str, grid: tuple = (2, 2), face: bool | None = None, preset: str | None = None, tokens: list | None = None) -> dict:
    """`face` (emoji-style: faces only, never bodies) auto-detects from the request (`generic emojis`, `an emoji pack`,
    ...) and stays on the plan, so recuts and rebuilds keep it. `preset` (a saved nine-slot grid from the canonical
    bank) names the nine directly; a face-mode sheet without one takes `core-v1`. `tokens` (explicit bank tokens, "Next batch" on a
    non-preset request) names the cells from the bank's face or whole-character sentences. Template v4 carries the face
    wording; v1-v3 are untouched."""
    rows, cols = grid
    words = re.findall(r"[a-z0-9]+", task.lower())
    if face is None:
        face = bool(set(words) & EMOJI_WORDS)
    if preset is None:
        preset = actions.preset_name(task)
    if preset:
        words = [w for w in words if w not in {"core", "social", "reactions", "daily", "v1"}]
        face = True                    # a preset grid is nine bank faces; its sentences are face-only
    body = " ".join(w for w in words if w not in VERBS or words.index(w) > 2)
    parts = SPLIT.split(body, maxsplit=1)
    subject = spelling.fix_text(parts[0].strip()) or "sticker"
    context = spelling.fix_text(parts[2].strip()) if len(parts) > 2 else ""
    scene = f"{parts[1]} {context}" if len(parts) > 2 and parts[1] in SCENE_WORDS and context else ""
    described = re.sub(r"^(?:a|an|the|some) ", "", f"{subject} {scene}".strip())     # "camel in Lamborghini": a setting / prop / vehicle is part of what is drawn, never dropped (a theme, "for school", picks the actions instead)
    subject_slug = slug(" ".join(w for w in subject.split() if w not in COLORS | STOP)) or "sticker"
    ctx_words = [w for w in context.lower().split() if w not in STOP]
    ctx_slug = slug(" ".join(ctx_words))
    kind = next((w for w in ctx_words + words if w in ACTIONS), "default")
    task_slug = subject_slug + (f"_{ctx_slug}" if ctx_slug else "")
    ctx_tags = [w for w in ctx_words if len(w) > 2][:1]
    cells, stickers = [], []
    if preset is None and face and grid == (3, 3) and not tokens:
        preset = "core-v1"                    # an emoji pack with no grid named starts at the first grid; `generate more` claims the next
    if tokens:
        entries = actions.token_cells(list(tokens)[: rows * cols], bool(face))
    elif preset:
        entries = [(tok, *rest) for tok, *rest in actions.preset_cells(preset, rows * cols)]
    elif face or not ACTIONS[kind]:
        entries = emotions.pick(rows * cols, subject_slug, face=face)      # face mode always draws from the mood bank: a context bank's props need hands
    else:
        entries = ACTIONS[kind][: rows * cols]
    for i, (suffix, phrase, emoji, motion) in enumerate(entries, 1):
        motion = video_sequences.default_motion(motion, bool(face))
        key = f"{subject_slug}_{suffix}"                  # searchable action name; also the file name tail
        if preset or tokens:
            seed = [suffix, *actions.generation_bank()[suffix]["aliases"]] + ctx_tags
        else:
            seed = [w for w in re.findall(r"[a-z0-9]+", phrase) if len(w) > 2 and w not in STOP and w not in {"with", "holding", "wearing"}][:3] + ctx_tags
        tags = clean_tags(key, seed)
        cells.append({"pos": i, "label": phrase, "tags": tags, "emoji": emoji, "motion": motion})      # every cell prompt starts with the subject description, scene included
    slots = {"subject_description": described, "style_id": "flat_vector", "mode": TEMPLATE_OF[(rows, cols)], "cells": cells,
             "action_guidance": kind, "key_colour": "green", "face": bool(face), "preset": preset,
             "video_template": {"id": VIDEO_TEMPLATE[0], "version": VIDEO_TEMPLATE[1]}}
    tid = TEMPLATE_OF[(rows, cols)]
    version = 4 if face else TEMPLATE_VERSION
    built = render_plan(slots, tid, version)
    for c in cells:
        stickers.append({"index": c["pos"], "id": f"prompt{c['pos']:02d}", "prompt": built["prompts"][c["pos"]], "key": c["tags"][0],
                         "tags": c["tags"], "emoji": c["emoji"]})
    g = {k: v.format(rows=rows, cols=cols, n=rows * cols) for k, v in GUIDELINES.items()}
    return {
        "task": body, "task_slug": task_slug, "subject": subject, "scene": scene, "described": described, "context": context, "kind": kind, "grid": [rows, cols],
        "template_id": tid, "template_version": version, "slots": slots,
        "guidelines": g,
        "sheet_prompt": built["sheet_prompt"],
        "video_prompt": built["video_prompt"],
        "stickers": stickers,
    }


def validate_plan(plan: dict) -> dict:
    """A hand-written prompts file (next to a sheet) replaces the stub. Same shape as expand(); raises ValueError.
    `grid` [rows, cols] is optional: 9 stickers = 3x3, 4 = 2x2, 1 = 1x1."""
    st = plan.get("stickers")
    if not isinstance(st, list) or not st:
        raise ValueError("stickers must be a non-empty list")
    n = len(st)
    grid = tuple(plan.get("grid") or {9: (3, 3), 4: (2, 2), 1: (1, 1)}.get(n, (0, 0)))
    if grid not in GRIDS or grid[0] * grid[1] != n:
        raise ValueError(f"{n} stickers do not fill a supported grid (3x3, 2x2, 1x1)")
    plan["grid"] = list(grid)
    if sorted(s.get("index") for s in st) != list(range(1, n + 1)):
        raise ValueError(f"stickers must have index 1..{n} exactly once")
    for s in st:
        for k in ("prompt", "key", "emoji"):
            if not s.get(k):
                raise ValueError(f"sticker {s.get('index')} is missing '{k}'")
        s["key"] = slug(s["key"])
        s["tags"] = clean_tags(s["key"], s.get("tags"))      # a hand-written file without tags is valid: tags = [key]
    plan["stickers"] = sorted(st, key=lambda s: s["index"])
    plan["task_slug"] = slug(plan.get("task_slug") or plan.get("task") or "task")
    plan.setdefault("task", plan["task_slug"])
    plan.setdefault("sheet_prompt", ""); plan.setdefault("video_prompt", "")
    if plan.get("template_id") and plan.get("slots"):        # the final prompts are rebuilt from the template file, never trusted as free text
        built = render_plan(plan["slots"], plan["template_id"], plan.get("template_version", 1))
        plan["sheet_prompt"], plan["video_prompt"] = built["sheet_prompt"], built["video_prompt"]
    return apply_custom(plan)
