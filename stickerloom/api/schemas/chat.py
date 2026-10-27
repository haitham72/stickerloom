"""Chat HTTP schemas."""
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict


class ChatTurnEvent(BaseModel):
    """`event: turn` of GET /api/chat/sessions/{id}/stream: the turn in progress, sent each time it changes (the step trace grows, a card arrives, the reply lands)."""
    model_config = ConfigDict(extra="forbid")
    working: bool
    count: int                              # how many messages the session holds now
    message: dict[str, Any] | None


class ChatDoneEvent(BaseModel):
    """`event: done`: the turn has finished; the page reads the whole session once."""
    model_config = ConfigDict(extra="forbid")
    working: Literal[False] = False
    count: int
