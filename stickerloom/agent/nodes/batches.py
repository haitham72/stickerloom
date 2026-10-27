"""Batches intent handlers. State is owned by Agent; no module state."""
from __future__ import annotations
import time
from ...runtime import cache as cachemod
from .. import creator, stages
from ..resolver import batch_intent
from ..tools import ToolError
from ..lifecycle.turn import Turn, State
from .shared import SUGGESTIONS, _credits, compact_plan

class BatchesNodes:
    @staticmethod
    def _first_batch(prompt: str, subject: str, grid: str, sid: str, ai: bool, plan: dict, card: dict) -> dict:
        """Batch 01 of the pack this request starts: the pack it opens (`sess["pack"]` once it starts) and the card's "Batch 01 of 04 · core"."""
        top = max(1, min(stages.MAX_BATCHES, int(plan.get("batches_max") or 1)))
        label = stages.batch_label(1, plan)
        if top > 1:
            card["batch"] = {"no": 1, "max": top, "label": label, "text": f"Batch 01 of {top:02d} · {label.split(' · ', 1)[1]}"}
        return {"no": 1, "label": label, "pack": {"request": prompt, "subject": subject, "grid": grid, "style_id": sid, "ai": ai, "plan": compact_plan(plan), "max": top,
                                                  "transformation": bool(plan.get("transformation"))}}

    def _track_batch(self, t: Turn, it: dict, r: dict) -> None:
        """A batch of the pack started: batch 01 opens the pack, a later batch or a regenerated one takes its number's row (the newest take is the batch)."""
        b = it.get("batch")
        if not b:
            return
        if b.get("pack"):
            t.sess["pack"] = {**b["pack"], "made": []}
        pk = t.sess.get("pack")
        if not pk:
            return
        manual = (it.get("plan") or {}).get("slots", {}).get("emoji_batch")
        if manual:
            pk.update(manual=True, library_id=manual.get("pack_id"))
        row = {"no": int(b["no"]), "label": b.get("label"), "generation": r.get("generation"), "job": r.get("job"), "plan": it.get("plan"),
               "creator": bool(it.get("creator_item")), "ts": round(time.time(), 3)}
        pk["made"] = sorted([x for x in pk.get("made") or [] if x["no"] != row["no"]] + [row], key=lambda x: x["no"])

    @staticmethod
    def _batch_tag(t: Turn, it: dict) -> str | None:
        """"Batch 02 · social" on a generation card, when the batch belongs to a pack of more than one batch."""
        b = it.get("batch") or {}
        top = int((b.get("pack") or t.sess.get("pack") or {}).get("max") or 1)
        return b.get("label") if b and (int(b.get("no") or 1) > 1 or top > 1) else None

    def _row_gen(self, row: dict) -> str | None:
        if not row.get("generation") and row.get("job"):
            try:
                row["generation"] = self.tools.job(row["job"]).get("generation")
            except Exception:
                pass
        return row.get("generation")

    def _batches_made(self, pk: dict) -> int:
        """How many batches the pack has: the chat's own, or more when the Studio made some of the same pack (`tools.batch_state`, the Studio picker's count)."""
        rows = pk.get("made") or []
        gens = [g for g in (self._row_gen(r) for r in rows) if g]
        known = self.tools.batch_state(gens) if gens else None
        return max(len(rows), int(known or 0))

    def _batch_price(self, stage: str):
        """One batch at this stage: the sheet, plus the animation when the stage animates (rule 13: the button states it before anything spends)."""
        if not self.tools.live():
            return None
        est = self.tools.estimate("image")
        if est is not None and stages.animates(stage):
            est = round(est + (self.tools.estimate("video") or 0), 2)
        return est

    def _followup(self, sess: dict) -> tuple[str, list]:
        """The ONE follow-up card after a batch reaches the stage's end: Regenerate · Batch 02 · Batch 03 · Batch 04 (only the batches still possible; "Batch 03"
        with one made makes 02 and 03). A transformation (one character) gets Regenerate only."""
        pk = sess.get("pack") or {}
        per = self._batch_price(stages.of(sess["settings"]))
        cost = lambda n: f" · {per * n:g} credits" if per else ""
        existing = self._batches_made(pk)
        top = min(int(pk.get("max") or 1), stages.MAX_BATCHES)
        last = next((g for r in reversed(pk.get("made") or []) for g in [self._row_gen(r)] if g), None)
        chips = [{"label": "Regenerate" + cost(1), "action": "regenerate", "generation": last}] if last else []
        if pk.get("manual"):
            return f"**{pk.get('subject') or 'The pack'}** is ready. Paste nine emojis for another batch; I will check repeats in this pack.", chips
        chips += [{"label": f"Batch {k:02d}" + cost(k - existing), "action": "batch_more", "to": k} for k in range(max(existing, 1) + 1, top + 1)]
        name = pk.get("subject") or "the pack"
        if top <= 1:
            text = f"**{name}** is ready. It is one character, so there are no other batches: Regenerate makes another take of the same plan."
        elif existing >= top:
            text = f"**{name}**: all {top} batches of the pack are made. Regenerate makes another take of the newest one."
        elif not existing:
            text = f"This is batch 01 of {top:02d} of **{name}**. Plan more batches with it, or Generate this one."
        else:
            text = f"**{name}**: batch {existing:02d} of {top:02d} is ready. Regenerate it, or make the next batches of the pack."
        return text, chips

    def _batch_item(self, pk: dict, no: int, plan: dict, prompt: str) -> dict:
        label = stages.batch_label(no, plan)
        return {"prompt": prompt, "subject": pk["subject"], "grid": pk.get("grid") or "3x3", "style_id": pk["style_id"], "ai": bool(pk.get("ai", True)), "plan": compact_plan(plan),
                "note": label, "batch": {"no": no, "label": label}}

    def n_batches(self, state: State) -> dict:
        """"Batch 03" (a button of the follow-up card, or typed: "next batch", "batch 3", "make the rest of the pack"): the batches after the ones made, planned through
        `tools.more_batches` (an emoji pack's next preset grids, else the next unused bank actions; never a repeat, never past batch 04), ONE card, ONE total price,
        ONE go-ahead; each runs to the chat's stage."""
        t: Turn = state["turn"]
        sess = t.sess
        pk = sess.get("pack")
        if not pk:
            t.reply = "There is no pack open in this chat to continue. Tell me what to make first."
            t.chips = [{"label": s, "text": s} for s in SUGGESTIONS[:3]]
            return {}
        a = t.action or {}
        to = a.get("to") if a.get("type") == "batch_more" else (batch_intent(t.text) or {}).get("to")
        top = min(int(pk.get("max") or 1), stages.MAX_BATCHES)
        existing = self._batches_made(pk)
        name = pk["subject"]
        t.trace.retitle(f"more batches of {name}")
        if top <= 1:
            t.reply, t.chips = self._followup(sess)
            t.reply = f"**{name}** is one character, so there are no other batches to make. Regenerate makes another take."
            t.trace.end("no other batches")
            return {}
        to = top if to == "rest" else (max(existing, 1) + 1 if to is None else int(to))
        if to > top:
            _, t.chips = self._followup(sess)
            t.reply = f"**{name}** has {top} batches at most, so there is no batch {to:02d}" + (f"; {existing} {'is' if existing == 1 else 'are'} made." if existing else ".")
            t.trace.end("no such batch")
            return {}
        if to <= existing:
            _, t.chips = self._followup(sess)
            t.reply = f"Batch {to:02d} of **{name}** is already made." + (" Pick a later one, or Regenerate." if existing < top else " Every batch of the pack is made; Regenerate makes another take.")
            t.trace.end("already made")
            return {}
        items, start = [], existing + 1
        if not pk.get("made"):                                     # nothing made yet (the Prompt stage, or a plan never started): batch 01 goes with them
            items.append(self._batch_item(pk, 1, pk["plan"], pk["request"]))
            start = 2
        if to >= start:
            gens = [g for g in (self._row_gen(r) for r in pk.get("made") or []) if g]
            try:
                plans = self.tools.more_batches(pk["request"], pk["plan"], to - start + 1, pk["style_id"], [r.get("plan") for r in pk.get("made") or []], gens)
            except ToolError as e:
                t.reply = f"I couldn't plan the next batches: {e}"
                t.trace.end("could not plan", ok=False)
                return {}
            items += [self._batch_item(pk, start + i, p, p.get("prompt") or pk["request"]) for i, p in enumerate(plans)]
        if len(items) < to - existing:
            t.trace.note("the bank has no more new actions for this pack" if items else "nothing new is left to draw for this pack")
        if not items:
            t.reply = f"Every action of the bank is already in **{name}**. Regenerate a batch you want better, or start a new request."
            t.trace.end("nothing left")
            return {}
        t.trace.step("planned " + ", ".join(i["batch"]["label"] for i in items), {"title": name, "lines": [i["batch"]["label"] for i in items]})
        return self._offer_batches(t, name, items)

    def n_regenerate(self, state: State) -> dict:
        """"Regenerate" (the follow-up card, or "regenerate it"): a new generation of the batch from its own saved plan, the Studio's Regenerate (parent + regen_of, so it
        joins the batch's family); priced on the button, one go-ahead, run to the chat's stage."""
        t: Turn = state["turn"]
        sess, st = t.sess, t.sess["settings"]
        pk = sess.get("pack") or {}
        gid = (t.action or {}).get("generation") or next((g for r in reversed(pk.get("made") or []) for g in [self._row_gen(r)] if g), None) \
            or (sess.get("focus") or {}).get("generation")
        if not gid:
            t.reply = "There is nothing to regenerate yet: the batch has not arrived. Wait for it, or tell me what to make."
            return {}
        try:
            plan = self.tools.generation_plan(gid)
        except ToolError as e:
            t.reply = f"I can't read the plan of {self._nm(sess, gid)} to make it again: {e}"
            return {}
        row = next((r for r in pk.get("made") or [] if self._row_gen(r) == gid), None)
        subj = self.store.subject_for_generation(sess, gid)
        p = next((q for q in (subj or {}).get("passes", []) if q.get("generation") == gid), {})
        label = (row or {}).get("label") or self._nm(sess, gid)
        grid = p.get("grid") or pk.get("grid") or st["grid"]
        item = {"prompt": p.get("prompt") or pk.get("request") or self._nm(sess, gid), "subject": (subj or {}).get("name") or pk.get("subject") or self._nm(sess, gid), "grid": grid,
                "style_id": p.get("style_id") or pk.get("style_id") or st["style_id"], "ai": True, "plan": compact_plan(plan), "parent": gid, "regen_of": gid,
                "note": f"regenerated: {label}", **({"batch": {"no": row["no"], "label": row.get("label")}} if row else {}), **self._edge(gid)}
        t.trace.retitle(f"regenerating {label}")
        t.trace.step(f"a new take of {label}", {"title": f"from {self._nm(sess, gid)}", "lines": ["the same plan, a new sheet; the old one stays"]})
        return self._offer_batches(t, item["subject"], [item], regen=True)

    def _offer_batches(self, t: Turn, name: str, items: list, regen: bool = False) -> dict:
        sess, st = t.sess, t.sess["settings"]
        stage = stages.of(st)
        per = self._batch_price(stage)
        what = ", ".join((i.get("batch") or {}).get("label") or i["note"] for i in items)
        title = (f"Regenerate {what}" if regen else f"{name}: {what}")
        card = self._items_card(title, items, st, per, {"batches": [(i.get("batch") or {}).get("label") for i in items], "stage": stage})
        total = card["estimate"]
        spec = {"type": "batch_more", "items": items, "stage": stage, "estimate": total, "subject": name, "label": title}
        t.cards.append(card)
        them = "them" if len(items) > 1 else "it"
        if stage == "prompt":                                        # the Prompt stage plans only: Generate is the go-ahead, whatever "ask before spending" says
            sess["pending"] = spec
            t.reply = f"Here {'are the plans' if len(items) > 1 else 'is the plan'} for **{name}**: {what}. Nothing is spent at the Prompt stage: Generate makes {them} ({_credits(total)})."
            t.chips = [{"label": "Generate" + (f" · {total:g} credits" if total else ""), "action": "confirm"}, {"label": "Not yet", "action": "cancel"}]
            t.trace.end("plans ready · nothing spent")
            return {}
        if self.tools.live() and st.get("ask_before_spending", True):
            sess["pending"] = spec
            then = {"animation": ", then animated", "export": ", then animated, packed and sent to Telegram"}.get(stage, "")
            t.reply = (f"I'll make a new take of **{what}** from the same plan; the old one stays" if regen else f"**{name}**: {what}, one sheet each") + f"{then}. Shall I make {them}?"
            t.chips = [{"label": ("Create them" if len(items) > 1 else "Create it") + (f" · {total:g} credits" if total else ""), "action": "confirm"}, {"label": "Not yet", "action": "cancel"}]
            t.trace.end(f"plan ready · {_credits(total)}")
            return {}
        self._run_batch_items(t, items, stage)
        return {}

    def _run_batch_items(self, t: Turn, items: list, stage: str) -> tuple[list, list]:
        """Start batches of the pack to the chat's stage: Emojis (and Generate at the Prompt stage) as sheets side by side (`_start_items`); Animation / Export through the
        creator, one run after the other (`sess["creator_queue"]`, started by `creator_tick` when a run is done; Stop clears it)."""
        spec = stages.run_spec("emojis" if stage == "prompt" else stage, creator.settings_of(t.sess)["bypass"])
        if not spec["creator"]:
            return self._start_items(t, items)
        live = self.tools.live()
        est, v_est = (self.tools.estimate("image"), self.tools.estimate("video")) if live else (None, None)
        runs = [{**it, "type": "creator", "estimate": est, "video_estimate": v_est, "scope": "video", "bypass": spec["bypass"], "end": spec["end"]} for it in items]
        queue = t.sess.get("creator_queue") or []
        if (t.sess.get("creator_run") or {}).get("status") in ("running", "waiting"):
            t.sess["creator_queue"] = queue + runs
            t.reply = "Queued: " + ", ".join((r.get("batch") or {}).get("label") or r["subject"] for r in runs) + " start when the creator finishes the run it is on."
            t.trace.end("queued")
            return runs, []
        self._start_creator(t, runs[0])
        t.sess["creator_queue"] = queue + runs[1:]
        if runs[1:]:
            t.reply += " Then " + ", ".join((r.get("batch") or {}).get("label") or r["subject"] for r in runs[1:]) + ", one after the other."
        return runs, []

    def batch_followup(self, sid: str) -> bool:
        """Post the follow-up card once the batches the chat started last are cut (the Emojis stage's end; a creator run says its own when it is done). Called on every
        poll of the chat (web and Telegram); takes the session's lock like any turn and is skipped while a turn holds it. True when it posted."""
        lock = self.cache.lock(f"session:{sid}")
        try:
            lock.__enter__()
        except cachemod.Busy:
            return False
        try:
            sess = self.store.load(sid)
            pk = sess.get("pack") or {}
            rows = pk.get("made") or []
            if not rows or max(rows, key=lambda r: r.get("ts") or 0).get("creator"):
                return False
            sig = "|".join(f"{r['no']}:{r.get('job') or r.get('generation')}" for r in rows)
            if pk.get("said") == sig:
                return False
            for r in rows:
                if r.get("creator"):
                    continue
                if r.get("job") and str(self.tools.job(r["job"]).get("status") or "") in ("FAILED", "TIMEOUT"):
                    continue
                gid = self._row_gen(r)
                if not gid:
                    return False
                card = self.tools.generation(gid)
                if not card.get("problem") and (not card.get("stickers") or any(s.get("status") == "PENDING" for s in card["stickers"])):
                    return False
            text, chips = self._followup(sess)
            pk["said"] = sig
            if chips:
                self.store.add_message(sess, "assistant", text, chips=chips, cards=[])
            self.store.save(sess)
            return bool(chips)
        finally:
            lock.__exit__(None, None, None)
