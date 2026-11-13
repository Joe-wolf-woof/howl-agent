"""Phase 3 の単体テスト（記憶の統合／行動選択レイヤー）。

APIキー不要・ネットワーク不要で動く。ストアは一時ディレクトリに差し替える。
実行: python -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import store
from src.autopost import run_autopost, variant_for_date
from src.generator import StubClient
from src.persona import build_user_prompt, load_persona
from src.selector import build_state, decide


class StorePhase3Test(unittest.TestCase):
    """ストアを一時ディレクトリに差し替えて検証する基底クラス。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._orig = {
            name: getattr(store, name)
            for name in (
                "DRAFTS_JSONL",
                "DRAFTS_DIR",
                "EPISODES_JSON",
                "OUTBOX_JSONL",
                "DECISIONS_JSONL",
                "REFLECTIONS_JSONL",
                "AUTOPOST_OFF",
            )
        }
        store.DRAFTS_JSONL = root / "drafts.jsonl"
        store.DRAFTS_DIR = root / "drafts"
        store.EPISODES_JSON = root / "episodes.json"
        store.OUTBOX_JSONL = root / "outbox.jsonl"
        store.DECISIONS_JSONL = root / "decisions.jsonl"
        store.REFLECTIONS_JSONL = root / "reflections.jsonl"
        store.AUTOPOST_OFF = root / "AUTOPOST_OFF"
        os.environ.pop("HOWL_AUTOPOST", None)

    def tearDown(self):
        for name, value in self._orig.items():
            setattr(store, name, value)
        os.environ.pop("HOWL_AUTOPOST", None)
        self.tmp.cleanup()

    def _day(self, variant, start=date(2026, 11, 14)):
        d = start
        while variant_for_date(d) != variant:
            d += timedelta(days=1)
        return d


class FakeAdapter:
    name = "fake"

    def __init__(self):
        self.posts = []

    def post(self, text):
        self.posts.append(text)
        return {"status": "posted", "adapter": self.name, "tweet_id": "12345"}


SHOWN_TEXT = "ポトスを見せてもらった。水の匂いがしたという反応だった。"


class ImportEpisodesTest(StorePhase3Test):
    ITEMS = [
        {"date": "2026-11-13", "kind": "shown", "text": SHOWN_TEXT},
        {"date": "2026-11-13", "kind": "shown", "text": SHOWN_TEXT},  # ファイル内重複
        {"date": "2026-11-12", "kind": "shown", "text": "マグカップを見せてもらった。"},
        {"kind": "shown"},  # 形式不正（date/text なし）
    ]

    def test_import_is_idempotent(self):
        added, skipped = store.import_shown_episodes(self.ITEMS)
        self.assertEqual((added, skipped), (2, 2))
        episodes = store.load_episodes()
        self.assertEqual(len(episodes), 2)
        for e in episodes:
            self.assertEqual(e["type"], "shown")
            self.assertEqual(e["kind"], "shown")
        self.assertEqual(episodes[0]["summary"], SHOWN_TEXT)

        added2, skipped2 = store.import_shown_episodes(self.ITEMS)
        self.assertEqual(added2, 0)
        self.assertEqual(skipped2, len(self.ITEMS))
        self.assertEqual(len(store.load_episodes()), 2)


class ExportMemoryTest(StorePhase3Test):
    def test_export_format_matches_app(self):
        draft = store.create_draft("投稿の本文です。", "psychology", "0.5")
        store.mark_posted(draft["id"], {"status": "posted", "tweet_id": "999"})
        store.add_episode(
            {"date": "2026-11-13", "type": "shown", "kind": "shown", "summary": SHOWN_TEXT}
        )
        out = Path(self.tmp.name) / "memory.json"
        n_posts, n_eps = store.export_app_memory(out)
        self.assertEqual((n_posts, n_eps), (1, 1))
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertIn("updatedAt", data)
        self.assertEqual(data["posts"][0]["text"], "投稿の本文です。")
        self.assertEqual(data["posts"][0]["theme"], "psychology")
        self.assertIn("date", data["posts"][0])
        self.assertEqual(data["episodes"][0]["summary"], SHOWN_TEXT)
        self.assertEqual(data["episodes"][0]["date"], "2026-11-13")


class ContextMergeTest(StorePhase3Test):
    def test_shown_not_buried_by_posts(self):
        store.add_episode(
            {"date": "2026-11-08", "type": "shown", "kind": "shown", "summary": SHOWN_TEXT}
        )
        for i in range(6):
            store.add_episode(
                {"date": f"2026-11-{9 + i:02d}", "type": "post", "summary": f"投稿 {i}"}
            )
        ctx = store.context_episodes(5)
        self.assertIn(SHOWN_TEXT, [e["summary"] for e in ctx])

    def test_user_prompt_labels_shown(self):
        persona = load_persona()
        episodes = [
            {"date": "2026-11-13", "type": "shown", "summary": SHOWN_TEXT},
            {"date": "2026-11-13", "type": "post", "summary": "Xに投稿した: 何か"},
        ]
        prompt = build_user_prompt(persona, "observation", episodes)
        self.assertIn("見せてもらった物", prompt)
        self.assertIn(SHOWN_TEXT, prompt)


class SelectorRulesTest(StorePhase3Test):
    def setUp(self):
        super().setUp()
        self.persona = load_persona()
        self.day = self._day("persona")

    def _decide(self, variant="persona"):
        from src.autopost import theme_for_date

        return decide(
            self.persona, self.day, variant, theme_for_date(self.persona, self.day)
        )

    def test_fresh_shown_selects_memory(self):
        store.add_episode(
            {
                "date": self.day.isoformat(),
                "type": "shown",
                "kind": "shown",
                "summary": SHOWN_TEXT,
            }
        )
        decision = self._decide()
        self.assertEqual(decision["action"], "memory")
        self.assertEqual(decision["method"], "rules")
        self.assertEqual(decision["target"]["summary"], SHOWN_TEXT)

    def test_no_shown_selects_theme(self):
        decision = self._decide()
        self.assertEqual(decision["action"], "theme")

    def test_old_shown_selects_theme(self):
        # 目標駆動の導入で仕様変更: 未投稿のまま4日以上経過した shown は
        # 循環目標（circulate）のため memory を優先するようになった。
        # もとの「古い shown は theme」という期待は
        # test_goals.SelectorGoalsTest.test_stale_shown_prioritized_for_circulate
        # で新しい期待に置き換えた。ここでは「投稿済みの古い shown は
        # 対象にしない」ことを確認する。
        old = (self.day - timedelta(days=5)).isoformat()
        store.add_episode(
            {"date": old, "type": "shown", "kind": "shown", "summary": SHOWN_TEXT}
        )
        store.add_episode(
            {
                "date": (self.day - timedelta(days=4)).isoformat(),
                "type": "post",
                "theme": "psychology",
                "action": "memory",
                "target": {"date": old, "summary": SHOWN_TEXT},
                "summary": "Xに投稿した: ポトスのこと",
            }
        )
        self.assertEqual(self._decide()["action"], "theme")

    def test_already_posted_shown_selects_theme(self):
        store.add_episode(
            {
                "date": self.day.isoformat(),
                "type": "shown",
                "kind": "shown",
                "summary": SHOWN_TEXT,
            }
        )
        store.add_episode(
            {
                "date": self.day.isoformat(),
                "type": "post",
                "theme": "psychology",
                "action": "memory",
                "target": {"date": self.day.isoformat(), "summary": SHOWN_TEXT},
                "summary": "Xに投稿した: ポトスのこと",
            }
        )
        self.assertEqual(self._decide()["action"], "theme")

    def test_plain_rules_always_theme(self):
        store.add_episode(
            {
                "date": self.day.isoformat(),
                "type": "shown",
                "kind": "shown",
                "summary": SHOWN_TEXT,
            }
        )
        decision = self._decide(variant="plain")
        self.assertEqual(decision["action"], "theme")
        self.assertIsNone(decision["target"])


class PlainPrivacyTest(StorePhase3Test):
    class RecordingJsonClient:
        def __init__(self, payload):
            self.payload = payload
            self.prompts = []

        def generate_json(self, system_prompt, user_prompt):
            self.prompts.append(system_prompt + "\n" + user_prompt)
            return self.payload

    def test_plain_state_and_prompt_hide_episode_text(self):
        persona = load_persona()
        day = self._day("plain")
        store.add_episode(
            {
                "date": day.isoformat(),
                "type": "shown",
                "kind": "shown",
                "summary": SHOWN_TEXT,
            }
        )
        state = build_state(persona, day, "plain", "psychology")
        self.assertNotIn("ポトス", json.dumps(state, ensure_ascii=False))
        self.assertEqual(state["shown_count"], 1)

        client = self.RecordingJsonClient({"action": "theme", "reason": "予定どおり"})
        decision = decide(persona, day, "plain", "psychology", client)
        self.assertEqual(decision["action"], "theme")
        joined = "\n".join(client.prompts)
        self.assertNotIn("ポトス", joined)  # エピソード本文を含めない
        self.assertNotIn("ハウル", joined)  # ペルソナ情報を含めない
        self.assertNotIn("memory", joined.split("action")[0])  # 選択肢に memory が無い


class AutopostRestTest(StorePhase3Test):
    class RestClient:
        def generate_json(self, system_prompt, user_prompt):
            return {"action": "rest", "reason": "今日は静かに過ごす"}

        def generate(self, system_prompt, user_prompt):
            raise AssertionError("rest の日に生成が呼ばれてはいけない")

    def test_rest_posts_nothing_and_records_no_episode(self):
        day = self._day("persona")
        adapter = FakeAdapter()
        result = run_autopost(day=day, client=self.RestClient(), adapter=adapter)
        self.assertEqual(result["status"], "rested")
        self.assertEqual(result["decision"]["action"], "rest")
        self.assertEqual(adapter.posts, [])
        self.assertEqual(store.load_episodes(), [])
        self.assertEqual(store.list_drafts(), [])
        decisions = store.load_decisions()
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["action"], "rest")


class AutopostDecisionRecordTest(StorePhase3Test):
    def test_memory_decision_recorded_on_draft_and_episode(self):
        day = self._day("persona")
        store.add_episode(
            {
                "date": day.isoformat(),
                "type": "shown",
                "kind": "shown",
                "summary": SHOWN_TEXT,
            }
        )
        adapter = FakeAdapter()
        result = run_autopost(day=day, client=StubClient(), adapter=adapter)
        self.assertEqual(result["status"], "posted")
        self.assertEqual(result["decision"]["action"], "memory")
        draft = store.get_draft(result["draft_id"])
        self.assertEqual(draft["decision"]["action"], "memory")
        self.assertEqual(draft["decision"]["target"]["summary"], SHOWN_TEXT)
        posts = [e for e in store.load_episodes() if e.get("type") == "post"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["action"], "memory")
        self.assertEqual(posts[0]["target"]["summary"], SHOWN_TEXT)
        self.assertEqual(len(store.load_decisions()), 1)


if __name__ == "__main__":
    unittest.main()
