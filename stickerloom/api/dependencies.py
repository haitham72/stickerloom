"""Shared native transport and legacy-compatible security checks."""
from __future__ import annotations
import asyncio
import http.client
import io
from types import SimpleNamespace
from pydantic import ValidationError
from .legacy_adapter import _identify

def _native_headers(request) -> dict:
    """The two headers every answer of this server carries: the API version and the request id (the caller's when it is well formed)."""
    import re
    import uuid
    from .openapi import VERSION
    rid = request.headers.get("x-request-id") or ""
    return {"X-API-Version": VERSION, "X-Request-Id": rid if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", rid) else uuid.uuid4().hex[:16], "Cache-Control": "no-store"}

class RequestContext:
    def __init__(self, console, handler, port, secure=False):
        self.console, self.handler = console, handler
        self.port, self.secure = port, secure

    def json(self, request, status: int, obj):
        """JSON exactly as the original handler writes it (ensure_ascii off, the same headers)."""
        import json
        from starlette.responses import Response
        return Response(json.dumps(obj, ensure_ascii=False).encode(), status_code=status, media_type='application/json; charset=utf-8', headers=_native_headers(request))

    async def who(self, request, path):
        return await asyncio.to_thread(_identify, self.handler, request, self.port, path)

    async def body(self, request, model):
        """A body that does not match the model answers in this server's own words (400 {"error"}), never FastAPI's 422."""
        import json
        raw = await request.body()
        try:
            return (model.model_validate(json.loads(raw or b'{}')), None)
        except (ValueError, ValidationError) as e:
            msg = e.errors()[0]['msg'] if isinstance(e, ValidationError) else str(e)
            return (None, f'bad request: {msg}')

    async def member(self, request, path):
        """The caller of a route: the local principal (the Host/Origin guard has passed)."""
        user, err = await self.who(request, path)
        if err:
            return (None, self.json(request, *err))
        return (user, None)

    admin = member

    def open_guard(self, request, path):
        """The checks a route reachable before signing in still gets: the Host and the Origin (no sign-in yet)."""
        h = self.handler.__new__(self.handler)
        h.headers = http.client.parse_headers(io.BytesIO(b''.join((k + b': ' + v + b'\r\n' for k, v in request.headers.raw)) + b'\r\n'))
        h.command, h.path = (request.method, path)
        h.client_address = (request.client.host if request.client else '127.0.0.1', 0)
        h.server = SimpleNamespace(server_address=('127.0.0.1', self.port))
        why = h._foreign()
        return (403, {'error': why}) if why else None

    def error(self, request, e):
        return self.json(request, getattr(e, 'code', 400), {'error': str(e)})
