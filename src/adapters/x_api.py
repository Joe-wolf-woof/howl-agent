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

    def _auth_header(self, method: str, url: str) -> str:
        return build_authorization_header(
            method=method,
            url=url,
            consumer_key=self.consumer_key,
            consumer_secret=self.consumer_secret,
            access_token=self.access_token,
            token_secret=self.token_secret,
        )

    def me(self) -> dict:
        """認証確認用: 自分自身のアカウント情報を読む（投稿しない読み取り確認）。"""
        url = "https://api.x.com/2/users/me"
        req = urllib.request.Request(
            url, headers={"Authorization": self._auth_header("GET", url)}, method="GET"
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"X API auth check failed: HTTP {e.code}: {body}") from e
        d = data["data"]
        return {"id": d["id"], "username": d["username"], "name": d["name"]}

    def public_metrics(self, tweet_ids: list[str]) -> dict[str, dict]:
        """ツイートの public_metrics をまとめて取得する（Phase 2 の測定用）。

        戻り値: {tweet_id: {"like_count": int, "reply_count": int,
        "impression_count": int, ...}}。取得できなかった ID は含まれない。
        クエリパラメータは oauth1 側で署名対象に含められる。
        """
        result: dict[str, dict] = {}
        for i in range(0, len(tweet_ids), 100):
            chunk = [t for t in tweet_ids[i : i + 100] if t]
            if not chunk:
                continue
            url = (
                "https://api.x.com/2/tweets?ids="
                + ",".join(chunk)
                + "&tweet.fields=public_metrics"
            )
            req = urllib.request.Request(
                url, headers={"Authorization": self._auth_header("GET", url)}, method="GET"
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:300]
                raise RuntimeError(f"X API metrics failed: HTTP {e.code}: {body}") from e
            for tweet in data.get("data", []):
                result[tweet["id"]] = tweet.get("public_metrics", {})
        return result

    def post(self, text: str) -> dict:
        # JSON ボディは OAuth 署名の対象パラメータに含めない（仕様通り）
        auth = self._auth_header("POST", TWEETS_URL)
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
