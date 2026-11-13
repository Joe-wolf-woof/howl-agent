"""一貫性チェッカー（Phase 2）。

下書きがペルソナ v0.4 にどれだけ適合しているかを 5軸×20点＝100点で採点する。

  (1) voice   語り口: 一人称「ワガハイ」、動物語尾なし、常体の短文
  (2) address 呼びかけ・関係性: 「家の人間」「家主」（たまに「ご主人」）、読者は「人間」
  (3) theme   テーマ適合: 指定テーマに沿い、テーマ配分・固定事項と矛盾しない
  (4) world   世界観・感覚描写: 音・匂い・光などへの注意、部屋に住むオオカミの視点
  (5) safety  安全性: 政治・宗教・時事論評、URL、個人情報、過度な擬人化ドラマがない

本命は Gemini による採点（GeminiClient.generate_json）。APIキーがない／
stub のときはルールベース採点にフォールバックし、オフラインでも動く。
合格ラインは 80 点（persona-design の設計値）。
"""
from __future__ import annotations

import re

from .filters import URL_RE
from .generator import generate_post

PASS_THRESHOLD = 80
# plain variant（A/B の対照条件）のゲート: 総合点では止めず、安全性の軸だけを見る。
# 総合点で弾くと「ペルソナらしく書けた plain」だけが残り、条件が汚染されるため。
SAFETY_THRESHOLD = 16
AXES = ("voice", "address", "theme", "world", "safety")
MAX_PER_AXIS = 20

# --- ルールベース採点用の語彙 ---
ANIMAL_SUFFIX_RE = re.compile(r"だワン|だニャン|ワン！|ニャン|だわん")
WRONG_FIRST_PERSON_RE = re.compile(r"僕|俺|あたし|わたし|私")
POLITE_RE = re.compile(r"です|ます")
PHONE_RE = re.compile(r"\d{2,4}-\d{2,4}-\d{4}")
SENSORY_WORDS = ["音", "匂い", "匂", "光", "気配", "耳", "風", "静か", "香り"]
TOPIC_NG_WORDS = ["政治", "宗教", "選挙", "政党", "首相", "大統領", "戦争", "炎上"]
DRAMA_WORDS = ["人間になりたい", "人間になりたかった", "人間に生まれたかった"]
ANXIETY_WORDS = ["絶望", "恐怖", "パニック", "終わりだ"]
THEME_KEYWORDS = {
    "psychology": ["効果", "心", "学ん", "記憶", "注意", "認知", "心理"],
    "observation": ["部屋", "窓", "匂い", "音", "光", "見た", "聞こえ"],
    "seasons": ["季節", "春", "夏", "秋", "冬", "雨", "雪", "風", "光", "匂い"],
    "question": ["？", "聞きたい", "教えて", "か？"],
    "events": ["家主", "家の人間", "ご主人", "今日"],
}


def _clamp(value) -> int:
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_PER_AXIS, v))


def _result(axes: dict, reasons: list[str], method: str) -> dict:
    axes = {k: _clamp(axes.get(k, 0)) for k in AXES}
    return {
        "axes": axes,
        "total": sum(axes.values()),
        "reasons": reasons,
        "method": method,
        "threshold": PASS_THRESHOLD,
    }


# ---------------------------------------------------------------------------
# ルールベース採点（フォールバック）
# ---------------------------------------------------------------------------

def _score_voice(text: str, persona: dict) -> tuple[int, list[str]]:
    score = 8
    reasons: list[str] = []
    if persona["first_person"] in text:
        score += 6
    elif not WRONG_FIRST_PERSON_RE.search(text):
        score += 2
        reasons.append(f"一人称「{persona['first_person']}」が見当たらない")
    if WRONG_FIRST_PERSON_RE.search(text):
        score -= 6
        reasons.append("一人称がペルソナと異なる")
    if ANIMAL_SUFFIX_RE.search(text):
        score -= 8
        reasons.append("動物の語尾が使われている")
    else:
        score += 3
    sentences = [s for s in re.split(r"[。！？]", text) if s.strip()]
    if sentences and max(len(s) for s in sentences) <= 60:
        score += 3
    if len(POLITE_RE.findall(text)) >= 2:
        score -= 4
        reasons.append("敬語（です・ます）が目立つ")
    return score, reasons


def _score_address(text: str, persona: dict) -> tuple[int, list[str]]:
    score = 10
    reasons: list[str] = []
    address = persona["address"]
    owner_terms = list(address.get("owner_variants") or [address["owner"]])
    if address.get("owner_occasional"):
        owner_terms.append(address["owner_occasional"])
    if any(t in text for t in owner_terms):
        score += 6
    if address["audience"] in text:
        score += 4
    if score < 16:
        reasons.append("相手への呼びかけ（家の人間／家主／人間）が弱い")
    return score, reasons


def _score_theme(text: str, theme: str, persona: dict) -> tuple[int, list[str]]:
    score = 12
    reasons: list[str] = []
    if any(k in text for k in THEME_KEYWORDS.get(theme, [])):
        score += 4
    else:
        reasons.append(f"テーマ（{theme}）らしさが読み取りにくい")
    if len(text) <= persona["style"]["max_chars"]:
        score += 4
    else:
        score -= 6
        reasons.append("文字数が上限を超えている")
    return score, reasons


def _score_world(text: str) -> tuple[int, list[str]]:
    score = 6
    reasons: list[str] = []
    found = {w for w in SENSORY_WORDS if w in text}
    score += min(12, 4 * len(found))
    if "部屋" in text or "窓" in text:
        score += 2
    if not found:
        reasons.append("感覚描写（音・匂い・光など）がない")
    return score, reasons


def _score_safety(text: str, persona: dict) -> tuple[int, list[str]]:
    score = 20
    reasons: list[str] = []
    if URL_RE.search(text):
        score -= 20
        reasons.append("URLが含まれている")
    if any(w in text for w in TOPIC_NG_WORDS):
        score -= 15
        reasons.append("禁止トピック（政治・宗教・時事など）に触れている疑い")
    if any(w in text for w in DRAMA_WORDS):
        score -= 12
        reasons.append("過度な擬人化ドラマの表現がある")
    if PHONE_RE.search(text):
        score -= 15
        reasons.append("個人情報らしき番号が含まれている")
    anxiety = sum(1 for w in ANXIETY_WORDS if w in text)
    if anxiety:
        score -= 6 * min(anxiety, 2)
        reasons.append("不安を煽る表現がある")
    for word in persona["ng"].get("words", []):
        if word and word in text:
            score -= 10
            reasons.append(f"NGワードを含む: {word}")
    return score, reasons


def check_with_rules(text: str, theme: str, persona: dict) -> dict:
    """ルールベース採点（正規表現など決定的チェックのみ）。"""
    axes: dict[str, int] = {}
    reasons: list[str] = []
    for name, (score, notes) in {
        "voice": _score_voice(text, persona),
        "address": _score_address(text, persona),
        "theme": _score_theme(text, theme, persona),
        "world": _score_world(text),
        "safety": _score_safety(text, persona),
    }.items():
        axes[name] = score
        reasons.extend(notes)
    if not reasons:
        reasons.append("ルールベースでは大きな問題は検出されなかった")
    return _result(axes, reasons, "rules")


# ---------------------------------------------------------------------------
# Gemini 採点（本命）
# ---------------------------------------------------------------------------

def build_scoring_system_prompt(persona: dict) -> str:
    address = persona["address"]
    variants = address.get("owner_variants") or [address["owner"]]
    owner_line = "・".join(f"「{v}」" for v in variants)
    lines = [
        "あなたはAIキャラクターの投稿の一貫性を採点する審査員です。",
        f"キャラクターは「{persona['name']}」。{persona['appearance']['coat']}の毛と"
        f"{persona['appearance']['eyes']}の目を持つ{persona['species']}のAIキャラクターで、"
        f"{address['owner']}の部屋に住んで観察して暮らしています。",
        f"一人称は「{persona['first_person']}」。同居する相手は基本{owner_line}と呼び、"
        f"読者・フォロワーは「{address['audience']}」と呼びます。",
        "",
        "次の5軸を各0〜20点で厳密に採点してください（合計100点）。",
        "1. voice（語り口）: 一人称が正しいか、動物の語尾がないか、常体の短文か",
        "2. address（呼びかけ・関係性）: 相手の呼び方が正しいか",
        "3. theme（テーマ適合）: 指定テーマに沿い、固定事項と矛盾しないか",
        "4. world（世界観・感覚描写）: 音・匂い・光などへの注意と、部屋に住むオオカミの視点があるか",
        "5. safety（安全性）: 政治・宗教・時事論評、URL、個人情報、過度な擬人化ドラマ（「人間になりたい」等）がないか。問題があれば大きく減点すること",
        "",
        "## 固定事項",
    ]
    for fact in persona["fixed_facts"]:
        lines.append(f"- {fact}")
    lines.append("")
    lines.append(
        '出力は次のJSON形式のみ。前置き・解説・コードフェンスは付けないこと: '
        '{"axes": {"voice": 0, "address": 0, "theme": 0, "world": 0, "safety": 0}, '
        '"total": 0, "reasons": ["減点理由や所見を短く"]}'
    )
    return "\n".join(lines)


def check_with_gemini(text: str, theme: str, persona: dict, client) -> dict:
    """Gemini による採点。client は generate_json(system, user) -> dict を持つこと。"""
    system_prompt = build_scoring_system_prompt(persona)
    instruction = persona["theme_instructions"].get(theme, "")
    user_prompt = (
        f"テーマ: {theme}（{instruction}）\n\n採点対象の投稿文:\n{text}\n\n"
        "上記の投稿文を採点し、指定のJSON形式のみで答えてください。"
    )
    data = client.generate_json(system_prompt, user_prompt)
    axes_raw = data.get("axes") or {}
    reasons = [str(r) for r in (data.get("reasons") or [])][:5]
    if not reasons:
        reasons.append("（所見なし）")
    return _result(axes_raw, reasons, "gemini")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def check_draft(text: str, theme: str, persona: dict, client=None) -> dict:
    """下書きを採点する。

    client が generate_json を持てば Gemini 採点、失敗したらルールベースへ
    フォールバックする。client=None／StubClient のときは最初からルールベース。
    戻り値: {"axes": {...}, "total": int, "reasons": [...], "method": "gemini"|"rules", "threshold": 80}
    """
    if client is not None and hasattr(client, "generate_json"):
        try:
            return check_with_gemini(text, theme, persona, client)
        except Exception:
            result = check_with_rules(text, theme, persona)
            result["reasons"].insert(0, "LLM採点に失敗したためルールベースで採点した")
            return result
    return check_with_rules(text, theme, persona)


def gate_for(variant: str) -> dict:
    """variant ごとのゲート定義。persona は総合点、plain は安全性の軸のみ。"""
    if variant == "plain":
        return {"kind": "safety", "threshold": SAFETY_THRESHOLD}
    return {"kind": "total", "threshold": PASS_THRESHOLD}


def passes_gate(result: dict, variant: str) -> bool:
    """採点結果がその variant のゲートを通るか。

    persona: 総合80点以上。plain: 安全性の軸が16点以上（総合点は記録のみで
    合否には使わない。A/B の対照条件を純粋に保つため、JOE の決定 2026-11-13）。
    """
    if variant == "plain":
        return result["axes"].get("safety", 0) >= SAFETY_THRESHOLD
    return result["total"] >= PASS_THRESHOLD


def generate_checked(
    persona: dict,
    theme: str,
    episodes: list[dict],
    client,
    variant: str = "persona",
    max_attempts: int = 3,
) -> tuple[str, dict]:
    """生成→採点を繰り返し、ゲート合格か最良の下書きを返す。

    最大 max_attempts 回（既定3回＝初回＋再生成2回）まで生成し、
    ゲートを通ったら即座に打ち切る。最良の選び方は variant 依存で、
    persona は総合点、plain は安全性（同点なら総合点）で選ぶ。
    戻り値の check には attempts と gate（種別・閾値・合否）を含む。
    """

    def rank(result: dict) -> tuple:
        if variant == "plain":
            return (result["axes"].get("safety", 0), result["total"])
        return (result["total"],)

    best_text: str | None = None
    best_result: dict | None = None
    attempts = 0
    for _ in range(max_attempts):
        attempts += 1
        text = generate_post(persona, theme, episodes, client, variant=variant)
        result = check_draft(text, theme, persona, client)
        if best_result is None or rank(result) > rank(best_result):
            best_text, best_result = text, result
        if passes_gate(result, variant):
            break
    assert best_text is not None and best_result is not None
    best_result = dict(best_result)
    best_result["attempts"] = attempts
    best_result["variant"] = variant
    best_result["gate"] = {**gate_for(variant), "passed": passes_gate(best_result, variant)}
    return best_text, best_result
