"""X APIアダプタ（骨格）。

前提（JOE側の準備が完了してから有効化する）:
  - キャラ専用Xアカウントの作成
  - X API（従量課金）開発者登録と初回クレジット取得
  - 認証情報を環境変数に設定: X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET

料金メモ（2026-11調査）: テキスト投稿 $0.015/件、URL付き $0.20/件。
だから投稿文にURLを入れない設計になっている（filters.py で機械チェック）。

TODO(登録後): OAuth 1.0a 署名つきで POST /2/tweets を呼ぶ実装をここに入れる。
現時点では未登録状態を正直にエラーとして返す（ダミー投稿はしない）。
"""
from __future__ import annotations

import os

REQUIRED_ENV = ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"]


class XApiAdapter:
    name = "x"

    def __init__(self):
        missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
        if missing:
            raise RuntimeError(
                "X API の認証情報が未設定です: " + ", ".join(missing)
                + "（Xアカウント作成とAPI登録が完了してから使えます）"
            )

    def post(self, text: str) -> dict:
        raise NotImplementedError(
            "X API への実投稿は未実装です。開発者登録の完了後に POST /2/tweets を実装します"
        )
