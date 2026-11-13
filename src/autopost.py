"""全自動投稿（Phase B）と A/B ローテーション。

Phase A（承認＋編集制）に対し、Phase B は承認を挟まずに投稿する。
暴走を防ぐ安全装置を必ず通す:
  - キルスイッチ: 環境変数 HOWL_AUTOPOST=off、または data/AUTOPOST_OFF ファイル
  - 1日2件上限: 当日の投稿済みエピソード数で判定
  - 一貫性ゲート: persona の日は総合80点以上が必須。plain の日は安全性の軸が
    満点（20/20）かつ総合60点超えが必須（A/B の条件を保ちつつ品質の下限を担保）
  - 既存 NG フィルタ（filters.check_post）も通過必須

A/B（仮説H1: ペルソナ＋記憶の一貫性が反応率を上げる）:
  variant は日替わりで交互に切り替える。
    "persona" = 記憶文脈＋ペルソナ指示あり（従来どおり）
    "plain"   = 記憶文脈なし・ペルソナ指示なしの素の生成
  テーマも日付ローテーションで決める（曜日や日付に偏らせないため ordinal ベース）。

Phase 3: 投稿前に行動選択レイヤー（selector）が状態を見て今日の一手を選ぶ。
巡回表のテーマは「既定案」になり、記憶について書く／問いかける／休むも選べる。
選択は result と下書きに decision として残り、実運用では data/decisions.jsonl
にも記録される。安全装置（ゲート・フィルタ・上限・キルスイッチ）は不変。
"""
from __future__ import annotations

from datetime import date, datetime

from . import store
from .checker import generate_checked, passes_gate
from .filters import check_post
from .generator import make_client
from .persona import load_persona
from .selector import decide

MAX_POSTS_PER_DAY = 2


THEME_CYCLE_SLOTS = 20


def theme_cycle(persona: dict) -> list[str]:
    """テーマ配分（重み）を20日分の巡回パターンに変換する（決定的）。

    Smooth Weighted Round-Robin で重みに比例した回数を均等に散らす。
    例: psychology 0.6 → 20日中12日、observation 0.2 → 4日。
    """
    weights = persona["themes"]
    total = sum(weights.values())
    current = {t: 0.0 for t in weights}
    cycle: list[str] = []
    for _ in range(THEME_CYCLE_SLOTS):
        for t in weights:
            current[t] += weights[t] / total
        pick = max(weights, key=lambda t: current[t])
        cycle.append(pick)
        current[pick] -= 1.0
    return cycle


def theme_for_date(persona: dict, day: date) -> str:
    """その日のテーマを配分どおりの日付ローテーションで決める（決定的）。"""
    cycle = theme_cycle(persona)
    return cycle[day.toordinal() % len(cycle)]


def variant_for_date(day: date) -> str:
    """その日の variant を日替わりで交互に切り替える（決定的）。"""
    return "persona" if day.toordinal() % 2 == 0 else "plain"


def _fetch_recent_metrics() -> dict | None:
    """知覚: 直近の投稿への反応（public_metrics）を取得する。

    実投稿モード専用。認証情報が無い・API が失敗した場合は静かに None を返す
    （選択はメトリクスなしでも成立する）。
    """
    try:
        from .adapters.x_api import XApiAdapter

        ids = []
        for d in store.list_drafts("posted")[-5:]:
            tid = (d.get("post_result") or {}).get("tweet_id")
            if tid:
                ids.append(tid)
        if not ids:
            return None
        return XApiAdapter().public_metrics(ids) or None
    except Exception:
        return None


def run_autopost(
    *,
    dry_run: bool = False,
    stub: bool = False,
    day: date | None = None,
    client=None,
    adapter=None,
) -> dict:
    """自動投稿を1回実行し、結果の dict を返す（例外は投げない方針）。

    status: "posted"（実投稿した）/ "dry-run"（投稿していない）/
            "rested"（行動選択で今日は休むと判断。投稿しない）/
            "aborted"（安全装置・チェッカー・フィルタで中止。reason に詳細）
    stub=True のときは生成を StubClient にし、投稿も dry-run 扱いにする
    （検証で実投稿が起きないための二重の安全策）。
    """
    persona = load_persona()
    day = day or datetime.now(store.JST).date()
    result: dict = {"date": day.isoformat(), "posted": False}

    kill = store.autopost_kill_reason()
    if kill:
        result.update(status="aborted", reason=f"kill-switch:{kill}")
        return result

    posts_today = store.posted_count_on(day.isoformat())
    result["posts_today"] = posts_today
    if posts_today >= MAX_POSTS_PER_DAY:
        result.update(status="aborted", reason="daily-limit")
        return result

    rotation_theme = theme_for_date(persona, day)
    variant = variant_for_date(day)
    result.update(variant=variant)

    if client is None:
        client = make_client(stub=stub)
    simulate = dry_run or stub

    # Phase 3: 行動選択。知覚（反応メトリクス）は実投稿モードのときだけ取得する
    metrics = None
    if not simulate:
        metrics = _fetch_recent_metrics()
    decision = decide(persona, day, variant, rotation_theme, client, metrics=metrics)
    result.update(decision=decision)
    if not simulate:
        store.append_decision(
            {
                "date": day.isoformat(),
                "variant": variant,
                "action": decision["action"],
                "theme": decision["theme"],
                "reason": decision["reason"],
                "method": decision["method"],
                "target": decision.get("target"),
            }
        )

    theme = decision["theme"]
    result["theme"] = theme
    if decision["action"] == "rest":
        result.update(status="rested", reason=decision.get("reason") or "rest")
        return result

    episodes = store.context_episodes(5)
    directive = None
    if decision["action"] == "memory" and decision.get("target"):
        target = decision["target"]
        fingerprint = (str(target["date"])[:10], str(target["summary"]))
        if not any(store.episode_fingerprint(e) == fingerprint for e in episodes):
            episodes = episodes + [
                {"date": target["date"], "type": "shown", "summary": target["summary"]}
            ]
        directive = (
            f"今日はこの出来事について書くこと。対象の出来事: "
            f"{target['date']}: {target['summary']}"
        )
    text, check = generate_checked(
        persona, theme, episodes, client, variant=variant, directive=directive
    )
    result.update(text=text, check=check)
    if not passes_gate(check, variant):
        if variant == "plain":
            gate = check.get("gate") or {}
            reason = (
                "safety-failed"
                if check["axes"].get("safety", 0) < gate.get("safety_required", 20)
                else "gate-failed"
            )
        else:
            reason = "check-failed"
        result.update(status="aborted", reason=reason)
        return result

    issues = check_post(text, persona)
    if issues:
        result.update(status="aborted", reason="filter", filter_issues=issues)
        return result

    draft = store.create_draft(
        text, theme, persona["version"], variant=variant, check=check, decision=decision
    )
    result["draft_id"] = draft["id"]

    if adapter is None:
        if simulate:
            from .adapters.dryrun import DryRunAdapter

            adapter = DryRunAdapter()
        else:
            from .adapters.x_api import XApiAdapter

            adapter = XApiAdapter()
    post_result = adapter.post(text)
    result["post_result"] = post_result
    if simulate:
        # dry-run では下書きを投稿済みにせず、エピソードも残さない
        # （当日の投稿数カウントと A/B 測定を汚さないため）
        result.update(status="dry-run")
    else:
        store.mark_posted(draft["id"], post_result)
        result.update(status="posted", posted=True)
    return result
