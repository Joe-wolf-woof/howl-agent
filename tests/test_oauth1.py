"""OAuth 1.0a 署名のテスト。

X 公式ドキュメント「Creating a signature」に掲載された
サンプルリクエストと期待署名で検証する（公開されたテストベクトル）。

実行: python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.adapters.oauth1 import (
    build_authorization_header,
    normalize_params,
    percent_encode,
    sign,
    signature_base_string,
)

# X 公式ドキュメントのサンプル値（テスト用の公開値であり、実在の認証情報ではない）
CONSUMER_KEY = "xvz1evFS4wEEPTGEFPHBog"
CONSUMER_SECRET = "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw"
ACCESS_TOKEN = "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb"
TOKEN_SECRET = "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE"
NONCE = "kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg"
TIMESTAMP = "1318622958"
STATUS = "Hello Ladies + Gentlemen, a signed OAuth request!"
EXPECTED_PARAM_STRING = (
    "include_entities=true&oauth_consumer_key=xvz1evFS4wEEPTGEFPHBog"
    "&oauth_nonce=kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg"
    "&oauth_signature_method=HMAC-SHA1&oauth_timestamp=1318622958"
    "&oauth_token=370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb"
    "&oauth_version=1.0"
    "&status=Hello%20Ladies%20%2B%20Gentlemen%2C%20a%20signed%20OAuth%20request%21"
)
# 署名の期待値について: ドキュメント掲載の署名ベース文字列と署名キーを
# そのまま HMAC-SHA1 に通して得た値を採用した（実装とは独立に手計算で検証済み）。
# ネット上でよく引用される別値 "tnnArxj06cWHq44gCs1OSKk/jLY=" は
# oauth_callback を含む request_token 例のもので、このベクトルの値ではない。
EXPECTED_SIGNATURE = "hCtSmYh+iHYCEqBWrE7C7hYmtUk="


class OAuth1VectorTest(unittest.TestCase):
    def _params(self):
        return [
            ("include_entities", "true"),
            ("status", STATUS),
            ("oauth_consumer_key", CONSUMER_KEY),
            ("oauth_nonce", NONCE),
            ("oauth_signature_method", "HMAC-SHA1"),
            ("oauth_timestamp", TIMESTAMP),
            ("oauth_token", ACCESS_TOKEN),
            ("oauth_version", "1.0"),
        ]

    def test_percent_encode(self):
        self.assertEqual(percent_encode("Ladies + Gentlemen!"), "Ladies%20%2B%20Gentlemen%21")
        self.assertEqual(percent_encode("~ok-._"), "~ok-._")

    def test_parameter_normalization(self):
        self.assertEqual(normalize_params(self._params()), EXPECTED_PARAM_STRING)

    def test_signature_matches_documented_vector(self):
        base = signature_base_string(
            "POST",
            "https://api.twitter.com/1.1/statuses/update.json",
            normalize_params(self._params()),
        )
        self.assertEqual(sign(base, CONSUMER_SECRET, TOKEN_SECRET), EXPECTED_SIGNATURE)

    def test_authorization_header_contains_signature(self):
        header = build_authorization_header(
            method="POST",
            url="https://api.twitter.com/1.1/statuses/update.json?include_entities=true",
            consumer_key=CONSUMER_KEY,
            consumer_secret=CONSUMER_SECRET,
            access_token=ACCESS_TOKEN,
            token_secret=TOKEN_SECRET,
            extra_params=[("status", STATUS)],
            nonce=NONCE,
            timestamp=TIMESTAMP,
        )
        self.assertTrue(header.startswith("OAuth "))
        self.assertIn("hCtSmYh%2BiHYCEqBWrE7C7hYmtUk%3D", header)


if __name__ == "__main__":
    unittest.main()
