# howl-agent — AIキャラクター「ハウル」自律運用エンジン

WebAR作品『Spatial AI NPC』のオオカミを、独立したAIキャラクター **ハウル** として運用するためのエージェント本体。
Xで自律発信し、WebARでは同じキャラクターに「会える」——作って終わりではなく、運用と測定までを含むプロジェクトの Phase 1 実装です。

## 現在の状態（Phase 1 + Phase 2）

- ✅ ペルソナエンジン: Big Fiveパラメータ＋行動変換ルールを `persona/persona.json` に外出しし、システムプロンプトへ変換
- ✅ 投稿生成: Gemini API（モデルフォールバック付き）。APIキーなしでも `--stub` で全フロー検証可能
- ✅ Phase A フロー（承認＋編集制）: 生成 → 人間が文章を編集 → 承認 → 投稿。編集差分は記録され、将来のペルソナ調整の材料になる
- ✅ NGフィルタ: 文字数（140字）・URL・NGワードの機械チェック
- ✅ エピソード記憶: `data/episodes.json` に蓄積し、生成時に参照（投稿すると自動で1件追加）
- ✅ 配信アダプタ: 疎結合な interface。DryRun（outboxへ記録、実投稿しない）と X API の両対応
- ✅ X APIアダプタ: `POST /2/tweets` を OAuth 1.0a 署名つきで実装済み（署名ロジックは公開テストベクトルで検証）。Xアカウント（@AgentName_Howl）で実投稿の運用中
- ✅ 一貫性チェッカー（Phase 2）: ペルソナ適合を5軸×20点＝100点で採点（本命は Gemini 採点、キーなし時はルールベースに自動フォールバック）。合格ライン80点
- ✅ 全自動投稿（Phase 2 / Phase B）: チェッカー80点以上＋NGフィルタ通過が必須。キルスイッチ（環境変数／ファイル）と1日2件上限つき
- ✅ A/B測定（Phase 2）: variant を日替わりで交互（persona＝記憶＋ペルソナ／plain＝素の生成）に切り替え、下書きとエピソードに記録。`report` で variant 別に集計し、X の public_metrics 平均も取得可能

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

Phase 2 のコマンド:

```bash
# 既存の下書きを一貫性チェッカーで採点（軸別スコアと合計を表示。結果は下書きに記録される）
python -m src.cli check <id>                          # Gemini採点（キーなし時はルールベース）
python -m src.cli check <id> --stub                   # ルールベース採点のみ

# 生成→採点し、80点未満なら再生成（最大で追加2回）。最良の下書きが残る
python -m src.cli generate --check [--theme psychology] [--stub]

# 全自動投稿（Phase B）: その日のテーマと variant を日付ローテーションで決定し、
# チェッカー＋NGフィルタを通った場合のみ投稿する
python -m src.cli autopost --dry-run --stub           # 検証（実投稿しない）
python -m src.cli autopost                            # 実投稿（X認証情報が必要）

# 止め方（キルスイッチ）: どちらかで autopost は即座に中止する
HOWL_AUTOPOST=off python -m src.cli autopost
touch data/AUTOPOST_OFF

# 測定レポート: 投稿済みエピソードを variant 別に集計。
# 実投稿の tweet_id があり X 認証情報が使える場合は、いいね/返信/インプレッションの平均も出す
python -m src.cli report [--no-metrics]
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
- 一人称: ワガハイ。JOEのことは基本「家の人間」「家主」と呼び、たまに文脈に合わせて「ご主人」とも呼ぶ。読者は「人間」と呼ぶ
- Big Five: 開放性85 / 誠実性65 / 外向性60 / 協調性80 / 神経症傾向35
- 3Dモデルは当面 Quaternius（CC0）。Xでの活動開始直前に、採用済みのAI生成モデル（Tripo Smart Mesh）へ差し替える計画

## 関連

- 前作『Spatial AI NPC』: https://github.com/Joe-wolf-woof/spatial-ai-npc-webar
- Zenn記事（前作）: https://zenn.dev/joe_wolf_woof/articles/de818e9d30fcaa
