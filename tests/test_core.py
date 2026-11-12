"""コアロジックの単体テスト（APIキー不要）。

実行: python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import store
from src.filters import check_post
from src.generator import StubClient, choose_theme, generate_post
from src.persona import build_system_prompt, load_persona


class PersonaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.persona = load_persona()

    def test_theme_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(self.persona["themes"].values()), 1.0)

    def test_psychology_is_largest_theme(self):
        themes = self.persona["themes"]
        self.assertEqual(max(themes, key=themes.get), "psychology")
        self.assertEqual(themes["psychology"], 0.4)

    def test_system_prompt_contains_identity(self):
        prompt = build_system_prompt(self.persona)
        for needle in ["ハウル", "ワガハイ", "家の人間", "人間", "開放性", "140字以内"]:
            self.assertIn(needle, prompt)

    def test_choose_theme_validates(self):
        self.assertEqual(choose_theme(self.persona, "psychology"), "psychology")
        with self.assertRaises(ValueError):
            choose_theme(self.persona, "nonexistent")


class FilterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.persona = load_persona()

    def test_clean_text_passes(self):
        self.assertEqual(check_post("雨だ。音の少ない日は観察に向いている。", self.persona), [])

    def test_url_rejected(self):
        issues = check_post("これを見て https://example.com", self.persona)
        self.assertTrue(any("URL" in i for i in issues))

    def test_over_length_rejected(self):
        issues = check_post("あ" * 141, self.persona)
        self.assertTrue(any("文字数" in i for i in issues))


class GeneratorStubTest(unittest.TestCase):
    def test_stub_generates_per_theme(self):
        persona = load_persona()
        text = generate_post(persona, "psychology", [], StubClient())
        self.assertIn("カクテルパーティー効果", text)
        self.assertEqual(check_post(text, persona), [])


class StoreTest(unittest.TestCase):
    def test_edit_diff_is_recorded(self):
        draft = store.create_draft("元の文です。", "observation", "0.2")
        txt = store.DRAFTS_DIR / f"{draft['id']}.txt"
        txt.write_text("編集された文です。", encoding="utf-8")
        approved = store.approve(draft["id"])
        self.assertTrue(approved["was_edited"])
        self.assertEqual(approved["edited_text"], "編集された文です。")
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(store.final_text(approved), "編集された文です。")


if __name__ == "__main__":
    unittest.main()
