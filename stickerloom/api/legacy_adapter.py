"""Legacy HTTP bridge. Preserve handler guards, bytes, streaming and Range."""
from __future__ import annotations
import asyncio
import http.client
import io
import queue
import socket
import threading
from types import SimpleNamespace
from fastapi import APIRouter, Request
from starlette.responses import StreamingResponse
_END = object()
_HOP = {b"server", b"date", b"connection", b"keep-alive", b"transfer-encoding"}

class _Pipe:
    """The handler's `wfile`: what it writes goes to the adapter; once the client is gone a write raises BrokenPipeError, so a long stream (SSE) stops."""

    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.closed = False

    def write(self, b) -> int:
        if self.closed:
            raise BrokenPipeError("the client went away")
        if b:
            self.q.put(bytes(b))
        return len(b)

    def flush(self) -> None:
        if self.closed:
            raise BrokenPipeError("the client went away")

def _handle(H, method: str, target: str, raw_headers: bytes, body: bytes, client: str, port: int, pipe: _Pipe) -> None:
    """Run the existing handler on one request without a socket: the same object the stdlib server builds, minus the connection."""
    try:                                                     # setup included: even a header line past the 64 KiB limit ends the request, never hangs it
        h = H.__new__(H)
        h.rfile, h.wfile = io.BytesIO(body), pipe
        h.client_address = (client, 0)
        h.server = SimpleNamespace(server_address=("127.0.0.1", port))
        h.command, h.path, h.request_version = method, target, "HTTP/1.1"
        h.requestline = f"{method} {target} HTTP/1.1"
        h.close_connection = True
        h.headers = http.client.parse_headers(io.BytesIO(raw_headers))
        fn = getattr(h, "do_" + method, None)
        if fn is None:
            h.send_error(501, f"Unsupported method ({method!r})")
        else:
            fn()
    except (BrokenPipeError, ConnectionError):
        pass
    finally:
        pipe.q.put(_END)

def _split_head(buf: bytes) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    status = int(lines[0].split(b" ", 2)[1])
    headers = []
    for ln in lines[1:]:
        k, _, v = ln.partition(b":")
        if k.strip().lower() not in _HOP:
            headers.append((k.strip(), v.strip()))              # the handler's own casing (Content-Type, Retry-After): the bytes stay the same
    return status, headers, rest

def _legacy_call(H, method: str, target: str, raw_headers: bytes, client: str, port: int) -> tuple[int, bytes]:
    """One in-process request through the original handler (the same guards: Host, sign-in, roles, ownership); the whole answer at once."""
    pipe = _Pipe()
    _handle(H, method, target, raw_headers, b"", client, port, pipe)
    buf = b""
    while True:
        chunk = pipe.q.get()
        if chunk is _END:
            break
        buf += chunk
    status, _, body = _split_head(buf)
    return status, body

def _identify(H, request, port: int, path: str):
    """The caller of a NATIVE route, checked by the original handler's own guard (Host and Origin): -> (principal, None) or (None, (status, body)).
    There is one local principal; nobody signs in."""
    raw_headers = b"".join(k + b": " + v + b"\r\n" for k, v in request.headers.raw) + b"\r\n"
    h = H.__new__(H)
    h.headers = http.client.parse_headers(io.BytesIO(raw_headers))
    h.command, h.path = request.method, path
    h.client_address = (request.client.host if request.client else "127.0.0.1", 0)
    h.server = SimpleNamespace(server_address=("127.0.0.1", port))
    why = h._foreign()
    if why:
        return None, (403, {"error": why})
    return h._who(path), None

def build_router(ctx):
    router = APIRouter()
    H, port = ctx.handler, ctx.port
    @router.api_route("/{rest:path}", methods=["GET", "POST", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"], include_in_schema=False)
    async def legacy(request: Request, rest: str):
        raw_path = request.scope.get("raw_path") or request.url.path.encode()
        qs = request.scope.get("query_string") or b""
        target = raw_path.decode("latin-1") + ("?" + qs.decode("latin-1") if qs else "")
        raw_headers = b"".join(k + b": " + v + b"\r\n" for k, v in request.headers.raw) + b"\r\n"
        body = await request.body()
        pipe = _Pipe()
        threading.Thread(target=_handle, args=(H, request.method, target, raw_headers, body, request.client.host if request.client else "127.0.0.1", port, pipe),
                         daemon=True, name="stickerloom-request").start()
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = await asyncio.to_thread(pipe.q.get)
            if chunk is _END:
                break
            buf += chunk
        try:
            status, headers, first = _split_head(buf) if b"\r\n\r\n" in buf else (500, [(b"content-type", b"application/json; charset=utf-8")], b'{"error": "internal error"}')
        except ValueError:
            status, headers, first = 500, [(b"content-type", b"application/json; charset=utf-8")], b'{"error": "internal error"}'  # garbage where the status line goes: a 500, like no head at all
        done = b"\r\n\r\n" not in buf

        async def stream():
            try:
                if first:
                    yield first
                if done:
                    return
                while True:
                    chunk = await asyncio.to_thread(pipe.q.get)
                    if chunk is _END:
                        return
                    yield chunk
            finally:
                pipe.closed = True

        resp = StreamingResponse(stream(), status_code=status)
        resp.raw_headers = headers
        return resp
    return router
