"""The one local principal: stickerloom runs for a single person on their own machine, so every caller is this principal.

It exists so the few places that ask "who is calling and may they spend?" keep a single, explicit answer: the local owner, who may spend
(the paid-call consent in `cost()` and the daily cap `STICKERLOOM_DAILY_CREDITS` still apply to every call)."""
from __future__ import annotations

PRINCIPAL = {"id": "local", "name": "local", "role": "owner", "can_spend": True, "disabled": False}


def principal() -> dict:
    return dict(PRINCIPAL)


def principal_id() -> str:
    return PRINCIPAL["id"]
