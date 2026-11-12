"""OAuth 1.0a 3脚認可フロー（アプリ所有者と投稿アカウントが異なる場合用）。

背景: 開発者アプリとクレジットは JOE の個人アカウント側にあり、
投稿はキャラアカウント @AgentName_Howl から行う。そのため、
アプリ（Consumer Keys）はそのままに、ハウル側の Access Token を
認可フローで取得する。

流れ:
  1. request_token() でリクエストトークンを取得し、認可URLを組み立てる
  2. ハウルのアカウントでログインしたブラウザで認可URLを開き、承認する
  3. コールバック先（http://127.0.0.1）に付いてくる oauth_verifier を控える
  4. access_token() で本物の Access Token / Secret と交換する
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .oauth1 import build_authorization_header

REQUEST_TOKEN_URL = "https://api.x.com/oauth/request_token"
ACCESS_TOKEN_URL = "https://api.x.com/oauth/access_token"
AUTHORIZE_URL = "https://api.x.com/oauth/authorize"
CALLBACK_URL = "http://127.0.0.1"

ROOT = Path(__file__).resolve().parent.parent.parent
PENDING_PATH = ROOT / "data" / "oauth_pending.json"


def parse_form_response(body: str) -> dict[str, str]:
    return dict(urllib.parse.parse_qsl(body, keep_blank_values=True))


def _post(url: str, auth_header: str) -> dict[str, str]:
    req = urllib.request.Request(
        url, data=b"", headers={"Authorization": auth_header}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return parse_form_response(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"OAuth flow failed: HTTP {e.code}: {body}") from e


def fetch_request_token(consumer_key: str, consumer_secret: str) -> dict[str, str]:
    auth = build_authorization_header(
        method="POST",
        url=REQUEST_TOKEN_URL,
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
        access_token=None,
        token_secret="",
        extra_oauth_params=[("oauth_callback", CALLBACK_URL)],
    )
    result = _post(REQUEST_TOKEN_URL, auth)
    if result.get("oauth_callback_confirmed") != "true":
        raise RuntimeError("oauth_callback が確認されませんでした")
    return result


def authorize_url(request_token: str) -> str:
    return f"{AUTHORIZE_URL}?oauth_token={urllib.parse.quote(request_token)}"


def save_pending(request: dict[str, str]) -> None:
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_PATH.write_text(
        json.dumps(
            {
                "oauth_token": request["oauth_token"],
                "oauth_token_secret": request["oauth_token_secret"],
            }
        ),
        encoding="utf-8",
    )
    PENDING_PATH.chmod(0o600)


def load_pending() -> dict[str, str]:
    if not PENDING_PATH.exists():
        raise RuntimeError("認可の途中状態がありません。先に authorize-url を実行してください")
    return json.loads(PENDING_PATH.read_text(encoding="utf-8"))


def clear_pending() -> None:
    PENDING_PATH.unlink(missing_ok=True)


def exchange_access_token(
    consumer_key: str, consumer_secret: str, pending: dict[str, str], verifier: str
) -> dict[str, str]:
    auth = build_authorization_header(
        method="POST",
        url=ACCESS_TOKEN_URL,
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
        access_token=pending["oauth_token"],
        token_secret=pending["oauth_token_secret"],
        extra_oauth_params=[("oauth_verifier", verifier)],
    )
    result = _post(ACCESS_TOKEN_URL, auth)
    if "oauth_token" not in result or "oauth_token_secret" not in result:
        raise RuntimeError(f"Access Token の交換に失敗しました: {sorted(result.keys())}")
    return result
