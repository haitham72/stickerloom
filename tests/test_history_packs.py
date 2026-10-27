"""Earlier batches groups the batches of one pack (flow/pipeline._packs) and drag and drop moves a batch between packs (flow/groups.set_pack). Pure: fake results."""
import unittest
from pathlib import Path
from unittest import mock

from stickerloom.flow import groups, pipeline as pl

H = 3600.0


class PackTests(unittest.TestCase):
    def setUp(self):
        self.disk = {}

        def add(n, prompt, created, preset=None, owner="local", **extra):
            self.disk[n] = {"number": n, "generation_id": f"G{n:03d}", "owner": owner, "prompt": prompt, "created": created,
                            "slots": {"preset": preset} if preset else {}, **extra}
        add(1, "generic emojis", 0)
        add(2, "generic emojis social-v1", 1 * H, "social-v1")
        add(3, "generic emojis core-v1", 2 * H, "core-v1")
        add(4, "a teddy bear", 2.5 * H)
        add(5, "generic emojis", 30 * H)                     # the same request a day later: another pack
        add(6, "generic emojis", 3 * H, owner="U002")        # another person: never mixed
        self.p = [mock.patch.object(pl, "read_result", lambda out, g: self.disk[int(g)]),
                  mock.patch.object(pl, "write_result", lambda out, g, r: self.disk.__setitem__(int(g), r)),
                  mock.patch.object(groups, "_alive", lambda out, g: int(g) in self.disk),
                  mock.patch.object(groups, "root_of", lambda out, g: int(g))]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()

    def packs(self):
        return {k: v for k, v in pl._packs(Path("."), {g: [g] for g in self.disk}).items()}

    def test_one_request_by_one_person_within_hours_is_one_pack_in_grid_order(self):
        p = self.packs()
        self.assertEqual(p[1], [3, 2, 1], "core-v1, social-v1, then the batch without a grid")
        self.assertEqual((p[4], p[5], p[6]), ([4], [5], [6]))

    def test_a_dropped_batch_joins_the_pack_and_taking_it_out_sticks(self):
        groups.set_pack(Path("."), 4, 2)                     # the teddy bear dropped on batch 2
        self.assertIn(4, self.packs()[1])
        groups.set_pack(Path("."), 1, None)                  # the pack's first batch taken out: a pack of its own, the others stay together
        p = self.packs()
        self.assertEqual(p[1], [1])
        self.assertEqual(sorted(next(v for v in p.values() if 2 in v)), [2, 3, 4])
        self.assertEqual(self.disk[1]["pack_history"][-1]["decision"], "UNPACK")
        with self.assertRaises(pl.PipelineError):
            groups.set_pack(Path("."), 2, 2)


if __name__ == "__main__":
    unittest.main()
