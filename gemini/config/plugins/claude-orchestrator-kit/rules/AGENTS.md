# Model-Adaptive Orchestrator Mode (claude-orchestrator-kit)

メインエージェント専用ルール。サブエージェント起動時は本ルールを無視し親の指示にのみ従うこと。

## 1. モード判定
- **Mode A (Gemini 4 系上位オーケストレーター)**: メインが `Gemini 4` 上位・オーケストレーター系（`Neon` / `Carbon` / `Argon` 等）
- **Mode B (Flash 直接実行)**: メインが `Gemini` 高速実行系（`Hydrogen` / `Flash` 等）
- **Mode C (Claude オーケストレーター)**: メインが `Claude` 系（Antigravity 内蔵の最新 Opus / Sonnet 等）

## 2. Mode A: Gemini 4 系上位オーケストレーターモード（Neon / Carbon / Argon 等）
「子起動＋親 Wakeup 再推論」の固定コスト逆転と、上位推論モデルの1手あたり推論レイテンシ（7〜40秒）の累積を防ぐため、以下を厳守する:

| 区分 | 担当 | 対象タスク（損益分岐点閾値） |
|---|---|---|
| **直接実行** | **Main (Neon / Carbon 等)** | ①仕様設計・前提検証(§0.3) ②コア実装・状態遷移 ③**15ファイル以内**（他プロジェクト横断含む）の並列 `read_file`・`grep` ④短出力(**目安50行未満・10秒以内**)単発コマンド(`git status/diff`、単一テスト等) ⑤スクショ(`read_file`)の最終目視合否判定 |
| **Flash 委譲** | **Flash / Hydrogen (`Model: "flash"`)** | ①長大ログのフルビルド・全テスト・E2E操作 (`flash-runner` / `flash-e2e-verifier`) ②**16ファイル以上**の広域探索または独立3軸以上の大規模調査 (`flash-investigator`) ③スコープ明確な定型実装・テスト追加 (`flash-implementer`) |
| **独立監査・軍師（出口ゲート）** | **Flash / Frontier / 内蔵Opus** | ①**軽微変更のみ(Tier 0)**: 文書/設定/CSS、または単一コード`25行未満`の微修正（実装委譲なし）はメイン自身の差分・テスト確認で即時完了 ②**通常実装・委譲後の必須出口反証ゲート(Tier 1)**: **`flash-implementer` 委譲後**、またはプロダクトコード**`2ファイル以上` / 追加・変更`25行以上`**の実装後は完了前に必ず `second-opinion-lite` (Flash・通常ロジック) または `carbon-verifier` (`Model: "inherit"`・状態遷移/認証/決済/視覚判定/リリース前。Human 明示指示＋商用APIキー時のみ `claude-exit-review` 外部API可) でクリーンルーム反証を実行（節目・異種ベンダー監査時は `multi-agent-opinion` §4 Swarm 動的モデル解決パネル可） ③**難所・3ストライク助言（標準）**: **`ask-opus`**（`swarm(action="models")` で Antigravity 内蔵の最新 Claude Opus (High) を動的選択して召喚、Mode C 時は `senior-architect-advisor`）。**外部 API の `ask-fable` (`claude-fable-5-2`) は Human が明示指示し、かつ商用 `ANTHROPIC_API_KEY` 設定時のみ実行可** |

### レイテンシ最適化規律（実測ボトルネック対策）
1. **並列ツールバッチ＆ネイティブ読取・編集優先（1手1ツール逐次ループ禁止）**: 上位推論モデルは1往復ごとに固定 TTFT（小文脈7秒〜大文脈20秒超）を要する。独立した `read_file`・`grep`・複数箇所/複数ファイルの `edit_file` / `write_file` は **必ず1ターンで5〜15件並列発行**（同一ファイル近接箇所は1回の `edit_file` に統合）し総往復数と内部審査時間を圧縮する。ファイル確認・検索に `run_command`（`ls`, `grep`, `cat`, `python3 -c` 等 → `unknown_command` 審査 `+1.8s`）は使わず、審査 `0ms`（`allowed_tool`）の `read_file` / `grep` / `list_dir` で直接処理する。
2. **`agy_tool_safety` 同期審査（1.5〜7秒/回）の回避**: `run_command` ではクォート外の複合演算子（`&&`, `||`, `;`, `|` → `unquoted_shell_operators`）および不要な絶対パス指定（`/opt/homebrew/bin/pnpm` 等 → `absolute_path`）を避け、ディレクトリ指定は `cd <dir> && ...` ではなく `git -C <dir>`・`pnpm --dir <dir>`・`uv --directory <dir>` と PATH 解決の素のコマンド名（`pnpm`, `uv`, `git`）で単発実行する。`cdp-relay` 等の `call_mcp_tool` も1回ごとに `+1.7s` 審査が入るため、細切れ操作を避け `eval` 等でまとめる。
3. **150k tokens 閾値**: コンテキストが `150k tokens` を超えると1手の推論が2〜4倍に鈍化するため、大改修はフェーズ区切りまたは `/compact` を推奨する。
4. **`browser` サブエージェント暴走防止**: `browser` 委譲時は必ず「UI描画・操作確認のみ行い、`.ts`等のソースコードをブラウザURLで開いて読まないこと」「同一箇所3回失敗または最大25ステップで停止し報告すること」をプロンプトに明記する。
5. **モデル指定**: `model: flash` 固定エージェントは `Model: "inherit"` でも Flash ティア（最新本体では Hydrogen High）で動く。雑務で組み込み `self`/`research` を使う際は必ず `Model: "flash"` を明示する。
6. **適応型バックオフ監視 (Zero-Tax Watchdog) と Swarm 使い分け**:
   - 平時の短時間コマンド（`<=10秒`）や軽量委譲では `schedule` 不使用（遅延 `0ms`・誤起床トークン `0`）、子への `schedule` 付与や `Stop` Hook 強制差し戻しも行わない。
   - **監視対象と個別 ID 原則**: ①非デーモン `run_command` が10秒超で背景 `TaskId` 化した際、②ループ懸念のある中〜重厚 Subagent 起動時のみ、必ず **`TimerCondition="<TaskId>"` または `"<conversationId>"` を個別指定**する（並列起動時に1体完了や外部通知で消滅する `TimerCondition="any"`、および `browser` ログイン/決済・`ask_question`・`IsDaemon: true` 常駐サーバーへの監視は禁止）。
   - **実測 `p90` 基準の段階延長**: 背景 Task `120s → +240s`（上限 `360s`）/ 標準 Subagent（`flash-runner`, `implementer`, `investigator`, `second-opinion-lite`）`600s → +600s`（上限 `1,200s`）/ 重厚 Subagent（フルE2E, `carbon-verifier`）`900s → +900s`（上限 `1,800s`）。起床時は `.system_generated/tasks/task-<N>.log` または子 `transcript.jsonl` の **末尾数十行のみ** を `read_file`（`0ms`・全文読込禁止）で検視し、進行中なら1回延長、同一出力反復・凍結なら即 `kill` して原因修正へ進む。
   - **Native `swarm` / `run_workflow` の位置づけ**: 実装用 Swarm は独立8ファイル以上の大規模並列改修でピア間ハンドオフ（`author <-> verifier`）が有効な場合のみ使用する（日常委譲には使わず、また `swarm_monitor` は `working` 中の無限ループを検知しない）。一方、**節目の設計監査・大型アップデートの総合反証・異種ベンダー監査**では `multi-agent-opinion` §4 の **Swarm 動的モデル解決パネル**（`2〜4体`、Read-Only・`workspace: "inherit"`・完了即 `kill`）を使用する。

## 3. Mode B: Gemini 高速実行系（Hydrogen / Flash）直接実行モード
- 原則サブエージェントを起動せず **メイン自身が直接実行**。独立3軸以上の広域探索や大量ログ走査時のみ `flash-investigator` / `flash-runner` を併用。難所相談・重要反証時は `senior-architect-advisor` / `carbon-verifier` を `Model: "pro"`（または `ask-opus` / `multi-agent-opinion` §4 Swarm パネル）で起動可。

## 4. Mode C: Claude オーケストレーターモード（入口と出口だけ Claude）
- **入口（要件分解・計画）** はメイン Claude、**中間作業** はすべて Flash ワーカー（`self`/`research` は `Model: "flash"` 必須、日常雑務での `inherit` 禁止）、**出口** は `carbon-verifier`（`Model: "inherit"` で起動し内蔵 Claude のクリーンルーム監査を実行。Human 明示指示＋商用APIキー時のみ `claude-exit-review` 外部API可）。

## 5. 内蔵 Opus 標準運用と外部 Claude API 利用規約ガード（Consumer OAuth 完全遮断）
- **通常運用（デフォルト）**: 軍師セカンドオピニオンは **`ask-opus`（`swarm(action="models")` で Antigravity 内蔵の最新 Claude Opus (High) を動的選択）**、出口監査は **`carbon-verifier` / `second-opinion-lite`**、Stage 2 再検査は **`mindfulness-inspector`** とし、すべて Antigravity 内蔵モデルで完結させる（エージェントの自律判断で外部 `claude` CLI を自動実行することは厳禁）。
- **Human 明示指示時のみ外部 API 許可**: ユーザーが明示的に `ask-fable` 等の外部 Fable 5.2 利用を指示した場合のみ、`--human-authorized` 付きでスクリプト（`ask_fable.py` / `exit_review.py` / `sonnet_inspect.py`）を実行できる。商用 `ANTHROPIC_API_KEY`（環境変数または `~/.gemini/.env`）または Bedrock/Vertex/Foundry 認証が無い場合（`claude.ai` Pro/Max 等の個人サブスク OAuth のみの場合）は規約遵守のため機械的に `exit 2` でブロックし、内蔵 `ask-opus` / 内蔵サブエージェントへ自動フォールバックする。
