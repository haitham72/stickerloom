"""The style tiles of the Studio: every preset has an honest tile (an abstract swatch, not a picture of something it is not), and a preset added tomorrow gets one with no UI change."""
import unittest
import xml.dom.minidom

from stickerloom.console import placeholders
from stickerloom.generation import styles


class StyleTileTests(unittest.TestCase):
    def test_every_preset_has_a_swatch_that_is_valid_svg(self):
        for s in styles.PRESETS:
            svg = placeholders.style_svg(s["id"])
            xml.dom.minidom.parseString(svg)
            self.assertIn("<svg", svg)

    def test_the_swatches_differ_and_none_is_a_teddy(self):
        arts = {s["id"]: placeholders.style_svg(s["id"]) for s in styles.PRESETS}
        self.assertEqual(len(set(arts.values())), len(arts), "two presets never share a tile")
        for sid, art in arts.items():
            self.assertNotIn('r="26"', art, f"{sid}: the old teddy ears are gone")

    def test_a_preset_with_no_art_still_gets_its_own_tile(self):
        a, b = placeholders.style_svg("brand_new_style"), placeholders.style_svg("another_one")
        xml.dom.minidom.parseString(a)
        self.assertNotEqual(a, b, "the colour is derived from the id")
        self.assertEqual(a, placeholders.style_svg("brand_new_style"), "and it is stable")

    def test_find_serves_the_swatch_for_a_style_without_a_file(self):
        body, ctype = placeholders.find("styles", "pixel_art")
        self.assertEqual(ctype, "image/svg+xml")
        self.assertIn(b"<svg", body)


if __name__ == "__main__":
    unittest.main()
