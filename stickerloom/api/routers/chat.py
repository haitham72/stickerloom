"""Chat native HTTP routes."""
from __future__ import annotations
import asyncio
from fastapi import APIRouter, Request
from ..dependencies import _native_headers
from starlette.responses import StreamingResponse
from ..legacy_adapter import _legacy_call
def _sse(event, data):
    return f"event: {event}\ndata: {data}\n\n".encode()

def build_router(ctx):
    router = APIRouter()
    H = ctx.handler
    port = ctx.port

    @router.get("/api/chat/sessions/{sid}/stream", include_in_schema=False)
    @router.get("/api/v1/chat/sessions/{sid}/stream", include_in_schema=False)
    async def chat_stream(request: Request, sid: str):
        """The chat turn as it happens: `turn` each time the last message changes (its steps, cards, text), then `done`; a comment every 15 s keeps
        the connection open; the stream ends after 10 minutes (EventSource reconnects). Access is the session route's own: the same headers go through the handler."""
        import json
        from starlette.responses import Response
        from ..schemas import ChatDoneEvent, ChatTurnEvent
        raw_headers = b"".join(k + b": " + v + b"\r\n" for k, v in request.headers.raw) + b"\r\n"
        client = request.client.host if request.client else "127.0.0.1"
        target = f"/api/chat/sessions/{sid}"
        status, body = await asyncio.to_thread(_legacy_call, H, "GET", target, raw_headers, client, port)
        if status != 200:                                # 401 / 403 / 404 exactly as the session route answers
            return Response(body, status_code=status, media_type="application/json; charset=utf-8", headers=_native_headers(request))

        async def events():
            nonlocal status, body
            loop = asyncio.get_running_loop()
            end, last, quiet = loop.time() + 600, None, loop.time()
            yield b"retry: 3000\n\n"
            while loop.time() < end:
                if await request.is_disconnected():
                    return
                s = json.loads(body)
                msgs = s.get("messages") or []
                turn = ChatTurnEvent(working=bool(s.get("working")), count=len(msgs), message=msgs[-1] if msgs else None)
                sig = turn.model_dump_json()
                if sig != last:
                    last, quiet = sig, loop.time()
                    yield _sse("turn", sig)
                if not turn.working:
                    yield _sse("done", ChatDoneEvent(count=len(msgs)).model_dump_json())
                    return
                if loop.time() - quiet > 15:
                    quiet = loop.time()
                    yield b": ping\n\n"
                await asyncio.sleep(0.4)
                status, body = await asyncio.to_thread(_legacy_call, H, "GET", target, raw_headers, client, port)
                if status != 200:
                    return

        return StreamingResponse(events(), media_type="text/event-stream; charset=utf-8", headers={**_native_headers(request), "X-Accel-Buffering": "no"})
    return router
