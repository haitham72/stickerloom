"""Delivery intent handlers. State is owned by Agent; no module state."""
from __future__ import annotations
import re
from .. import stages
from ..tools import ToolError
from ..lifecycle.turn import Turn, State
from .shared import SUGGESTIONS, _with_pending

class DeliveryNodes:
    def n_animate(self, state: State) -> dict:
        t: Turn = state["turn"]
        gen = t.res.generation or (t.sess.get("focus") or {}).get("generation") or (self.store.latest_pass(t.sess) or {}).get("generation")
        if not gen and re.search(r"\b(?:make|create|draw|generate|design)\b", t.text.lower()):
            return self.n_new(state)                       # "make me falcon stickers and animate them": a new request that runs to the animation (stages.resolve)
        stage = stages.of(t.sess["settings"])
        if not stages.at_least(stage, "animation") and not t.stage_force:
            return self._stage_ask(t, {"kind": "more", "need": "animation", "stage": stage})
        if not gen:
            t.reply = "There is nothing to animate yet. Let's make some stickers first."
            t.chips = [{"label": s, "text": s} for s in SUGGESTIONS[:3]]
            return {}
        ready = self.tools.ready_indexes(gen)
        if not ready:
            t.reply = f"{self._nm(t.sess, gen)} has no finished stickers yet; I'll animate them once they're ready."
            return {}
        subj = self.store.subject_for_generation(t.sess, gen)
        t.trace.retitle(f"animating {self._nm(t.sess, gen)}")
        t.trace.step(f"{len(ready)} stickers ready to move")
        spec = {"type": "animate", "generation": gen, "subject": subj["name"] if subj else "", "loop": False}
        if self.tools.live() and t.sess["settings"].get("ask_before_spending", True):
            t.sess["pending"] = spec
            t.reply = f"I'll animate the {len(ready)} stickers of **{self._nm(t.sess, gen)}** (the price is shown when it is sent; about 8 credits for a 3×3 sheet). Go ahead?"
            t.chips = [{"label": "Animate", "action": "confirm"}, {"label": "Not yet", "action": "cancel"}]
            t.trace.end("ready")
        else:
            t.sess["pending"] = spec
            self.n_confirm({"turn": _with_pending(t, spec)})
        return {}

    def _ready_now(self, gen: str) -> list | None:
        """ready_indexes, or None when the batch cannot be read (the caller then runs the existing path, which says so in words)."""
        try:
            return self.tools.ready_indexes(gen)
        except Exception:
            return None

    def n_export(self, state: State) -> dict:
        """Pack the open batch and send it to Telegram. Nothing moves yet: the animation goes first (its own go-ahead); a batch that already
        moves is packed and sent after one confirmation, because sending is outward."""
        t: Turn = state["turn"]
        gen = t.res.generation or (t.sess.get("focus") or {}).get("generation") or (self.store.latest_pass(t.sess, with_generation=True) or {}).get("generation")
        if not gen:
            t.reply = "There is nothing to send yet. Let's make some stickers first."
            t.chips = [{"label": s, "text": s} for s in SUGGESTIONS[:3]]
            return {}
        stage = stages.of(t.sess["settings"])
        if not stages.at_least(stage, "export") and not t.stage_force:
            return self._stage_ask(t, {"kind": "more", "need": "export", "stage": stage})
        name = self._nm(t.sess, gen)
        subj = self.store.subject_for_generation(t.sess, gen)
        subject = (subj or {}).get("name") or ""
        t.trace.retitle(f"sending {name} to Telegram")
        try:
            animated = [s["index"] for s in self.tools.generation(gen)["stickers"] if s.get("anim_status") == "READY"]
        except ToolError:
            animated = []
        if not animated:
            spec = {"type": "animate", "generation": gen, "subject": subject, "loop": False, "then": "pack_send"}
            if self.tools.live() and t.sess["settings"].get("ask_before_spending", True):
                t.sess["pending"] = spec
                t.reply = f"I'll animate **{name}** first (the price is shown when it is sent), then pack it and send it to Telegram. Go ahead?"
                t.chips = [{"label": "Animate", "action": "confirm"}, {"label": "Not yet", "action": "cancel"}]
                t.trace.end("ready")
            else:
                t.sess["pending"] = spec
                self.n_confirm({"turn": _with_pending(t, spec)})
            return {}
        spec = {"type": "pack_send", "generation": gen, "subject": subject}
        if self.tools.live() and t.sess["settings"].get("ask_before_spending", True):
            t.sess["pending"] = spec
            t.reply = f"Pack **{name}** ({len(animated)} animated) and send it to Telegram?"
            t.chips = [{"label": "Pack and send", "action": "confirm"}, {"label": "Not yet", "action": "cancel"}]
            t.trace.end("ready")
        else:
            t.sess["pending"] = spec
            self.n_confirm({"turn": _with_pending(t, spec)})
        return {}

    def n_feedback(self, state: State) -> dict:
        t: Turn = state["turn"]
        r = t.res
        if not (r.positive or r.negative):
            if r.stickers:                                                 # we know which, not what the user thinks of it
                n = r.stickers[0].split("/S")[1]
                t.reply = t.reply or f"What do you think of {', '.join(x.split('/')[1] for x in r.stickers)}?"
                t.chips = [{"label": "I like it", "text": f"I like number {n}"}, {"label": "Not for me", "text": f"I don't like number {n}"}]
                return {}
            t.reply = t.reply or "Which sticker do you mean? Say its number, or click it. (For example \"I like 2 and 7 but not 3\".)"
            if t.res.generation:
                t.sess["awaiting"] = {"intents": ["FEEDBACK"], "text": t.text}
            return {}
        persistent = bool(re.search(r"\b(never|always|from now on|every time|i never want|i always want)\b", t.text.lower()))
        if r.positive:
            self.store.add_feedback(t.sess, "POSITIVE", r.positive, t.text, "PERSISTENT" if persistent else "TEMPORARY")
        if r.negative:
            self.store.add_feedback(t.sess, "NEGATIVE", r.negative, t.text, "PERSISTENT" if persistent else "TEMPORARY")
        t.sess["focus"] = {"generation": r.generation, "stickers": r.positive[:1] or r.negative[:1]}
        bits = [f"+{x.split('/')[1]}" for x in r.positive] + [f"−{x.split('/')[1]}" for x in r.negative]
        t.trace.step("noted " + " ".join(bits))
        if persistent:
            t.trace.note("saved as a lasting preference because you said so")
        if "edit" not in t.queue:
            t.reply = f"Noted: {' '.join(bits)}. I'll use that for the next set." + (" (Saved as a lasting preference.)" if persistent else "")
            t.chips = [{"label": "Make another set", "text": "make another set"}] + (
                [{"label": f"Redo {r.negative[0].split('/')[1]}", "text": f"redo number {r.negative[0].split('/')[1][1:]}"}] if r.negative else [])
        return {}

    def n_review(self, state: State) -> dict:
        t: Turn = state["turn"]
        gen = t.res.generation or (t.sess.get("focus") or {}).get("generation")
        if not gen:
            t.reply = "There is nothing to review yet."
            return {}
        low = t.text.lower()
        decision = "REJECT" if re.search(r"\b(reject|decline)\b", low) else "APPROVE"
        ready = self.tools.ready_indexes(gen)
        bulk = False
        m = re.search(r"\ball\b(?:\s+(?:of them|the stickers))?\s*(?:but|except)\s+(.*)", low)
        if m:
            from ..resolver import _numbers
            skip = _numbers(m.group(1), 9)
            idx, bulk = [i for i in ready if i not in skip], True
        elif re.search(r"\b(all|everything|them all)\b", low):
            idx, bulk = ready, True
        else:
            idx = [int(s.split("/")[1][1:]) for s in t.res.stickers]
        if not idx:
            t.reply = "Which stickers? For example \"approve all but 5 and 6\"."
            t.sess["awaiting"] = {"intents": ["REVIEW"], "text": t.text}
            return {}
        if bulk and len(idx) > 1:                                    # a decision on many stickers at once is confirmed, never read off one sentence
            names = ", ".join("S" + str(i) for i in idx)
            t.sess["pending"] = {"type": "review", "generation": gen, "decision": decision, "indexes": idx, "subject": self._nm(t.sess, gen)}
            t.reply = f"{'Approve' if decision == 'APPROVE' else 'Reject'} {names} in **{self._nm(t.sess, gen)}**? Say yes to record it, or no."
            t.chips = [{"label": "Yes, " + decision.lower(), "action": "confirm"}, {"label": "No", "action": "cancel"}]
            t.trace.end("waiting for your yes")
            return {}
        return self._do_review(t, gen, decision, idx)

    def _do_review(self, t: Turn, gen: str, decision: str, idx: list) -> dict:
        t.trace.retitle(("approving" if decision == "APPROVE" else "rejecting") + f" in {self._nm(t.sess, gen)}")
        r = self.tools.review(gen, decision, idx, "from the chat")
        t.trace.step(f"{decision.lower()}d {', '.join('S' + str(i) for i in r['done'])}")
        verb = "Approved" if decision == "APPROVE" else "Rejected"
        t.reply = f"{verb} {', '.join('S' + str(i) for i in r['done'])}." + (
            " Python blocked " + ", ".join(f"S{x['index']}" for x in r["refused"]) + ", and a block is final." if r["refused"] else "")
        t.generation = gen
        t.cards.append({"type": "generation", "generation": gen, "job": None, "subject": ""})
        t.chips = [{"label": "Animate them", "text": "animate"}]
        t.trace.end("recorded as your decision")
        return {}
