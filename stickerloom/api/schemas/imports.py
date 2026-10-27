"""Imports HTTP schemas."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class ImportOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grid: Literal["2x2", "3x3"] | None = None
    name: str = Field(default="import.png", max_length=200)
    prompt: str = Field(default="", max_length=2000)
    generation: str | int | None = None
    sheet: str | None = None
    retry: bool = False
    again: bool = False                    # deliberately import bytes already in the ledger once more
    grid_ok: bool = False                  # explicit consent to cut a mismatched video with the destination grid
    job: str | None = Field(default=None, max_length=16)          # the failed local job (J023) this file completes, the "Is this the result of …?" choice
    as_new: bool = False                                          # import as a new batch even when a failed job holds this file's ticket
    batch: str | int | None = None


class ProviderImport(ImportOptions):
    id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
    local_job: str | None = Field(default=None, max_length=16)


class ImportResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int | None = None
    duplicate: bool | None = None
    kind: Literal["sheet", "video"] | None = None
    generation: str | None = None
    recoverable: bool | None = None
