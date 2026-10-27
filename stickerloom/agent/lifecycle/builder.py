"""Graph topology and dispatch. Intent handlers do not construct the graph."""
from __future__ import annotations
from .turn import State, Turn, Trace

def build_graph(agent):
    from langgraph.graph import END, StateGraph
    g = StateGraph(State)
    nodes = {"understand": agent.n_understand, "resolve": agent.n_resolve, "new": agent.n_new, "multi": agent.n_multi, "effects": agent.n_effects, "particles": agent.n_particles, "editroute": agent.n_editroute, "unsupported": agent.n_unsupported, "refine": agent.n_refine, "another": agent.n_another,
              "edit": agent.n_edit, "undo": agent.n_undo, "animate": agent.n_animate, "export": agent.n_export, "feedback": agent.n_feedback, "review": agent.n_review, "ask": agent.n_ask,
             "settings": agent.n_settings, "search": agent.n_search, "confirm": agent.n_confirm, "cancel": agent.n_cancel,
             "smalltalk": agent.n_smalltalk, "profile": agent.n_profile, "clarify": agent.n_clarify, "retry": agent.n_retry, "names": agent.n_names, "names_decide": agent.n_names_decide, "creator": agent.n_creator, "batches": agent.n_batches, "regenerate": agent.n_regenerate, "emoji_batch": agent.n_emoji_batch, "finish": agent.n_finish}
    def guard(fn):
        def w(state):
            t = state["turn"]
            if getattr(t, "trace", None) is None:
                t.trace = Trace(agent.store, t.sess, t.msg)
            return fn(state)
        return w
    for k, fn in nodes.items():
        g.add_node(k, guard(fn))
    g.set_entry_point("understand")
    g.add_edge("understand", "resolve")
    targets = {k: k for k in nodes if k not in ("understand", "resolve")}
    g.add_conditional_edges("resolve", agent.route, targets)
    for k in nodes:
        if k not in ("understand", "resolve", "finish"):
            g.add_conditional_edges(k, agent.route, targets)
    g.add_edge("finish", END)
    return g.compile(checkpointer=agent.saver)

def route(state: State) -> str:
    t: Turn = state["turn"]
    if t.res.needs_clarification and t.queue and t.queue[0] not in ("settings", "smalltalk"):
        t.queue.clear()
        return "clarify"
    return t.queue.pop(0) if t.queue else "finish"
