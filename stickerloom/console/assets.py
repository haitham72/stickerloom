"""Allowlisted UI sources and stable browser URLs, backed by one manifest."""
from __future__ import annotations

import json
from pathlib import Path

UI_ROOT = Path(__file__).parent / "ui"
ASSETS = json.loads((UI_ROOT / "manifest.json").read_text(encoding="utf-8"))


def _content_type(name):
    return {".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2"}[Path(name).suffix]


# Public legacy names and canonical source names address the exact same file.
UI_FILES = {name: _content_type(name) for name in ASSETS}
UI_FILES.update({target: _content_type(name) for name, target in ASSETS.items()})
_PATHS = {name: UI_ROOT / target for name, target in ASSETS.items()}
_PATHS.update({target: UI_ROOT / target for target in ASSETS.values()})


def asset_path(name: str) -> Path:
    """Resolve an allowlisted key only; unknown paths never reach the filesystem."""
    return _PATHS[name]


def script_sources() -> dict[str, str]:
    """First-party scripts under their stable names, excluding vendor code."""
    return {name: asset_path(name).read_text(encoding="utf-8")
            for name in ASSETS if name.endswith(".js") and not name.startswith("vendor/")}
