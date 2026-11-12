# howl-agent — AIキャラクター「ハウル」自律運用エンジン

WebAR作品『Spatial AI NPC』のオオカミを、独立したAIキャラクター **ハウル** として運用するためのエージェント本体。
Xで自律発信し、WebARでは同じキャラクターに「会える」——作って終わりではなく、運用と測定までを含むプロジェクトの Phase 1 実装です。

## 現在の状態（Phase 1 / MVP）

- ✅ ペルソナエンジン: Big Fiveパラメータ＋行動変換ルールを `persona/persona.json` に外出しし、システムプロンプトへ変換
- ✅ 投稿生成: Gemini API（モデルフォールバック付き）。APIキーなしでも `--stub` で全フロー検証可能
- ✅ Phase A フロー（承認＋編集制）: 生成 → 人間が文章を編集 → 承認 → 投稿。編集差分は記録され、将来のペルソナ調整の材料になる
- ✅ NGフィルタ: 文字数（140字）・URL・NGワードの機械チェック
- ✅ エピソード記憶: `data/episodes.json` に蓄積し、生成時に参照（投稿すると自動で1件追加）
- ✅ 配信アダプタ: 疎結合な interface。現在は DryRun（outboxへ記録、実投稿しない）
- ⏳ X APIアダプタ: 骨格のみ。Xアカウント作成・開発者登録の完了後に `POST /2/tweets` を実装する
- ⏳ 一貫性チェッカー（LLMによるペルソナ適合スコア）と全自動化・A/B測定は Phase 2

## 使い方

```bash
# 投稿案を生成して下書きに保存（テーマは配分に従って自動選択も可）
python -m src.cli generate --theme psychology        # 実生成（要 GEMINI_API_KEY）
python -m src.cli generate --stub                    # キーなし検証用サンプル

# 下書き一覧
python -m src.cli drafts

# data/drafts/<id>.txt を編集したあと、承認（差分が記録される）
python -m src.cli approve <id>

# 投稿（デフォルトは dry-run：outbox に記録されるだけ）
python -m src.cli post <id>
python -m src.cli post <id> --adapter x              # 登録後に有効化
```

テスト:

```bash
python -m unittest discover -s tests -v
```

## 設計の要点

- **ペルソナはデータ、エンジンはコード**: 性格パラメータ・口調・テーマ配分・NGはすべて `persona/persona.json`。設計の詳細は `persona-design.md`（親プロジェクトのドキュメント）を参照
- **編集はノイズではなく資産**: 運用者（JOE）の編集差分を残すことで、「人間の感性がどこに介入するか」を後から分析できる
- **配信先は差し替え可能**: X APIの仕様・料金変更に備え、アダプタパターンで分離。投稿にURLを入れないのは料金設計のため（URL付き投稿は割高）
- **仮説駆動**: H1 一貫性→反応率 / H2 空間文脈→反応 / H3「会える」導線の効果、を Phase 2 以降で実測する

## キャラクター

- 名前: ハウル（howl＝遠吠え。投稿は遠くへ向けた遠吠え）
- 一人称: ワガハイ。飼い主は「家の人間」、読者は「人間」と呼ぶ
- Big Five: 開放性85 / 誠実性65 / 外向性60 / 協調性80 / 神経症傾向35
- 3Dモデルは当面 Quaternius（CC0）。Xでの活動開始直前に、採用済みのAI生成モデル（Tripo Smart Mesh）へ差し替える計画

## 関連

- 前作『Spatial AI NPC』: https://github.com/Joe-wolf-woof/spatial-ai-npc-webar
- Zenn記事（前作）: https://zenn.dev/joe_wolf_woof/articles/de818e9d30fcaa
