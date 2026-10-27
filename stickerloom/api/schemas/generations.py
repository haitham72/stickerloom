"""Generations HTTP schemas."""
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, StrictBool


class ResizeBody(BaseModel):
    """POST /api/generations/{id}/resize: the batch's Resize box (flow/pipeline.set_resize). reslice: cut the animations again from the stored video (free)."""
    model_config = ConfigDict(extra="forbid")
    on: StrictBool
    reslice: StrictBool = False


class PositionBody(BaseModel):
    """POST /api/generations/{id}/position: one sticker's Position (flow/pipeline.set_position); offset [dx, dy] as a share of the canvas, null = centred
    as cut. scale (optional) sets its Size in the same step. reslice: cut that sticker again from the stored video (free)."""
    model_config = ConfigDict(extra="forbid")
    index: int
    offset: list[float] | None = None
    scale: float | None = None
    reslice: StrictBool = False
