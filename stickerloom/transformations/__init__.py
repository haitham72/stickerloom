"""Transformation templates ("dog as banana"): see base.py for what counts as one and registry.py for how a plan is built."""
from .base import Match, Transformation, detect
from .registry import FLAVOURS, TEMPLATES, flavour_for, get, plan, signature

__all__ = ["Match", "Transformation", "detect", "plan", "get", "flavour_for", "signature", "TEMPLATES", "FLAVOURS"]
