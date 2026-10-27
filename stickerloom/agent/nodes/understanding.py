"""Understanding intent handlers. State is owned by Agent; no module state."""
from __future__ import annotations
import re
from .. import stages, manual_batches
from .. import editroute
from ..resolver import batch_intent, beyond, classify, display_request, is_sticker_answer, particles_intent, polarity_of, resolve, profile_facts
from ..lifecycle.turn import Turn, State
from .shared import CONTINUE_RX

class UnderstandingNodes:
    def _nm(self, sess: dict, gid) -> str:
        """What the person calls a batch: its subject ("Eid mubarak greetings"), never the bare id "G096" (the id stays in the card's small print and the tooltips)."""
        gid = str(gid or "")
        subj = self.store.subject_for_generation(sess, gid) if gid else None
        name = (subj or {}).get("name") or ((self.store.outside_info(gid) or {}).get("prompt") if gid else "") or ""
        name = " ".join(str(name).split())[:48]
        return (name[:1].upper() + name[1:]) if name else gid

    def _named_batches(self, sess: dict, text: str) -> list[str]:
        """The batches the message names ("G012", "make G12/S3 happier") that exist and that this user may see, whether or not this chat has them yet."""
        gids = dict.fromkeys(f"G{int(m):03d}" for m in re.findall(r"\bg0*(\d{1,4})\b", text, flags=re.I))
        return [g for g in gids if self.store.subject_for_generation(sess, g) or self.store.outside_info(g)]

    @staticmethod
    def _profile_reply(text: str) -> str | None:
        """A short reply that can be the answer to my "what should I call you?": "noor", "it's Noor", "Sam!". Not a question, not a request, not a no."""
        s = text.strip().strip("\"'“”‘’.!")
        named = profile_facts(s)[0].get("name")
        if named:
            return named
        if not s or "?" in s or len(s.split()) > 3 or re.search(r"\b(?:make|create|generate|draw|give|no|nope|not|never|why|sticker|stickers)\b", s.lower()):
            return None
        return " ".join(w[:1].upper() + w[1:] for w in s.split()) if re.fullmatch(r"[^\W\d_][^\W\d_'’ -]{0,40}", s) else None

    def _route_context(self, sess: dict, asked: dict | None) -> str:
        """What the model router sees besides the message: the person's profile, the question I asked last turn, the plan I am holding, then the chat's summary.
        Without these, "noor" after "what should I call you?" reads like a subject."""
        bits = [self._profile().facts_text() or "The person: nothing known yet."]
        if asked:
            bits.append("My last question: " + ("what should I call you?" if asked.get("profile") else str(asked.get("text") or "")[:200]))
        held = sess.get("pending") or {}
        if held:
            bits.append(f"A plan I am holding (not started, waiting for the go-ahead): {held.get('subject') or held.get('type')}.")
        return "\n".join(bits + [self.store.summary_text(sess)])

    def _models_named(self, t: Turn) -> None:
        """A message that names a model ("animate with grok", "use nano banana pro") makes it the chat's own pick for that kind (settings.models, the
        image/video model selector shows it) and this turn's tools use it at once; the reply names it (`_model_said`)."""
        from ...generation import model_catalog
        picked = model_catalog.named_in(t.text) if t.text and not t.action else {}
        if not picked:
            return
        t.sess["settings"]["models"] = {**(t.sess["settings"].get("models") or {}), **picked}
        if hasattr(self.tools, "models"):
            self.tools.models = {**(self.tools.models or {}), **picked}
        names = [f"{model_catalog.find(k, v)['label']} for {'the stickers' if k == 'image' else 'the animation'}" for k, v in picked.items()]
        t.trace.step("model picked: " + ", ".join(names))
        t.model_said = "Using " + " and ".join(names) + " (it stays this chat's choice; change it in the image/video model selector)."

    def _set_stage(self, t: Turn, stage: str) -> None:
        """The person's one slider: this chat and every other one of theirs."""
        t.sess["settings"]["stage"] = stage
        try:
            self.store.set_person_stage(stage)
        except OSError:
            pass

    def _stage_ask(self, t: Turn, mis: dict) -> dict:
        """A message that does not fit the slider (agent/stages.resolve): say so in words, with buttons that set the slider and go, and nothing spent."""
        L = lambda s: stages.INFO[s]["label"]
        need, stage, text = mis["need"], mis["stage"], t.text
        go = lambda s, label, **x: {"label": label, "action": "stage_go", "stage": s, "text": text, **x}
        if mis["kind"] == "more":
            t.reply = f"That needs **{L(need)}**, and the slider is at **{L(stage)}**. Set it to {L(need)} or to Auto, and I'll go on."
            t.chips = [go(need, f"Set to {L(need)} and go"), go("auto", "Set to Auto and go"), {"label": "Not now", "action": "cancel"}]
        else:
            t.reply = f"The slider is at **{L(stage)}**, so this would go on past {L(need)}. You asked for {L(need)} only: set it to {L(need)} or to Auto?"
            t.chips = [go(need, f"Set to {L(need)} and go"), go("auto", "Set to Auto and go"), go(stage, f"Keep {L(stage)} and go", force=True)]
        t.trace.step(f"the slider is at {L(stage)}; this asks for {L(need)}" + (" only" if mis["kind"] == "less" else ""))
        t.trace.end("asked about the slider")
        return {}

    def n_understand(self, state: State) -> dict:
        t: Turn = state["turn"]
        sess = t.sess
        if t.action and t.action.get("type") == "stage_go":         # a button of _stage_ask: set the slider, then the message it was about runs again
            if stages.valid(t.action.get("stage")):
                self._set_stage(t, t.action["stage"])
            t.stage_force, t.text, t.action = bool(t.action.get("force")), str(t.action.get("text") or ""), None
        self._models_named(t)
        pending = bool(sess.get("pending"))
        t.prev_pending = sess.get("pending")
        has_gen = bool((sess.get("focus") or {}).get("generation") or self.store.latest_pass(sess) or self._named_batches(sess, t.text))
        asked = sess.pop("awaiting", None)                     # a question I asked last turn lives for exactly one answer
        answered = False
        if t.action and t.action.get("type") in ("confirm", "confirm_new", "cancel"):
            t.intents, t.conf = [t.action["type"].upper()], 1.0
        elif manual_batches.matches(t):
            t.intents, t.conf = ["EMOJI_BATCH"], 1.0
        elif t.action and t.action.get("type") == "retry_sheet":
            t.intents, t.conf = ["RETRY"], 1.0
        elif t.action and t.action.get("type") in ("batch_more", "regenerate"):
            t.intents, t.conf = (["BATCH_MORE"] if t.action["type"] == "batch_more" else ["REGENERATE"]), 1.0
        elif t.action and str(t.action.get("type", "")).startswith("creator_"):
            t.intents, t.conf = ["CREATOR"], 1.0
        elif (sess.get("creator_run") or {}).get("status") in ("waiting", "stopped") and re.match(CONTINUE_RX, t.text.strip().lower()):
            t.action = {"type": "creator_go"}
            t.intents, t.conf = ["CREATOR"], 0.95
        elif t.action and t.action.get("type") in ("names_apply", "names_keep"):
            t.intents, t.conf = ["NAMES_DECIDE"], 1.0
        elif asked and asked.get("profile") and self._profile_reply(t.text):
            t.profile_answer = {asked["profile"]: self._profile_reply(t.text)}       # "noor" right after my "what should I call you?" is the answer, never a subject
            t.intents, t.conf, answered = ["PROFILE"], 0.95, True
        elif asked and asked.get("intents") and is_sticker_answer(t.text, bool(t.selected)):
            t.answer = t.text
            t.text = f"{asked['text']} {t.text}".strip()       # the original request plus the missing "which": everything downstream reads it as one sentence
            t.intents, t.conf, answered = list(asked["intents"]), 0.95, True
            if "EDIT_ROUTE" in t.intents:
                t.er = editroute.classify_edit(asked["text"])
        else:
            t.intents, t.conf = classify(t.text, pending, has_gen, bool(t.selected))
            if not has_gen and sess.get("pack") and t.intents and t.intents[0] not in ("CONFIRM", "CANCEL") and batch_intent(t.text):
                t.intents, t.conf = (["REGENERATE"] if batch_intent(t.text)["type"] == "regenerate" else ["BATCH_MORE"]), 0.9      # the Prompt stage: a pack is planned, nothing made yet
            low = t.text.lower()
            if re.search(r"\b(approve|accept|reject|decline)\b", low) and has_gen:
                if re.search(r"\b(?:don'?t|do not|dont|can'?t|cannot|won'?t|will not|never|not|no)\b", low):
                    t.intents, t.conf, t.unsure_review = ["AMBIGUOUS"], 0.5, True          # "don't approve 3" approved S3 before: a decision is never guessed from a negation
                else:
                    t.intents, t.conf = ["REVIEW"], 0.9
            if t.conf < 0.6 and self.brain.available:
                got = self.brain.classify(t.text, self._route_context(sess, asked), focus=self.store.focus_context(sess))
                if got:
                    t.intents, t.conf = got, 0.7
            if has_gen and t.intents and t.intents[0] not in ("CONFIRM", "CANCEL", "SMALLTALK", "PROFILE", "CHANGE_SETTINGS", "REVIEW", "SEARCH", "CREATOR", "PARTICLES", "UNDO", "EXPORT", "BATCH_MORE", "REGENERATE"):         # a question ("can you rotate him?") is an ASK until the rules read it
                if editroute.unsupported(t.text):
                    t.intents, t.conf = ["UNSUPPORTED"], 0.9
                else:
                    er = editroute.classify_edit(t.text)
                    if er:                                              # the UI/UX spec P11-P13: every edit of what is open is one of: the editor, a tweak, a new action, a redesign
                        t.intents, t.conf, t.er = ["EDIT_ROUTE"], 0.9, er
            if t.intents and t.intents[0] != "PARTICLES" and particles_intent(t.text, bool(sess.get("particles"))):
                t.intents, t.conf = ["PARTICLES"], 0.9                  # "generate more", "also use them for the Princess pack": about the particle set this chat has in focus
            if t.intents and t.intents[0] in ("EDIT_STICKERS", "NEW", "FEEDBACK", "AMBIGUOUS"):
                refine_it = self._wants_refine(t, has_gen)
                if refine_it:
                    t.intents, t.conf = ["REFINE"], 0.9
        names = {"NEW": "a new set", "NEW_MULTI": "several new sets", "EFFECTS": "particle effects", "PARTICLES": "particles", "EDIT_ROUTE": "an edit", "UNSUPPORTED": "something I cannot do yet", "REFINE": "a change to a batch", "ANOTHER": "another pass", "EDIT_STICKERS": "an edit", "UNDO": "an undo", "ANIMATE": "an animation", "EXPORT": "an export", "FEEDBACK": "feedback",
                 "REVIEW": "a decision", "ASK": "a question", "CHANGE_SETTINGS": "a setting", "SEARCH": "a search", "CONFIRM": "your go-ahead",
                 "CANCEL": "a change of mind", "RETRY": "a new try of a sheet", "NAMES": "a look at the names", "CREATOR": "the creator", "BATCH_MORE": "more batches of the pack", "REGENERATE": "a new take of a batch", "NAMES_DECIDE": "your answer about the names", "SMALLTALK": "a hello", "PROFILE": "something about you", "AMBIGUOUS": "something I need to ask about"}
        t.trace.task("reading your message")
        t.trace.step("understood: " + " + ".join(names.get(i, i.lower()) for i in t.intents) + (" · answering my question" if answered else ""))
        order = {"CONFIRM": "confirm", "CONFIRM_NEW": "confirm", "CANCEL": "cancel", "CHANGE_SETTINGS": "settings", "FEEDBACK": "feedback", "REVIEW": "review",
                  "EDIT_STICKERS": "edit", "UNDO": "undo", "ANIMATE": "animate", "EXPORT": "export", "ANOTHER": "another", "NEW": "new", "NEW_MULTI": "multi", "EFFECTS": "effects", "PARTICLES": "particles", "EDIT_ROUTE": "editroute", "UNSUPPORTED": "unsupported", "REFINE": "refine", "ASK": "ask", "SEARCH": "search",
                 "SMALLTALK": "smalltalk", "PROFILE": "profile", "AMBIGUOUS": "clarify", "RETRY": "retry", "NAMES": "names", "NAMES_DECIDE": "names_decide", "CREATOR": "creator", "BATCH_MORE": "batches", "REGENERATE": "regenerate"}
        t.queue = [order[i] for i in t.intents if i in order] or ["clarify"]
        if t.intents == ["EMOJI_BATCH"]:
            t.queue = ["emoji_batch"]
        return {}

    def n_emoji_batch(self, state: State) -> dict:
        return manual_batches.handle(self, state["turn"])

    def _ctx(self, t: Turn) -> dict:
        sess = t.sess
        self.store.refresh(sess)
        focus = sess.get("focus") or {}
        gen = focus.get("generation") or (self.store.latest_pass(sess, with_generation=True) or {}).get("generation")
        fstickers = focus.get("stickers") or []
        if gen and not fstickers:
            fstickers = self._last_focus(t.sid, gen, (t.msg or {}).get("id"))  # a paid batch emptied the focus: the last turn still names its stickers
        stickers, n, parent, known = [], 9, None, {}
        for subj in sess["subjects"]:
            for p in subj["passes"]:
                if p.get("generation"):
                    known[p["generation"]] = 9
        if gen:
            try:
                card = self.tools.generation(gen)
                stickers = [{"index": s["index"], "key": s["key"], "name": s.get("name") or s["key"], "tags": s.get("tags") or [], "emoji": s.get("emoji")}
                            for s in card["stickers"]]
                n = len(stickers) or 9
                known[gen] = n
                parent = card.get("parent")
            except Exception:
                pass
        latest = (self.store.latest_pass(sess, with_generation=True) or {}).get("generation")
        return {"generation": gen, "n": n, "stickers": stickers, "focus_stickers": fstickers, "selected": t.selected,
                "parent": parent, "latest": latest, "known": known}

    def n_resolve(self, state: State) -> dict:
        t: Turn = state["turn"]
        needs = [q for q in t.queue if q in ("edit", "editroute", "feedback", "animate", "ask", "review", "another", "names")]
        if not needs:
            return {}
        for gid in self._named_batches(t.sess, t.text):                       # "make G012/S3 happier": a batch the Studio made becomes a pass of this chat
            if not self.store.subject_for_generation(t.sess, gid) and self.store.adopt(t.sess, gid):
                t.trace.note(f"{gid} was made outside this chat (the Studio): I can work on it now")
        ctx = self._ctx(t)
        replacement = re.match(r"\s*(?:replace|swap)\s+(.+?)\s+(?:with|for)\s+(.+)", t.text, re.I)
        r = resolve(replacement[1] if replacement else t.text, ctx)
        if display_request(t.text):
            r.stickers, r.needs_clarification, r.how = [], False, "whole batch"
        gone = beyond(t.text, int(ctx["known"].get(r.generation or ctx["generation"], ctx["n"]) or 9), t.answer) if (r.generation or ctx["generation"]) else []
        if gone and not r.stickers and not r.needs_clarification:       # "number 12" in a batch of 9: say what the batch has, never ask again in the same words
            size = int(ctx["known"].get(r.generation or ctx["generation"], ctx["n"]) or 9)
            r.needs_clarification, r.options = True, []
            r.clarification = f"{self._nm(t.sess, r.generation or ctx['generation'])} has {size} stickers, so there is no number {', '.join(str(g) for g in gone)}. Which one do you mean, 1 to {size}?"
        elif gone:
            t.trace.note(f"there is no number {', '.join(str(g) for g in gone)}: that batch has {ctx['n']} stickers")
        if not r.stickers and not r.needs_clarification and not display_request(t.text) and ("edit" in needs or "ask" in needs) and ctx["stickers"] and self.brain.available:
            nums = self.brain.pick_stickers(t.text, ctx["stickers"])
            if nums and ctx["generation"]:
                r.stickers, r.how = [f"{ctx['generation']}/S{i}" for i in nums], "language model"
        if "feedback" in needs and r.stickers and not (r.positive or r.negative):
            pol = polarity_of(t.text)                                     # "this is bad" + a clicked sticker: no number sat beside the opinion
            if pol == "NEGATIVE":
                r.negative = list(r.stickers)
            elif pol == "POSITIVE":
                r.positive = list(r.stickers)
        t.res = r
        if r.stickers:
            t.trace.step(f"found {', '.join(x.split('/')[1] for x in r.stickers)} in {self._nm(t.sess, r.generation)}" + (f" · {r.how}" if r.how else ""))
        elif r.generation and needs and needs != ["another"]:
            t.trace.step(f"working in {self._nm(t.sess, r.generation)}")
        return {}
