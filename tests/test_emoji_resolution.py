"""Expanded bank regression: interpretation, exact sequences and both exports."""
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from stickerloom.generation import actions, emoji
from stickerloom.media import export_names as xn, export_resolutions as xr
from stickerloom.media.library import Library, LibraryError
from stickerloom.flow import batches
from tests.test_batch_zip import make_batch


class ResolutionTests(unittest.TestCase):
    def test_every_qualified_sequence_extracts_and_validates_whole(self):
        for glyph in emoji.SEQUENCES:
            self.assertEqual(xn.first_emoji(glyph + "x"), glyph)
            self.assertTrue(actions.is_ios_emoji(glyph))
        for glyph in ("😀\u200dx", "😀\u200d", "😀🏽", "👍🏽🏽", "🇦", "x", "/", "🏴\U000e0067"):
            self.assertFalse(actions.is_ios_emoji(glyph), repr(glyph))

    def test_ambiguous_alias_is_resolved_by_emoji_or_explicit_action(self):
        r = actions.resolve("x", ["cherry"])
        self.assertEqual(r["reason"], "ambiguous_alias")
        self.assertIn("cherries", r["candidates"])
        self.assertIn("cherry-blossom", r["candidates"])
        self.assertEqual(actions.resolve("x", ["cherry"], emoji="🍒")["hits"][0][0], "cherries")
        self.assertEqual(actions.resolve("x", ["cherry"], action="cherries")["reason"], None)
        self.assertEqual(actions.resolve("x", ["heart"])["hits"][0][0], "love")
        self.assertEqual(actions.resolve("bus_happy", ["cherries"])["hits"][0][0], "cherries")

    def test_limits_and_mood_queue_are_independent_of_subjects(self):
        self.assertEqual(len(actions.canonical_all("bus", ["happy", "laugh", "cry", "wink"])), 4)
        self.assertEqual(len(actions.canonical_all("bus", ["happy"], limit=1)), 1)
        self.assertEqual(actions.canonical_mood("bus_happy", ["bus"])[0], "happy")
        self.assertGreater(len(actions.next_tokens([], 2000)), 1800)

    def test_invalid_saved_primary_is_unresolved_and_never_becomes_a_path(self):
        args = dict(key="bus", tags=["bus"], slug="city", index=1, gid=1, date="20261011")
        for glyph in ("x", "/", "\\", "\n", "😀\u200dx"):
            d = xn.describe(emoji=glyph, **args)
            self.assertTrue(d["unresolved"])
            self.assertEqual(d["resolution_reason"], "invalid_emoji")
            self.assertTrue(d["stem"].startswith("🙂-"))
        self.assertEqual(xn.describe(emoji="", **args)["emoji"], "🚌")
        self.assertEqual(xn.describe(emoji="👍🏽", **args)["emoji"], "👍🏽")

    def test_unchanged_metadata_reuses_saved_resolution_and_edits_create_snapshots(self):
        args = dict(emoji="🚌", key="bus", tags=["bus", "happy"], slug="city", index=1, gid=1, date="20261011")
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            first = xr.describe(out, "G001/S1", **args)
            with mock.patch.object(xn, "describe", side_effect=AssertionError("must reuse snapshot")):
                self.assertEqual(first, xr.describe(out, "G001/S1", **args))
            changed = xr.describe(out, "G001/S1", **{**args, "action": "happy"})
            self.assertEqual(changed["action"], "happy")
            self.assertEqual(len(json.loads((out / "export_resolutions.json").read_text())["snapshots"]), 2)


class ExportTests(unittest.TestCase):
    def test_batch_and_library_export_full_flags_and_resolution_diagnostics(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            make_batch(out, 1)
            path = out / "G001" / "result.json"
            r = json.loads(path.read_text())
            flag = actions.EXTENDED_BANK["flag-england"]["emoji"]
            r["stickers"][0].update(key="flag-england", tags=["flag-england"], emoji=flag)
            r["stickers"][1].update(key="bus", tags=["bus"], emoji="x")
            path.write_text(json.dumps(r))
            lib = Library(out)
            pk = lib.create_pack("cat")["id"]
            for s in r["stickers"][:2]:
                lib.add_bytes(pk, b"png", "png", s["key"], "static", s["emoji"], {"generation": "G001", "index": s["index"]})
            for raw in (batches.export_zip(out, 1)[0], lib.export_zip(pk)[0]):
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    assets = json.loads(z.read("manifest.json"))["assets"]
                    self.assertEqual(assets[0]["emoji"], flag)
                    self.assertTrue(assets[0]["filename"].startswith(flag + "-"))
                    self.assertEqual(assets[1]["resolution_reason"], "invalid_emoji")
                    self.assertTrue(assets[1]["unresolved"])
                    for a in assets:
                        self.assertIn(a["filename"], z.namelist())
                        self.assertEqual(a["bank_version"], actions.BANK_VERSION)

    def test_explicit_library_action_is_saved_cleared_and_validated(self):
        with tempfile.TemporaryDirectory() as td:
            lib = Library(Path(td))
            pk = lib.create_pack("fruit")["id"]
            s = lib.add_bytes(pk, b"png", "png", "cherry", emoji="🍒")
            chosen = lib.rename_sticker(pk, s["id"], action="cherries")
            self.assertEqual(chosen["action"], "cherries")
            with self.assertRaises(LibraryError):
                lib.rename_sticker(pk, s["id"], action="made-up")
            self.assertNotIn("action", lib.rename_sticker(pk, s["id"], action=""))
