"""Generations native HTTP routes."""
from __future__ import annotations
import asyncio
from fastapi import APIRouter, Request
from ..dependencies import _native_headers
from ...flow import pipeline as pl, gates as _gates, projects as _projects
from ..schemas.generations import PositionBody, ResizeBody
from ..schemas.projects import ProjectMap

def build_router(ctx):
    router = APIRouter()
    c = ctx.console
    _j = ctx.json
    _who = ctx.who
    _body = ctx.body
    _member = ctx.member

    async def _own_gen(request, gid, path):
        user, resp = await _member(request, path)
        if resp:
            return None, resp
        try:
            n = int(str(gid).upper().lstrip("G"))
        except ValueError:
            return None, _j(request, 404, {"error": "not found"})
        if not c.visible(user, n) or not pl.gen_dir(c.out, n).is_dir():
            return None, _j(request, 404, {"error": "not found"})
        if c.lock.locked():
            return None, _j(request, 409, {"error": "busy: a job is running, wait for it to finish"})
        return n, None

    def _recut(n, only=None):
        if _gates.can_reslice(pl.read_result(c.out, n)):
            c.submit(lambda: _gates.reslice(c.out, n, pl.cfg_for(pl.read_result(c.out, n), c.cfg), c.pace, only=only))
            return True
        return False

    @router.post("/api/generations/{gid}/resize", include_in_schema=False)
    async def generation_resize(request: Request, gid: str):
        n, resp = await _own_gen(request, gid, "/api/generations/resize")
        if resp:
            return resp
        body, err = await _body(request, ResizeBody)
        if err:
            return _j(request, 400, {"error": err})
        try:
            out = await asyncio.to_thread(pl.set_resize, c.out, n, body.on)
            if body.reslice:
                out["resliced"] = _recut(n)
            return _j(request, 200, out)
        except pl.PipelineError as e:
            return _j(request, e.code, {"error": str(e)})

    @router.post("/api/generations/{gid}/position", include_in_schema=False)
    async def generation_position(request: Request, gid: str):
        n, resp = await _own_gen(request, gid, "/api/generations/position")
        if resp:
            return resp
        body, err = await _body(request, PositionBody)
        if err:
            return _j(request, 400, {"error": err})
        try:
            out = await asyncio.to_thread(pl.set_position, c.out, n, body.index, body.offset)
            if body.scale is not None:
                out["anim_scale"] = (await asyncio.to_thread(pl.set_video_size, c.out, n, body.scale, body.index))["stickers"].get(body.index)
            if body.reslice:
                out["resliced"] = _recut(n, [body.index])
            return _j(request, 200, out)
        except pl.PipelineError as e:
            return _j(request, e.code, {"error": str(e)})

    @router.get("/api/generations/{gid}/map", include_in_schema=False)
    @router.get("/api/v1/generations/{gid}/map", include_in_schema=False)
    async def project_map(request: Request, gid: str):
        user, resp = await _member(request, f"/api/generations/{gid}/map")
        if resp:
            return resp
        num = gid.upper().lstrip("G")
        if not num.isdigit() or not await asyncio.to_thread(c.visible, user, int(num)):
            return _j(request, 404, {"error": "not found"})
        try:
            m = await asyncio.to_thread(_projects.project_map, c.out, int(num), lambda g: c.visible(user, g))
        except (FileNotFoundError, KeyError, ValueError, pl.PipelineError):
            return _j(request, 404, {"error": "not found"})
        return _j(request, 200, ProjectMap.model_validate(m).model_dump())

    @router.get("/api/generations/{gid}/export.zip", include_in_schema=False)
    @router.get("/api/v1/generations/{gid}/export.zip", include_in_schema=False)
    async def generation_zip(request: Request, gid: str):
        """The Studio's Download .zip: the batch's accepted stickers (animated where ready) and a manifest. The owner, or the batch's own person; a stranger's is a 404."""
        user, err = await _who(request, f"/api/generations/{gid}/export.zip")
        if err:
            return _j(request, *err)
        from starlette.responses import Response
        from ...flow import batches as _batches
        from ...flow.pipeline import PipelineError
        try:
            n = int(str(gid).upper().lstrip("G"))
            if not c.visible(user, n):
                raise KeyError(gid)
            data, stem = await asyncio.to_thread(_batches.export_zip, c.out, n, request.query_params.get("light", "1") != "0", request.query_params.get("retime") == "1")
        except (KeyError, ValueError, FileNotFoundError, PipelineError) as e:                # PipelineError: no such batch
            missing = isinstance(e, (KeyError, FileNotFoundError, PipelineError)) or "invalid literal" in str(e)
            return _j(request, 404 if missing else 409, {"error": "not found" if missing else str(e)})
        return Response(data, media_type="application/zip", headers={**_native_headers(request), "Content-Disposition": f'attachment; filename="{stem}.zip"'})
    return router
