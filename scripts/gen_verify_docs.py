"""Render docs/verification.md from the verifier catalogue (stickerloom/engine/verify.py).

Usage (from the repo root):

    python scripts/gen_verify_docs.py

Reads CATALOGUE at import time, so the table can never go stale: the script
asserts the known size (44 checks in 9 stages) and that every check has a
"what it catches" entry, and fails loudly on any drift.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

EXPECTED_CHECKS = 44
EXPECTED_STAGES = 9

# (stage, check id) -> what a failure of this check catches, in one line.
# Keyed explicitly so a renamed or added check fails the run instead of
# silently rendering a blank cell.
CATCHES = {
    ("sheet", "sheet_decodes"): "the file does not decode as an image",
    ("sheet", "sheet_size"): "a sheet smaller than the configured minimum",
    ("sheet", "background_is_key"): "a sheet with no key screen (nothing to key out)",
    ("sheet", "grid_detected"): "a detected grid that differs from the planned grid",
    ("sheet", "cut_clean"): "a cut that fell back from the gutter detector",
    ("sheet", "background_flat"): "an uneven key background that keys badly",
    ("still", "blank_cell"): "a cell with no cut subject (hard stop, gate)",
    ("still", "edge_trimmed"): "a trim pass that ate into the subject",
    ("still", "dimensions"): "a sticker that is not exactly 512x512 RGBA",
    ("still", "transparent_corners"): "a sticker whose corners are not transparent",
    ("still", "foreground"): "a sticker with too little subject in it",
    ("still", "inside_cell"): "a subject touching the edge of its cell",
    ("still", "no_spill"): "key colour left on the subject edge",
    ("still", "chroma_risk"): "a subject that is itself key-coloured",
    ("still", "holes"): "transparent holes enclosed by the subject",
    ("still", "single_subject"): "two characters (or a neighbour) in one cell",
    ("still", "duplicate_cell"): "a pose the model repeated from an earlier cell",
    ("still", "static_file"): "a still over the static size budget",
    ("slot", "inside_slot"): "a subject reaching the slot border in some frame",
    ("slot", "inside_frame"): "a prepared-clip cell spilling over its border",
    ("slot", "cross_slot"): "foreign foreground in the gutter between slots",
    ("anim", "size_budget"): "an animation over the video size budget",
    ("anim", "codec_vp9"): "an animation that is not VP9",
    ("anim", "dimensions"): "an animation that is not exactly 512x512",
    ("anim", "fps"): "an animation over the frame-rate limit",
    ("anim", "duration"): "an animation longer than the duration limit",
    ("anim", "no_audio"): "an animation carrying an audio track",
    ("anim", "alpha_mode_tag"): "an animation missing its alpha metadata",
    ("anim", "alpha_decoded"): "an animation with no real transparency range",
    ("anim", "loop_seam"): "a visible jump where the loop closes",
    ("anim", "identity_kept"): "an animation that drifted away from its still",
    ("anim", "motion_present"): "a clip with (almost) no motion in it",
    ("anim", "alpha_stable"): "a subject whose area flickers across frames",
    ("anim", "sharpness"): "a soft encode, or a cheap model behind the video",
    ("video_sheet", "slots_match_approved"): "slots that are not exactly the approved set",
    ("video_sheet", "no_outline_on_sheet"): "a white ring painted before animation",
    ("video", "video_decodes"): "a returned video that does not decode (hard stop, gate)",
    ("video", "video_specs"): "a returned video that is too short or has no rate",
    ("video", "layout_match"): "slots that no longer match the sheet they came from",
    ("video", "blank_slots_stay_empty"): "a model inventing characters in blank slots",
    ("pack", "pack_limits"): "a pack Telegram would refuse (count, keys, emoji, size)",
    ("telegram", "telegram_sticker"): "a sticker breaking Telegram format rules",
    ("telegram", "telegram_stroke"): "a static sticker missing its white outline",
    ("telegram_set", "telegram_set"): "a set breaking Telegram set rules",
}

# Which catalogue stages the "animation" overridable/technical rows apply to.
ANIMATION_STAGES = ("slot", "anim")


def classify(stage: str, cid: str, severity: str) -> str:
    from stickerloom.engine import verify as v

    if cid in v.TECHNICAL.get(stage, ()) or (
        stage in ANIMATION_STAGES and cid in v.TECHNICAL.get("animation", ())
    ):
        return "technical"
    if cid in v.OVERRIDABLE.get(stage, ()) or (
        stage in ANIMATION_STAGES and cid in v.OVERRIDABLE.get("animation", ())
    ):
        return "overridable"
    return "warning" if severity == "WARN" else "final"


def main() -> int:
    from stickerloom.engine import verify as v

    catalogue = v.CATALOGUE
    total = sum(len(rows) for rows in catalogue.values())
    if len(catalogue) != EXPECTED_STAGES or total != EXPECTED_CHECKS:
        print(
            f"DRIFT: verifier has {total} checks in {len(catalogue)} stages, "
            f"expected {EXPECTED_CHECKS} checks in {EXPECTED_STAGES} stages. "
            "Update CATCHES and EXPECTED_* in scripts/gen_verify_docs.py.",
            file=sys.stderr,
        )
        return 1
    missing = [
        (stage, cid)
        for stage, rows in catalogue.items()
        for cid, _, _, _ in rows
        if (stage, cid) not in CATCHES
    ]
    if missing:
        print(
            f"DRIFT: no 'what it catches' entry for {missing}. "
            "Add them to CATCHES in scripts/gen_verify_docs.py.",
            file=sys.stderr,
        )
        return 1

    lines = [
        "# verification",
        "",
        "Every artifact on the golden path passes these checks before it can ship.",
        "This table is generated from the verifier catalogue — do not edit it by hand:",
        "",
        "    python scripts/gen_verify_docs.py",
        "",
        f"The verifier (`stickerloom/engine/verify.py`, `VERIFY_VERSION = {v.VERIFY_VERSION}`)",
        f"holds {total} checks in {len(catalogue)} stages. `run()` never raises: a crashing",
        "check is a `BLOCK verifier_error`. The report's first failing `BLOCK` is the",
        "reason; `WARN` failures never fail a sticker on their own.",
        "",
        "A `BLOCK` is either **technical** (Telegram itself would refuse the file:",
        "format, size, codec, dimensions, rate, duration, audio, alpha — always final)",
        "or **overridable** (a judgement call a person may allow with a recorded",
        '"Use it anyway" click). A cell with no picture at all can never be allowed.',
        "Some checks only apply to some inputs (no layout, no stored still to compare",
        "against) and are skipped there instead of failing.",
        "",
        "| stage | check | severity | class | what it catches |",
        "|---|---|---|---|---|",
    ]
    for stage, rows in catalogue.items():
        for cid, severity, _fn, _gate in rows:
            lines.append(
                f"| {stage} | {cid} | {severity} | {classify(stage, cid, severity)} "
                f"| {CATCHES[(stage, cid)]} |"
            )
    lines += [
        "",
        "`holes` registers as a warning and escalates to a block past its upper limit.",
        "The engine-purity test keeps model clients out of the engine: only thresholds",
        "live in `EngineConfig`, never decisions.",
        "",
    ]
    out = Path(__file__).resolve().parent.parent / "docs" / "verification.md"
    out.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"wrote {out} ({total} checks, {len(catalogue)} stages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
