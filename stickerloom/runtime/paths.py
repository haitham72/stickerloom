"""Path config. Cross-platform (pathlib only). Override with env vars STICKERLOOM_INPUT / STICKERLOOM_OUT."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent.parent   # the repo root (the folder with pyproject.toml, inputs/ and out/)
REPO = PROJECT


def input_root() -> Path:
    """The folder that holds Images_gen/ and videos_gen/ (the watch folders). `inputs/` in the repo. STICKERLOOM_INPUT overrides."""
    env = os.environ.get("STICKERLOOM_INPUT")
    if env:
        return Path(env)
    return REPO / "inputs"


def out_root() -> Path:
    return Path(os.environ.get("STICKERLOOM_OUT", PROJECT / "out"))
