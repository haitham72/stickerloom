"""The HTTP contract as an OpenAPI 3.1 document, served at GET /api/openapi.json and turned into TypeScript types by `python -m stickerloom openapi --ts FILE`.

One table of routes, written by hand next to the server it describes. `tests/test_openapi.py` reads the server's source and fails when a route
exists in the server that this table does not describe (drift guard), so the spec cannot silently rot. Bodies and responses are typed where an
integrator needs them (chat, generations, review, judge, assets, health, jobs, live generation) and `object` where the Studio's own screens are
the only caller. Everything is JSON, `{"error": "..."}` on failure, ids everywhere, Bearer token optional ."""
from __future__ import annotations

VERSION = "1.0.0"

STR = {"type": "string"}
INT = {"type": "integer"}
NUM = {"type": "number"}
BOOL = {"type": "boolean"}
OBJ = {"type": "object", "additionalProperties": True}


def arr(x):
    return {"type": "array", "items": x}


def ref(n):
    return {"$ref": f"#/components/schemas/{n}"}


def obj(props: dict, required: list | None = None, extra: bool = False):
    d = {"type": "object", "properties": props, "additionalProperties": extra}
    if required:
        d["required"] = required
    return d


def nullable(x):
    return {"anyOf": [x, {"type": "null"}]}


SCHEMAS = {
    "Error": obj({"error": STR}, ["error"]),
    "Settings": obj({"grid": {"type": "string", "enum": ["3x3", "2x2"]}, "style_id": STR, "ask_before_spending": BOOL, "ai": BOOL, "allow_vlm": nullable(BOOL),
                     "stage": {"type": "string", "enum": ["auto", "prompt", "emojis", "animation", "export", "api"], "description": "How far a new request goes (agent/stages.py): auto = each message decides (default), prompt = the plan only, emojis = Stickers: sheet and stickers (default), animation = + the animations (ends at G4), export = Telegram: + pack and Telegram, api = Export: + the AddCollection API (owner only)"},
                     "models": {"type": "object", "description": "The chat's own model picks (the slider's details): image and video are catalog ids (GET /api/models), ai a model the local server lists; an empty value returns to the default", "properties": {"image": STR, "video": STR, "ai": STR}},
                     "creator": obj({"on": BOOL, "scope": {"type": "string", "enum": ["images", "video"]}, "bypass": BOOL})}),
    "Step": obj({"kind": {"type": "string", "enum": ["task", "step", "note", "final"]}, "label": STR, "detail": nullable(obj({"lines": arr(STR)}, extra=True)),
                 "status": {"type": "string", "enum": ["running", "done", "error"]}, "ts": NUM}, ["kind", "label"]),
    "Sticker": obj({"id": {"type": "string", "description": "G012/S3"}, "index": INT, "key": STR, "name": nullable(STR), "emoji": nullable(arr(STR)),
                    "status": STR, "reason": nullable(STR), "still": STR, "anim": STR, "anim_status": nullable(STR), "png": nullable(STR), "webm": nullable(STR)}, ["id", "index"], True),
    "GenerationCard": obj({"generation": STR, "stage": nullable(STR), "error": nullable(STR), "prompt": nullable(STR), "parent": nullable(STR),
                           "video_sheets": arr(obj({"id": STR, "picture": STR, "status": STR}, extra=True)), "stickers": arr(ref("Sticker"))}, ["generation"], True),
    "Card": obj({"type": {"type": "string", "enum": ["plan", "generation", "stickers"]}, "generation": nullable(STR), "job": nullable(STR), "subject": STR,
                 "job_status": nullable(STR), "data": ref("GenerationCard")}, ["type"], True),
    "Chip": obj({"label": STR, "text": STR, "action": {"type": "string", "enum": ["confirm", "cancel"]}}, ["label"]),
    "Message": obj({"id": STR, "role": {"type": "string", "enum": ["user", "assistant"]}, "text": STR, "status": {"type": "string", "enum": ["working", "done", "error"]},
                    "steps": arr(ref("Step")), "cards": arr(ref("Card")), "chips": arr(ref("Chip")), "ts": NUM}, ["id", "role", "text"], True),
    "SessionSummary": obj({"id": STR, "title": STR, "updated": NUM, "created": NUM, "subjects": arr(STR), "turns": INT, "focus": nullable(STR)}, ["id"]),
    "Session": obj({"id": STR, "user": STR, "title": STR, "settings": ref("Settings"), "focus": obj({"generation": nullable(STR), "stickers": arr(STR)}),
                    "subjects": arr(OBJ), "preferences": OBJ, "feedback": arr(OBJ), "interactions": arr(OBJ), "pending": nullable(OBJ),
                    "messages": arr(ref("Message")), "working": BOOL, "summary_text": STR}, ["id"], True),
    "ChatSend": obj({"text": STR, "selected": arr(STR), "action": obj({"type": {"type": "string", "enum": ["confirm", "confirm_new", "cancel", "batch_more", "regenerate"],
                                                                         "description": "batch_more {to: 2..4}: the next batches of the open pack up to batch `to` (one card, one price, one go-ahead); regenerate {generation}: a new take of that batch. Other button types (creator_*, names_*, retry_sheet) are the chat's own chips"},
                                                                "to": INT, "generation": STR}, ["type"], True)}),
    "Accepted": obj({"id": STR, "message": STR, "idempotent": BOOL}, ["id"], True),
    "GenerationCreate": obj({"prompt": STR, "variant": INT, "outline": INT, "erode": INT}, ["prompt"]),
    "GenerationCreated": obj({"id": INT, "idempotent": BOOL}, ["id"]),
    "Review": obj({"gate": {"type": "string", "enum": ["plan", "still", "video_sheet", "anim", "pack"]}, "decision": {"type": "string", "enum": ["APPROVE", "REJECT"]},
                   "index": nullable(INT), "note": nullable(STR)}, ["gate", "decision"]),
    "Judge": obj({"scope": {"type": "string", "enum": ["still", "anim"]}, "force": BOOL}),
    "Event": obj({"event": STR, "generation_id": STR, "stage": nullable(STR), "status": nullable(STR), "ts": nullable(NUM), "ms": nullable(INT), "actor": nullable(STR),
                  "decision": nullable(STR), "gate": nullable(STR), "index": nullable(INT), "sticker_id": nullable(STR), "asset_url": nullable(STR),
                  "trace_run_id": nullable(STR)}, ["event", "generation_id"]),
    "AssetSign": obj({"key": {"type": "string", "description": "a path under out/, e.g. G002/slices/x.png"}, "ttl": {"type": "integer", "minimum": 5, "maximum": 3600}}, ["key"]),
    "AssetLink": obj({"url": STR, "expires_in": INT}, ["url", "expires_in"]),
    "Health": obj({"ok": BOOL, "database": OBJ, "redis": OBJ, "models": OBJ, "providers": OBJ, "storage": OBJ, "warnings": arr(STR), "ms": INT}, ["ok"], True),
    "Job": obj({"id": STR, "kind": {"type": "string", "enum": ["sheet", "video", "single"]}, "status": {"type": "string", "enum": ["REQUESTED", "CLAIMED", "DONE", "FAILED", "TIMEOUT"]},
                "external_task_id": nullable(STR), "generation": nullable(STR), "model": nullable(STR), "cost": nullable(NUM), "error": nullable(STR),
                "provider_check": obj({"status": STR, "checked_at": NUM, "classification": STR, "message": STR}),
                "history": arr(OBJ), "retry_of": STR, "retried_as": STR}, ["id", "kind", "status"], True),
    "LiveSheet": obj({"prompt": STR, "grid": STR, "style_id": STR, "ai": BOOL, "refs": arr(STR), "model": STR, "options": OBJ, "outline": INT, "erode": INT, "parent": STR, "regen_of": STR,
                          "from_generation": INT, "sheet_prompt": {"type": "string", "maxLength": 6000, "description": "the prompt exactly as written (the Prompt tab); with from_generation the new sheet keeps that batch's cells and tags"},
                          "plan": {"type": "object", "description": "the plan Generate prompt (`POST /api/plan`) returned, sent back so the batch keeps the cells, tags and emoji the person saw (an AI-written draft costs no second model call). Untrusted: only slots.cells (pos, label, tags, emoji, motion), slots.subject_description and slots.key_colour are read, re-linted and the prompts rebuilt from the template; max 64 KB, exactly one cell per grid slot, not together with from_generation; an invalid plan is a 400 in words; left out, the request is planned as before", "additionalProperties": True}}, ["prompt"]),
    "LiveJob": obj({"job": STR, "task": STR, "estimate": nullable(NUM), "model": STR}, ["job"], True),
    "PoolHit": obj({"id": STR, "key": STR, "png": nullable(STR), "emoji": nullable(STR)}),
}

ERR = {"description": "error", "content": {"application/json": {"schema": ref("Error")}}}

# (method, path, tag, summary, request schema | None, response schema | None, status)
ROUTES = [
    # --- chat
    ("GET", "/api/chat/agent", "Chat", "Optional `session` query: resolve the saved AI model under the caller's session permissions (404 if inaccessible); otherwise use global defaults. Which model runs the assistant and the vision judge, what is available (the local model by a real probe, with the reason when it cannot answer) and `agent_status` {fallback, reason}: true when the chat is on its rules only", None, OBJ, 200),
    ("GET", "/api/chat/sessions", "Chat", "List chats, newest first", None, obj({"sessions": arr(ref("SessionSummary"))}), 200),
    ("POST", "/api/chat/sessions", "Chat", "Create a chat", obj({"title": STR, "settings": ref("Settings")}), ref("Session"), 200),
    ("GET", "/api/chat/sessions/{id}", "Chat", "A whole chat for display: messages with steps and cards (generation cards carry their live stickers), memory summary", None, ref("Session"), 200),
    ("GET", "/api/chat/sessions/{id}/stream", "Chat", "SSE of the turn in progress (native FastAPI): `event: turn` {working, count, message} each time the last message changes, then `event: done` {working: false, count}; retry 3000, a comment every 15 s, ends after 10 minutes. The same access as GET /api/chat/sessions/{id} (401 / 403 / 404 as JSON before any event)", None, obj({"working": BOOL, "count": INT, "message": OBJ}), 200),
    ("POST", "/api/chat/sessions/{id}/messages", "Chat", "Send a message or a button action; the turn runs in the background (409 while the last one runs). Idempotency-Key supported", ref("ChatSend"), ref("Accepted"), 202),
    ("POST", "/api/chat/sessions/{id}/settings", "Chat", "Grid, Ask-before-spending, style, AI vision, the stage (prompt | emojis | animation | export | api), the models (image | video | ai) and the creator's bypass; no chat turn. Unknown keys are ignored; an unknown style or stage is a 400", ref("Settings"), obj({"settings": ref("Settings")}), 200),
    ("POST", "/api/chat/sessions/{id}/delete", "Chat", "Delete a chat (its stickers stay)", None, obj({"deleted": STR}), 200),
    # --- particle effects (docs/architecture.md ('Reactions'))
    ("GET", "/api/packs/{id}/stickers/{sid}/particle-preview", "Particles", "Free Echo reaction for this sticker: newest own linked set, persisted motion and sizing. Returns {set, motion, url, params, preset} or {set:null,url:null}; no unrelated-set fallback. Members receive the empty answer", None, OBJ, 200),
    ("GET", "/api/generations/{id}/particles", "Particles", "READY batch cells: {generation, cells:[{index,link,sticker,offer_approve,sets,affirmed}]}", None, OBJ, 200),
    ("POST", "/api/particles/{id}/link", "Particles", "Link owner stickers {sticker_ids:[ids or owner records]}; free, no copies", OBJ, OBJ, 200),
    ("POST", "/api/particles/{id}/unlink", "Particles", "Unlink owner stickers {sticker_ids:[ids]}; set and cells stay", OBJ, OBJ, 200),
    ("GET", "/api/particles", "Particles", "Every particle set that is not in the trash, newest first: {sets: [{id, name, created, user, kind, elements[], packs[], used_in[{id, name}], cells[], picked[], n_cells, n_picked, renders, credits, drawing, sheets[]}]}. Sticker-owned sets include owner[], source.kind/job, credits, affirmed_in[], detached; packs[] is derived read-only (docs/architecture.md ('Reactions'))", None, obj({"sets": arr(OBJ)}), 200),
    ("POST", "/api/particles", "Particles", "Recovery {from_stickers:[library sticker ids],parent_pack_id,owners?,name?,target?} keeps originals and links tight sprites to the parent pack stickers. Owners {owners:[{pack_id,sticker_id}]} replace deprecated packs input. Imports accept target:P### to append; video imports are idempotent by effect/result id. ALSO {from_video:E###,picked?:[source cell indices]} keeps animated clips/frame timelines and keyed posters, {from_slices:[{generation,index}],target?:P###} copies selected slices (target appends). ALSO {from_generation: G###, name?, packs?, picked?} -> 201: the cells of a batch that was CUT AS PARTICLES become a set, as TIGHT sprites (cropped out of the keyed sheet, never 512 px stickers); a batch cut as stickers is a 409 with the way out (POST /api/generations/{id}/recut_particles), a missing one a 404. Save what a run drew as a DURABLE set: {from_effect: E###, name?, packs?: [ids], picked?: [cells]} (the effect's own pack by default) -> 201 the set. Without `from_effect` it makes an empty stand-alone set: {name?, elements?, packs?, kind?: drawn|stickers|video}. Free", OBJ, OBJ, 201),
    ("GET", "/api/particles/deleted", "Particles", "The trash, newest deleted first: {sets: [{id, name, created, kind, elements[], packs[], used_in[{id, name, missing?}], cells[] (urls into out/trash/particles/), picked[], n_cells, n_picked, credits, deleted: true, trashed: true, deleted_at}]}. Restore any of them with POST /api/particles/{id}/restore, long after the delete. Owner only", None, obj({"sets": arr(OBJ)}), 200),
    ("GET", "/api/particles/{id}", "Particles", "One set with its cells as /out/ urls, `used_in` the pack names, `sheets[]` (the sheets drawn for it by Generate more: {n, job, generation, grid, elements, status: REQUESTED|DRAWN|DONE|FAILED|NO_CELLS, estimate, cost, appended[], skipped[], error}), `drawing` (a sheet is on its way), `trashed: false`. A read also appends the cells of a sheet that was cut since the last one", None, OBJ, 200),
    ("POST", "/api/particles/{id}", "Particles", "Rename, re-pick cells, persist the default motion including sprite_px/scale or link owners through deprecated packs: {name?, elements?, packs?, picked?: [cells], motion?: {preset, params}, save?: true}. `save: true` is the person's Save: the set becomes (or stays) a saved row under its stickers (`saved_at`); a new set starts as a draft (`saved_at: null`). Unpicking never deletes a file (generate more appends). Empty `picked` is a 400", OBJ, OBJ, 200),
    ("POST", "/api/particles/{id}/assign", "Particles", "Deprecated one-release alias: link the stickers of packs {packs: [ids]}: a list edit, no file is copied; 404 for a pack that does not exist", obj({"packs": arr(STR)}), OBJ, 200),
    ("POST", "/api/particles/{id}/unassign", "Particles", "Deprecated one-release alias: unlink the stickers of packs {packs: [ids]}; the set and its cells STAY", obj({"packs": arr(STR)}), OBJ, 200),
    ("POST", "/api/particles/{id}/duplicate", "Particles", "An independent branch under a new id with the same sticker owners, saved motion, images and clip timelines; copied render affirmations are cleared ({name?}) -> 201", OBJ, OBJ, 201),
    ("POST", "/api/particles/{id}/save-as-new", "Particles", "Save as new: the same sprites with the edited motion become a NEW saved row under the same stickers ({motion?: {preset, params}, name?}) -> 201 the new set. The row it came from is unchanged; its bursts stay with it (the new row has none)", obj({"motion": OBJ, "name": STR}), OBJ, 201),
    ("POST", "/api/particles/{id}/delete", "Particles", "Move the set to out/trash/particles/ (nothing is destroyed). A set assigned to packs is REFUSED with 409 and the pack names unless {confirm: true}", obj({"confirm": BOOL}), obj({"ok": BOOL, "id": STR, "trashed": BOOL, "was_in": arr(STR)}), 200),
    ("POST", "/api/particles/{id}/restore", "Particles", "Put a deleted set back, same id and packs; 409 when that id is taken again", None, OBJ, 200),
    ("POST", "/api/particles/{id}/preview", "Particles", "The burst of the set's PICKED cells as a small looping WebP, rendered by the same engine as the final file (free; cached by what it was made from): {pack_id?, preset?: burst | fountain | vortex | rain | confetti (default: the set's own motion, else burst), params?: {magnitude, gravity, vortex, particle_size?: 0.25-3 (how big the particles look), count, spin, seed, ..., sprite_px?: 32-512 (sprite sharpness), scale?: 1-4} (strict: 400 for an unknown key), size?: 64-512}. -> {url, file, params, preset, sprites, source: cells | stickers, pack_id}. A set of kind `stickers` bursts the pack's own stickers; a drawn set with no cell yet is a 409", obj({"pack_id": STR, "preset": STR, "params": OBJ, "size": INT}), OBJ, 200),
    ("POST", "/api/particles/{id}/render", "Particles", "The final 512 px WebM of the burst FOR A PACK, judged and stored under out/particles/P###/renders/R###.webm with its checks: {pack_id (default: the set's only pack; 400 when it is in several), preset?, params?}. -> {id, set, pack_id, preset, params, status, bytes, url, checks[], warnings[], blocks[], metrics, added_to}. Stored whatever the checks say: ONLY Telegram's own limits make it FAILED (and a failed one is kept); every other check is a warning the person decides on", obj({"pack_id": STR, "preset": STR, "params": OBJ}), OBJ, 200),
    ("POST", "/api/particles/{id}/add", "Particles", "Put rendered bursts into a pack as animated stickers tagged with the pack's emoji (commonest first, at most 20): {renders?: [R###] (default: every READY burst rendered for that pack that is not in a pack yet), pack_id?, sticker_id? (the sticker whose row this is; default the set's first owner)}. -> {added: [{sticker, name, render}], pack_id}. The new pack sticker carries `source.particle_set`, `source.render` and `source.parent_sticker`, and the row shows it as `in_pack`. This click is the person's approval (history APPROVE). A FAILED render (a Telegram limit) is a 409; a warning never stops it; the same burst in the same pack twice is a 409", obj({"renders": arr(STR), "pack_id": STR, "sticker_id": STR}), obj({"added": arr(OBJ), "pack_id": STR}), 200),
    ("POST", "/api/particles/{id}/more", "Particles", "Add more with explicit mode:drawn|image|video|stickers (defaults to original source), prompt and grid. Video uses Kling quote/go and appends animated clips; quote returns effect context to resend as effect on go. Other modes draw a sheet for the set and APPEND its cut cells (numbered after the last cell, picked; nothing existing is deleted, overwritten or re-picked). {grid?: 2x2 | 3x3, elements?: [the particles to draw; default the set's own, at most the sheet's cells], estimate?: true -> 200 the free quote (prompt, picks, credits, model), go?: true}; 409 with the estimate unless `go: true` (the click is the go-ahead); 202 {job, task, estimate, id, grid}. A set made by hand (no run behind it) can gain cells this way. 400 with the reason when there is nothing to draw", obj({"mode": STR, "prompt": STR, "effect": STR, "grid": STR, "elements": arr(STR), "estimate": BOOL, "go": BOOL}), obj({"job": STR, "task": STR, "estimate": NUM, "id": STR, "grid": arr(INT)}), 202),
    ("GET", "/api/particles/emoji-motions", "Reactions", "The generic 1800 wall (read-only emoji:animation map): {motions: [{token, emoji, preset, params, label, motion}]}. Every predefined emoji owns one fixed animation; packs inherit it 1:1.", None, obj({"motions": arr(OBJ)}), 200),
    ("GET", "/api/particles/reactions", "Reactions", "Alias of /api/particles/emoji-motions.", None, obj({"motions": arr(OBJ)}), 200),
    ("POST", "/api/particles/{id}/export-lottie", "Reactions", "Optional third output (web only): the set's motion as a real Lottie JSON file with per-particle keyframes (Telegram stays WebM). {preset?, params?} -> {id, set, preset, params, file, url, layers}. The client downloads url (the stored renders/L###.lottie.json). Free.", obj({"preset": STR, "params": OBJ}), OBJ, 200),
    ("GET", "/api/particles/bank", "Reactions", "The emoji bank with per-token build state for Library > Reactions > Create: {bank: [{token, emoji, preset, params, label, motion, state: finished|unclaimed, building, set_id, n_picked}]}. Read-only, free.", None, obj({"bank": arr(OBJ)}), 200),
    ("POST", "/api/particles/emoji-preview", "Reactions", "Reaction effects (Create > New): one bank emoji's fixed motion flying as its own glyph, as a small looping WebP. {token (name or glyph), preset?, size_in_dur?, size_out_dur?, hue_var?, particle_size? (0.25-3), size_var? (0-1), magnitude?, gravity?, vortex?, spin?, count?, spread?, fade_start? (s after a particle's birth its opacity starts dropping), fade_dur? (s from 100% to 0%, linear; 0 = off), fade_var? (+- s on both, per particle) (a reaction is always 2 s) (Simulation; left out = the motion's own), seed?, size? 64-512} -> {token, emoji, preset, params, url}. 409 for an emoji outside the bank, 503 with no colour emoji font. Free.", obj({"token": STR, "preset": STR, "size_in_dur": NUM, "size_out_dur": NUM, "hue_var": NUM, "particle_size": NUM, "size_var": NUM, "magnitude": NUM, "gravity": NUM, "vortex": NUM, "spin": NUM, "count": INT, "spread": NUM, "fade_start": NUM, "fade_dur": NUM, "fade_var": NUM, "seed": INT, "size": INT}, ["token"]), OBJ, 200),
    ("GET", "/api/particles/emoji-presets", "Reactions", "Reaction effects: the saved presets, oldest first: {presets: [{id, name, settings, created}]}. Free.", None, obj({"presets": arr(OBJ)}), 200),
    ("POST", "/api/particles/emoji-presets", "Reactions", "Reaction effects, Save preset: {settings: {preset?, particle_size?, size_var?, size_in_dur?, size_out_dur?, hue_var?, magnitude?, gravity?, vortex?, spin?, count?, spread?}, name?} -> the next preset {id: preset01..., name: Preset 01..., settings, created}. 400 for an unknown key or bad value. Free.", obj({"settings": OBJ, "name": STR}, ["settings"]), OBJ, 201),
    ("POST", "/api/particles/emoji-presets/{id}/delete", "Reactions", "Reaction effects: remove a preset (reactions made with it keep their params): {deleted, presets}. 404 for an unknown id. Free.", None, OBJ, 200),
    ("POST", "/api/particles/emoji-presets/{id}", "Reactions", "Reaction effects, Presets > Update: {settings?, name?, token?} -> the preset (its motion follows its settings). 404 for an unknown id.", OBJ, OBJ, 200),
    ("GET", "/api/particles/emoji-batches", "Reactions", "Reaction effects: the newest background runs (a reloaded screen finds one still going): {batches: [{id, state, mode, total, done, errors}]}.", None, obj({"batches": arr(OBJ)}), 200),
    ("POST", "/api/particles/emoji-batches", "Reactions", "Reaction effects, batch create in the background: {tokens? (none = every bank emoji), mode: start | preset | random, preset_id?, only_missing?} -> 202 {id, state: running, total, done}. random = a random preset of each emoji's own motion (none of it: the start look). 409 while another run is going or when nothing is left to make.", OBJ, OBJ, 202),
    ("GET", "/api/particles/emoji-batches/{id}", "Reactions", "One background run: {id, state: running | done | cancelled | stopped, total, done, made: [{token, id, preset_id}], errors}.", None, OBJ, 200),
    ("POST", "/api/particles/emoji-batches/{id}/cancel", "Reactions", "Stop a background run between two emojis (what was made is kept).", OBJ, OBJ, 200),
    ("GET", "/api/particles/emoji-lottie.zip", "Reactions", "Export All | Selected (?tokens=a,b): the newest Lottie of each emoji in one zip, <token>.lottie.json + manifest.json {reactions, missing}. 409 when none has one.", None, OBJ, 200),
    ("POST", "/api/particles/emoji-lottie/batch", "Reactions", "Reaction effects, many at once: {tokens: [name or glyph, at most 24], preset_id?} -> {reactions: [as POST emoji-lottie], errors: [{token, error}], preset}; no preset_id = each emoji's own motion with the screen's defaults. One failing emoji does not stop the others. Free.", obj({"tokens": arr(STR), "preset_id": STR}, ["tokens"]), OBJ, 200),
    ("GET", "/api/particles/emoji-lottie", "Reactions", "Reaction effects: every created reaction, newest first (one per Lottie export): {reactions: [{id, token, emoji, preset, params, url, preview, bytes, created}]}; preview is that export's own looping WebP. Read-only, free.", None, obj({"reactions": arr(OBJ)}), 200),
    ("POST", "/api/particles/emoji-lottie", "Reactions", "Reaction effects (Create > New): the same burst as a playable Lottie JSON with the glyph embedded as an image asset (web only; Telegram stays WebM). {token, preset?, size_in_dur? (0.4), size_out_dur? (0.4), hue_var? (25), seed? (1), size? 128-1024} -> {id, token, emoji, preset, params, file, url, preview, layers, assets, bytes}; stores out/particles/emoji/<token>/renders/L###.lottie.json + L###.png + L###.webp, history LOTTIE. Same emoji + options + seed -> the same JSON. 409 for an emoji outside the bank. Free.", obj({"token": STR, "preset": STR, "size_in_dur": NUM, "size_out_dur": NUM, "hue_var": NUM, "particle_size": NUM, "size_var": NUM, "magnitude": NUM, "gravity": NUM, "vortex": NUM, "spin": NUM, "count": INT, "spread": NUM, "fade_start": NUM, "fade_dur": NUM, "fade_var": NUM, "seed": INT, "size": INT}, ["token"]), OBJ, 200),
    ("POST", "/api/particles/{id}/default-sprite", "Reactions", "Free instant fallback sprite for bank reactions with no drawn cells yet (procedural sparkle, picked at once; replaced later via Generate more). Idempotent: {default: true} when added, {default: false} when the set already flies.", None, OBJ, 200),
    ("GET", "/api/packs/{id}/reactions", "Reactions", "This pack's reaction variations: one row per emoji in the pack, motion inherited locked, art + Size/Energy vary. {pack_id, reactions: [{token, emoji, stickers[], preset, params, label, motion, locked, sets[]}]}.", None, OBJ, 200),
    ("GET", "/api/effects", "Effects", "Effects, newest last: id, pack, mode, status, counts", None, obj({"effects": arr(OBJ)}), 200),
    ("POST", "/api/effects", "Effects", "Start an effect for a pack: {pack_id, sticker_ids | \"all\", mode: video | sim, grid: 2x2 | 3x3, note?, allow_vlm?}; the analysis runs in the background (poll the record)", OBJ, obj({"id": STR, "status": STR}), 202),
    ("GET", "/api/effects/{id}", "Effects", "One effect: stickers, groups (subject, pieces, screen colour, motion presets, sprites), the particle set (set: {grid, elements, options, source, by, status: SUGGESTED | REQUESTED | DRAWN, job?, generation?, picked}), video jobs, results with their checks, history", None, OBJ, 200),
    ("POST", "/api/effects/{id}/analyse", "Effects", "Read the stickers again (allow_vlm: true lets a vision model look at the pictures; otherwise the built-in table answers)", OBJ, obj({"id": STR}), 202),
    ("POST", "/api/effects/{id}/plan", "Effects", "Edit a group: {group, elements?, subject?, style?, sprites?: \"own\" | {generation: N, picked?: [cells]}}; linted (400 with the reason)", OBJ, OBJ, 200),
    ("POST", "/api/effects/{id}/estimate", "Effects", "The text-only video of a group before anything is spent: prompt, screen colour, cells, credits, model (free)", OBJ, OBJ, 200),
    ("POST", "/api/effects/{id}/video", "Effects", "Start the text-only Kling video of a group; 409 with the estimate unless go: true (the click is the go-ahead)", OBJ, obj({"job": STR, "estimate": {"type": ["number", "null"]}}), 202),
    ("POST", "/api/effects/{id}/suggest", "Effects", "Candidate particles for the ONE set of the whole effect: {grid?: 2x2 | 3x3, allow_vlm?}; the vision model looks at one picture (the master sheet of the batch the stickers were cut from, else a contact sheet of the stickers) only with allow_vlm: true, else the built-in table answers. Answer: {options: [8-12 short names], n (cells of the grid), grid, source: {kind: batch | contact, generation?}, by: vlm | table, model?, notes}; stored in effect.set", OBJ, OBJ, 200),
    ("POST", "/api/effects/{id}/particles_estimate", "Effects", "The ONE AI-drawn sheet of particles for the whole effect before anything is spent: {grid?: 2x2 | 3x3, elements: [the person's picks, 1..rows*cols; fewer are cycled as variants]}; the prompt, the cells, screen colour, credits, model (free). Replaces pieces_estimate (kept as an alias; `group` there means that group's pieces)", OBJ, OBJ, 200),
    ("POST", "/api/effects/{id}/particles", "Effects", "Draw the particle sheet (an ordinary sheet job, outline 0, one sheet for the whole effect): {grid?, elements, go}; 409 with the estimate unless go: true. The batch is a particle batch (kind: particles: exact equal cells, no sticker rule blocks a cell, warnings only); when it is cut every group's sprites are {generation, picked} and effect.set is REQUESTED then DRAWN. Replaces pieces (kept as an alias)", OBJ, obj({"job": STR, "task": STR, "estimate": {"type": ["number", "null"]}, "id": STR, "grid": arr(INT)}), 202),
    ("POST", "/api/effects/{id}/particles_pick", "Effects", "Which cells of the drawn sheet are the particles: {indexes: [cell numbers]}; sets effect.set.picked and every group's sprites.picked; a cell with warnings can be picked, a cell with no picture cannot (400); recorded in the history (actor you)", OBJ, OBJ, 200),
    ("POST", "/api/effects/{id}/particles_recut", "Effects", "Cut the set's drawn sheet again as a particle sheet (a sheet drawn before batches knew they were particles, e.g. one read as 3x3 with cells blocked): exact equal cells, warnings only, free, from the stored sheet; 409 when nothing is drawn, a cell was already decided or the batch already is a particle batch. Answer 202 {id, generation}", OBJ, obj({"id": STR, "generation": STR}), 202),
    ("POST", "/api/effects/{id}/preview", "Effects", "The simulated burst of a sticker as a small looping WebP: {sticker_id, params: {gravity, magnitude, vortex, count, ..., sprite_px?: 32-512 (default 100), scale?: 1-4 (default 1)}, size?} (free); the sprites are fitted into sprite_px x scale px before simulating, invalid -> 400", OBJ, OBJ, 200),
    ("POST", "/api/effects/{id}/render", "Effects", "Render the final 512 px WebM of a sticker's burst and judge it (params as for preview, incl. sprite_px and scale); stored whatever the checks say (only a Telegram limit makes it FAILED)", OBJ, OBJ, 200),
    ("POST", "/api/effects/{id}/add", "Effects", "Add results to the pack as animated stickers tagged with their source emoji: {results?, pack_id?, sticker_ids?: only for these source stickers}; the click is the person's decision", OBJ, OBJ, 200),
    # --- generations
    ("GET", "/api/generations", "Generations", "Every batch (newest first) with the server's state", None, OBJ, 200),
    ("POST", "/api/generations", "Generations", "Start a batch from a prepared sheet, or run a reserved task. Idempotency-Key supported", ref("GenerationCreate"), ref("GenerationCreated"), 202),
    ("GET", "/api/generations/{id}", "Generations", "The full snapshot: result.json plus events", None, OBJ, 200),
    ("GET", "/api/generations/{id}/events", "Generations", "Server-sent events (Last-Event-ID or ?after= replays what was missed)", None, ref("Event"), 200),
    ("GET", "/api/generations/{id}/files", "Generations", "Where the batch's files are", None, OBJ, 200),
    ("GET", "/api/generations/{id}/edge_preview", "Generations", "One sticker with a stroke / trim, rendered on the fly (image/png)", None, None, 200),
    ("GET", "/api/generations/{id}/sheet_preview", "Generations", "The video sheet at a given fill (image/png)", None, None, 200),
    ("GET", "/api/generations/{id}/history", "Generations", "Every sticker's generation history, folded: {generation_id, stickers: [{id, index, key, name, status, review, lines, shown, last, stages: [{stage, count, last, lines}]}]}, decisions grouped by stage in the order they happened, newest line first; ?index=N for one sticker", None, OBJ, 200),
    ("POST", "/api/generations/{id}/more", "Generations", "The next prepared variation of the same subject", None, obj({"id": INT}), 202),
    ("POST", "/api/generations/{id}/regen", "Generations", "Regenerate one sticker as a 1x1 child batch", obj({"index": INT, "subject": STR}, ["index"]), obj({"id": INT}), 202),
    ("POST", "/api/generations/{id}/review", "Gates", "A human decision at a gate (Python's blocks are final)", ref("Review"), OBJ, 200),
    ("POST", "/api/generations/{id}/judge", "Gates", "The vision model pre-reviews the stickers (history lines only). Needs allow_vlm: true in the body (409 with consent_required otherwise): AI vision is the person's yes, asked once", ref("Judge"), obj({"id": INT, "scope": STR}), 202),
    ("GET", "/api/generations/{id}/captions", "Gates", "The stored AI caption of every cell of the sheet (grid read from result.json, 2x2 or 3x3): {generation_id, grid, cells: [{index, row, col, png, caption, text_visible, verdict, reasons, model}], missing, ready}. Read-only: no model, no consent", None, OBJ, 200),
    ("POST", "/api/generations/{id}/captions", "Gates", "Write the missing AI captions in the background (a model call per cell). Needs allow_vlm: true (409 with consent_required otherwise); {force?} captions again", obj({"allow_vlm": BOOL, "force": BOOL}, ["allow_vlm"]), obj({"id": INT, "force": BOOL}), 202),
    ("POST", "/api/generations/{id}/video_sheet", "Gates", "Build the video sheet from the approved stills", None, OBJ, 200),
    ("POST", "/api/generations/{id}/quick_sheet", "Gates", "Approve the kept stills, build and approve the video sheet (one click)", None, OBJ, 200),
    ("POST", "/api/generations/{id}/video_sheet/{aid}/video", "Gates", "Attach a returned video (raw body) to a video sheet and slice it", None, OBJ, 200),
    ("POST", "/api/generations/{id}/allow", "Gates", "\"Use it anyway\": allow, or take back (allow: false), a video sheet (kind: video_sheet; sheet A#), a sticker (kind: still) or an animation (kind: animation, the default) that Python blocked as a judgement call: a character touching its cell, a hole, a slot or a loop. Telegram's own limits (format, size, codec) and a cell with no picture stay final (409 with the reason). Pick the stickers with index, indexes or all; the permission is recorded on the sticker and its history and kept by every later cut. Answers {id, kind, indexes, index, allow}; GET /api/generations/{id} carries `allow` (what can be allowed now)", obj({"kind": {"type": "string", "enum": ["still", "animation", "video_sheet"]}, "sheet": STR, "index": {"oneOf": [INT, STR]}, "indexes": arr({"oneOf": [INT, STR]}), "all": BOOL, "allow": BOOL}), obj({"id": INT, "kind": STR, "indexes": arr({"oneOf": [INT, STR]}), "index": {"oneOf": [INT, STR]}, "allow": BOOL}), 202),
    ("GET", "/api/generations/removed", "Generations", "The batches in the trash, newest first: {batches: [{id, number, removed, by, subject}]} (owner only)", None, None, 200),
    ("POST", "/api/generations/{id}/remove", "Generations", "Move a batch to out/trash/batches (nothing is deleted); 409 in words while a job for it is in flight. Returns {id, number, removed, by}", None, OBJ, 200),
    ("POST", "/api/generations/{id}/restore", "Generations", "Put a removed batch back under the same name; 409 if a batch with that number exists now. Returns {id, number, restored}", None, OBJ, 200),
    ("POST", "/api/generations/{id}/join", "Generations", "Add to group: this batch and its whole family go under the family of {to: G### or number}; the target's root is the parent and names the family (flow/groups.py). -> {id, root, members}. 404 for a batch not on disk, 409 when it is already in that family", obj({"to": STR}), obj({"id": STR, "root": STR, "members": arr(STR)}), 200),
    ("POST", "/api/generations/{id}/pack", "Generations", "Earlier batches' drag and drop: this batch (its family) becomes another batch of the PACK of `to` ({to: G###}); {to: null} takes it out (a pack of its own). Stored on the family root (pack, pack_history); never a regeneration. {id, root, pack}", OBJ, OBJ, 200),
    ("POST", "/api/generations/{id}/leave", "Generations", "Take a batch out of its family (it becomes its own root; its own edits follow it). 409 for the family's root", None, obj({"id": STR, "root": STR, "left": STR, "members": arr(STR)}), 200),
    ("POST", "/api/generations/{id}/drop", "Gates", "Drop or restore a sticker from the set", obj({"index": INT, "dropped": BOOL}, ["index"]), OBJ, 200),
    ("POST", "/api/generations/{id}/animate", "Generations", "Animate a prepared video (no provider)", obj({"scope": STR, "index": INT}), OBJ, 202),
    ("POST", "/api/generations/{id}/video_size", "Generations", "Video size after the video is received, 0.5 to 1.5 (1 = as it is): {scale} for every animation of the batch, {scale, index} for one sticker (its own value wins over the batch's); scale null resets. reslice: true cuts again from the stored video, free (only that sticker when index is given)", obj({"scale": NUM, "index": INT, "reslice": BOOL}), obj({"id": INT, "anim_scale": NUM, "stickers": OBJ, "resliced": BOOL}), 200),
    ("POST", "/api/generations/{id}/keep_size", "Generations", "Motion's Keep approved size switch: on (the default) keeps every animation at its approved still's size and place, cutting off what moves past the frame (anim_metrics.cut_off_frames); off shrinks a clip until its whole motion fits; same: true (Same scale) wins over both, every sticker cut from its cell at one scale, uniform sizes as on the sheet (false clears it, absent leaves it). reslice: true cuts the animations again from the stored video (free)", obj({"on": BOOL, "same": BOOL, "reslice": BOOL}), obj({"id": INT, "keep_size": BOOL, "same_scale": BOOL, "resliced": BOOL}), 200),
    ("POST", "/api/generations/{id}/resize", "Generations", "Motion's Resize box (2026-10-15, replaces Keep approved size / Fit motion / Same scale on the screen): on sizes and places each animation by its hot spot (the pixels opaque in at least half the frames, anim_metrics.core_bbox) matched to its approved still; off cuts every sticker from its cell at one scale. reslice: true cuts again from the stored video (free)", obj({"on": BOOL, "reslice": BOOL}), obj({"id": INT, "resize": BOOL, "resliced": BOOL}), 200),
    ("POST", "/api/generations/{id}/position", "Generations", "One sticker's Position: offset [dx, dy] as a share of the canvas (each -0.5 to 0.5, + = right / down; null = as cut) and optionally its Size (scale 0.5 to 1.5), applied by moving the cut's window. reslice: true cuts that sticker again from the stored video (free)", obj({"index": INT, "offset": OBJ, "scale": NUM, "reslice": BOOL}), obj({"id": INT, "index": INT, "anim_offset": OBJ, "anim_scale": NUM, "resliced": BOOL}), 200),
    ("POST", "/api/generations/{id}/appearance", "Generations", "Set the outline / trim of the batch", obj({"outline": INT, "erode": INT, "reslice": BOOL}), OBJ, 200),
    ("POST", "/api/generations/{id}/edge", "Generations", "Apply or undo an edge snapshot", obj({"outline": INT, "erode": INT, "via": STR}), obj({"id": INT}), 202),
    ("POST", "/api/generations/{id}/reslice", "Generations", "Re-cut the animations from the stored video with the current edge", None, obj({"id": INT}), 202),
    ("POST", "/api/generations/{id}/recheck", "Generations", "Run the border check on animations made before it existed", None, OBJ, 202),
    ("POST", "/api/generations/{id}/edit", "Generations", "Save an edited still in place", OBJ, OBJ, 200),
    ("POST", "/api/generations/{id}/replace", "Generations", "Replace a still with a file edited outside the app (same S#, re-checked, back to approval, animation kept but marked from-the-previous-picture, old file kept for one undo) {index, png: base64} or {index, undo: true}; pack copies follow", OBJ, OBJ, 200),
    ("POST", "/api/generations/{id}/studio_edit", "Generations", "Layered edit of a sticker and its animation (open / commit)", OBJ, OBJ, 200),
    ("POST", "/api/generations/{id}/add", "Packs", "Add the approved stickers to a pack", OBJ, OBJ, 200),
    ("POST", "/api/generations/{id}/pack_add", "Packs", "Add chosen stickers to a pack", OBJ, OBJ, 200),
    ("POST", "/api/generations/{id}/recut_particles", "Generations", "A sheet of particles that was cut as stickers (a layout read from gutters, sticker rules on every cell, 512 px canvases) is cut again AS PARTICLES from the stored sheet, free: exact equal cells, no sticker rule blocks a cell. 409 when it already is a particle batch or a sticker was decided", None, obj({"id": INT}), 202),
    ("POST", "/api/generations/{id}/recut", "Generations", "\"Cut it anyway\": cut a batch whose sheet was stopped, from the stored sheet, free (409 once a sticker was decided)", None, obj({"id": INT}), 202),
    ("POST", "/api/generations/{id}/reveal", "Generations", "Open the batch's folder in the file manager", None, obj({"opened": STR}), 200),
    ("GET", "/api/history", "Generations", "Every PACK (the batches of one request by one person, made within 6 h of each other, or dropped onto it: pipeline._packs), each holding its batch FAMILIES as batches[]; a family is (a batch, its edits and redos, and the batches added to it: flow/groups.py), the most recently edited first, a page at a time by pack (?offset, ?limit); an item is the first batch's family root with variants[] (each batch of the family in the same item shape, root first) and relation (joined | redo | edit | null): {items: [{id, generation_id, prompt, created, edited, stage, error, ready, animated, grid: [rows, cols], cells: [{index, row, col, png, status, animated}], outline_px}], more, total}. The grid is the sheet's own (2x2 or 3x3, read from result.json) so a card can draw it as it was cut", None, OBJ, 200),
    ("GET", "/api/inputs", "Generations", "The prepared sheets found in the watch folders", None, OBJ, 200),
    ("GET", "/api/prepared/match", "Generations", "Which prepared subject a request names (?prompt=): {subject, variants, prefer}. Whole signature words only; free", None, OBJ, 200),
    ("GET", "/api/prepared/setting", "Generations", "Whether matching requests are served from the watch folder instead of a paid call {prefer}", None, OBJ, 200),
    ("POST", "/api/prepared/setting", "Generations", "Owner only: serve matching requests from the watch folder {prefer: bool} (STICKERLOOM_PREFER_PREPARED wins when set)", OBJ, OBJ, 200),
    # --- live generation
    ("POST", "/api/live/cost", "Live generation", "Price one call of a model (a quote, free)", OBJ, OBJ, 200),
    ("POST", "/api/live/sheet", "Live generation", "Reserve a task (the G1 approval) and start the sheet job; spends credits. Idempotency-Key supported", ref("LiveSheet"), ref("LiveJob"), 200),
    ("POST", "/api/live/video", "Live generation", "Start the video job (Kling, Grok…) for a built video sheet; spends credits. Idempotency-Key supported. Optional video_prompt (max 6000) is sent verbatim; redo: true regenerates the video in the same batch (the sliced sheet is retired as SUPERSEDED, its clips kept as anim_versions, the next sheet is built from the same kept stickers); from_generation + parent + regen_of: a new generation of that batch (its family), from the batch's own cells and tags", OBJ, OBJ, 200),
    ("POST", "/api/live/ref", "Live generation", "Store a reference image (raw body, ?name=)", None, OBJ, 200),
    ("GET", "/api/jobs", "Live generation", "Jobs for the operator, newest first", None, obj({"jobs": arr(ref("Job"))}), 200),
    ("POST", "/api/jobs", "Live generation", "Create a job file", OBJ, ref("Job"), 200),
    ("GET", "/api/jobs/{id}", "Live generation", "One job (the page polls it while waiting)", None, ref("Job"), 200),
    ("POST", "/api/jobs/{id}/claim", "Live generation", "Operator (owner only): store the provider ticket BEFORE waiting {ticket}", OBJ, ref("Job"), 200),
    ("POST", "/api/jobs/{id}/done", "Live generation", "Operator (owner only): attach the finished file {file, model, cost?}", OBJ, ref("Job"), 200),
    ("POST", "/api/jobs/{id}/fail", "Live generation", "Operator (owner only): mark the job failed {reason}", OBJ, ref("Job"), 200),
    ("POST", "/api/jobs/{id}/requeue", "Live generation", "Operator (owner only): a TIMEOUT / FAILED job asks again; a job that holds a provider ticket waits for the same provider job (no second charge)", None, ref("Job"), 200),
    ("POST", "/api/jobs/{id}/check", "Live generation", "One free read-only provider get; completed results are downloaded and reconciled by the same ticket. Human history records DIVERGENCE when local FAILED/TIMEOUT disagrees", None, ref("Job"), 200),
    ("POST", "/api/jobs/{id}/continue", "Live generation", "Continue a stalled job on its stored ticket via jobs.resume; never creates a paid request. 409 without a ticket or while already waiting", None, ref("Job"), 200),
    ("POST", "/api/packs/{id}/merge", "Library", "Fold this pack into another {into}: an animated sticker upgrades its still twin (the still keeps its id, name, emoji and particle links), the rest moves; the emptied pack goes to the trash", None, OBJ, 200),
    ("POST", "/api/jobs/{id}/dismiss", "Live generation", "Take a finished or failed job off the queue for good, for every browser (dismissed: {at, by}); the job itself stays. 409 while it is still running", None, ref("Job"), 200),
    ("POST", "/api/jobs/{id}/retry_estimate", "Live generation", "Quote a new paid Retry; {credits, message}. Creates no provider request", None, obj({"credits": NUM, "message": STR}), 200),
    ("POST", "/api/jobs/{id}/retry", "Live generation", "Explicit PAID re-request of a FAILED/TIMEOUT job; requires a current quoted estimate and go: true. Repeated requests return the same replacement job", obj({"go": BOOL, "estimate": NUM}, ["go", "estimate"]), ref("Job"), 200),
    ("GET", "/api/models", "Live generation", "Curated models, every other Higgsfield model, and the style presets", None, OBJ, 200),
    ("GET", "/api/higgsfield", "Live generation", "Is the CLI there, the balance, today's spend (never a credential)", None, OBJ, 200),
    ("GET", "/api/metrics", "Generations", "Quality and timing numbers over every batch (owner only): time to the first sticker, approval rates at the two gates, the regeneration rate, per-batch lines", None, OBJ, 200),
    ("GET", "/api/ai", "Live generation", "Is a language model available (never the key): the active backend, the person's choice and what is available", None, OBJ, 200),
    ("POST", "/api/ai/backend", "Live generation", "Choose the AI backend {backend: auto | local | cloud} and/or model {model: a listed provider:model id; empty clears saved choices; legacy bare ids select local models} (owner only; auto keeps a working backend and only a failed call switches it)", OBJ, OBJ, 200),
    ("GET", "/api/llm/models", "Chat", "Local model fields {models, current, preference, chosen, configured, ok, why}, plus grouped local/fallback/openai catalogs {id, model, label, provider, vision, free}, cloud default and selected model; local readiness is a real cached probe", None, OBJ, 200),
    ("GET", "/api/vision", "Gates", "The vision judge: model and policy", None, OBJ, 200),
    ("GET", "/api/prefs", "Chat", "The person's own settings every screen shares: {stage} (auto | prompt | emojis | animation | export | api; default auto)", None, OBJ, 200),
    ("POST", "/api/prefs", "Chat", "Set the person's slider {stage}: the chat and the Studio follow it; an unknown stage is a 400", OBJ, OBJ, 200),
    ("POST", "/api/plan", "Live generation", "Preview a plan; nothing is reserved. Body {prompt, grid?, style_id?, ai?, loop?, actions?: creative | predefined (nine from the full emoji bank)}. Manual batches: {emojis: nine glyphs as a string or list, pack_id?, prompt?, decisions?: cell-number -> overwrite|add|skip, replacements?: cell-number -> unused emoji, style_id?, loop?}; returns {entries, ready, waiting, suggestion, plan?}. Pack ownership and repeats are checked server-side; unresolved duplicates never return a runnable plan", OBJ, OBJ, 200),
    ("POST", "/api/plan/next", "Live generation", "Next batch: the next unclaimed batch of the batches on screen ({gens}) as a plan to read first, or {complete, message}; nothing is reserved or spent", OBJ, OBJ, 200),
    ("POST", "/api/plan/more", "Live generation", "Several batches in one Generate: the plans of the n batches after a previewed one ({prompt, plan, n, style_id?, loop?, gens?}): an emoji pack's next preset grids, else the next unused bank actions; with gens (a Next-batch draft) the session's own grids and actions are skipped and it never goes past batch 4. {plans, existing}; nothing is reserved or spent", OBJ, OBJ, 200),
    ("GET", "/api/generations/{id}/map", "Generations", "The project map of a batch (flow/projects.py): {project {id, title, request}, current, batches [{root, title, picked, preset, sheets [{id, number, n, label, relation, prompt, prompt_changed, status making|failed|not_cut|ready, stage, thumb, counts {stickers, ready, blocked, animated}, videos [{id, status, used, slots, thumb, animated, blocked}], jobs [{id, kind, status}], created}]}]}. A member sees only their own batches; another person's batch is a 404", None, OBJ, 200),
    ("GET", "/api/generations/{id}/family", "Generations", "The batch this generation is in: {root, picked, members [{id, generation_id, n, relation, stage, ready, sheet_model}]}", None, OBJ, 200),
    ("POST", "/api/generations/{id}/pick", "Generations", "Pick this generation for its batch (one per batch, stored on the root); answers the family", None, OBJ, 200),
    ("POST", "/api/generations/{id}/pick_video", "Generations", "Make an earlier animation {sheet} of this generation current again (re-cut from its stored video, free); 409 while a video is being cut", OBJ, OBJ, 200),
    ("POST", "/api/generations/{id}/remove_video", "Generations", "Take animation {sheet} out of the row (REJECTED, files kept); 409 for the one in use", OBJ, OBJ, 200),
    ("GET", "/api/tasks", "Live generation", "Reserved tasks", None, OBJ, 200),
    ("POST", "/api/tasks", "Live generation", "Reserve a task (the G1 approval)", OBJ, OBJ, 200),
    ("GET", "/api/tasks/{id}", "Live generation", "One task", None, OBJ, 200),
    # --- files and signed links
    ("GET", "/out/{path}", "Files", "A generated file (the path is resolved, then checked against the root)", None, None, 200),
    ("POST", "/api/assets/sign", "Files", "A signed, expiring link to one file under out/", ref("AssetSign"), ref("AssetLink"), 200),
    ("GET", "/api/assets/{token}", "Files", "Serve a signed link until it expires (403 when forged or expired)", None, None, 200),
    # --- library and packs
    ("GET", "/api/library", "Library", "Only the caller's packs, recent stickers and totals; legacy packs belong to local. Every pack has owner. Another person's pack or /lib file returns 404", None, OBJ, 200),
    ("POST", "/api/import", "Imports", "Owner only. Raw PNG/JPEG/WebP or MP4/MOV/WebM bytes, query name, prompt?, generation?, sheet?, retry?, again? (deliberate duplicate), grid_ok? (use the destination grid), job? (a failed local job this file completes), as_new? (import as an unrelated batch even when a failed job matches), batch? (any G### of a batch: a picture becomes its next generation, a video its main generation's next animation, the sheet it came from read from its first frame; the answer says which, match + message). A file naming a provider ticket with exactly one matching failed job completes it (no second charge); several matches are a 409 with candidates. Videos require an approved G3 sheet. 202 starts processing; 200 duplicate returns its existing job/task/generation/effect/import, status and recoverable. Retry reuses a failed import's batch. Free", None, OBJ, 202),
    ("GET", "/api/imports/candidates", "Imports", "Owner only. The person's own FAILED/TIMEOUT jobs holding ?ticket=, newest first: prompt, time and thumbnail (?ticket=a provider job id). Free", None, OBJ, 200),
    ("GET", "/api/higgsfield/history", "Imports", "Owner only. Recent image/video jobs, newest first, with known duplicate locations; size 1-100, default 40. Free, read-only", None, obj({"jobs": arr(OBJ)}), 200),
    ("POST", "/api/higgsfield/import", "Imports", "Owner only. Download an existing completed provider job and import its result with the same deduplication and G3 checks; local_job completes that failed local job instead of starting an unrelated batch; bounded HTTPS download, no paid generation", obj({"id": STR, "prompt": STR, "generation": STR, "sheet": STR, "retry": BOOL, "local_job": STR}, ["id"]), OBJ, 202),
    ("GET", "/api/generations/{id}/export.zip", "Generations", "The Studio's Download .zip: the batch's accepted stickers (the animation where it is ready, else the still, renamed to the export contract {emoji}-{slug}-{tag}-{sNN}-{G###}-{date}) and a manifest.json v1; animations compressed small as the pack's zip (?light=0 for the full-size files); 409 when nothing is accepted yet (application/zip)", None, None, 200),
    ("POST", "/api/packs", "Library", "Create a pack", obj({"name": STR}), OBJ, 200),
    ("POST", "/api/packs/{id}", "Library", "Rename, reorder or set the cover of a pack. {lead: true} makes it its group's parent; {parent: <pack id>} nests it under that pack, {parent: \"\"} takes it out", obj({"name": STR, "cover": nullable(STR), "order": arr(STR), "lead": BOOL, "parent": nullable(STR)}), OBJ, 200),
    ("POST", "/api/packs/{id}/delete", "Library", "Delete a pack: SOFT, it goes to the trash with its stickers and files untouched (GET /api/trash lists it). Returns {ok, id, trashed, name, stickers}", None, OBJ, 200),
    ("POST", "/api/packs/{id}/restore", "Library", "Put a deleted pack back from the trash under the same id, stickers and cover untouched (404 when it is not in the trash). Returns {ok, id, restored, name, stickers}", None, OBJ, 200),
    ("POST", "/api/packs/{id}/stickers", "Library", "Add a sticker to a pack", OBJ, OBJ, 200),
    ("POST", "/api/packs/{id}/render", "Library", "Save the editor's 512x512 canvas as a sticker (raw PNG body)", None, OBJ, 200),
    ("POST", "/api/packs/{id}/stickers/{sid}", "Library", "Update a sticker {name?, emoji?, action?}; action is a canonical emoji-bank token, empty clears it; invalid tokens return 400", OBJ, OBJ, 200),
    ("POST", "/api/packs/{id}/stickers/{sid}/replace", "Library", "Replace with a file edited outside the app (same id, name, emoji, particle links; type follows the file: PNG/JPG/WebP static 512 px 512 KB, WebM animated 512 px 256 KB). The batch and this pack take it; copies in other packs are offered ({others: [ids] | true}); the old file is kept for one undo ({undo: true})", OBJ, OBJ, 200),
    ("POST", "/api/packs/{id}/stickers/{sid}/animate", "Library", "Animate a library sticker", OBJ, OBJ, 200),
    ("POST", "/api/packs/{id}/stickers/{sid}/move", "Library", "Move one sticker into another pack: {to}", OBJ, OBJ, 200),
    ("POST", "/api/stickers/move", "Library", "Bulk move into one pack, all or nothing: {to, items: [{pack_id, id}]} -> {moved, skipped, to}", OBJ, OBJ, 200),
    ("POST", "/api/packs/{id}/stickers/{sid}/delete", "Library", "Remove a sticker", None, OBJ, 200),
    ("POST", "/api/stickers/delete", "Library", "Bulk delete: [{pack_id, id}]", OBJ, OBJ, 200),
    ("POST", "/api/cutout", "Library", "A photo (raw body) becomes a cut-out PNG; X-Cutout header describes the method", None, None, 200),
    ("POST", "/api/packs/{id}/telegram", "Telegram", "Create the pack on Telegram, or add what is new: queued, one send at a time. Body {name?, mode?, retime?} (retime: videos shorter than 3 s go up stretched to 3 s): mode once (default: the same content is never sent twice, the earlier export is returned with already=true and Telegram is not called), replace (send on purpose) or new_set (a second numbered set); 202 {id, state: queued, position}: GET /api/telegram/sends/{tid} answers its outcome; every send is recorded", OBJ, OBJ, 202),
    ("GET", "/api/packs/{id}/telegram", "Telegram", "Dry run: what would be created and every problem", None, OBJ, 200),
    ("GET", "/api/telegram/sends/{tid}", "Telegram", "One queued send's outcome, its sender only (404 for anyone else): queued | sending | done | failed with error", None, OBJ, 200),
    ("GET", "/api/packs/{id}/stickers/{sid}/particles", "Effects", "One sticker's particle versions: rows[] (saved, oldest first: {version, id, label, kind, sprites[] inside the row, n_sprites, motion, preview, renders, addable: {render, pack_id} | null, in_pack[], credits, job, shared_with, saved_at}) and drafts[] (started, not saved). Also sets[] and grouped legacy runs:[{effect,mode,cells,saved,imported_as:[Pids]}]. Each run groups all sprite slices; imported_as identifies owned sets that already contain it. Compatibility created[],saved[],effects[],can_make remain unchanged; a member gets an empty gallery", None, OBJ, 200),
    ("GET", "/api/packs/{id}/particles", "Particles", "The pack's particle studio in one read: {pack_id, sets[] (the sets assigned to it, cards as in GET /api/particles), bursts[] (every burst rendered for it, with its warnings and whether it is in the pack), counts: {sticker id: {created, saved}}} (docs/architecture.md ('Reactions')); a member gets {}", None, obj({"pack_id": STR, "sets": arr(OBJ), "bursts": arr(OBJ), "counts": OBJ}), 200),
    ("GET", "/api/packs/{id}/short", "Library", "The pack's animations shorter than 3 s, which an export asks about (Retime to 3 s or keep their length): {seconds: 3, short: [{id, name, emoji, seconds}]}", None, obj({"seconds": NUM, "short": arr(OBJ)}), 200),
    ("GET", "/api/packs/{id}/export.zip", "Library", "Download the pack: every sticker file (.webm animated, .png / .webp static, renamed to the export contract {emoji}-{slug}-{tag}-{sNN}-{G###}-{date}) and a manifest.json v1. Animated stickers ship compressed small by default (every frame kept, the best quality down to crf 46 that fits ~64 KB; ?light=0 keeps the full-size files; each row records crf/kb/frames/fps/fits); ?retime=1 plays an animation shorter than 3 s over 3 s (retimed_from on its row) (application/zip)", None, None, 200),
    ("GET", "/api/packs/{id}/telegram.zip", "Telegram", "No-credentials fallback: the files for @stickers (application/zip)", None, None, 200),
    ("GET", "/api/telegram", "Telegram", "Connected or not and which bot (never the token)", None, OBJ, 200),
    ("POST", "/api/telegram/config", "Telegram", "Save the bot token and user id", obj({"token": STR, "user_id": STR}), OBJ, 200),
    ("POST", "/api/telegram/disconnect", "Telegram", "Forget the token", None, OBJ, 200),
    # --- projects (video / GIF)
    ("GET", "/api/projects", "Projects", "Video / GIF projects", None, OBJ, 200),
    ("POST", "/api/projects", "Projects", "Import a video or GIF (raw body, ?name=)", None, OBJ, 200),
    ("GET", "/api/projects/{id}", "Projects", "One project", None, OBJ, 200),
    ("POST", "/api/projects/{id}", "Projects", "Update a project (autosave)", OBJ, OBJ, 200),
    ("POST", "/api/projects/{id}/delete", "Projects", "Delete a project", None, OBJ, 200),
    ("POST", "/api/projects/{id}/render", "Projects", "Render a project to WebM / WebP / GIF", OBJ, None, 200),
    ("POST", "/api/projects/from_sticker", "Projects", "Open an animated sticker as a project", obj({"pack_id": STR, "sticker_id": STR}, ["pack_id", "sticker_id"]), OBJ, 200),
    # --- watch folders, search, health
    ("GET", "/api/watch", "Inputs", "The watch folders side by side, plus the trash", None, OBJ, 200),
    ("POST", "/api/watch/remove", "Inputs", "Move a folder pair to the trash", OBJ, OBJ, 200),
    ("POST", "/api/watch/restore", "Inputs", "Restore a folder pair under its own names", OBJ, OBJ, 200),
    ("POST", "/api/watch/purge", "Inputs", "Delete a trashed pair for good", OBJ, OBJ, 200),
    ("GET", "/api/trash", "Trash", "Everything in the trash with exactly what a purge would remove (owner only): {batches: [{type: 'batch', id, number, subject, removed, by, stickers, content, files_total, bytes, files: [{path, bytes}] (first 40; files_truncated), db: {available, reason?, stickers, indexed, shared_in_pool, vectors, assets, events, video_sheets, reviews_kept, tasks_kept}, copies_in_packs: [{id, name, stickers, trashed}] (they stay), shared: {pool, packs}, in_flight[], needs_confirm, confirm_words, blocked}], packs: [{type: 'pack', id, name, deleted, by, stickers, files: [{sticker, name, file, bytes, missing, shared}], files_total, bytes, shared: [{sticker, name, file, also_in: [{id, name, trashed}]}], from_batches[], particle_sets[], needs_confirm, confirm_words, blocked}], totals, purge_all: {count, phrase, skipped[]}, database, purge (the last purge task or null), record (the last 20 ledger lines)}", None, OBJ, 200),
    ("POST", "/api/trash/purge", "Trash", "Delete ONE trashed batch or pack for good: {type: batch|pack, id, confirm_shared?}. Refused 404 when it is not in the trash, 409 in words while a job is in flight or a purge is running, and 409 naming the packs (or the shared pool) until {confirm_shared: true} when it shares a sticker file with another pack or has stickers in the shared pool. Runs in its own thread: 200 + the finished task, or 202 + the running task to poll at GET /api/trash/purges/{id}. Idempotent: an interrupted purge is run again; an item purged before answers 200 with {already: true}", obj({"type": {"type": "string", "enum": ["batch", "pack"]}, "id": STR, "confirm_shared": BOOL}, ["type", "id"]), OBJ, 200),
    ("POST", "/api/trash/purge_all", "Trash", "Delete everything in the trash that needs no confirmation of its own: {confirm: 'purge N', kind?: batch|pack} where N is the count GET /api/trash gives in purge_all (with kind: batch, only the removed batches: their count is purge_batches) (409 in words when the typed phrase or the count no longer matches the trash). Items that share stickers or have a job in flight are skipped and listed in `refused`, in words. Same 200 / 202 answer as purge; nothing to delete is a 200 with nothing_to_do", obj({"confirm": STR}, ["confirm"]), OBJ, 200),
    ("GET", "/api/trash/purges/{id}", "Trash", "The progress of a purge: {id, status: running|done|failed|interrupted, total, done, items[], results[], refused[], error, by, started, finished}", None, OBJ, 200),
    ("GET", "/api/watch/thumb/{name}", "Inputs", "A cached thumbnail of a sheet folder (image/jpeg)", None, None, 200),
    ("GET", "/api/search", "Search", "Search stickers (Postgres when up, else files); ?q=", None, obj({"results": arr(OBJ), "via": STR}), 200),
    ("GET", "/api/health", "System", "Every dependency reports itself; nothing raises", None, ref("Health"), 200),
    ("GET", "/api/health/models", "System", "Language, vision and tracing status", None, OBJ, 200),
    ("GET", "/api/health/storage", "System", "The out/ folder: free space, generations, who holds the writer lock", None, OBJ, 200),
    ("GET", "/api/openapi.json", "System", "This document", None, OBJ, 200),
]


def _params(path: str) -> list:
    import re
    return [{"name": n, "in": "path", "required": True, "schema": STR} for n in re.findall(r"\{(\w+)\}", path)]


def build(server_url: str = "http://127.0.0.1:8770") -> dict:
    paths: dict = {}
    for method, path, tag, summary, req, resp, status in ROUTES:
        op: dict = {"tags": [tag], "summary": summary, "operationId": method.lower() + "_" + path.strip("/").replace("/", "_").replace("{", "").replace("}", "").replace(".", "_").replace("-", "_"),
                    "responses": {str(status): {"description": "ok"}, "400": ERR, "401": ERR, "403": ERR, "404": ERR, "409": ERR, "429": ERR, "500": ERR}}
        if resp is not None:
            ctype = "text/event-stream" if path.endswith("/events") else "application/json"
            op["responses"][str(status)]["content"] = {ctype: {"schema": resp}}
        if req is not None:
            op["requestBody"] = {"required": True, "content": {"application/json": {"schema": req}}}
        params = _params(path)
        if path == "/api/import":
            op["requestBody"] = {"required": True, "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}
            params += [{"name": name, "in": "query", "required": False, "schema": BOOL if name in ("retry", "as_new", "again", "grid_ok") else STR} for name in ("name", "prompt", "generation", "sheet", "retry", "job", "as_new", "batch", "again", "grid_ok", "grid")]
            op["responses"]["200"] = {"description": "duplicate", "content": {"application/json": {"schema": OBJ}}}
        if path == "/api/higgsfield/import":
            op["responses"]["200"] = {"description": "duplicate", "content": {"application/json": {"schema": OBJ}}}
        if path == "/api/higgsfield/history":
            params.append({"name": "size", "in": "query", "required": False, "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 40}})
        if method == "GET" and path in ("/api/generations", "/api/jobs", "/api/chat/sessions"):
            params += [{"name": "limit", "in": "query", "required": False, "schema": INT, "description": "1-500: page the list; the answer then adds total, limit, offset"},
                       {"name": "offset", "in": "query", "required": False, "schema": INT, "description": "how many to skip (0 or more)"}]
        if method == "POST" and ("messages" in path or path in ("/api/generations", "/api/live/sheet", "/api/live/video")):
            params.append({"name": "Idempotency-Key", "in": "header", "required": False, "schema": STR,
                           "description": "the same key within 24 h returns the first answer and runs nothing again"})
        if path.endswith("/events"):
            params.append({"name": "Last-Event-ID", "in": "header", "required": False, "schema": STR})
        if params:
            op["parameters"] = params
        paths.setdefault(path, {})[method.lower()] = op
    return {"openapi": "3.1.0",
            "info": {"title": "stickerloom API", "version": VERSION,
                     "description": "High-quality animated stickers: chats, generations, gates, live generation, library, Telegram. Everything is JSON addressable by id (G012, G012/S3, J004, S002). "
                                    "Safety rules: Host/Origin guard, idempotency keys, signed links. "
                                    "Every route is also served under /api/v1/...; every answer carries X-API-Version and X-Request-Id (send your own X-Request-Id to trace a call); "
                                    "list routes take ?limit=1-500&offset=N (opt-in) and then report {total, limit, offset}."},
            "servers": [{"url": server_url}],
            "components": {"schemas": SCHEMAS, "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}},
            "security": [{}, {"bearerAuth": []}],
            "paths": paths}


# ---- TypeScript types from the component schemas (no npm: a small generator, so nothing is hand-copied) ----
def _ts(s: dict, indent: int = 0) -> str:
    if "$ref" in s:
        return s["$ref"].rsplit("/", 1)[1]
    if "anyOf" in s:
        return " | ".join(_ts(x, indent) for x in s["anyOf"])
    t = s.get("type")
    if "enum" in s:
        return " | ".join(f'"{v}"' for v in s["enum"])
    if t == "string":
        return "string"
    if t in ("integer", "number"):
        return "number"
    if t == "boolean":
        return "boolean"
    if t == "null":
        return "null"
    if t == "array":
        inner = _ts(s["items"], indent)
        return f"({inner})[]" if "|" in inner else f"{inner}[]"
    if t == "object":
        props = s.get("properties")
        if not props:
            return "Record<string, unknown>"
        req = set(s.get("required", []))
        pad = "  " * (indent + 1)
        lines = [f"{pad}{k}{'' if k in req else '?'}: {_ts(v, indent + 1)};" for k, v in props.items()]
        if s.get("additionalProperties") is True:
            lines.append(f"{pad}[key: string]: unknown;")
        return "{\n" + "\n".join(lines) + "\n" + "  " * indent + "}"
    return "unknown"


def typescript(spec: dict | None = None) -> str:
    spec = spec or build()
    out = ["// Generated by `python -m stickerloom openapi --ts`: do not edit. Source: stickerloom/api/openapi.py", ""]
    for name, sch in spec["components"]["schemas"].items():
        out.append(f"export interface {name} {_ts(sch)}" if sch.get("type") == "object" and sch.get("properties") else f"export type {name} = {_ts(sch)};")
        out.append("")
    out.append("export const routes = " + _routes_literal(spec) + " as const;")
    return "\n".join(out) + "\n"


def _routes_literal(spec: dict) -> str:
    import json
    items = {f"{m.upper()} {p}": op["operationId"] for p, ops in spec["paths"].items() for m, op in ops.items()}
    return json.dumps(items, indent=2)
