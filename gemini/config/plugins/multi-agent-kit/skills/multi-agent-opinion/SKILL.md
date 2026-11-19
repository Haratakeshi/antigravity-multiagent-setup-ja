---
name: multi-agent-opinion
description: Risk-scaled multi-agent clean-room review pipeline (Tier 0-3, 3-strike PDCA).
---

# Multi-Agent Second Opinion Review Skill（リスク比例・複眼型セカンドオピニオン）

コード実装・設計提案・3ストライク課題に対し、変更リスクと速度要件に応じた **Tier 0〜3 の4段階スケーリング** で独立監査とマインドフルネス事実再検査を実行する標準パイプラインです。各専門エージェントのプロンプト正本（SSOT）は `~/.gemini/config/plugins/multi-agent-kit/agents/*.md` に格納されており、本ファイルには重複記載しません。

- **2レーンハイブリッド設計（理念 SSOT × モデル追従の分離）**:
  - **Lane 1（通常レーン・`invoke_subagent`）**: Tier 1（`second-opinion-lite` / `carbon-verifier`）および日常の軽量 Tier 2 では、機械的 Read-Only・完全隔離・最速の `invoke_subagent`（`flash` / `inherit` / `pro` — 本体アップデートで最新世代へ自動追従）を使用します。
  - **Lane 2（Swarm 動的モデル解決パネル・`swarm`）**: 節目の重要設計・大型アップデートの総合反証・最新世代モデルの明示指名・異種ベンダー（Gemini × Claude）複眼レビューが必要な場合は、**§4 の動的モデル解決パネル**を使用します（`swarm(action="models")` で実行時にモデル解決するため世代固定なし）。

---

## 0. リスク・速度比例スケーリング（Tier 0〜3）と設定値

| Tier | 適用場面 | 起動構成 | トークン・時間負荷 |
|---|---|---|---|
| **Tier 0（軽微変更のみ）** | 文書・設定・文言/CSS微調整・単一コード`25行未満`の微修正（実装委譲なし） | メインエージェント自身の差分確認＋テスト実行＋`§0.5` セルフレビューのみで即時完了（監査サブエージェント自動起動なし） | ゼロ |
| **Tier 1（実装出口ゲート・既定）** | **`flash-implementer` 委譲後** / コード`2ファイル以上`または`25行以上`の通常実装・バグ修正 / 明示依頼 | 通常ロジックは高速な `second-opinion-lite`（Flash並列読取・1〜2分）、重要状態遷移・認証・決済・視覚判定・リリース直前は `carbon-verifier`（`Model: "inherit"`, 12往復上限） | 極小 |
| **Tier 2（標準 `multi-agent-opinion`）** | 共有状態・認証・API契約・状態遷移・複数モジュール変更 | Lane 1: Stage 1 **ドメイン選抜 3〜6体**（`Model: "flash"`）＋ Stage 2（再検査＋関連監査1〜2体）、または Lane 2: **§4 Swarm 動的モデル解決パネル** | 中 |
| **Tier 3（フルゲート）** | リリース前最終監査 / 明示的な「フル15体」指定 / **モードB（3ストライク）** | Stage 1 **全15体**（`Model: "flash"`）＋ Stage 2 **全3体** | 大（ここぞの場面のみ） |

- **タイムアウト**: Stage 1 `180秒` / Stage 2 `180秒`（親待機フォールバック `360秒`）
- **差分分割目安**: 1回あたり `500行 / 20KB` 超はモジュール単位で分割
- **収束判定（3-Strike PDCA、最大3ラウンド）**:
  - **収束完了**: `[CONFIRMED]` な `CRITICAL == 0` かつ `WARNING < 5`（または全 `[CLEAN]`）
  - **未収束（再実行）**: `[CONFIRMED]` な `CRITICAL >= 1` または `WARNING >= 5` → 修正＋ビルド/テスト確認後に次ラウンド
  - **上限到達**: Round 3 後も未収束 → 自動ループを停止し `pending_human_review` で人間へエスカレーション

---

## 1. 起動モード（クリーンルーム入力規律）

| モード | 用途 | レビュアーに渡す入力（これ以外は渡さない） | 出力形式 |
|---|---|---|---|
| **A: 成果物レビュー** | コミット/PR前・重要実装後 | 機密マスク済み Git 差分/対象コード ＋ 不変条件仕様（Invariants Spec / docs / API契約）。※作成側の思考経緯・言い訳は厳禁 | `[CATEGORY] [LEVEL] [file:line](url) - 内容 + 修正案` |
| **B: 発想転換（3ストライク）** | `GEMINI.md §0.2` 3連続失敗時 | 達成したいゴール ＋ 対象コードパスのみ（失敗ログ・仮説は遮断） | `[CATEGORY] [DIRECTION: 方針名] 内容 + 根拠` |
| **C: 設計ドラフト反証** | 実装前の設計・UX・構成計画 | 提案ドラフト ＋ 解決すべき症状 ＋ 現行コード/SSOT仕様パス（※横断設計の事前助言は `planning-advisor` / `agents/planning-advisor.md` も併用可） | `[CATEGORY] [VERDICT: REFUTED/SURVIVED/GAP] [file:line](url) - 反証と最小代替案` |

---

## 2. ドメイン選抜マトリクス（Tier 2 用）

Tier 2 では以下のカタログから **必須コア（2体）＋ 変更領域に合致する専門役（1〜4体）＝ 計3〜6体** を選抜します（該当しない領域はスキップします）:

1. **必須コア**: `zero-bias-reviewer`（事実誤認排除）＋ `domain-logic-reviewer`（またはセキュリティ主体の変更時は `security-vulnerability-reviewer`）
2. **セキュリティ・認可・外部入力**: `security-vulnerability-reviewer`
3. **パッケージ追加・lockfile変更**: `supply-chain-reviewer`
4. **例外処理・外部I/O・タイムアウト・冪等性**: `resilience-operations-reviewer`
5. **テスト追加・モック変更**: `test-integrity-reviewer`, `qa-adversarial-reviewer`
6. **タイマー・時計分離・状態遷移ライフサイクル**: `lifecycle-clock-reviewer`, `contract-boundary-reviewer`
7. **決定論・定数外部化・アーキテクチャ憲法**: `architecture-invariants-reviewer`
8. **仕様書同期・データ移行・ファイル分割**: `ssot-doc-sync-reviewer`, `data-migration-gap-reviewer`
9. **UI画面・表示計算同期・WCAG**: `ui-state-parity-reviewer`, `usability-accessibility-reviewer`
10. **DBクエリ・メモリ・競合・ホットパス**: `performance-efficiency-reviewer`
11. **Web公開準備 / Stripe決済**: `web-production-readiness-reviewer` / `stripe-billing-readiness-reviewer`

※ Tier 3 またはモード B では上記 1〜10 の基本15体すべてを並行起動します。

---

## 3. 実行ステップ

### ステップ 1: 対象抽出と機密マスク（モード A）
- `.env*`, `*.pem`, `*.key`, `*secret*` 等の機密ファイルを除外し、トークン・鍵文字列は `[REDACTED]` に置換して `<appDataDir>/brain/<conversation-id>/scratch/`（または `/tmp/`）に保存します。差分が空なら即時終了します。

### ステップ 2: ステージ1並行起動 (`invoke_subagent`, `Model: "flash"`)
- Tier 2（選抜3〜6体）または Tier 3（全15体）を並行起動し、`schedule(DurationSeconds=180, TimerCondition="never")` を設定します。
- 全員応答後にタイマーを `manage_task(Action="kill")` で解除します。全件 `[CLEAN]` ならステージ2を省略して完了レポートを出力します。重要レビュアーが異常終了・タイムアウトした場合は Fail-Closed としてリトライまたは `pending_human_review` とします。

### ステップ 3: ステージ2 事実再検査＆複合監査
ステージ1の中間指摘集約と対象コードに対し、以下を並行実行します:

1. **マインドフルネス事実再検査（既定: 内蔵 `mindfulness-inspector` サブエージェント / Human明示指示＋商用API時のみ外部 `sonnet_inspect.py`）**:
   - **標準動作（デフォルト）**: 内蔵サブエージェント **`mindfulness-inspector`（`Model: "inherit"`、メインが Flash 時は `"pro"`）** を `invoke_subagent` で起動し、ステージ1指摘と対象パスを渡して `[CONFIRMED / UNVERIFIED / REJECTED]` の3分類再検査を実行します（Consumer OAuth 保護および意図しない API 従量課金防止のため、通常時は外部 CLI を自動実行しません）。
   - **Human 明示指示時のみ（商用 `ANTHROPIC_API_KEY` 必須）**: 人間が明示的に外部 Fable 5.2 API での Stage 2 再検査を指示した場合に限り、`--human-authorized` 付きで `sonnet_inspect.py` を実行できます（API キー未設定時は `exit 2` で自動ブロックされるため、直ちに内蔵 `mindfulness-inspector` へフォールバックします）：
     ```bash
     python3 ~/.gemini/config/plugins/multi-agent-kit/skills/multi-agent-opinion/scripts/sonnet_inspect.py \
       --human-authorized \
       --findings <ステージ1指摘.md> --target <対象パス> [--target ...] \
       --repo <作業ディレクトリ> --out <artifactDir>/sonnet_inspection.md
     ```
2. **複合リスク・アーキテクチャ整合性監査（`Model: "inherit"`）**:
   - Tier 2 では変更内容に関連する `cross-domain-combinatorial-auditor`（複合状態・競合）および/または `system-architecture-coherence-auditor`（レイヤ境界・DRY）の1〜2体、Tier 3 では両方を並行起動します。

### ステップ 4: 統合レポート出力とデッドロック防止クリーンアップ（v2.30+ 準拠）
1. 実行主体自身の artifact ディレクトリ（`<appDataDir>/brain/<実行中のconversation-id>/second_opinion_report.md`, `ArtifactMetadata` 付き）へ `write_to_file` で出力します（※Antigravity v2.30+ ではサブエージェントから親の `brain/` ディレクトリへの書き込みはエラーになるため、サブエージェント自身の `brain/<subagent-id>/` に保存して絶対パスを親へ共有します）。
   - 構成: `1. Executive Summary` / `2. CONFIRMED Findings` / `3. Combinatorial & Architecture Risks` / `4. UNVERIFIED` / `5. REJECTED (反証根拠つき)` / `6. Action Queue`。
2. **最優先通知**: クリーンアップコマンドより前に、必ず `send_message` で親へ5行以内の要約とレポート絶対パスを送信します。
3. **安全な後始末**: `/tmp/<具体名>` 以外のパスに対する `rm` は `guard_commands.py` の `force_ask` によりバックグラウンドで停止するため、`brain/.../scratch/` 配下の一時ファイル破棄は空文字上書き（`Overwrite: true`）を使用し（`/tmp/<具体名>` 配下のみ `rm -rf /tmp/<具体名>` が自動許可されます）、`manage_subagents(Action="kill_all")` で子エージェントを終了します。

---

## 4. Swarm 動的モデル解決パネル（最新モデル指名・異種ベンダー複眼監査レーン）

日常の Tier 1 / 軽量 Tier 2 は軽量・機械的 Read-Only・完全隔離の **Lane 1（`invoke_subagent`）** を維持しつつ、**節目の重要設計・大型アップデートの総合反証・最新世代モデルの明示指名・異種ベンダー（Gemini × Claude）複眼レビュー** では本節の **Lane 2（Swarm 動的モデル解決パネル）** を使用します。

- **設計原則（理念 SSOT とモデル選定の分離）**:
  - `~/.gemini/config/plugins/*/agents/*.md` は「何を守り、どう反証し、どの形式で返すか」の純粋な理念正本（SSOT）として使い回し、プロンプトやスキルに世代固定のモデル ID をハードコードしません。
  - `swarm` ツールはトップレベルのメインエージェントのみ所持するため、本レーンはメインエージェントから直接起動します（`multi-agent-opinion` サブエージェント内からは `swarm` 非対応のため Lane 1 を使用）。

### Step 1: 動的モデル解決（ハードコード禁止）
1. 起動直前に **`swarm(action="models", all=True)`** を1回呼び出し、その時点で利用可能な最新モデル一覧と `quota_remaining` を取得します（※Gemini 群と Claude / GPT-OSS 群は別クォータ枠で計算されるため、異種ベンダー併用は精度向上とクォータ分散の両方に効きます）。
2. 一覧と残クォータから、次の **能力ポリシー** に従って実行モデルを動的決定します（対象モデルが `0%` の場合はフォールバック順へ切替）：
   - **高速広域スキャン枠（Fast Tier）**: 一覧中の最新 Gemini 高速実行系 `high`（例: `gemini-4-hydrogen-high`、残 `0%` 時は同 `medium` または次点の最新高速系）
   - **同系最上位・深掘り枠（Frontier Tier）**: 一覧中の最新 Gemini 上位系の標準/高推論（例: `gemini-4-neon-high`、重い設計相談や極限のセキュリティ・長距離検証時は v2.33.0+ の長考タイムアウト解消により **`gemini-4-carbon-high`** / `medium` も安定利用可。単純タスクでの overthinking を避けるため `xhigh` は明示要求時のみ）
   - **異種ベンダー反証枠（Cross-Vendor Tier）**: 一覧中の最新内蔵 `claude-opus-*-high`（例: `claude-opus-5-5-high`、残 `0%` 時は最新 `claude-opus-*-medium` → 最新 `claude-sonnet-*-high`）

### Step 2: Phase 1 — 独立並列査読（同調バイアス防止・1ターン同時 `add`）
1. §2 のマトリクスから変更内容に直結する直交ロール **2〜4体**（例: Fast 枠 1〜2体 ＋ Frontier 枠 1体 ＋ Cross-Vendor 枠 1体）を選抜し、**1ターンで同時に `swarm(action="add", ...)`** を発行して、そのターンは他のボードツールを呼ばずに終了します（ナレーター・モニター不要）。
2. **命名規則（`process-review-gate.mjs` 連動）**: 必ず `"workspace": "inherit"` とし、`name` は **`review-<role>` / `audit-<role>` / `verifier-<role>` / `opus-refuter` 等**（`review|reviewer|audit|auditor|verify|verifier|refute|refuter|inspector|critic|panel` の語を含み、`advisor|monitor|narrator` を含まない）にします。
3. 各ワーカーの `brief` には以下の4点を必ず明記します：
   1. 最初に担当する理念 SSOT ファイル（例: `~/.gemini/config/plugins/multi-agent-kit/agents/<role>.md` や `~/.gemini/config/plugins/claude-orchestrator-kit/agents/carbon-verifier.md`）を `read_file` で読み込み、その重点チェックリストと出力形式を厳守すること。
   2. **Read-Only 厳守**: 製品コード・設定ファイルの編集（`write_file` / `edit_file`）や破壊的コマンド実行は一切禁止（読取・検索・`git diff` 確認のみ可）。
   3. **クリーンルーム独立性**: Phase 1 では他メンバーのボード投稿を参照・追随せず、指定された差分・対象ファイル・不変条件のみから独立所見を導き `@coordinator [done]` で1投稿にまとめて報告すること（追加ヘルパーは起動しない）。
   4. 対象リポジトリ・差分/ファイルパス・不変条件仕様（※作成経緯や言い訳は渡さない）。

### Step 3: Phase 2 — 意見割れ時のみクロス反証 ＆ 事実照合・後始末
1. 起床後、`read_board(since=0)` で全ワーカーの独立所見を取得します。
2. Gemini 枠と Claude 枠などで `CRITICAL` / `MAJOR` の判定が割れた指摘がある場合のみ、該当投稿番号（`#seq`）を指してピンポイントにボード上でクロス反証させます（例: `@opus-refuter #4 の指摘を実コード file:line で反証検証して`）。
3. 最終的にメイン（または `mindfulness-inspector`）が全指摘を実コード・一次仕様と照合して `CONFIRMED / UNVERIFIED / REJECTED` を確定し、役割を終えた監査ワーカーを **`swarm(action="kill", name="...")`** でクリーン終了します。

### 4.1 計画時点の横断設計 Advisor プリセット（`planning-advisor` 連携）
実装着手前（計画確定前）に `Orchestrator (Neon)` 等の主担当から横断設計の助言（境界・SSOT・不可逆地雷・簡潔な代替案）を得たい場合は、スキル **`planning-advisor`** の手順で `agents/planning-advisor.md` を理念 SSOT として起動します:
- **Single Opus High**（日常の横断設計・異種ベンダー視点）: `name: "plan-opus-advisor"`, `model`: 最新 `claude-opus-*-high`
- **Single Carbon High**（大規模コードベースの構造深掘り）: `name: "plan-carbon-advisor"`, `model`: 最新 `gemini-4-carbon-high`
- **Dual High**（大型横断改修・不可逆変更）: `plan-opus-advisor` ＋ `plan-carbon-advisor` を 1 ターン並列 `swarm(action="add")` し、見解対立時のみ `#seq` で 1 回突合したのち `kill`。
- **ゲート境界注意**: `plan-*-advisor` は事前助言専用であり、**実装後の Tier 1+ 出口監査（`review-*` / `audit-*` / `opus-refuter` 等）を代替しません**（`process-review-gate.mjs` も非バイパス）。
