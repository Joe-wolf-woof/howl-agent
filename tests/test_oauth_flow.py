"""3脚認可フローの部品テスト（ネットワークなし）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.adapters.oauth1 import build_authorization_header
from src.adapters.oauth_flow import authorize_url, parse_form_response


class OAuthFlowPartsTest(unittest.TestCase):
    def test_request_token_header_has_callback_and_no_token(self):
        header = build_authorization_header(
            method="POST",
            url="https://api.x.com/oauth/request_token",
            consumer_key="ck",
            consumer_secret="cs",
            access_token=None,
            token_secret="",
            extra_oauth_params=[("oauth_callback", "http://127.0.0.1")],
            nonce="abc",
            timestamp="1700000000",
        )
        self.assertIn('oauth_callback="http%3A%2F%2F127.0.0.1"', header)
        self.assertNotIn("oauth_token=", header.replace("oauth_token_secret", ""))

    def test_parse_form_response(self):
        parsed = parse_form_response("oauth_token=AAA&oauth_token_secret=BBB&user_id=42&screen_name=AgentName_Howl")
        self.assertEqual(parsed["oauth_token"], "AAA")
        self.assertEqual(parsed["screen_name"], "AgentName_Howl")

    def test_authorize_url(self):
        self.assertEqual(
            authorize_url("tok123"),
            "https://api.x.com/oauth/authorize?oauth_token=tok123",
        )


if __name__ == "__main__":
    unittest.main()
