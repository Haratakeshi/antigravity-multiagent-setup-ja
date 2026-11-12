# Model-Adaptive Orchestrator Mode (claude-orchestrator-kit)

メインエージェント専用ルール。サブエージェント起動時は本ルールを無視し親の指示にのみ従うこと。

## 1. モード判定
- **Mode A (Carbon / Argon オーケストレーター)**: メインが `Gemini 4 Carbon` または `Gemini 4 Argon` 系
- **Mode B (Flash 直接実行)**: メインが `Gemini Flash` 系
- **Mode C (Claude オーケストレーター)**: メインが `Claude` 系（Antigravity 内蔵 Opus / Sonnet 等）

## 2. Mode A: Gemini 4 Carbon オーケストレーターモード（Argon 互換）
「子起動＋親 Wakeup 再推論」の固定コスト逆転と、Carbon の1手あたり推論レイテンシ（7〜40秒）の累積を防ぐため、以下を厳守する:

| 区分 | 担当 | 対象タスク（損益分岐点閾値） |
|---|---|---|
| **直接実行** | **Carbon (Main)** | ①仕様設計・前提検証(§0.3) ②コア実装・状態遷移 ③**3ファイル以内**の読取・grep ④短出力(**目安50行未満・10秒以内**)単発コマンド(`git status/diff`、単一テスト等) ⑤スクショ(`read_file`)の最終目視合否判定 |
| **Flash 委譲** | **Flash (`Model: "flash"`)** | ①長大ログのフルビルド・全テスト・E2E操作 (`flash-runner` / `flash-e2e-verifier`) ②**4ファイル以上**・複数軸の並列探索 (`flash-investigator`) ③スコープ明確な定型実装・テスト追加 (`flash-implementer`) |
| **独立監査・軍師** | **Flash / Carbon / CLI** | ①通常差分高速監査: `second-opinion-lite` (Flash) ②重要状態遷移・リリース前反証: `carbon-verifier` (`Model: "inherit"`) ③難所・3ストライク助言: `ask-opus`（不在時 `senior-architect-advisor`） |

### Carbon レイテンシ最適化規律（実測ボトルネック対策）
1. **並列ツールバッチ必須（1手1ツール逐次ループ禁止）**: Carbon は1往復ごとに固定 TTFT（小文脈7秒〜大文脈20秒超）を要する。独立した `read_file`・`grep`・複数箇所の `edit_file` は **必ず1ターンで複数並列発行** し、総往復数を圧縮する。
2. **単純正則コマンド優先**: 内部 Safety Monitor が `&&`, `;`, `|` 等の複合シェル演算子ごとに同期 LLM 審査（1.5〜7秒）を挟むため、`run_command` は不要な複合連結を避ける。
3. **150k tokens 閾値**: コンテキストが `150k tokens` を超えると1手の推論が2〜4倍に鈍化するため、大改修はフェーズ区切りまたは `/compact` を推奨する。
4. **`browser` サブエージェント暴走防止**: `browser` 委譲時は必ず「UI描画・操作確認のみ行い、`.ts`等のソースコードをブラウザURLで開いて読まないこと」「同一箇所3回失敗または最大25ステップで停止し報告すること」をプロンプトに明記する。
5. **モデル指定**: `model: flash` 固定エージェントは `Model: "inherit"` でも Flash で動く。雑務で組み込み `self`/`research` を使う際は必ず `Model: "flash"` を明示する。

## 3. Mode B: Gemini Flash 直接実行モード
- 原則サブエージェントを起動せず **Flash 自身が直接実行**。独立3軸以上の広域探索や大量ログ走査時のみ `flash-investigator` / `flash-runner` を併用。難所相談・重要反証時は `senior-architect-advisor` / `carbon-verifier` を `Model: "pro"` で起動可。

## 4. Mode C: Claude オーケストレーターモード（入口と出口だけ Claude）
- **入口（要件分解・計画）** はメイン Claude、**中間作業** はすべて Flash ワーカー（`self`/`research` は `Model: "flash"` 必須、日常雑務での `inherit` 禁止）、**出口** は `claude-exit-review`（外部CLI不在時は `carbon-verifier` を `Model: "inherit"` で起動し内蔵 Claude のクリーンルーム監査を実行）。

## 5. 外部 Claude CLI 不在環境での自動フォールバック
外部 `claude` CLI 未導入・未認証（終了コード `2`）時は、内蔵サブエージェントへ自動切替する（メインが Carbon/Argon/内蔵Claude なら `Model: "inherit"`、Flash なら `Model: "pro"`）:
- `ask-opus` → **`senior-architect-advisor`** / `claude-exit-review` → **`carbon-verifier`** / `sonnet_inspect.py` → **`mindfulness-inspector`**
