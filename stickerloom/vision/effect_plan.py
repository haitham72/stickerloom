"""The smart step of a particle effect: what bursts out of THIS sticker.

Telegram's effect for a heart is hearts, for a strawberry strawberries: it is never the picture itself flying about. For a Batman sticker the burst is bat signals and bats, for a
jewelry sticker small gold bars and diamonds, for a cat paws, ears and fish: the objects the sticker is ABOUT. A vision model reads each sticker and answers with a small JSON
(subject, pieces, mood); code lints it (`generation/effect_prompts.lint_plan`), groups stickers of the same subject (a pack of eight Superhero poses is ONE group, so it costs one sprite
sheet or one video), and picks a motion that fits each mood. Without a model, or when it answers nonsense, a built-in table of about a hundred common emoji and subjects answers instead
and the result says so (`by: "lexicon"`). Nothing is sent to a model without consent (`vision/consent.py`), exactly like the captions; nothing here approves anything."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ..generation import effect_prompts as ep
from ..services import llm
from . import consent
from .judge import CACHE_TTL, JudgeError, VisionJudge, _first_json, _flatten, target

PLAN_VERSION = "effect_plan_v1"
MAX_VLM_STICKERS = 12            # a pack is read this many stickers at a time; the rest join the group their name or emoji matches
GENERIC = ["small sparkles", "tiny stars", "little confetti pieces"]

PLAN_SYSTEM = f"""You design the BURST of a Telegram-style effect for ONE sticker (a cut-out picture on a grey background). When someone presses the sticker's emoji, small pieces burst out of
the centre and fall away. Decide WHAT bursts. The pieces are small objects that the sticker is ABOUT, never the character itself and never the whole picture: a Batman sticker bursts
bat signals and bats; a jewelry sticker small gold bars and diamonds; a cat paws, cat ears and fish; Superhero cape pieces and shield badges; a heart hearts; a strawberry small strawberries,
green leaves and tiny seeds. If the picture is itself a simple object, the pieces are small copies of it and its parts.
Rules: 2 to 6 different pieces, each at most 4 words, concrete things (no actions, no feelings), no text, letters, logos, faces, hands or people, and never the word "sticker".
Also say what the sticker is (subject, 1 to 3 words) and its mood: one of happy, excited, love, calm, sad, angry, neutral.
Reply with ONE JSON object and nothing else: {{"subject": "...", "elements": ["...", "..."], "mood": "happy"}}
{llm.DATA_RULE}"""

MOOD_PRESET = {"happy": "burst", "excited": "burst", "love": "vortex", "calm": "fountain", "sad": "rain", "angry": "burst", "neutral": "burst"}
MOODS = tuple(MOOD_PRESET)

# emoji (and subject words) -> (subject, pieces). A table of the obvious: it is the fallback, and it is what a person can read to see what "smart" means.
LEXICON: dict[str, tuple[str, list[str]]] = {
    "❤": ("heart", ["red hearts", "pink hearts", "tiny sparkles"]), "💖": ("heart", ["pink hearts", "small stars", "tiny sparkles"]), "😍": ("love", ["red hearts", "pink hearts", "small stars"]),
    "🥰": ("love", ["red hearts", "small pink flowers", "tiny sparkles"]), "😘": ("kiss", ["red hearts", "small lips", "tiny sparkles"]),
    "🍓": ("strawberry", ["small ripe strawberries", "green leaves", "tiny yellow seeds"]), "🍒": ("cherries", ["small cherries", "green leaves", "tiny sparkles"]),
    "🍌": ("banana", ["small bananas", "banana slices", "tiny sparkles"]), "🍎": ("apple", ["small red apples", "green leaves", "apple slices"]),
    "🍉": ("watermelon", ["watermelon slices", "black seeds", "small red pieces"]), "🍕": ("pizza", ["pizza slices", "pepperoni", "melted cheese bits"]),
    "🍔": ("burger", ["small burgers", "lettuce leaves", "sesame seeds"]), "🍩": ("donut", ["small donuts", "pink sprinkles", "chocolate drops"]),
    "🎂": ("cake", ["cake slices", "candles", "colourful sprinkles"]), "🎉": ("party", ["confetti", "streamers", "small stars"]), "🎁": ("gift", ["small gift boxes", "ribbons", "tiny stars"]),
    "🐱": ("cat", ["cat paws", "cat ears", "small fish"]), "😺": ("cat", ["cat paws", "cat ears", "small fish"]), "🐶": ("dog", ["dog paws", "small bones", "tiny hearts"]),
    "🐻": ("bear", ["bear paws", "honey drops", "tiny hearts"]), "🐼": ("panda", ["bamboo leaves", "panda paws", "tiny hearts"]), "🦊": ("fox", ["fox tails", "small paws", "autumn leaves"]),
    "🦅": ("falcon", ["falcon feathers", "small feathers", "tiny stars"]), "🐦": ("bird", ["small feathers", "tiny music notes", "tiny stars"]), "🦋": ("butterfly", ["small butterflies", "petals", "tiny sparkles"]),
    "🐝": ("bee", ["small bees", "honey drops", "tiny flowers"]), "🐟": ("fish", ["small fish", "bubbles", "tiny stars"]), "🐙": ("octopus", ["tentacles", "bubbles", "small shells"]),
    "🦄": ("unicorn", ["rainbow stars", "small horns", "glitter"]), "🐉": ("dragon", ["small flames", "scales", "tiny stars"]), "👻": ("ghost", ["small ghosts", "tiny stars", "wisps"]),
    "💎": ("diamond", ["small diamonds", "tiny sparkles", "gold coins"]), "💍": ("jewelry", ["small diamonds", "gold rings", "gold bars"]), "💰": ("money", ["gold coins", "gold bars", "small banknotes"]),
    "👑": ("crown", ["small gold crowns", "gold coins", "small gems"]), "🔥": ("fire", ["small flames", "orange sparks", "tiny embers"]), "⭐": ("star", ["small stars", "gold sparkles", "tiny comets"]),
    "🌟": ("star", ["small stars", "gold sparkles", "tiny comets"]), "✨": ("sparkle", ["small stars", "gold sparkles", "tiny diamonds"]), "💥": ("explosion", ["sparks", "small stars", "smoke puffs"]),
    "🌸": ("blossom", ["pink petals", "small flowers", "tiny sparkles"]), "🌹": ("rose", ["red petals", "small roses", "green leaves"]), "🌻": ("sunflower", ["yellow petals", "small sunflowers", "seeds"]),
    "🌈": ("rainbow", ["small rainbows", "colourful stars", "clouds"]), "☀": ("sun", ["small suns", "light rays", "gold sparkles"]), "🌙": ("moon", ["small crescent moons", "stars", "tiny clouds"]),
    "⚡": ("lightning", ["small lightning bolts", "electric sparks", "tiny stars"]), "❄": ("snow", ["snowflakes", "ice crystals", "tiny stars"]), "💧": ("water", ["water drops", "bubbles", "tiny splashes"]),
    "🎵": ("music", ["music notes", "small stars", "tiny sparkles"]), "⚽": ("football", ["small footballs", "grass bits", "tiny stars"]), "🏆": ("trophy", ["small gold trophies", "gold stars", "confetti"]),
    "🚀": ("rocket", ["small rockets", "flames", "tiny stars"]), "🚗": ("car", ["small cars", "gear wheels", "tiny sparks"]), "✈": ("airplane", ["small paper planes", "clouds", "tiny stars"]),
    "☕": ("coffee", ["coffee beans", "steam puffs", "small cups"]), "🍺": ("beer", ["foam bubbles", "small mugs", "wheat"]), "🎈": ("balloon", ["small balloons", "confetti", "ribbons"]),
    "😂": ("laughing", ["tears of joy", "small stars", "tiny sparkles"]), "😭": ("crying", ["water drops", "small tears", "tiny clouds"]), "😡": ("angry", ["small flames", "steam puffs", "tiny sparks"]),
    "👍": ("thumbs up", ["small stars", "tiny hearts", "sparkles"]), "🙏": ("thanks", ["small hearts", "gold sparkles", "small flowers"]), "💪": ("strength", ["small lightning bolts", "stars", "sparks"]),
    "🦸": ("superhero", ["cape pieces", "shield badges", "small stars"]), "🦇": ("bat", ["small bats", "bat signals", "moon pieces"]), "🕷": ("spider", ["small spiders", "web pieces", "tiny stars"]),
    "💀": ("skull", ["small skulls", "bones", "tiny sparks"]), "🤖": ("robot", ["gears", "bolts", "small sparks"]), "👽": ("alien", ["small ufos", "stars", "green slime drops"]),
}
KEYWORDS = {                                     # words of a sticker's name -> the same table
    "superhero": "🦸", "batman": "🦇", "cape": "🦸", "bat": "🦇", "cat": "🐱", "kitten": "🐱", "dog": "🐶", "puppy": "🐶", "falcon": "🦅", "bird": "🐦", "banana": "🍌", "strawberry": "🍓",
    "cherry": "🍒", "cherries": "🍒", "pizza": "🍕", "cake": "🎂", "gold": "💰", "diamond": "💎", "jewelry": "💍", "jewellery": "💍", "money": "💰", "heart": "❤", "love": "😍",
    "fire": "🔥", "star": "⭐", "rocket": "🚀", "coffee": "☕", "unicorn": "🦄", "dragon": "🐉", "ghost": "👻", "robot": "🤖", "fox": "🦊", "panda": "🐼", "bear": "🐻",
}


def lexicon_plan(name: str = "", emoji: str | list | None = None) -> dict:
    """The table's answer for a sticker: by its emoji tag first, then by a word of its name, else generic sparkles."""
    tags = emoji if isinstance(emoji, list) else list(emoji or "")
    for e in tags:
        e = str(e).replace("️", "")
        if e in LEXICON:
            s, els = LEXICON[e]
            return {"subject": s, "elements": list(els), "mood": "neutral", "by": "lexicon"}
    for w in re.findall(r"[a-z]+", str(name).lower()):
        e = KEYWORDS.get(w)
        if e:
            s, els = LEXICON[e]
            return {"subject": s, "elements": list(els), "mood": "neutral", "by": "lexicon"}
    subj = " ".join(re.findall(r"[A-Za-z]+", str(name))[:2]).strip().lower() or "sticker pack"
    return {"subject": subj if subj != "sticker pack" else "this sticker", "elements": list(GENERIC), "mood": "neutral", "by": "generic"}


class _Parsed:
    decision = None

    def __init__(self, plan: dict, model: str):
        self.plan, self.model = plan, model


def _parse(text: str, model: str) -> _Parsed:
    obj = _first_json(text)
    if not isinstance(obj, dict):
        raise ValueError("the answer is not a JSON object")
    mood = str(obj.get("mood") or "neutral").lower().strip()
    plan = {"subject": obj.get("subject"), "elements": obj.get("elements")}
    if not isinstance(plan["elements"], list):
        raise ValueError("'elements' must be a list of pieces")
    try:
        clean = ep.lint_plan(plan)
    except ValueError as e:
        raise ValueError(str(e))
    return _Parsed({**clean, "mood": mood if mood in MOODS else "neutral", "by": "vlm"}, model)


def _norm(subject: str) -> str:
    w = re.sub(r"\b(a|an|the|small|little|tiny|cute|emoji|sticker|stickers|pack)\b", " ", str(subject).lower())
    return re.sub(r"s\b", "", " ".join(w.split())).strip() or str(subject).lower().strip()


def analyse(stickers: list[dict], *, vlm: VisionJudge | None = None, allowed=None, note: str = "", out: Path | None = None) -> dict:
    """stickers: [{id, name, emoji, png (bytes or None)}]. Returns {"groups": [{id, subject, elements, style, key, stickers: [ids], moods: {id: mood}, preset: {id: name}, by}],
    "per_sticker": {id: plan}, "model": str|None, "notes": [str]}. `allowed` is the person's yes to sending pictures to a model (None = not asked: the table answers and the result
    says so). `note` is the person's own words ("make it bats and moons"): when it names pieces they replace the model's."""
    per: dict[str, dict] = {}
    notes: list[str] = []
    model = None
    use_vlm = allowed is True and any(s.get("png") for s in stickers)
    if allowed is not True:
        notes.append("Pictures were not sent to a model (not allowed), so the built-in table chose the particles: you can edit them.")
    vlm = vlm or (VisionJudge(out=out) if use_vlm else None)
    todo = [s for s in stickers if s.get("png")][:MAX_VLM_STICKERS] if use_vlm else []
    for s in todo:
        try:
            consent.require(allowed)
            sha = hashlib.sha256(s["png"]).hexdigest()
            key = vlm.cache.key("vlm", "effect_plan", sha, target()["model"], PLAN_VERSION)
            hit = vlm.cache.get(key, "vlm_effect_plan")
            if hit:
                per[s["id"]] = dict(hit, cached=True)
                continue
            v, meta = vlm._structured("VLM_EFFECT_PLAN", PLAN_SYSTEM, f"Sticker name: {s.get('name') or 'unknown'}. Emoji tag: {''.join(s.get('emoji') or [])}.",
                                      [_flatten(s["png"])], None, f"effect/{s['id']}", PLAN_VERSION, _parse)
            model = meta.get("model") or model
            per[s["id"]] = dict(v.plan, model=meta.get("model"))
            vlm.cache.set(key, per[s["id"]], CACHE_TTL)
        except consent.ConsentRequired:
            raise
        except (JudgeError, llm.LLMError, OSError, ValueError) as e:             # a model that fails, or a picture that does not open: the table answers for this sticker
            notes.append(f"The model could not read {s.get('name') or s['id']} ({str(e)[:120]}): the built-in table chose its particles.")
    for s in stickers:
        per.setdefault(s["id"], lexicon_plan(s.get("name", ""), s.get("emoji")))
    user_els = _pieces_from_note(note)
    if user_els:
        for p in per.values():
            p["elements"], p["by"] = user_els, "you"
        notes.append("Your own particles replace the suggested ones.")
    groups: dict[str, dict] = {}
    for s in stickers:
        p = per[s["id"]]
        g = groups.setdefault(_norm(p["subject"]), {"subject": p["subject"], "elements": [], "stickers": [], "moods": {}, "preset": {}, "by": p["by"]})
        for e in p["elements"]:
            if e.lower() not in {x.lower() for x in g["elements"]} and len(g["elements"]) < ep.MAX_ELEMENTS:
                g["elements"].append(e)
        g["stickers"].append(s["id"])
        g["moods"][s["id"]] = p.get("mood", "neutral")
        g["preset"][s["id"]] = MOOD_PRESET.get(p.get("mood", "neutral"), "burst")
    out_groups = []
    for n, (k, g) in enumerate(groups.items(), 1):
        g["elements"] = g["elements"][:6]
        try:
            clean = ep.lint_plan({"subject": g["subject"], "elements": g["elements"]})
        except ValueError:
            clean = {"subject": g["subject"], "elements": list(GENERIC), "style": "glossy cartoon look, bold clean shapes, vivid colours"}
        out_groups.append({"id": f"g{n}", **clean, "key": ep.key_colour_for(clean["elements"]), "stickers": g["stickers"], "moods": g["moods"], "preset": g["preset"], "by": g["by"]})
    return {"groups": out_groups, "per_sticker": per, "model": model, "notes": notes}


def _pieces_from_note(note: str) -> list[str]:
    """'bats and moons' / 'particles: gold bars, diamonds' (or the older 'pieces: ...') -> particles, when the note really names some; a sentence about anything else gives nothing."""
    m = re.search(r"(?:particles?|pieces?|elements?|burst(?:s)? (?:of|with|into)|made of|explode[sd]? (?:into|to)|only|just)\s*[:\-]?\s+([a-z0-9 ,&'\-]{3,120})$", str(note or "").strip().lower())
    if not m:
        return []
    parts = [x.strip(" .") for x in re.split(r",| and | & ", m.group(1)) if x.strip(" .")]
    try:
        return ep.lint_plan({"subject": "x", "elements": parts})["elements"]
    except ValueError:
        return []


# ---------- the particle SET of a whole effect (one set for the pack, drawn once: docs/architecture.md ("Reactions")) ----------
SET_VERSION = "effect_set_v1"
SET_MAX = 12                     # candidate particles offered at most
SET_ASK = "8 to 12"              # what the model is asked for ...
SET_MIN_USABLE = 3               # ... and the fewest it may answer with once the lint has dropped what is not allowed (less is nonsense: the table answers)
TABLE_PAD = ["small sparkles", "tiny stars", "little confetti pieces", "glitter dots", "round bubbles", "small light rays", "tiny circles", "soft glow dots"]
TABLE_MIN = 8                    # the table pads its answer with generic particles up to this many

SET_SYSTEM = f"""You choose the PARTICLES of a Telegram-style burst effect. When someone presses a sticker's emoji, small separate pieces burst out of it and fall away. ONE set of particles is
drawn for a WHOLE sticker pack and shared by every sticker, so the particles must fit the pack as a whole, not one pose of it. You are shown ONE picture: either the master sheet the stickers were cut
from, or a contact sheet of some of the pack's stickers. Look at what the pack is ABOUT (its character, objects, props, theme, colours) and list {SET_ASK} candidate particles: small concrete objects
related to what you see, never the character itself and never the whole picture (a Batman pack: bat signals, bats, cape pieces, utility belt pieces; a jewelry pack: small gold bars, diamonds, rings).
Rules: every candidate is at most 4 words, a concrete thing (no actions, no feelings), different from the others, and never contains text, letters, logos, faces, hands or people, nor the word "sticker".
Reply with ONE JSON object and nothing else: {{"options": ["...", "..."]}}
{llm.DATA_RULE}"""


def _clean_options(items) -> list[str]:
    """Each candidate through the lint of the effect prompts, one at a time: the ones that break a rule (too long, text, people, the word sticker) are dropped, the rest kept in order."""
    out: list[str] = []
    for it in items if isinstance(items, list) else []:
        try:
            e = ep.lint_plan({"subject": "x", "elements": [it]})["elements"][0]
        except (ValueError, IndexError):
            continue
        if e.lower() not in {x.lower() for x in out}:
            out.append(e)
    return out[:SET_MAX]


def _parse_set(text: str, model: str) -> _Parsed:
    obj = _first_json(text)
    if not isinstance(obj, dict):
        raise ValueError("the answer is not a JSON object")
    items = obj.get("options")
    if items is None:
        items = obj.get("particles") if obj.get("particles") is not None else obj.get("elements")
    if not isinstance(items, list):
        raise ValueError("'options' must be a list of particle names")
    clean = _clean_options(items)
    if len(clean) < SET_MIN_USABLE:
        raise ValueError(f"only {len(clean)} usable particle(s) after the rules (at most 4 words, no text, logos, people or the word sticker): list {SET_ASK}")
    return _Parsed({"options": clean}, model)


def table_options(stickers: list[dict]) -> list[str]:
    """The built-in table's answer for a pack: what each sticker's emoji (or a word of its name) says, one subject after another so every subject is heard, then generic particles up to
    `TABLE_MIN`, at most `SET_MAX`. `stickers`: [{name, emoji}]."""
    lists: list[list[str]] = []
    seen_subjects: set[str] = set()
    for s in stickers:
        p = lexicon_plan(s.get("name", ""), s.get("emoji"))
        if p["by"] != "lexicon" or p["subject"] in seen_subjects:
            continue
        seen_subjects.add(p["subject"])
        lists.append(list(p["elements"]))
    out: list[str] = []
    for r in range(max((len(x) for x in lists), default=0)):
        for x in lists:
            if r < len(x) and x[r].lower() not in {y.lower() for y in out}:
                out.append(x[r])
    for pad in (GENERIC if not out else []) + TABLE_PAD:
        if len(out) >= TABLE_MIN:
            break
        if pad.lower() not in {y.lower() for y in out}:
            out.append(pad)
    return out[:SET_MAX]


def suggest_options(png: bytes | None, *, kind: str, stickers: list[dict], pack_name: str = "", grid=(2, 2), allowed=None, vlm: VisionJudge | None = None,
                    out: Path | None = None) -> dict:
    """Candidate particles for the pack's ONE shared set. `png`: the single picture the model looks at (the batch's master sheet, `kind` "batch", or a contact sheet of the stickers, `kind`
    "contact"); `stickers`: [{name, emoji}] for the table's answer. The model is used only with the person's yes (`allowed is True`, the same consent and the same ledger line as the plan);
    without it, without a model or when it answers nonsense the table answers. Returns {"options": [str], "by": "vlm" | "table", "model": str | None, "notes": [str]}."""
    notes: list[str] = []
    if allowed is not True:
        notes.append("The picture was not sent to a model (not allowed), so the built-in table chose the candidate particles.")
    elif not png:
        notes.append("There is no picture to show a model, so the built-in table chose the candidate particles.")
    else:
        vlm = vlm or VisionJudge(out=out)
        try:
            consent.require(allowed)
            sha = hashlib.sha256(png).hexdigest()
            key = vlm.cache.key("vlm", "effect_set", sha, target()["model"], SET_VERSION)
            hit = vlm.cache.get(key, "vlm_effect_set")
            if hit:
                return {"options": list(hit["options"]), "by": "vlm", "model": hit.get("model"), "notes": notes, "cached": True}
            what = "the master sheet the pack's stickers were cut from" if kind == "batch" else "a contact sheet of the pack's stickers"
            names = ", ".join(str(s.get("name") or "") for s in stickers[:9] if s.get("name"))
            user = (f"The picture is {what}. Pack name: {llm.fence('PACK', pack_name or 'unknown', 120)}. Sticker names: {llm.fence('NAMES', names or 'unknown', 400)}. "
                    f"Emoji tags: {''.join(str(s.get('emoji') or '') for s in stickers[:9]) or 'none'}. The set will be drawn as a {grid[0]} by {grid[1]} sheet of {grid[0] * grid[1]} particles.")
            v, meta = vlm._structured("VLM_EFFECT_SET", SET_SYSTEM, user, [png], None, "set", SET_VERSION, _parse_set)
            res = {"options": v.plan["options"], "model": meta.get("model")}
            vlm.cache.set(key, res, CACHE_TTL)
            return {**res, "by": "vlm", "notes": notes}
        except consent.ConsentRequired:
            raise
        except (JudgeError, llm.LLMError, OSError, ValueError) as e:
            notes.append(f"The model could not read the picture ({str(e)[:120]}): the built-in table chose the candidate particles.")
    return {"options": table_options(stickers), "by": "table", "model": None, "notes": notes}
