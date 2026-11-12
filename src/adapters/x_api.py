"""X APIアダプタ。

認証は OAuth 1.0a user context（src/adapters/oauth1.py、標準ライブラリ実装）。
認証情報は環境変数から読む:
  X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET
（値はコードやリポジトリに絶対に含めない。Secure Vault 経由で受け取り、
 実行環境の環境変数として渡すこと）

前提（JOE側）:
  - キャラ専用Xアカウント: 作成済み（@AgentName_Howl、2026-11-12）
  - X API（従量課金）の開発者登録と初回クレジット取得: 未完了 ← 残作業

料金メモ（2026-11調査）: テキスト投稿 $0.015/件、URL付き $0.20/件。
だから投稿文にURLを入れない設計になっている（filters.py で機械チェック）。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .oauth1 import build_authorization_header

REQUIRED_ENV = ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"]
TWEETS_URL = "https://api.x.com/2/tweets"


class XApiAdapter:
    name = "x"

    def __init__(self):
        missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
        if missing:
            raise RuntimeError(
                "X API の認証情報が未設定です: " + ", ".join(missing)
                + "（開発者登録を完了し、認証情報を環境変数で渡してください）"
            )
        self.consumer_key = os.environ["X_API_KEY"]
        self.consumer_secret = os.environ["X_API_SECRET"]
        self.access_token = os.environ["X_ACCESS_TOKEN"]
        self.token_secret = os.environ["X_ACCESS_SECRET"]

    def post(self, text: str) -> dict:
        # JSON ボディは OAuth 署名の対象パラメータに含めない（仕様通り）
        auth = build_authorization_header(
            method="POST",
            url=TWEETS_URL,
            consumer_key=self.consumer_key,
            consumer_secret=self.consumer_secret,
            access_token=self.access_token,
            token_secret=self.token_secret,
        )
        req = urllib.request.Request(
            TWEETS_URL,
            data=json.dumps({"text": text}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": auth},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"X API post failed: HTTP {e.code}: {body}") from e
        return {"status": "posted", "adapter": self.name, "tweet_id": data["data"]["id"]}
