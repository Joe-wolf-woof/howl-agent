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
"""
from __future__ import annotations

from datetime import date, datetime

from . import store
from .checker import generate_checked, passes_gate
from .filters import check_post
from .generator import make_client
from .persona import load_persona

MAX_POSTS_PER_DAY = 2


def theme_for_date(persona: dict, day: date) -> str:
    """その日のテーマを日付ローテーションで決める（決定的）。"""
    themes = list(persona["themes"].keys())
    return themes[day.toordinal() % len(themes)]


def variant_for_date(day: date) -> str:
    """その日の variant を日替わりで交互に切り替える（決定的）。"""
    return "persona" if day.toordinal() % 2 == 0 else "plain"


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

    theme = theme_for_date(persona, day)
    variant = variant_for_date(day)
    result.update(theme=theme, variant=variant)

    if client is None:
        client = make_client(stub=stub)
    text, check = generate_checked(
        persona, theme, store.recent_episodes(5), client, variant=variant
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
        text, theme, persona["version"], variant=variant, check=check
    )
    result["draft_id"] = draft["id"]

    simulate = dry_run or stub
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
