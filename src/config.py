"""プロジェクト直下の .env を環境変数へ読み込む最小ローダ。

認証情報は .env（gitignore済み）にのみ置き、コード・リポジトリ・ログには
残さない。既に環境変数が設定されている場合はそちらを優先する。
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"


def parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.lstrip("\ufeff").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_dotenv(path: Path = ENV_PATH) -> list[str]:
    """読み込んだキー名の一覧を返す（値そのものは返さない・表示しない）。"""
    if not path.exists():
        return []
    parsed = parse_env_text(path.read_text(encoding="utf-8-sig"))
    loaded = []
    for key, value in parsed.items():
        if key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
