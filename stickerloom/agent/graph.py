"""Agent facade: owns state and composes lifecycle with intent handlers.

The original graph.Turn/Trace/State imports stay valid for stored checkpoints.
Tools and flow remain authoritative for gates, costs and media processing.
"""
from __future__ import annotations
import threading
from pathlib import Path
from ..runtime import cache as cachemod
from ..runtime.principal import principal
from . import creator
from .brain import Brain
from .memory import SessionStore
from .profile import EDIT_HINTS, Profile
from .tools import ConsoleTools
from .lifecycle.turn import Turn, State, Trace, _interrupt_message
from .lifecycle import checkpoints
from .lifecycle.runner import TurnLifecycle
from .lifecycle.builder import build_graph, route as dispatch_route
from .nodes.shared import STYLE_NAMES, compact_plan
from .nodes.understanding import UnderstandingNodes
from .nodes.creation import CreationNodes
from .nodes.batches import BatchesNodes
from .nodes.creator import CreatorNodes
from .nodes.reactions import ReactionsNodes
from .nodes.edits import EditsNodes
from .nodes.delivery import DeliveryNodes
from .nodes.conversation import ConversationNodes


class Agent(TurnLifecycle, UnderstandingNodes, CreationNodes, BatchesNodes, CreatorNodes, ReactionsNodes, EditsNodes, DeliveryNodes, ConversationNodes):
    def __init__(self, store: SessionStore, tools, brain: Brain | None = None, cache=None):
        self.store, self.tools = store, tools
        self.brain = brain or Brain()
        self.cache = cache or cachemod.default()
        self._resume_mode = threading.local()
        self.saver, self._saver_key = checkpoints.acquire(Path(self.store.out))
        self.graph = self._build()

    def _hello(self) -> str:
        who = self._profile().name()
        hi = f"Hi{' ' + who if who else ''}! "
        return hi + ("What are we making?" if self._profile().tone() == "brief" else "What will you create today?")

    def _edit_hint(self) -> str:
        """Nudge the phrasing the person uses most (tracked per edit kind), so the retry parses first time."""
        top = (self._profile().top_edit() or [None])[0]
        return f" You usually ask for {top} -- say it like '{EDIT_HINTS[top]}'." if top in EDIT_HINTS else ""

    def _profile(self) -> Profile:
        return Profile(self.store.out, getattr(self.store, "user", "local"))

    def _build(self):
        return build_graph(self)

    def route(self, state: State) -> str:
        return dispatch_route(state)


def run_turn(console, sid: str, text: str, selected: list | None = None, action: dict | None = None, user: dict | None = None) -> dict:
    """The entry the server calls: the Studio's Console, one session id, the user's text (or a button action) and the UI selection."""
    user = user or principal()
    store = SessionStore(console.out, user=user["id"], see_all=user.get("role") == "owner")
    agent = Agent(store, ConsoleTools(console, user), Brain(out=console.out))
    return agent.run_turn(sid, text, selected, action)

def _nobody_is_working(store: SessionStore, sid: str) -> bool:
    """A running turn holds the session's lock (see `Agent.prepare`); when it is free, a message that still says "working" died with its server."""
    try:
        with store.cache.lock(f"session:{sid}", 1000):
            return True
    except cachemod.Busy:
        return False

def hydrate(store: SessionStore, tools, sess: dict) -> dict:
    """The session as the page shows it: every generation card carries its live data (stickers with file urls, stage), and a card that is
    still a provider job carries the job's state until the job has produced a generation. Cheap enough to poll every second."""
    store.refresh(sess)
    if any(m.get("status") == "working" for m in sess["messages"]) and _nobody_is_working(store, sess["id"]):
        sess = store.load(sess["id"])                          # read again: the turn may have finished between the two reads
        for m in sess["messages"]:
            if m.get("status") == "working":
                _interrupt_message(m)
        store.save(sess)
    out = dict(sess)
    msgs = []
    for m in sess["messages"]:
        m = dict(m)
        cards = []
        for c in m.get("cards", []):
            c = dict(c)
            if c.get("type") == "generation":
                gid = c.get("generation")
                if c.get("job"):
                    try:
                        j = tools.job(c["job"])
                        c.update(job_status=j.get("status"), job_stage=j.get("stage"), job_error=j.get("error"), cost=j.get("cost"),
                                 job_info={k: j.get(k) for k in ("id", "status", "error", "external_task_id", "provider_check")})
                        gid = gid or j.get("generation")
                    except Exception:
                        pass
                if gid:
                    c["generation"] = gid
                    try:
                        c["data"] = tools.generation(gid)
                    except Exception:
                        pass
            if c.get("type") == "creator" and (sess.get("creator_run") or {}).get("id") == c.get("run_id"):
                c["run"] = dict(sess["creator_run"], steps=creator.labels(sess["creator_run"]))        # the card shows the run as it is now
            cards.append(c)
        m["cards"] = cards
        msgs.append(m)
    out["messages"] = msgs
    out["working"] = any(m.get("status") == "working" for m in msgs)
    out["summary_text"] = store.summary_text(sess)
    return out
