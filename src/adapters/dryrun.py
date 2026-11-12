"""DryRunアダプタ: 実際には投稿せず、data/outbox.jsonl に記録する。

X API の登録が済むまでの Phase A 開発・検証用。
outbox は「投稿したつもり」の記録であり、実投稿ではないことを
結果オブジェクトにも明記する。
"""
from __future__ import annotations

from ..store import append_outbox, now_iso


class DryRunAdapter:
    name = "dryrun"

    def post(self, text: str) -> dict:
        record = {"adapter": self.name, "posted_at": now_iso(), "text": text, "simulated": True}
        append_outbox(record)
        return {"status": "dry-run", "simulated": True, "adapter": self.name}
