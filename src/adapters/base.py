"""配信アダプタの共通インターフェース。

配信先（X API、将来のThreads等）はこの interface の実装として差し替える。
X API の仕様変更史への備えとして、生成・承認の本体と配信を疎結合にする
（設計書 §4.2 配信スケジューラ）。
"""
from __future__ import annotations

from typing import Protocol


class PostAdapter(Protocol):
    name: str

    def post(self, text: str) -> dict:
        """投稿を実行し、結果の記録（status等）を持つ dict を返す。"""
        ...
