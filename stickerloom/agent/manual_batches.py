"""Manual emoji batches: persisted choices, using the generation engine's duplicate rules."""
from __future__ import annotations

import re

from ..generation import emoji_batches as batches
from ..flow.pipeline import PipelineError
from . import creator, stages
from .tools import ToolError


def matches(turn) -> bool:
    action = (turn.action or {}).get("type", "")
    if action.startswith("emoji_batch_"):
        return True
    glyphs, words = batches.extract(turn.text)
    pending = turn.sess.get("pending") or {}
    if pending.get("type") == "emoji_batch":
        return bool(glyphs) or words.lower() in ("overwrite", "replace", "add", "add 02", "skip")
    return len(glyphs) >= 2 and (len(glyphs) >= 8 or not words or re.search(r"\b(?:batch|emojis?)\b", words, re.I))


def used_in_context(agent, turn, pending, packs) -> list[str]:
    pid = pending.get("pack_id")
    pack = next((p for p in packs if p["id"] == pid), None)
    if pid and not pack:
        raise ToolError("The target pack is no longer available. Paste the batch again and choose another pack.", 404)
    stickers = list((pack or {}).get("stickers") or [])
    for row in pending.get("previous", []):
        gid = agent._row_gen(row)
        plan = row.get("plan")
        if not plan and gid:
            try:
                plan = agent.tools.generation_plan(gid)
            except ToolError:
                plan = agent.tools.generation(gid)
        for sticker in (plan or {}).get("stickers", []):
            entry = next((e for e in (plan or {}).get("slots", {}).get("emoji_batch", {}).get("entries", []) if e["index"] == sticker.get("index")), {})
            stickers.append({**sticker, **({"emoji_version": entry["version"]} if entry else {}),
                             "source": {"generation": gid or row.get("job"), "index": sticker.get("index")}})
    return batches.sticker_emojis(stickers)


def show_waiting(turn, pending, checked) -> dict:
    turn.sess["pending"] = pending
    if pending.get("choose_pack"):
        turn.reply = "Which pack should this batch join? I will check repeats only within that pack."
        turn.chips = [{"label": p["name"], "action": "emoji_batch_pack", "to": p["id"]} for p in pending["choose_pack"]]
        turn.chips += [{"label": "New pack", "action": "emoji_batch_pack", "to": "new"}]
        return {}
    entry = checked["waiting"][0]
    pos, glyph = entry["index"], entry["emoji"]
    if entry["choice"] == "skip":
        turn.reply = f"Skipping {glyph} in cell {pos}. Paste one unused emoji to replace it."
        turn.chips = ([{"label": "Use " + checked["suggestion"], "text": checked["suggestion"]}] if checked["suggestion"] else [])
    else:
        version = entry["version"]
        turn.reply = f"{glyph} in cell {pos} is already used in this pack or earlier in this batch. Overwrite the existing sticker, add {glyph} {version:02d}, or skip it and provide an unused emoji."
        turn.chips = [{"label": label, "action": "emoji_batch_choice", "to": decision, "indexes": [pos]}
                      for label, decision in (("Overwrite", "overwrite"), (f"Add {version:02d}", "add"), ("Skip / replace", "skip"))]
    turn.chips.append({"label": "Cancel batch", "action": "cancel"})
    turn.trace.end("waiting for an emoji decision · nothing spent")
    return {}


def handle(agent, turn) -> dict:
    from .graph import compact_plan, STYLE_NAMES
    try:
        packs = agent.tools.emoji_packs()
        pending = turn.sess.get("pending") or {}
        glyphs, words = batches.extract(turn.text)
        action = turn.action or {}
        if pending.get("type") != "emoji_batch" or len(glyphs) == 9:
            batches.check(glyphs)
            logical = turn.sess.get("pack") or {}
            previous = list(logical.get("made") or [])
            focus = (turn.sess.get("focus") or {}).get("generation")
            if not previous and focus:
                previous = [{"no": 1, "generation": focus}]
            gens = {agent._row_gen(r) for r in previous}
            named = [p for p in packs if words and re.search(r"\b" + re.escape(p["name"]) + r"\b", words, re.I)]
            related = [p for p in packs if p["id"] == logical.get("library_id") or any((s.get("source") or {}).get("generation") in gens for s in p.get("stickers", []))]
            candidates = named or related
            target = candidates[0] if len(candidates) == 1 else None
            same = not named or target in related
            prompt = (logical.get("request") if same else None) or (target or {}).get("name") or words or "Generic emojis"
            subject = (logical.get("subject") if same else None) or (target or {}).get("name") or words or "Generic emojis"
            pending = {"type": "emoji_batch", "glyphs": glyphs, "prompt": prompt, "subject": subject, "pack_id": (target or {}).get("id"),
                       "previous": previous if same else [], "choices": {}, "replacements": {}, "style_id": turn.sess["settings"]["style_id"],
                       "continue_pack": bool(logical) and same, "next_no": max([r.get("no", 0) for r in previous] or [0]) + 1}
            if len(candidates) > 1:
                pending["choose_pack"] = [{"id": p["id"], "name": p["name"]} for p in candidates]
            elif not target and not previous and packs and not words:
                pending["choose_pack"] = [{"id": p["id"], "name": p["name"]} for p in packs]
        if pending.get("choose_pack"):
            if action.get("type") != "emoji_batch_pack":
                return show_waiting(turn, pending, {})
            pid = action.get("to")
            if pid != "new" and pid not in {p["id"] for p in pending["choose_pack"]}:
                raise ToolError("Choose one of the displayed packs.")
            pending["pack_id"] = None if pid == "new" else pid
            if pid == "new":
                pending.update(previous=[], continue_pack=False, next_no=1)
            pending.pop("choose_pack")
        elif action.get("type") == "emoji_batch_pack":
            raise ToolError("That pack question is no longer pending.")
        used = used_in_context(agent, turn, pending, packs)
        checked = batches.check(pending["glyphs"], used, pending["choices"], pending["replacements"])
        if checked["waiting"]:
            first = checked["waiting"][0]
            pos = first["index"]
            if action.get("type") == "emoji_batch_choice":
                if action.get("indexes") != [pos]:
                    raise ToolError("That emoji question has changed. Use the current choices.")
                pending["choices"][str(pos)] = action.get("to")
            elif words.lower() in ("overwrite", "replace", "add", "add 02", "skip"):
                pending["choices"][str(pos)] = {"replace": "overwrite", "add 02": "add"}.get(words.lower(), words.lower())
            elif len(glyphs) == 1 and first["choice"] == "skip":
                pending["replacements"][str(pos)] = glyphs[0]
        try:
            result = batches.plan(pending["prompt"], pending["glyphs"], used, pending["choices"], pending["replacements"], pending["pack_id"], pending["style_id"])
        except PipelineError:
            # An invalid replacement must not poison the saved draft.
            if len(glyphs) == 1 and checked["waiting"]:
                pending["replacements"].pop(str(checked["waiting"][0]["index"]), None)
            raise
        turn.sess["pending"] = pending
        if not result["ready"]:
            return show_waiting(turn, pending, result)
        plan = result["plan"]
        label = f"Batch {pending['next_no']:02d} · manual emojis"
        batch = {"no": pending["next_no"], "label": label}
        if not pending["continue_pack"]:
            batch["pack"] = {"request": pending["prompt"], "subject": pending["subject"], "grid": "3x3", "style_id": pending["style_id"],
                             "ai": False, "plan": compact_plan(plan), "max": 1, "manual": True, "library_id": pending["pack_id"], "made": []}
        est = agent.tools.estimate("image") if agent.tools.live() else None
        names = [f"{e['emoji']}" + (f" {e['version']:02d}" if e["version"] > 1 else "") for e in result["entries"]]
        stage = stages.of(turn.sess["settings"])
        run = stages.run_spec(stage if stage != "auto" else "emojis", creator.settings_of(turn.sess)["bypass"])
        video_est = agent.tools.estimate("video") if run["creator"] and run["scope"] == "video" and agent.tools.live() else None
        card = {"type": "plan", "subject": pending["subject"], "grid": "3x3", "style": STYLE_NAMES.get(pending["style_id"], pending["style_id"]),
                "count": 9, "names": names, "estimate": (est or 0) + (video_est or 0) or None, "balance": agent.tools.credits(),
                "free": not agent.tools.live(), "prompt": pending["prompt"], "stage": run["stage"]}
        spec = {"type": "creator" if run["creator"] else "create", "subject": pending["subject"], "prompt": pending["prompt"], "grid": "3x3",
                "style_id": pending["style_id"], "ai": False, "estimate": est, "plan": compact_plan(plan), "batch": batch, "force_live": True}
        if run["creator"]:
            spec.update(video_estimate=video_est, scope=run["scope"], bypass=run["bypass"], end=run["end"])
            card["creator"] = {"scope": run["scope"], "bypass": run["bypass"], "sheet": est, "video": video_est, "end": run["end"]}
        if run["plan_only"]:
            card["sheet_prompt"] = plan["sheet_prompt"]
        turn.sess["pending"] = spec
        turn.cards.append(card)
        turn.reply = "Your nine-emoji batch is ready: " + " ".join(names) + ". The duplicate choices are saved. Confirm to generate this exact batch."
        turn.chips = [{"label": "Generate batch", "action": "confirm"}, {"label": "Cancel batch", "action": "cancel"}]
        turn.trace.end("manual batch ready · nothing spent")
    except (PipelineError, ToolError) as error:
        turn.reply = str(error)
        turn.trace.end("manual batch needs a decision · nothing spent", ok=False)
    return {}
