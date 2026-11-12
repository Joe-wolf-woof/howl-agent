"""投稿文の機械チェック（NGフィルタ）。

Phase 1 のガードはこのモジュール：文字数・URL・NGワード・禁止トピック語。
LLMによる一貫性チェッカー（ペルソナ適合スコア）は Phase 2 でここに接続する。
"""
from __future__ import annotations

import re

URL_RE = re.compile(r"https?://|www\.")


def check_post(text: str, persona: dict) -> list[str]:
    """問題があればその説明のリストを返す。空リスト＝通過。"""
    issues: list[str] = []
    max_chars = persona["style"]["max_chars"]
    if len(text) > max_chars:
        issues.append(f"文字数オーバー: {len(text)}字（上限{max_chars}字）")
    if not text.strip():
        issues.append("本文が空")
    if persona["ng"].get("no_url") and URL_RE.search(text):
        issues.append("URLが含まれている（コスト設計により禁止）")
    for word in persona["ng"].get("words", []):
        if word and word in text:
            issues.append(f"NGワードを含む: {word}")
    # 禁止トピックは語の出現だけでは断定できないため、警告ではなく
    # チェック対象として明示する（最終判断はPhase 2の一貫性チェッカーと人間）
    return issues
