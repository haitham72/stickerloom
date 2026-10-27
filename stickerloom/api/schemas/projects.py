"""Projects HTTP schemas."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel


class MapVideo(BaseModel):
    """One animation of a sheet (result.json `video_sheets`); `used` = the one the stickers are cut from."""
    id: str
    status: str | None = None
    used: bool = False
    slots: int = 0
    thumb: str | None = None
    animated: int | None = None
    blocked: bool = False


class MapJob(BaseModel):
    id: str
    kind: str | None = None
    status: str


class MapSheet(BaseModel):
    """One try at a batch (a generation of the family)."""
    id: str
    number: int
    n: int
    label: str
    relation: str | None = None
    prompt: str = ""
    prompt_changed: bool = False
    status: Literal["making", "failed", "not_cut", "ready"]
    stage: str | None = None
    thumb: str | None = None
    counts: dict[str, int]
    videos: list[MapVideo]
    jobs: list[MapJob]
    created: float | None = None


class MapBatch(BaseModel):
    root: str
    title: str
    picked: str
    preset: str | None = None
    sheets: list[MapSheet]


class MapProject(BaseModel):
    id: str
    title: str
    request: str


class ProjectMap(BaseModel):
    """GET /api/generations/{id}/map (flow/projects.py): the project of a batch as one tree, Project > Batch > Sheet > Video."""
    project: MapProject
    current: str
    batches: list[MapBatch]
