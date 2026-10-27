# api

The HTTP contract, generated from `GET /api/openapi.json`. Do not edit the
table by hand: serve the app and fetch the contract, then re-render.

```powershell
$env:STICKERLOOM_PROVIDER="fake"
$env:STICKERLOOM_NO_REAL_CLI="1"
$env:STICKERLOOM_OUT="$env:TEMP\sl_out"
$env:STICKERLOOM_INPUT="$env:TEMP\sl_in"
python -m stickerloom serve --port 8799
Invoke-RestMethod -Uri "http://127.0.0.1:8799/api/openapi.json" `
  -OutFile "$env:TEMP\sl_openapi.json"
```

Contract: OpenAPI 3.1.0, title `stickerloom API`, version `1.0.0`.
200 operations over 184 paths, 24 schemas.

| method | path | summary |
|---|---|---|
| GET | `/api/ai` | Is a language model available (never the key): the active backend, the person's choice and what is available |
| POST | `/api/ai/backend` | Choose the AI backend {backend: auto / local / cloud} and/or model {model: a listed provider:model id; empty clears saved choices; legacy bare ids select local models} (owner only; auto keeps a working backend and only a failed call switches it) |
| POST | `/api/assets/sign` | A signed, expiring link to one file under out/ |
| GET | `/api/assets/{token}` | Serve a signed link until it expires (403 when forged or expired) |
| GET | `/api/chat/agent` | Optional `session` query: resolve the saved AI model under the caller's session permissions (404 if inaccessible); otherwise use global defaults. Which model runs the assistant and the vision judge, what is available (the local model by a real probe, with the reason when it cannot answer) and `agent_status` {fallback, reason}: true when the chat is on its rules only |
| GET | `/api/chat/sessions` | List chats, newest first |
| POST | `/api/chat/sessions` | Create a chat |
| GET | `/api/chat/sessions/{id}` | A whole chat for display: messages with steps and cards (generation cards carry their live stickers), memory summary |
| POST | `/api/chat/sessions/{id}/delete` | Delete a chat (its stickers stay) |
| POST | `/api/chat/sessions/{id}/messages` | Send a message or a button action; the turn runs in the background (409 while the last one runs). Idempotency-Key supported |
| POST | `/api/chat/sessions/{id}/settings` | Grid, Ask-before-spending, style, AI vision, the stage (prompt / emojis / animation / export / api), the models (image / video / ai) and the creator's bypass; no chat turn. Unknown keys are ignored; an unknown style or stage is a 400 |
| GET | `/api/chat/sessions/{id}/stream` | SSE of the turn in progress (native FastAPI): `event: turn` {working, count, message} each time the last message changes, then `event: done` {working: false, count}; retry 3000, a comment every 15 s, ends after 10 minutes. The same access as GET /api/chat/sessions/{id} (401 / 403 / 404 as JSON before any event) |
| POST | `/api/cutout` | A photo (raw body) becomes a cut-out PNG; X-Cutout header describes the method |
| GET | `/api/effects` | Effects, newest last: id, pack, mode, status, counts |
| POST | `/api/effects` | Start an effect for a pack: {pack_id, sticker_ids / "all", mode: video / sim, grid: 2x2 / 3x3, note?, allow_vlm?}; the analysis runs in the background (poll the record) |
| GET | `/api/effects/{id}` | One effect: stickers, groups (subject, pieces, screen colour, motion presets, sprites), the particle set (set: {grid, elements, options, source, by, status: SUGGESTED / REQUESTED / DRAWN, job?, generation?, picked}), video jobs, results with their checks, history |
| POST | `/api/effects/{id}/add` | Add results to the pack as animated stickers tagged with their source emoji: {results?, pack_id?, sticker_ids?: only for these source stickers}; the click is the person's decision |
| POST | `/api/effects/{id}/analyse` | Read the stickers again (allow_vlm: true lets a vision model look at the pictures; otherwise the built-in table answers) |
| POST | `/api/effects/{id}/estimate` | The text-only video of a group before anything is spent: prompt, screen colour, cells, credits, model (free) |
| POST | `/api/effects/{id}/particles` | Draw the particle sheet (an ordinary sheet job, outline 0, one sheet for the whole effect): {grid?, elements, go}; 409 with the estimate unless go: true. The batch is a particle batch (kind: particles: exact equal cells, no sticker rule blocks a cell, warnings only); when it is cut every group's sprites are {generation, picked} and effect.set is REQUESTED then DRAWN. Replaces pieces (kept as an alias) |
| POST | `/api/effects/{id}/particles_estimate` | The ONE AI-drawn sheet of particles for the whole effect before anything is spent: {grid?: 2x2 / 3x3, elements: [the person's picks, 1..rows*cols; fewer are cycled as variants]}; the prompt, the cells, screen colour, credits, model (free). Replaces pieces_estimate (kept as an alias; `group` there means that group's pieces) |
| POST | `/api/effects/{id}/particles_pick` | Which cells of the drawn sheet are the particles: {indexes: [cell numbers]}; sets effect.set.picked and every group's sprites.picked; a cell with warnings can be picked, a cell with no picture cannot (400); recorded in the history (actor you) |
| POST | `/api/effects/{id}/particles_recut` | Cut the set's drawn sheet again as a particle sheet (a sheet drawn before batches knew they were particles, e.g. one read as 3x3 with cells blocked): exact equal cells, warnings only, free, from the stored sheet; 409 when nothing is drawn, a cell was already decided or the batch already is a particle batch. Answer 202 {id, generation} |
| POST | `/api/effects/{id}/plan` | Edit a group: {group, elements?, subject?, style?, sprites?: "own" / {generation: N, picked?: [cells]}}; linted (400 with the reason) |
| POST | `/api/effects/{id}/preview` | The simulated burst of a sticker as a small looping WebP: {sticker_id, params: {gravity, magnitude, vortex, count, ..., sprite_px?: 32-512 (default 100), scale?: 1-4 (default 1)}, size?} (free); the sprites are fitted into sprite_px x scale px before simulating, invalid -> 400 |
| POST | `/api/effects/{id}/render` | Render the final 512 px WebM of a sticker's burst and judge it (params as for preview, incl. sprite_px and scale); stored whatever the checks say (only a Telegram limit makes it FAILED) |
| POST | `/api/effects/{id}/suggest` | Candidate particles for the ONE set of the whole effect: {grid?: 2x2 / 3x3, allow_vlm?}; the vision model looks at one picture (the master sheet of the batch the stickers were cut from, else a contact sheet of the stickers) only with allow_vlm: true, else the built-in table answers. Answer: {options: [8-12 short names], n (cells of the grid), grid, source: {kind: batch / contact, generation?}, by: vlm / table, model?, notes}; stored in effect.set |
| POST | `/api/effects/{id}/video` | Start the text-only Kling video of a group; 409 with the estimate unless go: true (the click is the go-ahead) |
| GET | `/api/generations` | Every batch (newest first) with the server's state |
| POST | `/api/generations` | Start a batch from a prepared sheet, or run a reserved task. Idempotency-Key supported |
| GET | `/api/generations/removed` | The batches in the trash, newest first: {batches: [{id, number, removed, by, subject}]} (owner only) |
| GET | `/api/generations/{id}` | The full snapshot: result.json plus events |
| POST | `/api/generations/{id}/add` | Add the approved stickers to a pack |
| POST | `/api/generations/{id}/allow` | "Use it anyway": allow, or take back (allow: false), a video sheet (kind: video_sheet; sheet A#), a sticker (kind: still) or an animation (kind: animation, the default) that Python blocked as a judgement call: a character touching its cell, a hole, a slot or a loop. Telegram's own limits (format, size, codec) and a cell with no picture stay final (409 with the reason). Pick the stickers with index, indexes or all; the permission is recorded on the sticker and its history and kept by every later cut. Answers {id, kind, indexes, index, allow}; GET /api/generations/{id} carries `allow` (what can be allowed now) |
| POST | `/api/generations/{id}/animate` | Animate a prepared video (no provider) |
| POST | `/api/generations/{id}/appearance` | Set the outline / trim of the batch |
| GET | `/api/generations/{id}/captions` | The stored AI caption of every cell of the sheet (grid read from result.json, 2x2 or 3x3): {generation_id, grid, cells: [{index, row, col, png, caption, text_visible, verdict, reasons, model}], missing, ready}. Read-only: no model, no consent |
| POST | `/api/generations/{id}/captions` | Write the missing AI captions in the background (a model call per cell). Needs allow_vlm: true (409 with consent_required otherwise); {force?} captions again |
| POST | `/api/generations/{id}/drop` | Drop or restore a sticker from the set |
| POST | `/api/generations/{id}/edge` | Apply or undo an edge snapshot |
| GET | `/api/generations/{id}/edge_preview` | One sticker with a stroke / trim, rendered on the fly (image/png) |
| POST | `/api/generations/{id}/edit` | Save an edited still in place |
| GET | `/api/generations/{id}/events` | Server-sent events (Last-Event-ID or ?after= replays what was missed) |
| GET | `/api/generations/{id}/export.zip` | The Studio's Download .zip: the batch's accepted stickers (the animation where it is ready, else the still, renamed to the export contract {emoji}-{slug}-{tag}-{sNN}-{G###}-{date}) and a manifest.json v1; animations compressed small as the pack's zip (?light=0 for the full-size files); 409 when nothing is accepted yet (application/zip) |
| GET | `/api/generations/{id}/family` | The batch this generation is in: {root, picked, members [{id, generation_id, n, relation, stage, ready, sheet_model}]} |
| GET | `/api/generations/{id}/files` | Where the batch's files are |
| GET | `/api/generations/{id}/history` | Every sticker's generation history, folded: {generation_id, stickers: [{id, index, key, name, status, review, lines, shown, last, stages: [{stage, count, last, lines}]}]}, decisions grouped by stage in the order they happened, newest line first; ?index=N for one sticker |
| POST | `/api/generations/{id}/join` | Add to group: this batch and its whole family go under the family of {to: G### or number}; the target's root is the parent and names the family (flow/groups.py). -> {id, root, members}. 404 for a batch not on disk, 409 when it is already in that family |
| POST | `/api/generations/{id}/judge` | The vision model pre-reviews the stickers (history lines only). Needs allow_vlm: true in the body (409 with consent_required otherwise): AI vision is the person's yes, asked once |
| POST | `/api/generations/{id}/keep_size` | Motion's Keep approved size switch: on (the default) keeps every animation at its approved still's size and place, cutting off what moves past the frame (anim_metrics.cut_off_frames); off shrinks a clip until its whole motion fits; same: true (Same scale) wins over both, every sticker cut from its cell at one scale, uniform sizes as on the sheet (false clears it, absent leaves it). reslice: true cuts the animations again from the stored video (free) |
| POST | `/api/generations/{id}/leave` | Take a batch out of its family (it becomes its own root; its own edits follow it). 409 for the family's root |
| GET | `/api/generations/{id}/map` | The project map of a batch (flow/projects.py): {project {id, title, request}, current, batches [{root, title, picked, preset, sheets [{id, number, n, label, relation, prompt, prompt_changed, status making/failed/not_cut/ready, stage, thumb, counts {stickers, ready, blocked, animated}, videos [{id, status, used, slots, thumb, animated, blocked}], jobs [{id, kind, status}], created}]}]}. A member sees only their own batches; another person's batch is a 404 |
| POST | `/api/generations/{id}/more` | The next prepared variation of the same subject |
| POST | `/api/generations/{id}/pack` | Earlier batches' drag and drop: this batch (its family) becomes another batch of the PACK of `to` ({to: G###}); {to: null} takes it out (a pack of its own). Stored on the family root (pack, pack_history); never a regeneration. {id, root, pack} |
| POST | `/api/generations/{id}/pack_add` | Add chosen stickers to a pack |
| GET | `/api/generations/{id}/particles` | READY batch cells: {generation, cells:[{index,link,sticker,offer_approve,sets,affirmed}]} |
| POST | `/api/generations/{id}/pick` | Pick this generation for its batch (one per batch, stored on the root); answers the family |
| POST | `/api/generations/{id}/pick_video` | Make an earlier animation {sheet} of this generation current again (re-cut from its stored video, free); 409 while a video is being cut |
| POST | `/api/generations/{id}/position` | One sticker's Position: offset [dx, dy] as a share of the canvas (each -0.5 to 0.5, + = right / down; null = as cut) and optionally its Size (scale 0.5 to 1.5), applied by moving the cut's window. reslice: true cuts that sticker again from the stored video (free) |
| POST | `/api/generations/{id}/quick_sheet` | Approve the kept stills, build and approve the video sheet (one click) |
| POST | `/api/generations/{id}/recheck` | Run the border check on animations made before it existed |
| POST | `/api/generations/{id}/recut` | "Cut it anyway": cut a batch whose sheet was stopped, from the stored sheet, free (409 once a sticker was decided) |
| POST | `/api/generations/{id}/recut_particles` | A sheet of particles that was cut as stickers (a layout read from gutters, sticker rules on every cell, 512 px canvases) is cut again AS PARTICLES from the stored sheet, free: exact equal cells, no sticker rule blocks a cell. 409 when it already is a particle batch or a sticker was decided |
| POST | `/api/generations/{id}/regen` | Regenerate one sticker as a 1x1 child batch |
| POST | `/api/generations/{id}/remove` | Move a batch to out/trash/batches (nothing is deleted); 409 in words while a job for it is in flight. Returns {id, number, removed, by} |
| POST | `/api/generations/{id}/remove_video` | Take animation {sheet} out of the row (REJECTED, files kept); 409 for the one in use |
| POST | `/api/generations/{id}/replace` | Replace a still with a file edited outside the app (same S#, re-checked, back to approval, animation kept but marked from-the-previous-picture, old file kept for one undo) {index, png: base64} or {index, undo: true}; pack copies follow |
| POST | `/api/generations/{id}/resize` | Motion's Resize box (2026-10-15, replaces Keep approved size / Fit motion / Same scale on the screen): on sizes and places each animation by its hot spot (the pixels opaque in at least half the frames, anim_metrics.core_bbox) matched to its approved still; off cuts every sticker from its cell at one scale. reslice: true cuts again from the stored video (free) |
| POST | `/api/generations/{id}/reslice` | Re-cut the animations from the stored video with the current edge |
| POST | `/api/generations/{id}/restore` | Put a removed batch back under the same name; 409 if a batch with that number exists now. Returns {id, number, restored} |
| POST | `/api/generations/{id}/reveal` | Open the batch's folder in the file manager |
| POST | `/api/generations/{id}/review` | A human decision at a gate (Python's blocks are final) |
| GET | `/api/generations/{id}/sheet_preview` | The video sheet at a given fill (image/png) |
| POST | `/api/generations/{id}/studio_edit` | Layered edit of a sticker and its animation (open / commit) |
| POST | `/api/generations/{id}/video_sheet` | Build the video sheet from the approved stills |
| POST | `/api/generations/{id}/video_sheet/{aid}/video` | Attach a returned video (raw body) to a video sheet and slice it |
| POST | `/api/generations/{id}/video_size` | Video size after the video is received, 0.5 to 1.5 (1 = as it is): {scale} for every animation of the batch, {scale, index} for one sticker (its own value wins over the batch's); scale null resets. reslice: true cuts again from the stored video, free (only that sticker when index is given) |
| GET | `/api/health` | Every dependency reports itself; nothing raises |
| GET | `/api/health/models` | Language, vision and tracing status |
| GET | `/api/health/storage` | The out/ folder: free space, generations, who holds the writer lock |
| GET | `/api/higgsfield` | Is the CLI there, the balance, today's spend (never a credential) |
| GET | `/api/higgsfield/history` | Owner only. Recent image/video jobs, newest first, with known duplicate locations; size 1-100, default 40. Free, read-only |
| POST | `/api/higgsfield/import` | Owner only. Download an existing completed provider job and import its result with the same deduplication and G3 checks; local_job completes that failed local job instead of starting an unrelated batch; bounded HTTPS download, no paid generation |
| GET | `/api/history` | Every PACK (the batches of one request by one person, made within 6 h of each other, or dropped onto it: pipeline._packs), each holding its batch FAMILIES as batches[]; a family is (a batch, its edits and redos, and the batches added to it: flow/groups.py), the most recently edited first, a page at a time by pack (?offset, ?limit); an item is the first batch's family root with variants[] (each batch of the family in the same item shape, root first) and relation (joined / redo / edit / null): {items: [{id, generation_id, prompt, created, edited, stage, error, ready, animated, grid: [rows, cols], cells: [{index, row, col, png, status, animated}], outline_px}], more, total}. The grid is the sheet's own (2x2 or 3x3, read from result.json) so a card can draw it as it was cut |
| POST | `/api/import` | Owner only. Raw PNG/JPEG/WebP or MP4/MOV/WebM bytes, query name, prompt?, generation?, sheet?, retry?, again? (deliberate duplicate), grid_ok? (use the destination grid), job? (a failed local job this file completes), as_new? (import as an unrelated batch even when a failed job matches), batch? (any G### of a batch: a picture becomes its next generation, a video its main generation's next animation, the sheet it came from read from its first frame; the answer says which, match + message). A file naming a provider ticket with exactly one matching failed job completes it (no second charge); several matches are a 409 with candidates. Videos require an approved G3 sheet. 202 starts processing; 200 duplicate returns its existing job/task/generation/effect/import, status and recoverable. Retry reuses a failed import's batch. Free |
| GET | `/api/imports/candidates` | Owner only. The person's own FAILED/TIMEOUT jobs holding ?ticket=, newest first: prompt, time and thumbnail (?ticket=a provider job id). Free |
| GET | `/api/inputs` | The prepared sheets found in the watch folders |
| GET | `/api/jobs` | Jobs for the operator, newest first |
| POST | `/api/jobs` | Create a job file |
| GET | `/api/jobs/{id}` | One job (the page polls it while waiting) |
| POST | `/api/jobs/{id}/check` | One free read-only provider get; completed results are downloaded and reconciled by the same ticket. Human history records DIVERGENCE when local FAILED/TIMEOUT disagrees |
| POST | `/api/jobs/{id}/claim` | Operator (owner only): store the provider ticket BEFORE waiting {ticket} |
| POST | `/api/jobs/{id}/continue` | Continue a stalled job on its stored ticket via jobs.resume; never creates a paid request. 409 without a ticket or while already waiting |
| POST | `/api/jobs/{id}/dismiss` | Take a finished or failed job off the queue for good, for every browser (dismissed: {at, by}); the job itself stays. 409 while it is still running |
| POST | `/api/jobs/{id}/done` | Operator (owner only): attach the finished file {file, model, cost?} |
| POST | `/api/jobs/{id}/fail` | Operator (owner only): mark the job failed {reason} |
| POST | `/api/jobs/{id}/requeue` | Operator (owner only): a TIMEOUT / FAILED job asks again; a job that holds a provider ticket waits for the same provider job (no second charge) |
| POST | `/api/jobs/{id}/retry` | Explicit PAID re-request of a FAILED/TIMEOUT job; requires a current quoted estimate and go: true. Repeated requests return the same replacement job |
| POST | `/api/jobs/{id}/retry_estimate` | Quote a new paid Retry; {credits, message}. Creates no provider request |
| GET | `/api/library` | Only the caller's packs, recent stickers and totals; legacy packs belong to local. Every pack has owner. Another person's pack or /lib file returns 404 |
| POST | `/api/live/cost` | Price one call of a model (a quote, free) |
| POST | `/api/live/ref` | Store a reference image (raw body, ?name=) |
| POST | `/api/live/sheet` | Reserve a task (the G1 approval) and start the sheet job; spends credits. Idempotency-Key supported |
| POST | `/api/live/video` | Start the video job (Kling, Grok…) for a built video sheet; spends credits. Idempotency-Key supported. Optional video_prompt (max 6000) is sent verbatim; redo: true regenerates the video in the same batch (the sliced sheet is retired as SUPERSEDED, its clips kept as anim_versions, the next sheet is built from the same kept stickers); from_generation + parent + regen_of: a new generation of that batch (its family), from the batch's own cells and tags |
| GET | `/api/llm/models` | Local model fields {models, current, preference, chosen, configured, ok, why}, plus grouped local/fallback/openai catalogs {id, model, label, provider, vision, free}, cloud default and selected model; local readiness is a real cached probe |
| GET | `/api/metrics` | Quality and timing numbers over every batch (owner only): time to the first sticker, approval rates at the two gates, the regeneration rate, per-batch lines |
| GET | `/api/models` | Curated models, every other Higgsfield model, and the style presets |
| GET | `/api/openapi.json` | This document |
| POST | `/api/packs` | Create a pack |
| POST | `/api/packs/{id}` | Rename, reorder or set the cover of a pack. {lead: true} makes it its group's parent; {parent: <pack id>} nests it under that pack, {parent: ""} takes it out |
| POST | `/api/packs/{id}/delete` | Delete a pack: SOFT, it goes to the trash with its stickers and files untouched (GET /api/trash lists it). Returns {ok, id, trashed, name, stickers} |
| GET | `/api/packs/{id}/export.zip` | Download the pack: every sticker file (.webm animated, .png / .webp static, renamed to the export contract {emoji}-{slug}-{tag}-{sNN}-{G###}-{date}) and a manifest.json v1. Animated stickers ship compressed small by default (every frame kept, the best quality down to crf 46 that fits ~64 KB; ?light=0 keeps the full-size files; each row records crf/kb/frames/fps/fits); ?retime=1 plays an animation shorter than 3 s over 3 s (retimed_from on its row) (application/zip) |
| POST | `/api/packs/{id}/merge` | Fold this pack into another {into}: an animated sticker upgrades its still twin (the still keeps its id, name, emoji and particle links), the rest moves; the emptied pack goes to the trash |
| GET | `/api/packs/{id}/particles` | The pack's particle studio in one read: {pack_id, sets[] (the sets assigned to it, cards as in GET /api/particles), bursts[] (every burst rendered for it, with its warnings and whether it is in the pack), counts: {sticker id: {created, saved}}} (docs/architecture.md ('Reactions')); a member gets {} |
| GET | `/api/packs/{id}/reactions` | This pack's reaction variations: one row per emoji in the pack, motion inherited locked, art + Size/Energy vary. {pack_id, reactions: [{token, emoji, stickers[], preset, params, label, motion, locked, sets[]}]}. |
| POST | `/api/packs/{id}/render` | Save the editor's 512x512 canvas as a sticker (raw PNG body) |
| POST | `/api/packs/{id}/restore` | Put a deleted pack back from the trash under the same id, stickers and cover untouched (404 when it is not in the trash). Returns {ok, id, restored, name, stickers} |
| GET | `/api/packs/{id}/short` | The pack's animations shorter than 3 s, which an export asks about (Retime to 3 s or keep their length): {seconds: 3, short: [{id, name, emoji, seconds}]} |
| POST | `/api/packs/{id}/stickers` | Add a sticker to a pack |
| POST | `/api/packs/{id}/stickers/{sid}` | Update a sticker {name?, emoji?, action?}; action is a canonical emoji-bank token, empty clears it; invalid tokens return 400 |
| POST | `/api/packs/{id}/stickers/{sid}/animate` | Animate a library sticker |
| POST | `/api/packs/{id}/stickers/{sid}/delete` | Remove a sticker |
| POST | `/api/packs/{id}/stickers/{sid}/move` | Move one sticker into another pack: {to} |
| GET | `/api/packs/{id}/stickers/{sid}/particle-preview` | Free Echo reaction for this sticker: newest own linked set, persisted motion and sizing. Returns {set, motion, url, params, preset} or {set:null,url:null}; no unrelated-set fallback. Members receive the empty answer |
| GET | `/api/packs/{id}/stickers/{sid}/particles` | One sticker's particle versions: rows[] (saved, oldest first: {version, id, label, kind, sprites[] inside the row, n_sprites, motion, preview, renders, addable: {render, pack_id} / null, in_pack[], credits, job, shared_with, saved_at}) and drafts[] (started, not saved). Also sets[] and grouped legacy runs:[{effect,mode,cells,saved,imported_as:[Pids]}]. Each run groups all sprite slices; imported_as identifies owned sets that already contain it. Compatibility created[],saved[],effects[],can_make remain unchanged; a member gets an empty gallery |
| POST | `/api/packs/{id}/stickers/{sid}/replace` | Replace with a file edited outside the app (same id, name, emoji, particle links; type follows the file: PNG/JPG/WebP static 512 px 512 KB, WebM animated 512 px 256 KB). The batch and this pack take it; copies in other packs are offered ({others: [ids] / true}); the old file is kept for one undo ({undo: true}) |
| GET | `/api/packs/{id}/telegram` | Dry run: what would be created and every problem |
| POST | `/api/packs/{id}/telegram` | Create the pack on Telegram, or add what is new: queued, one send at a time. Body {name?, mode?, retime?} (retime: videos shorter than 3 s go up stretched to 3 s): mode once (default: the same content is never sent twice, the earlier export is returned with already=true and Telegram is not called), replace (send on purpose) or new_set (a second numbered set); 202 {id, state: queued, position}: GET /api/telegram/sends/{tid} answers its outcome; every send is recorded |
| GET | `/api/packs/{id}/telegram.zip` | No-credentials fallback: the files for @stickers (application/zip) |
| GET | `/api/particles` | Every particle set that is not in the trash, newest first: {sets: [{id, name, created, user, kind, elements[], packs[], used_in[{id, name}], cells[], picked[], n_cells, n_picked, renders, credits, drawing, sheets[]}]}. Sticker-owned sets include owner[], source.kind/job, credits, affirmed_in[], detached; packs[] is derived read-only (docs/architecture.md ('Reactions')) |
| POST | `/api/particles` | Recovery {from_stickers:[library sticker ids],parent_pack_id,owners?,name?,target?} keeps originals and links tight sprites to the parent pack stickers. Owners {owners:[{pack_id,sticker_id}]} replace deprecated packs input. Imports accept target:P### to append; video imports are idempotent by effect/result id. ALSO {from_video:E###,picked?:[source cell indices]} keeps animated clips/frame timelines and keyed posters, {from_slices:[{generation,index}],target?:P###} copies selected slices (target appends). ALSO {from_generation: G###, name?, packs?, picked?} -> 201: the cells of a batch that was CUT AS PARTICLES become a set, as TIGHT sprites (cropped out of the keyed sheet, never 512 px stickers); a batch cut as stickers is a 409 with the way out (POST /api/generations/{id}/recut_particles), a missing one a 404. Save what a run drew as a DURABLE set: {from_effect: E###, name?, packs?: [ids], picked?: [cells]} (the effect's own pack by default) -> 201 the set. Without `from_effect` it makes an empty stand-alone set: {name?, elements?, packs?, kind?: drawn/stickers/video}. Free |
| GET | `/api/particles/bank` | The emoji bank with per-token build state for Library > Reactions > Create: {bank: [{token, emoji, preset, params, label, motion, state: finished/unclaimed, building, set_id, n_picked}]}. Read-only, free. |
| GET | `/api/particles/deleted` | The trash, newest deleted first: {sets: [{id, name, created, kind, elements[], packs[], used_in[{id, name, missing?}], cells[] (urls into out/trash/particles/), picked[], n_cells, n_picked, credits, deleted: true, trashed: true, deleted_at}]}. Restore any of them with POST /api/particles/{id}/restore, long after the delete. Owner only |
| GET | `/api/particles/emoji-batches` | Reaction effects: the newest background runs (a reloaded screen finds one still going): {batches: [{id, state, mode, total, done, errors}]}. |
| POST | `/api/particles/emoji-batches` | Reaction effects, batch create in the background: {tokens? (none = every bank emoji), mode: start / preset / random, preset_id?, only_missing?} -> 202 {id, state: running, total, done}. random = a random preset of each emoji's own motion (none of it: the start look). 409 while another run is going or when nothing is left to make. |
| GET | `/api/particles/emoji-batches/{id}` | One background run: {id, state: running / done / cancelled / stopped, total, done, made: [{token, id, preset_id}], errors}. |
| POST | `/api/particles/emoji-batches/{id}/cancel` | Stop a background run between two emojis (what was made is kept). |
| GET | `/api/particles/emoji-lottie` | Reaction effects: every created reaction, newest first (one per Lottie export): {reactions: [{id, token, emoji, preset, params, url, preview, bytes, created}]}; preview is that export's own looping WebP. Read-only, free. |
| POST | `/api/particles/emoji-lottie` | Reaction effects (Create > New): the same burst as a playable Lottie JSON with the glyph embedded as an image asset (web only; Telegram stays WebM). {token, preset?, size_in_dur? (0.4), size_out_dur? (0.4), hue_var? (25), seed? (1), size? 128-1024} -> {id, token, emoji, preset, params, file, url, preview, layers, assets, bytes}; stores out/particles/emoji/<token>/renders/L###.lottie.json + L###.png + L###.webp, history LOTTIE. Same emoji + options + seed -> the same JSON. 409 for an emoji outside the bank. Free. |
| GET | `/api/particles/emoji-lottie.zip` | Export All / Selected (?tokens=a,b): the newest Lottie of each emoji in one zip, <token>.lottie.json + manifest.json {reactions, missing}. 409 when none has one. |
| POST | `/api/particles/emoji-lottie/batch` | Reaction effects, many at once: {tokens: [name or glyph, at most 24], preset_id?} -> {reactions: [as POST emoji-lottie], errors: [{token, error}], preset}; no preset_id = each emoji's own motion with the screen's defaults. One failing emoji does not stop the others. Free. |
| GET | `/api/particles/emoji-motions` | The generic 1800 wall (read-only emoji:animation map): {motions: [{token, emoji, preset, params, label, motion}]}. Every predefined emoji owns one fixed animation; packs inherit it 1:1. |
| GET | `/api/particles/emoji-presets` | Reaction effects: the saved presets, oldest first: {presets: [{id, name, settings, created}]}. Free. |
| POST | `/api/particles/emoji-presets` | Reaction effects, Save preset: {settings: {preset?, particle_size?, size_var?, size_in_dur?, size_out_dur?, hue_var?, magnitude?, gravity?, vortex?, spin?, count?, spread?}, name?} -> the next preset {id: preset01..., name: Preset 01..., settings, created}. 400 for an unknown key or bad value. Free. |
| POST | `/api/particles/emoji-presets/{id}` | Reaction effects, Presets > Update: {settings?, name?, token?} -> the preset (its motion follows its settings). 404 for an unknown id. |
| POST | `/api/particles/emoji-presets/{id}/delete` | Reaction effects: remove a preset (reactions made with it keep their params): {deleted, presets}. 404 for an unknown id. Free. |
| POST | `/api/particles/emoji-preview` | Reaction effects (Create > New): one bank emoji's fixed motion flying as its own glyph, as a small looping WebP. {token (name or glyph), preset?, size_in_dur?, size_out_dur?, hue_var?, particle_size? (0.25-3), size_var? (0-1), magnitude?, gravity?, vortex?, spin?, count?, spread?, fade_start? (s after a particle's birth its opacity starts dropping), fade_dur? (s from 100% to 0%, linear; 0 = off), fade_var? (+- s on both, per particle) (a reaction is always 2 s) (Simulation; left out = the motion's own), seed?, size? 64-512} -> {token, emoji, preset, params, url}. 409 for an emoji outside the bank, 503 with no colour emoji font. Free. |
| GET | `/api/particles/reactions` | Alias of /api/particles/emoji-motions. |
| GET | `/api/particles/{id}` | One set with its cells as /out/ urls, `used_in` the pack names, `sheets[]` (the sheets drawn for it by Generate more: {n, job, generation, grid, elements, status: REQUESTED/DRAWN/DONE/FAILED/NO_CELLS, estimate, cost, appended[], skipped[], error}), `drawing` (a sheet is on its way), `trashed: false`. A read also appends the cells of a sheet that was cut since the last one |
| POST | `/api/particles/{id}` | Rename, re-pick cells, persist the default motion including sprite_px/scale or link owners through deprecated packs: {name?, elements?, packs?, picked?: [cells], motion?: {preset, params}, save?: true}. `save: true` is the person's Save: the set becomes (or stays) a saved row under its stickers (`saved_at`); a new set starts as a draft (`saved_at: null`). Unpicking never deletes a file (generate more appends). Empty `picked` is a 400 |
| POST | `/api/particles/{id}/add` | Put rendered bursts into a pack as animated stickers tagged with the pack's emoji (commonest first, at most 20): {renders?: [R###] (default: every READY burst rendered for that pack that is not in a pack yet), pack_id?, sticker_id? (the sticker whose row this is; default the set's first owner)}. -> {added: [{sticker, name, render}], pack_id}. The new pack sticker carries `source.particle_set`, `source.render` and `source.parent_sticker`, and the row shows it as `in_pack`. This click is the person's approval (history APPROVE). A FAILED render (a Telegram limit) is a 409; a warning never stops it; the same burst in the same pack twice is a 409 |
| POST | `/api/particles/{id}/assign` | Deprecated one-release alias: link the stickers of packs {packs: [ids]}: a list edit, no file is copied; 404 for a pack that does not exist |
| POST | `/api/particles/{id}/default-sprite` | Free instant fallback sprite for bank reactions with no drawn cells yet (procedural sparkle, picked at once; replaced later via Generate more). Idempotent: {default: true} when added, {default: false} when the set already flies. |
| POST | `/api/particles/{id}/delete` | Move the set to out/trash/particles/ (nothing is destroyed). A set assigned to packs is REFUSED with 409 and the pack names unless {confirm: true} |
| POST | `/api/particles/{id}/duplicate` | An independent branch under a new id with the same sticker owners, saved motion, images and clip timelines; copied render affirmations are cleared ({name?}) -> 201 |
| POST | `/api/particles/{id}/export-lottie` | Optional third output (web only): the set's motion as a real Lottie JSON file with per-particle keyframes (Telegram stays WebM). {preset?, params?} -> {id, set, preset, params, file, url, layers}. The client downloads url (the stored renders/L###.lottie.json). Free. |
| POST | `/api/particles/{id}/link` | Link owner stickers {sticker_ids:[ids or owner records]}; free, no copies |
| POST | `/api/particles/{id}/more` | Add more with explicit mode:drawn/image/video/stickers (defaults to original source), prompt and grid. Video uses Kling quote/go and appends animated clips; quote returns effect context to resend as effect on go. Other modes draw a sheet for the set and APPEND its cut cells (numbered after the last cell, picked; nothing existing is deleted, overwritten or re-picked). {grid?: 2x2 / 3x3, elements?: [the particles to draw; default the set's own, at most the sheet's cells], estimate?: true -> 200 the free quote (prompt, picks, credits, model), go?: true}; 409 with the estimate unless `go: true` (the click is the go-ahead); 202 {job, task, estimate, id, grid}. A set made by hand (no run behind it) can gain cells this way. 400 with the reason when there is nothing to draw |
| POST | `/api/particles/{id}/preview` | The burst of the set's PICKED cells as a small looping WebP, rendered by the same engine as the final file (free; cached by what it was made from): {pack_id?, preset?: burst / fountain / vortex / rain / confetti (default: the set's own motion, else burst), params?: {magnitude, gravity, vortex, particle_size?: 0.25-3 (how big the particles look), count, spin, seed, ..., sprite_px?: 32-512 (sprite sharpness), scale?: 1-4} (strict: 400 for an unknown key), size?: 64-512}. -> {url, file, params, preset, sprites, source: cells / stickers, pack_id}. A set of kind `stickers` bursts the pack's own stickers; a drawn set with no cell yet is a 409 |
| POST | `/api/particles/{id}/render` | The final 512 px WebM of the burst FOR A PACK, judged and stored under out/particles/P###/renders/R###.webm with its checks: {pack_id (default: the set's only pack; 400 when it is in several), preset?, params?}. -> {id, set, pack_id, preset, params, status, bytes, url, checks[], warnings[], blocks[], metrics, added_to}. Stored whatever the checks say: ONLY Telegram's own limits make it FAILED (and a failed one is kept); every other check is a warning the person decides on |
| POST | `/api/particles/{id}/restore` | Put a deleted set back, same id and packs; 409 when that id is taken again |
| POST | `/api/particles/{id}/save-as-new` | Save as new: the same sprites with the edited motion become a NEW saved row under the same stickers ({motion?: {preset, params}, name?}) -> 201 the new set. The row it came from is unchanged; its bursts stay with it (the new row has none) |
| POST | `/api/particles/{id}/unassign` | Deprecated one-release alias: unlink the stickers of packs {packs: [ids]}; the set and its cells STAY |
| POST | `/api/particles/{id}/unlink` | Unlink owner stickers {sticker_ids:[ids]}; set and cells stay |
| POST | `/api/plan` | Preview a plan; nothing is reserved. Body {prompt, grid?, style_id?, ai?, loop?, actions?: creative / predefined (nine from the full emoji bank)}. Manual batches: {emojis: nine glyphs as a string or list, pack_id?, prompt?, decisions?: cell-number -> overwrite/add/skip, replacements?: cell-number -> unused emoji, style_id?, loop?}; returns {entries, ready, waiting, suggestion, plan?}. Pack ownership and repeats are checked server-side; unresolved duplicates never return a runnable plan |
| POST | `/api/plan/more` | Several batches in one Generate: the plans of the n batches after a previewed one ({prompt, plan, n, style_id?, loop?, gens?}): an emoji pack's next preset grids, else the next unused bank actions; with gens (a Next-batch draft) the session's own grids and actions are skipped and it never goes past batch 4. {plans, existing}; nothing is reserved or spent |
| POST | `/api/plan/next` | Next batch: the next unclaimed batch of the batches on screen ({gens}) as a plan to read first, or {complete, message}; nothing is reserved or spent |
| GET | `/api/prefs` | The person's own settings every screen shares: {stage} (auto / prompt / emojis / animation / export / api; default auto) |
| POST | `/api/prefs` | Set the person's slider {stage}: the chat and the Studio follow it; an unknown stage is a 400 |
| GET | `/api/prepared/match` | Which prepared subject a request names (?prompt=): {subject, variants, prefer}. Whole signature words only; free |
| GET | `/api/prepared/setting` | Whether matching requests are served from the watch folder instead of a paid call {prefer} |
| POST | `/api/prepared/setting` | Owner only: serve matching requests from the watch folder {prefer: bool} (STICKERLOOM_PREFER_PREPARED wins when set) |
| GET | `/api/projects` | Video / GIF projects |
| POST | `/api/projects` | Import a video or GIF (raw body, ?name=) |
| POST | `/api/projects/from_sticker` | Open an animated sticker as a project |
| GET | `/api/projects/{id}` | One project |
| POST | `/api/projects/{id}` | Update a project (autosave) |
| POST | `/api/projects/{id}/delete` | Delete a project |
| POST | `/api/projects/{id}/render` | Render a project to WebM / WebP / GIF |
| GET | `/api/search` | Search stickers (Postgres when up, else files); ?q= |
| POST | `/api/stickers/delete` | Bulk delete: [{pack_id, id}] |
| POST | `/api/stickers/move` | Bulk move into one pack, all or nothing: {to, items: [{pack_id, id}]} -> {moved, skipped, to} |
| GET | `/api/tasks` | Reserved tasks |
| POST | `/api/tasks` | Reserve a task (the G1 approval) |
| GET | `/api/tasks/{id}` | One task |
| GET | `/api/telegram` | Connected or not and which bot (never the token) |
| POST | `/api/telegram/config` | Save the bot token and user id |
| POST | `/api/telegram/disconnect` | Forget the token |
| GET | `/api/telegram/sends/{tid}` | One queued send's outcome, its sender only (404 for anyone else): queued / sending / done / failed with error |
| GET | `/api/trash` | Everything in the trash with exactly what a purge would remove (owner only): {batches: [{type: 'batch', id, number, subject, removed, by, stickers, content, files_total, bytes, files: [{path, bytes}] (first 40; files_truncated), db: {available, reason?, stickers, indexed, shared_in_pool, vectors, assets, events, video_sheets, reviews_kept, tasks_kept}, copies_in_packs: [{id, name, stickers, trashed}] (they stay), shared: {pool, packs}, in_flight[], needs_confirm, confirm_words, blocked}], packs: [{type: 'pack', id, name, deleted, by, stickers, files: [{sticker, name, file, bytes, missing, shared}], files_total, bytes, shared: [{sticker, name, file, also_in: [{id, name, trashed}]}], from_batches[], particle_sets[], needs_confirm, confirm_words, blocked}], totals, purge_all: {count, phrase, skipped[]}, database, purge (the last purge task or null), record (the last 20 ledger lines)} |
| POST | `/api/trash/purge` | Delete ONE trashed batch or pack for good: {type: batch/pack, id, confirm_shared?}. Refused 404 when it is not in the trash, 409 in words while a job is in flight or a purge is running, and 409 naming the packs (or the shared pool) until {confirm_shared: true} when it shares a sticker file with another pack or has stickers in the shared pool. Runs in its own thread: 200 + the finished task, or 202 + the running task to poll at GET /api/trash/purges/{id}. Idempotent: an interrupted purge is run again; an item purged before answers 200 with {already: true} |
| POST | `/api/trash/purge_all` | Delete everything in the trash that needs no confirmation of its own: {confirm: 'purge N', kind?: batch/pack} where N is the count GET /api/trash gives in purge_all (with kind: batch, only the removed batches: their count is purge_batches) (409 in words when the typed phrase or the count no longer matches the trash). Items that share stickers or have a job in flight are skipped and listed in `refused`, in words. Same 200 / 202 answer as purge; nothing to delete is a 200 with nothing_to_do |
| GET | `/api/trash/purges/{id}` | The progress of a purge: {id, status: running/done/failed/interrupted, total, done, items[], results[], refused[], error, by, started, finished} |
| GET | `/api/vision` | The vision judge: model and policy |
| GET | `/api/watch` | The watch folders side by side, plus the trash |
| POST | `/api/watch/purge` | Delete a trashed pair for good |
| POST | `/api/watch/remove` | Move a folder pair to the trash |
| POST | `/api/watch/restore` | Restore a folder pair under its own names |
| GET | `/api/watch/thumb/{name}` | A cached thumbnail of a sheet folder (image/jpeg) |
| GET | `/out/{path}` | A generated file (the path is resolved, then checked against the root) |

The full request/response shapes live in the fetched `sl_openapi.json`
(`components.schemas`), and as TypeScript types via:

```powershell
python -m stickerloom openapi --ts stickerloom-types.ts
```
