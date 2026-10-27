"""Shared presentation constants and pure plan helpers for intent handlers."""
from __future__ import annotations
from ...generation import styles
from ..lifecycle.turn import Turn

CONTINUE_RX = r"^(?:continue|go on|go ahead|proceed|carry on|keep going|approve and continue|resume)\b"
CREATION_INTENTS = {"NEW", "NEW_MULTI", "ANOTHER", "REFINE", "EDIT_STICKERS", "EDIT_ROUTE", "ANIMATE", "CONFIRM", "EFFECTS", "PARTICLES", "CREATOR", "RETRY", "BATCH_MORE", "REGENERATE"}
SUGGESTIONS = ["a teddy bear waving", "falcon stickers", "my dog as a banana", "Eid mubarak greetings"]
TONE_ACK = {"brief": " I'll keep it short.", "warm": " I'll keep it warm and chatty.", "formal": " I'll keep it formal."}
STYLE_NAMES = {p["id"]: p["label"].lower() for p in styles.PRESETS}          # the names the cards and replies use: the real presets, never a list of the chat's own


def _credits(x) -> str:
    return "free" if not x else f"about {x:g} credit" + ("" if x == 1 else "s")

def _edge_kw(item: dict) -> dict:
    """The edge finish (white stroke, fringe trim) an edit carries from its parent batch, as keyword arguments for `tools.create`; nothing for a new request, so every other caller is unchanged."""
    return {k: int(item[k]) for k in ("outline", "erode") if item.get(k) is not None}

def compact_plan(plan: dict) -> dict:
    """The plan a card shows, as it is stored in `pending`: the template, the slots and the cells; the prompts are rebuilt from them (`tasks.plan_again`), so the stored plan is small and the
    batch that runs is exactly the one that was approved."""
    keep = {k: v for k, v in (plan or {}).items() if k not in ("sheet_prompt", "video_prompt")}
    keep["stickers"] = [{k: v for k, v in s.items() if k != "prompt"} for s in (plan or {}).get("stickers") or []]
    return keep

def _with_pending(t: Turn, spec: dict) -> Turn:
    t.sess["pending"] = spec
    return t
