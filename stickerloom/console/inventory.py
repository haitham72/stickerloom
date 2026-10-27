"""What the browser sandbox can DO, read from its own scripts: every action (`ACT.<name>=` in console/*.js), the
scripts that draw a button for it (`data-act=<name>`), and the server routes its code names (`/api/...`, its own and its helpers' two calls deep, not into the helpers that only redraw a screen). Pure: reads files, writes nothing.

Written for the redesign, so that no logic is lost: tests/test_ui_inventory.py
holds today's list as a baseline, so an action can only disappear from the console when the plan says where it went or the owner retired it.
`python -m stickerloom.console.inventory` prints the action/screen/route tables; `--baseline` prints a fresh baseline (empty renamed / retired)."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from .assets import script_sources

UI = Path(__file__).resolve().parent
_DEF = re.compile(r"\bACT\.([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)")
_USE = re.compile(r"data-act=[\"']?([A-Za-z_][A-Za-z0-9_]*)")
_ROUTE = re.compile(r"[`'\"](/api/[^`'\"\s]*)")
_TOP = re.compile(r"^(?:\S)")             # a line that starts a new top-level statement
_FN = re.compile(r"^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(|^\s*(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>")
_CALL = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\(")
DEPTH = 2                                 # helper calls followed from an action's own code
_REDRAW = re.compile(r"^(draw|render|paint|redraw|refresh|repaint|gview|gbody|load|auto|apply|sync|tick|poll)", re.I)   # reads that redraw a screen: not followed


def _norm(route: str) -> str:
    """`/api/generations/${id}/allow` -> `/api/generations/{}/allow`; a query string is dropped."""
    route = re.sub(r"\$\{[^}]*\}", "{}", route.split("?")[0])
    return re.sub(r"\{\}[^/]*", "{}", route).rstrip("/") or route


def _bodies(text: str):
    """(name, line number, the code of its statement) for every ACT.<name>= in one script. A statement runs to the next definition on the
    same line, or to the next line that starts at column 0 (the console's style: a handler's continuation lines are indented)."""
    lines = text.split("\n")
    for n, line in enumerate(lines):
        defs = list(_DEF.finditer(line))
        for k, m in enumerate(defs):
            end = defs[k + 1].start() if k + 1 < len(defs) else len(line)
            body = [line[m.start():end]]
            if k + 1 == len(defs):
                for nxt in lines[n + 1:]:
                    if nxt.strip() and len(nxt)-len(nxt.lstrip()) <= len(line)-len(line.lstrip()) and not nxt.lstrip().startswith("}"):
                        break
                    body.append(nxt)
            yield m.group(1), n + 1, "\n".join(body)


def _helpers(texts: dict[str, str]) -> dict[str, str]:
    """name -> code of every top-level function (`function f(`, `const f=(...)=>`) in the scripts; the first definition wins."""
    out: dict[str, str] = {}
    for t in texts.values():
        lines = t.split("\n")
        for n, line in enumerate(lines):
            m = _FN.match(line)
            if not m:
                continue
            body = [line]
            for nxt in lines[n + 1:]:
                if nxt.strip() and len(nxt)-len(nxt.lstrip()) <= len(line)-len(line.lstrip()) and not nxt.lstrip().startswith("}"):
                    break
                body.append(nxt)
            out.setdefault(m.group(1) or m.group(2), "\n".join(body))
    return out


def _routes(body: str, helpers: dict[str, str], depth: int = DEPTH) -> set[str]:
    found, seen, todo = set(), set(), [body]
    for _ in range(depth + 1):
        nxt = []
        for code in todo:
            found |= {_norm(r) for r in _ROUTE.findall(code)}
            for name in _CALL.findall(code):
                if name in helpers and name not in seen and name not in ("api", "post") and not _REDRAW.match(name):
                    seen.add(name)
                    nxt.append(helpers[name])
        todo = nxt
    return found


def _sources(ui: Path) -> dict[str, str]:
    if ui == UI:
        return dict(sorted(script_sources().items()))
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(ui.glob("*.js"))}


def actions(ui: Path = UI) -> list[dict]:
    """Every action, sorted by file then line: {name, file, line, drawn_in: [files], routes: [routes]}. A name defined twice is listed twice
    (tests/test_js.py says which double definitions are intentional)."""
    texts = _sources(ui)
    drawn: dict[str, set[str]] = {}
    for f, t in texts.items():
        for m in _USE.finditer(t):
            drawn.setdefault(m.group(1), set()).add(f)
    helpers, out = _helpers(texts), []
    for f, t in texts.items():
        for name, line, body in _bodies(t):
            routes = sorted(_routes(body, helpers))
            out.append({"name": name, "file": f, "line": line, "drawn_in": sorted(drawn.get(name, ())), "routes": routes})
    return out


def screens(ui: Path = UI) -> list[tuple[str, str]]:
    """(screen, file) for every `RENDER.<screen>=`: the routes `#/<screen>` the router can show."""
    out = []
    for name, text in _sources(ui).items():
        out += [(m.group(1), name) for m in re.finditer(r"\bRENDER\.([A-Za-z_]\w*)\s*=(?!=)", text)]
    return out


def routes_called(ui: Path = UI) -> list[str]:
    """Every server route named anywhere in the console's scripts (normalised): what the page needs the API to keep answering."""
    found = set()
    for text in _sources(ui).values():
        found |= {_norm(r) for r in _ROUTE.findall(text)}
    return sorted(found)


def baseline(ui: Path = UI) -> dict:
    """What tests/data/ui_baseline.json records: the action names, the screens and the routes of the console as it is."""
    return {"actions": sorted({a["name"] for a in actions(ui)}), "screens": sorted({s for s, _ in screens(ui)}), "routes": routes_called(ui)}


def markdown(acts: list[dict]) -> str:
    rows, cur = [], None
    for a in acts:
        if a["file"] != cur:
            cur = a["file"]
            n = sum(1 for x in acts if x["file"] == cur)
            rows += ["", f"### `{cur}` ({n})", "", "| action | line | button drawn in | server routes (its code + its helpers, 2 deep) |", "|---|---|---|---|"]
        drawn = ", ".join(a["drawn_in"]) or "(no `data-act` button: called from code, a key, or a form)"
        routes = "<br>".join(f"`{r}`" for r in a["routes"]) or "none (browser only)"
        rows.append(f"| `{a['name']}` | {a['line']} | {drawn} | {routes} |")
    return "\n".join(rows).lstrip("\n") + "\n"


if __name__ == "__main__":
    if sys.argv[1:] == ["--baseline"]:
        import json
        sys.stdout.write(json.dumps({**baseline(), "renamed": {k: {} for k in ("actions", "screens", "routes")}, "retired": {k: {} for k in ("actions", "screens", "routes")}}, indent=1, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(markdown(actions()))
