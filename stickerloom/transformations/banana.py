"""The banana lexicon for `subject_as_target` ("dog as banana"): sharper wording for the three required cells and for the body. A flavour only changes words, never the rules
(the required cells, the "one character, not two things" sentence and the lint stay the generic template's)."""
from __future__ import annotations

FLAVOUR = {
    "id": "banana",
    "version": 1,
    "targets": ("banana", "bananas"),
    "body": "its whole body is one curved yellow banana (soft banana-yellow with a few brown freckles, the stem on top, a dark tip at the bottom, the peel as its skin)",
    "cells": {
        "dance": ("dancing", "dancing joyfully, the curved banana body swaying, peel flaps swinging like arms, eyes closed, big smile", "💃",
                  "the banana body sways and curves to a beat, the peel flaps swing, a little hop and a spin"),
        "shock": ("shocked", "shocked, the peel flaps slipping down in surprise, eyes huge, mouth wide open", "😱",
                  "the peel flaps slip down with a jolt, the eyes pop wide, the whole banana trembles"),
        "squash": ("squashed", "squashed flat like a cartoon, the banana body mushed wide with soft banana squeezing out at the ends, dazed face, stars circling", "🥴",
                   "squashes flat then springs back to its curved shape, dazed, stars orbit"),
    },
}
