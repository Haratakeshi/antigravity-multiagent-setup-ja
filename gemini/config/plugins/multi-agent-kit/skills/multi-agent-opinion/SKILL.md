---
name: multi-agent-opinion
description: Risk-scaled multi-agent clean-room review pipeline (Tier 0-3, 3-strike PDCA).
---

# Multi-Agent Second Opinion Review Skill（リスク比例・複眼型セカンドオピニオン）

コード実装・設計提案・3ストライク課題に対し、変更リスクと速度要件に応じた **Tier 0〜3 の4段階スケーリング** で独立サブエージェント監査とマインドフルネス事実再検査を実行する標準パイプラインです。各専門エージェントのプロンプト正本（SSOT）は `~/.gemini/config/plugins/multi-agent-kit/agents/*.md` に格納されており、本ファイルには重複記載しません。

---

## 0. リスク・速度比例スケーリング（Tier 0〜3）と設定値

| Tier | 適用場面 | 起動構成 | トークン・時間負荷 |
|---|---|---|---|
| **Tier 0（日常既定）** | 軽微〜通常の実装・バグ修正・設定調整 | メインエージェント自身の差分確認＋テスト実行＋`§0.5` セルフレビューのみで即時完了（監査サブエージェント自動起動なし） | ゼロ |
| **Tier 1（中規模・明示依頼）** | 中規模以上のロジック変更・明示的なセカンドオピニオン依頼 | 通常は高速な `second-opinion-lite`（Flash並列読取・1〜2分）、重要状態遷移・視覚判定・リリース直前は `carbon-verifier`（`Model: "inherit"`, 12往復上限） | 極小 |
| **Tier 2（標準 `multi-agent-opinion`）** | 共有状態・認証・API契約・状態遷移・複数モジュール変更 | Stage 1 **ドメイン選抜 3〜6体**（`Model: "flash"`）＋ Stage 2（再検査＋関連監査1〜2体） | 中 |
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
| **C: 設計ドラフト反証** | 実装前の設計・UX・構成計画 | 提案ドラフト ＋ 解決すべき症状 ＋ 現行コード/SSOT仕様パス | `[CATEGORY] [VERDICT: REFUTED/SURVIVED/GAP] [file:line](url) - 反証と最小代替案` |

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

1. **マインドフルネス事実再検査（第1手段: Claude Sonnet CLI / フォールバック: 内蔵サブエージェント）**:
   ```bash
   python3 ~/.gemini/config/plugins/multi-agent-kit/skills/multi-agent-opinion/scripts/sonnet_inspect.py \
     --findings <ステージ1指摘.md> --target <対象パス> [--target ...] \
     --repo <作業ディレクトリ> --out <artifactDir>/sonnet_inspection.md
   ```
   - 終了コード `0` なら `--out` から `[CONFIRMED / UNVERIFIED / REJECTED]` を採用。
   - 終了コード `2`（`external_review_unavailable` / CLI不在・認証切れ・レート制限）の場合は、直ちに内蔵サブエージェント **`mindfulness-inspector`（`Model: "inherit"`、メインが Flash 時は `"pro"`）** を `invoke_subagent` で起動して同じ3分類再検査を完遂します。
2. **複合リスク・アーキテクチャ整合性監査（`Model: "inherit"`）**:
   - Tier 2 では変更内容に関連する `cross-domain-combinatorial-auditor`（複合状態・競合）および/または `system-architecture-coherence-auditor`（レイヤ境界・DRY）の1〜2体、Tier 3 では両方を並行起動します。

### ステップ 4: 統合レポート出力とデッドロック防止クリーンアップ（v2.30+ 準拠）
1. 実行主体自身の artifact ディレクトリ（`<appDataDir>/brain/<実行中のconversation-id>/second_opinion_report.md`, `ArtifactMetadata` 付き）へ `write_to_file` で出力します（※Antigravity v2.30+ ではサブエージェントから親の `brain/` ディレクトリへの書き込みはエラーになるため、サブエージェント自身の `brain/<subagent-id>/` に保存して絶対パスを親へ共有します）。
   - 構成: `1. Executive Summary` / `2. CONFIRMED Findings` / `3. Combinatorial & Architecture Risks` / `4. UNVERIFIED` / `5. REJECTED (反証根拠つき)` / `6. Action Queue`。
2. **最優先通知**: クリーンアップコマンドより前に、必ず `send_message` で親へ5行以内の要約とレポート絶対パスを送信します。
3. **安全な後始末**: `/tmp/<具体名>` 以外のパスに対する `rm` は `guard_commands.py` の `force_ask` によりバックグラウンドで停止するため、`brain/.../scratch/` 配下の一時ファイル破棄は空文字上書き（`Overwrite: true`）を使用し（`/tmp/<具体名>` 配下のみ `rm -rf /tmp/<具体名>` が自動許可されます）、`manage_subagents(Action="kill_all")` で子エージェントを終了します。
