"""Reactions HTTP schemas."""
from __future__ import annotations
from typing import Any
from pydantic import BaseModel, ConfigDict


class LottieReactionBody(BaseModel):
    """POST /api/particles/lottie-reactions/{file} (save) and .../{file}/preview (the viewer): the editor's settings and morph targets. Keys and
    numbers are checked by flow/lottie_reactions.py (one list of settings, one 400 wording); left out = the saved values."""
    model_config = ConfigDict(extra="forbid")
    settings: dict[str, Any] | None = None
    morph_targets: list[str] | None = None


class SuperReactionBody(BaseModel):
    """POST /api/particles/super: one dropped Lottie (`file`, a name from GET /api/particles/super) played over a screen of `aspect` = width / height.
    glow: auto | solid (color) | gradient (colors = [bottom, top]) | none. settings: count, rise, float, sway, spread, glow_strength, pop
    (flow/lottie_reactions.py super_compose; engine/super_reaction.py checks them)."""
    model_config = ConfigDict(extra="forbid")
    file: str
    aspect: float
    glow: str = "auto"
    color: str | None = None
    colors: list[str] | None = None
    settings: dict[str, Any] | None = None



class SuperExportBody(BaseModel):
    """POST /api/particles/super/export.zip: every file's saved versions (an unsaved file: its random look), for every screen bucket."""
    model_config = ConfigDict(extra="forbid")
    files: list[str] | None = None


class SuperRandomBody(BaseModel):
    """POST /api/particles/super/random: Shuffle, the random look of `file` for `seed`."""
    model_config = ConfigDict(extra="forbid")
    file: str
    seed: int


class SuperVersionBody(BaseModel):
    """POST /api/particles/super/versions (Save as; `file` required) and .../versions/{id} (Save). look = {settings, glow, color?, colors?, seed?},
    checked by flow/super_reactions.py clean_look."""
    model_config = ConfigDict(extra="forbid")
    file: str | None = None
    name: str | None = None
    look: dict[str, Any] | None = None


class SuperPresetBody(BaseModel):
    """POST /api/particles/super/presets: Save as preset."""
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    look: dict[str, Any]
