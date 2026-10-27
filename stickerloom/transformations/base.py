"""Transformation templates: "dog as banana" is ONE new character (a dog that IS a banana), not a dog next to a banana.

A request becomes a transformation when it says the subject *becomes* something (`as`, `into`, `turned into`, `shaped like`, `looks like`). "dog with bananas",
"a dog holding a banana", "a dog eating a banana" and "a dog as a pilot" (a costume or role, not a shape) are NOT transformations and take the normal plan.

A template is deterministic and versioned (like the prompt templates): it names the cells that MUST exist (`required`), builds the character sentence that goes into every prompt
(`enrich`), fills the rest of the grid from the emotion bank, and lints its own result (`validate`). The user can override it in the request itself ("no dancing").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..generation import prompter

# connectors that make a request a transformation ("as" only when the target is a thing, see ROLES)
SHAPE = re.compile(r"\b(turned into|turn into|turns into|transformed into|transform into|morphed into|morphing into|becomes|become|into|shaped like|in the shape of|"
                   r"that looks like|looks like|looking like|as)\b")
# words before the connector that mean the subject and another thing are TWO things (a dog with bananas): never a transformation
TWO_THINGS = {"with", "and", "holding", "eating", "wearing", "carrying", "beside", "near", "riding", "hugging", "plus", "next", "sitting", "standing"}
# a request ABOUT stickers ("a birthday sticker pack as a gift") has no subject to transform
NOT_SUBJECT = {"sticker", "stickers", "pack", "packs", "set", "emoji", "emojis", "collection", "soon", "well", "much", "far", "long"}
FILLER = {"too", "very", "much", "more", "any", "all", "one", "other", "another", "same", "that", "this", "so", "really", "quite"}
# "dog as a pilot": a role or costume, the subject stays itself
ROLES = {"pilot", "doctor", "king", "queen", "teacher", "chef", "superhero", "wizard", "pirate", "ninja", "astronaut", "cowboy", "detective", "nurse", "firefighter", "policeman",
         "police", "santa", "student", "soldier", "knight", "princess", "prince", "vampire", "ghost", "witch", "clown", "farmer", "scientist", "artist", "singer", "dancer",
         "judge", "captain", "hero", "villain", "boss", "waiter", "driver", "guard", "angel", "devil", "mascot", "cheerleader", "athlete", "player", "baby", "grandma", "grandpa"}
LEAD = re.compile(r"^(?:please\s+|can you\s+)?(?:(?:create|make|generate|draw|design|build|give me|turn|transform|show)\s+)?(?:me\s+)?(?:a\s+(?:set|pack)\s+of\s+|some\s+)?"
                  r"(?:stickers?\s+of\s+|sticker\s+pack\s+of\s+)?")
ARTICLES = {"a", "an", "the", "my", "some", "this", "that", "your", "our", "his", "her", "its"}
CONTEXT = re.compile(r"\b(for|in|at|during|on)\b")
OVERRIDE = re.compile(r"(?:^|[,;.]|\bbut\b|\band\b|\s)(?:no|without|never|not|skip|except|exclude|don't use|do not use|dont use)\s+([a-z' ]{3,40}?)(?=[,;.]|$|\bbut\b|\bfor\b|\band\b)")
STEMS = {"dance": ("danc",), "shock": ("shock", "scare", "scared", "startl", "gasp"), "squash": ("squash", "squish", "flatten", "flattened", "splat", "crush")}


@dataclass
class Match:
    subject: str
    target: str
    context: str = ""
    forbidden: list = field(default_factory=list)      # stems the user ruled out ("no dancing" -> "danc")
    connector: str = "as"


def _strip(words: list) -> list:
    return [w for w in words if w not in ARTICLES]


def _words(text: str) -> list:
    return re.findall(r"[a-z0-9']+", text.lower())


def stem(word: str) -> str:
    """dancing / dance / dancer -> danc; squashed -> squash; crying -> cry. Crude on purpose: it only has to match the words of a label."""
    w = re.sub(r"(ing|ers|er|ed|es|s|e)$", "", word.lower())
    return w if len(w) >= 3 else word.lower()


def overrides(text: str) -> list:
    """What the user ruled out: "no dancing", "without squash or shock" -> stems. A required slot named here is dropped; a word nobody knows is still kept out of every cell."""
    out = []
    for m in OVERRIDE.finditer(" " + text.lower()):
        for part in re.split(r"\bor\b|,|\band\b", m.group(1)):
            w = [x for x in _words(part) if x not in ARTICLES and x not in FILLER]
            if w and len(stem(w[0])) >= 3:
                out.append(stem(w[0]))
    seen = []
    for s in out:
        if s not in seen:
            seen.append(s)
    return seen


def detect(text: str) -> Match | None:
    """The subject and the target of a transformation request, or None when it is not one."""
    t = " ".join(str(text or "").lower().replace("’", "'").split())
    m = SHAPE.search(t)
    if not m:
        return None
    left = LEAD.sub("", t[:m.start()]).strip(" ,.;")
    right = t[m.end():].strip(" ,.;")
    lw = _strip(_words(left))
    if not lw or TWO_THINGS & set(lw) or NOT_SUBJECT & set(lw):
        return None
    cut = re.split(r"\b(?:for|in|at|during|on|with|but|no|without|never|not|skip|except|exclude)\b|[,;.]| and ", right, maxsplit=1)
    tw = _strip(_words(cut[0]))
    if not tw or len(tw) > 4 or "as" in tw or "than" in tw:         # "a dog as big as a house" compares sizes, it does not transform
        return None
    if m.group(1) == "as" and (set(tw) & ROLES):
        return None
    ctx = CONTEXT.search(right)
    context = " ".join(_strip(_words(right[ctx.end():]))) if ctx else ""
    context = re.split(r"\b(?:no|without|never|not|skip|except|exclude)\b", context)[0].strip()
    return Match(subject=" ".join(lw), target=" ".join(tw), context=context, forbidden=overrides(t), connector=m.group(1))


class Transformation:
    """One versioned template. Subclass or instantiate; `registry` holds them."""
    id = "subject_as_target"
    version = 1
    label = "a subject that becomes something else"
    required = ("dance", "shock", "squash")

    # ---- the wording of the required cells (a lexicon can sharpen it per target, see banana.py)
    def required_cells(self, subject: str, target: str, flavour: dict | None = None) -> dict:
        t = target
        d = {
            "dance": ("dancing", f"dancing joyfully, the whole {t} shape swaying and wiggling side to side, eyes closed, big smile", "💃",
                      f"the {t} body sways and wiggles to a beat, little hops, spins once"),
            "shock": ("shocked", f"shocked, eyes huge, mouth wide open, the {t} body jolting upright", "😱",
                      f"the {t} body jolts with a start, trembles, then freezes wide-eyed"),
            "squash": ("squashed", f"squashed flat like a cartoon, the {t} shape pressed wide and short, dazed face, stars circling", "🥴",
                       f"squashes flat then springs back up to full shape, dazed, stars orbit"),
        }
        for k, v in ((flavour or {}).get("cells") or {}).items():
            d[k] = v
        return d

    def enrich(self, subject: str, target: str, flavour: dict | None = None) -> str:
        """The character sentence every prompt carries (the `{subject_description}` slot): the whole body IS the target, the subject only lends the face."""
        body = (flavour or {}).get("body") or f"its whole body is a {target} (the {target}'s own shape, colour and texture)"
        return (f"a {subject} transformed into a {target}: ONE single character, {body}, with the {subject}'s face, expression and personality on it; "
                f"it is not a {subject} standing next to a {target}, not a {subject} holding or wearing a {target}, and not a {subject} with a {target} pattern; "
                f"small cartoon arms and legs are allowed so it can act; identical in every cell")

    def build_slots(self, m: Match, rows: int, cols: int, flavour: dict | None = None, seed: str = "") -> dict:
        from ..generation import emotions
        n = rows * cols
        subj_slug = prompter.slug(f"{m.subject} as {m.target}") or "sticker"
        forbidden = list(m.forbidden)

        def banned(*texts) -> bool:
            blob = " ".join(texts).lower()
            return any(f and f in blob for f in forbidden)

        from ..generation import expander
        key_colour = "blue" if expander.GREEN_WORDS & set(_words(m.target)) else "green"      # a green target (avocado) would vanish on a green screen
        cells_src = self.required_cells(m.subject, m.target, flavour)
        entries, used = [], set()
        for rid in self.required:
            stems = STEMS.get(rid, (rid[:5],))
            if any(f in stems or any(s.startswith(f) for s in stems) for f in forbidden) or rid not in cells_src:
                continue                                                  # the user ruled this one out ("no dancing")
            suffix, label, emoji, motion = cells_src[rid]
            entries.append((rid, suffix, label, emoji, motion))
            used.add(rid)
        entries = entries[:n]
        used = {e[0] for e in entries if e[0]}                           # only the required cells that fit the grid (a 1x1 has room for one)
        pool = [e for e in emotions.pick(max(n * 2, 12), seed or subj_slug) + [x for g in emotions.GROUPS.values() for x in g]]
        seen_keys = {e[1] for e in entries}
        for suffix, label, emoji, motion in pool:
            if len(entries) >= n:
                break
            kind = next((r for r, st in STEMS.items() if any(s in (suffix + " " + label).lower() for s in st)), None)
            if suffix in seen_keys or (kind and (kind in used or kind in self.required)) or banned(suffix, label, motion):
                continue                                                  # a second dance, or something the user ruled out
            seen_keys.add(suffix)
            entries.append((None, suffix, label, emoji, motion))
        cells = []
        words = [prompter.tag(m.subject), prompter.tag(m.target), "transformation"]
        for i, (rid, suffix, label, emoji, motion) in enumerate(entries[:n], 1):
            key = f"{subj_slug}_{suffix}"
            cell = {"pos": i, "label": label, "tags": prompter.clean_tags(key, [*words, *( [rid] if rid else [])][:4]), "emoji": emoji, "motion": motion}
            cells.append(cell)
        slots = {"subject_description": self.enrich(m.subject, m.target, flavour), "style_id": "flat_vector", "mode": prompter.TEMPLATE_OF[(rows, cols)],
                 "cells": cells, "action_guidance": "transformation", "key_colour": key_colour,
                 "transformation": {"id": self.id, "version": self.version, "flavour": (flavour or {}).get("id"), "subject": m.subject, "target": m.target,
                                    "required": [r for r in self.required if r in used], "forbidden": forbidden, "connector": m.connector}}
        return slots

    def validate(self, slots: dict) -> list:
        """Problems with a built (or hand-edited) transformation plan: a missing required cell, something the user ruled out, a character sentence that lost the point."""
        tr = slots.get("transformation") or {}
        problems = []
        cells = slots.get("cells") or []
        have = {t for c in cells for t in (c.get("tags") or [])}
        for rid in tr.get("required", []):
            if rid not in have:
                problems.append(f"the required '{rid}' cell is missing")
        for c in cells:
            blob = f"{c.get('label', '')} {c.get('motion', '')} {' '.join(c.get('tags') or [])}".lower()
            for f in tr.get("forbidden", []):
                if f and f in blob:
                    problems.append(f"cell {c.get('pos')} uses '{f}', which the request ruled out")
        sd = str(slots.get("subject_description", "")).lower()
        if tr.get("subject") and tr.get("target") and (tr["subject"] not in sd or tr["target"] not in sd or "not a" not in sd):
            problems.append("the character sentence must name the subject and the target and say it is one character")
        rows, cols = prompter.TEMPLATE_GRID.get(slots.get("mode"), (0, 0))
        if len(cells) != rows * cols:
            problems.append(f"need {rows * cols} cells, built {len(cells)}")
        keys = [(c.get("tags") or [""])[0] for c in cells]
        if len(set(keys)) != len(keys):
            problems.append("two cells share a key")
        return problems
