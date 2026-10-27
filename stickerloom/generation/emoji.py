"""Offline Unicode 15.1 emoji sequences, with complete cluster extraction.

The checked-in data is the fully-qualified rows of Unicode emoji-test.txt. This
validates actual tone/ZWJ combinations rather than accepting a valid prefix.
"""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path

_DATA = json.loads(Path(__file__).with_name("emoji_sequences.json").read_text(encoding="utf-8"))
VERSION = _DATA["unicode_version"]
SEQUENCES = frozenset(_DATA["sequences"])


def first_emoji(text: str | None) -> str:
    """Extract the first cluster; extraction alone does not establish validity."""
    s = str(text or "")
    if not s:
        return ""
    i = 1
    if 0x1F1E6 <= ord(s[0]) <= 0x1F1FF and i < len(s) and 0x1F1E6 <= ord(s[i]) <= 0x1F1FF:
        i += 1
    while i < len(s):
        o = ord(s[i])
        if o == 0x200D:
            # Keep an incomplete joiner too: exact validation must reject it.
            i = min(len(s), i + 2)
        elif o in (0xFE0E, 0xFE0F, 0x20E3) or 0x1F3FB <= o <= 0x1F3FF or 0xE0020 <= o <= 0xE007F or unicodedata.combining(s[i]):
            i += 1
        else:
            break
    return s[:i]


def split_emoji(text: str | None) -> list[str]:
    out, rest = [], str(text or "")
    while rest:
        cluster = first_emoji(rest)
        out.append(cluster)
        rest = rest[len(cluster):]
    return out


def valid(glyph: str) -> bool:
    return glyph in SEQUENCES


def base(glyph: str) -> str:
    """Only for bank associations after exact validation; keep ZWJ/flag structure."""
    return "".join(c for c in glyph if not 0x1F3FB <= ord(c) <= 0x1F3FF).replace("\ufe0f", "")
