"""Bounded recovery after the judge (pure rules, no model, no spend): what SHOULD be regenerated, and when to stop.

It acts on VLM rejections and Python's sheet-level verdicts only. A human REJECT regenerates only when the human asks, and an
approved sticker is never touched. Nothing here calls a provider: the caller shows the price and a person confirms (Phase 2's
spend rules), so a plan is a recommendation with its numbers, not an action.

Rules (HANDOFF.md, "Bounded recovery"):
- more than 2 of a 9-cell sheet rejected (1 of a 4-cell sheet), or the sheet check failed, or Python could not cut the grid
  (`cut_clean` / `grid_detected`)  -> a NEW SHEET, at most 3 attempts; after the third the best attempt is kept as PARTIAL
  (FAILED when nothing is approved);
- 1-2 rejected                      -> only those cells again as 1x1 through the same engine, at most 2 attempts per cell;
- CHROMA_RISK from the judge, or Python's `chroma_risk` / `holes` above threshold -> ONE sheet with the other key colour,
  recorded; never twice."""
from __future__ import annotations

from dataclasses import dataclass, field

MAX_SHEET_ATTEMPTS = 3
MAX_CELL_ATTEMPTS = 2
SHEET_FAIL_PYTHON = {"cut_clean", "grid_detected"}
CHROMA_PYTHON = {"chroma_risk", "holes"}


@dataclass
class Plan:
    action: str                      # NONE | REGEN_CELLS | NEW_SHEET | SWITCH_KEY | KEEP_PARTIAL | FAILED
    cells: list = field(default_factory=list)
    reason: str = ""
    key_colour: str | None = None    # for SWITCH_KEY: the colour to use
    attempt: int = 0                 # the attempt this action would be (sheet attempt for NEW_SHEET / SWITCH_KEY, cell attempt max for REGEN_CELLS)
    gave_up: list = field(default_factory=list)   # rejected cells that ran out of attempts and stay rejected

    def to_dict(self) -> dict:
        return {"action": self.action, "cells": self.cells, "reason": self.reason, "key_colour": self.key_colour,
                "attempt": self.attempt, "gave_up": self.gave_up}


def other_key(colour: str) -> str:
    return "blue" if colour == "green" else "green"


def plan_recovery(n_cells: int, rejected: list, approved: list, *, sheet_ok: bool = True, reasons: dict | None = None,
                  python_flags: set | None = None, sheet_attempts: int = 1, cell_attempts: dict | None = None,
                  key_colour: str = "green", key_switched: bool = False) -> Plan:
    """`rejected` / `approved`: sticker indices the judge rejected / approved (human or Python rejections are not passed).
    `reasons`: {index: [codes]}. `python_flags`: names of failed Python checks at sheet level. `sheet_attempts`: sheets made so far
    for this request (1 = the first). `cell_attempts`: {index: single-cell regenerations already made}."""
    reasons = reasons or {}
    flags = set(python_flags or ())
    cell_attempts = cell_attempts or {}
    chroma = bool(flags & CHROMA_PYTHON) or any("CHROMA_RISK" in (reasons.get(i) or []) for i in rejected)
    # 1. a colour problem is fixed by the other key, once
    if chroma and not key_switched:
        if sheet_attempts >= MAX_SHEET_ATTEMPTS:
            return _stop(approved, "colour problem but the 3 sheet attempts are used")
        return Plan("SWITCH_KEY", reason="CHROMA_RISK: the subject fights the key colour", key_colour=other_key(key_colour),
                    attempt=sheet_attempts + 1)
    # 2. a broken sheet, or too many rejected, is a new sheet
    too_many = len(rejected) > (2 if n_cells >= 9 else max(1, n_cells // 4))
    if (not sheet_ok) or (flags & SHEET_FAIL_PYTHON) or too_many:
        why = ("the sheet check failed" if not sheet_ok else
               f"Python could not cut the grid ({', '.join(sorted(flags & SHEET_FAIL_PYTHON))})" if flags & SHEET_FAIL_PYTHON else
               f"{len(rejected)} of {n_cells} rejected")
        if sheet_attempts >= MAX_SHEET_ATTEMPTS:
            return _stop(approved, why + f" and the {MAX_SHEET_ATTEMPTS} sheet attempts are used")
        return Plan("NEW_SHEET", reason=why, attempt=sheet_attempts + 1)
    # 3. a few rejected: only those cells, bounded
    if rejected:
        todo = [i for i in rejected if cell_attempts.get(i, 0) < MAX_CELL_ATTEMPTS]
        gave_up = [i for i in rejected if i not in todo]
        if not todo:
            return Plan("KEEP_PARTIAL" if approved else "FAILED", reason="every rejected cell used its 2 attempts",
                        gave_up=gave_up)
        return Plan("REGEN_CELLS", cells=sorted(todo), reason=f"{len(rejected)} rejected", gave_up=gave_up,
                    attempt=max(cell_attempts.get(i, 0) for i in todo) + 1)
    return Plan("NONE", reason="nothing rejected")


def _stop(approved: list, why: str) -> Plan:
    return Plan("KEEP_PARTIAL" if approved else "FAILED", reason=why)
