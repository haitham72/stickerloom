"""What an edit request MEANS, by rules (the UI/UX spec P11-P13). Pure: no model, no files, no network.

The agent used to treat "can you make him iron man?" as a NEW request (and mangle it into the subject "can you him iron man"), answered "can you rotate him?" with a refusal from the model, and sent
no picture with any edit. Every request about what is on screen now falls into exactly one of:

    route "editor"   a transformation or a cleaning of ONE slice (rotate, flip, crop, remove the stray lines, add a text): the image editor does it, nothing is generated, nothing is spent.
    route "regen"    the picture has to be drawn again, and how it is sent depends on which of three things the person means:
        case "tweak"      (a) I like the image, change a detail (move its hand, make him cry, her blonde, a hijab, remove the skyline, make it cartoonish, cuter):
                          the SHEET goes as the picture, with the parent's prompt and only that change.
        case "action"     (b) the same image, a new action ("now make him play football"): the shape absolutely stays, the action changes: the sheet goes as the picture, the prompt's actions change.
        case "redesign"   (c) the same subject and actions with another design ("nice, now make it as a lemon", "make him iron man"): the actions are approved, the design is replaced:
                          NO picture is sent, the parent's prompt is reused with the new subject.

`classify_edit` returns None for anything that is not an edit of what is open ("make me a falcon" is a new request). `plan_for` writes the case into a COPY of the parent's stored plan; the picture
and the sentence that tells the image model what to keep go with it (`REF_CLAUSES`). The subject of the turn ("iron man") is the subject the new batch is saved under, so the next turn knows it."""
from __future__ import annotations

import copy
import re

FILLER = re.compile(r"^(?:(?:can|could|would|will) you|please|pls|cool|nice|ok(?:ay)?|great|good|awesome|perfect|well|so|and|then|now|alright|hey|yeah|yes|thanks?|thank you|i want you to|i'd like you to|i would like you to|just)\b[\s,.!-]*", re.I)

# ---- the editor: a transformation or a cleaning of one slice
EDITOR = [
    ("rotate", r"\b(?:rotat\w*|tilt\w*|straighten\w*|turn\s+(?:him|her|it|them)\s+(?:around|upside\s*down|\d+))"),
    ("flip", r"\b(?:flip\w*|mirror\w*)"),
    ("crop", r"\b(?:crop\w*|trim\w*)"),
    ("clean", r"\b(?:clean\w*|tidy\w*)\b|\b(?:erase|remove|delete|get rid of|wipe(?: off)?)\s+(?:the\s+|these\s+|those\s+|all\s+|any\s+|some\s+)?(?:\w+\s+)?"
              r"(?:lines?|borders?|edges?|dots?|marks?|stains?|noise|artefacts?|artifacts?|separators?|watermarks?|text|background bits?|green bits?)\b"),
    ("text", r"\badd\s+(?:a\s+|some\s+)?(?:text|caption|words?|emoji|label)\b"),
]

# ---- words that decide between a detail, an action and a subject
EMOTIONS = r"(?:cry\w*|laugh\w*|smil\w*|happ\w*|sad\w*|angry|anger|mad|scared|afraid|shy|sleepy|tired|surprised|shocked|bored|proud|excited|calm|cute\w*|ugly|pretty|serious|funny|silly|crazy|lazy|nervous|jealous|confident|brave)"
COLOUR = r"(?:red|blue|green|yellow|pink|purple|orange|black|white|brown|gr[ae]y|gold\w*|silver|blond\w*|brunette|dark\w*|light\w*|pastel|neon|bright|pale|tan\w*)"
COMPARATIVE = r"(?:\w+er|more|less|bigger|smaller|taller|shorter|older|younger|slimmer|fatter|thinner)"
WEAR = r"(?:wear\w*|put on|hold\w*|carry\w*|have|has|hat|cap|coat|jacket|glasses|sunglasses|cape|crown|scarf|shoes|mask|beard|hijab|helmet|tie|suit|dress|shirt|gloves)"
ACTVERB = (r"(?:play\w*|run\w*|jump\w*|sleep\w*|sit\w*|stand\w*|danc\w*|fly\w*|flying|walk\w*|swim\w*|eat\w*|drink\w*|driv\w*|rid(?:e|ing)|fight\w*|kick\w*|throw\w*|catch\w*|read\w*|writ\w*|sing\w*|"
           r"cook\w*|wav\w*|hug\w*|kneel\w*|climb\w*|pos(?:e|ing)|spin\w*|lie|lying|sprint\w*|skat\w*|surf\w*|shoot\w*|box\w*|clap\w*|salut\w*|bow\w*|stretch\w*|exercis\w*|study\w*|paint\w*|div(?:e|ing)|"
           r"crawl\w*|hop\w*|march\w*|rac(?:e|ing)|bounc\w*|float\w*|hid(?:e|ing)|sneak\w*|work\w*|train\w*|meditat\w*)")
PERSON = r"(?:(?:the|that|this)\s+)?(?:(?:last|same)\s+)?(?:guy|man|woman|girl|boy|dude|lady|character|person)"      # "make the last guy happier": the character of the chat, like "him"
PRON = rf"(?:him|her|it|them|{PERSON})"
MAKEVERB = r"(?:make|turn|change|transform|convert|redo|remake|redesign|draw|do)"
EDITVERB = (r"(?:make|turn|change|put|give|add|remove|take off|take away|delete|replace|swap|move|raise|lower|open|close|shrink|enlarge|fix|redo|improve|adjust|tweak|set|let|get|dress|"
            r"lift|bend|point|show|hide|cover|colou?r|paint)")
POSSESSIVE = r"(?:his|her|its|their)"
NOT_AN_EDIT = re.compile(r"\b(?:make|create|give|draw|design|build|generate)\s+(?:me|us)\b|\b(?:i want|i need|i'd like)\b.{0,12}\b(?:\d+|a|an|some)\b|\b(?:\d+|three|four|five|six|several|many)\s+(?:\w+\s+){0,2}(?:stickers?|packs?|sets?)\b|"
                         r"\b(?:to|in|into)\s+(?:a|my|the)\s+(?:\w+\s+)?(?:pack|library|telegram)\b|\bpacks?\b|\btelegram\b|\bsend\b|\blike\s+(?:number\s+|no\.?\s*|#|s)?\d+\b|\bsame (?:as|look)\b")


# ---- what is left of a sentence once the thing it points at is taken out -----------------------------------------------------------------------------------------------
_ORD = r"(?:last|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|\d{1,2}(?:st|nd|rd|th))"
_POINT = (rf"(?:(?:on|in|for|of|to)\s+)?(?:(?:the\s+)?{_ORD}\s+(?:one|sticker|image|picture|pic|cell|card|tile)\b"
          r"|(?:number|no\.?|cell|slice|sticker|#)\s*(?:\d{1,3}|one|two|three|four|five|six|seven|eight|nine)\b|\bg\d{1,4}\s*/?\s*s\d\b|\bs\d\b)")
LEAD = rf"^(?:{MAKEVERB}|let|get|have)\s+"


def delta_of(text: str) -> str:
    """The change a sentence asks for, with what it points at taken out and nothing else touched: "make number 3 wear a hat" -> "wear a hat", "make him wear a hat" -> "wear a hat", "make the last one happier" ->
    "happier", "make 3 wear a hat and a scarf" -> "wear a hat and a scarf". Words such as a, the, and, it are the person's own and stay ("wear a hat" is never "wear hat")."""
    t = _norm(text)
    if not re.match(rf"(?:{EDITVERB}|redo|regenerate)\b", t):                  # "I like 2 but make 5 happier": the clause that asks for the change
        clauses = [c.strip(" ,") for c in re.split(r"\bbut\b|;|\.\s", t) if re.match(rf"\s*(?:{EDITVERB}|redo|regenerate)\b", c.strip())]
        t = clauses[-1] if clauses else t
    t = re.sub(_POINT, "§", t)
    t = re.sub(rf"{LEAD}(?:{PRON}|these|those|§|\d{{1,2}}(?:\s*(?:,|and|&)\s*\d{{1,2}})*)\s+", "", t, count=1)
    t = re.sub(r"\b(?:like|match|the same as|similar to)\s+(?:number\s+|no\.?\s*|#|s)?\d{1,2}\b", " ", t)               # "make 5 like 2": the reference is not part of the change
    t = re.sub(r"^\d{1,2}(?:\s*(?:,|and|&)\s*\d{1,2})*\s+", "", t.replace("§", " ").strip())
    return " ".join(t.split()).strip(" ,.;:")


def fresh_take(delta: str) -> bool:
    """True when the "change" is only a verb and the numbers it points at ("redo 3", "regenerate 2 and 5"): a new drawing of the same prompt, nothing added to it."""
    return not delta.strip() or bool(re.fullmatch(r"(?:redo|regenerate|remake|retry|change|fix|improve|replace|swap|do)(?:\s+(?:#?\d{1,2}|and|,|&|it|them|these|those|number|no\.?|sticker|stickers|again))*", delta.strip().lower()))


def add_to_label(plan: dict, delta: str) -> dict:
    """A COPY of a (1x1) plan with the change appended to the cell's label ("pose 3" -> "pose 3, wear a hat"): the prompt the template builds carries it, next to the parent's own wording. The rendered prompts are dropped (rebuilt from the slots)."""
    p = copy.deepcopy(plan)
    for c in (p.get("slots") or {}).get("cells") or []:
        old = str(c.get("label") or "").strip()
        c["label"] = f"{old}, {delta}" if old and delta.lower() not in old.lower() else (old or delta)
    p.setdefault("edits", []).append({"case": "tweak", "delta": delta})
    for k in ("sheet_prompt", "video_prompt"):
        p.pop(k, None)
    for st in p.get("stickers") or []:
        st.pop("prompt", None)
    return p


def _norm(text: str) -> str:
    t = " ".join(str(text or "").lower().replace("’", "'").split())
    t = re.sub(r"[?!.]+$", "", t).strip(" ,;:")
    prev = None
    while prev != t:                                                    # "cool now make it cuter" -> "make it cuter"
        prev = t
        t = FILLER.sub("", t).strip(" ,;:")
    return t


def _subject_like(rest: str) -> bool:
    """Is what follows "make him" a SUBJECT ("iron man", "a princess") and not a detail ("angry", "red", "happier", "wear a hat") or an action ("sleep")?"""
    r = re.sub(r"^(?:an?|the)\s+", "", rest.strip())
    words = r.split()
    if not 1 <= len(words) <= 4 or re.search(r"\d", r):
        return False
    first = words[0]
    from .refine import STYLE_WORDS
    if any(re.fullmatch(rx, first) for rx, _ in STYLE_WORDS) or re.search(r"(?:ish|ful|less|ous|ive|able)$", first) or first in ("simple", "detailed", "bold", "soft", "sharp", "shiny", "matte", "sketchy", "thick", "thin", "fluffy", "smooth", "rough"):
        return False                                                   # a style or a quality is a detail ("make it cartoonish"), not a subject
    if re.fullmatch(EMOTIONS, first) or re.fullmatch(COLOUR, first) or re.fullmatch(ACTVERB, first) or re.fullmatch(WEAR, first):
        return False
    if re.fullmatch(COMPARATIVE, first) and first not in ("super", "spider", "water", "silver", "tiger", "butter", "ginger", "pepper", "monster", "hunter", "baker", "runner", "dancer", "singer", "driver", "teacher",
                                                            "soldier", "robber", "farmer", "fighter", "player", "sailor", "writer", "painter", "doctor", "officer", "winter", "summer", "dinosaur"):
        return False
    return first not in ("a", "an", "the", "very", "too", "so", "much", "slightly", "bit", "little", "really", "way", "less", "more", "different", "same", "bigger", "smaller")


def unsupported(text: str) -> str | None:
    """A request the chat cannot do yet, said plainly (P13): "multiple packs of the same character" needs the burst feature (many packs from one liked sheet), which is not built. None otherwise."""
    t = _norm(text)
    if re.search(r"\b(?:multiple|many|several|more|\d+|few)\s+(?:different\s+)?(?:packs?|sets?)\b.{0,40}\b(?:same|this|that|one)\s+(?:character|guy|girl|man|woman|subject|person|hero|one)\b|"
                 r"\b(?:same|this|that)\s+(?:character|guy|girl|man|woman|subject|person|hero)\b.{0,40}\b(?:multiple|many|several|\d+)\s+(?:packs?|sets?)\b", t):
        return ("Many packs of the same character from one sheet is not supported yet. It is the next feature (burst creation: the same liked sheet, several actions, one queue each, "
                "each pack named after its subject and action). For now I can redo this pack with a new action (\"now make him play football\") or a new design (\"make it as a lemon\").")
    return None


def classify_edit(text: str) -> dict | None:
    """{route, case, ops, subject, action, delta} for a request about what is open, or None (a new request, a question, small talk). See the module doc for the cases."""
    t = _norm(text)
    from .resolver import display_request, DESCRIBE
    if not t or NOT_AN_EDIT.search(t) or display_request(t) or re.search(DESCRIBE, t):
        return None
    replacement = re.match(r"^(?:replace|swap)\s+(.+?)\s+(?:with|for)\s+(.+)$", t)
    if replacement and re.fullmatch(rf"(?:{PRON}|{_ORD}(?:\s+(?:one|sticker))?|#?\d+|number\s+\d+|g\d+/s\d|s\d|the .+ one)", replacement[1]):
        subject = re.sub(r"^(?:an?|the)\s+", "", replacement[2]).strip()
        if _subject_like(subject):
            return {"route": "regen", "case": "redesign", "ops": [], "subject": subject, "action": None, "delta": None}
    if re.search(r"\b(?:style|pose|expression|face|colou?r|composition|subject|character|animation|motion)\s+(?:from|of)\s+(?:the\s+)?(?:number\s+|#|s)?(?:\d|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|last)\b", t):
        return None                                  # the numbered-reference edit node binds each image to its requested role
    ops = [name for name, rx in EDITOR if re.search(rx, t)]
    if ops:
        return {"route": "editor", "case": None, "ops": ops, "subject": None, "action": None, "delta": None}
    m = re.match(rf"^{MAKEVERB}\s+(?:{PRON}|everything|them all|all of them|everyone)\s+(?:into|as|to be|to|like)\s+(?:an?\s+|the\s+)?(?P<s>[a-z][a-z0-9' -]*)$", t) \
        or re.match(rf"^{MAKEVERB}\s+{PRON}\s+(?:an?\s+|the\s+)?(?P<s>[a-z][a-z0-9' -]*)$", t)
    if m and _subject_like(m.group("s")):
        return {"route": "regen", "case": "redesign", "ops": [], "subject": re.sub(r"^(?:an?|the)\s+", "", m.group("s")).strip(), "action": None, "delta": None}
    a = re.match(rf"^(?:make|let|have|get)\s+(?:{PRON}|the \w+)\s+(?P<a>{ACTVERB}\b.*)$", t) or re.match(rf"^(?:he|she|it|they)\s+(?:should|must|can|will|needs? to)\s+(?P<a>{ACTVERB}\b.*)$", t) \
        or re.match(rf"^(?:show|draw|put)\s+{PRON}\s+(?P<a>{ACTVERB}\b.*)$", t)
    if a:
        return {"route": "regen", "case": "action", "ops": [], "subject": None, "action": a.group("a").strip(), "delta": None}
    if re.match(rf"^{EDITVERB}\b", t) and (re.search(rf"\b{PRON}\b|\b{POSSESSIVE}\b|\b(?:number|no\.?|cell|slice|sticker|#)\s*\d+|\bs\d\b", t) or re.search(_POINT, t)
                                          or re.match(rf"{LEAD}\d{{1,2}}\s+\S", t) or re.match(r"^(?:add|remove|put|take off|take away|delete|replace|swap)\b", t)):
        d = delta_of(t)
        if re.fullmatch(r"(?:redo|regenerate|remake|redesign|retry|fix|improve|change|do|make)(?:\s+(?:it|them|this|that))?", d or "redo") and (re.search(_POINT, t) or re.search(r"\b\d{1,2}\b", t)):
            return None                                                # a sticker pointed at and no change named ("redo 3"): the sticker edit says "a fresh take"
        return {"route": "regen", "case": "tweak", "ops": [], "subject": None, "action": None, "delta": d or t}
    return None


def clear_change(text: str, er: dict) -> bool:
    """Conservative fallback when semantic interpretation is unavailable. Unknown changes need a question."""
    t = _norm(text)
    if er.get("route") == "editor":
        return True
    change = er.get("delta") or er.get("action") or er.get("subject") or ""
    if re.search(r"\b(?:and|then|also)\b", change):
        # Each extra clause must mean something; never drop an unknown tail from a mixed request.
        clauses = re.split(r"\b(?:and|then|also)\b", change)
        if any(not re.search(rf"\b(?:{EMOTIONS}|{COLOUR}|{WEAR}|{ACTVERB})\b", c) for c in clauses):
            return False
    if er.get("case") == "redesign":
        return bool(re.match(r"^(?:replace|swap)\b.*\b(?:with|for)\s+\S|^(?:make|turn|change|transform|convert|redesign)\b", t) and _subject_like(change))
    return bool(re.search(rf"\b(?:{EMOTIONS}|{COLOUR}|{WEAR}|{ACTVERB})\b", change)
                or re.search(r"\b(?:bigger|smaller|taller|shorter|more|less|cartoonish|realistic|outline|skyline|background|hand|eyes|mouth)\b", change))


# ---- writing a case into a copy of the parent's stored plan ----------------------------------------------------------------------------------------------------------
REF_CLAUSES = {
    "tweak": ("Reference: the attached image is the sheet to keep. Keep every character exactly as drawn: the same design, colours, proportions and the same pose in every cell. "
              "Apply only this change: {what}."),
    "like": ("Reference: the attached image is the look to match. Draw the character of this prompt in exactly that design, colours, proportions and style; the pose and the expression come from the prompt."),
    "action": ("Reference: the attached image is the sheet to keep. Keep every character's shape, design, colours and proportions exactly; the shape absolutely stays. "
               "Change only what each character is doing, to: {what}."),
}


def reference_clause(case: str, what: str, slice_: bool = False) -> str | None:
    """The sentence that tells the image model what the attached picture is for (the sheet, or one slice of it). None for a redesign: nothing is attached."""
    if case not in REF_CLAUSES or (not what and case != "like"):
        return None
    text = REF_CLAUSES[case].format(what=what.strip(" ."))
    return text.replace("the attached image is the sheet to keep", "the attached image is the character to keep").replace("every character", "the character") if slice_ else text


def sends_image(case: str) -> bool:
    """(a) and (b) send the sheet; (c) sends no picture."""
    return case in ("tweak", "action")


def reference_roles_clause(references: list[dict], change: str = "") -> str:
    """Bind each attached image's position to the explicitly requested role."""
    if len(references) == 1 and references[0]["role"] == "STYLE":
        return reference_clause("like", "") + (f" Also apply this change: {change}." if change else "")
    roles = {"STYLE": "design, colours, proportions and style", "POSE": "pose only", "EXPRESSION": "facial expression only",
             "COLOR": "colours only", "COMPOSITION": "composition only", "SUBJECT": "character identity only", "ANIMATION": "motion only"}
    clauses = [f"Reference image {i}: use its {roles.get(r['role'], r['role'].lower())} (from {r['source']})."
               for i, r in enumerate(references, 1)]
    clauses.append("Keep the other details from the prompt.")
    if change:
        clauses.append(f"Apply this change: {change}.")
    return " ".join(clauses)


def plan_for(plan: dict, case: str, *, subject: str | None = None, action: str | None = None, delta: str | None = None, said: str = "") -> dict:
    """A COPY of the parent's stored plan with the case written in; the cells, tags and template stay (the new sheet is the old prompt plus the one change), and a sticker keeps its place, so its S#.
    tweak: the plan is unchanged (the change travels in the reference clause, next to the picture); action: every cell's label gets the new action in front of its old pose;
    redesign: the subject is replaced everywhere it is named (the description, the folder subject, the keys and first tags) and the actions stay. The prompts are rebuilt by the template."""
    from ..generation import prompter
    p = copy.deepcopy(plan)
    slots = p.setdefault("slots", {})
    if case == "action" and action:
        for c in slots.get("cells") or []:
            old = str(c.get("label") or "").strip()
            c["label"] = f"{action}, {old}" if old and action.lower() not in old.lower() else (old or action)
    elif case == "redesign" and subject:
        old_slug, new_slug = prompter.slug(str(p.get("subject") or "")), prompter.slug(subject) or "subject"
        slots["subject_description"] = subject
        p["subject"], p["task"], p["task_slug"] = subject, subject, new_slug

        def rekey(key: str) -> str:
            key = str(key or "")
            return new_slug + key[len(old_slug):] if old_slug and key.startswith(old_slug + "_") else (f"{new_slug}_{key}" if key else new_slug)
        for s in p.get("stickers") or []:
            was = s.get("key")
            s["key"] = rekey(was)
            if s.get("tags") and s["tags"][0] == was:
                s["tags"] = [s["key"], *s["tags"][1:]]
        for c in slots.get("cells") or []:
            if c.get("tags") and c.get("label"):
                key = next((x.get("key") for x in p.get("stickers") or [] if x.get("index") == c.get("pos")), None)
                if key:
                    c["tags"] = [key, *[t for t in c["tags"][1:] if t != key]]
    p.setdefault("edits", []).append({"case": case, "subject": subject, "action": action, "delta": delta, "said": said[:300]})
    for k in ("sheet_prompt", "video_prompt"):
        p.pop(k, None)
    return p
