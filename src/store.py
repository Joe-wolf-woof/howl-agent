"""下書きキュー・エピソード記憶・編集差分の記録。

Phase A（承認＋編集制）の流れを支えるストア:
  生成 → drafts.jsonl に status=draft で保存し、編集用 .txt を書き出す
  編集 → JOE が .txt を書き換える
  承認 → approve() が .txt を読み戻し、生成文との差分を記録して status=approved
  投稿 → mark_posted() で status=posted にし、エピソード記憶にも1件追加する

編集差分（original_text → edited_text）は、JOEの感性の傾向を後から分析し、
ペルソナ調整に活かすための記録として残す（persona-design.md §10）。
"""
from __future__ import annotations

import difflib
import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DRAFTS_JSONL = DATA / "drafts.jsonl"
DRAFTS_DIR = DATA / "drafts"
EPISODES_JSON = DATA / "episodes.json"
OUTBOX_JSONL = DATA / "outbox.jsonl"
AUTOPOST_OFF = DATA / "AUTOPOST_OFF"

JST = timezone(timedelta(hours=9))


def now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _load_drafts() -> list[dict]:
    if not DRAFTS_JSONL.exists():
        return []
    with open(DRAFTS_JSONL, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _save_drafts(drafts: list[dict]) -> None:
    DRAFTS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with open(DRAFTS_JSONL, "w", encoding="utf-8") as f:
        for d in drafts:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")


def create_draft(
    text: str,
    theme: str,
    persona_version: str,
    variant: str = "persona",
    check: dict | None = None,
) -> dict:
    draft_id = uuid.uuid4().hex[:8]
    draft = {
        "id": draft_id,
        "created_at": now_iso(),
        "theme": theme,
        "persona_version": persona_version,
        # Phase 2: A/B（仮説H1）の variant と一貫性チェッカーの結果。
        # 旧レコードには無いキーだが、読む側は .get で扱うため後方互換。
        "variant": variant,
        "check": check,
        "original_text": text,
        "edited_text": None,
        "status": "draft",
        "filter_issues": [],
        "posted_at": None,
        "post_result": None,
    }
    drafts = _load_drafts()
    drafts.append(draft)
    _save_drafts(drafts)
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    (DRAFTS_DIR / f"{draft_id}.txt").write_text(text, encoding="utf-8")
    return draft


def get_draft(draft_id: str) -> dict:
    for d in _load_drafts():
        if d["id"] == draft_id:
            return d
    raise KeyError(f"draft not found: {draft_id}")


def update_draft(draft_id: str, **fields) -> dict:
    """下書きの任意フィールドを更新する（check 結果の記録などに使う）。"""
    drafts = _load_drafts()
    for d in drafts:
        if d["id"] == draft_id:
            d.update(fields)
            _save_drafts(drafts)
            return d
    raise KeyError(f"draft not found: {draft_id}")


def list_drafts(status: str | None = None) -> list[dict]:
    drafts = _load_drafts()
    if status:
        drafts = [d for d in drafts if d["status"] == status]
    return drafts


def approve(draft_id: str) -> dict:
    """編集用 .txt を読み戻し、差分を記録して承認済みにする。"""
    drafts = _load_drafts()
    for d in drafts:
        if d["id"] == draft_id:
            txt_path = DRAFTS_DIR / f"{draft_id}.txt"
            edited = txt_path.read_text(encoding="utf-8").strip() if txt_path.exists() else d["original_text"]
            d["edited_text"] = edited
            d["was_edited"] = edited != d["original_text"]
            d["edit_diff"] = "\n".join(
                difflib.unified_diff(
                    d["original_text"].splitlines(),
                    edited.splitlines(),
                    lineterm="",
                )
            )
            d["status"] = "approved"
            d["approved_at"] = now_iso()
            _save_drafts(drafts)
            return d
    raise KeyError(f"draft not found: {draft_id}")


def final_text(draft: dict) -> str:
    return draft.get("edited_text") or draft["original_text"]


def mark_posted(draft_id: str, result: dict) -> dict:
    drafts = _load_drafts()
    for d in drafts:
        if d["id"] == draft_id:
            d["status"] = "posted"
            d["posted_at"] = now_iso()
            d["post_result"] = result
            _save_drafts(drafts)
            add_episode(
                {
                    "date": d["posted_at"][:10],
                    "type": "post",
                    "theme": d["theme"],
                    # Phase 2: A/B 集計用。旧フローで作られた下書きには無い場合がある
                    "variant": d.get("variant", "persona"),
                    "check_total": (d.get("check") or {}).get("total"),
                    "summary": f"Xに投稿した: {final_text(d)[:60]}",
                }
            )
            return d
    raise KeyError(f"draft not found: {draft_id}")


def load_episodes() -> list[dict]:
    if not EPISODES_JSON.exists():
        return []
    with open(EPISODES_JSON, encoding="utf-8") as f:
        return json.load(f)


def add_episode(episode: dict) -> None:
    episodes = load_episodes()
    episodes.append(episode)
    EPISODES_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(EPISODES_JSON, "w", encoding="utf-8") as f:
        json.dump(episodes, f, ensure_ascii=False, indent=2)


def recent_episodes(n: int = 5) -> list[dict]:
    return load_episodes()[-n:]


def append_outbox(record: dict) -> None:
    OUTBOX_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTBOX_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Phase B（全自動投稿）の安全装置
# ---------------------------------------------------------------------------

def autopost_kill_reason() -> str | None:
    """キルスイッチが効いていれば理由を返す（"env" / "file"）。なければ None。"""
    if os.environ.get("HOWL_AUTOPOST", "").lower() == "off":
        return "env"
    if AUTOPOST_OFF.exists():
        return "file"
    return None


def posted_count_on(date_str: str) -> int:
    """指定日（YYYY-MM-DD）に投稿済みのエピソード数を返す（1日上限の判定用）。"""
    return sum(
        1
        for e in load_episodes()
        if e.get("type") == "post" and e.get("date") == date_str
    )
