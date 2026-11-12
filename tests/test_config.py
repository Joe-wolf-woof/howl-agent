"""dotenv パーサのテスト。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import parse_env_text


class ParseEnvTest(unittest.TestCase):
    def test_basic_pairs_comments_and_blanks(self):
        text = "# comment\nX_API_KEY=abc123\n\nX_API_SECRET = spaced \n"
        parsed = parse_env_text(text)
        self.assertEqual(parsed["X_API_KEY"], "abc123")
        self.assertEqual(parsed["X_API_SECRET"], "spaced")

    def test_bom_and_quotes(self):
        text = '\ufeffX_ACCESS_TOKEN="tok-1"\nX_ACCESS_SECRET=\'sec-2\'\n'
        parsed = parse_env_text(text)
        self.assertEqual(parsed["X_ACCESS_TOKEN"], "tok-1")
        self.assertEqual(parsed["X_ACCESS_SECRET"], "sec-2")

    def test_value_may_contain_equals(self):
        parsed = parse_env_text("K=a=b=c\n")
        self.assertEqual(parsed["K"], "a=b=c")


if __name__ == "__main__":
    unittest.main()
