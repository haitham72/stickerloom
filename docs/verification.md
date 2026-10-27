# verification

Every artifact on the golden path passes these checks before it can ship.
This table is generated from the verifier catalogue — do not edit it by hand:

    python scripts/gen_verify_docs.py

The verifier (`stickerloom/engine/verify.py`, `VERIFY_VERSION = 2`)
holds 44 checks in 9 stages. `run()` never raises: a crashing
check is a `BLOCK verifier_error`. The report's first failing `BLOCK` is the
reason; `WARN` failures never fail a sticker on their own.

A `BLOCK` is either **technical** (Telegram itself would refuse the file:
format, size, codec, dimensions, rate, duration, audio, alpha — always final)
or **overridable** (a judgement call a person may allow with a recorded
"Use it anyway" click). A cell with no picture at all can never be allowed.
Some checks only apply to some inputs (no layout, no stored still to compare
against) and are skipped there instead of failing.

| stage | check | severity | class | what it catches |
|---|---|---|---|---|
| sheet | sheet_decodes | BLOCK | final | the file does not decode as an image |
| sheet | sheet_size | BLOCK | final | a sheet smaller than the configured minimum |
| sheet | background_is_key | BLOCK | final | a sheet with no key screen (nothing to key out) |
| sheet | grid_detected | BLOCK | final | a detected grid that differs from the planned grid |
| sheet | cut_clean | WARN | warning | a cut that fell back from the gutter detector |
| sheet | background_flat | WARN | warning | an uneven key background that keys badly |
| still | blank_cell | BLOCK | overridable | a cell with no cut subject (hard stop, gate) |
| still | edge_trimmed | WARN | warning | a trim pass that ate into the subject |
| still | dimensions | BLOCK | technical | a sticker that is not exactly 512x512 RGBA |
| still | transparent_corners | BLOCK | technical | a sticker whose corners are not transparent |
| still | foreground | BLOCK | overridable | a sticker with too little subject in it |
| still | inside_cell | BLOCK | overridable | a subject touching the edge of its cell |
| still | no_spill | BLOCK | overridable | key colour left on the subject edge |
| still | chroma_risk | WARN | warning | a subject that is itself key-coloured |
| still | holes | WARN | overridable | transparent holes enclosed by the subject |
| still | single_subject | WARN | warning | two characters (or a neighbour) in one cell |
| still | duplicate_cell | WARN | warning | a pose the model repeated from an earlier cell |
| still | static_file | BLOCK | technical | a still over the static size budget |
| slot | inside_slot | BLOCK | overridable | a subject reaching the slot border in some frame |
| slot | inside_frame | WARN | warning | a prepared-clip cell spilling over its border |
| slot | cross_slot | BLOCK | overridable | foreign foreground in the gutter between slots |
| anim | size_budget | BLOCK | technical | an animation over the video size budget |
| anim | codec_vp9 | BLOCK | technical | an animation that is not VP9 |
| anim | dimensions | BLOCK | technical | an animation that is not exactly 512x512 |
| anim | fps | BLOCK | technical | an animation over the frame-rate limit |
| anim | duration | BLOCK | technical | an animation longer than the duration limit |
| anim | no_audio | BLOCK | technical | an animation carrying an audio track |
| anim | alpha_mode_tag | BLOCK | technical | an animation missing its alpha metadata |
| anim | alpha_decoded | BLOCK | technical | an animation with no real transparency range |
| anim | loop_seam | BLOCK | overridable | a visible jump where the loop closes |
| anim | identity_kept | WARN | warning | an animation that drifted away from its still |
| anim | motion_present | WARN | warning | a clip with (almost) no motion in it |
| anim | alpha_stable | WARN | warning | a subject whose area flickers across frames |
| anim | sharpness | WARN | warning | a soft encode, or a cheap model behind the video |
| video_sheet | slots_match_approved | BLOCK | final | slots that are not exactly the approved set |
| video_sheet | no_outline_on_sheet | BLOCK | overridable | a white ring painted before animation |
| video | video_decodes | BLOCK | final | a returned video that does not decode (hard stop, gate) |
| video | video_specs | BLOCK | final | a returned video that is too short or has no rate |
| video | layout_match | BLOCK | final | slots that no longer match the sheet they came from |
| video | blank_slots_stay_empty | BLOCK | final | a model inventing characters in blank slots |
| pack | pack_limits | BLOCK | final | a pack Telegram would refuse (count, keys, emoji, size) |
| telegram | telegram_sticker | BLOCK | final | a sticker breaking Telegram format rules |
| telegram | telegram_stroke | WARN | warning | a static sticker missing its white outline |
| telegram_set | telegram_set | BLOCK | final | a set breaking Telegram set rules |

`holes` registers as a warning and escalates to a block past its upper limit.
The engine-purity test keeps model clients out of the engine: only thresholds
live in `EngineConfig`, never decisions.
