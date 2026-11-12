"""ペルソナデータの読み込みと、生成用システムプロンプトの構築。

ペルソナは persona/persona.json に外出しされている（設計書 §4.2）。
コードを変えずにパラメータだけを調整できることが、この分離の目的。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PERSONA_PATH = ROOT / "persona" / "persona.json"

BIG_FIVE_LABELS = {
    "openness": "開放性",
    "conscientiousness": "誠実性",
    "extraversion": "外向性",
    "agreeableness": "協調性",
    "neuroticism": "神経症傾向",
}


def load_persona(path: Path = PERSONA_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_system_prompt(persona: dict) -> str:
    bf = persona["big_five"]
    lines = [
        f"あなたは「{persona['name']}」。{persona['appearance']['coat']}の毛と"
        f"{persona['appearance']['eyes']}の目を持つ{persona['species']}のAIキャラクターです。",
        f"一人称は「{persona['first_person']}」。飼い主ではなく同居する相手のことは"
        f"「{persona['address']['owner']}」と呼び、読者・フォロワー全般を"
        f"「{persona['address']['audience']}」と呼びます。",
        "",
        "## 性格（Big Fiveパラメータ）",
    ]
    for key, label in BIG_FIVE_LABELS.items():
        lines.append(f"- {label}: {bf[key]}/100")
    lines.append("")
    lines.append("## 行動ルール（パラメータの実装）")
    for key in BIG_FIVE_LABELS:
        for rule in persona["behavior_rules"].get(key, []):
            lines.append(f"- {rule}")
    lines.append("")
    lines.append("## 文体")
    style = persona["style"]
    lines.append(f"- {style['register']}")
    lines.append(f"- {style['sensory_description']}")
    lines.append(f"- 投稿は{style['max_chars']}字以内")
    lines.append("- 動物の語尾（「〜だワン」等）は使わない")
    lines.append(f"- 絵文字: {style['emoji']}／ハッシュタグ: {style['hashtags']}")
    lines.append(f"- {style['politeness']}")
    lines.append("")
    lines.append("## 固定事項（矛盾させないこと）")
    for fact in persona["fixed_facts"]:
        lines.append(f"- {fact}")
    lines.append("")
    lines.append("## 禁止事項")
    for topic in persona["ng"]["topics"]:
        lines.append(f"- {topic}は扱わない")
    for note in persona["ng"]["notes"]:
        lines.append(f"- {note}")
    lines.append("- URLは入れない")
    lines.append("")
    lines.append("出力は投稿文のみ。前置き・解説・引用符は付けないこと。")
    return "\n".join(lines)


def build_user_prompt(persona: dict, theme: str, episodes: list[dict]) -> str:
    instruction = persona["theme_instructions"][theme]
    lines = [f"テーマ: {theme}", f"指示: {instruction}"]
    if episodes:
        lines.append("")
        lines.append("## 最近のエピソード記憶（矛盾させず、必要なら軽く触れてよい）")
        for ep in episodes:
            lines.append(f"- {ep.get('date', '')}: {ep.get('summary', '')}")
    lines.append("")
    lines.append("この条件で、Xへの投稿文を1つだけ書いてください。")
    return "\n".join(lines)
