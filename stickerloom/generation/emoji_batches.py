"""Manual nine-cell batches and pack-scoped duplicate decisions. No provider calls."""
from __future__ import annotations

from . import actions, emoji, tasks


def extract(text: str) -> tuple[list[str], str]:
    glyphs, words = [], []
    rest = str(text or "")
    while rest:
        cluster = emoji.first_emoji(rest)
        if emoji.valid(cluster):
            glyphs.append(cluster)
        else:
            words.append(cluster)
        rest = rest[len(cluster):]
    return glyphs, "".join(words).strip(" ,;\n")


def identity(glyph: str) -> str:
    return emoji.base(glyph)


def sticker_emojis(stickers) -> list[str]:
    out, seen, versions, legacy = {}, set(), {}, {}
    for s in stickers or []:
        raw = s.get("emoji") or []
        glyphs = emoji.split_emoji(raw) if isinstance(raw, str) else raw
        glyph = next((g for g in glyphs if emoji.valid(g)), None)
        if glyph:
            src = s.get("source") or {}
            base = identity(glyph)
            key = (src.get("generation"), src.get("index"), base) if src.get("generation") and src.get("index") else (s.get("id") or len(seen), base)
            if key not in seen:
                seen.add(key)
                out[base] = glyph
                version = s.get("emoji_version")
                if isinstance(version, int) and 1 <= version <= 10000:
                    versions[base] = max(versions.get(base, 0), version)
                else:
                    legacy[base] = legacy.get(base, 0) + 1
    return [glyph for base, glyph in out.items() for _ in range(max(legacy.get(base, 0), versions.get(base, 0)))]


def check(glyphs, used=(), choices=None, replacements=None) -> dict:
    if not isinstance(glyphs, list) or len(glyphs) != 9 or not all(emoji.valid(g) and actions.token_for_emoji(g) for g in glyphs):
        raise tasks.sources_error("Paste exactly nine supported emojis for a 3×3 batch.")
    choices, replacements = choices or {}, replacements or {}
    if not isinstance(choices, dict) or not isinstance(replacements, dict):
        raise tasks.sources_error("Emoji decisions and replacements must be objects keyed by cell number.")
    counts = {}
    for glyph in used:
        if emoji.valid(glyph):
            counts[identity(glyph)] = counts.get(identity(glyph), 0) + 1
    entries, seen = [], dict(counts)
    reserved = set(counts) | {identity(g) for g in glyphs}
    for pos, original in enumerate(glyphs, 1):
        glyph = replacements.get(str(pos), original)
        if not emoji.valid(glyph) or not actions.token_for_emoji(glyph):
            raise tasks.sources_error(f"Cell {pos} needs a supported emoji.")
        if glyph != original and identity(glyph) in reserved:
            raise tasks.sources_error(f"{glyph} is already used in this pack or batch. Choose an unused emoji.")
        duplicate = seen.get(identity(glyph), 0) > 0
        choice = choices.get(str(pos)) if duplicate else "new"
        if choice not in (None, "new", "overwrite", "add", "skip") or duplicate and choice == "new":
            raise tasks.sources_error("Choose overwrite, add or skip for a repeated emoji.")
        if glyph != original:
            choice = "new"
            reserved.add(identity(glyph))
        entries.append({"index": pos, "emoji": glyph, "original": original, "duplicate": duplicate,
                        "choice": choice, "version": seen.get(identity(glyph), 0) + (choice != "overwrite")})
        if choice != "overwrite":
            seen[identity(glyph)] = seen.get(identity(glyph), 0) + 1
    waiting = [e for e in entries if e["duplicate"] and e["choice"] in (None, "skip")]
    suggestion = next((r["emoji"] for r in actions.generation_bank().values() if identity(r["emoji"]) not in reserved), None)
    return {"entries": entries, "ready": not waiting, "waiting": waiting, "suggestion": suggestion}


def plan(prompt, glyphs, used=(), choices=None, replacements=None, pack_id=None, style_id="flat_vector", loop=False) -> dict:
    if not isinstance(prompt, str) or not prompt.strip() or style_id not in tasks.prompter.STYLES:
        raise tasks.sources_error("Provide a subject and a supported style for the batch.")
    checked = check(glyphs, used, choices, replacements)
    if not checked["ready"]:
        return checked
    actual = [e["emoji"] for e in checked["entries"]]
    prompt = actions.strip_presets(prompt)
    p = tasks._token_plan(prompt, (3, 3), False, [actions.token_for_emoji(g) for g in actual], style_id, loop)
    for cell, sticker, glyph, entry in zip(p["slots"]["cells"], p["stickers"], actual, checked["entries"]):
        cell["emoji"] = sticker["emoji"] = glyph
        if entry["version"] > 1:
            cell["tags"][0] += f"_v{entry['version']:02d}"
            sticker["key"] = cell["tags"][0]
            cell["label"] += f" (version {entry['version']:02d})"
        if any(c["label"] == cell["label"] for c in p["slots"]["cells"][:cell["pos"] - 1]):
            cell["label"] += f" (take {cell['pos']})"
            cell["tags"][0] += f"_take{cell['pos']}"
            sticker["key"] = cell["tags"][0]
    p["slots"]["emoji_batch"] = {"pack_id": pack_id, "entries": [
        {k: e[k] for k in ("index", "emoji", "choice", "version")} for e in checked["entries"]]}
    p["batches_max"] = 1
    return {**checked, "plan": tasks.plan_again(p, loop)}


def validate_metadata(raw, cells) -> dict:
    if not isinstance(raw, dict) or raw.get("pack_id") is not None and not isinstance(raw["pack_id"], str):
        raise tasks.sources_error("The manual emoji batch has an invalid target pack.")
    entries = raw.get("entries")
    if not isinstance(entries, list) or len(entries) != len(cells) or len(entries) != 9:
        raise tasks.sources_error("The manual emoji batch needs nine decisions.")
    clean = []
    for pos, (e, c) in enumerate(zip(entries, cells), 1):
        if not isinstance(e, dict) or e.get("index") != pos or e.get("emoji") != c["emoji"] or not emoji.valid(e["emoji"]) or e.get("choice") not in ("new", "overwrite", "add"):
            raise tasks.sources_error("The manual emoji batch decisions must match the previewed cells.")
        version = e.get("version", 1)
        if not isinstance(version, int) or not 1 <= version <= 10000:
            raise tasks.sources_error("The emoji version is invalid.")
        clean.append({"index": pos, "emoji": e["emoji"], "choice": e["choice"], "version": version})
    return {"pack_id": raw.get("pack_id"), "entries": clean}


def validate_target(metadata, lib, owner) -> None:
    """Check ownership and newly added duplicates immediately before provider submission."""
    if not metadata or not metadata.get("pack_id"):
        return
    pid = metadata["pack_id"]
    if not lib.owns(pid, owner):
        raise tasks.sources_error("No such target pack", 404)
    pack = next(p for p in lib.snapshot(owner=owner)["packs"] if p["id"] == pid)
    entries = metadata["entries"]
    checked = check([e["emoji"] for e in entries], sticker_emojis(pack["stickers"]),
                    {str(e["index"]): e["choice"] if e["choice"] != "new" else None for e in entries})
    if not checked["ready"]:
        raise tasks.sources_error("This pack now contains a selected emoji. Paste the batch again to choose overwrite, add or skip.", 409)
