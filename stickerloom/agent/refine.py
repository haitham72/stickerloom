"""Feedback about a whole subject becomes a change to the prompt that made it: "the cherries were so realistic, make them more cartoonish; the banana was so small, make it bigger".

`mentions` finds which subjects of the chat a message talks about and cuts it into one clause per subject; `extract` reads a clause into a small structured delta (style, size, colour, extra
words); `apply` writes that delta into a copy of the batch's stored plan (its slots: the style id, the subject description) and records it in `plan["refinements"]`, so the NEW prompt is the OLD
prompt plus the change and the lineage is on the plan. Pure: no model, no files; the graph asks a model only when the rules found nothing (`brain.refine_delta`)."""
from __future__ import annotations

import copy
import re
import time

STYLE_WORDS = [                                   # (regex, preset id), most specific first
    (r"cartoon\w*|toon\w*|cel[- ]?shad\w*|animated", "toon_shade"),
    (r"water ?colou?r\w*", "watercolor"),
    (r"hand[- ]?drawn|sketch\w*|pencil|marker|doodle\w*", "hand_drawn"),
    (r"pixel\w*|8[- ]?bit|16[- ]?bit|retro game", "pixel_art"),
    (r"paper ?cut\w*|paper craft|origami", "paper_cut"),
    (r"comic\w*|pop[- ]?art|halftone", "pop_comic"),
    (r"kawaii|chibi", "kawaii"),
    (r"minimal\w*|clean line", "minimal"),
    (r"clay|plasticine|claymation|matte", "clay_3d"),
    (r"glossy|shiny|3d|pixar|plastic", "glossy_3d"),
    (r"flat|vector|simple shapes?", "flat_vector"),
    (r"realistic|realism|lifelike|photo\w*|real", "realistic"),
]
OPPOSITE = {"realistic": "toon_shade", "toon_shade": "realistic", "flat_vector": "glossy_3d", "glossy_3d": "flat_vector", "pixel_art": "flat_vector", "hand_drawn": "flat_vector"}
LABEL = {"toon_shade": "cartoonish", "realistic": "realistic", "flat_vector": "flat", "glossy_3d": "glossy 3D", "clay_3d": "soft clay 3D", "hand_drawn": "hand-drawn", "minimal": "minimal",
         "pixel_art": "pixel art", "watercolor": "watercolour", "paper_cut": "paper cut", "pop_comic": "pop comic", "kawaii": "kawaii"}
COLOURS = "red|blue|green|yellow|pink|purple|orange|black|white|brown|gold|golden|silver|pastel|neon|dark|bright"
SIZE_CLAUSE = {"larger": "drawn large so each character fills most of its cell", "smaller": "drawn small with plenty of empty space around each character"}
COLOUR_MARK = "in {c} tones"
INTENSE = r"(?:too|so|very|really|way too|a bit too|a little too|much too)"


def stem(w: str) -> str:
    w = w.lower()
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    return re.sub(r"(?:es|s)$", "", w) if len(w) > 3 else w


def mentions(text: str, names: list[str]) -> list[tuple[str, str]]:
    """[(subject name, its clause)] in the order they appear: the clause of a subject runs from its name to the next subject's name ("the cherries ... cartoonish the banana ... bigger")."""
    low = text.lower()
    hits = []
    for name in names:
        words = [stem(w) for w in re.findall(r"[a-z]+", name.lower()) if w not in ("pack", "set", "stickers", "sticker", "the", "a", "an")]
        if not words:
            continue
        pos = None
        for m in re.finditer(r"[a-z]+", low):
            if stem(m.group(0)) == words[0] or (len(words) > 1 and stem(m.group(0)) in words):
                pos = m.start()
                break
        if pos is not None:
            hits.append((pos, name))
    hits.sort()
    out = []
    for i, (pos, name) in enumerate(hits):
        end = hits[i + 1][0] if i + 1 < len(hits) else len(text)
        out.append((name, text[pos:end].strip(" ,.;")))
    return out


def _style_in(clause: str, pattern_prefix: str) -> str | None:
    for rx, sid in STYLE_WORDS:
        if re.search(pattern_prefix.replace("{S}", rx), clause):
            return sid
    return None


def extract(clause: str) -> dict:
    """{"style_id", "size", "colour", "notes", "matched"} from one clause. `matched` is true when a style, a size or a colour was understood (an extra word alone is not enough to act on)."""
    c = " ".join(clause.lower().split())
    d: dict = {"style_id": None, "size": None, "colour": None, "notes": [], "matched": False}
    toward = (_style_in(c, r"\bmore\s+(?:{S})\b") or _style_in(c, r"\b(?:make|turn|change|redo)\s+(?:it|them|him|her|this|that|\w+)\s+(?:look\s+)?(?:more\s+)?(?:{S})\b")
              or _style_in(c, r"\b(?:in|into|with)\s+(?:a\s+|an\s+|the\s+)?(?:{S})\s+(?:style|look|version)\b") or _style_in(c, r"\b(?:{S})\s+(?:style|look|version)\b")
              or _style_in(c, r"\bless\s+(?:{S})\b") and OPPOSITE.get(_style_in(c, r"\bless\s+(?:{S})\b")))
    if not toward:                                # a complaint about a style ("so realistic") moves away from it
        said = _style_in(c, rf"\b{INTENSE}\s+(?:{{S}})\b")
        toward = OPPOSITE.get(said) if said else None
    if toward:
        d["style_id"] = toward
    if re.search(r"\b(?:too|so|very|really|way too|a bit too)\s+(?:small|tiny|little)\b|\btiny\b|\bsmall\b(?!er)|\b(?:bigger|larger|huge|enormous|bigger than|fill(?:s|ing)? (?:the )?cell|zoom(?:ed)? in)\b|\bmake (?:it|them|\w+) big\b", c) \
            and not re.search(r"\b(?:smaller|tinier)\b|\btoo (?:big|large)\b|\bso (?:big|large)\b", c):
        d["size"] = "larger"
    elif re.search(r"\b(?:smaller|tinier)\b|\b(?:too|so|very|way too) (?:big|large|huge)\b|\bmake (?:it|them|\w+) small\b", c):
        d["size"] = "smaller"
    m = re.search(rf"\b(?:more|make (?:it|them|him|her|\w+)|in|with|turn (?:it|them) into)\s+(?:a\s+)?({COLOURS})\b(?!\s+(?:style|look))", c)
    if m and not re.search(rf"\bless\s+{m.group(1)}\b", c):
        d["colour"] = m.group(1)
    for n in re.findall(r"\b(?:wearing|wear|with|holding|hold)\s+((?:a|an|the)\s+[a-z]+(?:\s+[a-z]+)?)", c):
        d["notes"].append(("wearing " if re.search(r"\bwear", c) else "with ") + n.strip())
    d["matched"] = bool(d["style_id"] or d["size"] or d["colour"])
    return d


def describe(delta: dict) -> list[str]:
    """The change in the person's words, for the card: ["cartoonish style", "bigger", "in red tones"]."""
    out = []
    if delta.get("style_id"):
        out.append(f"{LABEL.get(delta['style_id'], delta['style_id'])} style")
    if delta.get("size"):
        out.append("bigger in its cell" if delta["size"] == "larger" else "smaller in its cell")
    if delta.get("colour"):
        out.append(COLOUR_MARK.format(c=delta["colour"]))
    out += delta.get("notes") or []
    return out


def _strip_old(desc: str) -> str:
    for s in SIZE_CLAUSE.values():
        desc = desc.replace(", " + s, "").replace(s, "")
    return re.sub(r",?\s*in (?:" + COLOURS + r") tones", "", desc).strip(" ,")


def apply(plan: dict, delta: dict, said: str = "", subject: str = "") -> dict:
    """A COPY of the stored plan with the change written in: the style id of the slots, the size / colour / extra words appended to the subject description (an earlier size or colour clause is
    replaced, never stacked), and the refinement recorded on the plan. The cells, tags and template stay: the new batch is the old prompt plus the change."""
    p = copy.deepcopy(plan)
    slots = p.setdefault("slots", {})
    desc = _strip_old(str(slots.get("subject_description") or subject or p.get("subject") or ""))
    if delta.get("style_id"):
        slots["style_id"] = delta["style_id"]
    if delta.get("size"):
        desc = f"{desc}, {SIZE_CLAUSE[delta['size']]}"
    if delta.get("colour"):
        desc = f"{desc}, {COLOUR_MARK.format(c=delta['colour'])}"
    for n in delta.get("notes") or []:
        if n not in desc:
            desc = f"{desc}, {n}"
    slots["subject_description"] = desc.strip(" ,")
    p.setdefault("refinements", []).append({"ts": round(time.time(), 3), "said": said[:300], "delta": {k: delta.get(k) for k in ("style_id", "size", "colour", "notes")}})
    for k in ("sheet_prompt", "video_prompt"):
        p.pop(k, None)
    return p


def style_of_request(text: str) -> tuple[str | None, str]:
    """A style named in a NEW request ("a teddy bear in clay style", "pixel art falcon", "watercolour cat"): (preset id | None, the text without the style words). The words never stay in the subject line
    while the Style line says something else (the audit found "a teddy bear in clay 3d style" planned as flat vector with the words in the subject)."""
    low = text
    for rx, sid in STYLE_WORDS:
        m = re.search(rf"\b(?:in|with|using|as)\s+(?:an?\s+|the\s+)?(?:{rx})\s*(?:style|look|version|art)?\b|\b(?:{rx})\s+(?:style|look|version)\b", low, re.I)
        if m:
            return sid, re.sub(r"\s{2,}", " ", (low[:m.start()] + " " + low[m.end():])).strip(" ,.")
    return None, text
