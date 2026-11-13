# howl-agent — AIキャラクター「ハウル」自律運用エンジン

WebAR作品『Spatial AI NPC』のオオカミを、独立したAIキャラクター **ハウル** として運用するためのエージェント本体。
Xで自律発信し、WebARでは同じキャラクターに「会える」——作って終わりではなく、運用と測定までを含むプロジェクトの Phase 1 実装です。

## 現在の状態（Phase 1 + Phase 2 + Phase 3）

- ✅ ペルソナエンジン: Big Fiveパラメータ＋行動変換ルールを `persona/persona.json` に外出しし、システムプロンプトへ変換
- ✅ 投稿生成: Gemini API（モデルフォールバック付き）。APIキーなしでも `--stub` で全フロー検証可能
- ✅ Phase A フロー（承認＋編集制）: 生成 → 人間が文章を編集 → 承認 → 投稿。編集差分は記録され、将来のペルソナ調整の材料になる
- ✅ NGフィルタ: 文字数（140字）・URL・NGワードの機械チェック
- ✅ エピソード記憶: `data/episodes.json` に蓄積し、生成時に参照（投稿すると自動で1件追加）
- ✅ 配信アダプタ: 疎結合な interface。DryRun（outboxへ記録、実投稿しない）と X API の両対応
- ✅ X APIアダプタ: `POST /2/tweets` を OAuth 1.0a 署名つきで実装済み（署名ロジックは公開テストベクトルで検証）。Xアカウント（@AgentName_Howl）で実投稿の運用中
- ✅ 一貫性チェッカー（Phase 2）: ペルソナ適合を5軸×20点＝100点で採点（本命は Gemini 採点、キーなし時はルールベースに自動フォールバック）。合格ラインは persona が総合80点、plain は安全性の軸が満点かつ総合60点超え（A/B の条件を保ちつつ品質の下限を担保）
- ✅ 全自動投稿（Phase 2 / Phase B）: variant 別のゲート＋NGフィルタ通過が必須。キルスイッチ（環境変数／ファイル）と1日2件上限つき
- ✅ A/B測定（Phase 2）: variant を日替わりで交互（persona＝記憶＋ペルソナ／plain＝素の生成）に切り替え、下書きとエピソードに記録。`report` で variant 別に集計し、X の public_metrics 平均も取得可能
- ✅ 記憶の統合（Phase 3）: ARアプリ『ハウリング』の「見せた記憶」を `import-episodes` で中央の記憶へ取り込み（冪等）、生成文脈では「見せてもらった物」として優先的に参照。逆方向は `export-memory` でアプリ形式の memory.json を書き出し
- ✅ 行動選択レイヤー（Phase 3）: 投稿前に状態（記憶・投稿履歴・テーマの間隔・継続目標・直近の反応）を見て、今日の一手（記憶について書く／巡回表のテーマ／問いかけ／休む）を選択。判断は下書きと `data/decisions.jsonl` に記録される
- ✅ 目標駆動（Phase 3）: 継続目標を3件（learn／circulate／structure）に置き換え。`weekly-review` が直近7日の運用を集計して学びと来週の方針を `data/reflections.jsonl` に記録し、selector は最新の振り返り（learnings）と目標の進捗を翌週の選択に反映する

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

# 『ハウリング』の「見せた記憶」書き出しJSON（[{date, kind:"shown", text}]）を
# 中央のエピソード記憶へ取り込む。(date, text) で重複判定するため冪等
python -m src.cli import-episodes howling-episodes.json

# 中央ストアからアプリ形式の memory.json を書き出す（posts / episodes / updatedAt）
python -m src.cli export-memory ../howling/data/memory.json

# 週次振り返り（目標駆動）: 直近7日の投稿・選択・スコア（実モードでは反応も）を
# 集計し、学びと来週の方針を data/reflections.jsonl に記録する。
# --append-md で Markdown（運用の学びノート）にもセクションを追記できる
python -m src.cli weekly-review [--days 7] [--stub] [--append-md learnings.md]
```

### Phase 3: 行動選択レイヤー（編集長）

Phase B までの autopost は「日付ローテーションで決めたテーマで書く」だけだった。
Phase 3 からは投稿前に `src/selector.py` が現在の状態を見て今日の一手を選ぶ:

- **状態**: 直近のエピソード（見せてもらった物を含む）、直近の投稿とテーマ別の間隔、巡回表の今日の候補、継続目標（`data/goals.json` の3件。下記「目標駆動」参照）の進捗、最新の週次振り返り（learnings）、実投稿モードでは直近投稿の反応メトリクス
- **選択肢**: `memory`（記憶にある出来事について書く）／`theme`（巡回表のテーマ＝既定案）／`question`（人間に問いかける）／`rest`（今日は休む。投稿もエピソードも残らない）
- **実装**: 本命は Gemini の JSON 選択、失敗時はルールベース（3日以内の未投稿の「見せた記憶」があれば memory、無くても未投稿のまま4日以上経過した記憶があれば循環目標のため memory を優先、それ以外は theme）
- **A/B の保全**: plain の日は状態からエピソード本文とペルソナ情報を一切含めず（件数と日付だけ）、選択肢も theme / question / rest のみ
- **記録**: 選択は結果と下書きの `decision` に残り、実運用では `data/decisions.jsonl` にも1行ずつ追記される。安全装置（ゲート・NGフィルタ・上限・キルスイッチ）は Phase 2 のまま

### 目標駆動：3つの継続目標と週次振り返り

ハウルの行動選択は、`data/goals.json` の3つの継続目標を判断材料にする。X運用（反応と継続）とポートフォリオ（仮説検証の実証）の両方に効くよう設計した:

| id | 目標 | 進捗の測り方（selector の状態） |
|---|---|---|
| learn | 毎週、反応の良かったテーマと書き方を振り返り、翌週の選び方に反映する | 最後の振り返りからの日数と、方針の有無。最新の振り返り（policy＋学び上位3件）は `learnings` として状態に入る |
| circulate | ARで見せてもらった物を投稿に変え、投稿をアプリの挨拶に返す循環を絶やさない | 未投稿の「見せた記憶」の件数と最古の経過日数、循環の成立回数（見せた日から7日以内に、その記憶を対象に投稿できた場合に成立）と最終成立日 |
| structure | 週に一度、運用で分かったことを短い要約として記録する | `data/reflections.jsonl` の件数と最終記録日 |

`weekly-review`（`src/review.py`）が structure と learn の実践になる:

- 直近7日（`--days` で変更可）の投稿エピソードを variant別・テーマ別に集計し、`decisions.jsonl` から行動選択の内訳、下書きから一貫性チェッカーの平均を出す。実モード（`--stub` でない）ときは X の public_metrics から直近投稿の反応平均も取得する（失敗したら静かに省略）
- 学び（learnings）と来週の方針（policy）は Gemini の JSON 生成が本命。使えない場合は数値からテンプレートで組み立てる（記録には method が残る）
- 記録は `data/reflections.jsonl` に1行ずつ追記され（git管理外）、`--append-md <path>` で Markdown ノートにも週次セクションを追記できる
- A/B の保全は selector と同じ: plain の日の状態には目標文や学びの本文は含めず、件数だけを渡す

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
