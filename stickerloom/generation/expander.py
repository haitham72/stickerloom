"""Subject -> the full named set. The user asks for `{subject}`; the AI expands it into N distinct, animatable stickers and gives every one its key name,
tags and emoji. The AI only fills a small JSON; code lints it (and gets one repair round), then the SAVED TEMPLATE builds every prompt (prompter.render_plan),
so the prompt text is never free-written by the model. No key, no network, or a bad answer: the deterministic sets of `prompter.expand` are used and the
plan says so (`expanded_by`)."""
from __future__ import annotations

import json
import re

from . import prompter
from ..services import llm

GREEN_WORDS = {"green", "leaf", "leaves", "plant", "grass", "palm", "mint", "lime", "olive", "emerald", "cactus", "tree", "frog", "watermelon", "avocado", "broccoli"}
BANNED = {"text", "caption", "logo", "watermark", "flag", "transparent", "shadow"}

SYSTEM = """You plan sticker packs. You are given a short request (any language) and a grid size. Reply with ONE JSON object and nothing else:
{"subject_description": "<one line: the single character/subject, its look, identical in every cell>",
 "cells": [ {"label": "<the starting expression and pose, 6-14 words, English>", "motion": "<two distinct expressive actions joined by 'then', optionally a third introduced by 'finally', as one natural English sentence>", "key": "<snake_case action, 1-4 words, no subject>", "tags": ["<0-3 extra snake_case search words>"], "emoji": ["<1-2 emoji that fit the pose>"]} ]}
Rules: exactly N cells, all completely different; cover a WIDE range of emotions and reactions (for example joy, love, laughter, pride, doubt, sadness, anger, shock, fear, embarrassment, boredom, mischief, sleepiness), each exaggerated and readable at small size, and in a different state of action (standing, walking, running, jumping, sitting, lying down, leaning, reaching, spinning), so no two share a pose or a silhouette; every label animatable (a character that can move in place);
no text, captions, logos, flags or real people in any label; keep the user's subject and constraints, never add another character; a place, vehicle or prop the request names ("in a Lamborghini", "with a book", "on the beach") is part of subject_description and shows in the cells, never dropped; labels in English even when the request is Arabic or Arabizi.
Motion: give each cell its own little emotional progression with two meaningful actions and an optional third payoff, connected smoothly rather than a numbered list. Keep the sequence readable in a short clip, with anticipation and follow-through; a generic blink alone is not enough. Keep simultaneous details joined by 'while'. Describe only that cell, never choreography for the whole grid. For face-only emojis keep the image label face/head-only; video gestures may use a small detached hand that emerges from behind the face and withdraws afterwards, with no visible arm or connection to a body. Do not add a torso or attached limbs. Do not force a return to the starting frame in motion text: the user's Loop choice controls that separately.
""" + llm.DATA_RULE


def _lint(cells, n: int, subject_slug: str, request: str) -> list[str]:
    problems = []
    if not isinstance(cells, list) or len(cells) != n:
        return [f"need exactly {n} cells, got {len(cells) if isinstance(cells, list) else 'none'}"]
    labels, keys = set(), set()
    for i, c in enumerate(cells, 1):
        if not isinstance(c, dict):
            problems.append(f"cell {i} is not an object"); continue
        label = str(c.get("label", "")).strip()
        key = prompter.slug(str(c.get("key") or label))
        if not label:
            problems.append(f"cell {i} has no label")
        if label.lower() in labels:
            problems.append(f"cell {i} repeats the label '{label}'")
        labels.add(label.lower())
        if not key or key in keys:
            problems.append(f"cell {i} key '{key}' is empty or repeated")
        keys.add(key)
        if len(f"{subject_slug}_{key}") > 60:
            problems.append(f"cell {i} key is too long")
        if BANNED & set(re.findall(r"[a-z]+", label.lower())):
            problems.append(f"cell {i} label '{label}' uses a banned word ({', '.join(sorted(BANNED & set(re.findall(r'[a-z]+', label.lower()))))})")
        em = c.get("emoji")
        if not isinstance(em, list) or not [e for e in em if isinstance(e, str) and e.strip()]:
            problems.append(f"cell {i} needs at least one emoji")
        mo = c.get("motion", "")
        if not isinstance(mo, str):
            problems.append(f"cell {i} motion must be a sentence")
        elif BANNED & set(re.findall(r"[a-z]+", mo.lower())):
            problems.append(f"cell {i} motion uses a banned word")
        if not isinstance(c.get("tags", []), list):
            problems.append(f"cell {i} tags must be a list")
    return problems


def lint_slots(slots: dict, n: int, subject_slug: str, request: str) -> list[str]:
    """`_lint` for a finished plan's slots (cells as `prompter` stores them: tags[0] is `<subject>_<key>`, emoji is one string): the same rules the enhancer's answer passed,
    so a plan handed back by a client is judged exactly like one the model just wrote. No model is asked."""
    cells = []
    for c in slots.get("cells") or []:
        if not isinstance(c, dict):
            cells.append(c)
            continue
        tags = [t for t in (c.get("tags") or []) if isinstance(t, str)]
        key = tags[0] if tags else ""
        if key.startswith(subject_slug + "_"):
            key = key[len(subject_slug) + 1:]
        cells.append({"label": c.get("label", ""), "key": key, "tags": tags[1:], "motion": c.get("motion", ""),
                      "emoji": [c["emoji"]] if isinstance(c.get("emoji"), str) else c.get("emoji")})
    return _lint(cells, n, subject_slug, request)


def _parse(text: str):
    return llm.extract_json(text)


REVIEW_SYSTEM = """You review sticker slot JSON. You are given a request and a slot object with subject_description and cells (label, key, tags, emoji). Reply with ONE JSON object and nothing else:
{"ok": true/false, "problems": ["<short concrete problem>"]}.
Rules: cells must be distinct and animatable (a character that can move in place); subject_description must match the request without adding another character; every cell needs 1-2 fitting emoji. Say ok only when all hold.
""" + llm.DATA_RULE


def review(slots: dict, n: int, subject_slug: str, request: str, complete=None) -> dict:
    """The small slot reviewer (S5): code lint first, then a cheap second model answering {ok, problems[]}.
    The reviewer never edits the template; a failed review fails cleanly to the built-in sets."""
    cells = []
    for c in slots.get("cells") or []:
        tags = list(c.get("tags") or [])
        em = c.get("emoji")
        cells.append({"label": c.get("label", ""), "key": tags[0] if tags else prompter.slug(str(c.get("label", ""))),
                      "tags": tags[1:], "emoji": [em] if isinstance(em, str) and em.strip() else (em or [])})
    problems = _lint(cells, n, subject_slug, request)
    if not str(slots.get("subject_description", "")).strip():
        problems.append("subject_description is empty")
    if problems:
        return {"ok": False, "problems": problems, "model": None}
    complete = complete or llm.complete
    if complete is llm.complete and not llm.configured():
        return {"ok": True, "problems": [], "model": None}  # lint passed; no second model without a key
    try:
        text, meta = complete(REVIEW_SYSTEM, f"{llm.fence('REQUEST', request, 600)}\n{llm.fence('SLOTS', json.dumps(slots, ensure_ascii=False), 6000)}",
                              json_mode=True)
        ans = _parse(text)
        probs = [str(p) for p in ans.get("problems", []) if str(p).strip()]
        return {"ok": bool(ans.get("ok")) and not probs, "problems": probs, "model": meta.get("model")}
    except (llm.LLMError, ValueError) as e:
        return {"ok": True, "problems": [], "model": None, "review_error": str(e)[:200]}


def scene_missing(scene: str | None, ai: dict) -> bool:
    """A scene the request names ("in Lamborghini") that reaches no cell: not in subject_description (which starts every cell prompt) and in no label. Such a plan is re-planned, never shown."""
    words = [w for w in re.findall(r"[a-z]{3,}", str(scene or "").lower()) if w not in {"the", "and", "with"}]
    if not words:
        return False
    text = " ".join([str(ai.get("subject_description", ""))] + [str(c.get("label", "")) for c in (ai.get("cells") or []) if isinstance(c, dict)]).lower()
    return not any(w in text for w in words)


def expand(task: str, grid: tuple = (2, 2), *, use_ai: bool = False, complete=None, review_ai: bool = False) -> dict:
    """The plan for a typed request, in `prompter.expand`'s exact shape, plus `expanded_by` ('ai' | 'deterministic'), `expand_model`, `expand_error`.
    `complete(system, user) -> (text, meta)` is injectable for tests. `review_ai` runs the slot reviewer after the lint."""
    from . import spelling
    task = spelling.fix_text(task)                            # "camel in lamborgini" is planned (and shown) as "camel in Lamborghini"
    base = prompter.expand(task, grid)
    base["expanded_by"] = "deterministic"
    from .. import transformations  # "dog as banana": one new character, built by a versioned template (no model needed, so none is asked)
    transformed = transformations.plan(base, task, *grid)
    if transformed:
        return transformed
    if not use_ai:
        return base
    complete = complete or llm.complete
    if complete is llm.complete and not llm.configured():
        base["expand_error"] = f"No AI key (add {llm.KEY_VAR} to .env): using the built-in sets."
        return base
    rows, cols = grid
    n = rows * cols
    subject_slug = prompter.slug(" ".join(w for w in base["subject"].split() if w not in prompter.COLORS | prompter.STOP)) or "sticker"
    user = f"{llm.fence('REQUEST', task, 600)}\nGrid: {rows}x{cols}, N = {n} cells."
    if base['slots'].get('face'):
        user += "\nFace-only emoji mode: image labels describe only faces/heads; any video hand gesture must use a detached hand without an arm."
    problems, meta, ai = [], {}, None
    for attempt in range(2):                                  # one answer, one repair round, then the built-in sets
        try:
            text, meta = complete(SYSTEM, user if not problems else user + "\nYour last answer had these problems, fix them and answer again:\n- " + "\n- ".join(problems))
            ai = _parse(text)
            problems = _lint(ai.get("cells"), n, subject_slug, task)
            if not problems and not str(ai.get("subject_description", "")).strip():
                problems = ["subject_description is empty"]
            if not problems and scene_missing(base.get("scene"), ai):
                problems = [f"the request says \"{base['scene']}\": put it in subject_description (every cell is drawn from it)"]
        except (llm.LLMError, ValueError) as e:
            if isinstance(e, llm.LLMError):
                base["expand_error"] = str(e)[:240]
                return base
            problems = [f"not valid JSON: {e}"]
        if not problems:
            break
    if problems:
        base["expand_error"] = "The AI answer did not pass the checks (" + "; ".join(problems)[:200] + "): using the built-in sets."
        return base
    if review_ai:
        rv = review({"subject_description": str(ai.get("subject_description", "")), "cells": [
            {"label": c.get("label", ""), "tags": [prompter.slug(str(c.get("key") or c.get("label", "")))]
             + [str(t) for t in (c.get("tags") or [])],
             "emoji": "".join([e for e in (c.get("emoji") or []) if isinstance(e, str)])} for c in ai["cells"]]},
            n, subject_slug, task, complete=complete)
        base["review"] = {k: v for k, v in rv.items() if k != "review_error"}
        if not rv["ok"]:
            base["expand_error"] = "The slot reviewer rejected the answer (" + "; ".join(rv["problems"])[:200] + "): using the built-in sets."
            return base
    cells = []
    words = " ".join([task, ai["subject_description"]] + [str(c.get("label", "")) for c in ai["cells"]]).lower()
    for i, c in enumerate(ai["cells"], 1):
        key = f"{subject_slug}_{prompter.slug(str(c.get('key') or c['label']))}"
        extra = [prompter.tag(str(t)) for t in (c.get("tags") or [])][:3]
        emoji = "".join([e.strip() for e in c["emoji"] if isinstance(e, str) and e.strip()][:2])
        cell = {"pos": i, "label": str(c["label"]).strip(), "tags": prompter.clean_tags(key, extra), "emoji": emoji}
        if str(c.get("motion") or "").strip():
            cell["motion"] = str(c["motion"]).strip()[:600]
        cells.append(cell)
    slots = dict(base["slots"])
    slots.update(subject_description=str(ai["subject_description"]).strip(), cells=cells,
                 key_colour="blue" if GREEN_WORDS & set(re.findall(r"[a-z]+", words)) else "green")
    built = prompter.render_plan(slots, base["template_id"], base["template_version"])
    base.update(slots=slots, sheet_prompt=built["sheet_prompt"], video_prompt=built["video_prompt"], expanded_by="ai", expand_model=meta.get("model"))
    base.pop("expand_error", None)
    if complete is llm.complete:  # ledger only for real calls (fakes in tests never log)
        from . import model_calls as _mc
        _mc.append(None, "LLM_PLAN", str(meta.get("provider") or "openai"), str(meta.get("model") or llm.model()), status="OK",
                   latency_ms=meta.get("ms"), tokens_in=meta.get("tokens_in"), tokens_out=meta.get("tokens_out"),
                   extra={"task": task, "grid": f"{rows}x{cols}", "reviewed": review_ai,
                          "review_model": (base.get("review") or {}).get("model")})
    base["stickers"] = [{"index": c["pos"], "id": f"prompt{c['pos']:02d}", "prompt": built["prompts"][c["pos"]], "key": c["tags"][0], "tags": c["tags"], "emoji": c["emoji"]}
                        for c in cells]
    return base
