"""Edits intent handlers. State is owned by Agent; no module state."""
from __future__ import annotations
import re
from .. import editroute
from ..tools import ToolError
from ..lifecycle.turn import Turn, State
from .shared import _credits, compact_plan, _with_pending

class EditsNodes:
    def _explicit_targets(self, t: Turn) -> list:
        """The stickers the person POINTED at in this message (a number, an id, a selection, "the shocked one"): a slice. A pronoun ("him", "it") is the character, so the whole sheet."""
        if re.match(r"\s*(?:replace|swap)\b", t.text, re.I):
            return list(t.res.stickers)
        return list(t.res.stickers) if t.res.how in ("number", "explicit id", "selection", "concept", "make A like B", "feedback clauses") else []

    def n_editroute(self, state: State) -> dict:
        """"can you rotate him?" -> the EDITOR (a transformation or a cleaning of one slice, free); "make him cry" / "now make him play football" / "make it as a lemon" -> drawn again, and what is sent
        depends on the case (agent/editroute.py): the sheet as the picture for a tweak and a new action, no picture for a redesign; the parent's own saved prompt is what changes, never a sentence
        rebuilt from the words. The batch is the one in focus, the sheet the person picked in this chat."""
        t: Turn = state["turn"]
        er = t.er or editroute.classify_edit(t.text) or {}
        gen = t.res.generation or (t.sess.get("focus") or {}).get("generation") or (self.store.latest_pass(t.sess, with_generation=True) or {}).get("generation")
        if not gen or not er:
            t.reply = "There is nothing to change yet. Let's make some stickers first."
            return {}
        replacement = re.match(r"\s*(?:replace|swap)\s+(.+?)\s+(?:with|for)\s+(.+)", t.text, re.I)
        if replacement and re.fullmatch(r"(?:#?\d+|s\d+|g\d+/s\d+|(?:the )?(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth)(?: sticker| one)?)", replacement[2], re.I):
            target = ", ".join(x.split("/")[1] for x in t.res.stickers) or replacement[1]
            t.reply = f"Do you want to copy {replacement[2]} into {target}, or swap the two stickers?"
            t.trace.end("asked what replacement means")
            return {}
        if er["route"] == "editor":
            self._profile().count_edit("editor")
            return self._editor_offer(t, gen, er)
        interpreted = self.brain.edit_meaning(t.text, self.store.summary_text(t.sess)) if self.brain.available else None
        if interpreted and not interpreted["clear"]:
            t.reply = ((interpreted["understood"] + " ") if interpreted["understood"] else "") + (interpreted["clarification"] or "What should I change?")
            t.trace.end("asked what the edit means")
            return {}
        if interpreted:
            case = interpreted["case"]
            er = {**er, "case": case, "subject": None, "action": None, "delta": None,
                  {"tweak": "delta", "action": "action", "redesign": "subject"}[case]: interpreted["change"]}
        elif not editroute.clear_change(t.text, er):
            t.reply = "I couldn't confidently understand the whole change. What should I change, and on which sticker?" + self._edit_hint()
            t.trace.end("asked what the edit means")
            return {}
        if re.match(r"\s*(?:replace|swap)\b", t.text, re.I) and not t.res.stickers:
            t.reply = "Which sticker should I replace? Give its number or select it."
            t.sess["awaiting"] = {"intents": ["EDIT_ROUTE"], "text": t.text}
            t.trace.end("asked which sticker")
            return {}
        return self._regen_offer(t, gen, er)

    def _editor_offer(self, t: Turn, gen: str, er: dict) -> dict:
        t.trace.retitle("opening the editor")
        pointed = self._explicit_targets(t)
        focus = (t.sess.get("focus") or {}).get("stickers") or []
        sid = (pointed or [s for s in focus if s.startswith(gen + "/")] or [None])[0]
        if not sid:
            t.reply = "Which sticker should I open in the editor? Say a number, like \"flip number 3\", or click one."
            t.sess["awaiting"] = {"intents": ["EDIT_ROUTE"], "text": t.text}
            t.trace.end("asked which sticker")
            return {}
        index = int(sid.split("/S")[1])
        what = {"rotate": "rotating", "flip": "flipping", "crop": "cropping", "clean": "cleaning", "text": "adding text"}.get((er.get("ops") or ["rotate"])[0], "that")
        t.sess["focus"] = {"generation": gen, "stickers": [sid]}
        t.reply = (f"Sure — {what} {self._key_of(gen, sid)} ({sid.split('/')[1]}) is a job for the editor, not a new picture, so nothing is spent. "
                   "Want me to boot up the editor for you? When you save, you come straight back here with the slice updated.")
        t.chips = [{"label": "Open the editor", "editor": {"generation": gen, "index": index}}]
        t.trace.end("editor offered")
        return {}

    def _regen_offer(self, t: Turn, gen: str, er: dict) -> dict:
        case = er["case"]
        self._profile().count_edit(case)
        what = {"tweak": er.get("delta"), "action": er.get("action"), "redesign": er.get("subject")}[case]
        try:
            base = self.tools.generation_plan(gen)
        except ToolError as e:
            t.reply = f"I can't read the prompt of {self._nm(t.sess, gen)} to change it: {e}"
            return {}
        subj = self.store.subject_for_generation(t.sess, gen)
        old = (subj or {}).get("name") or base.get("subject") or "the stickers"
        name = what if case == "redesign" else old
        st = t.sess["settings"]
        t.trace.retitle({"tweak": "changing a detail", "action": "a new action", "redesign": f"redesigning as {what}"}[case])
        pointed = self._explicit_targets(t)[:4]
        est = self.tools.estimate("image") if self.tools.live() else None
        if pointed:
            return self._regen_slices(t, gen, base, pointed, case, what, name, est)
        plan = editroute.plan_for(base, case, subject=er.get("subject"), action=er.get("action"), delta=er.get("delta"), said=t.text)
        refs, clause, sheet_px = [], None, None
        if editroute.sends_image(case):
            try:
                ref = self.tools.sheet_reference(gen)
            except ToolError as e:
                t.reply = f"I can't send the sheet of {self._nm(t.sess, gen)} as the picture: {e}"
                return {}
            refs, clause, sheet_px = [ref["ref"]], editroute.reference_clause(case, what), ref.get("px")
        rows, cols = (plan.get("grid") or [3, 3])[:2] if isinstance(plan.get("grid"), (list, tuple)) else (3, 3)
        sid = plan["slots"].get("style_id") or st["style_id"]
        change = {"tweak": f"change only: {what}", "action": f"new action: {what}", "redesign": f"new design: {what}"}[case]
        item = {"prompt": name, "subject": name, "grid": f"{rows}x{cols}", "style_id": sid, "ai": True, "plan": compact_plan(plan), "parent": gen, "refs": refs, "ref_clause": clause,
                "note": f"{case}: {what}", "changes": [change], **self._edge(gen)}
        t.trace.step(f"{case}: {what}", {"title": f"from {self._nm(t.sess, gen)}", "lines": [f"the picture: {'this sheet (' + str(sheet_px) + ' px)' if refs else 'none: the actions are approved, the design is new'}"]})
        card = self._items_card(f"{name}: {change}", [item], st, est, {"edit": case, "image": bool(refs)})
        how = {"tweak": f"I'll send **this sheet** as the picture with the same prompt and change only: {what}.",
               "action": f"The shape stays: I'll send **this sheet** as the picture with the same prompt, and every character does “{what}” instead.",
               "redesign": f"Same actions, new design: I'll make **{what}** from the same prompt as {old}, and **no picture is sent** (the actions are approved, only the design changes)."}[case]
        return self._offer_items(t, f"{name} ({case})", [item], est, card, f"Got it. {how} A new sheet; the original stays.")

    def _regen_slices(self, t: Turn, gen: str, base: dict, pointed: list, case: str, what: str, name: str, est) -> dict:
        """One or a few slices drawn again as 1x1: only THAT slice goes to the provider, as the picture where the case sends one, with its own cell of the parent's prompt."""
        st = t.sess["settings"]
        items, notes = [], []
        for sid in pointed:
            try:
                sp = editroute.plan_for(self.tools.slice_plan(sid), case, subject=what if case == "redesign" else None, action=what if case == "action" else None, delta=what if case == "tweak" else None, said=t.text)
            except ToolError as e:
                t.reply = f"I can't read the prompt of {sid.split('/')[1]}: {e}"
                return {}
            refs, clause = [], None
            if editroute.sends_image(case):
                r = self.tools.slice_reference(sid)
                refs, clause = [r["ref"]], editroute.reference_clause(case, what, slice_=True)
                notes.append(f"{sid.split('/')[1]} is {r['from_px']} px" + (f", under the {r['min_px']} px minimum, so it was scaled up to {r['px']} px" if r.get("scaled") else f", over the {r['min_px']} px minimum, so it is sent as it is"))
            label = sid.split("/")[1]
            items.append({"prompt": f"{name}: {self._key_of(gen, sid)}, {what}", "subject": name, "parent": gen, "regen_of": sid, "refs": refs, "ref_clause": clause, "plan": compact_plan(sp), "label": label,
                          "style_id": sp["slots"].get("style_id") or st["style_id"], "note": f"{label} redone ({case}): {what}", **self._edge(gen)})
        total = (est or 0) * len(items)
        spec = {"type": "batch", "items": items, "style_id": st["style_id"], "ai": True, "estimate": total}
        labels = ", ".join(i["label"] for i in items)
        t.trace.step(f"{case}: {what}", {"title": "only the slice goes to the provider", "lines": notes or ["no picture is sent"]})
        say = f"I'll redo {labels} ({case}: {what}); the rest of the batch stays." + (" " + "; ".join(notes) + "." if notes else "") + (f" That costs {_credits(total)}." if total else "")
        if self.tools.live() and st.get("ask_before_spending", True):
            t.sess["pending"] = spec
            t.reply = say + " Go ahead?"
            t.chips = [{"label": "Do it", "action": "confirm"}, {"label": "Not yet", "action": "cancel"}]
            t.trace.end(f"ready · {_credits(total)}")
        else:
            t.sess["pending"] = spec
            t.reply = say
            self.n_confirm({"turn": _with_pending(t, spec)})
        return {}

    def _edge(self, gen: str) -> dict:
        """The edge finish of the batch an edit is made from (white stroke px, fringe trim px), so the child looks like its parent and not like the defaults. {} when it cannot be read."""
        try:
            e = self.tools.edge_of(gen) or {}
        except Exception:
            e = {}
        return {k: e[k] for k in ("outline", "erode") if e.get(k) is not None}

    def _person_edit(self, t: Turn) -> dict:
        """"him", "the guy", "last guy" with nothing said about what to change: the person means the character of the chat, so the batch is assumed (the focus; "last" = the newest of the chat) and the
        one thing asked is WHAT to change, never "which sticker?" and never a new sheet called "last guy"."""
        gen = t.res.generation or (t.sess.get("focus") or {}).get("generation") or (self.store.latest_pass(t.sess, with_generation=True) or {}).get("generation")
        if not gen:
            t.reply = "There is nothing to change yet. Let's make some stickers first."
            return {}
        t.sess["focus"] = {"generation": gen, "stickers": []}
        t.generation = gen
        t.reply = f"I'll work on **{self._nm(t.sess, gen)}**, the batch we have open. What should I change about him? For example \"make him wear a hat\" or \"make him happier\"."
        t.chips = [{"label": "Make him happier", "text": "make him happier"}, {"label": "Give him a hat", "text": "give him a hat"}]
        t.trace.step("assumed the character of " + self._nm(t.sess, gen))
        t.trace.end("asked what to change")
        return {}

    def n_edit(self, state: State) -> dict:
        """Numbered stickers (and a clicked selection) drawn again as 1x1. AN EDIT REUSES THE PARENT'S PROMPT: the 1x1 is the parent's own plan for that cell (`tools.slice_plan` ->
        `gates.regen_plan`: its style, key colour, template version and cell label) with the change appended to the cell's label, the parent's own picture of that sticker goes as the reference
        with a clause that allows the change, and the batch keeps the parent's edge finish. Nothing is rebuilt from the words of the sentence."""
        t: Turn = state["turn"]
        subj, p = self._focus_pass(t)
        targets = t.res.stickers or (t.res.negative if t.res.negative else [])
        if t.res.how == "person reference" and not targets:
            return self._person_edit(t)
        if not targets or not p:
            t.reply = "Which sticker should I change? Say a number, like \"make number 3 happier\", or click one."
            t.chips = []
            if p:
                t.sess["awaiting"] = {"intents": ["EDIT_STICKERS"], "text": t.text}
            return {}
        gen = targets[0].split("/")[0]
        edit = editroute.delta_of(t.text)
        fresh = editroute.fresh_take(edit)
        if fresh:
            edit = ""
        elif not t.res.references:
            meaning = self.brain.edit_meaning(t.text, self.store.summary_text(t.sess)) if self.brain.available else None
            if (meaning and not meaning["clear"]) or (not meaning and not editroute.clear_change(t.text, {"case": "tweak", "delta": edit})):
                t.reply = (meaning or {}).get("clarification") or "What exactly should I change on this sticker?"
                t.trace.end("asked what the edit means")
                return {}
            if meaning:
                return self._regen_offer(t, gen, {"case": meaning["case"],
                    {"tweak": "delta", "action": "action", "redesign": "subject"}[meaning["case"]]: meaning["change"]})
        like = ", ".join(self._key_of(gen, r["source"]) for r in t.res.references)
        said = (("same look as " + like) if like else "") + (f" ({edit})" if like and edit else edit if edit else "") or "a fresh take"
        card = self.tools.generation(gen)
        st = t.sess["settings"]
        items, notes = [], []
        for sid in targets[:4]:
            s = next((x for x in card["stickers"] if x["id"] == sid), None)
            if not s:
                continue
            try:
                sp = self.tools.slice_plan(sid)
            except ToolError as e:
                t.reply = f"I can't read the prompt of {sid.split('/')[1]} to change it: {e}"
                return {}
            if edit:
                sp = editroute.add_to_label(sp, edit)
            elif like:
                sp = editroute.add_to_label(sp, "same look as " + like)
            base = (subj["name"] if subj else "sticker")
            refs, clause = [], None
            ref_roles = []
            for r in t.res.references:
                if r["target"] in (None, sid) and hasattr(self.tools, "reference_from_sticker"):
                    try:
                        refs.append(self.tools.reference_from_sticker(r["source"]))
                        ref_roles.append({"source": r["source"], "role": r["role"]})
                    except Exception:
                        pass
            if ref_roles:
                clause = editroute.reference_roles_clause(ref_roles, edit)
            if not refs and not fresh:                                      # the sticker as it is, to be changed (a fresh take is drawn from the prompt alone)
                try:
                    r = self.tools.slice_reference(sid)
                    refs, clause = [r["ref"]], editroute.reference_clause("tweak", edit, slice_=True)
                    notes.append(f"{sid.split('/')[1]} is {r['from_px']} px" + (f", under the {r['min_px']} px minimum, so it was scaled up to {r['px']} px" if r.get("scaled") else f", over the {r['min_px']} px minimum, so it is sent as it is"))
                except Exception:
                    refs, clause = [], None
            items.append({"prompt": f"{base}: {s['key'].replace('_', ' ')}, {said}", "subject": subj["name"] if subj else base, "parent": gen, "regen_of": sid, "refs": refs, "ref_clause": clause,
                          "plan": compact_plan(sp), "style_id": sp["slots"].get("style_id") or st["style_id"], "note": f"{sid.split('/')[1]} redone: {said}", "label": sid.split("/")[1], **self._edge(gen)})
        if not items:
            t.reply = "I couldn't find those stickers in this batch."
            return {}
        t.trace.retitle("editing " + ", ".join(i["label"] for i in items))
        t.trace.step("same prompt, one change", {"title": f"from {self._nm(t.sess, gen)}", "lines": [i["prompt"] for i in items] + notes})
        if t.res.references:
            t.trace.note("using " + ", ".join(r["source"].split("/")[1] for r in t.res.references) + " as the reference")
        est = self.tools.estimate("image")
        total = (est or 0) * len(items)
        spec = {"type": "batch", "items": items, "style_id": st["style_id"], "ai": True, "estimate": total}
        more = f" I did the first 4 of {len(targets)}; ask again for the rest." if len(targets) > 4 else ""
        say = f"I'll redo {', '.join(i['label'] for i in items)} ({said}) from the same prompt; the rest of the batch stays." + (" " + "; ".join(notes) + "." if notes else "") + more
        if self.tools.live() and st.get("ask_before_spending", True):
            t.sess["pending"] = spec
            t.reply = say + (f" That costs {_credits(total)}." if total else "") + " Go ahead?"
            t.chips = [{"label": "Do it", "action": "confirm"}, {"label": "Not yet", "action": "cancel"}]
            t.trace.end(f"ready · {_credits(total)}")
        else:
            t.sess["pending"] = spec
            t.reply = say
            t.trace.step("confirmed: edit")
            self.n_confirm({"turn": _with_pending(t, spec)})
        return {}

    def n_undo(self, state: State) -> dict:
        """"undo" / "revert": the last REFINEMENT of this chat is taken back, the simple way. A plan still waiting for the go-ahead is dropped (nothing was spent); otherwise the newest batch the chat made FROM
        another one (an edit, a refinement, a redesign, another pass) is marked undone and the focus returns to the batch it came from, so "it" / "him" / "number 3" mean the earlier version again.
        Nothing is deleted or un-paid: the newer batch stays in History, and a job still drawing is not stopped. Feedback ("I hate 4") and approvals are not refinements and are not undone here."""
        t: Turn = state["turn"]
        sess = t.sess
        if sess.get("pending"):
            what = t.prev_pending or sess["pending"]
            sess["pending"] = None
            names = ", ".join(i.get("label") or i.get("subject") or "" for i in (what or {}).get("items") or []) if (what or {}).get("type") == "batch" else str((what or {}).get("subject") or (what or {}).get("label") or "")
            t.reply = f"Undone: I dropped the plan{(' for **' + names + '**') if names.strip(', ') else ''}. Nothing was spent."
            t.trace.end("plan dropped, nothing spent")
            return {}
        last = None
        for subj in sess["subjects"]:
            for p in subj["passes"]:
                if p.get("parent") and not p.get("undone") and (last is None or p["created"] >= last[1]["created"]):
                    last = (subj, p)
        if not last:
            t.reply = "There is nothing to undo yet: I have not changed anything in this chat. Tell me what to change, and I can take it back."
            t.trace.end("nothing to undo")
            return {}
        subj, p = last
        parent = p["parent"]
        p["undone"] = True
        sess["focus"] = {"generation": parent, "stickers": []}
        t.generation = parent
        again = (f"the new batch ({p['generation']}) stays in History" if p.get("generation") else "the new batch is still being drawn and cannot be stopped; it will stay in History")
        t.reply = f"Undone. We are back on **{self._nm(sess, parent)}**, the version before your last change ({parent}); {again}, nothing is deleted. Say what you want changed on this one."
        t.cards.append({"type": "generation", "generation": parent, "job": None, "subject": subj["name"], "note": "back to this version"})
        t.trace.step(f"back to {parent}")
        t.trace.end("done")
        return {}

    def _key_of(self, gen: str, sid: str) -> str:
        try:
            s = next(x for x in self.tools.generation(gen)["stickers"] if x["id"] == sid)
            return s["key"].replace("_", " ")
        except Exception:
            return sid.split("/")[1]
