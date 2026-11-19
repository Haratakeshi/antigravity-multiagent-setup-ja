---
name: planning-advisor
description: "計画時点の横断設計アドバイザー（Claude Code Advisor Strategy の Antigravity 実装）。Neon 等の主担当が実装へ踏み切る前に、Swarm 動的モデル解決で最新内蔵 Claude Opus (High) / Gemini 4 Carbon (High) を単体または Dual 並列召喚し、責務境界・不可逆地雷・代替構造の短い戦略助言を取得する。"
---

# planning-advisor: 計画時点の横断設計アドバイザー召喚スキル

Claude Code の **Advisor Strategy**（高速な主担当＝`Orchestrator (Neon)` 等が実装を進める前段階・設計の要所で、最上位頭脳を Read-Only 相談役として呼び出し、**短尺・高密度の戦略助言**を受け取って迷走と手戻りを防ぐ設計）を Antigravity の Swarm / Subagent 機構へ統合したスキルです。

- **使い分け（`ask-opus` / `multi-agent-opinion` との境界）**:
  - **`planning-advisor`（本スキル — 着手前）**: 設計・計画段階で複数モジュール横断の責務境界・SSOT・不可逆リスク・代替構造を確認したいとき。
  - **`ask-opus`（実装中）**: 実装途中の難所・単発トレードオフ・3ストライク行き詰まり打破。
  - **`multi-agent-opinion` / `second-opinion-lite` / `carbon-verifier`（実装後）**: 変更完了後のクリーンルーム出口反証監査。
- **長時間タイムアウト緩和への最適化**: Antigravity v2.33.0 / CLI v1.5.10+ で 5分超の長考タイムアウト不具合が解消されたため、計画時の深掘りには積極的に **`High` ティア**（`claude-opus-*-high`, `gemini-4-carbon-high`）を使用できます。
- **100% Antigravity 内蔵完結**: 標準プリセットはすべて Antigravity 内蔵モデルを使用するため ToS 完全準拠・追加 API 課金ゼロです（外部 `ask-fable` は人間がチャットで明示指示し、かつ商用 `ANTHROPIC_API_KEY` が設定されている場合のみ任意併用）。

---

## 1. 発火基準（いつ呼び、いつ飛ばすか）

### 呼ぶべき場面（Call）
- 複数モジュール・パッケージ横断の変更、API / スキーマ / 契約変更、大規模リファクタの着手前
- 不可逆コストが高い作業（DB移行、永続化データ形式変更、認証/決済、状態遷移・非同期排他設計）の前
- `grill-me` / `grilling` の Step 8（最終 Plan 策定前）で横断的な blind-spot を潰したいとき
- ユーザーから「計画時点で Advisor に聞いて」「横断設計のアドバイスをもらって」等と指示されたとき

### 呼ばない場面（Skip — Break-Even 厳守）
- Tier 0 の単一ファイル・軽微修正や定型ボイラープレート（主担当 `Neon` が直接実装した方が速い）
- **初期オリエンテーション完了前**: 関連ファイル・現行契約・実装ドラフトを主担当がまだ集めていない状態で丸投げしない（まず主担当が `read_file` / `grep` または `flash-investigator` で地図とドラフトを用意してから召喚する）。

---

## 2. ゲート分離規律（`process-review-gate.mjs` 非バイパス）

> [!CAUTION]
> 計画時点の Advisor 召喚は、**実装後の Tier 1+ 出口反証監査（`second-opinion-lite` / `carbon-verifier` / Swarm `review-*` パネル）の代わりになりません**。
> `process-review-gate.mjs` と整合させるため、Swarm ワーカー名には必ず `advisor` を含め（**`plan-opus-advisor` / `plan-carbon-advisor`**）、実装後に別途コード監査を実行してください。

---

## 3. 動的モデル解決 & 3つの召喚プリセット（ハードコード禁止）

### Step 1: 動的モデル・クォータ確認
起動直前に `swarm(action="models", all=True)` を 1 回呼び出し、利用可能な最新モデルと `quota_remaining` を確認します。

| プリセット | 用途・場面 | 動的選択モデルとワーカー名（`workspace: "inherit"`, ナレーター/モニター不要） |
|---|---|---|
| **Preset 1: Single Cross-Vendor**（`Neon` 主担当時の標準既定） | 日常の中〜重要設計・Cross-Vendor 盲点チェック | `name: "plan-opus-advisor"` / `model`: 最新 `claude-opus-*-high`（`0%` 時: 最新 `claude-opus-*-medium` → `claude-sonnet-*-high`）。Claude 別クォータ枠を活用。 |
| **Preset 2: Single Same-Vendor Deep** | 巨大コードベース・長い依存鎖の深い構造精読 | `name: "plan-carbon-advisor"` / `model`: 最新 `gemini-4-carbon-high`（`0%` 時: `gemini-4-carbon-medium`）。 |
| **Preset 3: Dual High Advisor**（大型横断設計・高リスク改修） | 設計の取りこぼしを極小化したい節目（`Opus High × Carbon High`） | **1ターンで並列 `swarm(action="add")`**: `plan-opus-advisor`（最新 `claude-opus-*-high`）＋ `plan-carbon-advisor`（最新 `gemini-4-carbon-high`）。 |

※ **Human 明示指示＋商用 API キー時のみ（任意）**: ユーザーが「外部 `Fable` も入れて」等と明示した場合は、`ask_fable.py --human-authorized` を並列併用できます（API キー未設定・OAuth のみの場合は自動ブロック）。
※ **Mode C（メインが既に内蔵 Claude Opus）または `swarm` 非対応コンテキスト時**: `invoke_subagent(TypeName="planning-advisor", Role="Pre-Implementation Cross-Cutting Advisor", Model="inherit")`（親が Flash 時は `Model: "pro"`）を使用します。

---

### Step 2: 並列ブリーフ投入（Phase 1 独立助言）

1ターンで必要な Advisor ワーカーを `swarm(action="add", ...)` で起動し、**そのターンは他のボードツールを呼ばずに終了**して返信を待ちます（同一会話で名前が `kill` 済みの場合は `plan-opus-advisor-2` のように連番）。

```json
{
  "action": "add",
  "name": "plan-opus-advisor",
  "model": "<Step 1 で動的選択した最新 claude-opus-*-high>",
  "workspace": "inherit",
  "brief": "あなたは計画時点の横断設計アドバイザー（Read-Only）です。最初に理念 SSOT ファイル `~/.gemini/config/plugins/multi-agent-kit/agents/planning-advisor.md` を read_file で読み込み、その制約・横断チェック6軸・探索予算・5セクション簡潔フォーマットを厳守してください。\n\n- 製品コード編集・実行コマンド・ボード上の他メンバーへの追随は禁止です。\n- 完了時は `@coordinator [done]` を先頭に付けて1投稿のみ返してください（追加ヘルパーは起動しないこと）。\n\n【達成ゴール】\n<この実装で実現したいこと・非ゴール>\n\n【主担当の初期調査・実装計画ドラフト】\n<方針・変更予定概要>\n\n【参照対象ファイル・横断接続先（実コードを確認すること）】\n<対象ファイル・呼び出し元・契約・設定ファイルの絶対パス一覧>\n\n【守るべき不変条件・制約】\n<後方互換・データ移行・SLA・運用規約>"
}
```

### Step 3: 収束（意見割れ時の Phase 2）とクリーンアップ
1. 起床後、`read_board(since=0)` で助言を取得します。
2. **Dual High 時の最小クロス確認**: Opus と Carbon で `RETHINK` / `GO` が割れた場合や、指摘した地雷に明確な見解対立がある場合のみ、投稿番号（`#seq`）を指して 1 回だけボード上で突合します（例: `@plan-carbon-advisor #2 の Opus 見解（二重正本リスク）を実コード file:line で反証・検証して`）。一致または補強のみならクロス確認なしで即採用へ進みます。
3. 主担当側で指摘 `file:line` を実コード照合（§3.4）し、計画（`implementation_plan.md` 等）へ反映したうえで、不要になった Advisor ワーカーを `swarm(action="kill", name="...")` で終了します。
