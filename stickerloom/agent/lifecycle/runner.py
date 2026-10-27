"""One-turn locking, safe replay, execution and checkpoint resource release."""
from __future__ import annotations
from ...runtime import cache as cachemod
from ..memory import SessionError
from .turn import Turn, Trace, _interrupt_message
from . import checkpoints
SAFE_RESUME_NODES = {"resolve", "clarify", "smalltalk", "finish"}

class TurnLifecycle:
    def prepare(self, sid: str, text: str, selected: list | None = None, action: dict | None = None) -> Turn:
        """Take the session's lock (one turn at a time: a second message gets a 409, never a silent queue) and write the user's message and the
        assistant's "working" placeholder, so the page shows the turn at once. `execute` then runs the graph and releases the lock."""
        lock = self.cache.lock(f"session:{sid}")
        try:
            lock.__enter__()
        except cachemod.Busy:
            raise SessionError("The assistant is still working on your last message.", 409)
        try:
            sess = self.store.load(sid)
            if self._resume_dead_turn(sid, sess, lock):
                sess = self.store.load(sid)
            for m in sess["messages"]:                           # we hold the lock, so nobody is running a turn: a "working" message is one that died with its server
                if m.get("status") == "working":
                    _interrupt_message(m)
            label = text.strip() if text.strip() else {"confirm": "Create", "cancel": "No", "creator_go": "Approve and continue", "creator_stop": "Stop", "creator_skip": "Continue without those", "creator_force": "Continue with them",
        "creator_force_video": "Animate at the new price", "names_apply": "Apply the new names", "names_keep": "Keep my names", "retry_sheet": "Try the sheet again",
        "regenerate": "Regenerate"}.get((action or {}).get("type"), "")
            if not label and (action or {}).get("type") == "batch_more" and str(action.get("to")).isdigit():
                label = f"Batch {int(action['to']):02d}"
            self.store.add_message(sess, "user", label)
            msg = self.store.add_message(sess, "assistant", "", status="working")
            self.store.save(sess)
        except Exception:
            lock.__exit__(None, None, None)
            raise
        t = Turn(sid, text, list(selected or []), action, sess, msg, Trace(self.store, sess, msg))
        t._lock = lock
        return t

    def execute(self, t: Turn) -> dict:
        self.brain.last_error = None                                  # per turn: `_finish` says "answered by rules" only for a failure of THIS turn
        try:
            try:
                self.graph.invoke({"turn": t}, {"configurable": {"thread_id": t.sid}})
            except Exception as e:                                  # the user sees a calm sentence, never the internals
                t.trace.end("something went wrong, nothing was spent", ok=False)
                t.reply = "Something went wrong on my side, and nothing was spent. Try again, or tell me a little differently."
                t.cards, t.chips = [], [{"label": "Try again", "text": t.text}] if t.text else []
                t.msg["error"] = f"{type(e).__name__}: {e}"[:300]
                self._finish(t, ok=False)
            return self.store.load(t.sid)["messages"][-1]
        finally:
            t._lock.__exit__(None, None, None)

    def run_turn(self, sid: str, text: str, selected: list | None = None, action: dict | None = None) -> dict:
        return self.execute(self.prepare(sid, text, selected, action))

    def _last_focus(self, sid: str, gen: str, skip_msg) -> list:
        """Newest earlier turn's stickers for this batch, from the checkpoints (never the live turn)."""
        try:
            history = list(self.graph.get_state_history({"configurable": {"thread_id": sid}}, limit=40))
        except Exception:
            return []
        for snap in history:
            try:
                t = (snap.values or {}).get("turn")
            except Exception:
                continue
            if t is None or (t.msg or {}).get("id") == skip_msg:
                continue
            focus = (getattr(t, "sess", None) or {}).get("focus") or {}
            if focus.get("generation") == gen and focus.get("stickers"):
                return list(focus["stickers"])
        return []

    def _resume_dead_turn(self, sid: str, sess: dict, lock) -> bool:
        """Replay only safe nodes, checking every checkpoint before advancing one step.

        Provider-capable nodes (including understand/profile/ask/names) and decisions
        are never replayed. A new message always gets a fresh turn after this boundary.
        """
        try:
            snap = self.graph.get_state({"configurable": {"thread_id": sid}})
        except Exception:
            return False
        if not snap or not snap.next or set(snap.next) - SAFE_RESUME_NODES:
            return False
        t = (snap.values or {}).get("turn")
        if t is None or getattr(t, "spent", 0):
            return False
        config = {"configurable": {"thread_id": sid}}
        self._resume_mode.active = True
        try:
            for _ in range(25):
                if not snap.next:
                    return True
                t = (snap.values or {}).get("turn")
                if set(snap.next) - SAFE_RESUME_NODES or t is None or getattr(t, "spent", 0):
                    return False
                # resolve's model fallback is not replayable either.
                if "resolve" in snap.next and any(q in ("ask", "edit") for q in t.queue):
                    return False
                self.graph.invoke(None, config, interrupt_after="*")
                snap = self.graph.get_state(config)
            return False
        except Exception:
            return False
        finally:
            self._resume_mode.active = False

    def close(self):
        checkpoints.release(getattr(self, "_saver_key", ""))

    @staticmethod
    def close_all():
        checkpoints.close_all()
