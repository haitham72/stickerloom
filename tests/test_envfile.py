""".env is read like a dotenv file: a trailing ` # comment` is not part of the value (the chat's provider used to become 'auto   # the chat assistant; ...')."""
import os
import unittest
from unittest import mock

from stickerloom.runtime import envfile


class EnvFileTests(unittest.TestCase):
    def test_a_trailing_comment_is_not_the_value(self):
        got = envfile.parse("STICKERLOOM_AGENT_PROVIDER=auto          # the chat assistant;  STICKERLOOM_VISION_PROVIDER=auto  the vision judge\n"
                            "STICKERLOOM_LLM_PROVIDER=auto            # local | openai | auto\n")
        self.assertEqual(got, {"STICKERLOOM_AGENT_PROVIDER": "auto", "STICKERLOOM_LLM_PROVIDER": "auto"})

    def test_quotes_protect_a_hash_and_urls_keep_theirs(self):
        got = envfile.parse('A="x # not a comment"  # but this is\nB=\'y#z\'\nC=https://host/path#frag\nD=a#b\n')
        self.assertEqual(got, {"A": "x # not a comment", "B": "y#z", "C": "https://host/path#frag", "D": "a#b"})

    def test_blank_lines_comments_export_and_garbage(self):
        got = envfile.parse("\n# KEY=1\nexport K2 = v2\nnoequals\n=novalue\nEMPTY=\n")
        self.assertEqual(got, {"K2": "v2", "EMPTY": ""})

    def test_the_real_environment_wins(self):
        with mock.patch.dict(os.environ, {"ZZ_ENVFILE_TEST": "real"}, clear=False):
            import tempfile
            from pathlib import Path
            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / ".env"
                p.write_text("ZZ_ENVFILE_TEST=file\nZZ_ENVFILE_NEW=new # c\n", encoding="utf-8")
                try:
                    envfile.load(p)
                    self.assertEqual((os.environ["ZZ_ENVFILE_TEST"], os.environ["ZZ_ENVFILE_NEW"]), ("real", "new"))
                finally:
                    os.environ.pop("ZZ_ENVFILE_NEW", None)

    def test_a_provider_is_one_word_even_if_a_comment_reached_the_environment(self):
        with mock.patch.dict(os.environ, {"ZZ_PROV": "Auto   # local | openai"}):
            self.assertEqual(envfile.choice("ZZ_PROV"), "auto")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZZ_PROV", None)
            self.assertEqual(envfile.choice("ZZ_PROV", "local"), "local")


if __name__ == "__main__":
    unittest.main()
