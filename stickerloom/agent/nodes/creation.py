"""Creation intent handlers. State is owned by Agent; no module state."""
from __future__ import annotations
import re
import time
from .. import creator, stages, manual_batches
from .. import editroute, refine, subjects
from ..resolver import request_text
from ..tools import ToolError
from ..lifecycle.turn import Turn, State
from .shared import SUGGESTIONS, STYLE_NAMES, _credits, _edge_kw, compact_plan

class CreationNodes:
    def _prefs(self, t: Turn, subject_text: str) -> tuple[str, list]:
        """The user's memory as plan wording, and what it contributed (shown as steps and notes)."""
        sess, bits, notes = t.sess, [], []
        for trait in self.store.traits(sess):
            bits.append(trait)
            notes.append(f"you consistently asked for {trait}")
        for p in sess["preferences"]["persistent"]:
            bits.append(p)
        avoid = []
        for f in self.store.consume_temporary(sess):
            if f["polarity"] == "NEGATIVE":
                avoid += f["sticker_ids"]
        avoid = list(dict.fromkeys(avoid))
        if avoid:                                                      # the poses themselves go into the plan, not just a note: "avoid the poses of banana squashed, banana flat"
            bits.append("avoid the poses of " + ", ".join(dict.fromkeys(self._key_of(x.split("/")[0], x) for x in avoid[:4])))
        labels = [x.split("/")[1] for x in avoid]
        return (", ".join(dict.fromkeys(bits)), notes + ([f"you disliked {', '.join(labels)} last time, so those poses change"] if avoid else []))

    def n_new(self, state: State) -> dict:
        t: Turn = state["turn"]
        sess, st = t.sess, t.sess["settings"]
        asked = request_text(t.text)                    # "hello from noor, make me a camel in lamborgini" -> "make me a camel in lamborgini": the speaker's words go, every content word stays
        guess = re.sub(r"^(?:please\s+)?(?:can you\s+)?(?:make|create|generate|give|draw|design|build)\s+(?:me\s+)?(?:some\s+|a\s+|an\s+)?|\bstickers?\b|\bpack of\b|\bset of\b",
                       " ", asked, flags=re.I).strip(" .,!?") or asked
        t.trace.retitle(f"generating {guess}")
        sid, said_text, assumed = self._style_for(t, asked)
        prefs, notes = self._prefs(t, guess)
        notes = list(notes) + assumed
        prompt = said_text.strip() + (f". {prefs[0].upper() + prefs[1:]}" if prefs else "")
        eng = self.tools.engine_label(bool(st.get("ai", True)))
        t.trace.step(f"writing {int(st['grid'][0]) * int(st['grid'][-1])} sticker ideas" + (f" with {eng}" if eng else " from the built-in sets"))   # shown at once: the model can take a few seconds
        try:
            plan = self.tools.plan(prompt, st["grid"], sid, bool(st.get("ai", True)), st.get("actions", "predefined"))
        except ToolError as e:
            t.reply = f"I couldn't turn that into a plan: {e}"
            t.trace.end("could not plan", ok=False)
            t.chips = [{"label": s, "text": s} for s in SUGGESTIONS[:3]]
            return {}
        scene = str(plan.get("scene") or "")
        names = [n + (f" {scene}" if scene and scene.split()[-1].lower() not in n.lower() else "")       # the card says what every cell is drawn with: "camel waving in Lamborghini"
                 for n in (s["key"].replace("_", " ") for s in plan["stickers"])]
        t.trace.step("expand prompt", {"title": f"{len(names)} stickers", "lines": names})
        if plan.get("expand_error"):                # the AI could not write the ideas (not reachable, bad answer): say so, the built-in sets were used instead
            t.trace.note("the AI could not write the ideas (" + str(plan["expand_error"])[:140] + "): I used the built-in sets")
        if prefs:
            t.trace.step("added your preferences", {"lines": [p.strip() for p in prefs.split(",") if p.strip()]})
        for n in notes:
            t.trace.note(n)
        tr = plan.get("transformation")
        if tr:                                    # "dog as banana": say that it is ONE new character, and which cells the template guarantees
            t.trace.note(f"a transformation: the whole character is a {tr['target']} with the {tr['subject']}'s face"
                         + (f"; {', '.join(tr['required'])} included" if tr.get("required") else "")
                         + (f"; left out as you asked: {', '.join(tr['forbidden'])}" if tr.get("forbidden") else ""))
        subject = (plan.get("described") if plan.get("scene") else None) or plan.get("subject") or guess         # the normalised request ("camel in Lamborghini"), never the bare character
        t.res.generation = None
        pm = self.tools.prepared_match(prompt, st["grid"]) if self.tools.live() else None
        est = 0 if pm else (self.tools.estimate("image") if self.tools.live() else None)
        card = {"type": "plan", "subject": subject, "grid": st["grid"], "style": STYLE_NAMES.get(sid, sid),
                "count": len(names), "names": names, "estimate": est, "balance": self.tools.credits(), "prompt": prompt,
                "prepared": bool(pm),
                "free": not self.tools.live(), **({"transformation": {k: tr[k] for k in ("id", "subject", "target", "required")}} if tr else {})}
        spend = self.tools.live()
        eff, mis = stages.resolve(stages.of(st), t.text)                              # how far this request goes: the slider, or the message when it is Auto
        if mis and not t.stage_force:
            return self._stage_ask(t, mis)
        run = stages.run_spec(eff, creator.settings_of(sess)["bypass"])
        card["stage"] = run["stage"]
        batch = self._first_batch(prompt, subject, st["grid"], sid, bool(st.get("ai", True)), plan, card)       # a pack request plans batch 01 only (plan.md Step 3)
        if run["plan_only"]:
            sess["pack"] = {**batch["pack"], "made": []}
            return self._prompt_plan(t, subject, prompt, sid, plan, est, card, batch)
        if run["creator"]:
            return self._creator_plan(t, subject, prompt, names, est, card, {"on": True, "scope": run["scope"], "bypass": run["bypass"]}, end=run["end"], style_id=sid,
                                      plan=compact_plan(plan), batch=batch)
        if spend and st.get("ask_before_spending", True):
            sess["pending"] = {"type": "create", "prompt": prompt, "subject": subject, "grid": st["grid"], "style_id": sid,
                               "ai": bool(st.get("ai", True)), "estimate": est, "plan": compact_plan(plan), "batch": batch}
            t.cards.append(card)
            t.reply = f"Here's the plan for **{subject}**: {len(names)} stickers, {STYLE_NAMES.get(st['style_id'], st['style_id'])}" + (" (I have this prepared: 0 credits)" if pm else "") + ". Shall I create it?"
            t.chips = ([{"label": "Create it", "action": "confirm"}] + ([{"label": "Make a new one (paid)", "action": "confirm_new"}] if pm else []) + [{"label": "Not yet", "action": "cancel"}])
            t.trace.end(f"plan ready · {_credits(est)}")
            return {}
        self._start_create(t, {"prompt": prompt, "subject": subject, "grid": st["grid"], "style_id": sid, "ai": bool(st.get("ai", True)), "plan": compact_plan(plan), "batch": batch}, card)
        return {}

    def _prompt_plan(self, t: Turn, subject: str, prompt: str, sid: str, plan: dict, est, card: dict, batch: dict | None = None) -> dict:
        """The Prompt stage: the plan (cells, tags, the sheet prompt) and Generate / Edit. Nothing is spent and nothing starts, even with "ask before
        spending" off: Generate is the go-ahead, and it makes the sheet."""
        sess, st = t.sess, t.sess["settings"]
        sess["pending"] = {"type": "create", "prompt": prompt, "subject": subject, "grid": st["grid"], "style_id": sid, "ai": bool(st.get("ai", True)), "estimate": est,
                           "plan": compact_plan(plan), "stage": "prompt", **({"batch": batch} if batch else {})}
        card["sheet_prompt"] = plan.get("sheet_prompt") or ""
        t.cards.append(card)
        t.reply = (f"Here's the prompt for **{subject}**: {card['count']} stickers, {STYLE_NAMES.get(sid, sid)}. Nothing is spent at the Prompt stage: "
                   f"press Generate to make the sheet ({_credits(est)}), or tell me what to change.")
        t.chips = [{"label": "Generate" + (f" · {est:g} credits" if est else ""), "action": "confirm"}, {"label": "Edit", "fill": prompt}]
        t.chips += [c for c in self._followup(sess)[1] if c.get("action") == "batch_more"]      # the follow-up comes right after the plan: "Batch 03" plans 01-03 together
        t.trace.end("prompt ready · nothing spent")
        return {}

    def _start_create(self, t: Turn, p: dict, plan_card: dict | None = None, parent: str | None = None, regen_of: str | None = None,
                      refs: list | None = None, note: str = "") -> bool:
        """Start a batch from an approved plan (`p["plan"]` is sent as it is: the card and the batch are the same). True when it started; False leaves the reply explaining why."""
        try:
            force_live = bool(p.get("force_live")) or (t.action or {}).get("type") == "confirm_new"      # manual batches must keep their exact approved cells
            r = self.tools.create(p["prompt"], p["grid"], p["style_id"], p.get("ai", True), parent=parent, regen_of=regen_of, refs=refs, base_plan=p.get("plan"), ref_clause=p.get("ref_clause"), **_edge_kw(p), force_live=force_live)
        except ToolError as e:
            t.reply = f"I couldn't start that: {e}"
            t.trace.end("not started", ok=False)
            return False
        self.store.add_pass(t.sess, p["subject"], generation=r.get("generation"), job=r.get("job"), prompt=p["prompt"], grid=p["grid"],
                            style_id=p["style_id"], parent=parent, note=note)
        self._track_batch(t, p, r)
        if r.get("generation"):
            t.generation = r["generation"]
            t.sess["focus"] = {"generation": r["generation"], "stickers": []}
        t.spent += r.get("estimate") or 0
        t.cards.append({"type": "generation", "generation": r.get("generation"), "job": r.get("job"), "subject": p["subject"],
                        "parent": parent, "note": note, **({"batch": self._batch_tag(t, p)} if self._batch_tag(t, p) else {})})
        t.reply = (t.reply + " " if t.reply else "") + (f"Creating **{p['subject']}** now" + (f" ({_credits(r.get('estimate'))})" if r.get("estimate") else "") + ". The stickers appear below as they are ready.")
        t.trace.end("started · " + (r.get("job") or r.get("generation") or ""))
        return True

    def n_confirm(self, state: State) -> dict:
        t: Turn = state["turn"]
        p = t.sess.get("pending")
        if not p:
            t.reply = "There is nothing waiting for a go-ahead. What would you like to make?"
            t.chips = [{"label": s, "text": s} for s in SUGGESTIONS[:3]]
            return {}
        t.trace.step(f"confirmed: {p['type']}")
        if p["type"] == "emoji_batch":
            return manual_batches.handle(self, t)
        if p["type"] == "multi":
            t.trace.retitle(f"generating {p.get('label') or 'several packs'}")
            done, failed = self._start_items(t, p["items"])
            if failed:                                       # what could not start stays waiting; what started is not asked again
                t.sess["pending"] = {**p, "items": [i for i, _ in failed], "estimate": None}
                t.chips = [{"label": "Try the rest", "action": "confirm"}, {"label": "Not yet", "action": "cancel"}]
            else:
                t.sess["pending"] = None
            return {}
        if p["type"] == "batch_more":
            t.trace.retitle("generating " + (p.get("label") or p.get("subject") or "the batches"))
            done, failed = self._run_batch_items(t, p["items"], p.get("stage") or "emojis")
            if failed:                                       # what could not start stays waiting; what started is not asked again
                t.sess["pending"] = {**p, "items": [i for i, _ in failed], "estimate": None}
                t.chips = [{"label": "Try the rest", "action": "confirm"}, {"label": "Not yet", "action": "cancel"}]
            else:
                t.sess["pending"] = None
            return {}
        if p["type"] == "create":
            t.trace.retitle(f"generating {p['subject']}")
            if self._start_create(t, p, parent=p.get("parent"), regen_of=p.get("regen_of"), refs=p.get("refs"), note=p.get("note", "")):
                t.sess["pending"] = None
            else:                                           # a refused start must not cost the plan: the person presses the same button again
                t.reply += " I kept your plan: press Create it to try again, or Not yet to drop it."
                t.chips = [{"label": "Create it", "action": "confirm"}, {"label": "Not yet", "action": "cancel"}]
            return {}
        t.sess["pending"] = None
        if p["type"] == "review":
            return self._do_review(t, p["generation"], p["decision"], p["indexes"])
        elif p["type"] == "creator":
            t.trace.retitle(f"creating {p['subject']} for Telegram")
            self._start_creator(t, p)
        elif p["type"] == "animate":
            t.trace.retitle(f"animating {self._nm(t.sess, p['generation'])}")
            try:
                r = self.tools.animate(p["generation"], p.get("loop", False))
                t.generation = p["generation"]
                t.cards.append({"type": "generation", "generation": p["generation"], "job": r["job"], "subject": p.get("subject", ""), "animating": True})
                t.reply = f"Animating **{self._nm(t.sess, p['generation'])}**" + (f" ({_credits(r.get('estimate'))})" if r.get("estimate") else "") + ". I'll show each one as it's ready." \
                    + (" Once they're moving, say \"export\" and I'll pack and send them." if p.get("then") == "pack_send" else "")
                t.trace.end("animation started")
            except ToolError as e:
                t.reply = f"I couldn't start the animation: {e}"
                t.trace.end("not started", ok=False)
        elif p["type"] == "pack_send":
            t.trace.retitle(f"sending {self._nm(t.sess, p['generation'])} to Telegram")
            try:
                pack = self.tools.pack_add(p["generation"], p.get("subject") or self._nm(t.sess, p["generation"]))
                rep = self.tools.telegram_send(pack["pack_id"])
            except ToolError as e:
                t.sess["pending"] = p
                t.reply = f"I couldn't send it: {e} Nothing was packed."
                t.chips = [{"label": "Pack and send", "action": "confirm"}, {"label": "Not yet", "action": "cancel"}]
                t.trace.end("not sent", ok=False)
                return {}
            links = " ".join(s["link"] for s in rep.get("sets", []))
            t.reply = f"Done: **{self._nm(t.sess, p['generation'])}** is on Telegram. {links}".strip()
            out = getattr(self.tools, "out", None)                               # WP9: what was sent is also left as files
            has_lib = getattr(getattr(self.tools, "c", None), "lib", None) is not None
            if out is not None and has_lib:
                try:
                    from ...media import pack_exchange as _px
                    exported = _px.export_pack(out, pack["pack_id"])
                    t.reply += f" Exported files: {exported['dir']}."
                except Exception:
                    pass                                                         # the send stands; a failed file copy is not an error
            t.trace.end("sent")
        elif p["type"] == "names":                          # the same consent, asked because the person wanted names looked at
            if self._ready_now(p["generation"]) == []:      # nothing cut yet: no vision call, one calm sentence, the pending goes
                t.reply = f"{self._nm(t.sess, p['generation'])} has no finished stickers yet — I'll look at them once they're cut."
                t.trace.end("nothing to look at yet")
                return {}
            t.sess["settings"]["allow_vlm"] = True
            t.trace.retitle(f"looking at {self._nm(t.sess, p['generation'])}")
            self._run_names(t, p["generation"])
        elif p["type"] == "describe":                       # "Allow AI vision of generated media?" answered yes: asked once, remembered in the session
            if self._ready_now(p["generation"]) == []:
                t.reply = f"{self._nm(t.sess, p['generation'])} has no finished stickers yet — I'll look at them once they're cut."
                t.trace.end("nothing to look at yet")
                return {}
            t.sess["settings"]["allow_vlm"] = True
            t.trace.retitle(f"looking at {self._nm(t.sess, p['generation'])}")
            self._run_describe(t, p["generation"], p.get("only") or [], question=p.get("question"))
        elif p["type"] == "particles":
            self._do_particles(t, p)
        elif p["type"] == "batch":
            t.trace.retitle("regenerating " + ", ".join(i["label"] for i in p["items"]))
            n = 0
            for it in p["items"]:
                self._start_create(t, {**it, "grid": "1x1", "style_id": it.get("style_id") or p["style_id"], "ai": p.get("ai", True)}, parent=it["parent"],
                                   regen_of=it["regen_of"], refs=it.get("refs"), note=it["note"])
                n += 1
            t.reply = f"Regenerating {n} sticker{'s' if n != 1 else ''}; the other stickers stay as they are."
        return {}

    def n_cancel(self, state: State) -> dict:
        t: Turn = state["turn"]
        p = t.sess.get("pending")
        t.sess["pending"] = None
        if p and p.get("type") == "describe":
            t.sess["settings"]["allow_vlm"] = False
            t.trace.step("AI vision stays off")
            t.reply = "No problem, I won't send your stickers to a vision model. Say \"allow AI vision\" if you change your mind."
            return {}
        t.trace.step("cancelled, nothing was spent")
        t.reply = "No problem, nothing was spent. Tell me what to change, or what else to make."
        t.chips = [{"label": s, "text": s} for s in SUGGESTIONS[:3]]
        return {}

    def _focus_pass(self, t: Turn) -> tuple[dict | None, dict | None]:
        gen = t.res.generation or (t.sess.get("focus") or {}).get("generation") or (self.store.latest_pass(t.sess) or {}).get("generation")
        subj = self.store.subject_for_generation(t.sess, gen) if gen else None
        p = next((q for q in subj["passes"] if q.get("generation") == gen), None) if subj else None
        return subj, p

    def _subject_names(self, sess: dict) -> list[str]:
        return [s["name"] for s in sess["subjects"] if any(p.get("generation") for p in s["passes"])]

    def _has_sticker_ref(self, t: Turn) -> bool:
        from ..resolver import _numbers
        if t.selected or _numbers(t.text, 9) or re.search(r"\bg\d+\s*/?\s*s\d", t.text.lower()):
            return True
        return bool((t.sess.get("focus") or {}).get("stickers") and re.search(r"\b(it|that|this)\b", t.text.lower()))

    def _wants_refine(self, t: Turn, has_gen: bool) -> bool:
        """A change to a whole subject, not to numbered stickers: it names a subject of this chat and says what to change ("the cherries were so realistic, make them cartoonish"), or it is an
        edit with no sticker pointed at ("make him bigger", "same but red"). "make me a banana in clay style" is a new request and stays one."""
        if not has_gen or t.selected or re.search(r"\b(?:make|create|generate|give|draw|design)\s+me\b|\b(?:a|an|some|\d+)\s+(?:\w+\s+)?(?:stickers?|packs?|sets?)\b", t.text.lower()):
            return False
        segs = refine.mentions(t.text, self._subject_names(t.sess))
        if segs and any(refine.extract(c)["matched"] for _, c in segs):
            return True
        return t.intents[0] == "EDIT_STICKERS" and not self._has_sticker_ref(t) and refine.extract(t.text)["matched"]

    def _style_for(self, t: Turn, text: str) -> tuple[str, str, list]:
        """(style id, the text without style words, what was assumed): the style named in the sentence wins; else what this person has asked for twice or more (said on the card, one click to
        undo); else the chat's setting."""
        from ...generation import styles as _styles
        st = t.sess["settings"]
        sid, rest = refine.style_of_request(text)
        if sid:
            return sid, rest, []
        if st.get("style_id") != _styles.DEFAULT:
            return st["style_id"], text, []
        d = self._profile().defaults().get("style_id")
        if d:                                                          # a remembered taste is offered, never applied unasked
            return st["style_id"], text, [f"you asked for {refine.LABEL.get(d[0], d[0])} {d[1]} times before: say \"{refine.LABEL.get(d[0], d[0]).lower()}\" to use it here"]
        return st["style_id"], text, []

    def _items_card(self, title: str, items: list, st: dict, est_each, extra: dict | None = None) -> dict:
        total = round(est_each * len(items), 2) if est_each else None
        return {"type": "multi", "title": title, "grid": st["grid"], "estimate": total, "balance": self.tools.credits(), "free": not self.tools.live(),
                "items": [{"subject": i["subject"], "count": len(i["plan"].get("stickers") or []), "names": [s["key"].replace("_", " ") for s in (i["plan"].get("stickers") or [])][:9],
                           "style": STYLE_NAMES.get(i["style_id"], i["style_id"]), "changes": i.get("changes") or []} for i in items], **(extra or {})}

    def _offer_items(self, t: Turn, label: str, items: list, est_each, card: dict, reply: str) -> dict:
        """The plan card of several batches: ONE total price and one go-ahead (or, with 'Ask before spending' off or without a provider, they start at once)."""
        sess, st = t.sess, t.sess["settings"]
        t.cards.append(card)
        if self.tools.live() and st.get("ask_before_spending", True):
            sess["pending"] = {"type": "multi", "items": items, "estimate": card["estimate"], "subject": label, "label": label}
            t.reply = reply + " Shall I create them?"
            t.chips = [{"label": "Create them", "action": "confirm"}, {"label": "Not yet", "action": "cancel"}]
            t.trace.end(f"plan ready · {_credits(card['estimate'])}")
            return {}
        self._start_items(t, items)
        return {}

    def _start_items(self, t: Turn, items: list) -> tuple[list, list]:
        """Start every batch of the plan together (the provider jobs run side by side, `jobs.paid_parallel`). Returns (started, [(item, why)] that could not start)."""
        done, failed = [], []
        for it in items:
            try:
                r = self.tools.create(it["prompt"], it["grid"], it["style_id"], it.get("ai", True), parent=it.get("parent"), regen_of=it.get("regen_of"), base_plan=it.get("plan"), refs=it.get("refs"), ref_clause=it.get("ref_clause"), **_edge_kw(it))
            except ToolError as e:
                failed.append((it, str(e)))
                continue
            self.store.add_pass(t.sess, it["subject"], generation=r.get("generation"), job=r.get("job"), prompt=it["prompt"], grid=it["grid"], style_id=it["style_id"],
                                parent=it.get("parent"), note=it.get("note", ""))
            self._track_batch(t, it, r)
            if r.get("generation"):
                t.sess["focus"] = {"generation": r["generation"], "stickers": []}
            t.spent += r.get("estimate") or 0
            t.cards.append({"type": "generation", "generation": r.get("generation"), "job": r.get("job"), "subject": it["subject"], "parent": it.get("parent"), "note": it.get("note", ""),
                            **({"batch": it["batch"]["label"]} if it.get("batch") else {})})
            if it.get("delta"):                                          # what the person asked for, counted as a taste only now that it is really made
                try:
                    self._profile().vote_delta(it["delta"], it.get("note", ""))
                except Exception:
                    pass
            done.append(it)
        names = ", ".join(f"**{i['subject']}**" for i in done)
        if done:
            t.reply = (t.reply + " " if t.reply else "") + f"Creating {names}" + (f" ({_credits(t.spent)})" if t.spent else "") + ". Each one appears below as it is ready."
        if failed:
            t.reply = (t.reply + " " if t.reply else "") + "I could not start " + ", ".join(f"**{i['subject']}**" for i, _ in failed) + f": {failed[0][1]}."
        t.trace.end(f"started {len(done)}" + (f", {len(failed)} refused" if failed else ""), ok=bool(done))
        return done, failed

    def n_multi(self, state: State) -> dict:
        """"Create three sticker packs of fruits": the chat picks three different subjects of the category, plans each, and asks once for the total."""
        t: Turn = state["turn"]
        sess, st = t.sess, t.sess["settings"]
        got = subjects.parse_multi(t.text)
        if not got:
            t.reply = "How many packs, and of what? For example \"three sticker packs of fruits\"."
            return {}
        n, cat = got
        t.trace.retitle(f"planning {n} packs of {cat}")
        avoid = [s["name"] for s in sess["subjects"]]
        names, how = subjects.pick(cat, n, avoid, ask=self.brain.pick_subjects if self.brain.available else None, seed=int(time.time() * 1000) % 1000000007)
        if not names:
            t.reply = f"I have no list for \"{cat}\" yet. Tell me the {n} subjects, for example \"strawberry, cherries, banana\"."
            t.trace.end("asked for the subjects")
            return {}
        t.trace.step(f"chose {len(names)} {cat}" + (" with the language model" if how.startswith("model") else " from my built-in list"), {"title": ", ".join(names), "lines": names})
        if len(names) < n:
            t.trace.note(f"I could only find {len(names)} different ones")
        sid, _, assumed = self._style_for(t, t.text)
        for a in assumed:
            t.trace.note(a)
        items = []
        for name in names:
            try:
                plan = self.tools.plan(name, st["grid"], sid, bool(st.get("ai", True)), st.get("actions", "predefined"))
            except ToolError as e:
                t.trace.note(f"I could not plan {name}: {e}")
                continue
            items.append({"prompt": name, "subject": plan.get("subject") or name, "grid": st["grid"], "style_id": sid, "ai": bool(st.get("ai", True)), "plan": compact_plan(plan),
                          "note": f"one of {len(names)} packs of {cat}"})
        if not items:
            t.reply = "I couldn't plan any of them."
            t.trace.end("could not plan", ok=False)
            return {}
        est = self.tools.estimate("image") if self.tools.live() else None
        title = f"{len(items)} packs of {cat}"
        card = self._items_card(title, items, st, est, {"assumed": assumed})
        reply = f"Here's the plan for {title}: " + ", ".join(f"**{i['subject']}**" for i in items) + f", {len(items) * (len(items[0]['plan'].get('stickers') or []))} stickers in all, {STYLE_NAMES.get(sid, sid)}."
        return self._offer_items(t, title, items, est, card, reply)

    def n_refine(self, state: State) -> dict:
        """"The cherries were so realistic, make them more cartoonish; the banana was so small, make it bigger": each subject's stored plan gets its change written in, and a new sheet is made as a
        child of the old batch (the old one stays). One card, one total price, one go-ahead."""
        t: Turn = state["turn"]
        sess, st = t.sess, t.sess["settings"]
        names = self._subject_names(sess)
        segs = refine.mentions(t.text, names)
        if not segs:
            subj, p = self._focus_pass(t)
            segs = [(subj["name"], t.text)] if subj and p else []
        if not segs:
            t.reply = "Which pack should I change? Name it, for example \"make the cherries more cartoonish\"."
            return {}
        t.trace.retitle("changing " + ", ".join(n for n, _ in segs))
        items, skipped = [], []
        for name, clause in segs:
            delta = refine.extract(clause)
            if not delta["matched"] and self.brain.available:
                m = self.brain.refine_delta(clause, name) or {}
                from ...generation import styles as _styles
                sid = m.get("style_id") if m.get("style_id") in {p["id"] for p in _styles.PRESETS} else None
                size = m.get("size") if m.get("size") in ("larger", "smaller") else None
                colour = re.sub(r"[^a-z ]", "", str(m.get("colour") or "").lower())[:20] or None
                delta.update(style_id=sid, size=size, colour=colour, matched=bool(sid or size or colour))
            subj = next((s for s in sess["subjects"] if s["name"] == name), None)
            p = next((q for q in reversed(subj["passes"]) if q.get("generation")), None) if subj else None
            if not delta["matched"] or not p:
                skipped.append(name)
                continue
            try:
                base = self.tools.generation_plan(p["generation"])
            except ToolError:
                skipped.append(name)
                continue
            new_plan = refine.apply(base, delta, clause, name)
            changes = refine.describe(delta)
            sid = new_plan["slots"].get("style_id") or st["style_id"]
            t.trace.step(f"{name}: " + ", ".join(changes), {"title": f"from {self._nm(sess, p['generation'])}", "lines": [clause]})
            items.append({"prompt": p["prompt"], "subject": name, "grid": p.get("grid") or st["grid"], "style_id": sid, "ai": True, "plan": compact_plan(new_plan), "parent": p["generation"],
                          "note": "changed: " + ", ".join(changes), "changes": changes, "delta": {k: delta.get(k) for k in ("style_id", "size", "colour")}})
        if not items:
            t.reply = "I could not tell what to change" + (f" about {', '.join(skipped)}" if skipped else "") + ". Tell me the style (\"more cartoonish\"), the size (\"bigger\") or a colour (\"more red\")."
            t.trace.end("asked what to change")
            return {}
        est = self.tools.estimate("image") if self.tools.live() else None
        title = "changes to " + " and ".join(i["subject"] for i in items)
        card = self._items_card(title, items, st, est, {"refine": True})
        reply = "Here's what I will change: " + "; ".join(f"**{i['subject']}**: {', '.join(i['changes'])}" for i in items) + ". Each is a new sheet from the same prompt with just that change; the originals stay."
        if skipped:
            reply += f" (I did not understand what to change about {', '.join(skipped)}.)"
        return self._offer_items(t, title, items, est, card, reply)

    def n_another(self, state: State) -> dict:
        t: Turn = state["turn"]
        subj, p = self._focus_pass(t)
        if not p:
            t.reply = "Which subject would you like another set of? Tell me what to make."
            return {}
        st = t.sess["settings"]
        t.trace.retitle(f"another pass of {subj['name']}")
        prefs, notes = self._prefs(t, subj["name"])
        prompt = p["prompt"] + (f". {prefs[0].upper() + prefs[1:]}" if prefs else "") + ". A different set of poses and expressions than before"
        for n in notes:
            t.trace.note(n)
        spec = {"prompt": prompt, "subject": subj["name"], "grid": p.get("grid") or st["grid"], "style_id": p.get("style_id") or st["style_id"], "ai": True}
        if not self.tools.live():
            try:
                r = self.tools.more(p["generation"])
                self.store.add_pass(t.sess, subj["name"], generation=r["generation"], prompt=p["prompt"], grid=spec["grid"],
                                    style_id=spec["style_id"], parent=p["generation"], note="another variation")
                t.generation = r["generation"]
                t.sess["focus"] = {"generation": r["generation"], "stickers": []}
                t.cards.append({"type": "generation", "generation": r["generation"], "job": None, "subject": subj["name"], "parent": p["generation"]})
                t.reply = f"Here is another take on **{subj['name']}**."
                t.trace.end("done")
            except ToolError as e:
                t.reply = str(e)
                t.trace.end("no more variations", ok=False)
            return {}
        est = self.tools.estimate("image")
        spec.update(type="create", estimate=est, parent=p["generation"], note=f"another pass of {self._nm(t.sess, p['generation'])}")
        if t.sess["settings"].get("ask_before_spending", True):
            t.sess["pending"] = spec
            t.reply = f"I'll make another pass of **{subj['name']}** with different poses ({_credits(est)}). Go ahead?"
            t.chips = [{"label": "Create it", "action": "confirm"}, {"label": "Not yet", "action": "cancel"}]
            t.trace.end(f"ready · {_credits(est)}")
        else:
            self._start_create(t, spec, parent=p["generation"], note=spec["note"])
        return {}

    def n_unsupported(self, state: State) -> dict:
        t: Turn = state["turn"]
        t.reply = editroute.unsupported(t.text) or "I can't do that yet."
        t.trace.end("not supported yet")
        return {}
