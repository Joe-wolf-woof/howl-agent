"""Phase 2 の単体テスト（一貫性チェッカー／全自動投稿／A/B）。

APIキー不要・ネットワーク不要で動く。ストアは一時ディレクトリに差し替える。
実行: python -m unittest discover -s tests -v
"""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import store
from src.autopost import run_autopost, theme_for_date, variant_for_date
from src.checker import PASS_THRESHOLD, check_draft, check_with_rules, generate_checked
from src.generator import STUB_SAMPLES, StubClient, generate_post
from src.persona import load_persona


class RuleCheckerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.persona = load_persona()

    def test_stub_samples_pass(self):
        for theme, text in STUB_SAMPLES.items():
            result = check_with_rules(text, theme, self.persona)
            self.assertGreaterEqual(result["total"], PASS_THRESHOLD, f"{theme}: {result}")
            self.assertEqual(result["method"], "rules")
            self.assertEqual(set(result["axes"]), {"voice", "address", "theme", "world", "safety"})

    def test_bad_text_fails(self):
        bad = "政治の話をしよう。詳しくは https://example.com を見てくれ。僕は人間になりたい。"
        result = check_with_rules(bad, "psychology", self.persona)
        self.assertLess(result["total"], PASS_THRESHOLD)
        self.assertLessEqual(result["axes"]["safety"], 5)

    def test_url_hits_safety(self):
        text = "ワガハイは見た。詳しくは https://example.com にある。"
        result = check_with_rules(text, "observation", self.persona)
        self.assertEqual(result["axes"]["safety"], 0)

    def test_first_person_affects_voice(self):
        with_fp = "ワガハイは窓の音を聞いた。静かな夜だ。"
        without_fp = "窓の音を聞いた。静かな夜だ。"
        a = check_with_rules(with_fp, "observation", self.persona)
        b = check_with_rules(without_fp, "observation", self.persona)
        self.assertGreater(a["axes"]["voice"], b["axes"]["voice"])

    def test_animal_suffix_hits_voice(self):
        text = "ワガハイは嬉しいだワン。窓の音が聞こえる。"
        result = check_with_rules(text, "observation", self.persona)
        self.assertLessEqual(result["axes"]["voice"], 12)


class FakeJsonClient:
    """generate_json を持つフェイク（Gemini 採点経路のテスト用）。"""

    def __init__(self, payload=None, exc=None):
        self.payload = payload
        self.exc = exc

    def generate_json(self, system_prompt, user_prompt):
        if self.exc:
            raise self.exc
        return self.payload


class GeminiCheckerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.persona = load_persona()

    def test_gemini_scores_are_clamped_and_summed(self):
        client = FakeJsonClient(
            {"axes": {"voice": 25, "address": 18, "theme": 19, "world": 17, "safety": 20},
             "total": 999, "reasons": ["概ね良好"]}
        )
        result = check_draft("任意の文。", "observation", self.persona, client)
        self.assertEqual(result["method"], "gemini")
        self.assertEqual(result["axes"]["voice"], 20)  # 25 → 20 にクランプ
        self.assertEqual(result["total"], 20 + 18 + 19 + 17 + 20)  # total は軸合計で再計算

    def test_gemini_failure_falls_back_to_rules(self):
        client = FakeJsonClient(exc=RuntimeError("api down"))
        result = check_draft(STUB_SAMPLES["observation"], "observation", self.persona, client)
        self.assertEqual(result["method"], "rules")
        self.assertGreaterEqual(result["total"], PASS_THRESHOLD)


class GenerateCheckedTest(unittest.TestCase):
    def test_stub_passes_on_first_attempt(self):
        persona = load_persona()
        text, check = generate_checked(persona, "psychology", [], StubClient())
        self.assertEqual(text, STUB_SAMPLES["psychology"])
        self.assertGreaterEqual(check["total"], PASS_THRESHOLD)
        self.assertEqual(check["attempts"], 1)

    def test_retries_and_keeps_best(self):
        persona = load_persona()

        class FlakyClient:
            def __init__(self):
                self.calls = 0

            def generate(self, system_prompt, user_prompt):
                self.calls += 1
                if self.calls < 3:
                    return "僕の文です。政治の話 https://example.com"  # 低スコア
                return STUB_SAMPLES["observation"]

        text, check = generate_checked(persona, "observation", [], FlakyClient())
        self.assertEqual(text, STUB_SAMPLES["observation"])
        self.assertEqual(check["attempts"], 3)
        self.assertGreaterEqual(check["total"], PASS_THRESHOLD)


class VariantPromptTest(unittest.TestCase):
    class RecordingClient:
        def __init__(self):
            self.calls = []

        def generate(self, system_prompt, user_prompt):
            self.calls.append((system_prompt, user_prompt))
            return "文です。"

    def test_plain_has_no_persona_or_memory(self):
        persona = load_persona()
        client = self.RecordingClient()
        episodes = [{"date": "2026-11-13", "summary": "ポトスに水をやった"}]
        generate_post(persona, "observation", episodes, client, variant="plain")
        system, user = client.calls[0]
        self.assertNotIn("ワガハイ", system)
        self.assertNotIn("ポトス", user)
        self.assertNotIn("エピソード", user)

    def test_persona_includes_persona_and_memory(self):
        persona = load_persona()
        client = self.RecordingClient()
        episodes = [{"date": "2026-11-13", "summary": "ポトスに水をやった"}]
        generate_post(persona, "observation", episodes, client, variant="persona")
        system, user = client.calls[0]
        self.assertIn("ワガハイ", system)
        self.assertIn("ポトス", user)


class StorePhase2Test(unittest.TestCase):
    """ストアを一時ディレクトリに差し替えて検証する基底クラス。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._orig = {
            name: getattr(store, name)
            for name in ("DRAFTS_JSONL", "DRAFTS_DIR", "EPISODES_JSON", "OUTBOX_JSONL", "AUTOPOST_OFF")
        }
        store.DRAFTS_JSONL = root / "drafts.jsonl"
        store.DRAFTS_DIR = root / "drafts"
        store.EPISODES_JSON = root / "episodes.json"
        store.OUTBOX_JSONL = root / "outbox.jsonl"
        store.AUTOPOST_OFF = root / "AUTOPOST_OFF"
        os.environ.pop("HOWL_AUTOPOST", None)

    def tearDown(self):
        for name, value in self._orig.items():
            setattr(store, name, value)
        os.environ.pop("HOWL_AUTOPOST", None)
        self.tmp.cleanup()


class FakeAdapter:
    name = "fake"

    def __init__(self):
        self.posts = []

    def post(self, text):
        self.posts.append(text)
        return {"status": "posted", "adapter": self.name, "tweet_id": "12345"}


class AutopostTest(StorePhase2Test):
    def test_rotation_is_deterministic_and_alternates(self):
        persona = load_persona()
        d0 = date(2026, 11, 14)
        self.assertIn(theme_for_date(persona, d0), persona["themes"])
        self.assertEqual(theme_for_date(persona, d0), theme_for_date(persona, d0))
        self.assertNotEqual(variant_for_date(d0), variant_for_date(d0 + timedelta(days=1)))

    def test_dry_run_posts_nothing_and_records_no_episode(self):
        result = run_autopost(dry_run=True, stub=True, day=date(2026, 11, 14))
        self.assertEqual(result["status"], "dry-run")
        self.assertFalse(result["posted"])
        self.assertEqual(store.load_episodes(), [])  # エピソードは汚さない
        draft = store.get_draft(result["draft_id"])
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["variant"], result["variant"])
        self.assertIsNotNone(draft["check"])
        outbox = Path(store.OUTBOX_JSONL).read_text(encoding="utf-8")
        self.assertIn("simulated", outbox)

    def test_real_post_records_episode_with_variant(self):
        adapter = FakeAdapter()
        day = date(2026, 11, 15)
        result = run_autopost(day=day, client=StubClient(), adapter=adapter)
        self.assertEqual(result["status"], "posted")
        self.assertEqual(len(adapter.posts), 1)
        episodes = store.load_episodes()
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["variant"], variant_for_date(day))
        self.assertEqual(episodes[0]["type"], "post")
        self.assertEqual(store.get_draft(result["draft_id"])["status"], "posted")

    def test_kill_switch_env(self):
        os.environ["HOWL_AUTOPOST"] = "off"
        result = run_autopost(dry_run=True, stub=True, day=date(2026, 11, 14))
        self.assertEqual(result["status"], "aborted")
        self.assertIn("kill-switch", result["reason"])

    def test_kill_switch_file(self):
        Path(store.AUTOPOST_OFF).write_text("stop", encoding="utf-8")
        result = run_autopost(dry_run=True, stub=True, day=date(2026, 11, 14))
        self.assertEqual(result["status"], "aborted")
        self.assertIn("kill-switch", result["reason"])

    def test_daily_limit(self):
        day = date(2026, 11, 14)
        for _ in range(2):
            store.add_episode({"date": day.isoformat(), "type": "post", "theme": "events", "summary": "x"})
        result = run_autopost(dry_run=True, stub=True, day=day)
        self.assertEqual(result["status"], "aborted")
        self.assertEqual(result["reason"], "daily-limit")


class ReportTest(StorePhase2Test):
    def test_report_counts_by_variant_without_credentials(self):
        from src.cli import cmd_report

        store.add_episode({"date": "2026-11-14", "type": "post", "theme": "events", "variant": "persona", "summary": "a"})
        store.add_episode({"date": "2026-11-15", "type": "post", "theme": "events", "variant": "plain", "summary": "b"})
        store.add_episode({"date": "2026-11-11", "type": "legacy", "summary": "対象外"})

        class Args:
            no_metrics = False

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cmd_report(Args())
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("投稿済みエピソード: 2 件", out)
        self.assertIn("persona: 1 件", out)
        self.assertIn("plain: 1 件", out)


if __name__ == "__main__":
    unittest.main()
