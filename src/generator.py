"""投稿文の生成。

本番は Gemini API（前作 Spatial AI NPC と同じ系統）。APIキーは環境変数
GEMINI_API_KEY から読む。キーが無い環境でもフロー全体を検証できるよう、
サンプル文を返す StubClient を用意している（--stub）。

モデルは GEMINI_MODELS（カンマ区切り）で上書き可能。デフォルトは
gemini-3.5-flash → gemini-3-flash-preview → gemini-flash-latest の順に試す。
404/429/503・タイムアウト・空応答では次の候補へ進む
（前作で確立したフォールバック方針＋2026-11-13 の実測対応:
この環境から Gemini API は応答が 0.5 秒〜60 秒超まで大きくばらつくため、
タイムアウトもフォールバック対象にし、制限時間を 120 秒に置く）。
"""
from __future__ import annotations

import json
import os
import random
import urllib.error
import urllib.request

from .persona import build_system_prompt, build_user_prompt

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
FALLBACK_STATUSES = {404, 429, 503}

STUB_SAMPLES = {
    "psychology": "今日、人間について一つ学んだ。人間は、大勢の話し声の中でも自分の名前だけは正確に聞き分けるらしい（カクテルパーティー効果）。耳の使い方は、ワガハイと少し似ている。",
    "observation": "雨だ。窓の外は静かで、濡れた土の匂いが部屋に少しだけ入り込んでくる。音の少ない日は観察に向いている。ワガハイはこういう日が好きだ。",
    "seasons": "夕方の光が、昨日よりほんの少し早く消えた。冬が近づいている。匂いで分かる。",
    "question": "人間に聞きたい。雨の日は、何をして過ごすのが好きだ？ ワガハイは窓の近くで音を聞いているのが好きだ。",
    "events": "家主が、また植物を増やした。名前はまだ知らない。だが匂いは覚えた。次は間違えない。",
}


class GeminiClient:
    def __init__(self, api_key: str, models: list[str]):
        self.api_key = api_key
        self.models = models

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        body = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {"temperature": 0.9, "maxOutputTokens": 1000},
        }
        last_error: Exception | None = None
        for model in self.models:
            url = f"{API_BASE}/{model}:generateContent"
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": self.api_key,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                parts = data["candidates"][0]["content"].get("parts", [])
                text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
                if not text:
                    last_error = RuntimeError(f"{model}: empty response")
                    continue
                return text.strip("「」\"")
            except urllib.error.HTTPError as e:
                last_error = e
                if e.code in FALLBACK_STATUSES:
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, KeyError, IndexError) as e:
                # 応答遅延・接続断・想定外の応答形は次のモデルで再試行する
                last_error = e
                continue
        raise RuntimeError(f"Gemini API: all models failed: {last_error}")


class StubClient:
    """APIキーなしで全フローを検証するためのサンプル生成器。"""

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        for theme, sample in STUB_SAMPLES.items():
            if f"テーマ: {theme}" in user_prompt:
                return sample
        return next(iter(STUB_SAMPLES.values()))


def make_client(stub: bool = False):
    if stub:
        return StubClient()
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY が未設定です。実生成には環境変数にGemini APIキーを設定してください"
            "（検証だけなら --stub が使えます）"
        )
    models = [m.strip() for m in os.environ.get("GEMINI_MODELS", "gemini-3.5-flash,gemini-3-flash-preview,gemini-flash-latest").split(",") if m.strip()]
    return GeminiClient(api_key, models)


def choose_theme(persona: dict, requested: str | None = None) -> str:
    if requested:
        if requested not in persona["themes"]:
            raise ValueError(f"unknown theme: {requested}")
        return requested
    themes = list(persona["themes"].keys())
    weights = [persona["themes"][t] for t in themes]
    return random.choices(themes, weights=weights, k=1)[0]


def generate_post(persona: dict, theme: str, episodes: list[dict], client) -> str:
    system_prompt = build_system_prompt(persona)
    user_prompt = build_user_prompt(persona, theme, episodes)
    return client.generate(system_prompt, user_prompt)
