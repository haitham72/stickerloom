"""Creator intent handlers. State is owned by Agent; no module state."""
from __future__ import annotations
from ...runtime import cache as cachemod
from .. import creator
from ..lifecycle.turn import Turn, State, Trace
from .shared import _credits

class CreatorNodes:
    def _creator_plan(self, t: Turn, subject: str, prompt: str, names: list, est, card: dict, cs: dict, end: str = "export", style_id: str | None = None,
                      plan: dict | None = None, batch: dict | None = None) -> dict:
        """The Animation and Export stages: the creator with the whole price (sheet + animation) on the card before the go-ahead. `end` is the stage:
        'animation' stops after the animations are approved, 'export' packs and sends (D1)."""
        sess, st = t.sess, t.sess["settings"]
        if (sess.get("creator_run") or {}).get("status") in ("running", "waiting"):
            t.reply = "The creator is still working on your last pack. Wait for it, or press Stop on its card."
            t.trace.end("busy", ok=False)
            return {}
        v_est = self.tools.estimate("video") if cs["scope"] == "video" and self.tools.live() else None
        total = round((est or 0) + (v_est or 0), 2) or None
        spec = {"type": "creator", "prompt": prompt, "subject": subject, "grid": st["grid"], "style_id": style_id or st["style_id"], "ai": bool(st.get("ai", True)), "estimate": est,
                "video_estimate": v_est, "scope": cs["scope"], "bypass": cs["bypass"], "end": end, **({"plan": plan} if plan else {}), **({"batch": batch} if batch else {})}
        card.update(estimate=total, creator={"scope": cs["scope"], "bypass": cs["bypass"], "sheet": est, "video": v_est, "end": end})
        how = "approving everything for you and stopping at the first rejection" if cs["bypass"] else "stopping at each approval for one click from you"
        if end == "animation":
            t.reply = f"Plan for **{subject}**: {len(names)} stickers, then animated, {how}. It ends with the animations: nothing is packed or sent. Shall I run it?"
            go = "Create and animate"
        else:
            ready, why = self.tools.telegram_ready()
            what = "the stickers as a static pack" if cs["scope"] == "images" else "the stickers animated"
            t.reply = (f"Creator plan for **{subject}**: {len(names)} stickers, then {what}, then to Telegram, {how}. "
                       + ("" if ready else "Telegram is not connected yet, so I will stop before sending. ") + "Shall I run it?")
            go = "Create and send to Telegram"
        t.trace.step("creator: " + " > ".join(label for _, label in creator.steps_of({"scope": cs["scope"], "end": end})))
        if self.tools.live() and st.get("ask_before_spending", True):
            sess["pending"] = spec
            t.cards.append(card)
            t.chips = [{"label": go, "action": "confirm"}, {"label": "Not yet", "action": "cancel"}]
            t.trace.end(f"plan ready · {_credits(total)}")
            return {}
        self._start_creator(t, spec)
        return {}

    def _start_creator(self, t: Turn, p: dict) -> None:
        sess = t.sess
        self._start_create(t, {**p, "creator_item": True}, parent=p.get("parent"), regen_of=p.get("regen_of"), note=p.get("note", ""))
        card = next((c for c in reversed(t.cards) if c.get("type") == "generation"), None)
        if not card:
            return
        run = creator.new_run(prompt=p["prompt"], subject=p["subject"], grid=p["grid"], style_id=p["style_id"], scope=p.get("scope", "images"), bypass=p.get("bypass", False),
                              estimate=p.get("estimate"), video_estimate=p.get("video_estimate"), end=p.get("end", "export"))
        run["job"], run["generation"] = card.get("job"), card.get("generation")
        batch_no = (p.get("batch") or {}).get("no", 1)
        if batch_no > 1:
            run["pack_name"] = f"Batch {batch_no:02d} {p['subject']}"
        creator._log(run, "started: " + ("the sheet is being drawn" if run["job"] else "the sheet is ready"))
        sess["creator_run"] = run
        t.cards.append({"type": "creator", "run_id": run["id"]})
        t.reply = f"Running the creator for **{p['subject']}**. I will stop and tell you if anything is rejected."
        t.chips = []

    def n_creator(self, state: State) -> dict:
        """The person's button on the creator card (continue / skip / force / stop), or "continue" typed while it waits."""
        t: Turn = state["turn"]
        run = t.sess.get("creator_run")
        act = str((t.action or {}).get("type") or "creator_go")
        if not run:
            t.reply = "There is no creator run to continue."
            return {}
        creator.resume(run, act, (t.action or {}).get("indexes"))
        if act == "creator_stop":
            t.sess.pop("creator_queue", None)                       # Stop means stop: the queued batches of the pack are not started either
        t.trace.task("continuing the creator" if act != "creator_stop" else "stopping the creator")
        t.trace.step({"creator_go": "approved", "creator_skip": "continuing without those stickers", "creator_force": "continuing with them anyway",
                      "creator_allow": "you allowed a block Python flagged: cutting it again", "creator_unallow": "the permission is taken back",
                      "creator_force_video": "animation price accepted", "creator_stop": "stopped"}.get(act, act))
        t.trace.end("done")
        t.reply = "Stopped. Nothing more will be made or spent; what exists stays in the Studio." if act == "creator_stop" else "Continuing."
        t.cards.append({"type": "creator", "run_id": run["id"]})
        return {}

    def creator_tick(self, sid: str) -> str:
        """Advance the session's creator run as far as it can go right now and say, as a message of its own, what happened when it stops, waits or finishes. Takes the session's lock like any
        turn (a person's message in the middle gets its 409 for a moment, never a clash). Returns the run's status ('none' without a run, 'busy' when a turn holds the lock)."""
        lock = self.cache.lock(f"session:{sid}")
        try:
            lock.__enter__()
        except cachemod.Busy:
            return "busy"
        try:
            sess = self.store.load(sid)
            run = sess.get("creator_run")
            if not run:
                return "none"
            if run["status"] == "running":
                creator.advance(self.tools, run, sess["settings"].get("allow_vlm") is True, self.tools.telegram_ready,
                                checkpoint=lambda current: self.store.save(sess))
            mark = f"{run['status']}:{run['step']}"
            if run["status"] in ("stopped", "waiting", "done") and run.get("said") != mark:
                run["said"] = mark
                self._creator_say(sess, run)
            if run["status"] == "done" and sess.get("creator_queue"):
                self._next_queued(sess)                             # the next batch of the pack, already priced and approved with the first
                run = sess["creator_run"]
            self.store.save(sess)
            return run["status"]
        finally:
            lock.__exit__(None, None, None)

    def _next_queued(self, sess: dict) -> None:
        spec = sess["creator_queue"].pop(0)
        msg = self.store.add_message(sess, "assistant", "", status="working")
        t = Turn(sess["id"], "", [], None, sess, msg, Trace(self.store, sess, msg))
        t.trace.task(f"starting {(spec.get('batch') or {}).get('label') or spec['subject']}")
        self._start_creator(t, spec)
        msg.update(text=t.reply, cards=t.cards, chips=t.chips, status="done")

    def _creator_say(self, sess: dict, run: dict) -> None:
        name = run["subject"]
        if run["status"] == "done" and run.get("end") == "animation":
            text, chips = f"Done: **{name}** is animated and the animations are approved. Nothing was packed or sent; say \"export\" to pack it and send it to Telegram.", []
        elif run["status"] == "done":
            links = [s["link"] for s in (run.get("telegram") or {}).get("sets", [])]
            text, chips = f"Done: **{name}** is on Telegram. " + " ".join(links), []
        if run["status"] == "done" and not sess.get("creator_queue") and (sess.get("pack") or {}).get("made"):
            more, chips = self._followup(sess)                       # the stage's end: the ONE follow-up card (Regenerate · Batch 02 · 03 · 04)
            text += "\n\n" + more
            pk = sess["pack"]
            pk["said"] = "|".join(f"{r['no']}:{r.get('job') or r.get('generation')}" for r in pk["made"])
        elif run["status"] == "waiting":
            text, chips = f"**{name}**: {run['waiting']['why']}.", run["waiting"]["chips"]
        elif run["status"] != "done":
            text, chips = f"**{name}**: stopped, {run['stop']['why']}", run["stop"]["chips"]
        cards = [{"type": "creator", "run_id": run["id"]}]
        # The picture travels with the message (CLAUDE.md rule 10: a rejection is never a bare word). A run that stopped on a block shows
        # the batch's own carousel, so the rejected stickers are visible where the buttons that decide about them are.
        if run["status"] == "stopped" and run.get("generation") and run["stop"].get("kind") != "error":
            cards.append({"type": "generation", "generation": run["generation"], "subject": name})
        self.store.add_message(sess, "assistant", text, chips=chips, cards=cards)
