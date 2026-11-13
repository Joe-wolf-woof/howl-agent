"""週次振り返り（目標駆動: learn / structure）。

直近 N 日（既定7日）の運用を集計し、「分かったこと（learnings）」と
「来週の方針（policy）」を作って data/reflections.jsonl に記録する。
selector は最新の reflection を状態（learnings）として読み、翌週の
選択に反映する（目標 learn）。記録そのものが目標 structure の実践になる。

集計対象:
  - 投稿エピソード: variant 別・テーマ別の件数
  - decisions.jsonl: 行動選択（memory/theme/question/rest）の内訳
  - 下書き: 一貫性チェッカーのスコア平均
  - 反応: 実モードのときだけ X の public_metrics（失敗したら静かに省略）

振り返り文の生成は Gemini（generate_json）が本命。使えない場合は
数値からテンプレートで組み立てる（method に "gemini" / "template" を記録）。
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from . import store

DEFAULT_DAYS = 7


def _in_period(day_str, start: date, end: date) -> bool:
    try:
        d = date.fromisoformat(str(day_str)[:10])
    except (TypeError, ValueError):
        return False
    return start <= d <= end


def collect_stats(days: int = DEFAULT_DAYS, end: date | None = None) -> dict:
    """対象期間の運用統計を集計する。"""
    end = end or datetime.now(store.JST).date()
    start = end - timedelta(days=days - 1)

    episodes = store.load_episodes()
    posts = [
        e
        for e in episodes
        if e.get("type") == "post" and _in_period(e.get("date"), start, end)
    ]
    decisions = [
        d for d in store.load_decisions() if _in_period(d.get("date"), start, end)
    ]

    scores = [
        d["check"]["total"]
        for d in store.list_drafts()
        if d.get("check")
        and isinstance(d["check"].get("total"), (int, float))
        and _in_period(d.get("created_at"), start, end)
    ]
    if not scores:
        # 下書き側に採点が無ければ、投稿エピソードに残る check_total で代替する
        scores = [
            e["check_total"]
            for e in posts
            if isinstance(e.get("check_total"), (int, float))
        ]

    return {
        "period": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
        "posts": len(posts),
        "by_variant": dict(Counter(e.get("variant") or "phase-a" for e in posts)),
        "by_theme": dict(Counter(e.get("theme") or "?" for e in posts)),
        "decisions": dict(Counter(d.get("action") or "?" for d in decisions)),
        "avg_check_score": round(sum(scores) / len(scores), 1) if scores else None,
        "metrics": None,
    }


def fetch_period_metrics(stats: dict) -> dict | None:
    """直近投稿の反応（public_metrics の平均）を取得する。失敗したら None。

    読み取りのみ。認証情報が無い・API が失敗した場合は静かに諦める
    （振り返り自体は数値なしでも成立させる）。
    """
    try:
        from .adapters.x_api import XApiAdapter

        start = date.fromisoformat(stats["period"]["start"])
        end = date.fromisoformat(stats["period"]["end"])
        posted = [d for d in store.list_drafts("posted") if d.get("posted_at")]
        posted.sort(key=lambda d: d["posted_at"])
        in_period = [
            d for d in posted if _in_period(d["posted_at"], start, end)
        ]
        targets = in_period or posted[-5:]
        ids = [
            (d.get("post_result") or {}).get("tweet_id")
            for d in targets
        ]
        ids = [i for i in ids if i]
        if not ids:
            return None
        metrics = XApiAdapter().public_metrics(ids)
        rows = [metrics[i] for i in ids if i in metrics]
        if not rows:
            return None
        n = len(rows)
        return {
            "count": n,
            "like_avg": round(sum(r.get("like_count", 0) for r in rows) / n, 1),
            "reply_avg": round(sum(r.get("reply_count", 0) for r in rows) / n, 1),
            "impression_avg": round(
                sum(r.get("impression_count", 0) for r in rows) / n, 1
            ),
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 振り返り文の生成（Gemini 本命／テンプレート フォールバック）
# ---------------------------------------------------------------------------

def _template_reflection(stats: dict) -> tuple[list[str], str]:
    """数値だけから決定的に組み立てる振り返り（モデルが使えない場合）。"""
    period = stats["period"]
    learnings: list[str] = []
    learnings.append(
        f"{period['start']}〜{period['end']} に {stats['posts']} 件投稿した"
        f"（variant別: {stats['by_variant']}）"
    )
    if stats["by_theme"]:
        theme, count = Counter(stats["by_theme"]).most_common(1)[0]
        learnings.append(f"最も多かったテーマは {theme}（{count} 件）だった")
    if stats["decisions"]:
        learnings.append(f"行動選択の内訳は {stats['decisions']} だった")
    if stats["avg_check_score"] is not None:
        learnings.append(
            f"一貫性チェッカーの平均は {stats['avg_check_score']} 点だった"
        )
    metrics = stats.get("metrics")
    if metrics:
        learnings.append(
            f"反応の平均は いいね {metrics['like_avg']} / 返信 {metrics['reply_avg']}"
            f" / インプレッション {metrics['impression_avg']}（{metrics['count']} 件）だった"
        )
    if stats["posts"] == 0:
        policy = "投稿がなかった週。来週はまず巡回表どおりに1件出すことを優先する。"
    elif stats["by_theme"]:
        theme = Counter(stats["by_theme"]).most_common(1)[0][0]
        policy = (
            f"来週は実績の多い {theme} を軸にしつつ、反応を見て選び方を調整する。"
        )
    else:
        policy = "来週も継続し、反応を見て選び方を調整する。"
    return learnings, policy


def _model_reflection(stats: dict, client) -> tuple[list[str], str]:
    import json

    system = (
        "あなたはAIキャラクター『ハウル』のX運用を振り返る編集長です。"
        "与えられた運用統計から、分かったことと来週の方針を作ってください。"
        "出力は次のJSON形式のみ。前置き・解説・コードフェンスは付けないこと: "
        '{"learnings": ["分かったこと（各80字以内、最大4件）"], '
        '"policy": "来週の方針（1〜2文）"}'
    )
    user = (
        f"運用統計:\n{json.dumps(stats, ensure_ascii=False, indent=2)}\n\n"
        "数字に表れた事実だけを根拠に、具体的に書いてください。JSONのみで答えること。"
    )
    data = client.generate_json(system, user)
    learnings = [str(x)[:200] for x in (data.get("learnings") or [])][:5]
    policy = str(data.get("policy") or "")[:300]
    if not learnings or not policy:
        raise ValueError("review: empty learnings/policy from model")
    return learnings, policy


def build_reflection(
    stats: dict, client=None
) -> tuple[list[str], str, str]:
    """(learnings, policy, method) を返す。モデル失敗時はテンプレートへ。"""
    if client is not None and hasattr(client, "generate_json"):
        try:
            learnings, policy = _model_reflection(stats, client)
            return learnings, policy, "gemini"
        except Exception:
            learnings, policy = _template_reflection(stats)
            return learnings, policy, "template"
    learnings, policy = _template_reflection(stats)
    return learnings, policy, "template"


# ---------------------------------------------------------------------------
# Markdown への追記（structure 目標の「記録」の受け皿）
# ---------------------------------------------------------------------------

def render_markdown_section(record: dict) -> str:
    stats = record["stats"]
    period = stats["period"]
    lines = [
        f"## 週次振り返り（{period['start']}〜{period['end']}）",
        "",
        f"- 投稿: {stats['posts']} 件（variant別: {stats['by_variant']}）",
        f"- テーマ別: {stats['by_theme']}",
    ]
    if stats.get("metrics"):
        m = stats["metrics"]
        lines.append(
            f"- 反応（平均）: いいね {m['like_avg']} / 返信 {m['reply_avg']}"
            f" / インプレッション {m['impression_avg']}（{m['count']} 件）"
        )
    if stats["decisions"]:
        lines.append(f"- 行動選択: {stats['decisions']}")
    if stats["avg_check_score"] is not None:
        lines.append(f"- チェッカー平均: {stats['avg_check_score']} 点")
    lines += ["", "### 学び", ""]
    lines += [f"- {x}" for x in record["learnings"]]
    lines += ["", "### 来週の方針", "", record["policy"], ""]
    return "\n".join(lines)


def append_markdown(path, record: dict) -> None:
    path = Path(path)
    section = render_markdown_section(record)
    if path.exists():
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n" + section)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("# 運用の学び（週次振り返り）\n\n" + section)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def run_review(
    days: int = DEFAULT_DAYS,
    client=None,
    fetch_metrics: bool = False,
    today: date | None = None,
    append_md=None,
) -> dict:
    """週次振り返りを1回実行し、記録して返す。

    統計を集計 →（任意で）反応を取得 → 振り返りを生成 →
    data/reflections.jsonl に追記 →（任意で）Markdown にも追記する。
    """
    today = today or datetime.now(store.JST).date()
    stats = collect_stats(days=days, end=today)
    if fetch_metrics:
        stats["metrics"] = fetch_period_metrics(stats)
    learnings, policy, method = build_reflection(stats, client)
    record = {
        "date": today.isoformat(),
        "period": stats["period"],
        "stats": stats,
        "learnings": learnings,
        "policy": policy,
        "method": method,
    }
    store.append_reflection(record)
    if append_md:
        append_markdown(append_md, record)
    return record
