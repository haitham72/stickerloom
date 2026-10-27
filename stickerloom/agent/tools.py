"""What the agent can DO: a small interface over the Studio's own engine, so "the chat's backend is the Studio" is literally true.

`ConsoleTools(console)` calls the same functions the Studio's buttons call (`Console.live`, `pipeline`, `gates`, search). Nothing here
re-implements generation, gating or search. `FakeTools` (tests) implements the same methods without a provider. The agent never spends
unless the user said so: every method that costs credits is called only after a confirmation (or the user's "don't ask me" setting)."""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..flow import gates, pipeline as pl
from ..generation import tasks
from ..runtime.principal import principal


class ToolError(Exception):
    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = code


class ConsoleTools:
    def __init__(self, c, user: dict | None = None, models: dict | None = None):
        self.c = c
        self.out = Path(c.out)
        self.user = user or principal()
        self.models = models or {}          # the session's own picks {image, video, ai} (a Telegram chat: /model); none = the server's defaults

    @property
    def member(self) -> bool:
        return self.user.get("role") != "owner"

    def _see(self, gid: str) -> None:
        """A member reaches only the batches they own; anything else is 'not found' (a 404 never reveals that it exists)."""
        if not self.member:
            return
        try:
            res = pl.read_result(self.out, int(gid[1:]))
        except Exception:
            raise ToolError("No such batch", 404)
        if res.get("owner", "local") != self.user["id"]:
            raise ToolError("No such batch", 404)

    def _may_spend(self) -> None:
        """Spending is always allowed for the local principal; the price is shown and accepted before every paid call (cost() consent)."""

    # ---- reads -------------------------------------------------------------------------------------------------------------------
    def live(self) -> bool:
        from .. import providers
        return providers.current().available()

    def credits(self) -> float | None:
        if self.member:                       # the owner's balance is not a member's business
            return None
        try:
            a = self.c.hf_account()
            return a.get("credits") if a.get("available") else None
        except Exception:
            return None

    def plan(self, prompt: str, grid: str, style_id: str, ai: bool, actions_mode: str = "creative") -> dict:
        """The plan for a request, from the Redis planner cache when this exact request (normalised: case, spaces, end punctuation) was planned
        before with the same templates and model: 0 model calls. A plan the AI failed to expand (it fell back to the built-in sets) is never cached."""
        from .. import transformations
        from ..generation import prompter
        from ..runtime import cache as cachemod
        from ..services import llm
        c = cachemod.default()
        versions = sorted(f.stem for f in prompter.TEMPLATES.glob("*.txt")) + [transformations.signature()]
        key = c.key("plan", cachemod.digest(cachemod.normalize_request(prompt), grid, style_id, bool(ai), actions_mode, ",".join(versions),
                                              llm.model() if ai and llm.configured() else "-"))
        hit = c.get(key, "plan")
        if hit:
            return hit
        try:
            plan = tasks.preview(prompt, grid, style_id, ai, actions_mode=actions_mode)
        except pl.PipelineError as e:
            raise ToolError(str(e), e.code)
        if not plan.get("expand_error"):
            c.set(key, plan, 7 * 24 * 3600)
        return plan

    def batch_state(self, gens: list) -> int | None:
        """How many batches the pack these generations belong to already has (`tasks.session_state`: an emoji pack counts its grids, wherever they were made,
        as the Studio's picker does); None when it cannot be read (the chat then counts its own)."""
        try:
            return int(tasks.session_state(self.out, [g for g in gens if g])["existing"])
        except Exception:
            return None

    def more_batches(self, request: str, plan: dict, k: int, style_id: str, made_plans: list, gens: list | None = None) -> list:
        """The plans of the next `k` batches of a pack after `plan` (its batch 01), skipping every grid and bank action the chat's batches (and, through
        `gens`, the Studio's batches of the same pack) already used. Nothing is reserved or spent."""
        from ..generation import actions
        used_presets = {((p or {}).get("slots") or {}).get("preset") for p in made_plans} - {None}
        used_tokens = actions.tokens_from_stickers([s for p in made_plans for s in (p or {}).get("stickers") or []])
        if gens:
            try:
                ses = tasks.session_state(self.out, [g for g in gens if g])
                used_presets |= set(ses["used_presets"])
                used_tokens |= set(ses["used_tokens"])
            except Exception:
                pass
        try:
            return tasks.more_batches(request, plan, k, style_id, False, used_presets, used_tokens)
        except pl.PipelineError as e:
            raise ToolError(str(e), e.code)

    def engine_label(self, ai: bool = True) -> str | None:
        """Which model writes the sticker ideas right now ("<model> (local)", "<model> (cloud)"), None when the built-in sets are used. Shown as a step before the model is asked."""
        from ..services import llm
        if not ai or not llm.configured():
            return None
        return f"{llm.model().split('/')[-1].replace(':2', '')} ({'local' if llm.provider() == 'local' else 'cloud'})"

    def estimate(self, kind: str = "image") -> float | None:
        """What one call of the default model costs (a sheet; a video needs a built video sheet and is priced when it is sent)."""
        if not self.live():
            return None
        memo = getattr(self.c, "_estimates", None)
        if memo is None:
            memo = self.c._estimates = {}
        hit = memo.get(kind + str(self.models.get(kind) or ""))
        if hit and time.time() - hit[0] < 60:                    # the card polls every second; a price does not change that fast
            return hit[1]
        try:
            v = self.c.live("cost", {"kind": kind, **({"model": self.models[kind]} if self.models.get(kind) else {})}).get("credits")
        except Exception:
            return None
        memo[kind + str(self.models.get(kind) or "")] = (time.time(), v)
        return v

    def generation(self, gid: str) -> dict:
        """The sticker list of one batch for the chat (ids, keys, emoji, status, reviews, file urls).

        The batch carries what a person may DO with what Python blocked (`allow`): exactly `gates.allow_info`, the block the Studio's route sends too, per kind (`still`, `animation`):
        `can` (indexes that may be allowed now), `allowed` (carry a permission), `undo` (may be taken back now), `why` ({index: plain words}) and `final` ({index: why it cannot be
        allowed}). Without it the chat tile can show a blocked sticker but cannot offer the override that rule 10 requires on every surface. Computed, never stored; a sticker carries only
        `waived` (the checks it was allowed past), no second copy of the block."""
        self._see(gid)
        res = pl.read_result(self.out, int(gid[1:]))
        stickers = []
        for s in res["stickers"]:
            i = s["index"]
            stickers.append({"id": f"{gid}/S{i}", "index": i, "key": s["key"], "name": s.get("name"), "emoji": s.get("emoji"),
                             "tags": s.get("tags"), "title": s.get("title"), "proposed_title": (s.get("title_proposal") or {}).get("name"), "status": s["status"], "reason": s.get("reason"), "still": s["review"]["still"],
                             "anim": s["review"]["anim"], "anim_status": s.get("anim_status"), "anim_from_previous": bool(s.get("anim_from_previous")),
                             "waived": list(s.get("still_override") or []) + list(s.get("anim_override") or []),
                             "png": f"/out/{gid}/{s['png']}?e={s.get('rendered_at') or s.get('edited_at') or 0}" if s.get("png") else None,          # the edit time: an edited slice is a new url, never the cached picture
                             "webm": f"/out/{gid}/{s['webm']}" if s.get("webm") else None, "prompt": s.get("prompt")})
        return {"generation": gid, "stage": res.get("stage"), "error": res.get("error"), "prompt": res.get("prompt"), "problem": self._problem(res), "allow": gates.allow_info(res),
                "video_sheets": [{**v, "picture": f"/out/{gid}/{v['file']}"} for v in res["video_sheets"]],
                "parent": f"G{int(res['parent']):03d}" if res.get("parent") else None, "grid": res.get("grid"), "stickers": stickers}

    def _problem(self, res: dict) -> dict | None:
        p = pl.problem_of(self.out, res)
        if p:
            p["retry_estimate"] = self.estimate("image")
        return p

    def job(self, jid: str) -> dict:
        from ..generation import jobs
        try:
            return jobs.read(self.out, jid)
        except jobs.JobError as e:
            raise ToolError(str(e), e.code)

    def search(self, q: str) -> list:
        """Matching stickers as chat cards: id, key, emoji, and a file url the page can show. Postgres pool search when it is up, else files."""
        def url(gen, png):
            return f"/out/{gen}/{png}" if png else None

        try:
            from ..store import db, sync
            if sync.is_default_out(self.out) and db.available():
                from ..store import pool
                with db.connect() as conn:
                    r = pool.search(conn, q, count=12, per_gen=12, viewer=self.user["id"] if self.member else None)
                return [{"id": h["sticker_id"], "key": h["key"], "png": url(h["generation_id"], h.get("png")), "emoji": "".join(h.get("emoji") or [])}
                        for h in r["hits"]]
        except Exception:
            pass
        rows = gates.search(self.out, q, 24)
        if self.member:
            rows = [r for r in rows if self._owns(r["generation"])]
        return [{"id": f"{r['generation']}/S{r['index']}", "key": r["key"], "png": url(r["generation"], r.get("png")), "emoji": r.get("emoji")}
                for r in rows]

    def _owns(self, gid: str) -> bool:
        try:
            return pl.read_result(self.out, int(gid[1:])).get("owner", "local") == self.user["id"]
        except Exception:
            return False

    def reference_from_sticker(self, sid: str) -> str:
        """A finished sticker as a reference image for the next sheet ("make 5 like 2"): copied into out/refs/ as R###, the Studio's own reference store."""
        gid, idx = sid.split("/")
        res = pl.read_result(self.out, int(gid[1:]))
        st = next(x for x in res["stickers"] if x["index"] == int(idx[1:]))
        data = (pl.gen_dir(self.out, int(gid[1:])) / st["png"]).read_bytes()
        return self.c.save_ref(data, f"{sid.replace('/', '_')}.png")["id"]

    def prepared_match(self, prompt: str, grid=None) -> str | None:
        """The prepared subject this request names in this grid, when serving it from the watch folder is preferred (else None:
        the request goes live). Free: no provider call, no credits."""
        if not self.live():
            return None
        from ..flow import sources
        try:
            return sources.match_subject(self.c.inp, prompt, grid) if sources.prefer_prepared(self.out) else None
        except Exception:
            return None

    # ---- writes (these can spend) -----------------------------------------------------------------------------------------------------
    def create(self, prompt: str, grid: str = "2x2", style_id: str = "flat_vector", ai: bool = True, parent: str | None = None,
               regen_of: str | None = None, refs: list | None = None, base_plan: dict | None = None, ref_clause: str | None = None,
               outline: int | None = None, erode: int | None = None, force_live: bool = False) -> dict:
        """A new batch: the Studio's Generate. Live -> a sheet job (the active provider); a request that names a prepared
        subject -> that prepared set (0 credits, the same gates), unless `force_live` ("Make a new one");
        otherwise the prepared-sheet lookup, exactly as the page does. `base_plan` is the plan the person
        approved on the card: it is what is sent (the cells, tags and slots of the card), never planned a second time."""
        match = None if force_live else self.prepared_match(prompt, grid)
        manual = (base_plan or {}).get("slots", {}).get("emoji_batch")
        if manual:
            from ..generation.emoji_batches import validate_target
            try:
                validate_target(manual, self.c.lib, self.user["id"])
            except pl.PipelineError as e:
                raise ToolError(str(e), e.code)
            if not self.live():
                raise ToolError("Manual emoji batches need live generation. Enable the provider before creating this plan.", 409)
            match = None
        if not match:
            self._may_spend()
        if parent:
            self._see(parent)
        try:
            if self.live() and not match:
                body = {"prompt": prompt, "grid": grid, "style_id": style_id, "ai": ai, "refs": refs or [], **({"model": self.models["image"]} if self.models.get("image") else {})}
                if ref_clause and refs:
                    body["ref_clause"] = ref_clause                 # what the attached picture is for (editroute.REF_CLAUSES), instead of the default "change only the expression and the pose"
                if outline is not None:
                    body["outline"] = int(outline)                  # an edit keeps its parent's edge finish (white stroke / fringe trim), not the default
                if erode is not None:
                    body["erode"] = int(erode)
                if parent:
                    body["parent"] = parent
                if regen_of:
                    body["regen_of"] = regen_of
                r = self.c.live("sheet", body, base_plan=base_plan)
                return {"job": r["job"], "task": r["task"], "estimate": r.get("estimate"), "generation": None, "live": True}
            if self.c.lock.locked():
                raise pl.PipelineError("busy: a job is running, wait for it to finish", 409)
            gid = pl.start(prompt, self.out, self.c.inp)
            pl.approve_plan(self.out, gid, "approved from the chat")
            self.c.submit(lambda: pl.run_stills(self.out, gid, self.c.cfg, self.c.pace))
            return {"job": None, "task": None, "estimate": 0 if match else None, "generation": f"G{gid:03d}",
                    "live": False, **({"prepared": True} if match else {})}
        except pl.PipelineError as e:
            raise ToolError(str(e), e.code)

    def _png_ref(self, data: bytes, name: str) -> str:
        return self.c.save_ref(data, name)["id"]

    def sheet_reference(self, gid: str) -> dict:
        """The picture a tweak or a new action is made from (UI/UX spec P12): the batch's SHEET, the one the person picked in the chat, and after a slice was edited and saved (the editor, the Studio, the
        chat) the sheet rebuilt with those slices fixed (`source/sheet_fixed.png`). Copied into out/refs/ as R###, the Studio's own reference store. {ref, px (its short side), file}."""
        import io
        from PIL import Image
        self._see(gid)
        res = pl.read_result(self.out, int(gid[1:]))
        d = pl.gen_dir(self.out, int(gid[1:]))
        rel = next((r for r in (res["source"].get("sheet_fixed"), res["source"].get("sheet_copy")) if r and (d / r).is_file()), None)
        if not rel:
            raise ToolError("That batch has no sheet to send as a picture.", 409)
        data = (d / rel).read_bytes()
        im = Image.open(io.BytesIO(data))
        px, ext = min(im.size), "png"
        if len(data) > 14 * 1024 * 1024:                              # the reference store takes 15 MB: a big sheet goes as a high-quality JPEG
            buf = io.BytesIO()
            im.convert("RGB").save(buf, "JPEG", quality=92)
            data, ext = buf.getvalue(), "jpg"
        return {"ref": self._png_ref(data, f"{gid}_sheet.{ext}"), "px": px, "file": rel}

    def slice_reference(self, sid: str, min_px: int = 400) -> dict:
        """ONE slice as the picture (UI/UX spec P11b): its cell cut out of the batch's sheet (the fixed sheet when a slice was edited) at the sheet's own resolution, with the green screen the new picture
        should have. Scaled up only when its short side is under `min_px` (the provider's minimum); {ref, px (what was sent), from_px (what the cell is), scaled, min_px}."""
        import io
        import numpy as np
        from PIL import Image
        gid, idx = sid.split("/")
        self._see(gid)
        n, i = int(gid[1:]), int(idx[1:])
        res = pl.read_result(self.out, n)
        d = pl.gen_dir(self.out, n)
        st = next(x for x in res["stickers"] if x["index"] == i)
        rel = next((r for r in (res["source"].get("sheet_fixed"), res["source"].get("sheet_copy")) if r and (d / r).is_file()), None)
        cell = (st.get("metrics") or {}).get("cell")
        if rel and cell:
            x, y, w, h = (int(v) for v in cell)
            im = Image.open(d / rel).convert("RGB").crop((x, y, x + w, y + h))
        elif st.get("png") and (d / st["png"]).is_file():
            im = Image.open(d / st["png"]).convert("RGBA")
        else:
            raise ToolError("That slice has no picture to send.", 409)
        from_px = min(im.size)
        scaled = from_px < min_px
        if scaled:
            k = min_px / from_px
            im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return {"ref": self._png_ref(buf.getvalue(), f"{gid}_S{i}.png"), "px": min(im.size), "from_px": from_px, "scaled": scaled, "min_px": min_px}

    def slice_plan(self, sid: str) -> dict:
        """The plan of ONE sticker, made from its parent's own saved slots (same style, same cell label, same tags), as a 1x1: `gates.regen_plan`."""
        gid, idx = sid.split("/")
        self._see(gid)
        try:
            return gates.regen_plan(pl.read_result(self.out, int(gid[1:])), int(idx[1:]))
        except Exception as e:
            raise ToolError(f"I could not read the plan of {sid}: {e}", 409)

    def edge_of(self, gid: str) -> dict:
        """The edge finish a batch was made with, `{outline, erode}` in px (result.json `outline_px` / `erode_px`): an edit of it is made with the same finish. {} when it cannot be read."""
        self._see(gid)
        try:
            res = pl.read_result(self.out, int(gid[1:]))
        except Exception:
            return {}
        return {k: int(res[f"{k}_px"]) for k in ("outline", "erode") if res.get(f"{k}_px") is not None}

    def generation_plan(self, gid: str) -> dict:
        """The saved plan (prompts.json) of a batch: the cells, tags, slots and template it was made from, to start a changed copy of it (a refinement keeps everything but the change)."""
        self._see(gid)
        try:
            return json.loads((pl.gen_dir(self.out, int(gid[1:])) / "prompts.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ToolError("That batch has no saved plan to change", 404)

    def more(self, gid: str) -> dict:
        """The next prepared variation of the same subject (no provider): the Studio's 'Create more' on a prepared set."""
        self._see(gid)
        try:
            if self.c.lock.locked():
                raise pl.PipelineError("busy: a job is running, wait for it to finish", 409)
            new = pl.more(self.out, self.c.inp, int(gid[1:]))
            self.c.submit(lambda: pl.run_stills(self.out, new, self.c.cfg, self.c.pace))
            return {"generation": f"G{new:03d}"}
        except pl.PipelineError as e:
            raise ToolError(str(e), e.code)

    def creator_job(self, run_id: str) -> dict | None:
        from ..generation import jobs
        return next((j for j in jobs.list(self.out) if j["kind"] == "video" and
                     (j.get("request") or {}).get("creator_run") == run_id and
                     (j.get("request") or {}).get("user") == self.user["id"]), None)

    def animate(self, gid: str, loop: bool = False, *, creator_run=None, on_job=None) -> dict:
        """One click, as the Studio's animate button: approve the kept stills, build and approve the video sheet, start Kling."""
        self._see(gid)
        self._may_spend()
        try:
            r = self.c.live("video", {"generation": int(gid[1:]), "loop": loop, **({"model": self.models["video"]} if self.models.get("video") else {})},
                            creator_run=creator_run, on_job=on_job)
            return {"job": r["job"], "estimate": r.get("estimate")}
        except pl.PipelineError as e:
            raise ToolError(str(e), e.code)

    def review(self, gid: str, decision: str, indexes: list, note: str = "from the chat", gate: str = "still") -> dict:
        """Human decisions at the stills gate (or `gate="anim"`) for the given stickers. Python's blocks stay final: a refused sticker is reported, not forced."""
        self._see(gid)
        done, refused = [], []
        for i in indexes:
            try:
                gates.review(self.out, int(gid[1:]), gate, decision, int(i), note)
                done.append(int(i))
            except pl.PipelineError as e:
                refused.append({"index": int(i), "why": str(e)})
        return {"done": done, "refused": refused}

    # ---- "Use it anyway" (CLAUDE.md rule 10): a judgement-call block a person may allow, and take back ----
    def processing(self) -> bool:
        """Whether the shared local writer is still applying a free edit/override."""
        return self.c.lock.locked()

    def allowable(self, gid: str, kind: str = "still", allow: bool = True) -> list[int]:
        """The stickers whose block is a judgement call and may therefore be allowed right now (or whose permission may be taken back).
        A technical block (Telegram's own limits) and a cell with no picture are never in this list: those are final."""
        self._see(gid)
        try:
            return gates.allowable(pl.read_result(self.out, int(gid[1:])), allow, kind)
        except pl.PipelineError:
            return []

    def allow(self, gid: str, indexes: list, kind: str = "still", allow: bool = True) -> dict:
        """A person's recorded, reversible permission for what Python blocked as a judgement call: the sticker is cut again with that
        check kept as a warning ("allowed by you"), free and in the background. The same call the Studio's tile and sheet make."""
        self._see(gid)
        g = int(gid[1:])
        done, refused = [], []
        for i in indexes:
            try:
                if kind == "video_sheet":
                    res = pl.read_result(self.out, g)
                    why = gates.sheet_problem(res, gates.sheet_of(res, str(i)), allow)
                    if why:
                        raise pl.PipelineError(why, 409)
                    done.append(str(i))
                else:
                    gates.check_allow(self.out, g, int(i), allow, kind)
                    done.append(int(i))
            except pl.PipelineError as e:
                refused.append({"index": i, "why": str(e)})
        if not done:
            why = refused[0]["why"] if refused else "there is nothing to allow here"
            raise ToolError(why, 409)
        self.c.submit(lambda: gates.allow_cells(self.out, g, kind, done, allow, pl.cfg_for(pl.read_result(self.out, g), self.c.cfg), self.c.pace))
        return {"done": done, "refused": refused, "kind": kind, "allow": allow}

    # ---- the agentic creator's steps (agent/creator.py) ------------------------------------------------------------------------------------------
    def judge(self, gid: str) -> dict:
        """The vision judge's pre-review of the stills (it only advises). A judge that cannot run is a skipped check, never a failed run."""
        from ..vision import judge as vj
        self._see(gid)
        try:
            return vj.judge_generation(self.out, int(gid[1:]), "still", allowed=True)
        except Exception as e:
            return {"rejected": [], "approved": [], "unjudged": [], "skipped": str(e)[:200]}

    def pack_add(self, gid: str, name: str) -> dict:
        """Approve what was kept (G2 / G4), approve the final pack (G5) and add it to a new library pack: the Studio's "Add to a pack"."""
        self._see(gid)
        try:
            return gates.quick_add(self.out, int(gid[1:]), self.c.lib, None, name, owner=self.user["id"])
        except pl.PipelineError as e:
            raise ToolError(str(e), e.code)

    def packs(self) -> list:
        """The library's packs the person may use for effects (owner only: the effects routes are owner only)."""
        if self.member:
            return []
        return [{"id": p["id"], "name": p["name"], "count": len(p.get("stickers") or [])} for p in self.c.lib.snapshot(owner=self.user["id"])["packs"]]

    def emoji_packs(self) -> list:
        """Owned packs for manual emoji batches, including members' own packs."""
        return self.c.lib.snapshot(owner=self.user["id"])["packs"]

    # ---- particle sets (docs/architecture.md ("Reactions")): the chat's reads and free edits, and the one call that spends (a sheet) ----
    def _owner(self) -> None:
        if self.member:
            raise ToolError("Particle sets are for the owner's account for now.", 403)

    def particle_sets(self) -> list:
        """The particle sets that are not in the trash, compact: what the chat needs to name one and to say where it is used."""
        if self.member:
            return []
        from ..flow import particle_sets as ps
        return [{k: r.get(k) for k in ("id", "name", "packs", "used_in", "n_cells", "n_picked", "elements", "drawing", "owner", "source", "created", "credits")} for r in ps.list_sets(self.out, self.c.lib)]

    def particle_deleted(self) -> list:
        if self.member:
            return []
        from ..flow import particle_sets as ps
        return [{k: r.get(k) for k in ("id", "name", "packs", "used_in")} for r in ps.list_deleted(self.out, self.c.lib)]

    def particle_options(self, pack_id: str, n: int = 4) -> list:
        """Candidate particles for a pack, free: the built-in table reads the pack's stickers (no picture leaves the machine here)."""
        self._owner()
        from ..vision import effect_plan
        pk = next((p for p in self.c.lib.snapshot(owner=self.user["id"])["packs"] if p["id"] == pack_id), None)
        if not pk:
            raise ToolError("No such pack", 404)
        r = effect_plan.suggest_options(None, kind="contact", stickers=[{"name": s.get("name"), "emoji": s.get("emoji")} for s in pk.get("stickers") or []], pack_name=pk["name"],
                                        grid=(2, 2) if n <= 4 else (3, 3), allowed=False, out=self.out)
        return list(r["options"])[:n]

    def particle_owners(self, pack_id=None, generation=None, sticker_ids=None):
        """Resolve library stickers by explicit scope, retaining their ordered set links."""
        self._owner()
        rows = []
        for p in self.c.lib.snapshot(owner=self.user["id"])['packs']:
            for st in p.get('stickers', []):
                src = st.get('source') or {}
                if pack_id and p['id'] != pack_id:
                    continue
                if generation and src.get('generation') != generation:
                    continue
                if sticker_ids and st['id'] not in sticker_ids and f"{src.get('generation')}/S{src.get('index')}" not in sticker_ids:
                    continue
                rows.append({'sticker_id':st['id'], 'pack_id':p['id'], 'generation':src.get('generation'), 'index':src.get('index'), 'particles':st.get('particles', [])})
        return rows

    def particles_link(self, set_id, sticker_ids):
        self._owner()
        from ..flow import particle_sets as ps
        try:
            return ps.link(self.out, self.c.lib, set_id, sticker_ids, self.user['id'])
        except ps.SetError as e:
            raise ToolError(str(e), e.code)

    def particles_burst(self, set_id, pack_id):
        self._owner()
        from ..flow import particle_sets as ps
        try:
            return ps.render(self.out, self.c.lib, set_id, self.c.cfg, pack_id=pack_id, user=self.user['id'])
        except ps.SetError as e:
            raise ToolError(str(e), e.code)

    def particles_add(self, set_id, pack_id):
        self._owner()
        from ..flow import particle_sets as ps
        try:
            return ps.add(self.out, self.c.lib, set_id, pack_id=pack_id, user=self.user['id'])
        except ps.SetError as e:
            raise ToolError(str(e), e.code)

    def particles_start(self, set_id=None, pack_id=None, grid: str = "2x2", elements=None, name=None, owners=None, fresh=False) -> dict:
        """SPENDS after the caller's quoted confirmation. Reuse the newest common owner set unless fresh was requested."""
        self._owner()
        self._may_spend()
        from ..flow import particle_sets as ps
        if not self.live():
            raise ToolError("Drawing particles needs live generation (no provider is available).", 503)
        try:
            if set_id is None:
                rows = owners if owners is not None else self.particle_owners(pack_id=pack_id)
                resolved = ps.owners_for(self.c.lib, rows)
                if not resolved:
                    raise ToolError('Choose the stickers these particles are for', 400)
                candidates = ps.list_sets(self.out, self.c.lib)
                ids = {o['sticker_id'] for o in resolved}
                existing = next((s for s in candidates if ids <= {o['sticker_id'] for o in s['owner']}), None)
                if existing and not fresh:
                    set_id = existing['id']
                else:
                    set_id = ps.create(self.out, self.c.lib, name=name or 'Sticker particles', elements=elements, owners=resolved, user=self.user['id'])['id']
            plan = ps.more_plan(self.out, self.c.lib, set_id, grid, elements)
            base = ps.more_base_plan(self.out, self.c.lib, plan["id"], plan["grid"], plan["picks"])
            r = self.c.live("sheet", {"prompt": base["task"], "grid": f"{plan['grid'][0]}x{plan['grid'][1]}", "outline": 0}, base_plan=base, particles={"set": plan["id"], "elements": plan["picks"]})
        except (ps.SetError, pl.PipelineError) as e:
            raise ToolError(str(e), e.code)
        return {"set": plan["id"], "job": r["job"], "estimate": r.get("estimate"), "grid": plan["grid"]}

    def particles_alone(self, request: str, grid: str = "2x2") -> dict:
        """SPENDS after the caller's quoted confirmation. Particles on their own (no sticker owns them): the set the request names (`particle_sets.from_request`),
        then its sheet, exactly as the Studio's Particles choice."""
        self._owner()
        self._may_spend()
        from ..flow import particle_sets as ps
        if not self.live():
            raise ToolError("Drawing particles needs live generation (no provider is available).", 503)
        try:
            s = ps.from_request(self.out, self.c.lib, request, grid=grid, user=self.user['id'])
        except ps.SetError as e:
            raise ToolError(str(e), e.code)
        r = self.particles_start(set_id=s["id"], grid=grid, elements=s["plan"]["elements"])
        return {**r, "name": s["name"]}

    def particles_delete(self, set_id: str, confirm: bool = False) -> dict:
        self._owner()
        from ..flow import particle_sets as ps
        try:
            return ps.delete(self.out, self.c.lib, set_id, confirm_packs=confirm is True, user=self.user["id"])
        except ps.SetError as e:
            raise ToolError(str(e), e.code)

    def particles_restore(self, set_id: str) -> dict:
        self._owner()
        from ..flow import particle_sets as ps
        try:
            return ps.restore(self.out, self.c.lib, set_id, self.user["id"])
        except ps.SetError as e:
            raise ToolError(str(e), e.code)

    def particles_assign(self, set_id: str, packs: list) -> dict:
        self._owner()
        from ..flow import particle_sets as ps
        try:
            return ps.assign(self.out, self.c.lib, set_id, packs, self.user["id"])
        except ps.SetError as e:
            raise ToolError(str(e), e.code)

    def effects_start(self, pack_id: str, allowed: bool = False) -> dict:
        """The same call as POST /api/effects: a new E### for every sticker of the pack, analysed in the background (free; `allowed` is the person's yes to AI vision)."""
        import threading
        from ..flow import effects as fx
        if self.member:
            raise ToolError("Particle effects are for the owner's account for now.", 403)
        try:
            e = fx.create(self.out, self.c.lib, pack_id=pack_id, sticker_ids="all", mode="video", grid="2x2", user=self.user["id"])
        except fx.EffectError as ex:
            raise ToolError(str(ex), getattr(ex, "code", 400))
        eid, who = e["id"], self.user["id"]

        def run():
            tok = pl.OWNER.set(who)
            try:
                fx.analyse(self.out, eid, allowed=allowed is True)
            except Exception as ex:
                try:
                    rec = fx.read(self.out, eid)
                    rec.update(status="ERROR", error=str(ex)[:300])
                    fx._write(self.out, rec)
                except Exception:
                    pass
            finally:
                pl.OWNER.reset(tok)
        threading.Thread(target=run, daemon=True).start()
        return {"id": eid, "pack_name": e["pack_name"], "count": len(e["stickers"])}

    def telegram_ready(self) -> tuple:
        from ..services import telegram
        if telegram.status(self.out)["configured"]:
            return True, ""
        return False, "Telegram is not connected."

    def telegram_send(self, pid: str) -> dict:
        from ..services import telegram
        try:
            return telegram.send(self.out, self.c.lib, pid)
        except telegram.TelegramError as e:
            raise ToolError(str(e), getattr(e, "code", 409) if getattr(e, "code", 409) < 500 else 409)

    def ready_indexes(self, gid: str) -> list:
        self._see(gid)
        res = pl.read_result(self.out, int(gid[1:]))
        return [s["index"] for s in res["stickers"] if s["status"] == "READY"]

    def name_proposals(self, gid: str, ask, allowed=None) -> list:
        """Look at the pictures of a batch (the person's yes to AI vision, `allowed`) and propose a better name where one does not fit: [{index, current, caption, fits, name}]."""
        from ..vision import consent, naming
        self._see(gid)
        try:
            return naming.propose(self.out, gid, allowed=allowed, ask=ask)
        except consent.ConsentRequired as e:
            raise ToolError(str(e), 409)

    def apply_titles(self, gid: str, indexes: list | None = None) -> dict:
        """Apply the names that were proposed and are waiting for the person's yes: {index: title}."""
        from ..vision import naming
        self._see(gid)
        return naming.apply(self.out, gid, indexes)

    def pending_titles(self, gid: str) -> dict:
        from ..vision import naming
        self._see(gid)
        return naming.pending(self.out, gid)

    def describe(self, gid: str, index: int, question: str, allowed=None, on_delta=None) -> str:
        from ..vision import consent, describe
        from ..vision.judge import JudgeError
        self._see(gid)
        try:
            return describe.sticker(self.out, gid, index, question, allowed=allowed, on_delta=on_delta)
        except (consent.ConsentRequired, JudgeError) as e:
            raise ToolError(str(e), 409)

    def captions(self, gid: str, allowed=None) -> list:
        """The AI caption of every READY sticker of a batch (one vision-model call per cell that has none; a stored caption is read for free). `allowed` must be True: the
        person's yes to AI vision (vision/consent.py); the chat passes it from the session's `allow_vlm`."""
        from ..vision import consent, transcribe
        self._see(gid)
        try:
            return [c.to_dict() for c in transcribe.captions_for(self.out, gid, allowed=allowed)]
        except consent.ConsentRequired as e:
            raise ToolError(str(e), 409)


class FakeTools:
    """The same interface without a provider or files: what the agent tests drive. `calls` records every spend-capable call."""

    def __init__(self, generations: dict | None = None, live: bool = True, credits: float = 100.0):
        self.gens = generations or {}
        self._live, self._credits = live, credits
        self.models = {}                    # like ConsoleTools: the chat's own picks {image, video, ai}
        self.calls: list = []
        self.proposed: dict = {}
        self.sent_plans: list = []
        self.sent: list = []                                  # every create(): prompt, grid, parent, regen_of, refs, ref_clause, plan (what the provider would get)
        self.plans: dict = {}
        self.fail_next_create = False
        self.judge_rejects: list = []
        self.job_generations: dict = {}
        self.job_status: dict = {}
        self.job_errors: dict = {}
        self.video_estimate = 8.0
        self.telegram = True
        self.batches_max = 4                                  # what tasks.preview answers for a bank request; 1 for a transformation
        self.transform = False
        self.existing = None                                  # batch_state: None = the chat counts its own batches
        self.n_jobs = 0
        self.n_gens = max([int(g[1:]) for g in self.gens] or [0])

    def live(self):
        return self._live

    def credits(self):
        return self._credits

    def plan(self, prompt, grid, style_id, ai, actions_mode="creative"):
        self.calls.append(("plan", prompt))
        words = [w for w in prompt.lower().split() if w not in ("make", "me", "a", "an", "some", "stickers", "sticker", "of", "create")]
        subject = " ".join(words[:3]) or "sticker"
        n = 9 if grid == "3x3" else 4 if grid == "2x2" else 1
        preset = "core-v1" if "emoji" in prompt.lower() else None
        out = {"subject": subject, "task_slug": subject.replace(" ", "_"), "grid": grid, "expanded_by": "transformation" if self.transform else "fake", "template_id": "fake_t", "template_version": 1,
               "slots": {"subject_description": subject, "style_id": style_id, **({"preset": preset} if preset else {})}, "sheet_prompt": f"sheet of {subject}",
               "stickers": [{"index": i, "key": f"{subject.replace(' ', '_')}_{i}", "emoji": ["😀"], "prompt": f"{subject} {i}"} for i in range(1, n + 1)],
               "batches_max": 1 if self.transform or n == 1 else self.batches_max}
        if self.transform:
            out["transformation"] = {"id": "fake_tr", "subject": "dog", "target": "banana", "required": []}
        return out

    def batch_state(self, gens):
        return self.existing

    def more_batches(self, request, plan, k, style_id, made_plans, gens=None):
        self.calls.append(("more_batches", request, k))
        presets = ["core-v1", "social-v1", "reactions-v1", "daily-v1"]
        used = {((p or {}).get("slots") or {}).get("preset") for p in made_plans} | {(plan.get("slots") or {}).get("preset")}
        free = [p for p in presets if p not in used]
        out = []
        for i in range(k):
            pre = free[i] if (plan.get("slots") or {}).get("preset") and i < len(free) else None
            out.append({"prompt": request + (f" {pre}" if pre else ""), "subject": plan.get("subject"), "grid": plan.get("grid") or "3x3", "expanded_by": "deterministic",
                        "slots": {**(plan.get("slots") or {}), **({"preset": pre} if pre else {})},
                        "stickers": [{"index": j, "key": f"more{len(made_plans) + i + 2}_{j}", "emoji": ["😀"]} for j in range(1, 10)]})
        return out

    def engine_label(self, ai=True):
        return None

    def estimate(self, kind="image"):
        if not self._live:
            return None
        return self.video_estimate if kind == "video" else 2.0

    def generation(self, gid):
        card = self.generation_card(gid)
        out = {k: v for k, v in card.items() if k not in ("stickers", "allow")}      # a copy: the fixture is never mutated (a test asserts on its own stickers)
        out["stickers"] = []
        allow = {kind: {"can": [], "allowed": [], "undo": [], "why": {}, "final": {}} for kind in ("still", "animation")}
        for s in card["stickers"]:                                    # FakeTools: the same block the real tools take from gates.allow_info (index lists per kind)
            st = {k: v for k, v in s.items() if k not in ("allow", "waived")}
            for kind, status, why, over in (("still", "status", "reason", "still_override"), ("animation", "anim_status", "anim_reason", "anim_override")):
                if s.get(status) == "FAILED":
                    allow[kind]["can"].append(s["index"])
                    allow[kind]["why"][str(s["index"])] = s.get(why)
                if s.get(over):
                    allow[kind]["allowed"].append(s["index"])
            st["waived"] = list(s.get("still_override") or []) + list(s.get("anim_override") or [])
            out["stickers"].append(st)
        out["allow"] = card.get("allow") or allow
        return out

    def generation_card(self, gid):
        return self.gens[gid]

    def job(self, jid):
        return {"id": jid, "status": self.job_status.get(jid, "DONE"), "generation": self.job_generations.get(jid), "error": self.job_errors.get(jid)}

    def search(self, q):
        return [{"id": g["generation"] + "/S1", "key": g["stickers"][0]["key"], "png": None, "emoji": None} for g in self.gens.values()
                if q.lower().split()[0] in str(g).lower()]

    def sheet_reference(self, gid):
        self.calls.append(("sheet_reference", gid))
        self.n_refs = getattr(self, "n_refs", 0) + 1
        return {"ref": f"R{100 + self.n_refs}", "px": 2048, "file": "source/sheet.png"}

    def slice_reference(self, sid, min_px=400):
        self.calls.append(("slice_reference", sid, min_px))
        self.n_slice_refs = getattr(self, "n_slice_refs", 0) + 1
        return {"ref": f"R{200 + self.n_slice_refs}", "px": 682, "from_px": 682, "scaled": False, "min_px": min_px}

    def reference_from_sticker(self, sid):
        self.calls.append(("reference_from_sticker", sid))
        self.n_sticker_refs = getattr(self, "n_sticker_refs", 0) + 1
        return f"R{300 + self.n_sticker_refs}"

    def slice_plan(self, sid):
        gid, idx = sid.split("/")
        base = self.generation_plan(gid)
        cell = next(c for c in base["slots"]["cells"] if c["pos"] == int(idx[1:]))
        st = next(s for s in base["stickers"] if s["index"] == int(idx[1:]))
        return {"template_id": "single_1x1", "template_version": base.get("template_version", 3), "task": base.get("task"), "task_slug": base.get("task_slug"), "subject": base.get("subject"), "grid": [1, 1],
                "slots": {**{k: v for k, v in base["slots"].items() if k != "cells"}, "mode": "single_1x1", "cells": [{**cell, "pos": 1}]},
                "stickers": [{**st, "index": 1, "id": "prompt01"}]}

    def edge_of(self, gid):
        return dict(getattr(self, "edges", {}).get(gid) or {})

    def prepared_match(self, prompt, grid=None):
        return None

    def create(self, prompt, grid="2x2", style_id="flat_vector", ai=True, parent=None, regen_of=None, refs=None, base_plan=None, ref_clause=None, outline=None, erode=None, force_live=False):
        if getattr(self, "fail_next_create", False):
            self.fail_next_create = False
            raise ToolError("the provider refused it; try again in a minute", 503)
        self.sent.append({"prompt": prompt, "grid": grid, "style_id": style_id, "parent": parent, "regen_of": regen_of, "refs": list(refs or []), "ref_clause": ref_clause, "plan": base_plan, "outline": outline, "erode": erode})
        self.calls.append(("create", prompt, grid, parent, regen_of))
        if base_plan is not None:
            self.sent_plans.append(base_plan)
        if self._live:
            self.n_jobs += 1
            return {"job": f"J{self.n_jobs:03d}", "task": f"{self.n_jobs:03d}", "estimate": 2.0, "generation": None, "live": True}
        self.n_gens += 1
        gid = f"G{self.n_gens:03d}"
        self.gens[gid] = {"generation": gid, "stickers": [{"id": f"{gid}/S{i}", "index": i, "key": f"s{i}", "status": "READY", "still": "PENDING"}
                                                          for i in range(1, 10)]}
        if base_plan is not None:
            self.plans[gid] = json.loads(json.dumps(base_plan))       # the new batch's saved plan is the plan that was sent, like prompts.json
        return {"job": None, "task": None, "estimate": None, "generation": gid, "live": False}

    def generation_plan(self, gid):
        if gid in self.plans:
            return json.loads(json.dumps(self.plans[gid]))
        subj = (self.gens.get(gid) or {}).get("subject") or "subject"
        return {"template_id": "fake_t", "template_version": 1, "subject": subj, "task": subj, "task_slug": subj.replace(" ", "_"), "grid": [3, 3],
                "slots": {"subject_description": subj, "style_id": "realistic", "cells": [{"pos": i, "label": f"pose {i}", "tags": [f"{subj}_{i}"], "emoji": "😀"} for i in range(1, 10)]},
                "stickers": [{"index": i, "key": f"{subj}_{i}", "tags": [f"{subj}_{i}"], "emoji": ["😀"]} for i in range(1, 10)]}

    def more(self, gid):
        self.calls.append(("more", gid))
        self.n_gens += 1
        return {"generation": f"G{self.n_gens:03d}"}

    def creator_job(self, run_id):
        return getattr(self, "creator_jobs", {}).get(run_id)

    def animate(self, gid, loop=False, *, creator_run=None, on_job=None):
        self.calls.append(("animate", gid))
        self.n_jobs += 1
        if creator_run:
            if not hasattr(self, "creator_jobs"):
                self.creator_jobs = {}
            self.creator_jobs[creator_run] = {"id": f"J{self.n_jobs:03d}", "status": "CLAIMED"}
        if on_job:
            on_job({"id": f"J{self.n_jobs:03d}"})
        return {"job": f"J{self.n_jobs:03d}", "estimate": 9.0}

    def review(self, gid, decision, indexes, note="", gate="still"):
        self.calls.append(("review", gid, decision, list(indexes)) if gate == "still" else ("review", gid, decision, list(indexes), gate))
        key = "still" if gate == "still" else "anim"
        for s in self.gens[gid]["stickers"]:
            if s["index"] in indexes:
                s[key] = "APPROVED" if decision == "APPROVE" else "REJECTED"
        return {"done": list(indexes), "refused": []}

    def allowable(self, gid, kind="still", allow=True):
        """The blocked stickers a person may allow (or take back): in the fake, every FAILED one of that kind carries no override flag."""
        if kind == "animation":
            return [s["index"] for s in self.gens[gid]["stickers"] if s.get("anim_status") == "FAILED" and not s.get("anim_override") == ()]
        return [s["index"] for s in self.gens[gid]["stickers"] if s.get("status") == "FAILED"]

    def allow(self, gid, indexes, kind="still", allow=True):
        self.calls.append(("allow", gid, list(indexes), kind, allow))
        key = "anim_override" if kind == "animation" else "still_override"
        done = []
        for s in self.gens[gid]["stickers"]:
            if s["index"] not in indexes:
                continue
            if allow:
                s[key] = [s.get("anim_reason" if kind == "animation" else "reason") or "blocked"]
                if kind == "animation":
                    s["anim_status"] = "READY"
                else:
                    s["status"], s["reason"] = "READY", None
                    s["still"] = "PENDING"
            else:
                s[key] = []
                if kind == "animation":
                    s["anim_status"] = "FAILED"
                else:
                    s["status"], s["reason"] = "FAILED", "inside_cell"
                    s["still"] = "BLOCKED"
            done.append(s["index"])
        return {"done": done, "refused": [], "kind": kind, "allow": allow}

    def judge(self, gid):
        self.calls.append(("judge", gid))
        return {"rejected": list(self.judge_rejects), "approved": [s["index"] for s in self.gens[gid]["stickers"] if s["index"] not in self.judge_rejects]}

    def pack_add(self, gid, name):
        self.calls.append(("pack_add", gid, name))
        return {"pack_id": "P1", "added": len([s for s in self.gens[gid]["stickers"] if s["status"] == "READY"]), "kind": "static"}

    def packs(self):
        return list(getattr(self, "pack_list", []))

    def emoji_packs(self):
        return list(getattr(self, "pack_list", []))

    def particle_sets(self):
        return [dict(x) for x in getattr(self, "sets", [])]

    def particle_deleted(self):
        return [dict(x) for x in getattr(self, "deleted", [])]

    def particle_options(self, pack_id, n=4):
        return ["pink hearts", "gold stars", "tiny sparkles", "flower petals", "soft bubbles"][:n]

    def particle_owners(self, pack_id=None, generation=None, sticker_ids=None):
        return [dict(o) for o in getattr(self, 'owner_rows', []) if (not pack_id or o.get('pack_id')==pack_id) and (not generation or o.get('generation')==generation) and (not sticker_ids or o['sticker_id'] in sticker_ids or f"{o.get('generation')}/S{o.get('index')}" in sticker_ids)]

    def particles_link(self, set_id, sticker_ids):
        self.calls.append(('particles_link', set_id, list(sticker_ids)))
        return {'id':set_id}

    def particles_burst(self, set_id, pack_id):
        self.calls.append(('particles_burst', set_id, pack_id))
        return {'id':'R001', 'status':'READY'}

    def particles_add(self, set_id, pack_id):
        self.calls.append(('particles_add', set_id, pack_id))
        return {'added':1}

    def particles_start(self, set_id=None, pack_id=None, grid="2x2", elements=None, name=None, owners=None, fresh=False):
        self.calls.append(("particles_start", set_id, pack_id, grid, list(elements or [])))
        self.n_jobs += 1
        return {"set": set_id or "S9", "job": f"J{self.n_jobs:03d}", "estimate": 2.0, "grid": [2, 2]}

    def particles_alone(self, request, grid="2x2"):
        self.calls.append(("particles_alone", request, grid))
        self.n_jobs += 1
        return {"set": "S8", "job": f"J{self.n_jobs:03d}", "estimate": 2.0, "grid": [2, 2], "name": "Lipstick, Ribbon particles"}

    def particles_delete(self, set_id, confirm=False):
        self.calls.append(("particles_delete", set_id, confirm))
        return {"ok": True, "id": set_id, "trashed": True}

    def particles_restore(self, set_id):
        self.calls.append(("particles_restore", set_id))
        return {"id": set_id}

    def particles_assign(self, set_id, packs):
        self.calls.append(("particles_assign", set_id, list(packs)))
        return {"id": set_id, "packs": list(packs)}

    def effects_start(self, pack_id, allowed=False):
        self.calls.append(("effects_start", pack_id, allowed))
        p = next(x for x in self.packs() if x["id"] == pack_id)
        return {"id": "E001", "pack_name": p["name"], "count": p["count"]}

    def telegram_ready(self):
        return (True, "") if self.telegram else (False, "Telegram is not connected.")

    def telegram_send(self, pid):
        self.calls.append(("telegram_send", pid))
        return {"sets": [{"kind": "static", "name": "pack_by_bot", "link": "https://t.me/addstickers/pack_by_bot", "added": 9, "total": 9}]}

    def ready_indexes(self, gid):
        return [s["index"] for s in self.gens[gid]["stickers"] if s["status"] == "READY"]

    def name_proposals(self, gid, ask, allowed=None):
        self.calls.append(("name_proposals", gid, allowed))
        if allowed is not True:
            raise ToolError("AI vision is not allowed", 409)
        items = [{"index": s["index"], "current": s["key"].replace("_", " "), "caption": f"fake caption {s['index']}"} for s in self.gens[gid]["stickers"] if s["status"] == "READY"]
        got = ask(items) or {}
        rows = []
        for it in items:
            a = got.get(it["index"]) or {}
            fits = bool(a.get("fits", True)) or not a.get("name")
            rows.append({**it, "fits": fits, "name": None if fits else a["name"]})
            if not fits:
                self.proposed.setdefault(gid, {})[it["index"]] = a["name"]
        return rows

    def apply_titles(self, gid, indexes=None):
        self.calls.append(("apply_titles", gid))
        mine = self.proposed.get(gid, {})
        pick = {i: n for i, n in mine.items() if not indexes or i in indexes}
        for i, n in pick.items():
            for s in self.gens[gid]["stickers"]:
                if s["index"] == i:
                    s["title"] = n
            mine.pop(i, None)
        return pick

    def pending_titles(self, gid):
        return dict(self.proposed.get(gid, {}))

    def describe(self, gid, index, question, allowed=None, on_delta=None):
        self.calls.append(("describe", gid, index, question, allowed))
        if allowed is not True:
            raise ToolError("AI vision is not allowed", 409)
        text = f"The character in S{index} is holding a book. He is looking down at its pages."
        if on_delta:
            on_delta(text[:len(text) // 2])
            on_delta(text[len(text) // 2:])
        return text

    def captions(self, gid, allowed=None):
        self.calls.append(("captions", gid, allowed))
        if allowed is not True:
            raise ToolError("AI vision is not allowed", 409)
        return [{"index": s["index"], "caption": f"fake caption {s['index']}", "error": None} for s in self.gens[gid]["stickers"] if s["status"] == "READY"]
