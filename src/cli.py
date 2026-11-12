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
"""
from __future__ import annotations

import argparse
import sys

from . import store
from .config import load_dotenv
from .filters import check_post
from .generator import choose_theme, generate_post, make_client
from .persona import load_persona


def cmd_generate(args) -> int:
    persona = load_persona()
    theme = choose_theme(persona, args.theme)
    client = make_client(stub=args.stub)
    text = generate_post(persona, theme, store.recent_episodes(5), client)
    issues = check_post(text, persona)
    draft = store.create_draft(text, theme, persona["version"])
    if issues:
        drafts = store._load_drafts()
        for d in drafts:
            if d["id"] == draft["id"]:
                d["filter_issues"] = issues
        store._save_drafts(drafts)
    print(f"draft {draft['id']} (theme={theme})")
    print(text)
    print(f"編集用ファイル: data/drafts/{draft['id']}.txt")
    if issues:
        print("フィルタ警告: " + " / ".join(issues))
    return 0


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="howl-agent", description="ハウル投稿エンジン（Phase A）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("generate", help="投稿案を生成して下書きに保存")
    p.add_argument("--theme", default=None, help="psychology/observation/seasons/question/events")
    p.add_argument("--stub", action="store_true", help="APIキーなしのサンプル生成で検証する")
    p.set_defaults(func=cmd_generate)

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
