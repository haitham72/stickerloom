"""FastAPI construction and server lifecycle; domain routes live in routers/."""
from __future__ import annotations
import asyncio
import socket
import threading
import uvicorn
from fastapi import FastAPI
from .dependencies import RequestContext
from . import legacy_adapter
from .routers import imports, reactions, generations, chat, telegram

def quiet_loop():
    """Event loop for `Server`: identical, except a client that vanishes mid-connection stays silent. On Windows every
    aborted keep-alive (a closed tab, a parallel fetch, antivirus) otherwise prints a ConnectionResetError traceback
    from the event loop; the disconnect itself is harmless and anything else still goes to the default handler."""
    loop = asyncio.new_event_loop()

    def _quiet(failed, context):
        if isinstance(context.get("exception"), (ConnectionResetError, BrokenPipeError)):
            return
        failed.default_exception_handler(context)

    loop.set_exception_handler(_quiet)
    return loop

def create_app(c, port: int, secure: bool = False) -> FastAPI:
    from ..console.server import make_handler
    app = FastAPI(title="stickerloom", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.console = c
    ctx = RequestContext(c, make_handler(c), port, secure)
    for domain in (imports, reactions, generations, chat, telegram):
        app.include_router(domain.build_router(ctx))
    app.include_router(legacy_adapter.build_router(ctx))
    return app

class Server:
    """uvicorn with the interface the rest of the code and the tests use of the stdlib server: `server_address`, `serve_forever()`, `shutdown()`, `server_close()`."""

    def __init__(self, c, host: str = "127.0.0.1", port: int = 8770, tls: dict | None = None):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.listen(128)
        self.server_address = self.sock.getsockname()[:2]
        tls = tls or {}
        self.app = create_app(c, self.server_address[1], secure=bool(tls))
        self.uv = uvicorn.Server(uvicorn.Config(self.app, log_level="warning", access_log=False, lifespan="off", timeout_keep_alive=5,
                                                timeout_graceful_shutdown=3,         # Ctrl+C: open streams (a tab's event feed) get 3 s, never a wait for ever
                                                loop="stickerloom.api.app:quiet_loop",
                                                ssl_certfile=tls.get("cert"), ssl_keyfile=tls.get("key")))
        self._stopped = threading.Event()

    def serve_forever(self, poll_interval: float | None = None) -> None:      # the stdlib signature (poll_interval is uvicorn's own business)
        try:
            self.uv.run(sockets=[self.sock])
        finally:
            self._stopped.set()

    def shutdown(self) -> None:
        self.uv.should_exit = True
        self._stopped.wait(10)

    def server_close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass
