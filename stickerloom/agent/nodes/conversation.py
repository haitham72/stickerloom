"""Conversation intent handlers. State is owned by Agent; no module state."""
from __future__ import annotations
import re
from ...runtime import cache as cachemod
from .. import creator
from ..profile import detect_tone
from ..resolver import DESCRIBE, NAME_QUESTION, display_request, settings_from, smalltalk_kind, profile_facts
from ..profile import profile_said
from ..tools import ToolError
from ..lifecycle.turn import Turn, State, Trace
from .shared import CREATION_INTENTS, SUGGESTIONS, TONE_ACK, STYLE_NAMES

class ConversationNodes:
    VISION_ASK = ("One more thing, once: may I look at your stickers with a vision model (the local one when LM Studio is running, otherwise the cloud one)? "
                      "I would check each picture, describe it, and suggest a better name when the current one does not fit. Nothing is sent until you say yes.")

    def n_ask(self, state: State) -> dict:
        t: Turn = state["turn"]
        r = t.res
        low = t.text.lower()
        if display_request(t.text):
            gen = r.generation
            if not gen:
                t.reply = "Which batch should I show?"
                t.trace.end("asked which batch")
                return {}
            hits = self.tools.generation(gen)["stickers"]
            t.cards.append({"type": "stickers", "stickers": hits})
            t.reply = f"Here are all {len(hits)} stickers from **{self._nm(t.sess, gen)}**."
            t.generation = gen
            t.trace.end("showed the whole batch")
            return {}
        if re.search(DESCRIBE, low):
            return self._describe(t)
        t.trace.retitle("looking it up")
        if r.stickers and re.search(r"\b(which|what|where|who)\b", low):
            gen = r.generation
            card = self.tools.generation(gen)
            hits = [s for s in card["stickers"] if s["id"] in r.stickers]
            t.reply = "; ".join(f"{s['id']}: {s['key'].replace('_', ' ')} {''.join(s['emoji']) if isinstance(s.get('emoji'), list) else s.get('emoji') or ''}".strip() for s in hits)
            t.cards.append({"type": "stickers", "stickers": hits})
            t.sess["focus"] = {"generation": gen, "stickers": [s["id"] for s in hits][:1]}
            t.trace.end("answered from what we made")
            return {}
        if re.search(r"\bhow many\b", low):
            self.store.refresh(t.sess)
            total = sum(p["ready"] for s in t.sess["subjects"] for p in s["passes"])
            appr = sum(p["approved"] for s in t.sess["subjects"] for p in s["passes"])
            t.reply = f"{total} stickers made in this chat, {appr} approved."
            t.trace.end("counted")
            return {}
        prof = self._profile()
        who = prof.name()
        if re.search(NAME_QUESTION, low):                               # "what is my name?": what the person told me (their profile), never a guess from the stickers
            if re.search(r"\babout me\b", low):
                known = prof.facts_text()
                t.reply = (known.replace("The person: ", "What you told me: ") if known else "Nothing yet.") + " Tell me anything else you'd like me to remember."
            elif who:
                t.reply = f"You're {who}."
            else:                                                       # one honest question, and the answer that follows sticks (n_understand reads it as the name)
                t.reply = "I don't know your name yet. What should I call you?"
                t.sess["awaiting"] = {"profile": "name", "text": t.text}
            t.trace.end("answered from what you told me")
            return {}
        facts = ((prof.facts_text() + "\n") if prof.facts_text() else "") + self.store.summary_text(t.sess)
        ans = self.brain.answer(t.text, facts) if self.brain.available else None
        t.reply = ans or facts
        t.trace.end("answered" if ans else "here is what we have")
        return {}

    def _describe(self, t: Turn) -> dict:
        """"Describe the stickers": the person's yes to AI vision comes first, once per chat (`settings.allow_vlm`: None = not asked, True, False)."""
        gen = t.res.generation or (t.sess.get("focus") or {}).get("generation") or (self.store.latest_pass(t.sess) or {}).get("generation")
        if not gen:
            t.reply = "Which batch should I look at? Tell me its name or its number (like G012)."
            return {}
        only = [int(x.split("/S")[1]) for x in t.res.stickers if x.startswith(gen + "/S")]
        allow = t.sess["settings"].get("allow_vlm")
        t.trace.retitle(f"looking at {self._nm(t.sess, gen)}")
        if allow is False:
            t.reply = "AI vision is off for this chat, so I won't send the pictures to a model. Say \"allow AI vision\" and ask again."
            t.trace.end("AI vision is off", ok=False)
            return {}
        if allow is None:
            t.sess["pending"] = {"type": "describe", "generation": gen, "only": only, "question": t.text}
            t.reply = (f"To describe **{self._nm(t.sess, gen)}** I send its pictures to the vision model (the local one when LM Studio is running, otherwise the cloud one). "
                       "Allow AI vision of generated media? I only ask once.")
            t.chips = [{"label": "Allow AI vision", "action": "confirm"}, {"label": "Not now", "action": "cancel"}]
            t.trace.end("waiting for your yes")
            return {}
        self._run_describe(t, gen, only)
        return {}

    def _run_describe(self, t: Turn, gen: str, only: list, question: str | None = None) -> None:
        card = self.tools.generation(gen)
        hits = [s for s in card["stickers"] if not only or s["index"] in only]
        t.cards.append({"type": "stickers", "stickers": hits})
        t.msg.update(cards=t.cards, cards_first=True)
        self.store.save(t.sess)                      # the selected sticker reaches SSE before any vision call
        if only:
            t.trace.step("looking at the selected still image")
            parts = []
            failed = False
            for item in hits:
                prefix = ("\n\n".join(parts) + "\n\n" if parts else "") + f"**S{item['index']}**: "
                current = ""

                def delta(text):
                    nonlocal current
                    current += text
                    t.msg["text"] = prefix + current
                    self.store.save(t.sess)

                try:
                    answer = self.tools.describe(gen, item["index"], question or t.text, allowed=True, on_delta=delta)
                except ToolError as e:
                    failed = True
                    answer = f"I couldn't inspect this still: {e}"
                parts.append(f"**S{item['index']}**: {answer}")
            t.reply = "\n\n".join(parts) or "That sticker has no finished still yet."
            t.sess["focus"] = {"generation": gen, "stickers": [s["id"] for s in hits][:3]}
            t.res.generation, t.res.stickers = gen, [s["id"] for s in hits]
            t.generation = gen
            t.trace.end("could not inspect the still" if failed else "described the still", ok=not failed)
            return
        try:
            caps = self.tools.captions(gen, True)
        except ToolError as e:
            t.reply = str(e) if e.code != 404 else "I can't find that batch."
            t.trace.end("could not look", ok=False)
            return
        rows = [c for c in caps if not only or c["index"] in only]
        t.trace.step(f"asked the vision model about {len(rows)} sticker{'s' if len(rows) != 1 else ''}")
        t.reply = f"**{self._nm(t.sess, gen)}**\n" + "\n".join(f"S{c['index']}: {c['caption']}" if c.get("caption") else f"S{c['index']}: I couldn't read this one" for c in rows) or f"{self._nm(t.sess, gen)} has no finished stickers yet."
        t.sess["focus"] = {"generation": gen, "stickers": [f"{gen}/S{c['index']}" for c in rows][:3] if only else []}
        t.generation = gen
        t.trace.end("described")

    def n_search(self, state: State) -> dict:
        t: Turn = state["turn"]
        q = re.sub(r"\b(find|search|look for|do i have|show me|my|old|from before|sticker|stickers|a|the)\b", " ", t.text, flags=re.I).strip()
        q = re.sub(r"\s+", " ", q) or t.text
        t.trace.retitle(f"searching for {q}")
        hits = self.tools.search(q)
        t.trace.step(f"{len(hits)} match{'es' if len(hits) != 1 else ''}")
        if hits:
            t.cards.append({"type": "stickers", "stickers": hits[:9]})
            t.reply = f"Found {len(hits)} for \"{q}\"."
        else:
            t.reply = f"Nothing matches \"{q}\" yet. Want me to make it?"
            t.chips = [{"label": f"Make {q}", "text": f"make {q}"}]
        t.trace.end("done")
        return {}

    def n_settings(self, state: State) -> dict:
        t: Turn = state["turn"]
        try:
            from ...generation import prompter
            ids = list(prompter.STYLES)
        except Exception:
            ids = None
        new = settings_from(t.text, ids)
        if not new:
            if re.search(r"\bvision\b|\bvlm\b", t.text.lower()):
                t.sess["vision_asked"] = True                              # the one-time question is not asked again in this chat; the vision setting itself is untouched
                t.reply = ("Understood, I will not ask about AI vision again in this chat, and \"ask before spending\" is unchanged. AI vision stays as it is: say \"allow AI vision\" or \"don't use AI vision\" "
                           "when you decide. If you ask me to describe or rename stickers, I still need your yes first.")
                return {}
            t.reply = "Which setting? I can change the grid (2×2 or 3×3), the style, and whether I ask before spending."
            return {}
        t.sess["settings"].update(new)
        labels = {"grid": lambda v: f"{v} grid", "style_id": lambda v: STYLE_NAMES.get(v, v) + " style", "ask_before_spending": lambda v: "asking before I spend" if v else "no confirmation before spending",
                  "allow_vlm": lambda v: "AI vision allowed (I may send your stickers to the vision model)" if v else "AI vision off"}
        said = ", ".join(labels[k](v) for k, v in new.items() if k in labels)
        t.trace.task("changing settings")
        t.trace.step(said)
        t.trace.end("saved")
        t.reply = f"Done: {said}, from now on."
        return {}

    def n_retry(self, state: State) -> dict:
        """"Try the sheet again" on a batch whose sheet Python blocked (`flow/explain.py`). The button states the price, so the click is the go-ahead; the new sheet is a new batch
        with the same words, the blocked one stays as it is (nothing is deleted)."""
        t: Turn = state["turn"]
        gid = str((t.action or {}).get("generation") or "")
        try:
            card = self.tools.generation(gid)
        except ToolError as e:
            t.reply = str(e)
            return {}
        if not card.get("problem"):
            t.reply = "That sheet is fine, there is nothing to redo."
            return {}
        name = self._nm(t.sess, gid)
        grid = "x".join(str(x) for x in (card.get("grid") or [3, 3])) if isinstance(card.get("grid"), list) else (card.get("grid") or t.sess["settings"]["grid"])
        subj = self.store.subject_for_generation(t.sess, gid)
        p = next((q for q in (subj or {}).get("passes", []) if q.get("generation") == gid), {})
        t.trace.retitle(f"a new sheet for {name}")
        t.trace.step("the first sheet could not be cut: " + str(card["problem"].get("check") or ""))
        self._start_create(t, {"prompt": card.get("prompt") or name, "subject": (subj or {}).get("name") or name, "grid": grid,
                               "style_id": p.get("style_id") or t.sess["settings"]["style_id"], "ai": True},
                           note=f"a new sheet for {name}: the first could not be cut")
        run = t.sess.get("creator_run")
        new = next((c for c in reversed(t.cards) if c.get("type") == "generation"), None)
        if run and new and run.get("generation") == gid and run["status"] in ("stopped", "waiting"):        # the creator follows the new sheet
            run.update(job=new.get("job"), generation=new.get("generation"), step="sheet", status="running", stop=None, waiting=None, skip=[])
            creator._log(run, "a new sheet was started")
            t.cards.append({"type": "creator", "run_id": run["id"]})
        return {}

    def n_names(self, state: State) -> dict:
        t: Turn = state["turn"]
        gen = t.res.generation or (t.sess.get("focus") or {}).get("generation") or (self.store.latest_pass(t.sess) or {}).get("generation")
        if not gen:
            t.reply = "Which batch should I look at? Make some stickers first, or tell me its name."
            return {}
        allow = t.sess["settings"].get("allow_vlm")
        t.trace.retitle(f"looking at {self._nm(t.sess, gen)}")
        if allow is False:
            t.reply = "AI vision is off for this chat, so I won't send the pictures to a model. Say \"allow AI vision\" and ask again."
            t.trace.end("AI vision is off", ok=False)
            return {}
        if allow is None:
            t.sess["pending"] = {"type": "names", "generation": gen}
            t.reply = (f"To check the names of **{self._nm(t.sess, gen)}** I send its pictures to the vision model (the local one when LM Studio is running, otherwise the cloud one). "
                       "Allow AI vision of generated media? I only ask once.")
            t.chips = [{"label": "Allow AI vision", "action": "confirm"}, {"label": "Not now", "action": "cancel"}]
            t.trace.end("waiting for your yes")
            return {}
        self._run_names(t, gen)
        return {}

    def _run_names(self, t: Turn, gen: str) -> None:
        try:
            rows = self.tools.name_proposals(gen, self.brain.name_check if self.brain.available else (lambda items: None), allowed=True)
        except ToolError as e:
            t.reply = str(e) if e.code != 404 else "I can't find that batch."
            t.trace.end("could not look", ok=False)
            return
        self._names_reply(t, gen, rows)
        t.trace.end("checked")

    def _names_reply(self, t: Turn, gen: str, rows: list) -> None:
        nm = self._nm(t.sess, gen)
        t.generation = gen
        t.trace.step(f"looked at {len(rows)} sticker{'s' if len(rows) != 1 else ''}")
        change = [r for r in rows if not r["fits"]]
        if not rows:
            t.reply = f"**{nm}** has no finished stickers to look at yet."
        elif not change:
            t.reply = f"I looked at all {len(rows)} stickers of **{nm}**: the names fit the pictures."
        else:
            t.reply = f"I looked at **{nm}**. These names do not fit the picture:\n" + "\n".join(f"S{r['index']}: \"{r['current']}\" -> **{r['name']}**" for r in change)
            t.chips = [{"label": "Apply the new names", "action": "names_apply", "generation": gen}, {"label": "Keep my names", "action": "names_keep", "generation": gen}]

    def n_names_decide(self, state: State) -> dict:
        t: Turn = state["turn"]
        gen = str((t.action or {}).get("generation") or "")
        if (t.action or {}).get("type") == "names_keep":
            t.reply = "Kept: the names stay as they are."
            return {}
        try:
            done = self.tools.apply_titles(gen)
        except ToolError as e:
            t.reply = str(e)
            return {}
        t.generation = gen or None
        t.trace.task("renaming")
        t.trace.step(f"renamed {len(done)} sticker{'s' if len(done) != 1 else ''}")
        t.trace.end("recorded in each sticker's history")
        t.reply = ("Renamed " + ", ".join(f"S{i}" for i in sorted(done)) + ". The files and the search keep their own names; only what you read changed.") if done else "There was nothing waiting to rename."
        return {}

    def auto_name(self, sid: str, gid: str) -> bool:
        """Once AI vision is allowed, a batch that has finished stickers gets looked at without being asked: the answer arrives as a message of its own in the chat (a turn the assistant starts
        itself, so it takes the session's lock like any turn and is skipped, to be tried at the next poll, when the person is mid-turn). Once per batch (`named`). Returns True when it ran."""
        lock = self.cache.lock(f"session:{sid}")
        try:
            lock.__enter__()
        except cachemod.Busy:
            return False
        try:
            sess = self.store.load(sid)
            if sess["settings"].get("allow_vlm") is not True or gid in sess.setdefault("named", []):
                return False
            try:
                if not self.tools.ready_indexes(gid):
                    return False
            except ToolError:
                return False
            sess["named"].append(gid)
            msg = self.store.add_message(sess, "assistant", "", status="working")
            self.store.save(sess)
            t = Turn(sid, "", [], None, sess, msg, Trace(self.store, sess, msg))
            t.trace.task(f"looking at {self._nm(sess, gid)}")
            try:
                rows = self.tools.name_proposals(gid, self.brain.name_check if self.brain.available else (lambda items: None), allowed=True)
                self._names_reply(t, gid, rows)
                t.trace.end("checked")
                ok = True
            except Exception as e:
                t.reply, ok = "I could not look at the pictures this time; nothing changed.", False
                t.trace.end("could not look", ok=False)
                msg["error"] = f"{type(e).__name__}: {e}"[:300]
            msg.update(text=t.reply, cards=t.cards, chips=t.chips, status="done" if ok else "error")
            self.store.save(sess)
            return True
        finally:
            lock.__exit__(None, None, None)

    def n_smalltalk(self, state: State) -> dict:
        t: Turn = state["turn"]
        kind = smalltalk_kind(t.text)
        t.trace.task({"thanks": "saying you're welcome", "bye": "saying goodbye", "ack": "noting that", "no": "noting that"}.get(kind, "saying hello"))
        t.reply = {"thanks": "You're welcome! Tell me what to change, or what to make next.", "bye": "Bye! Your stickers will be here when you come back.",
                   "ack": "Anytime. Tell me what to change, or what to make next.", "no": "Okay. Tell me when you want to make something."}.get(kind, self._hello())
        t.chips = [{"label": s, "text": s} for s in SUGGESTIONS]
        t.trace.end("ready")
        return {}

    def n_profile(self, state: State) -> dict:
        """What the person says about themselves ("hello from noor", "it is 'noor'", "I live in Paris", the answer to my "what should I call you?") goes to THEIR profile
        (out/profile/<user>.json, agent/profile.py) and nothing else changes: no subject, no plan, and a plan I am holding stays held. The rules read it first; the model only reads what
        they could not (several facts, a correction, a reference), and whatever it proposes passes profile.validate_facts before anything is written. The reply says what was saved."""
        t: Turn = state["turn"]
        prof = self._profile()
        facts = dict(t.profile_answer or profile_facts(t.text)[0])
        if not facts and self.brain.available:
            facts = self.brain.extract_profile(t.text, prof.facts_text()) or {}
        t.trace.task("remembering what you told me")
        saved = prof.set_facts(facts, t.text)
        tone = detect_tone(t.text)
        if tone:
            prof.set_tone(tone, t.text)
        if not saved and not tone:
            t.prefix = "I didn't catch what to remember. Say it like \"my name is Noor\" or \"I live in Paris\"."
            t.trace.end("nothing saved", ok=False)
            return {}
        held = (t.sess.get("pending") or {}).get("subject")
        dropped = ""
        if held and "name" in saved and re.sub(r"[^a-z]", "", str(held).lower()) in (re.sub(r"[^a-z]", "", saved["name"].lower()), re.sub(r"[^a-z]", "", t.text.lower())):
            t.sess.pop("pending", None)                                 # a plan "for noor" was the name misread as a subject (before 2026-10-04): it goes, said once
            dropped, held = f" I dropped the plan for \"{held}\": that was your name, not a sticker subject.", None
        said = profile_said(saved)
        hello = f"Nice to meet you, {saved['name']}! " if "name" in saved else "Got it! "
        more = "NEW" in t.intents                                       # "hi, I'm Sam, make me a falcon": the request goes on after this
        t.prefix = hello + (f"I'll remember {said}." if saved else "Nothing new to remember.") + dropped + ("" if more else f" Your plan for {held} is still waiting: say \"create it\" when you're ready." if held else " What will you create today?")
        if tone:
            t.prefix += TONE_ACK[tone]
        if not held and not more:
            t.chips = [{"label": s, "text": s} for s in SUGGESTIONS]
        t.trace.end("saved: " + said)
        return {}

    def n_clarify(self, state: State) -> dict:
        t: Turn = state["turn"]
        r = t.res
        if r.needs_clarification:
            asked = [i for i in t.intents if i not in ("AMBIGUOUS", "SMALLTALK", "CONFIRM", "CANCEL")]
            if asked:
                t.sess["awaiting"] = {"intents": asked, "text": t.text}
            t.reply = r.clarification or "Which one do you mean?"
            t.chips = [{"label": "#" + o.split("/S")[1], "text": f"number {o.split('/S')[1]}"} for o in r.options]
            t.trace.end("one question")
        elif t.unsure_review:
            t.reply = "I did not decide anything: I could not tell whether you want to approve or reject. Say it plainly, for example \"approve 3\" or \"reject 4 and 5\"."
            t.trace.end("asked what to decide")
        else:
            if (t.sess.get("focus") or {}).get("generation"):
                t.reply = "I couldn't understand that request. What would you like to see or change in this batch?"
                t.trace.end("asked what you mean")
            else:
                t.reply = "I'm not sure what to make yet. Tell me a subject, like \"a teddy bear waving\", or pick one:"
                t.chips = [{"label": s, "text": s} for s in SUGGESTIONS]
                t.trace.end("asked what to make")
        return {}

    def n_finish(self, state: State) -> dict:
        t: Turn = state["turn"]
        self._finish(t)
        return {}

    def _vision_ack(self, t: Turn) -> None:
        """The person flipped the AI vision switch since the last turn (`SessionStore.set_vision`: state only, no turn of its own). This turn goes on as usual and says so once, in its own words."""
        ack = t.sess.pop("vision_ack", None)
        if ack:
            t.reply = (t.reply + "\n\n" if t.reply else "") + ("AI vision is on, as you allowed: I can look at your stickers now (names, captions)." if ack == "allowed"
                                                                 else "AI vision is off, as you chose: no picture goes to a vision model. Say \"allow AI vision\" or use the switch any time.")

    def _ask_vision_early(self, t: Turn) -> bool:
        """The first answer of a chat also asks, once, whether AI vision may be used (`settings.allow_vlm`: None = never asked). It is a pair of buttons that change only that setting,
        so it never replaces a pending go-ahead (the plan's Create stays what it was) and a person who ignores it is simply not asked again in this chat."""
        sess = t.sess
        if sess["settings"].get("allow_vlm") is not None or sess.get("vision_asked"):
            return False
        if not set(t.intents) & CREATION_INTENTS:
            return False                                  # only on the creation path: a hello, a name, a question or a "not yet" has nothing to look at
        if sess.get("awaiting") or (sess.get("pending") or {}).get("type") in ("describe", "names") or any(x in ("CONFIRM", "CANCEL") for x in t.intents) and not sess["interactions"]:
            return False                                  # a question of mine is open (which sticker? the describe consent): one question at a time
        sess["vision_asked"] = True
        t.reply = (t.reply + "\n\n" if t.reply else "") + self.VISION_ASK
        # a one-time decision is not a creation control (UI/UX spec P9): the plan keeps only Create it (a typed "no" still cancels), and the question is ONE switch on the right with a glow. Pressing it
        # writes the setting through the settings route and makes no chat turn (P10); leaving it alone is "not now" and is not asked again in this chat.
        t.chips = [c for c in t.chips if c.get("action") != "cancel"] + [{"label": "Allow AI vision", "on": "AI vision on", "off": "AI vision off", "setting": {"allow_vlm": True}, "side": "right", "glow": True}]
        return True

    def _finish(self, t: Turn, ok: bool = True) -> None:
        sess, msg = t.sess, t.msg
        if t.prefix:
            t.reply = t.prefix + ("\n\n" + t.reply if t.reply else "")
        if t.res.generation and t.res.stickers:
            sess["focus"] = {"generation": t.res.generation, "stickers": t.res.stickers[:3]}
        elif t.generation:
            sess["focus"] = {"generation": t.generation, "stickers": []}
        if ok:
            self._vision_ack(t)
        if ok and self._ask_vision_early(t):
            pass
        old, new = t.prev_pending, sess.get("pending")
        if old and new and new is not old and (old.get("type"), old.get("subject")) != (new.get("type"), new.get("subject")) and not (t.action or {}).get("type") in ("confirm", "cancel") \
                and t.intents and t.intents[0] not in ("CONFIRM", "CANCEL"):
            gone = old.get("subject") or old.get("type")
            t.reply = (t.reply + " " if t.reply else "") + f"(This replaces the plan I was holding for {gone}: nothing was spent on it.)"
        if self.brain.last_error:                                      # the model was asked in this turn and could not answer: the rules answered, and the person is told why
            t.trace.rules_note(self.brain.last_error)
        if t.model_said and ok:
            t.reply = (t.reply + "\n\n" if t.reply else "") + t.model_said
        msg.update(text=t.reply, cards=t.cards, chips=t.chips, status="done" if ok else "error")
        if msg["steps"] and msg["steps"][-1]["kind"] != "final":
            t.trace.end("done" if ok else "stopped", ok=ok)
        self.store.add_interaction(sess, t.text or (t.action or {}).get("type", ""), t.reply, t.intents,
                                   {**t.res.to_dict(), "spent_estimate": t.spent}, t.generation or t.res.generation)
        if self.store.reduce(sess, summarise=(self.brain.summarise if self.brain.available and not getattr(self._resume_mode, "active", False) else None)):
            t.trace.note("summarised the earlier part of this chat")
        self.store.save(sess)
