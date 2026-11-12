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


def update_env_values(updates: dict[str, str], path: Path = ENV_PATH) -> None:
    """既存の .env の該当キーだけを新しい値で書き換える（他キーは保持）。

    認可フローで新しい Access Token を得たときに使う。値は表示・記録しない。
    """
    parsed = parse_env_text(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    parsed.update(updates)
    body = "".join(f"{k}={v}\n" for k, v in parsed.items())
    path.write_text(body, encoding="utf-8")
    path.chmod(0o600)
    for key, value in updates.items():
        os.environ[key] = value
