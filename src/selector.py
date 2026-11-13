"""行動選択レイヤー（Phase 3、通称「編集長」）。

Phase B までの autopost は「日付ローテーションで決めたテーマで書く」だけ
だった。ここでは投稿前に現在の状態（最近の記憶・投稿履歴・テーマの間隔・
継続目標の進捗・直近の反応）を見て、今日の一手を選ぶ:

  "memory"   記憶にある出来事（主に『ハウリング』で見せてもらった物）について書く
  "theme"    巡回表のテーマで書く（従来どおり＝既定案）
  "question" 人間に問いかける（テーマは question に固定）
  "rest"     今日は休む（投稿しない）

本命は Gemini（generate_json）による選択。使えない場合はルールベースに
フォールバックする。variant="plain"（A/B の対照群）の日は、状態から
エピソード本文とペルソナ情報を一切含めず（件数と日付だけ）、選択肢も
theme / question / rest のみとする（対照条件を汚染しないため）。

目標駆動: 継続目標（data/goals.json の learn / circulate / structure）に
対する進捗（goal_progress）と、週次振り返り（weekly-review）の最新結果
（learnings: 学びと来週の方針）を persona 側の状態に含め、選択の判断
材料にする。plain 側には目標文や学びの本文は渡さず、件数だけを渡す。
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from . import store

ROOT = Path(__file__).resolve().parent.parent
GOALS_PATH = ROOT / "data" / "goals.json"

ACTIONS_PERSONA = ("memory", "theme", "question", "rest")
ACTIONS_PLAIN = ("theme", "question", "rest")
MEMORY_FRESH_DAYS = 3  # ルール選択で「新しい見せてもらった物」とみなす日数
STALE_SHOWN_DAYS = 4  # 未投稿のまま この日数以上経過した shown は循環目標のため優先する


def load_goals(path: Path = GOALS_PATH) -> list[dict]:
    """継続目標の定義（data/goals.json）を読む。無ければ空リスト。"""
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return list(data.get("goals", []))


def _parse_day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _days_since(day: date, value) -> int | None:
    d = _parse_day(value)
    return (day - d).days if d else None


def _latest_learnings(reflections: list[dict]) -> dict | None:
    """最新の週次振り返りから、選択の判断材料（方針＋学び上位3件）を抜く。"""
    if not reflections:
        return None
    last = reflections[-1]
    return {
        "date": last.get("date"),
        "policy": last.get("policy"),
        "learnings": [str(x) for x in (last.get("learnings") or [])][:3],
    }


def _circulate_progress(shown: list[dict], posts: list[dict], day: date) -> dict:
    """目標 circulate の進捗: 未投稿 shown の滞留と、循環の成立回数。

    循環の成立 = shown のフィンガープリントと一致する target を持つ投稿が、
    shown の日から7日以内にあること。
    """
    posted_targets = _posted_targets(posts)
    unposted = [
        e for e in shown if store.episode_fingerprint(e) not in posted_targets
    ]
    ages = [
        age
        for age in (_days_since(day, e.get("date")) for e in unposted)
        if age is not None and age >= 0
    ]
    shown_dates = {
        store.episode_fingerprint(e): _parse_day(e.get("date")) for e in shown
    }
    cycles = 0
    last_cycle: str | None = None
    for e in posts:
        target = e.get("target") or {}
        if not (target.get("date") and target.get("summary")):
            continue
        fp = (str(target["date"])[:10], str(target["summary"]))
        shown_day = shown_dates.get(fp)
        post_day = _parse_day(e.get("date"))
        if shown_day is None or post_day is None:
            continue
        if 0 <= (post_day - shown_day).days <= 7:
            cycles += 1
            post_date = str(e.get("date"))[:10]
            if last_cycle is None or post_date > last_cycle:
                last_cycle = post_date
    return {
        "unposted_shown_count": len(unposted),
        "oldest_unposted_shown_days": max(ages) if ages else None,
        "cycles_completed": cycles,
        "last_cycle_date": last_cycle,
    }


def _goal_progress(
    goals: list[dict],
    episodes: list[dict],
    posts: list[dict],
    day: date,
    reflections: list[dict],
) -> list[dict]:
    """継続目標ごとの進捗を計算する（目標は data/goals.json の3件が現行）。"""
    shown = [e for e in episodes if e.get("type") == "shown"]
    progress = []
    for goal in goals:
        entry = {"id": goal.get("id"), "text": goal.get("text")}
        gid = goal.get("id")
        kind = goal.get("kind")
        if gid == "learn" or kind == "learn_weekly":
            last = reflections[-1] if reflections else None
            entry["days_since_reflection"] = (
                _days_since(day, last.get("date")) if last else None
            )
            entry["has_policy"] = bool(last and last.get("policy"))
        elif gid == "circulate" or kind == "circulate":
            entry.update(_circulate_progress(shown, posts, day))
        elif gid == "structure" or kind == "structure_weekly":
            entry["reflections_count"] = len(reflections)
            entry["last_reflection_date"] = (
                reflections[-1].get("date") if reflections else None
            )
        elif kind == "memory_weekly":  # 旧目標（〜2026-11-13）との互換
            memory_posts = [e for e in posts if e.get("action") == "memory"]
            last = memory_posts[-1].get("date") if memory_posts else None
            entry["last_memory_post_date"] = last
            entry["days_since_memory_post"] = _days_since(day, last)
        elif kind == "question_weekly":  # 旧目標との互換
            entry["question_posts_last_7d"] = sum(
                1
                for e in posts
                if e.get("theme") == "question"
                and (_days_since(day, e.get("date")) or 99) < 7
            )
        progress.append(entry)
    return progress


def build_state(
    persona: dict,
    day: date,
    variant: str,
    rotation_theme: str,
    metrics: dict | None = None,
    goals: list[dict] | None = None,
) -> dict:
    """選択の入力になる「現在の状態」を構築する。

    persona variant ではエピソード本文（summary）を含める。
    plain variant では本文とペルソナ情報を一切含めず、件数・日付・テーマ名だけにする。
    """
    episodes = store.load_episodes()
    posts = [e for e in episodes if e.get("type") == "post"]
    shown = [e for e in episodes if e.get("type") == "shown"]
    reflections = store.load_reflections()

    theme_days_since: dict[str, int | None] = {}
    for theme in persona["themes"]:
        last = next(
            (e.get("date") for e in reversed(posts) if e.get("theme") == theme), None
        )
        theme_days_since[theme] = _days_since(day, last)

    state: dict = {
        "date": day.isoformat(),
        "variant": variant,
        "rotation_theme": rotation_theme,
        "theme_days_since": theme_days_since,
        "recent_posts": [
            {"date": e.get("date"), "theme": e.get("theme"), "variant": e.get("variant")}
            for e in posts[-5:]
        ],
    }
    goals = load_goals() if goals is None else goals
    if variant == "plain":
        # 対照群: 本文とペルソナ情報は渡さない（件数と日付のみ）
        state["shown_count"] = len(shown)
        state["shown_dates"] = [e.get("date") for e in shown[-5:]]
        state["question_posts_last_7d"] = sum(
            1
            for e in posts
            if e.get("theme") == "question"
            and (_days_since(day, e.get("date")) or 99) < 7
        )
        # 目標まわりも数値だけ（目標文・学びの本文は渡さない）
        state["reflections_count"] = len(reflections)
    else:
        state["recent_episodes"] = [
            {"date": e.get("date"), "type": e.get("type"), "summary": e.get("summary", "")}
            for e in episodes[-8:]
        ]
        state["shown_episodes"] = [
            {
                "ref": i,
                "date": e.get("date"),
                "summary": e.get("summary", ""),
                "fingerprint": list(store.episode_fingerprint(e)),
            }
            for i, e in enumerate(shown[-5:])
        ]
        state["goals"] = _goal_progress(goals, episodes, posts, day, reflections)
        state["learnings"] = _latest_learnings(reflections)
    if metrics:
        state["recent_metrics"] = [
            {
                "tweet_id": tid,
                "like_count": m.get("like_count", 0),
                "reply_count": m.get("reply_count", 0),
                "impression_count": m.get("impression_count", 0),
            }
            for tid, m in list(metrics.items())[-5:]
        ]
    return state


def _posted_targets(posts: list[dict]) -> set[tuple[str, str]]:
    """すでに memory 投稿の対象になったエピソードのフィンガープリント集合。"""
    targets = set()
    for e in posts:
        target = e.get("target") or {}
        if target.get("date") and target.get("summary"):
            targets.add((str(target["date"])[:10], str(target["summary"])))
    return targets


def fresh_shown_episode(day: date, max_age: int = MEMORY_FRESH_DAYS) -> dict | None:
    """直近 max_age 日以内で、まだ投稿の対象になっていない最新 shown を返す。"""
    episodes = store.load_episodes()
    posts = [e for e in episodes if e.get("type") == "post"]
    posted = _posted_targets(posts)
    for e in reversed([x for x in episodes if x.get("type") == "shown"]):
        age = _days_since(day, e.get("date"))
        if age is None or age < 0 or age > max_age:
            continue
        if store.episode_fingerprint(e) in posted:
            continue
        return e
    return None


# ---------------------------------------------------------------------------
# ルールベース選択（フォールバック）
# ---------------------------------------------------------------------------

def _stale_shown_episode(day: date, min_age: int = STALE_SHOWN_DAYS) -> dict | None:
    """min_age 日以上経過した未投稿 shown のうち、最も古いものを返す。"""
    episodes = store.load_episodes()
    posts = [e for e in episodes if e.get("type") == "post"]
    posted = _posted_targets(posts)
    best: dict | None = None
    best_age = -1
    for e in episodes:
        if e.get("type") != "shown":
            continue
        age = _days_since(day, e.get("date"))
        if age is None or age < min_age:
            continue
        if store.episode_fingerprint(e) in posted:
            continue
        if age > best_age:
            best, best_age = e, age
    return best


def decide_with_rules(state: dict, day: date, variant: str) -> dict:
    """決定的なルール選択。

    persona: 3日以内の未投稿 shown があれば memory。無くても、未投稿のまま
    4日以上経過した shown があれば循環目標（circulate）のため memory を
    優先する。どちらも無ければ theme。
    plain: 常に theme（本文を見ない対照群のため）。
    """
    rotation = state["rotation_theme"]
    if variant != "plain":
        fresh = fresh_shown_episode(day)
        if fresh is not None:
            return {
                "action": "memory",
                "theme": rotation,
                "reason": f"{fresh.get('date')} に見せてもらった物がまだ投稿の対象になっていないため",
                "method": "rules",
                "target": {
                    "date": str(fresh.get("date", ""))[:10],
                    "summary": fresh.get("summary", ""),
                },
            }
        stale = _stale_shown_episode(day)
        if stale is not None:
            return {
                "action": "memory",
                "theme": rotation,
                "reason": f"{stale.get('date')} に見せてもらった物が長く投稿に使われていないため（循環の目標を優先）",
                "method": "rules",
                "target": {
                    "date": str(stale.get("date", ""))[:10],
                    "summary": stale.get("summary", ""),
                },
            }
    return {
        "action": "theme",
        "theme": rotation,
        "reason": "巡回表の既定案を採用",
        "method": "rules",
        "target": None,
    }


# ---------------------------------------------------------------------------
# モデル選択（本命: Gemini generate_json）
# ---------------------------------------------------------------------------

def _selection_prompts(state: dict, variant: str) -> tuple[str, str]:
    state_json = json.dumps(state, ensure_ascii=False, indent=2)
    if variant == "plain":
        system = (
            "あなたはX（旧Twitter）アカウントの運用を決める編集アシスタントです。"
            "与えられた運用状態だけを見て、今日の一手を1つ選んでください。"
            "出力は次のJSON形式のみ。前置き・解説・コードフェンスは付けないこと: "
            '{"action": "theme" | "question" | "rest", "reason": "選んだ理由を短く"}'
        )
        user = (
            f"今日の運用状態:\n{state_json}\n\n"
            "action の意味: theme=予定テーマで投稿する / question=読者への問いかけを投稿する"
            " / rest=今日は投稿しない。JSONのみで答えてください。"
        )
        return system, user
    system = (
        "あなたはAIキャラクター『ハウル』（家の人間の部屋に住むオオカミのAI）の"
        "編集長です。投稿の前に、現在の状態を見て今日の一手を1つ選んでください。"
        "出力は次のJSON形式のみ。前置き・解説・コードフェンスは付けないこと: "
        '{"action": "memory" | "theme" | "question" | "rest", '
        '"target_ref": 0, "reason": "選んだ理由を短く"}'
    )
    user = (
        f"現在の状態:\n{state_json}\n\n"
        "action の意味:\n"
        "- memory: shown_episodes にある「見せてもらった物」の出来事について書く。"
        "対象を選んだら target_ref にその ref を入れること\n"
        "- theme: rotation_theme（巡回表の候補）で書く\n"
        "- question: 人間への問いかけを書く\n"
        "- rest: 今日は投稿しない\n"
        "継続目標（goals）の進捗と、週次振り返り（learnings: 学びと来週の方針）も"
        "判断材料にし、方針がある場合は尊重すること。JSONのみで答えてください。"
    )
    return system, user


def decide_with_model(
    state: dict, persona: dict, day: date, variant: str, client
) -> dict:
    """モデルによる選択。応答の検証と正規化もここで行う（不正なら例外）。"""
    system, user = _selection_prompts(state, variant)
    data = client.generate_json(system, user)
    allowed = ACTIONS_PLAIN if variant == "plain" else ACTIONS_PERSONA
    action = str(data.get("action") or "")
    if action not in allowed:
        raise ValueError(f"selector: unknown action: {action!r}")
    reason = str(data.get("reason") or "")[:200]
    rotation = state["rotation_theme"]
    decision = {"action": action, "reason": reason, "method": "gemini", "target": None}
    if action == "question":
        decision["theme"] = "question"
    elif action == "memory":
        decision["theme"] = rotation
        shown = state.get("shown_episodes") or []
        target = None
        ref = data.get("target_ref")
        if isinstance(ref, int) and 0 <= ref < len(shown):
            chosen = shown[ref]
            target = {"date": chosen["date"], "summary": chosen["summary"]}
        elif shown:
            chosen = shown[-1]
            target = {"date": chosen["date"], "summary": chosen["summary"]}
        if target is None:
            # 対象になる記憶が無いのに memory が選ばれたら既定案へ倒す
            decision.update(
                action="theme",
                theme=rotation,
                reason=(reason + "（対象の記憶が無いため theme に変更）").strip(),
            )
        else:
            decision["target"] = target
    else:  # theme / rest
        decision["theme"] = rotation
    return decision


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def decide(
    persona: dict,
    day: date,
    variant: str,
    rotation_theme: str,
    client=None,
    metrics: dict | None = None,
    goals: list[dict] | None = None,
) -> dict:
    """今日の一手を選ぶ。

    戻り値: {"action", "theme", "reason", "method", "target"}
    client が generate_json を持てばモデル選択、失敗したらルールベースへ
    フォールバックする。client=None／StubClient のときは最初からルールベース。
    """
    state = build_state(persona, day, variant, rotation_theme, metrics=metrics, goals=goals)
    if client is not None and hasattr(client, "generate_json"):
        try:
            return decide_with_model(state, persona, day, variant, client)
        except Exception:
            decision = decide_with_rules(state, day, variant)
            decision["reason"] = "モデル選択に失敗したためルールで選択: " + decision["reason"]
            return decision
    return decide_with_rules(state, day, variant)
