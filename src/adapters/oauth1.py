"""OAuth 1.0a 署名（HMAC-SHA1）の最小実装。標準ライブラリのみ。

X API の user context 認証用。テストは X 公式ドキュメントの
署名サンプルベクトルで検証している（tests/test_oauth1.py）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import urllib.parse


def percent_encode(value: str) -> str:
    """RFC 3986 の percent-encoding（unreserved 以外をすべて %XX にする）。"""
    return urllib.parse.quote(str(value), safe="")


def normalize_params(params: list[tuple[str, str]]) -> str:
    """パラメータを encode してキー・値の順にソートし、k=v&... に正規化する。"""
    encoded = sorted((percent_encode(k), percent_encode(v)) for k, v in params)
    return "&".join(f"{k}={v}" for k, v in encoded)


def signature_base_string(method: str, base_url: str, param_string: str) -> str:
    return "&".join(
        [
            method.upper(),
            percent_encode(base_url),
            percent_encode(param_string),
        ]
    )


def sign(base_string: str, consumer_secret: str, token_secret: str) -> str:
    key = f"{percent_encode(consumer_secret)}&{percent_encode(token_secret)}"
    digest = hmac.new(key.encode("utf-8"), base_string.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def build_authorization_header(
    *,
    method: str,
    url: str,
    consumer_key: str,
    consumer_secret: str,
    access_token: str | None = None,
    token_secret: str = "",
    extra_params: list[tuple[str, str]] | None = None,
    extra_oauth_params: list[tuple[str, str]] | None = None,
    nonce: str | None = None,
    timestamp: str | None = None,
) -> str:
    """署名つき Authorization ヘッダを組み立てる。

    url にクエリ文字列が含まれていれば、そのパラメータも署名対象に含める。
    extra_params は application/x-www-form-urlencoded のボディ用
    （JSON ボディの場合は署名対象に含めないため、呼び出し側で渡さない）。
    extra_oauth_params は oauth_callback / oauth_verifier など、
    署名対象かつヘッダに載せる追加の oauth_* パラメータ（3脚認可フロー用）。
    access_token=None のときは oauth_token を含めない（request_token 取得時）。
    """
    parsed = urllib.parse.urlparse(url)
    base_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)

    oauth_params: list[tuple[str, str]] = [
        ("oauth_consumer_key", consumer_key),
        ("oauth_nonce", nonce or secrets.token_hex(16)),
        ("oauth_signature_method", "HMAC-SHA1"),
        ("oauth_timestamp", timestamp or str(int(time.time()))),
        ("oauth_version", "1.0"),
    ]
    if access_token:
        oauth_params.append(("oauth_token", access_token))
    if extra_oauth_params:
        oauth_params.extend(extra_oauth_params)
    all_params = oauth_params + query_params + (extra_params or [])
    base_string = signature_base_string(method, base_url, normalize_params(all_params))
    signature = sign(base_string, consumer_secret, token_secret)

    header_params = sorted(oauth_params + [("oauth_signature", signature)])
    header = ", ".join(f'{percent_encode(k)}="{percent_encode(v)}"' for k, v in header_params)
    return f"OAuth {header}"
