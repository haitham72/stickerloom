"""Natural, spatially bound video directions. No model or provider calls."""
from __future__ import annotations

import re


FACE_GESTURES = {
    "sighs, a tear slides down, the face droops lower":
        "cries softly then starts sobbing in a cute way, finally wipes away the tear with a small detached hand",
    "tears stream in two arcs, the lip quivers, the face crumples":
        "cries with big streaming tears then sobs with a trembling lip, finally wipes the tears with a small detached hand",
}


def default_motion(motion: str, face: bool) -> str:
    """New face plans can use a detached gesture; saved slot text is never rewritten."""
    return FACE_GESTURES.get(motion, motion) if face else motion


def position(pos: int, rows: int, cols: int) -> str:
    if rows == cols == 1:
        return "Centre"
    row, col = divmod(pos - 1, cols)
    vertical = {1: ("Centre",), 2: ("Top", "Bottom"), 3: ("Top", "Mid", "Bottom")}[rows]
    horizontal = {1: ("centre",), 2: ("left", "right"), 3: ("left", "centre", "right")}[cols]
    return f"{vertical[row]}-{horizontal[col]}"


def motion_steps(motion: str, face: bool) -> list[str]:
    # "while" describes simultaneous detail within one beat. Never turn it into
    # a later action. Extra legacy clauses stay in the final beat, not discarded.
    parts = re.split(r"\s*(?:,\s*)?\b(?:and\s+)?(?:then|finally|next|after that)\b\s*|[;,]\s*",
                     str(motion or ""), flags=re.I)
    steps = [re.sub(r"^and\s+", "", part.strip(" .,;"), flags=re.I) for part in parts if part.strip(" .,;")]
    if not steps:
        steps = ["expresses its emotion clearly in place"]
    if len(steps) < 2:
        steps.append("lets the expression grow stronger with a small head tilt" if face
                     else "adds a playful expressive reaction and settles in place")
    if len(steps) > 3:
        steps = steps[:2] + [", and ".join(steps[2:])]
    return steps


def has_hand(motion: str) -> bool:
    positive = re.sub(r"\b(?:no|without|never)\s+(?:visible\s+)?hands?\b", "", str(motion), flags=re.I)
    return bool(re.search(r"\b(?:hands?|fingers?|palms?)\b", positive, re.I))


def face_policy(cells: list) -> str:
    if not any(has_hand(c.get("motion", "")) for c in cells):
        return " Faces only: no bodies, no hands."
    return (" Faces only: no bodies or attached limbs. Only a cell whose gesture explicitly calls for a hand may show a small detached hand; "
            "no visible arms or connection to the face. All other cells stay face-only.")


def narratives(cells: list, rows: int, cols: int, face: bool, loop: bool = False) -> str:
    lines = []
    for cell in cells:
        steps = motion_steps(cell.get("motion", ""), face)
        story = steps[0] + ", then " + steps[1]
        if len(steps) == 3:
            story += ", and finally " + steps[2]
        label = str(cell["label"]).strip(" .")
        first = steps[0].split()[0].lower()
        explicit_subject = re.match(r"^(?:the|its|a|an|eyes|tears|cheeks|eyebrows|steam|head|lips?|mouth|drool|sparkles|sunglasses)\b", steps[0], re.I)
        if first.endswith("s") and not explicit_subject:
            sentence = f"The {label} {story}."
        else:
            sentence = f"{label[:1].upper() + label[1:]}. {story[:1].upper() + story[1:]}."
        if face and has_hand(cell.get("motion", "")):
            sentence += (" The detached hand emerges from behind the face for the gesture and withdraws behind it afterwards, "
                         "with no visible arm connecting it to a body.")
        if loop:
            sentence += " It then returns smoothly to its exact first-frame expression and position."
        lines.append(f"{position(cell['pos'], rows, cols)} (cell {cell['pos']}): {sentence}")
    return "\n".join(lines)
