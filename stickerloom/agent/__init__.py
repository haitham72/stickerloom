"""The agentic chat (Phase 4): sessions with memory, a deterministic-first resolver, a LangGraph graph over the Studio's own engine.

    from stickerloom.agent import run_turn

The chat is an INTERFACE; structured state is the system of record. The LLM orchestrates the deterministic pipeline and never
generates media or judges pixels. Every reference a user makes ends as an id like `G012/S3`; every change is a new generation
with a parent; the gate rules stay Python's (a human approves, the VLM only pre-reviews)."""
from .memory import SessionStore
from .graph import run_turn

__all__ = ["SessionStore", "run_turn"]
