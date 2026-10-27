"""How far a NEW chat request goes: the chat's stage, one of five, picked on the chat bar's slider.

    prompt    -> "Prompt": the plan only (cells, tags, the sheet prompt) and Generate / Edit: nothing is spent, no job starts
    emojis    -> "Stickers": the sheet and the cut stickers, stopping at G2 for the person (the default, today's turn)
    animation -> "Animation": stickers + the video: the agentic creator with scope video, ENDING after "approve the animations" (G4): no pack, no Telegram
    export    -> "Telegram": the creator run to the Library pack and the Telegram send (D1). The id stays `export` so saved chats keep their meaning

The stage is `settings.stage` of the chat session. It maps onto the creator (agent/creator.py); it never forks it. Animation and export stop at G2 and G4
for one click unless the creator's bypass ("Approve everything for me", `settings.creator.bypass`) is on (D2). A session written before the stage existed
keeps working: `creator.on` with `scope: video` reads as export, anything else as emojis. Pure: no I/O, no model, no tools."""
from __future__ import annotations

STAGES = ("auto", "prompt", "emojis", "animation", "export")
STEPS = STAGES[1:]          # the fixed stops, in order; "auto" lets each message decide
DEFAULT = "auto"
MAX_BATCHES = 4          # = generation/tasks.MAX_BATCHES: one automatic request has at most four batches; manual additions can continue
INFO = {"auto": {"label": "Auto", "hint": "each message decides"},
        "prompt": {"label": "Prompt", "hint": "plan only, free"},
        "emojis": {"label": "Stickers", "hint": "sheet and stickers"},
        "animation": {"label": "Animation", "hint": "+ animation"},
        "export": {"label": "Telegram", "hint": "+ pack and Telegram"}}


def of(settings: dict | None) -> str:
    """The stage of a session's settings; an old session's creator switches migrate on read (on + video -> export)."""
    st = settings or {}
    if st.get("stage") in STAGES:
        return st["stage"]
    c = st.get("creator") or {}
    return "export" if c.get("on") and c.get("scope") == "video" else DEFAULT


def valid(stage) -> bool:
    return stage in STAGES


import re as _re

# what a message asks for: a step it needs ("animate it" needs Animation), and whether it says "only" that step ("just the stickers")
_NEEDS = (("export", r"\b(?:telegram|send (?:it|them|the pack)|pack and send)\b"),
          ("animation", r"\b(?:animat\w*|make (?:it|them) move|moving|motion|video)\b"),
          ("prompt", r"\b(?:prompt only|just the (?:prompt|plan)|only the (?:prompt|plan)|plan only|don'?t make|no sheet)\b"))
_ONLY = r"\b(?:only|just|without (?:animat\w*|video|motion)|no (?:animation|video|motion)|don'?t animate)\b"


def need_of(text: str | None) -> tuple[str | None, bool]:
    """(the step a message asks for or None, True when it says that step and NO further: "just stickers", "stickers only, no animation").
    The furthest step named wins ("make stickers and animate them" -> animation). Pure: words only."""
    low = str(text or "").lower()
    for step, rx in _NEEDS:
        if _re.search(rx, low):
            if step == "animation" and _re.search(r"\b(?:without|no|don'?t) (?:animat\w*|video|motion)\b", low):
                continue
            return step, step == "prompt"
    if _re.search(_ONLY, low) and _re.search(r"\bstickers?\b|\bemojis?\b|\bsheet\b", low):
        return "emojis", True
    return None, False


def resolve(stage: str, text: str | None) -> tuple[str, dict | None]:
    """(the step this request runs to, a mismatch or None). Auto: the message decides (nothing named = Stickers). A fixed stop is a boundary both ways:
    a message that needs a step past it -> {"kind": "more"}, a message that says "only" an earlier step -> {"kind": "less"}; the caller asks the person
    (buttons that set the slider and go). Pure."""
    stage = stage if stage in STAGES else DEFAULT
    need, only = need_of(text)
    if stage == "auto":
        return need or "emojis", None
    if need and STEPS.index(need) > STEPS.index(stage):
        return stage, {"kind": "more", "need": need, "stage": stage}
    if need and only and STEPS.index(need) < STEPS.index(stage):
        return stage, {"kind": "less", "need": need, "stage": stage}
    return stage, None


def at_least(stage: str, step: str) -> bool:
    """May an explicit action of `step` run at this stage? Auto allows everything; a fixed stop allows its own step and the ones before it."""
    stage = stage if stage in STAGES else DEFAULT
    return stage == "auto" or STEPS.index(step) <= STEPS.index(stage)


def reached(gen: dict | None) -> str | None:
    """The furthest step a batch already has: "animation" (an animation is ready), "emojis" (a sticker is ready), "prompt" (only a plan), or None."""
    if not gen:
        return None
    sts = gen.get("stickers") or []
    if any(s.get("anim_status") == "READY" for s in sts):
        return "animation"
    if any(s.get("status") == "READY" for s in sts):
        return "emojis"
    return "prompt"


def run_spec(stage: str, bypass: bool = False) -> dict:
    """What a new request does at this stage: {plan_only, creator, scope, end, bypass}. `end` is where a creator run stops: 'animation' (after G4) or
    'export' (after the Telegram send); None when the creator is not used."""
    stage = stage if stage in STAGES else DEFAULT
    stage = "emojis" if stage == "auto" else stage              # Auto's own step is decided per message (resolve); unresolved it is Stickers
    creator = stage in ("animation", "export")
    return {"stage": stage, "plan_only": stage == "prompt", "creator": creator, "scope": "video" if creator else "images",
            "end": stage if creator else None, "bypass": bool(bypass) if creator else False}


def animates(stage: str) -> bool:
    """The stage's price includes the animation (the plan card shows sheet + animation)."""
    return stage in ("animation", "export")


def batch_label(no: int, plan: dict | None = None) -> str:
    """"Batch 02 · social" / "Batch 01 · actions 1-9": what the cards call one batch of a pack. An emoji pack's batch is a preset grid
    (core-v1 -> core); any other request's batch k is bank actions 9(k-1)+1 .. 9k."""
    p = ((plan or {}).get("slots") or {}).get("preset")
    if p:
        return f"Batch {no:02d} · {str(p).split('-')[0]}"
    n = 9
    grid = (plan or {}).get("grid")
    if isinstance(grid, (list, tuple)) and len(grid) >= 2:
        n = int(grid[0]) * int(grid[1])
    elif isinstance(grid, str) and "x" in grid:
        a, b = grid.split("x")[:2]
        n = int(a) * int(b)
    return f"Batch {no:02d} · actions {n * (no - 1) + 1}-{n * no}"
