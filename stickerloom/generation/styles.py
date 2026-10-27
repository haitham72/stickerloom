"""Style presets: what the user picks next to the subject. `phrase` is the text the saved templates plug into the prompt (the word
"sticker" is deliberately never used: image models answer it with a white die-cut border). `hint` is the short line under the tile in the UI.
A tile image is optional: drop <id>.png/.jpg/.webp/.svg into stickerloom/console/assets/styles/ and it replaces the placeholder (an abstract swatch, console/placeholders.py:
any preset added here gets one without further work). The Studio reads this list from the API, so a new preset appears there with no UI change."""
from __future__ import annotations

PRESETS = [
    {"id": "flat_vector", "label": "Flat",
     "phrase": "flat vector illustration, bold clean shapes, solid vibrant colours, friendly proportions",
     "hint": "bold shapes, solid colours"},
    {"id": "toon_shade", "label": "Toon shade",
     "phrase": "cel-shaded toon style, crisp two-tone shading, clean colour blocks, a soft rim light, expressive cartoon features",
     "hint": "cartoon cel shading"},
    {"id": "glossy_3d", "label": "Glossy 3D",
     "phrase": "glossy 3D render, smooth rounded shapes, shiny plastic-like material, soft studio lighting with bright reflections",
     "hint": "shiny, polished 3D"},
    {"id": "clay_3d", "label": "Soft clay 3D",
     "phrase": "soft 3D clay render, chunky rounded forms, matte material, gentle studio lighting, charming handmade feel",
     "hint": "matte, handmade 3D"},
    {"id": "realistic", "label": "Realistic",
     "phrase": "photorealistic character, natural material and fur texture, realistic proportions and lighting, expressive face",
     "hint": "lifelike texture and light"},
    {"id": "hand_drawn", "label": "Hand-drawn",
     "phrase": "hand-drawn illustration, marker and pencil texture, wobbly confident lines, warm playful colours",
     "hint": "marker and pencil"},
    {"id": "minimal", "label": "Minimal",
     "phrase": "minimal illustration, a few simple shapes, one or two flat colours, generous empty space, thin clean outline, calm and modern",
     "hint": "few shapes, calm colours"},
    {"id": "pixel_art", "label": "Pixel art",
     "phrase": "16-bit pixel art character, crisp square pixels, limited colour palette, clean dark outline, retro game sprite look",
     "hint": "retro game sprites"},
    {"id": "watercolor", "label": "Watercolour",
     "phrase": "soft watercolour illustration, translucent washes of colour, gentle bleeding edges, visible paper grain, delicate ink outline",
     "hint": "soft washes, paper grain"},
    {"id": "paper_cut", "label": "Paper cut",
     "phrase": "layered paper cut-out craft, flat pieces of coloured paper stacked with soft shadows between the layers, slightly rough cut edges",
     "hint": "layered paper craft"},
    {"id": "pop_comic", "label": "Pop comic",
     "phrase": "pop-art comic style, bold black ink outlines, halftone dot shading, saturated primary colours, dynamic expressive poses",
     "hint": "bold ink, halftone dots"},
    {"id": "kawaii", "label": "Kawaii",
     "phrase": "kawaii chibi style, big shiny eyes, tiny body and an oversized head, pastel colours, soft blush cheeks, very cute and rounded",
     "hint": "cute, round, pastel"},
]
DEFAULT = "flat_vector"
PHRASE = {p["id"]: p["phrase"] for p in PRESETS}
# the wording the v1 templates used; kept so plans saved with template_version 1 rebuild to exactly the same prompt
LEGACY_V1 = "flat vector sticker illustration, bold clean shapes, vibrant colors, friendly proportions"


def phrase(style_id: str | None, version: int = 2) -> str:
    if int(version) < 2:
        return LEGACY_V1
    return PHRASE.get(style_id or DEFAULT, PHRASE[DEFAULT])
