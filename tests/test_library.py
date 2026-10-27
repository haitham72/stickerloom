import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from stickerloom.engine.config import EngineConfig
from stickerloom.runtime import names
from stickerloom.media.library import Library, LibraryError, cutout, decode_image, png_bytes
from tests import synth


def photo(bgcolor=(120, 90, 60), noise=6):
    """Non-green 'photo': brown noisy background, a yellow disc with a red core."""
    rng = np.random.default_rng(3)
    im = np.clip(np.array(bgcolor) + rng.normal(0, noise, (300, 400, 3)), 0, 255).astype(np.uint8)
    cv2.circle(im, (200, 150), 90, (250, 220, 20), -1, cv2.LINE_AA)
    cv2.circle(im, (200, 150), 30, (200, 30, 30), -1, cv2.LINE_AA)
    return im


def encode(rgb, fmt="png"):
    b = io.BytesIO(); Image.fromarray(rgb).save(b, fmt); return b.getvalue()


class LibraryTests(unittest.TestCase):
    def test_semantic_counts_preserve_items_and_follow_owner_visibility(self):
        from stickerloom.media.library import pack_counts
        for items, expected in [([], (0, 0)), ([{}], (1, 0)), ([{"source": {"particle_set": "P1"}}], (0, 1)), ([{}, {"source": {"particle_set": "P1"}}], (1, 1))]:
            counts = pack_counts({"stickers": items})
            self.assertEqual((counts["sticker_count"], counts["reaction_count"]), expected)
        own = self.lib.create_pack("Mine", owner="A")
        other = self.lib.create_pack("Other", owner="B")
        self.lib.add_bytes(own["id"], self.sticker_png(), "png", "sticker", "static", "🙂")
        self.lib.add_bytes(own["id"], self.sticker_png(), "png", "reaction", "static", "🙂", source={"particle_set": "P1"})
        self.lib.add_bytes(other["id"], self.sticker_png(), "png", "private", "static", "🙂")
        snap = self.lib.snapshot(owner="A")
        self.assertEqual([p["id"] for p in snap["packs"]], [own["id"]])
        self.assertEqual((snap["total"], snap["sticker_count"], snap["reaction_count"]), (2, 1, 1))
        self.assertEqual(len(snap["packs"][0]["stickers"]), 2)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.lib = Library(self.tmp); self.cfg = EngineConfig()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sticker_png(self, seed=0):
        rgba = np.zeros((512, 512, 4), np.uint8)
        cv2.circle(rgba, (256, 256), 150 + seed, (250, 200, 20, 255), -1, cv2.LINE_AA)
        return png_bytes(rgba)

    def test_cutout_methods(self):
        rgba, info = cutout(decode_image(encode(photo())), self.cfg, "grabcut")
        self.assertEqual(info["method"], "grabcut")
        a = rgba[..., 3]
        self.assertGreater((a > 127).mean(), 0.3)
        self.assertEqual(a[0, 0], 0)                      # background removed at the corner
        green = synth.make_sheet(seed=1)[:200, :200]
        rgba, info = cutout(decode_image(encode(green)), self.cfg)
        self.assertTrue(info["method"].startswith("chroma"))
        cut = np.zeros((100, 100, 4), np.uint8); cut[30:70, 30:70] = (255, 0, 0, 255)
        self.assertEqual(cutout(decode_image(png_bytes(cut)), self.cfg)[1]["method"], "existing_alpha")
        with self.assertRaises(LibraryError):
            cutout(decode_image(encode(np.full((200, 200, 3), 128, np.uint8))), self.cfg)

    def test_cutout_matte_and_forced_methods(self):
        from stickerloom.media import matte
        img = np.dstack([photo(), np.full(photo().shape[:2], 255, np.uint8)])
        with self.assertRaises(LibraryError):
            cutout(img, self.cfg, "bogus")
        _, info = cutout(img, self.cfg, "grabcut"); self.assertEqual(info["method"], "grabcut")
        if matte.status()["ok"]:
            out, info = cutout(img, self.cfg, "matte")
            self.assertTrue(info["method"].startswith("matte_")); self.assertTrue(0.03 < info["foreground"] < 0.9)
            self.assertLess(float((out[..., 3] > 127).mean()), 0.95)      # not a rectangle: background pixels are transparent
        else:
            with self.assertRaises(LibraryError):
                cutout(img, self.cfg, "matte")

    def test_merging_a_pack_upgrades_its_parents_stills_and_keeps_their_particles(self):
        a = self.lib.create_pack("Angel")["id"]; b = self.lib.create_pack("Angel 2")["id"]
        still = self.lib.add_bytes(a, b"png-bytes", "png", "Happy", "static", "😀", {"generation": "G001", "index": 1})
        db = self.lib._load(); db["packs"][0]["stickers"][0]["particles"] = ["P008"]; self.lib._save(db)       # a particle set points at the still
        anim = self.lib.add_bytes(b, b"webm-bytes", "webm", "Happy anim", "animated", "🙂", {"generation": "G001", "index": 1})
        extra = self.lib.add_bytes(b, b"webm-two", "webm", "Burst", "animated", "✨", {"particle_set": "P009"})
        r = self.lib.merge_pack(b, a)
        self.assertEqual((r["upgraded"], r["moved"], r["trashed"]), (1, 1, True))
        p = self.lib._pack(self.lib._load(), a)
        up = next(s for s in p["stickers"] if s["id"] == still["id"])
        self.assertEqual((up["type"], up["file"], up["name"], up["emoji"], up["particles"]), ("animated", anim["file"], "Happy", "😀", ["P008"]),
                         "the still keeps its id, name, emoji and particles and takes the animated file")
        self.assertIn(extra["id"], [s["id"] for s in p["stickers"]], "a sticker with no twin moves over")
        self.assertNotIn(b, [x["id"] for x in self.lib._load()["packs"]], "the emptied pack is in the trash")
        with self.assertRaises(LibraryError):
            self.lib.merge_pack(a, a)

    def test_packs_stickers_and_export(self):
        p = self.lib.create_pack("Summer Moments")
        for i in range(4):
            s = self.lib.add_render(p["id"], self.sticker_png(i), f"Sticker {i}", "🇦🇪", self.cfg)
        self.assertTrue(s["file"].startswith("own/img-sticker_3-custom-summer_moments-"), s["file"])           # files born in the library live in files/own/, named by names.py
        snap = self.lib.snapshot()
        self.assertEqual(len(snap["packs"][0]["stickers"]), 4); self.assertEqual(snap["packs"][0]["cover"], snap["packs"][0]["stickers"][0]["id"])
        ids = [x["id"] for x in snap["packs"][0]["stickers"]]
        self.lib.update_pack(p["id"], name="Renamed", order=ids[::-1], cover=ids[2])
        pk = self.lib.snapshot()["packs"][0]
        self.assertEqual([x["id"] for x in pk["stickers"]], ids[::-1]); self.assertEqual(pk["cover"], ids[2]); self.assertEqual(pk["name"], "Renamed")
        with self.assertRaises(LibraryError):
            self.lib.update_pack(p["id"], order=ids[:2])
        self.lib.delete_sticker(p["id"], ids[2])                       # deleting the cover promotes another sticker
        self.assertNotEqual(self.lib.snapshot()["packs"][0]["cover"], ids[2])
        self.lib.delete_sticker(p["id"], ids[0])
        self.lib.delete_pack(p["id"]); self.assertEqual(self.lib.snapshot()["packs"], [])
        self.assertEqual(len(list(self.lib.files.rglob("*.*"))), 2, "Delete pack is SOFT: the trashed pack keeps its two remaining files until it is purged (tests/test_purge.py)")
        self.lib.purge_pack(p["id"]); self.assertEqual(list(self.lib.files.rglob("*.*")), [])

    def test_a_pack_dropped_on_a_pack_becomes_a_sub_pack_and_nothing_moves(self):
        from stickerloom.flow import groups
        a = self.lib.create_pack("Faces")["id"]; b = self.lib.create_pack("Faces toon")["id"]; c = self.lib.create_pack("Faces flat")["id"]
        sb = self.lib.add_bytes(b, b"png-b", "png", "Happy", "static", "😀", {})
        self.lib.update_pack(b, parent=a); self.lib.update_pack(c, parent=b)                         # c under b under a: all three in a's group
        db = self.lib._load(); packs = db["packs"]
        self.assertEqual([s["id"] for s in self.lib._pack(db, b)["stickers"]], [sb["id"]], "a sub-pack keeps its own stickers")
        g = groups.pack_groups(Path(self.tmp), packs)
        self.assertEqual({g[a], g[b], g[c]}, {f"P-{a}"})
        self.assertEqual(groups.pack_leads(packs, g), {b}, "the newest target leads (b took c last)")
        with self.assertRaises(LibraryError):
            self.lib.update_pack(a, parent=c)                                                        # a loop is refused
        self.lib.update_pack(c, parent=""); self.lib.update_pack(b, parent="")                       # Take out
        self.assertEqual(groups.pack_groups(Path(self.tmp), self.lib._load()["packs"]), {})

    def test_move_sticker_between_packs(self):
        a = self.lib.create_pack("Old Pack")["id"]; b = self.lib.create_pack("New Pack")["id"]
        s1 = self.lib.add_render(a, self.sticker_png(0), "One", "🙂", self.cfg)
        s2 = self.lib.add_render(a, self.sticker_png(1), "Two", "🙂", self.cfg)
        m = self.lib.move_sticker(a, s1["id"], b)
        self.assertEqual(m["file"], s1["file"], "a move changes the pack in the library's data, never the file or its name")
        pk = {p["id"]: p for p in self.lib.snapshot()["packs"]}
        self.assertEqual([x["id"] for x in pk[a]["stickers"]], [s2["id"]]); self.assertEqual(pk[a]["cover"], s2["id"])
        self.assertEqual([x["id"] for x in pk[b]["stickers"]], [s1["id"]]); self.assertEqual(pk[b]["cover"], s1["id"])
        self.assertTrue((self.lib.files / m["file"]).is_file()); self.assertEqual(len(list(self.lib.files.rglob("*.*"))), 2)
        with self.assertRaises(LibraryError):
            self.lib.move_sticker(a, s1["id"], b)                     # no longer in pack a
        with self.assertRaises(LibraryError):
            self.lib.move_sticker(b, s1["id"], b)                     # same pack

    def test_bad_render(self):
        p = self.lib.create_pack("x")
        with self.assertRaises(LibraryError):
            self.lib.add_render(p["id"], png_bytes(np.zeros((256, 256, 4), np.uint8)), "a", "🙂", self.cfg)
        with self.assertRaises(LibraryError):
            self.lib.add_render(p["id"], png_bytes(np.zeros((512, 512, 4), np.uint8)), "a", "🙂", self.cfg)


class NamingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.lib = Library(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_export_zip_has_every_file_under_its_name_and_a_manifest(self):
        from stickerloom.media import export_names as xn
        with self.assertRaises(LibraryError):
            self.lib.export_zip(self.lib.create_pack("Empty")["id"])                                  # nothing to download yet
        pk = self.lib.create_pack("My Pack")["id"]
        self.lib.add_bytes(pk, b"png-bytes", "png", "still one", "static", "😀", {"generation": "G001", "index": 1})
        self.lib.add_bytes(pk, b"webm-bytes", "webm", "moving one", "animated", "🔥", {"generation": "G001", "index": 2})
        data, stem = self.lib.export_zip(pk)
        self.assertEqual(stem, "my_pack")
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            man = json.loads(z.read("manifest.json"))
            self.assertEqual(sorted(n.rsplit(".", 1)[-1] for n in names if n != "manifest.json"), ["png", "webm"])
            self.assertEqual(man["schema_version"], 1)
            self.assertEqual(man["pack"]["generations"], ["G001"])
            for n in names:
                if n == "manifest.json":
                    continue
                p = xn.parse(n)
                self.assertIsNotNone(p, f"{n} follows the export contract")
            self.assertEqual([(a["media"], a.get("unresolved")) for a in man["assets"]], [("static", True), ("video", True)])
            webm = next(a["filename"] for a in man["assets"] if a["media"] == "video")
            self.assertEqual(z.read(webm), b"webm-bytes")                                              # the file as stored, not re-encoded

    def test_export_zip_light_compresses_animations_and_records_quality(self):
        from stickerloom.engine.config import EngineConfig
        from stickerloom.engine.particles import to_webm
        frames = np.zeros((90, 512, 512, 4), np.uint8)
        for i in range(90):
            x, y = 100 + i * 3, 256 + int(100 * np.sin(i / 6))
            frames[i][:, :, :3] = (20, 30, 200)
            rr, cc = np.ogrid[:512, :512]
            frames[i][(rr - y) ** 2 + (cc - x) ** 2 < 60 ** 2] = (250, 240, 200, 255)
        webm = to_webm(frames, EngineConfig(), Path(self.tmp) / "src.webm")["bytes"]
        pk = self.lib.create_pack("Light")["id"]
        self.lib.add_bytes(pk, webm, "webm", "moving", "animated", "🔥", {"generation": "G001", "index": 1})
        data, _ = self.lib.export_zip(pk)                                                             # light is the default
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            row = json.loads(z.read("manifest.json"))["assets"][0]
            self.assertTrue(row.get("light"), row)
            self.assertEqual(row["frames"], 90, "every frame is kept")
            self.assertAlmostEqual(row["fps"], 30, delta=0.5, msg="at its own frame rate")
            self.assertLessEqual(row["crf"], 46, "quality is never traded past LIGHT_MAX_CRF")
            got = z.read(row["filename"])
            if row["fits"]:
                self.assertLessEqual(len(got), 64 * 1024, f"{len(got) // 1024}KB at crf{row['crf']}")
            self.assertEqual(row["kb"], max(1, len(got) // 1024))
            g = json.loads(z.read("manifest.json"))["grey"]                                             # its grey stand-in (no still on disk: the first frame)
            self.assertEqual([x["filename"] for x in g], [row["filename"].rsplit(".", 1)[0] + "_grey.png"], "beside its original, under its name")
            from PIL import Image
            im = Image.open(io.BytesIO(z.read(g[0]["filename"])))
            self.assertEqual((im.mode, im.size, im.getpalette()[:3]), ("P", (512, 512), [191, 191, 191]))
            self.assertEqual(max(im.info["transparency"]), 153, "40% transparent: a solid pixel is at 60%")
            self.assertLess(len(z.read(g[0]["filename"])), 6 * 1024)
        data, _ = self.lib.export_zip(pk, light=False)                                                # opt out: the stored file untouched
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            row = json.loads(z.read("manifest.json"))["assets"][0]
            self.assertNotIn("light", row)
            self.assertEqual(z.read(row["filename"]), webm)

    def test_a_short_animation_is_listed_and_retimed_to_3_s_on_request(self):
        from stickerloom.engine.config import EngineConfig
        from stickerloom.engine.particles import to_webm
        from stickerloom.engine import ffmpeg as ff
        frames = np.zeros((30, 512, 512, 4), np.uint8)                                              # 1 s at 30 fps
        for i in range(30):
            frames[i][200:300, 100 + i * 5:200 + i * 5] = (250, 200, 40, 255)
        webm = to_webm(frames, EngineConfig(), Path(self.tmp) / "short.webm")["bytes"]
        pk = self.lib.create_pack("Short")["id"]
        s = self.lib.add_bytes(pk, webm, "webm", "quick", "animated", "⚡", {})
        self.assertEqual([x["id"] for x in self.lib.short_animations(pk)], [s["id"]])
        for light in (False, True):
            data, _ = self.lib.export_zip(pk, light=light, retime=True)
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                row = json.loads(z.read("manifest.json"))["assets"][0]
                self.assertAlmostEqual(row["retimed_from"], 1.0, delta=0.1)
                out = Path(self.tmp) / f"out{int(light)}.webm"; out.write_bytes(z.read(row["filename"]))
                self.assertAlmostEqual(ff.probe(out, vp9_native=True)["duration"], 3.0, delta=0.15, msg=f"light={light}")
        data, _ = self.lib.export_zip(pk, light=False)                                              # keep their length: as stored
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            row = json.loads(z.read("manifest.json"))["assets"][0]
            self.assertNotIn("retimed_from", row)
            self.assertEqual(z.read(row["filename"]), webm)

    def test_readable_name_and_legacy_file_style_names(self):
        from stickerloom.media.library import readable_name
        self.assertEqual(readable_name("generic_emojis_laughing"), "Generic emojis laughing")
        pk = self.lib.create_pack("P")["id"]
        s = self.lib.add_bytes(pk, b"x", "png", "img-027-generic_emojis-generic_emojis_laughing", "static", "😂", {"generation": "G027", "index": 2})
        self.lib.add_bytes(pk, b"x", "png", "my own name", "static", "🙂", {"generation": "G027", "index": 3})
        got = {x["id"]: x for x in self.lib.snapshot()["packs"][0]["stickers"]}
        self.assertEqual((got[s["id"]]["name"], got[s["id"]]["file_name"]), ("Generic emojis laughing", "img-027-generic_emojis-generic_emojis_laughing"))
        self.assertNotIn("file_name", [x for x in got.values() if x["name"] == "my own name"][0])      # a name the user chose is never touched

    def test_animated_takes_the_stills_place(self):
        pk = self.lib.create_pack("P")["id"]
        a = self.lib.add_bytes(pk, b"a", "png", "a", "static", "🙂", {"generation": "G1", "index": 1})
        b = self.lib.add_bytes(pk, b"b", "png", "b", "static", "🙂", {"generation": "G1", "index": 2})
        c = self.lib.add_bytes(pk, b"c", "png", "c", "static", "🙂", {"generation": "G2", "index": 2})
        self.assertEqual([x["id"] for x in self.lib.static_twins(pk, "G1", [2])], [b["id"]])
        new = self.lib.add_bytes(pk, b"v", "webm", "b", "animated", "🙂", {"generation": "G1", "index": 2})
        self.assertEqual(self.lib.replace_static_with_animated(pk, "G1", [2]), 1)
        ids = [x["id"] for x in self.lib.snapshot()["packs"][0]["stickers"]]
        self.assertEqual(ids, [a["id"], new["id"], c["id"]])


class AnimateTests(unittest.TestCase):
    def setUp(self):
        from stickerloom.engine import ffmpeg as ff
        self.tmp = Path(tempfile.mkdtemp()); self.lib = Library(self.tmp); self.cfg = EngineConfig()
        frs = np.zeros((30, 512, 512, 4), np.uint8)
        for i in range(30):
            cv2.circle(frs[i], (100 + i * 8, 256), 60, (250, 120, 20, 255), -1, cv2.LINE_AA)
        p = self.tmp / "a.webm"; ff.encode_webm(frs, 30, 38, p)
        self.pack = self.lib.create_pack("Anim")["id"]
        self.sid = self.lib.add_bytes(self.pack, p.read_bytes(), "webm", "Bounce", "animated")["id"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_formats_trim_and_save(self):
        data, mime, ext, info = self.lib.animate(self.pack, self.sid, 0.0, 0.5, 15, "webp", True, self.cfg)
        self.assertEqual((data[:4], data[8:12], mime), (b"RIFF", b"WEBP", "image/webp")); self.assertEqual(info["frames"], 8)   # 0.5 s at 15 fps
        self.assertLessEqual(len(data), 500 * 1024)
        self.assertEqual(self.lib.animate(self.pack, self.sid, 0.2, 0.8, 10, "gif", True, self.cfg)[0][:6], b"GIF89a")
        new = self.lib.animate(self.pack, self.sid, 0.0, 0.6, 12, "webm", True, self.cfg, save=True, name="Short")
        self.assertEqual((new["type"], new["name"]), ("animated", "Short")); self.assertEqual(new["info"]["frames"], 7)
        self.assertEqual(len(self.lib.snapshot()["packs"][0]["stickers"]), 2)
        with self.assertRaises(LibraryError):
            self.lib.animate(self.pack, self.sid, 0.5, 0.52, 12, "webm", True, self.cfg)          # too short
        with self.assertRaises(LibraryError):
            self.lib.animate(self.pack, self.sid, 0, 1, 12, "webp", True, self.cfg, save=True)    # only webm can be saved


class BulkTests(unittest.TestCase):
    def test_move_many_stickers_at_once_all_or_nothing(self):
        """a drop or a button moved ONE sticker and the selection stayed on the rest."""
        tmp = Path(tempfile.mkdtemp())
        try:
            lib = Library(tmp)
            a, b, c = lib.create_pack("A")["id"], lib.create_pack("B")["id"], lib.create_pack("C")["id"]
            sa = [lib.add_bytes(a, b"x", "png", f"a{i}") for i in range(4)]
            sc = lib.add_bytes(c, b"x", "png", "c0")
            items = [{"pack_id": a, "id": s["id"]} for s in sa[:3]] + [{"pack_id": c, "id": sc["id"]}]       # a selection can span packs (My Stickers)
            r = lib.move_stickers(items, b)
            self.assertEqual((r["moved"], r["skipped"]), (4, 0))
            snap = {p["id"]: p for p in lib.snapshot()["packs"]}
            self.assertEqual([s["id"] for s in snap[b]["stickers"]], [s["id"] for s in sa[:3]] + [sc["id"]])
            self.assertEqual([s["id"] for s in snap[a]["stickers"]], [sa[3]["id"]])
            self.assertEqual(snap[c]["stickers"], [])
            self.assertIsNone(snap[c]["cover"])
            self.assertEqual(snap[a]["cover"], sa[3]["id"])                         # the cover that left is replaced
            self.assertEqual(snap[b]["cover"], sa[0]["id"])
            names = [s["file"] for s in snap[b]["stickers"]]
            self.assertEqual(len(set(names)), 4)
            self.assertTrue(all(n.startswith("own/img-") for n in names), names)                   # nothing was renamed or moved on disk
            self.assertEqual(sorted(p.relative_to(lib.files).as_posix() for p in lib.files.rglob("*.*")), sorted(names + [snap[a]["stickers"][0]["file"]]))
            # one unknown sticker refuses the whole batch and changes nothing
            before = json.dumps(lib.snapshot(), sort_keys=True)
            with self.assertRaises(LibraryError) as cm:
                lib.move_stickers([{"pack_id": a, "id": sa[3]["id"]}, {"pack_id": a, "id": "nope"}], c)
            self.assertEqual(cm.exception.code, 404)
            self.assertEqual(json.dumps(lib.snapshot(), sort_keys=True), before)
            with self.assertRaises(LibraryError):
                lib.move_stickers([{"pack_id": a, "id": sa[3]["id"]}], "no-such-pack")
            with self.assertRaises(LibraryError):
                lib.move_stickers([], b)                                              # nothing selected is not a move
            # stickers already in the target are skipped, not an error
            r = lib.move_stickers([{"pack_id": b, "id": sa[0]["id"]}, {"pack_id": a, "id": sa[3]["id"]}], b)
            self.assertEqual((r["moved"], r["skipped"]), (1, 1))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_delete_many_stickers_at_once(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            lib = Library(tmp)
            a, b = lib.create_pack("A")["id"], lib.create_pack("B")["id"]
            sa = [lib.add_bytes(a, b"x", "png", f"a{i}") for i in range(3)]
            sb = [lib.add_bytes(b, b"x", "png", f"b{i}") for i in range(2)]
            n = lib.delete_stickers([{"pack_id": a, "id": sa[0]["id"]}, {"pack_id": a, "id": sa[1]["id"]}, {"pack_id": b, "id": sb[0]["id"]},
                                     {"pack_id": b, "id": "nope"}, {"pack_id": "nope", "id": "x"}])
            self.assertEqual(n, 3)                                           # unknown ones are skipped, not errors
            snap = {p["id"]: p for p in lib.snapshot()["packs"]}
            self.assertEqual([s["id"] for s in snap[a]["stickers"]], [sa[2]["id"]])
            self.assertEqual(snap[a]["cover"], sa[2]["id"])                  # the cover that went is replaced
            self.assertEqual(len(list(lib.files.rglob("*.*"))), 2)           # and the files are gone
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class GroupingAndMigrationTests(unittest.TestCase):
    """2026-10-02: library files are grouped per batch and carry the one naming convention; an old flat library is regrouped once."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_generation_sticker_goes_to_its_batch_folder_with_a_traceable_name(self):
        lib = Library(self.tmp)
        pk = lib.create_pack("Gold Pack")["id"]
        s = lib.add_bytes(pk, b"x", "png", "Diving wing", "static", "x", {"generation": "G012", "index": 1}, gen_file_name="img-012-superhero_paris-superhero_diving_wing")
        self.assertTrue(s["file"].startswith("G012/img-superhero_paris-diving_wing-gold_pack-"), s["file"])
        p = names.parse(s["file"].split("/")[1])
        self.assertEqual((p["media"], p["subject"], p["action"], p["pack"], p["legacy"]), ("img", "superhero_paris", "diving_wing", "gold_pack", False))
        self.assertTrue((lib.files / s["file"]).is_file())
        t = lib.add_bytes(pk, b"y", "webm", "Diving wing", "animated", "x", {"generation": "G012", "index": 1}, gen_file_name="img-012-superhero_paris-superhero_diving_wing")
        self.assertTrue(t["file"].startswith("G012/vid-"))
        self.assertNotEqual(s["file"], t["file"])
        lib.delete_sticker(pk, s["id"]); lib.delete_sticker(pk, t["id"])
        self.assertFalse((lib.files / "G012").exists(), "a batch folder with nothing left in it is removed")

    def test_the_same_sticker_added_twice_never_shares_a_file(self):
        lib = Library(self.tmp)
        a, b = lib.create_pack("A")["id"], lib.create_pack("B")["id"]
        x = lib.add_bytes(a, b"x", "png", "Wave", "static", "x", {"generation": "G001", "index": 1}, gen_file_name="img-001-owl-owl_wave")
        y = lib.add_bytes(b, b"x", "png", "Wave", "static", "x", {"generation": "G001", "index": 1}, gen_file_name="img-001-owl-owl_wave")
        self.assertNotEqual(x["file"], y["file"])

    def _flat_library(self):
        """A library as it was before: one flat folder, counter names, a `next` per pack, no layout flag."""
        root = self.tmp / "library"
        (root / "files").mkdir(parents=True)
        for f in ("img-001-gold-diving.png", "vid-002-gold-diving.webm", "img-003-gold-my_photo.png"):
            (root / "files" / f).write_bytes(b"data-" + f.encode())
        db = {"packs": [{"id": "p1", "name": "Gold", "slug": "gold", "cover": "s1", "next": 4, "created": 1790000000.0, "stickers": [
            {"id": "s1", "name": "Diving", "file": "img-001-gold-diving.png", "type": "static", "emoji": "x", "kb": 1, "w": 512, "h": 512, "created": 1790000100.0,
             "source": {"generation": "G012", "index": 1}, "file_name": "img-012-superhero_paris-superhero_diving_wing"},
            {"id": "s2", "name": "Diving", "file": "vid-002-gold-diving.webm", "type": "animated", "emoji": "x", "kb": 1, "w": 512, "h": 512, "created": 1790000200.0,
             "source": {"generation": "G012", "index": 1}, "file_name": "img-012-superhero_paris-superhero_diving_wing"},
            {"id": "s3", "name": "My photo", "file": "img-003-gold-my_photo.png", "type": "static", "emoji": "x", "kb": 1, "w": 512, "h": 512, "created": 1790000300.0, "source": {}}]}]}
        (root / "library.json").write_text(json.dumps(db), encoding="utf-8")

    def test_a_flat_library_is_regrouped_and_renamed_once_and_nothing_is_lost(self):
        self._flat_library()
        lib = Library(self.tmp)
        snap = {s["id"]: s for p in lib.snapshot()["packs"] for s in p["stickers"]}
        self.assertTrue(snap["s1"]["file"].startswith("G012/img-superhero_paris-diving_wing-gold-20260921T"), snap["s1"]["file"])
        self.assertTrue(snap["s2"]["file"].startswith("G012/vid-"))
        self.assertTrue(snap["s3"]["file"].startswith("own/img-my_photo-custom-gold-"))
        self.assertEqual((lib.files / snap["s1"]["file"]).read_bytes(), b"data-img-001-gold-diving.png")                       # same bytes, new place
        self.assertEqual(sorted(p.name for p in lib.files.iterdir()), ["G012", "own"])                                           # the flat folder is empty of files
        raw = json.loads(lib.db_path.read_text(encoding="utf-8"))
        self.assertEqual(raw["layout"], 2)
        self.assertNotIn("next", raw["packs"][0])
        before = json.dumps(raw, sort_keys=True)
        Library(self.tmp)                                                                                                     # opening it again moves nothing
        self.assertEqual(json.dumps(json.loads(lib.db_path.read_text(encoding="utf-8")), sort_keys=True), before)
        self.assertEqual(lib.migrate_layout(), 0)

    def test_a_missing_file_does_not_stop_the_migration_of_the_others(self):
        self._flat_library()
        (self.tmp / "library" / "files" / "vid-002-gold-diving.webm").unlink()
        lib = Library(self.tmp)
        snap = {s["id"]: s for p in lib.snapshot()["packs"] for s in p["stickers"]}
        self.assertEqual(snap["s2"]["file"], "vid-002-gold-diving.webm")                                                        # left as it was
        self.assertTrue(snap["s1"]["file"].startswith("G012/"))



class PackDeleteKeepsOriginals(unittest.TestCase):
    """P3 of the UI/UX spec: deleting a pack removes only the library's copies. The batch a sticker came from (out/G###) and the pool are untouched, so the sticker stays reusable; a file two
    packs share stays for the other pack."""

    def test_the_batch_original_survives_a_pack_delete_and_a_shared_file_stays(self):
        import shutil
        import tempfile
        from pathlib import Path
        from stickerloom.engine.config import EngineConfig
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        lib = Library(tmp / "out")
        gdir = tmp / "out" / "G012" / "slices"
        gdir.mkdir(parents=True)
        (gdir / "S1.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        a, b = lib.create_pack("A")["id"], lib.create_pack("B")["id"]
        s = lib.add_bytes(a, (gdir / "S1.png").read_bytes(), "png", "Cape", "static", "🦸", source={"generation": "G012", "index": 1})
        only = lib.add_bytes(a, b"\x89PNG\r\n\x1a\n" + b"1" * 64, "png", "Editor", "static", "🙂")
        shared = lib.snapshot()["packs"][0]["stickers"][0]["file"]
        with lib.lock:                                                       # the same file also sits in pack B
            db = lib._load()
            db["packs"][1]["stickers"].append({**s, "id": "dup"})
            lib._save(db)
        lib.delete_pack(a)
        self.assertTrue((lib.files / only["file"]).is_file(), "Delete pack is SOFT: the pack's own files wait in the trash")
        with self.assertRaises(LibraryError) as ctx:
            lib.purge_pack(a)                                                # the shared file is refused in words until confirmed
        self.assertEqual(ctx.exception.code, 409)
        lib.purge_pack(a, confirm_shared=True)
        self.assertTrue((gdir / "S1.png").is_file(), "the batch original is never touched")
        self.assertTrue((lib.files / shared).is_file(), "a file another pack still uses stays")
        self.assertFalse((lib.files / only["file"]).exists(), "a sticker that existed only in the purged pack goes with it")
        self.assertEqual([p["id"] for p in lib.snapshot()["packs"]], [b])
