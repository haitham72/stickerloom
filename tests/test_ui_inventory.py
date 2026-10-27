"""The redesign's safety net.

tests/data/ui_baseline.json is the console as it was before the redesign: every action (`ACT.<name>=`), every screen (`RENDER.<name>=`) and every
server route the page names. While the screens are rebuilt, each of them must still exist, OR be listed in the same file under `renamed`
(old -> new, and the new one must exist) OR under `retired` (name -> why, with the owner's approval and the date). Nothing disappears silently.
The baseline is never regenerated during the redesign; only `renamed` and `retired` grow."""
import json
import unittest
from pathlib import Path

from stickerloom.console import inventory

BASE = json.loads((Path(__file__).resolve().parent / "data" / "ui_baseline.json").read_text(encoding="utf-8"))


class UIInventoryTests(unittest.TestCase):
    def check(self, kind, now):
        renamed, retired = BASE["renamed"].get(kind, {}), BASE["retired"].get(kind, {})
        lost = [x for x in BASE[kind] if x not in now and x not in renamed and x not in retired]
        self.assertEqual(lost, [], f"{kind} gone with no new home: add each to `renamed` (old -> new) or, with the owner's approval, to `retired` "
                                   f"in tests/data/ui_baseline.json")
        dangling = {old: new for old, new in renamed.items() if new not in now}
        self.assertEqual(dangling, {}, f"{kind} renamed to something that does not exist")
        for name, why in retired.items():
            self.assertTrue(str(why).strip(), f"{kind} {name} is retired without a reason")

    def test_every_shell_script_is_served(self):
        import re
        from stickerloom.console.server import UI_FILES
        shell = (inventory.UI / "index.html").read_text(encoding="utf-8")
        scripts = re.findall(r"<script src=/ui/([^>]+)>", shell)
        self.assertTrue(scripts)
        self.assertEqual([name for name in scripts if UI_FILES.get(name) != "text/javascript"], [])

    def test_no_action_is_lost(self):
        self.check("actions", {a["name"] for a in inventory.actions()})

    def test_no_screen_is_lost(self):
        self.check("screens", {s for s, _ in inventory.screens()})

    def test_no_server_route_the_page_used_is_dropped(self):
        self.check("routes", set(inventory.routes_called()))

    def test_the_inventory_reads_the_console(self):
        acts = inventory.actions()
        by = {a["name"]: a for a in acts}
        self.assertIn("/api/generations/{}/allow", by["gcell"]["routes"], "a helper's route is found (one call deep)")
        self.assertIn("studio-tiles.js", by["gcell"]["drawn_in"])
        self.assertIn(("home", "home.js"), inventory.screens())


if __name__ == "__main__":
    unittest.main()
