"""目標駆動の単体テスト（週次振り返り／selector への目標組み込み）。

目標は3件（learn / circulate / structure）。APIキー不要・ネットワーク不要。
ストアは一時ディレクトリに差し替える（reflections.jsonl も含めること。
Phase 3 で decisions の差し替え漏れによる実データ混入があったため）。
実行: python -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import store
from src.autopost import variant_for_date
from src.generator import StubClient
from src.persona import load_persona
from src.review import run_review
from src.selector import build_state, decide, load_goals

SHOWN_TEXT = "ポトスを見せてもらった。水の匂いがしたという反応だった。"


class GoalsStoreTest(unittest.TestCase):
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


class GoalsDefinitionTest(unittest.TestCase):
    def test_three_goals_are_defined(self):
        goals = load_goals()
        self.assertEqual([g["id"] for g in goals], ["learn", "circulate", "structure"])
        for g in goals:
            self.assertTrue(g["text"])


class WeeklyReviewTest(GoalsStoreTest):
    def _seed_posts(self):
        for total in (80, 100):
            draft = store.create_draft(
                "振り返り用の投稿文です。",
                "psychology",
                "0.5",
                check={"total": total, "axes": {}, "method": "rules"},
            )
            store.mark_posted(draft["id"], {"status": "posted", "tweet_id": "111"})

    def test_review_records_reflection_with_stub(self):
        today = datetime.now(store.JST).date()
        self._seed_posts()
        store.append_decision(
            {
                "date": today.isoformat(),
                "variant": "persona",
                "action": "theme",
                "theme": "psychology",
                "reason": "テスト用",
                "method": "rules",
                "target": None,
            }
        )
        record = run_review(
            days=7, client=StubClient(), fetch_metrics=False, today=today
        )
        self.assertEqual(record["method"], "template")
        self.assertEqual(record["stats"]["posts"], 2)
        self.assertEqual(record["stats"]["by_variant"], {"persona": 2})
        self.assertEqual(record["stats"]["by_theme"], {"psychology": 2})
        self.assertEqual(record["stats"]["decisions"], {"theme": 1})
        self.assertEqual(record["stats"]["avg_check_score"], 90.0)
        self.assertTrue(record["learnings"])
        self.assertTrue(record["policy"])
        saved = store.load_reflections()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["policy"], record["policy"])
        self.assertEqual(saved[0]["learnings"], record["learnings"])
        self.assertEqual(saved[0]["period"], record["period"])

    def test_review_uses_model_when_available(self):
        class JsonClient:
            def generate_json(self, system_prompt, user_prompt):
                return {"learnings": ["学びA", "学びB"], "policy": "来週の方針X"}

        record = run_review(
            days=7,
            client=JsonClient(),
            fetch_metrics=False,
            today=datetime.now(store.JST).date(),
        )
        self.assertEqual(record["method"], "gemini")
        self.assertEqual(record["learnings"], ["学びA", "学びB"])
        self.assertEqual(record["policy"], "来週の方針X")

    def test_append_md_adds_sections(self):
        today = datetime.now(store.JST).date()
        md = Path(self.tmp.name) / "learnings.md"
        run_review(
            days=7, client=StubClient(), fetch_metrics=False, today=today, append_md=md
        )
        text = md.read_text(encoding="utf-8")
        self.assertIn("# 運用の学び", text)
        self.assertIn("## 週次振り返り", text)
        self.assertIn("### 学び", text)
        self.assertIn("### 来週の方針", text)

        run_review(
            days=7, client=StubClient(), fetch_metrics=False, today=today, append_md=md
        )
        text2 = md.read_text(encoding="utf-8")
        self.assertEqual(text2.count("## 週次振り返り"), 2)
        self.assertEqual(len(store.load_reflections()), 2)


class SelectorGoalsTest(GoalsStoreTest):
    def setUp(self):
        super().setUp()
        self.persona = load_persona()
        self.day = self._day("persona")

    def _reflection(self, days_ago=2, policy="来週の方針テスト"):
        store.append_reflection(
            {
                "date": (self.day - timedelta(days=days_ago)).isoformat(),
                "period": {"start": "2026-11-07", "end": "2026-11-13", "days": 7},
                "stats": {},
                "learnings": ["学び1", "学び2", "学び3", "学び4"],
                "policy": policy,
                "method": "template",
            }
        )

    def _goals_by_id(self, state):
        return {g["id"]: g for g in state["goals"]}

    def test_state_includes_learnings_and_goal_progress(self):
        self._reflection()
        store.add_episode(
            {
                "date": (self.day - timedelta(days=5)).isoformat(),
                "type": "shown",
                "kind": "shown",
                "summary": SHOWN_TEXT,
            }
        )
        state = build_state(self.persona, self.day, "persona", "psychology")
        self.assertEqual(state["learnings"]["policy"], "来週の方針テスト")
        self.assertEqual(len(state["learnings"]["learnings"]), 3)  # 上位3件に絞る
        goals = self._goals_by_id(state)
        self.assertEqual(set(goals), {"learn", "circulate", "structure"})
        self.assertEqual(goals["learn"]["days_since_reflection"], 2)
        self.assertTrue(goals["learn"]["has_policy"])
        self.assertEqual(goals["circulate"]["unposted_shown_count"], 1)
        self.assertEqual(goals["circulate"]["oldest_unposted_shown_days"], 5)
        self.assertEqual(goals["circulate"]["cycles_completed"], 0)
        self.assertIsNone(goals["circulate"]["last_cycle_date"])
        self.assertEqual(goals["structure"]["reflections_count"], 1)
        self.assertEqual(
            goals["structure"]["last_reflection_date"],
            (self.day - timedelta(days=2)).isoformat(),
        )

    def test_state_learnings_none_without_reflection(self):
        state = build_state(self.persona, self.day, "persona", "psychology")
        self.assertIsNone(state["learnings"])
        goals = self._goals_by_id(state)
        self.assertIsNone(goals["learn"]["days_since_reflection"])
        self.assertFalse(goals["learn"]["has_policy"])
        self.assertEqual(goals["structure"]["reflections_count"], 0)

    def test_circulate_cycle_within_7_days_counts(self):
        shown_day = self.day - timedelta(days=5)
        post_day = self.day - timedelta(days=3)  # shown の2日後 → 7日以内で成立
        store.add_episode(
            {
                "date": shown_day.isoformat(),
                "type": "shown",
                "kind": "shown",
                "summary": SHOWN_TEXT,
            }
        )
        store.add_episode(
            {
                "date": post_day.isoformat(),
                "type": "post",
                "theme": "psychology",
                "action": "memory",
                "target": {"date": shown_day.isoformat(), "summary": SHOWN_TEXT},
                "summary": "Xに投稿した: ポトスのこと",
            }
        )
        state = build_state(self.persona, self.day, "persona", "psychology")
        circ = self._goals_by_id(state)["circulate"]
        self.assertEqual(circ["cycles_completed"], 1)
        self.assertEqual(circ["last_cycle_date"], post_day.isoformat())
        self.assertEqual(circ["unposted_shown_count"], 0)

    def test_circulate_cycle_after_7_days_does_not_count(self):
        shown_day = self.day - timedelta(days=10)
        post_day = self.day - timedelta(days=1)  # shown の9日後 → 遅すぎて不成立
        store.add_episode(
            {
                "date": shown_day.isoformat(),
                "type": "shown",
                "kind": "shown",
                "summary": SHOWN_TEXT,
            }
        )
        store.add_episode(
            {
                "date": post_day.isoformat(),
                "type": "post",
                "theme": "psychology",
                "action": "memory",
                "target": {"date": shown_day.isoformat(), "summary": SHOWN_TEXT},
                "summary": "Xに投稿した: ポトスのこと",
            }
        )
        state = build_state(self.persona, self.day, "persona", "psychology")
        circ = self._goals_by_id(state)["circulate"]
        self.assertEqual(circ["cycles_completed"], 0)
        self.assertIsNone(circ["last_cycle_date"])
        # target として使われてはいるので「未投稿」には数えない
        self.assertEqual(circ["unposted_shown_count"], 0)

    def test_stale_shown_prioritized_for_circulate(self):
        store.add_episode(
            {
                "date": (self.day - timedelta(days=5)).isoformat(),
                "type": "shown",
                "kind": "shown",
                "summary": SHOWN_TEXT,
            }
        )
        decision = decide(self.persona, self.day, "persona", "psychology")
        self.assertEqual(decision["action"], "memory")
        self.assertEqual(decision["method"], "rules")
        self.assertEqual(decision["target"]["summary"], SHOWN_TEXT)
        self.assertIn("循環", decision["reason"])

    def test_plain_state_hides_goal_details(self):
        self._reflection(policy="秘密の方針テキスト")
        store.add_episode(
            {
                "date": self.day.isoformat(),
                "type": "shown",
                "kind": "shown",
                "summary": SHOWN_TEXT,
            }
        )
        plain_day = self._day("plain")
        state = build_state(self.persona, plain_day, "plain", "psychology")
        dumped = json.dumps(state, ensure_ascii=False)
        self.assertNotIn("ポトス", dumped)  # エピソード本文
        self.assertNotIn("秘密の方針テキスト", dumped)  # 学び・方針の本文
        for goal in load_goals():
            self.assertNotIn(goal["text"], dumped)  # 目標文の詳細
        self.assertNotIn("learnings", state)
        self.assertNotIn("goals", state)
        self.assertEqual(state["reflections_count"], 1)  # 件数だけは可


if __name__ == "__main__":
    unittest.main()
