"""Telegram native HTTP routes."""
from __future__ import annotations
import asyncio
from fastapi import APIRouter, Request
from ...services import telegram as tg

def build_router(ctx):
    router = APIRouter()
    c = ctx.console
    _j = ctx.json
    _who = ctx.who

    @router.get("/api/telegram/sends/{tid}", include_in_schema=False)
    @router.get("/api/v1/telegram/sends/{tid}", include_in_schema=False)
    async def telegram_send_state(request: Request, tid: str):
        user, err = await _who(request, f"/api/telegram/sends/{tid}")
        if err:
            return _j(request, *err)
        st = await asyncio.to_thread(tg.send_state, c.out, tid)
        if not st or user.get("role") != "owner" or st.get("by") != (user.get("id") or "human"):      # only its sender sees it
            return _j(request, 404, {"error": "not found"})
        return _j(request, 200, st)

    return router
