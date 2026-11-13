"""ハウル投稿エンジン CLI（Phase A: 承認＋編集制）。

使い方:
  python -m src.cli generate [--theme psychology] [--stub]
      投稿案を生成し、下書きとして保存する。data/drafts/<id>.txt が編集用ファイル。
  python -m src.cli drafts [--status draft]
      下書き一覧を表示する。
  python -m src.cli approve <id>
      編集用 .txt を読み戻し、生成文との差分を記録して承認済みにする。
      （JOEはこの前に .txt を編集して自分の感性を加える）
  python -m src.cli post <id> [--adapter dryrun|x]
      承認済みの下書きを投稿する。デフォルトは dryrun（実投稿しない）。

Phase 2:
  python -m src.cli check <id> [--stub]
      既存の下書きを一貫性チェッカーで採点する（5軸×20点、合格80点）。
  python -m src.cli generate --check [--theme X] [--stub]
      生成→採点を繰り返し、合格か最良の下書きを保存する。
  python -m src.cli autopost [--dry-run] [--stub]
      全自動投稿（Phase B）。安全装置とチェッカー必須。A/B variant は日替わり。
  python -m src.cli report [--no-metrics]
      投稿済みエピソードを variant 別に集計し、可能なら X の指標平均も出す。

Phase 3:
  python -m src.cli import-episodes <path>
      『ハウリング』の「見せた記憶」書き出しJSONを中央の記憶へ取り込む（冪等）。
  python -m src.cli export-memory <path>
      中央ストアからアプリ形式の memory.json を書き出す。
      autopost は投稿前に行動選択レイヤーが今日の一手を選ぶようになった。
"""
from __future__ import annotations

import argparse
import sys

from . import store
from .checker import PASS_THRESHOLD, check_draft, generate_checked
from .config import load_dotenv
from .filters import check_post
from .generator import choose_theme, generate_post, make_client
from .persona import load_persona


def _print_check(result: dict) -> None:
    gate = result.get("gate")
    if gate and gate["kind"] == "plain":
        verdict = "PASS" if gate["passed"] else "FAIL"
        print(
            f"一貫性チェック: {result['total']}/100 ({result['method']}) "
            f"ゲート（安全性 {result['axes'].get('safety', 0)}/20 満点必須・総合 {gate['total_threshold']} 点超え）: {verdict}"
        )
    elif gate:
        verdict = "PASS" if gate["passed"] else "FAIL"
        print(
            f"一貫性チェック: {result['total']}/100 ({result['method']}) {verdict}"
            f"（合格ライン {gate['threshold']}）"
        )
    else:
        verdict = "PASS" if result["total"] >= PASS_THRESHOLD else "FAIL"
        print(
            f"一貫性チェック: {result['total']}/100 ({result['method']}) {verdict}"
            f"（合格ライン {PASS_THRESHOLD}）"
        )
    for axis, score in result["axes"].items():
        print(f"  {axis:<8} {score:>2}/20")
    for reason in result.get("reasons", []):
        print(f"  - {reason}")


def cmd_generate(args) -> int:
    persona = load_persona()
    theme = choose_theme(persona, args.theme)
    client = make_client(stub=args.stub)
    check = None
    if args.check:
        text, check = generate_checked(persona, theme, store.context_episodes(5), client)
    else:
        text = generate_post(persona, theme, store.context_episodes(5), client)
    issues = check_post(text, persona)
    draft = store.create_draft(text, theme, persona["version"], check=check)
    if issues:
        store.update_draft(draft["id"], filter_issues=issues)
    print(f"draft {draft['id']} (theme={theme})")
    print(text)
    print(f"編集用ファイル: data/drafts/{draft['id']}.txt")
    if check is not None:
        print(f"試行回数: {check.get('attempts', 1)}")
        _print_check(check)
    if issues:
        print("フィルタ警告: " + " / ".join(issues))
    return 0


def cmd_check(args) -> int:
    persona = load_persona()
    draft = store.get_draft(args.id)
    text = store.final_text(draft)
    client = None
    if not args.stub:
        try:
            client = make_client()
        except RuntimeError:
            client = None  # キーがなければルールベース採点にフォールバック
    result = check_draft(text, draft["theme"], persona, client)
    store.update_draft(args.id, check=result)
    print(f"check {draft['id']} (theme={draft['theme']})")
    print(text)
    _print_check(result)
    return 0 if result["total"] >= PASS_THRESHOLD else 1


def cmd_drafts(args) -> int:
    for d in store.list_drafts(args.status):
        text = store.final_text(d)
        print(f"{d['id']} [{d['status']}] ({d['theme']}) {text[:60]}")
    return 0


def cmd_approve(args) -> int:
    persona = load_persona()
    draft = store.approve(args.id)
    text = store.final_text(draft)
    issues = check_post(text, persona)
    print(f"approved {draft['id']} (edited={draft['was_edited']})")
    print(text)
    if issues:
        print("フィルタ警告: " + " / ".join(issues))
    return 0


def cmd_verify(args) -> int:
    from .adapters.x_api import XApiAdapter

    try:
        me = XApiAdapter().me()
    except RuntimeError as e:
        print(f"認証確認に失敗: {e}", file=sys.stderr)
        return 1
    print(f"認証OK: @{me['username']}（{me['name']}）として接続できました")
    return 0


def _consumer_credentials() -> tuple[str, str]:
    import os

    key = os.environ.get("X_API_KEY", "")
    secret = os.environ.get("X_API_SECRET", "")
    if not key or not secret:
        raise RuntimeError("X_API_KEY / X_API_SECRET が未設定です（.env を確認してください）")
    return key, secret


def cmd_authorize_url(args) -> int:
    from .adapters import oauth_flow

    try:
        key, secret = _consumer_credentials()
        request = oauth_flow.fetch_request_token(key, secret)
    except RuntimeError as e:
        print(f"リクエストトークン取得に失敗: {e}", file=sys.stderr)
        return 1
    oauth_flow.save_pending(request)
    print("次のURLを、ハウル（@AgentName_Howl）でログインしたブラウザで開いて承認してください：")
    print(oauth_flow.authorize_url(request["oauth_token"]))
    print("承認後に飛ぶ http://127.0.0.1 のURLに含まれる oauth_verifier の値を控えてください。")
    return 0


def cmd_authorize_complete(args) -> int:
    from .adapters import oauth_flow
    from .config import update_env_values

    try:
        key, secret = _consumer_credentials()
        pending = oauth_flow.load_pending()
        result = oauth_flow.exchange_access_token(key, secret, pending, args.verifier.strip())
    except RuntimeError as e:
        print(f"Access Token の交換に失敗: {e}", file=sys.stderr)
        return 1
    update_env_values(
        {
            "X_ACCESS_TOKEN": result["oauth_token"],
            "X_ACCESS_SECRET": result["oauth_token_secret"],
        }
    )
    oauth_flow.clear_pending()
    print(f"交換完了: @{result.get('screen_name', '?')} 用の Access Token を .env に保存しました")
    return cmd_verify(args)


def cmd_post(args) -> int:
    draft = store.get_draft(args.id)
    if draft["status"] != "approved":
        print(f"エラー: 承認済みの下書きだけ投稿できます（現在: {draft['status']}）", file=sys.stderr)
        return 1
    if args.adapter == "x":
        from .adapters.x_api import XApiAdapter

        adapter = XApiAdapter()
    else:
        from .adapters.dryrun import DryRunAdapter

        adapter = DryRunAdapter()
    result = adapter.post(store.final_text(draft))
    store.mark_posted(args.id, result)
    print(f"posted {args.id} via {adapter.name}: {result['status']}")
    return 0


def cmd_autopost(args) -> int:
    from .autopost import run_autopost

    result = run_autopost(dry_run=args.dry_run, stub=args.stub)
    print(
        f"autopost {result['date']}: status={result['status']}"
        + (f" reason={result['reason']}" if result.get("reason") else "")
    )
    if result.get("decision"):
        d = result["decision"]
        print(f"decision: action={d['action']} ({d['method']}) - {d['reason']}")
    if result.get("theme"):
        print(f"theme={result['theme']} variant={result['variant']}")
    if result.get("check"):
        _print_check(result["check"])
    if result.get("text"):
        print(result["text"])
    if result.get("filter_issues"):
        print("フィルタ警告: " + " / ".join(result["filter_issues"]))
    if result["status"] == "posted":
        print(f"投稿しました (draft {result['draft_id']})")
    return 0 if result["status"] in ("posted", "dry-run", "rested") else 1


def cmd_import_episodes(args) -> int:
    import json
    from pathlib import Path

    path = Path(args.path)
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"読み込みに失敗: {e}", file=sys.stderr)
        return 1
    if not isinstance(items, list):
        print("エラー: 書き出しJSONは配列である必要があります", file=sys.stderr)
        return 1
    added, skipped = store.import_shown_episodes(items)
    print(f"import-episodes: {added} 件を取り込み、{skipped} 件をスキップしました")
    return 0


def cmd_export_memory(args) -> int:
    posts, episodes = store.export_app_memory(args.path)
    print(f"export-memory: posts {posts} 件 / episodes {episodes} 件を {args.path} に書き出しました")
    return 0


def cmd_report(args) -> int:
    episodes = [e for e in store.load_episodes() if e.get("type") == "post"]
    by_variant: dict[str, list[dict]] = {}
    for e in episodes:
        by_variant.setdefault(e.get("variant") or "phase-a", []).append(e)
    print(f"投稿済みエピソード: {len(episodes)} 件")
    for variant, eps in sorted(by_variant.items()):
        print(f"  {variant}: {len(eps)} 件")

    # X の指標は、実投稿済み下書きの tweet_id がある場合のみ取得を試みる。
    ids_by_variant: dict[str, list[str]] = {}
    for d in store.list_drafts("posted"):
        tweet_id = (d.get("post_result") or {}).get("tweet_id")
        if tweet_id:
            ids_by_variant.setdefault(d.get("variant", "persona"), []).append(tweet_id)
    if not ids_by_variant:
        print("指標: 実投稿の tweet_id がないため、保存データのみで集計しました")
        return 0
    if args.no_metrics:
        print("指標: --no-metrics のため取得しませんでした")
        return 0
    try:
        from .adapters.x_api import XApiAdapter

        metrics = XApiAdapter().public_metrics(
            [tid for ids in ids_by_variant.values() for tid in ids]
        )
    except Exception as e:  # 認証情報なし・API障害時は保存データのみで劣化
        print(f"指標: 取得できませんでした（{e}）")
        return 0
    if not metrics:
        print("指標: 取得結果が空でした")
        return 0
    print("指標（variant 別平均）:")
    for variant, ids in sorted(ids_by_variant.items()):
        rows = [metrics[i] for i in ids if i in metrics]
        if not rows:
            continue
        n = len(rows)
        avg = lambda key: sum(r.get(key, 0) for r in rows) / n  # noqa: E731
        print(
            f"  {variant}: いいね {avg('like_count'):.1f} / 返信 {avg('reply_count'):.1f}"
            f" / インプレッション {avg('impression_count'):.1f}（{n} 件）"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="howl-agent", description="ハウル投稿エンジン（Phase A）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("generate", help="投稿案を生成して下書きに保存")
    p.add_argument("--theme", default=None, help="psychology/observation/seasons/question/events")
    p.add_argument("--stub", action="store_true", help="APIキーなしのサンプル生成で検証する")
    p.add_argument("--check", action="store_true", help="生成後に一貫性チェックし、80点未満なら再生成する（Phase 2）")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("check", help="既存の下書きを一貫性チェッカーで採点する（Phase 2）")
    p.add_argument("id")
    p.add_argument("--stub", action="store_true", help="LLM採点を使わずルールベースで採点する")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("autopost", help="全自動投稿（Phase B、安全装置つき）")
    p.add_argument("--dry-run", action="store_true", help="実投稿せず dryrun アダプタで内容を確認する")
    p.add_argument("--stub", action="store_true", help="サンプル生成＋dry-run で検証する")
    p.set_defaults(func=cmd_autopost)

    p = sub.add_parser("report", help="投稿実績を variant 別に集計する（Phase 2）")
    p.add_argument("--no-metrics", action="store_true", help="X API の指標を取得しない")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("import-episodes", help="『ハウリング』の見せた記憶を取り込む（Phase 3）")
    p.add_argument("path", help="アプリの書き出しJSON（[{date, kind:'shown', text}]）")
    p.set_defaults(func=cmd_import_episodes)

    p = sub.add_parser("export-memory", help="アプリ形式の memory.json を書き出す（Phase 3）")
    p.add_argument("path", help="書き出し先のパス")
    p.set_defaults(func=cmd_export_memory)

    p = sub.add_parser("drafts", help="下書き一覧")
    p.add_argument("--status", default=None)
    p.set_defaults(func=cmd_drafts)

    p = sub.add_parser("approve", help="編集済み .txt を読み戻して承認する")
    p.add_argument("id")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("post", help="承認済み下書きを投稿する")
    p.add_argument("id")
    p.add_argument("--adapter", default="dryrun", choices=["dryrun", "x"])
    p.set_defaults(func=cmd_post)

    p = sub.add_parser("verify", help="X APIの認証だけを確認する（投稿しない）")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("authorize-url", help="別アカウント用の認可URLを発行する（3脚フロー手順1）")
    p.set_defaults(func=cmd_authorize_url)

    p = sub.add_parser("authorize-complete", help="oauth_verifier と Access Token を交換する（手順2）")
    p.add_argument("verifier")
    p.set_defaults(func=cmd_authorize_complete)

    args = parser.parse_args(argv)
    load_dotenv()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
