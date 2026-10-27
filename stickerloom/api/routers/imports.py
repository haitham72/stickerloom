"""Imports native HTTP routes."""
from __future__ import annotations
import asyncio
from fastapi import APIRouter, Request
from pydantic import ValidationError
from ...flow import imports as im, pipeline as pl
from ...media.video_project import MAX_UPLOAD
from ..schemas.imports import ImportOptions, ProviderImport, ImportResult

def build_router(ctx):
    router = APIRouter()
    c = ctx.console
    _j = ctx.json
    _who = ctx.who
    _body = ctx.body

    async def _import_owner(request):
        user, err = await _who(request, "/api/import")
        if err:
            return None, _j(request, *err)
        if user.get("role") != "owner" or user.get("status") == "pending":
            return None, _j(request, 403, {"error": "this account cannot do that (owner only)"})
        return user, None

    @router.post("/api/import", include_in_schema=False)
    @router.post("/api/v1/import", include_in_schema=False)
    async def import_upload(request: Request):
        user, resp = await _import_owner(request)
        if resp:
            return resp
        try:
            options = ImportOptions.model_validate(dict(request.query_params))
            data = bytearray()
            async for chunk in request.stream():
                if len(data) + len(chunk) > MAX_UPLOAD:
                    return _j(request, 413, {"error": "file too large"})
                data.extend(chunk)
            status, body = await asyncio.to_thread(im.import_file, c, user, data=bytes(data), **options.model_dump())
            ImportResult.model_validate(body)
            return _j(request, status, body)
        except ValidationError as e:
            return _j(request, 400, {"error": "bad request: " + e.errors()[0]["msg"]})
        except (im.ImportError, pl.PipelineError) as e:
            return _j(request, e.code, {"error": str(e), **getattr(e, "hint", {})})

    @router.get("/api/higgsfield/history", include_in_schema=False)
    @router.get("/api/v1/higgsfield/history", include_in_schema=False)
    async def import_history(request: Request):
        _, resp = await _import_owner(request)
        if resp:
            return resp
        try:
            return _j(request, 200, await asyncio.to_thread(im.history, c, int(request.query_params.get("size", "40"))))
        except ValueError:
            return _j(request, 400, {"error": "size must be 1 to 100"})
        except im.ImportError as e:
            return _j(request, e.code, {"error": str(e)})

    @router.post("/api/higgsfield/import", include_in_schema=False)
    @router.post("/api/v1/higgsfield/import", include_in_schema=False)
    async def import_provider(request: Request):
        user, resp = await _import_owner(request)
        if resp:
            return resp
        body, bad = await _body(request, ProviderImport)
        if bad:
            return _j(request, 400, {"error": bad})
        try:
            options = body.model_dump(exclude={"id", "name", "job", "as_new"}, exclude_none=True)
            status, result = await asyncio.to_thread(im.import_job, c, user, body.id, **options)
            ImportResult.model_validate(result)
            return _j(request, status, result)
        except (im.ImportError, pl.PipelineError) as e:
            return _j(request, e.code, {"error": str(e), **getattr(e, "hint", {})})
    return router
