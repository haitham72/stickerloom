"""The table that proves every check of `verify.CATALOGUE` has BOTH a PASS and a FAIL fixture (HANDOFF: a check never seen to fail proves nothing).

`FIXTURES` maps (stage, check id) to two builders, one returning an input the check must PASS on and one returning an input it must FAIL on. `Coverage` is generated from
the catalogue: it fails for an id with no row (add the row, do not loosen the test), for a row whose id left the catalogue, and for a fixture that does not do what its name
says (the check not applicable, crashed, or failing at another severity than the catalogue declares). Each check runs on its own (`only`), so a fixture exercises exactly the
function it names. The smallest synthetic inputs, built the way tests/test_verify.py and tests/test_verify_gaps.py build theirs."""
import functools
import unittest
from types import SimpleNamespace

import cv2
import numpy as np

from stickerloom.engine import verify
from stickerloom.engine.config import EngineConfig
from tests import synth
from tests.test_verify import GRID9, build, disc_at, disc_sheet, frames_of, png

CFG = EngineConfig()
S = CFG.size


# ---------- builders ----------
def sheet_in(rgb=None, **kw):
    return {"rgb": disc_sheet(1200, GRID9, r=80) if rgb is None else rgb, "grid": (3, 3), **kw}


def solid(size=S, hole=False):
    a = np.zeros((size, size, 4), np.uint8)
    a[100:400, 100:400] = (200, 80, 60, 255)
    if hole:
        a[200:300, 200:300] = 0                      # 11% of the subject enclosed: a green part keyed away
    return a


def cell(bbox=(100, 100, 400, 400), fg_px=90_000, edge_px=0, rgba=None, index=1):
    """What the still checks read of a CellKey: index, bbox, fg_px, edge_px, keyed.t and keyed.rgba."""
    return SimpleNamespace(index=index, bbox=bbox, fg_px=fg_px, edge_px=edge_px, keyed=SimpleNamespace(t=40.0, rgba=rgba if rgba is not None else solid()))


def still_in(render=None, **kw):
    return {"cell": cell(), "render": render or (lambda: solid()), "encode": lambda img, cfg: (b"x" * 1024, "png"), **kw}


def blobs(*centres, r=40, size=300):
    a = np.zeros((size, size, 4), np.uint8)
    for cx, cy in centres:
        cv2.circle(a, (cx, cy), r, (250, 220, 20, 255), -1)
    return a


def hash_of(seed):
    return np.random.default_rng(seed).random(64) > 0.5


def two_discs(f, t):
    cv2.circle(f, (100, 100), 30, (250, 220, 20, 255), -1)
    cv2.circle(f, (170, 100), 14, (250, 220, 20, 255), -1)           # a second part that never enters the slot's core


def drifting():
    return frames_of(disc_at(lambda t: 100 - t * 4.5, lambda t: 100))     # slides left until it leaves its cell


def centred():
    return frames_of(disc_at(lambda t: 100, lambda t: 100))


def info(**kw):
    return {"codec": "vp9", "width": S, "height": S, "fps": 30.0, "duration": 2.9, "audio": False, "alpha_mode": "1", **kw}


def alpha_frames(lo, hi):
    f = np.full((3, 8, 8, 4), 128, np.uint8)
    f[0, 0, 0, 3], f[0, 1, 1, 3] = lo, hi
    return f


@functools.lru_cache(maxsize=None)
def good_sheet():
    return build()


def video_in(**kw):
    sheet, lay = good_sheet()
    small = cv2.resize(sheet, (600, 600), interpolation=cv2.INTER_AREA)
    inp = {"info": {"codec": "h264", "width": 600, "height": 600, "fps": 30.0, "duration": 2.0, "audio": False}, "first_frame": small,
           "layout": lay, "sheet": sheet, "blank_share": {5: 0.0, 6: 0.0}}
    inp.update(kw)
    return inp


def other_first_frame():
    other, _ = synth_other_sheet()
    return cv2.resize(other, (600, 600), interpolation=cv2.INTER_AREA)


@functools.lru_cache(maxsize=None)
def synth_other_sheet():
    from stickerloom.engine.video_sheet import build_video_sheet
    return build_video_sheet({i: synth.sticker_rgba("tri" if i % 2 else "wide") for i in range(1, 10)}, [1, 2, 3, 4, 7, 8, 9], CFG, (3, 3))


def video_sheet_in(sheet_lay, approved):
    sheet, lay = sheet_lay
    return {"sheet": sheet, "layout": lay, "approved": approved}


def st(key="a", emoji="x", kind="static", n=1000):
    return {"key": key, "emoji": emoji, "kind": kind, "bytes": n}


def tg_static(**kw):
    return {"kind": "static", "w": S, "h": S, "bytes": 100_000, "alpha": True, "emoji": ["x"], **kw}


def tg_set(**kw):
    return {"stickers": [st("a"), st("b")], "name": "pack_by_mybot", "bot": "mybot", "title": "Pack", **kw}


# ---------- the table: (stage, id) -> (PASS builder, FAIL builder) ----------
FIXTURES = {
    # sheet
    ("sheet", "sheet_decodes"): (lambda: {"data": png(disc_sheet(1200, GRID9, r=80))}, lambda: {"data": b"not an image"}),
    ("sheet", "sheet_size"): (lambda: sheet_in(), lambda: sheet_in(disc_sheet(600, [(x // 2, y // 2) for x, y in GRID9], r=40))),
    ("sheet", "background_is_key"): (lambda: sheet_in(), lambda: sheet_in(cv2.circle(np.full((1200, 1200, 3), 128, np.uint8), (600, 600), 200, synth.YELLOW, -1))),
    ("sheet", "grid_detected"): (lambda: sheet_in(), lambda: sheet_in(grid=(2, 2))),
    ("sheet", "cut_clean"): (lambda: sheet_in(), lambda: sheet_in(disc_sheet(1200, [(600, 600)], r=560))),
    ("sheet", "background_flat"): (lambda: sheet_in(), lambda: sheet_in(_gradient_sheet())),
    # still
    ("still", "blank_cell"): (lambda: still_in(), lambda: still_in(cell=cell(bbox=None, fg_px=0))),
    ("still", "edge_trimmed"): (lambda: still_in(plain=solid()[..., 3]), lambda: still_in(plain=np.full((S, S), 255, np.uint8))),
    ("still", "dimensions"): (lambda: still_in(), lambda: still_in(render=lambda: np.zeros((400, 400, 4), np.uint8))),
    ("still", "transparent_corners"): (lambda: still_in(), lambda: still_in(render=lambda: _with_corner(solid()))),
    ("still", "foreground"): (lambda: still_in(), lambda: still_in(render=lambda: np.zeros((S, S, 4), np.uint8))),
    ("still", "inside_cell"): (lambda: still_in(cell=cell(edge_px=0)), lambda: still_in(cell=cell(edge_px=CFG.edge_touch_px + 1))),
    ("still", "no_spill"): (lambda: still_in(_spill={"spill": 0, "n": 20000, "risk": 0.0}), lambda: still_in(_spill={"spill": 101, "n": 100000, "risk": 0.0})),
    ("still", "chroma_risk"): (lambda: still_in(_spill={"spill": 0, "n": 20000, "risk": 0.0}), lambda: still_in(_spill={"spill": 0, "n": 20000, "risk": 0.5})),
    ("still", "holes"): (lambda: still_in(), lambda: still_in(render=lambda: solid(hole=True))),
    ("still", "single_subject"): (lambda: still_in(cell=cell(bbox=(60, 60, 140, 140), rgba=blobs((100, 100)))),
                                  lambda: still_in(cell=cell(bbox=(60, 60, 240, 140), rgba=blobs((100, 100), (200, 100))))),
    ("still", "duplicate_cell"): (lambda: still_in(cell=cell(index=2), hashes={1: hash_of(1), 2: hash_of(2)}),
                                  lambda: still_in(cell=cell(index=2), hashes={1: hash_of(1), 2: hash_of(1)})),
    ("still", "static_file"): (lambda: still_in(), lambda: still_in(encode=lambda img, cfg: (b"x" * (CFG.static_max_bytes + 1), "webp"))),
    # slot
    ("slot", "inside_slot"): (lambda: {"slot_frames": centred()}, lambda: {"slot_frames": drifting()}),
    ("slot", "inside_frame"): (lambda: {"cell_frames": centred()}, lambda: {"cell_frames": drifting()}),
    ("slot", "cross_slot"): (lambda: {"slot_frames": centred()}, lambda: {"slot_frames": frames_of(two_discs)}),
    # anim
    ("anim", "size_budget"): (lambda: {"data": b"x" * 100_000, "metrics": {"crf": 30}}, lambda: {"data": b"x" * (CFG.video_max_bytes + 1), "metrics": {"crf": 40}}),
    ("anim", "codec_vp9"): (lambda: {"info": info()}, lambda: {"info": info(codec="h264")}),
    ("anim", "dimensions"): (lambda: {"info": info()}, lambda: {"info": info(width=480)}),
    ("anim", "fps"): (lambda: {"info": info()}, lambda: {"info": info(fps=60.0)}),
    ("anim", "duration"): (lambda: {"info": info()}, lambda: {"info": info(duration=4.0)}),
    ("anim", "no_audio"): (lambda: {"info": info()}, lambda: {"info": info(audio=True)}),
    ("anim", "alpha_mode_tag"): (lambda: {"info": info(alpha_mode=None), "info_native": {"alpha_mode": "1"}},
                                 lambda: {"info": info(alpha_mode=None), "info_native": {"alpha_mode": None}}),
    ("anim", "alpha_decoded"): (lambda: {"alpha": alpha_frames(0, 255)}, lambda: {"alpha": np.full((3, 8, 8, 4), 255, np.uint8)}),
    ("anim", "loop_seam"): (lambda: {"metrics": {"loop_seam": 5, "loop_limit": 12}}, lambda: {"metrics": {"loop_seam": 40, "loop_limit": 12}}),
    ("anim", "identity_kept"): (lambda: {"ref_alpha": synth.sticker_rgba("disc")[..., 3], "frames_out": np.stack([synth.sticker_rgba("disc")] * 3)},
                                lambda: {"ref_alpha": synth.sticker_rgba("disc")[..., 3], "frames_out": np.stack([synth.sticker_rgba("wide")] * 3)}),
    ("anim", "motion_present"): (lambda: {"metrics": {"motion": 4.0}}, lambda: {"metrics": {"motion": 0.0}}),
    ("anim", "alpha_stable"): (lambda: {"frames_out": np.stack([synth.sticker_rgba("disc")] * 6)}, lambda: {"frames_out": _pumping()}),
    ("anim", "sharpness"): (lambda: {"metrics": {"sharp_kept": 1.0}}, lambda: {"metrics": {"sharp_kept": 0.5}}),
    # video_sheet
    ("video_sheet", "slots_match_approved"): (lambda: video_sheet_in(good_sheet(), [1, 2, 3, 4, 7, 8, 9]), lambda: video_sheet_in(good_sheet(), [1, 2, 3, 4, 5, 7, 8, 9])),
    ("video_sheet", "no_outline_on_sheet"): (lambda: video_sheet_in(good_sheet(), [1, 2, 3, 4, 7, 8, 9]),
                                             lambda: video_sheet_in(build(ring=14), [1, 2, 3, 4, 7, 8, 9])),
    # video
    ("video", "video_decodes"): (lambda: video_in(), lambda: video_in(first_frame=None)),
    ("video", "video_specs"): (lambda: video_in(), lambda: video_in(info={"codec": "h264", "width": 600, "height": 600, "fps": 30.0, "duration": 0.4, "audio": False})),
    ("video", "layout_match"): (lambda: video_in(), lambda: video_in(first_frame=other_first_frame())),
    ("video", "blank_slots_stay_empty"): (lambda: video_in(), lambda: video_in(blank_share={5: 0.0, 6: 0.04})),
    # pack
    ("pack", "pack_limits"): (lambda: {"stickers": [st("a"), st("b", kind="animated", n=200 * 1024)]}, lambda: {"stickers": [st("a"), st("a")]}),
    # telegram
    ("telegram", "telegram_sticker"): (lambda: tg_static(), lambda: tg_static(w=513)),
    ("telegram", "telegram_stroke"): (lambda: tg_static(stroke=True), lambda: tg_static(stroke=False)),
    ("telegram_set", "telegram_set"): (lambda: tg_set(), lambda: tg_set(name="pack__bad")),
}


def _gradient_sheet():
    grad = disc_sheet(1200, GRID9, r=80)
    ramp = np.linspace(110, 255, 1200)[None, :]                    # a strong gradient screen: still keyable, but it keys worse
    grad[..., 1] = np.where(grad[..., 0] < 100, ramp.astype(np.uint8), grad[..., 1])
    return grad


def _with_corner(a):
    a[0, 0, 3] = 255
    return a


def _pumping():
    pump = np.stack([synth.sticker_rgba("disc") if t % 2 else np.zeros((512, 512, 4), np.uint8) for t in range(6)])
    pump[::2, 200:260, 200:260] = 255
    return pump


def catalogue_table():
    """(stage, id) -> declared severity, generated from the catalogue."""
    return {(stage, cid): sev for stage, rows in verify.CATALOGUE.items() for cid, sev, _fn, _gate in rows}


def one(stage, cid, inp):
    out = verify.run(stage, dict(inp), CFG, only=(cid,))
    return out[0] if out else None


class Coverage(unittest.TestCase):
    def test_every_check_of_the_catalogue_has_a_pass_and_a_fail_fixture(self):
        table = catalogue_table()
        self.assertEqual(sum(len(r) for r in verify.CATALOGUE.values()), len(table), "two checks of one stage share an id")
        missing = sorted(set(table) - set(FIXTURES))
        stale = sorted(set(FIXTURES) - set(table))
        self.assertEqual(missing, [], "a catalogue check with no PASS/FAIL row in FIXTURES (tests/test_verify_fixtures.py): add both fixtures, never loosen the check")
        self.assertEqual(stale, [], "a FIXTURES row for a check that left the catalogue")
        for key, pair in FIXTURES.items():
            self.assertEqual(len(pair), 2, key)
            self.assertTrue(all(callable(b) for b in pair), key)

    def test_each_pass_fixture_passes_and_each_fail_fixture_fails_at_the_declared_severity(self):
        for (stage, cid), declared in sorted(catalogue_table().items()):
            good, bad = FIXTURES[(stage, cid)]
            with self.subTest(check=f"{stage}.{cid}", fixture="PASS"):
                c = one(stage, cid, good())
                self.assertIsNotNone(c, "the PASS fixture made the check not applicable")
                self.assertTrue(c.ok, (c.note, c.detail))
            with self.subTest(check=f"{stage}.{cid}", fixture="FAIL"):
                c = one(stage, cid, bad())
                self.assertIsNotNone(c, "the FAIL fixture made the check not applicable")
                self.assertNotEqual(c.reason, "verifier_error", c.detail)       # a crash is not a failure of the check
                self.assertFalse(c.ok, "the FAIL fixture passed")
                self.assertEqual(c.severity, declared)


if __name__ == "__main__":
    unittest.main()
