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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
