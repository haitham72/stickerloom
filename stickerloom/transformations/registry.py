"""The registry: which templates exist, which lexicon sharpens a target, and the one function the planner calls.

`plan(base, text, rows, cols)` takes the normal plan `prompter.expand` made for the request and, when the request is a transformation, returns it rebuilt from the template
(same JSON shape, plus `slots["transformation"]` = `{id, version, flavour, subject, target, required, forbidden}` which is stored with the generation). It returns None when the
request is not a transformation, or when the template cannot satisfy it (the caller then keeps the normal plan and says why)."""
from __future__ import annotations

import re

from ..generation import prompter
from . import banana
from .base import Match, Transformation, detect as _detect

TEMPLATES = {"subject_as_target": Transformation()}
FLAVOURS = {t: banana.FLAVOUR for t in banana.FLAVOUR["targets"]}          # target noun -> lexicon (add a module and a line here for the next one)


def get(template_id: str) -> Transformation:
    if template_id not in TEMPLATES:
        raise ValueError(f"unknown transformation template {template_id}")
    return TEMPLATES[template_id]


def flavour_for(target: str) -> dict | None:
    for w in re.findall(r"[a-z]+", target.lower()):
        if w in FLAVOURS:
            return FLAVOURS[w]
    return None


def signature() -> str:
    """Every template and lexicon version, for cache keys: a changed template must never be answered from a plan cached before it."""
    return ";".join([f"{k}:{t.version}" for k, t in sorted(TEMPLATES.items())] + sorted({f"{f['id']}:{f.get('version', 0)}" for f in FLAVOURS.values()}))


def detect(text: str) -> Match | None:
    return _detect(text)


def plan(base: dict, text: str, rows: int, cols: int) -> dict | None:
    m = _detect(text)
    if not m:
        return None
    tpl = TEMPLATES["subject_as_target"]
    slots = tpl.build_slots(m, rows, cols, flavour_for(m.target), seed=text)
    problems = tpl.validate(slots)
    if problems:
        base["expand_error"] = "The transformation template could not satisfy the request (" + "; ".join(problems)[:200] + "): using the normal plan."
        return None
    built = prompter.render_plan(slots, base["template_id"], base["template_version"])
    subject = f"{m.subject} as {m.target}"
    ctx = f"_{prompter.slug(m.context)}" if m.context else ""
    out = dict(base)
    out.update(subject=subject, context=m.context, kind="transformation", task_slug=prompter.slug(subject) + ctx, slots=slots,
               sheet_prompt=built["sheet_prompt"], video_prompt=built["video_prompt"], expanded_by="transformation",
               transformation=slots["transformation"],
               stickers=[{"index": c["pos"], "id": f"prompt{c['pos']:02d}", "prompt": built["prompts"][c["pos"]], "key": c["tags"][0], "tags": c["tags"], "emoji": c["emoji"]}
                         for c in slots["cells"]])
    out.pop("expand_error", None)
    return out
