"""The Inbox backend (checkpoint 1G): the manual loop until Phase 3 automates generation.

  Prepare -> the plan (template + slot JSON) and its final prompt, copyable for Higgsfield
  Reserve -> the next folder names (img-NNN-<subject>, vid-NNN-<subject>) and out/tasks/<NNN>.json (this is the Phase 2 `tasks` row, as JSON)
  Watch   -> every watch folder with its state; misnamed folders are flagged with the nearest valid name
  Run     -> a generation linked to its task, so the prompt that was used is stored with the result

The app NEVER writes inside the watch folders (inputs/Images_gen, videos_gen): the person creates the folder with the shown name."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from ..flow import sources
from . import expander, prompter

PROVIDER = "higgsfield-manual"
GRID_NAMES = {"3x3": (3, 3), "2x2": (2, 2), "1x1": (1, 1)}
LOOSE = re.compile(r"^(img|vid)[-_ ]*(\d+)[-_ ]*(.*)$", re.I)


def tasks_dir(out: Path) -> Path:
    return Path(out) / "tasks"


def _write(p: Path, obj: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    from ..runtime import atomic
    atomic.write_text(p, json.dumps(obj, indent=2, ensure_ascii=False))


def read_task(out: Path, number: str) -> dict:
    f = tasks_dir(out) / f"{int(number):03d}.json"
    if not f.is_file():
        raise sources_error(f"No task {number}", 404)
    return json.loads(f.read_text(encoding="utf-8"))


def sources_error(msg: str, code: int = 400):
    from ..flow.pipeline import PipelineError
    return PipelineError(msg, code)


def list_tasks(out: Path) -> list[dict]:
    d = tasks_dir(out)
    rows = [json.loads(f.read_text(encoding="utf-8")) for f in sorted(d.glob("*.json"))] if d.is_dir() else []
    return list(reversed(rows))


def watch_dirs(inp: Path) -> list[tuple[str, Path]]:
    """Every sub-folder of the two watch folders, as (kind dir, path). Names only; nothing is opened."""
    out = []
    for kind, sub in (("img", "Images_gen"), ("vid", "videos_gen")):
        base = Path(inp) / sub
        if base.is_dir():
            out += [(kind, d) for d in sorted(base.iterdir()) if d.is_dir()]
    return out


def next_number(out: Path, inp: Path) -> int:
    """One number for the whole project: the highest NNN used by any watch folder or task, plus one."""
    nums = [int(m[2]) for _, d in watch_dirs(inp) if (m := sources.DIR_RE.match(d.name))]
    nums += [t["number"] for t in list_tasks(out)]
    return max(nums, default=0) + 1


def nearest_valid(name: str, kind: str, number: int) -> str:
    """img-5-Teddy Bear -> img-005-teddy_bear ; 'Teddy Bear' -> img-<next>-teddy_bear (kind = the folder it sits in)."""
    m = LOOSE.match(name.strip())
    if m:
        kind, num, rest = m[1].lower(), int(m[2]), m[3]
    else:
        num, rest = number, name
    return f"{kind}-{num:03d}-{prompter.slug(rest) or 'subject'}"


ACTIONS_MODES = ("creative", "predefined")


def preview(prompt: str, grid: str | list | tuple = "2x2", style_id: str = "flat_vector", ai: bool = False, loop: bool = False,
            actions_mode: str = "creative") -> dict:
    """The plan for a typed task, for the Inbox to show before anything is reserved: template, slot JSON, final prompts.
    `actions_mode`: "creative" = the AI's (or the mood bank's) own nine stickers; "predefined" = the first nine of the complete bank
    actions (`actions.generation_bank`, the order "Next batch" continues in). Named grids retain their original cells,
    and a transformation writes its own cells, so for those the mode changes nothing."""
    g = GRID_NAMES.get(grid) if isinstance(grid, str) else tuple(grid)
    if actions_mode not in ACTIONS_MODES:
        raise sources_error("actions must be creative or predefined")
    if g not in prompter.GRIDS:
        raise sources_error("grid must be 3x3, 2x2 or 1x1")
    if not str(prompt).strip():
        raise sources_error("describe the subject first")
    if style_id not in prompter.STYLES:
        raise sources_error(f"unknown style '{style_id}'")
    if actions_mode == "predefined":
        from . import actions as bank
        words = set(re.findall(r"[a-z0-9]+", str(prompt).lower()))
        if not bank.preset_name(prompt) and expander.expand(str(prompt).strip(), g).get("expanded_by") != "transformation":
            plan = _token_plan(str(prompt).strip(), g, bool(words & prompter.EMOJI_WORDS), bank.next_tokens(set(), g[0] * g[1]), style_id, loop)
            plan["actions_mode"] = "predefined"
            plan["batches_max"] = batches_max(plan)
            return plan
    plan = expander.expand(str(prompt).strip(), g, use_ai=ai)       # ai: the model expands the subject and names every sticker; else the built-in sets
    plan["slots"]["style_id"] = style_id
    plan["slots"]["loop"] = bool(loop)               # Loop is a choice: off = no loop wording in the video prompt, no end image
    built = prompter.render_plan(plan["slots"], plan["template_id"], plan["template_version"])
    plan["sheet_prompt"], plan["video_prompt"] = built["sheet_prompt"], built["video_prompt"]
    for s in plan["stickers"]:
        s["prompt"] = built["prompts"][s["index"]]
    plan["batches_max"] = batches_max(plan)          # the Prompt step offers 1 .. this many batches in one Generate
    return plan


MAX_PLAN_BYTES = 64_000
PLAN_SOURCES = {"ai", "deterministic", "transformation"}
_CTRL = re.compile(r"[\x00-\x1f\x7f\u2028\u2029]")


def _text(value, what: str, limit: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise sources_error(f"The plan is not valid: {what} must be text")
    if _CTRL.search(value):
        raise sources_error(f"The plan is not valid: {what} has a line break or a control character")
    value = value.strip()
    if not value and not allow_empty:
        raise sources_error(f"The plan is not valid: {what} is empty")
    if len(value) > limit:
        raise sources_error(f"The plan is not valid: {what} is {len(value)} characters long; the limit is {limit}")
    return value


def plan_from_preview(raw, prompt: str, grid: str | list | tuple = "2x2", style_id: str = "flat_vector", loop: bool = False) -> dict:
    """The plan the person previewed (`preview`'s answer, sent back by the browser) as a plan this server can trust. The browser is untrusted: only the CELLS (label, tags, emoji, motion),
    the subject sentence and the key colour are taken from it, each checked for type, size and characters, then linted by the enhancer's own rules (`expander.lint_slots`); everything else
    (task, template, grid, style, loop, the prompts) is rebuilt here from the request and the saved template (`prompter.render_plan`), so a forged plan cannot carry its own instructions.
    A request that is a transformation is rebuilt without the cells (the built-in template writes the same plan every time). A plan that fails raises PipelineError 400 in words; no model is asked."""
    if not isinstance(raw, dict):
        raise sources_error("The plan is not valid: it must be the object that Generate prompt returned")
    if len(json.dumps(raw, ensure_ascii=False, default=str)) > MAX_PLAN_BYTES:
        raise sources_error(f"The plan is too large (the limit is {MAX_PLAN_BYTES // 1000} KB)")
    g = GRID_NAMES.get(grid) if isinstance(grid, str) else tuple(grid)
    if g not in prompter.GRIDS:
        raise sources_error("grid must be 3x3, 2x2 or 1x1")
    if style_id not in prompter.STYLES:
        raise sources_error(f"unknown style '{style_id}'")
    if not str(prompt).strip():
        raise sources_error("describe the subject first")
    base = expander.expand(str(prompt).strip(), g, use_ai=False)           # trusted: the request alone, no model
    slots_in = raw.get("slots")
    if not isinstance(slots_in, dict):
        raise sources_error("The plan is not valid: it has no slots (the cells of the stickers)")
    n = g[0] * g[1]
    cells_in = slots_in.get("cells")
    if not isinstance(cells_in, list) or len(cells_in) != n:
        raise sources_error(f"The plan is not valid: it needs exactly {n} cells, got {len(cells_in) if isinstance(cells_in, list) else 'none'}")
    tid = raw.get("template_id")
    if tid is not None and tid != base["template_id"]:
        raise sources_error(f"The plan is not valid: it is for the template {str(tid)[:40]!r}, this request uses {base['template_id']}")
    src = raw.get("expanded_by")
    if src is not None and src not in PLAN_SOURCES:
        raise sources_error("The plan is not valid: expanded_by must be ai, deterministic or transformation")
    desc = _text(slots_in.get("subject_description"), "the subject description", 800)
    key = slots_in.get("key_colour", "green")
    if key not in prompter.KEYS:
        raise sources_error("The plan is not valid: key_colour must be green or blue")
    cells = []
    for i, c in enumerate(cells_in, 1):
        if not isinstance(c, dict):
            raise sources_error(f"The plan is not valid: cell {i} is not an object")
        if c.get("pos") != i:
            raise sources_error(f"The plan is not valid: cell {i} must have pos {i}")
        tags_in = c.get("tags")
        if not isinstance(tags_in, list) or not 1 <= len(tags_in) <= 5 or not all(isinstance(t, str) for t in tags_in):
            raise sources_error(f"The plan is not valid: cell {i} needs 1 to 5 tags")
        tags = prompter.clean_tags(prompter.tag(_text(tags_in[0], f"cell {i}'s first tag", 80)), [_text(t, f"cell {i}'s tags", 60) for t in tags_in[1:]])
        if not tags[0]:
            raise sources_error(f"The plan is not valid: cell {i} has an empty first tag")
        if isinstance(c.get("emoji"), str) and re.search(r"[A-Za-z]", c["emoji"]):
            raise sources_error(f"The plan is not valid: cell {i}'s emoji must be emoji, not words")
        emoji = _text(c.get("emoji"), f"cell {i}'s emoji", 16)
        cell = {"pos": i, "label": _text(c.get("label"), f"cell {i}'s label", 200), "tags": tags, "emoji": emoji}
        if c.get("motion") not in (None, ""):
            cell["motion"] = _text(c["motion"], f"cell {i}'s motion", 220)
        cells.append(cell)
    subject_slug = prompter.slug(" ".join(w for w in base["subject"].split() if w not in prompter.COLORS | prompter.STOP)) or "sticker"
    problems = expander.lint_slots({"cells": cells}, n, subject_slug, str(prompt))
    if problems:
        raise sources_error("The plan did not pass the checks: " + "; ".join(problems)[:300])
    if base.get("expanded_by") != "transformation":      # a transformation is the built-in template's, rebuilt whole; any other plan keeps the cells that were shown
        base["slots"] = {**base["slots"], "subject_description": desc, "cells": cells, "key_colour": key}
        base["expanded_by"] = src or "deterministic"
        model = raw.get("expand_model")
        if base["expanded_by"] == "ai" and isinstance(model, str) and model.strip() and not _CTRL.search(model):
            base["expand_model"] = model.strip()[:80]
        base["stickers"] = [{"index": c["pos"], "id": f"prompt{c['pos']:02d}", "prompt": "", "key": c["tags"][0], "tags": c["tags"], "emoji": c["emoji"]} for c in cells]
    base["slots"]["style_id"] = style_id
    base["slots"]["loop"] = bool(loop)
    if "emoji_batch" in slots_in:
        from .emoji_batches import validate_metadata
        base["slots"]["emoji_batch"] = validate_metadata(slots_in["emoji_batch"], base["slots"]["cells"])
    base["previewed"] = True              # the cells came from a plan the person saw (kept on the task, so a batch can say where its cells came from)
    return plan_again(base, loop)


def subject_of(plan: dict) -> str:
    """The folder subject: the plan's subject words as snake_case (teddy yellow bear for school -> teddy_bear)."""
    return prompter.slug(" ".join(w for w in plan["subject"].split() if w not in prompter.COLORS | prompter.STOP)) or "sticker"


def plan_again(base: dict, loop: bool | None = None) -> dict:
    """A copy of a saved plan (a batch's prompts.json) for a new task: the same cells, tags and template; the prompts are rebuilt from them (a hand-written one stays)."""
    plan = json.loads(json.dumps(base))
    if not (plan.get("template_id") and plan.get("slots")):
        raise sources_error("That batch has no template plan to start from", 409)
    if loop is not None:
        plan["slots"]["loop"] = bool(loop)
    built = prompter.render_plan(plan["slots"], plan["template_id"], plan.get("template_version", prompter.TEMPLATE_VERSION))
    plan["sheet_prompt"], plan["video_prompt"] = built["sheet_prompt"], built["video_prompt"]
    for s in plan.get("stickers", []):
        s["prompt"] = built["prompts"].get(s["index"], s.get("prompt"))
    return plan


def reserve(out: Path, inp: Path, prompt: str, grid="2x2", style_id: str = "flat_vector", ai: bool = False, loop: bool = False,
            base_plan: dict | None = None, custom: dict | None = None, actions_mode: str = "creative") -> dict:
    """Save the task: this IS the G1 approval of the plan (recorded on the task and copied onto every generation run from it).
    `base_plan` starts from a batch's own saved plan (same cells and tags) instead of expanding the request again; `custom` is a prompt the user wrote by hand
    ({"sheet_prompt": text}), kept in `plan["custom"]` so it is what is sent, what the batch shows and what a rebuild does not undo."""
    plan = plan_again(base_plan, loop) if base_plan else preview(prompt, grid, style_id, ai, loop, actions_mode)
    if custom:
        plan["custom"] = {**(plan.get("custom") or {}), **custom}
    prompter.apply_custom(plan)
    n = next_number(out, inp)
    subj = subject_of(plan)
    img, vid = f"img-{n:03d}-{subj}", f"vid-{n:03d}-{subj}"
    now = round(time.time(), 3)
    task = {"id": f"{n:03d}", "number": n, "provider": PROVIDER, "external_task_id": img, "name_key": plan["task_slug"], "status": "reserved",
            "created": now, "prompt": str(prompt).strip(), "grid": plan["grid"], "style_id": style_id,
            "folders": {"img": img, "vid": vid},
            "paths": {"img": str(Path(inp) / "Images_gen" / img), "vid": str(Path(inp) / "videos_gen" / vid)},
            "request": {"template_id": plan["template_id"], "template_version": plan["template_version"], "slots": plan["slots"], "grid": plan["grid"]},
            "plan": plan, "plan_review": {"decision": "APPROVE", "by": "human", "ts": now, "note": "approved when the task was reserved"}, "generations": []}
    if plan["slots"].get("key_colour") == "blue":      # annotated only when it is blue: green is the default and is never written
        task["key_colour"] = "blue"
    _write(tasks_dir(out) / f"{n:03d}.json", task)
    return task


def annotate_key(out: Path, number: str, key: str, asked: str = "green") -> None:
    """The sheet came back with this screen colour. The task says `key_colour: blue` only when it is blue (`key_detected` when blue was not what the prompt asked for,
    i.e. the model drifted); a green screen leaves no mark, and a blue mark from a prompt that asked for blue is removed if the sheet came back green."""
    try:
        t = read_task(out, number)
    except Exception:
        return
    before = (t.get("key_colour"), t.get("key_detected"))
    if key == "blue":
        t["key_colour"] = "blue"
        if asked != "blue":
            t["key_detected"] = True
        else:
            t.pop("key_detected", None)
    else:
        t.pop("key_colour", None)
        t.pop("key_detected", None)
    if (t.get("key_colour"), t.get("key_detected")) != before:
        _write(tasks_dir(out) / f"{int(number):03d}.json", t)


def link_generation(out: Path, number: str, gid: int) -> None:
    t = read_task(out, number)
    t.setdefault("generations", []).append(f"G{gid:03d}")
    t["status"] = "running"
    _write(tasks_dir(out) / f"{int(number):03d}.json", t)


def _files(folder: Path, exts) -> list[str]:
    return sorted(f.name for f in folder.iterdir() if f.is_file() and f.suffix.lower() in exts) if folder.is_dir() else []


def inbox(out: Path, inp: Path) -> dict:
    """Every watch folder with its state (names and sizes only). Tasks first, then folders made outside the app."""
    inp = Path(inp)
    img_root, vid_root = inp / "Images_gen", inp / "videos_gen"
    picks = sources.scan(inp)
    by_id: dict = {}
    for subject, ps in picks.items():
        for p in ps:
            by_id.setdefault((subject, p.subject_id), []).append(p)
    nxt = next_number(out, inp)
    from ..flow.pipeline import generations_by_folder
    made = generations_by_folder(out)
    tasks = {t["id"]: t for t in list_tasks(out)}
    rows, seen = [], set()
    img_names = {d.name for k, d in watch_dirs(inp) if k == "img"}
    for t in sorted(tasks.values(), key=lambda t: -t["number"]):
        img, vid = t["folders"]["img"], t["folders"]["vid"]
        subj = img.split("-", 2)[2]
        seen |= {img, vid}
        ifiles, vfiles = _files(img_root / img, sources.IMG_EXT), _files(vid_root / vid, sources.VID_EXT)
        ps = by_id.get((subj, t["id"]), [])
        states = ["reserved, waiting for file"] if not ifiles else ["sheet arrived"]
        has_video = bool(vfiles) or any(p.has_video for p in ps)
        if ifiles:
            states.append("video arrived" if has_video else "no video yet")
        for p in ps:
            if p.clips_dup_of:
                states.append(f"clips: copies of {p.clips_dup_of} (ignored)")
                break
        rows.append({"kind": "task", "task": t["id"], "name": img, "vid_name": vid, "subject": subj, "prompt": t["prompt"], "grid": t["plan"]["grid"],
                     "states": states, "state": " · ".join(states),
                     "sheets": ifiles, "videos": vfiles, "paths": t["paths"], "generations": sorted(set(t.get("generations", [])) | set(made.get((subj, t["id"]), []))), "has_video": has_video,
                     "can_run": bool(ifiles), "problems": [], "ts": t["created"]})
    for kind, d in watch_dirs(inp):
        if d.name in seen:
            continue
        m = sources.DIR_RE.match(d.name)
        if m and m[1] == "vid" and kind == "vid" and any(n.startswith(f"img-{m[2]}-") for n in img_names):
            continue          # its video is already shown on the image folder's row
        if m and m[1] == kind:
            files = _files(d, sources.IMG_EXT if kind == "img" else sources.VID_EXT)
            ps = by_id.get((m[3], m[2]), [])
            has_video = any(p.has_video for p in ps) or (kind == "vid" and bool(files))
            states = ["sheet arrived" if kind == "img" and files else "video arrived" if files else "empty folder"]
            if kind == "img" and files:
                states.append("video arrived" if has_video else "no video yet")
                states.append("no matching task (made outside the app; prompts come from the stub or a <sheet>.json)")
            if any(p.clips_dup_of for p in ps):
                states.append(f"clips: copies of {next(p.clips_dup_of for p in ps if p.clips_dup_of)} (ignored)")
            vi = next((i + 1 for i, p in enumerate(picks.get(m[3], [])) if p.subject_id == m[2]), None)
            rows.append({"kind": "folder", "task": None, "name": d.name, "subject": m[3], "variant": vi, "prompt": m[3].replace("_", " "), "states": states, "state": " · ".join(x for x in states if not x.startswith("no matching") and "ignored" not in x), "sheets": files if kind == "img" else [],
                         "videos": files if kind == "vid" else [], "can_run": kind == "img" and bool(files), "problems": [], "paths": {kind: str(d)},
                         "generations": made.get((m[3], m[2]), []) if kind == "img" else [], "has_video": has_video, "ts": d.stat().st_mtime})
        else:
            fix = nearest_valid(d.name, kind, nxt)
            why = (f"is a {m[1]}- folder inside {'Images_gen' if kind == 'img' else 'videos_gen'}" if m else "does not match img-NNN-<subject> / vid-NNN-<subject>")
            rows.append({"kind": "invalid", "task": None, "name": d.name, "subject": None, "states": ["name invalid"], "state": "name invalid",
                         "sheets": [], "videos": [], "can_run": False, "paths": {kind: str(d)}, "generations": [], "ts": d.stat().st_mtime,
                         "problems": [{"what": why, "expected": "img-NNN-<subject> in Images_gen, vid-NNN-<subject> in videos_gen (NNN = 3 digits, subject = lowercase_snake)",
                                       "nearest": fix}]})
    return {"next_number": nxt, "paths": {"images": str(img_root), "videos": str(vid_root)}, "rows": rows,
            "subjects": sorted(picks), "grids": list(GRID_NAMES), "styles": list(prompter.STYLES)}


def pick_for_task(inp: Path, task: dict, take: int = 0):
    """The prepared sheet that belongs to a reserved task: its own folder, by folder number (never by filename guessing)."""
    img = task["folders"]["img"]
    subj, nnn = img.split("-", 2)[2], task["id"]
    ps = [p for p in sources.scan(inp).get(subj, []) if p.subject_id == nnn]
    if not ps:
        raise sources_error(f"The sheet has not arrived yet: put it in {task['paths']['img']}", 409)
    return ps[min(max(take, 0), len(ps) - 1)]


def next_batch(out: Path, gens, style_id: str = "flat_vector", loop: bool = False) -> dict:
    """"Next batch": the plan of the next UNCLAIMED batch of this session, as a prompt to read before anything is spent
    (the same shape as `preview`, plus `prompt` and `next`). The session is the batches the Studio shows (`gens`, any generation of each batch);
    every generation of their families counts as used. A preset emoji pack takes the next preset grid nobody used (core-v1 -> social-v1 ->
    reactions-v1 -> daily-v1); any other request takes the next bank actions none of them drew yet (bank order). When nothing is left the answer
    is {complete: true, message} in words, never a silent repeat."""
    from . import actions, spelling
    ses = session_state(out, gens)
    request, (rows, cols), existing = ses["request"], ses["grid"], ses["existing"]
    room = MAX_BATCHES - existing                     # one Generate never takes a session past MAX_BATCHES batches (the picker counts from the next batch)
    if ses["used_presets"]:
        free = [p for p in actions.PRESETS if p not in ses["used_presets"]]
        if not free:
            return {"complete": True, "message": f"All {len(actions.PRESETS)} preset grids of this pack are made ({9 * len(actions.PRESETS)} stickers). Start a new pack, or regenerate a batch you want better."}
        text = f"{request} {free[0]}"
        plan = preview(text, (rows, cols), style_id, False, loop)
        return {**plan, "prompt": text, "next": {"kind": "preset", "preset": free[0], "left": len(free) - 1},
                "existing": existing, "batches_max": max(1, min(len(free), room))}
    if expander.expand(spelling.fix_text(request), (rows, cols)).get("expanded_by") == "transformation":
        return {"complete": True, "message": "This request is one transformed character, so there are no other actions to draw: regenerate it for another take."}
    used = ses["used_tokens"]
    tokens = actions.next_tokens(used, rows * cols)
    if len(tokens) < rows * cols:
        return {"complete": True, "message": f"Every action of the {len(actions.generation_bank())}-action bank is already in these batches. Start a new request, or regenerate a batch you want better."}
    plan = _token_plan(request, (rows, cols), ses["face"], tokens, style_id, loop)
    left = len(actions.next_tokens(used | set(tokens), 999))
    return {**plan, "prompt": request, "next": {"kind": "actions", "tokens": tokens, "left": left},
            "existing": existing, "batches_max": max(1, min(1 + left // (rows * cols), room))}


def session_state(out: Path, gens) -> dict:
    """What a Studio session already holds (`gens`: any generation of each batch on screen; every generation of their families counts): its request, grid,
    the preset grids and bank actions it used, face mode, and how many BATCHES it has (families, not generations)."""
    from ..flow import groups, pipeline as pl
    from . import actions
    seen, results, roots = set(), [], set()
    for g in gens or []:
        try:
            gid = int(str(g).upper().lstrip("G"))
            roots.add(groups.root_of(out, gid))
            for m in groups.members(out, gid):
                if m not in seen:
                    seen.add(m)
                    results.append(pl.read_result(out, m))
        except Exception:
            continue
    if not results:
        raise sources_error("Open a batch first: Next batch continues the batches on screen", 409)
    first = min(results, key=lambda r: r.get("number") or 0)
    request = actions.strip_presets(str(first.get("prompt") or "").strip()) or str((first.get("source") or {}).get("subject", "")).replace("_", " ")
    used = actions.tokens_from_stickers([s for r in results for s in r["stickers"]])
    presets = {(r.get("slots") or {}).get("preset") for r in results} - {None}
    if presets:          # an emoji pack: its grids are the pack's, wherever its batches are (on screen or not): every batch of the same request and person counts
        key, owner = request.lower(), first.get("owner")
        for gid in pl.list_ids(out)[-300:]:
            try:
                r = pl.read_result(out, gid)
            except Exception:
                continue
            if r.get("owner") == owner and (r.get("slots") or {}).get("preset") and actions.strip_presets(str(r.get("prompt") or "")).lower() == key:
                presets.add(r["slots"]["preset"])
    return {"request": request, "grid": tuple((first.get("grid") or [3, 3])[:2]), "used_presets": presets,
            "used_tokens": used, "face": any((r.get("slots") or {}).get("face") for r in results),
            "existing": len(presets) if presets else len(roots)}          # an emoji pack counts its grids: batch k IS grid k (core 1-9, social 10-18, reactions 19-27, daily 28-36)


def _token_plan(request: str, grid: tuple, face: bool, tokens: list, style_id: str, loop: bool) -> dict:
    """The plan of one batch drawn from these bank actions (Next batch, and the batches after the first of a multi-batch Generate)."""
    from . import spelling
    plan = prompter.expand(spelling.fix_text(request), tuple(grid), face=face, tokens=tokens)
    plan["expanded_by"] = "deterministic"
    plan["slots"]["style_id"] = style_id
    plan["slots"]["loop"] = bool(loop)
    built = prompter.render_plan(plan["slots"], plan["template_id"], plan["template_version"])
    plan["sheet_prompt"], plan["video_prompt"] = built["sheet_prompt"], built["video_prompt"]
    for s in plan["stickers"]:
        s["prompt"] = built["prompts"][s["index"]]
    return plan


MAX_BATCHES = 4          # one Generate makes at most this many batches (a preset emoji pack has 4 grids = 36 actions)


def batches_max(plan: dict) -> int:
    """How many batches one Generate may make from this previewed plan, the first included: an emoji pack's preset grids from this one on, a bank request
    as many full grids as the bank still has (capped at MAX_BATCHES), a transformation (one character changed) or a 1x1 only itself."""
    from . import actions
    slots, grid = plan.get("slots") or {}, plan.get("grid") or [3, 3]
    if plan.get("expanded_by") == "transformation" or list(grid)[:2] == [1, 1]:
        return 1
    if slots.get("preset") in actions.PRESETS:
        return min(MAX_BATCHES, len(actions.PRESETS))
    used = actions.tokens_from_stickers(plan.get("stickers"))
    n = int(grid[0]) * int(grid[1])
    return max(1, min(MAX_BATCHES, 1 + len(actions.next_tokens(used, 999)) // n))


def more_batches(request: str, plan: dict, k: int, style_id: str = "flat_vector", loop: bool = False, used_presets=(), used_tokens=()) -> list[dict]:
    """The `k` batches that follow a previewed plan in ONE Generate: an emoji pack's next preset
    grids (core-v1 -> social-v1 -> reactions-v1 -> daily-v1, the one shown skipped), any other request the next unused bank actions, 9 at a time. Each
    comes back with its own `prompt`; nothing is reserved or spent here (each sheet is priced and started on its own)."""
    from . import actions
    k = max(0, min(int(k), MAX_BATCHES - 1, batches_max(plan) - 1))
    if not k:
        return []
    request = str(request or "").strip()
    slots, grid = plan.get("slots") or {}, tuple((plan.get("grid") or [3, 3])[:2])
    if slots.get("preset") in actions.PRESETS:
        base = actions.strip_presets(request) or request
        out = []
        for p in [x for x in actions.PRESETS if x != slots["preset"] and x not in set(used_presets)][:k]:      # a session's own grids are never made twice
            text = f"{base} {p}"
            out.append({**preview(text, grid, style_id, False, loop), "prompt": text})
        return out
    used = set(used_tokens) | actions.tokens_from_stickers(plan.get("stickers"))
    out = []
    for _ in range(k):
        tokens = actions.next_tokens(used, grid[0] * grid[1])
        if len(tokens) < grid[0] * grid[1]:
            break
        used |= set(tokens)
        out.append({**_token_plan(request, grid, bool(slots.get("face")), tokens, style_id, loop), "prompt": request})
    return out
