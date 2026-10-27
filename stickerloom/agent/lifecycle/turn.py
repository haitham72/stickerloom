"""Checkpointable turn state and its visible trace."""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Any, TypedDict
from ..memory import SessionStore
from ..resolver import Resolution

INTERRUPTED = "That turn was interrupted before it finished (the server restarted). Check the Queue for any job already started before trying again."

def _interrupt_message(msg):
    msg.update(status="error", text=msg.get("text") or INTERRUPTED)
    msg.setdefault("steps", []).append({"kind": "note", "label": INTERRUPTED, "status": "done"})


class Trace:
    """The visible step queue of one turn. Saved as it grows so the page (which polls) shows the agent working."""

    def __init__(self, store: SessionStore, sess: dict, msg: dict):
        self.store, self.sess, self.msg = store, sess, msg

    def _add(self, kind: str, label: str, detail=None, status: str = "done") -> dict:
        s = {"kind": kind, "label": label, "detail": detail, "status": status, "ts": round(time.time(), 3)}
        self.msg["steps"].append(s)
        self.store.save(self.sess)
        return s

    def task(self, label: str):
        return self._add("task", label, status="running")

    def step(self, label: str, detail=None):
        return self._add("step", label, detail)

    def note(self, label: str):
        return self._add("note", label)

    def end(self, label: str, ok: bool = True):
        return self._add("final", label, status="done" if ok else "error")

    def rules_note(self, reason: str):
        """"answered by rules: <why>": the model was asked and could not answer, so the rules decided. Placed before the ending step (the collapsed trace shows the last step's label)."""
        steps = self.msg["steps"]
        steps.insert(len(steps) - 1 if steps and steps[-1]["kind"] == "final" else len(steps),
                     {"kind": "note", "label": "answered by rules: " + " ".join(str(reason).split())[:160], "detail": None, "status": "done", "ts": round(time.time(), 3)})
        self.store.save(self.sess)

    def retitle(self, label: str) -> None:
        for s in self.msg["steps"]:
            if s["kind"] == "task":
                s["label"] = label
                break

@dataclass
class Turn:
    sid: str
    text: str
    selected: list
    action: dict | None
    sess: dict
    msg: dict
    trace: Trace
    intents: list = field(default_factory=list)
    conf: float = 0.0
    res: Resolution = field(default_factory=Resolution)
    reply: str = ""
    cards: list = field(default_factory=list)
    chips: list = field(default_factory=list)
    generation: str | None = None
    queue: list = field(default_factory=list)
    spent: float = 0.0
    prev_pending: dict | None = None             # the plan that was waiting when this message arrived (a new one replaces it, and the reply says so)
    er: dict | None = None                       # what an edit request means (agent/editroute.classify_edit): the editor, or a tweak / an action / a redesign
    answer: str = ""                             # the typed answer to my "which sticker?" (t.text then holds the original request + the answer): "12" is checked against the batch's size
    unsure_review: bool = False                  # an approve / reject sentence with a negation in it: nothing is decided, the person is asked
    profile_answer: dict | None = None           # the reply to my own "what should I call you?": {"name": "Noor"}, never a subject
    stage_force: bool = False                    # the person chose "Keep <stage> and go": this message runs at the stage as it is, no question
    model_said: str = ""                         # "Using Grok Video 1.5 for the animation": a model the message named (_models_named), said at the end of the reply
    prefix: str = ""                             # what the profile node said, put before the rest of the turn's answer ("Nice to meet you, Sam! ..." then the plan)
    _lock: Any = None

    def __getstate__(self):
        d = dict(self.__dict__)
        d.pop("trace", None)
        d.pop("_lock", None)
        return d

    def __setstate__(self, d):
        self.__dict__.update(d)
        self.trace = None
        self._lock = None

class State(TypedDict, total=False):
    turn: Any
