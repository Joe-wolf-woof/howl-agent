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
DECISIONS_JSONL = DATA / "decisions.jsonl"
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
    decision: dict | None = None,
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
        # Phase 3: 行動選択レイヤーの判断（action/theme/reason/method/target）。
        "decision": decision,
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
            episode = {
                "date": d["posted_at"][:10],
                "type": "post",
                "theme": d["theme"],
                # Phase 2: A/B 集計用。旧フローで作られた下書きには無い場合がある
                "variant": d.get("variant", "persona"),
                "check_total": (d.get("check") or {}).get("total"),
                "summary": f"Xに投稿した: {final_text(d)[:60]}",
            }
            # Phase 3: 行動選択の結果もエピソードに残す（継続目標の進捗集計用）
            decision = d.get("decision") or {}
            if decision.get("action"):
                episode["action"] = decision["action"]
            if decision.get("target"):
                episode["target"] = decision["target"]
            add_episode(episode)
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


# ---------------------------------------------------------------------------
# Phase 3: 記憶の統合（ARアプリ『ハウリング』との連携）と判断ログ
# ---------------------------------------------------------------------------

def episode_fingerprint(episode: dict) -> tuple[str, str]:
    """エピソードの同一性判定用フィンガープリント（日付＋本文）。"""
    return (str(episode.get("date", ""))[:10], str(episode.get("summary", "")))


def import_shown_episodes(items: list) -> tuple[int, int]:
    """『ハウリング』の書き出し（[{date, kind:"shown", text}]）を取り込む。

    各件は type/kind ともに "shown"、summary に text を入れて保存する。
    (date, text) のフィンガープリントで重複判定するため冪等
    （同じファイルを2回取り込んでも増えない）。ファイル内の重複も除く。
    戻り値: (取り込み件数, スキップ件数)。形式不正な件もスキップに数える。
    """
    episodes = load_episodes()
    seen = {
        episode_fingerprint(e) for e in episodes if e.get("type") == "shown"
    }
    added = 0
    skipped = 0
    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        text = str(item.get("text") or "").strip()
        raw_date = str(item.get("date") or "").strip()
        if not text or not raw_date:
            skipped += 1
            continue
        fingerprint = (raw_date[:10], text)
        if fingerprint in seen:
            skipped += 1
            continue
        seen.add(fingerprint)
        episode = {
            "date": raw_date[:10],
            "type": "shown",
            "kind": "shown",
            "source": "howling-app",
            "summary": text,
        }
        if len(raw_date) > 10:
            episode["shown_at"] = raw_date
        episodes.append(episode)
        added += 1
    if added:
        EPISODES_JSON.parent.mkdir(parents=True, exist_ok=True)
        with open(EPISODES_JSON, "w", encoding="utf-8") as f:
            json.dump(episodes, f, ensure_ascii=False, indent=2)
    return added, skipped


def context_episodes(total: int = 5, shown_slots: int = 2) -> list[dict]:
    """生成文脈用のエピソード選択。

    直近 total 件を基本としつつ、直近の shown（見せてもらった物）が
    投稿エピソードに埋もれて落ちないよう、最大 shown_slots 件を保証して含める。
    並びは元の時系列順を保つ。
    """
    episodes = load_episodes()
    merged = list(episodes[-total:]) if total else []
    for s in [e for e in episodes if e.get("type") == "shown"][-shown_slots:]:
        if s not in merged:
            merged.append(s)
    order = {id(e): i for i, e in enumerate(episodes)}
    merged.sort(key=lambda e: order.get(id(e), 0))
    return merged


def append_decision(record: dict) -> None:
    """行動選択レイヤーの判断を data/decisions.jsonl に1行追記する。"""
    DECISIONS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with open(DECISIONS_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_decisions() -> list[dict]:
    if not DECISIONS_JSONL.exists():
        return []
    with open(DECISIONS_JSONL, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def export_app_memory(path, max_posts: int = 10) -> tuple[int, int]:
    """中央ストアからアプリ形式の memory.json を書き出す。

    形式は『ハウリング』側 data/memory.json と同じ:
      {"posts": [{date, text, theme}], "episodes": [{date, summary}], "updatedAt": ...}
    posts は投稿済み下書きの実文、episodes は出来事系（投稿以外）エピソード。
    戻り値: (posts件数, episodes件数)。
    """
    posts = []
    posted = [d for d in list_drafts("posted") if d.get("posted_at")]
    posted.sort(key=lambda d: d["posted_at"])
    for d in posted[-max_posts:]:
        posts.append(
            {
                "date": d["posted_at"][:10],
                "text": final_text(d),
                "theme": d["theme"],
            }
        )
    episodes = [
        {"date": e.get("date", ""), "summary": e.get("summary", "")}
        for e in load_episodes()
        if e.get("type") != "post"
    ]
    payload = {
        "posts": posts,
        "episodes": episodes,
        "updatedAt": now_iso(),
        "note": "howl-agent の export-memory による書き出し（中央ストア→アプリの静的連携）。",
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return len(posts), len(episodes)


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
