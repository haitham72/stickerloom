"""Per-emoji reaction motions: every predefined emoji owns one animation.

New rule: a predefined emoji's animation is fixed
globally (the `1800+` bank), completely separate from a pack's own preset
variations. A pack inherits the motion 1:1 and varies only sprite art +
Size/Energy (locked motion type).

Pure, deterministic, stdlib only (rule 3: the engine never imports psycopg,
redis, langgraph or a model client). No import of generation/ actions here:
the 36 core tokens are pinned below; every other token falls back
deterministically by hash onto the five simulator presets.
"""
from __future__ import annotations

import hashlib

# preset -> base params (mirrors engine.particles.PRESETS, pinned here so the
# table reads without importing particles at module load).
_PRESET_BASE = ("burst", "fountain", "vortex", "rain", "confetti")

# Core 36 mood tokens -> (preset, {param overrides}).
# Every row keeps its preset's measurable signature (engine.particles PRESETS; tests/test_emoji_motion.py checks each
# token on trajectories()): a vortex row swirls (|vortex| >= 1.5, magnitude <= 0.6), a rain row falls (magnitude <= 0.5),
# a fountain row rises from the bottom, a confetti row scatters with low gravity. Size/Energy remain pack-tunable.
CORE_MOTIONS: dict[str, dict] = {
    "happy": {"preset": "burst", "params": {"magnitude": 1.0, "gravity": 1.0}},
    "laugh": {"preset": "fountain", "params": {"magnitude": 1.9, "gravity": 1.3, "spin": 1.5}},
    "cry": {"preset": "rain", "params": {"magnitude": 0.45, "gravity": 0.9}},
    "sad": {"preset": "rain", "params": {"magnitude": 0.3, "gravity": 0.7}},
    "love": {"preset": "burst", "params": {"magnitude": 0.9, "gravity": 0.6, "vortex": 0.3}},
    "angry": {"preset": "burst", "params": {"magnitude": 1.6, "gravity": 1.1}},
    "wink": {"preset": "burst", "params": {"magnitude": 0.8, "gravity": 0.7}},
    "kiss": {"preset": "burst", "params": {"magnitude": 0.8, "gravity": 0.5}},
    "surprised": {"preset": "burst", "params": {"magnitude": 1.4, "gravity": 0.9}},
    "scared": {"preset": "vortex", "params": {"vortex": 2.0, "magnitude": 0.45, "gravity": 0.1}},
    "confused": {"preset": "vortex", "params": {"vortex": 1.6, "magnitude": 0.4, "gravity": 0.1}},
    "think": {"preset": "vortex", "params": {"vortex": 1.5, "magnitude": 0.35, "gravity": 0.05}},
    "eye-roll": {"preset": "vortex", "params": {"vortex": -1.8, "magnitude": 0.5, "gravity": 0.1}},
    "sleep": {"preset": "rain", "params": {"magnitude": 0.3, "gravity": 0.5}},
    "cool": {"preset": "burst", "params": {"magnitude": 1.0, "gravity": 0.8}},
    "shy": {"preset": "burst", "params": {"magnitude": 0.7, "gravity": 0.6}},
    "sick": {"preset": "rain", "params": {"magnitude": 0.35, "gravity": 0.6}},
    "sneeze": {"preset": "burst", "params": {"magnitude": 1.5, "gravity": 1.0}},
    "celebrate": {"preset": "confetti", "params": {}},
    "clap": {"preset": "confetti", "params": {"magnitude": 1.3, "gravity": 0.3}},
    "approve": {"preset": "burst", "params": {"magnitude": 1.1, "gravity": 0.8}},
    "disapprove": {"preset": "burst", "params": {"magnitude": 0.9, "gravity": 1.0}},
    "thanks": {"preset": "fountain", "params": {"magnitude": 1.2, "gravity": 1.0}},
    "hello": {"preset": "fountain", "params": {"magnitude": 1.4, "gravity": 1.0}},
    "hug": {"preset": "fountain", "params": {"magnitude": 1.3, "gravity": 0.9}},
    "flex": {"preset": "burst", "params": {"magnitude": 1.2, "gravity": 0.9}},
    "scheme": {"preset": "vortex", "params": {"vortex": -1.6, "magnitude": 0.5, "gravity": 0.05}},
    "facepalm": {"preset": "rain", "params": {"magnitude": 0.5, "gravity": 0.8}},
    "shrug": {"preset": "vortex", "params": {"vortex": 1.6, "magnitude": 0.45, "gravity": 0.1}},
    "bored": {"preset": "rain", "params": {"magnitude": 0.3, "gravity": 0.5}},
    "dance": {"preset": "confetti", "params": {"magnitude": 1.6, "gravity": 0.3}},
    "plead": {"preset": "fountain", "params": {"magnitude": 1.0, "gravity": 1.0}},
    "salute": {"preset": "burst", "params": {"magnitude": 1.1, "gravity": 0.9}},
    "cheers": {"preset": "confetti", "params": {}},
    "gift": {"preset": "fountain", "params": {"magnitude": 1.5, "gravity": 1.1}},
    "star-struck": {"preset": "confetti", "params": {"magnitude": 1.7, "gravity": 0.3}},
}


def motion_for_token(token: str) -> dict:
    """The fixed animation for one bank token: {preset, params}.

    Core 36: pinned above. Anything else (the extended 1800+ bank):
    deterministic hash onto the five presets, default params. Same token
    always gives the same motion; no model, no randomness beyond the hash.
    """
    t = str(token or "").strip().lower() or "happy"
    if t in CORE_MOTIONS:
        row = CORE_MOTIONS[t]
        return {"preset": row["preset"], "params": dict(row["params"])}
    h = int(hashlib.sha256(t.encode("utf-8")).hexdigest(), 16)
    return {"preset": _PRESET_BASE[h % len(_PRESET_BASE)], "params": {}}


def motions_for_tokens(tokens) -> dict:
    """{token: motion_for_token(token)} for a list; order preserved, dupes dropped."""
    out: dict[str, dict] = {}
    for t in tokens or []:
        key = str(t or "").strip().lower()
        if key and key not in out:
            out[key] = motion_for_token(key)
    return out
