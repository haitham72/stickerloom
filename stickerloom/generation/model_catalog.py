"""The models a user can pick, and the selections each one offers. Pure data + validation: the UI draws it, the fulfiller (jobs.fulfil) calls
`resolve` to turn a selection into a provider's model id and params. Anything not listed here cannot be requested.
Per provider (`STICKERLOOM_PROVIDER`, default `higgsfield`): IMAGE/VIDEO stay the higgsfield lists under their old
names; entries carry no vendor logos (neutral text badges in the UI). The standing rules live here: images default
to Nano Banana 2 at 2k; animation is always 1080 or more (Kling pro, Grok 1080p), and Kling never offers `4k`."""
from __future__ import annotations

import os

DEFAULT_IMAGE = "nano_banana_flash"
DEFAULT_VIDEO = "kling3_0"


def _opt(name, label, choices, default, hint=""):
    return {"name": name, "label": label, "choices": [str(c) for c in choices], "default": str(default), "hint": hint}


IMAGE = [
    {"id": "nano_banana_flash", "label": "Nano Banana 2", "default": True,
     "note": "Fast and sharp; stickerloom's default for sheets.",
     "fixed": {"aspect_ratio": "1:1"}, "options": [_opt("resolution", "Resolution", ["1k", "2k", "4k"], "2k")]},
    {"id": "nano_banana_pro", "label": "Nano Banana Pro",
     "note": "Slower, steadier backgrounds.",
     "fixed": {"aspect_ratio": "1:1"}, "options": [_opt("resolution", "Resolution", ["1k", "2k", "4k"], "2k")]},
    {"id": "nano_banana_2_lite", "label": "Nano Banana 2 Lite",
     "note": "Cheapest Nano Banana; 1k only.",
     "fixed": {"aspect_ratio": "1:1", "resolution": "1k"}, "options": [_opt("thinking", "Thinking", ["MINIMAL", "HIGH"], "HIGH")]},
    {"id": "gpt_image_2", "label": "GPT Image 2",
     "note": "",
     "fixed": {"aspect_ratio": "1:1"}, "options": [_opt("quality", "Quality", ["low", "medium", "high"], "high"),
                                                   _opt("resolution", "Resolution", ["1k", "2k", "4k"], "2k")]},
    {"id": "gpt_image_2_5", "label": "GPT Image 2.5",
     "note": "Two variants: flare and sunburst.",
     "fixed": {"aspect_ratio": "1:1"}, "options": [_opt("variant", "Variant", ["flare", "sunburst"], "flare"),
                                                   _opt("quality", "Quality", ["low", "medium", "high", "xhigh", "max"], "medium"),
                                                   _opt("resolution", "Resolution", ["1k", "2k", "4k"], "2k")]},
    {"id": "seedream_v5_pro", "label": "Seedream 5.0 Pro",
     "note": "",
     "fixed": {"aspect_ratio": "1:1"}, "options": [_opt("resolution", "Resolution", ["1k", "1.5k", "2k"], "2k")]},
    {"id": "seedream_5_0_flash", "label": "Seedream 5.0 Flash",
     "note": "",
     "fixed": {"aspect_ratio": "1:1"}, "options": [_opt("resolution", "Resolution", ["1k", "1.5k", "2k"], "2k")]},
    {"id": "seedream_v5_lite", "label": "Seedream 5.0 Lite",
     "note": "",
     "fixed": {"aspect_ratio": "1:1"}, "options": [_opt("quality", "Quality", ["basic", "high"], "high")]},
]

VIDEO = [
    {"id": "kling3_0", "label": "Kling v3.0", "default": True, "end_image": True,
     "note": "With Loop on, the start image is also the end image. Always pro: 1440 px for a square sheet (std is 960 px and looks pixelated); the 4k mode is never offered.",
     "fixed": {"aspect_ratio": "1:1", "sound": "off"},
     "options": [_opt("mode", "Quality", ["pro"], "pro", "pro: 1440 px"),
                 _opt("duration", "Seconds", [3, 5], 3)]},
    {"id": "grok_video_v15", "label": "Grok Video 1.5", "end_image": False,
     "note": "Starts from the sheet; no end image, so the loop is not guaranteed. Costs several times more than Kling.",
     "fixed": {},
     "options": [_opt("resolution", "Resolution", ["1080p"], "1080p"),
                 _opt("duration", "Seconds", [3, 5], 3)]},
    {"id": "grok_video_v15_lite", "label": "Grok Imagine 1.5 Lite", "end_image": False,
     "note": "Starts from the sheet; no end image, so the loop is not guaranteed. Half the price of Grok Video 1.5 (12 credits for 3 s at 1080p), still more than Kling.",
     "fixed": {"aspect_ratio": "1:1"},
     "options": [_opt("resolution", "Resolution", ["1080p"], "1080p"),
                 _opt("duration", "Seconds", [3, 5], 3)]},
]

FAKE_IMAGE = [
    {"id": "fake_sheet", "label": "Fake sheet", "default": True,
     "note": "Deterministic offline renderer: coloured shapes on a flat chroma sheet. No account, no credits.",
     "fixed": {"aspect_ratio": "1:1"}, "options": [_opt("resolution", "Resolution", ["1k", "2k"], "1k")]},
]

FAKE_VIDEO = [
    {"id": "fake_video", "label": "Fake video", "default": True, "end_image": False,
     "note": "Deterministic offline renderer: a short seamless loop of the start image. No account, no credits.",
     "fixed": {"aspect_ratio": "1:1"},
     "options": [_opt("duration", "Seconds", [3, 5], 3)]},
]

OPENAI_IMAGE = [
    {"id": "gpt_image_1", "label": "GPT Image 1 (experimental)", "default": True,
     "note": "Billed to your own OpenAI key; stills only.",
     "fixed": {"aspect_ratio": "1:1"}, "options": []},
]

CATALOGS = {
    "higgsfield": {"image": IMAGE, "video": VIDEO, "defaults": {"image": DEFAULT_IMAGE, "video": DEFAULT_VIDEO}},
    "fake": {"image": FAKE_IMAGE, "video": FAKE_VIDEO, "defaults": {"image": "fake_sheet", "video": "fake_video"}},
    "openai": {"image": OPENAI_IMAGE, "video": [], "defaults": {"image": "gpt_image_1", "video": "gpt_image_1"}},
}

for _m in IMAGE:
    _m.setdefault("refs", True)        # all eight accept image_references (checked against `model get`, 2026-10-01)
for _m in VIDEO:
    _m.setdefault("refs", False)
FAKE_IMAGE[0].setdefault("refs", True)     # accepted and ignored by the renderer
FAKE_VIDEO[0].setdefault("refs", False)
OPENAI_IMAGE[0].setdefault("refs", False)
KINDS = {"image": IMAGE, "video": VIDEO}


class CatalogError(ValueError):
    pass


def provider_name(provider: str | None = None) -> str:
    """The catalog to read: the explicit provider, else STICKERLOOM_PROVIDER, else higgsfield."""
    name = (provider if provider is not None else os.environ.get("STICKERLOOM_PROVIDER", "higgsfield")).strip().lower()
    if name not in CATALOGS:
        raise CatalogError(f"unknown provider '{name}' (STICKERLOOM_PROVIDER=fake|higgsfield|openai)")
    return name


def _entries(kind: str, provider: str | None) -> tuple[list[dict], dict]:
    name = provider_name(provider)
    cat = CATALOGS[name]
    items = cat.get(kind)
    if items is None:
        raise CatalogError(f"kind must be image or video, not '{kind}'")
    return items, cat["defaults"]


_DUMP: dict | None = None          # the full Higgsfield list (higgsfield.load_models), set by the server; None = curated models only
SKIP_PARAMS = {"prompt", "image_references", "video_references", "audio_references", "start_image", "end_image", "mask", "is_inpaint",
               "width", "height", "input_image", "input_images", "medias", "reference_elements", "batch_size", "application", "surface"}


def set_dump(d: dict | None) -> None:
    global _DUMP
    _DUMP = d


def generic(kind: str, d: dict) -> dict:
    """A catalog entry built from a model's own parameter list: every enum parameter becomes a drop-down; the aspect ratio is 1:1 when allowed."""
    fixed, options = {}, []
    names = {p["name"] for p in d.get("params", [])}
    for p in d.get("params", []):
        n, enum = p["name"], p.get("enum")
        if n in SKIP_PARAMS or p.get("type") not in ("string", "integer", "boolean", "string|null"):
            continue
        if n == "aspect_ratio":
            if enum and "1:1" in enum:
                fixed[n] = "1:1"
            continue
        if enum:
            default = p.get("default")
            options.append(_opt(n, n.replace("_", " ").capitalize(), enum, default if default is not None and str(default) in map(str, enum) else enum[0]))
    return {"id": d["job_type"], "label": d.get("display_name") or d["job_type"], "generic": True,
            "end_image": "end_image" in names, "refs": "image_references" in names and kind == "image", "note": "", "fixed": fixed, "options": options}


def more(kind: str, provider: str | None = None) -> list[dict]:
    """Every dumped model of this kind that is not in the curated list (higgsfield only)."""
    if provider_name(provider) != "higgsfield" or not _DUMP:
        return []
    have = {m["id"] for m in KINDS[kind]}
    seen, out = set(), []
    for d in _DUMP.get(kind, []):
        if d["job_type"] in have or d["job_type"] in seen or d.get("error"):
            continue
        seen.add(d["job_type"])
        out.append(generic(kind, d))
    return sorted(out, key=lambda m: m["label"].lower())


def catalog(provider: str | None = None) -> dict:
    name = provider_name(provider)
    items = CATALOGS[name]
    return {"provider": name, "image": items["image"], "video": items["video"],
            "more": {"image": more("image", name), "video": more("video", name)},
            "defaults": items["defaults"], "full_list": bool(_DUMP) and name == "higgsfield",
            "counts": (_DUMP or {}).get("counts", {}) if name == "higgsfield" else {}}


def find(kind: str, model_id: str | None, provider: str | None = None) -> dict:
    items, defaults = _entries(kind, provider)
    mid = model_id or defaults[kind]
    m = next((x for x in items if x["id"] == mid), None) or next((x for x in more(kind, provider) if x["id"] == mid), None)
    if not m:
        raise CatalogError(f"'{mid}' is not an available {kind} model")
    return m


def resolve(kind: str, model_id: str | None, options: dict | None = None, provider: str | None = None) -> tuple[str, dict]:
    """(model id, params) for a selection. Unknown options and values outside the model's choices are refused."""
    m = find(kind, model_id, provider)
    options = dict(options or {})
    params = dict(m["fixed"])
    for o in m["options"]:
        v = str(options.pop(o["name"], o["default"]))
        if v not in o["choices"]:
            raise CatalogError(f"{m['label']}: {o['label'].lower()} '{v}' is not available (choose {', '.join(o['choices'])})")
        params[o["name"]] = v
    if options:
        raise CatalogError(f"{m['label']} has no option '{', '.join(sorted(options))}'")
    return m["id"], params


FAMILY_WORDS = {"nano": ("nano", "banana"), "gpt": ("gpt",), "seedream": ("seedream",), "kling": ("kling",), "grok": ("grok",)}
NAMED_RX = r"(?:with|using|use|in|on|via|by|through)\s+(?:the\s+)?"


def named_in(text: str | None) -> dict:
    """The models a chat message names: {kind: model id} for each kind whose model family follows
    "with / using / in / on / via / by / through" ("animate with grok", "use nano banana pro"). Inside a family the model whose label words the message has
    most wins, the catalog order breaking ties ("grok lite" -> Grok Imagine 1.5 Lite, "grok" -> Grok Video 1.5). Pure: the catalog only."""
    import re
    low = " " + re.sub(r"[^a-z0-9.]+", " ", str(text or "").lower()) + " "
    words = set(low.split())
    out = {}
    for kind in ("image", "video"):
        best = None
        for i, m in enumerate(KINDS[kind] + more(kind)):
            fam = next((f for f, w in FAMILY_WORDS.items() if m["id"].startswith(f) or m["label"].lower().startswith(w[0])), None)
            if not fam or not re.search(NAMED_RX + re.escape(FAMILY_WORDS[fam][0]) + r"\b", low):
                continue
            label = set(re.sub(r"[^a-z0-9.]+", " ", m["label"].lower()).split()) - set(FAMILY_WORDS[fam])
            score = len(label & words)
            if best is None or score > best[0]:
                best = (score, i, m["id"])
        if best:
            out[kind] = best[2]
    return out
