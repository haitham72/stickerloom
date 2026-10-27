"""The bank picker posts `from_request: 'reactions for <tokens>'`: the words must parse to token names."""
import unittest

from stickerloom.flow.particle_sets import request_elements


class ReactionRequestTests(unittest.TestCase):
    def test_reactions_wording_parses_to_token_names(self):
        self.assertEqual(request_elements("reactions for laugh, cry", 9), ["laugh", "cry"])
        self.assertEqual(request_elements("create reactions for school", 4), ["school"])

    def test_particles_wording_still_parses(self):
        self.assertEqual(request_elements("create particles for lipsticks and ribbons", 9), ["lipstick", "ribbon"])

    def test_bare_wording_still_asks(self):
        self.assertEqual(request_elements("create reactions", 4), [])
        self.assertEqual(request_elements("create particles", 4), [])


if __name__ == "__main__":
    unittest.main()
