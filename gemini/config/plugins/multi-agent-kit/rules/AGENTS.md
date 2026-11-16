# Multi-Agent Subagent Orchestration & Audit Rules (§3)

詳細手順は `skills/multi-agent-opinion/SKILL.md` を SSOT とする。サブエージェント生ログは親コンテキストへ展開せずアーティファクトへ出力する。

## 3. Audit Independence

- **3.1 内蔵独立監査の標準化と外部 API 利用規約ガード**: 通常運用では Antigravity 内蔵サブエージェント（`carbon-verifier` / `mindfulness-inspector` / `second-opinion-lite` / `ask-opus` 内蔵 `claude-opus-5-5-high`）で独立検証を完遂する。外部 Claude CLI スクリプト（`exit_review.py` / `sonnet_inspect.py` / `ask_fable.py`）は **Human が明示指示（`--human-authorized`）し、かつ商用 `ANTHROPIC_API_KEY`（または Bedrock/Vertex/Foundry）が設定されている場合のみ `--bare` モードで実行**し、個人サブスクリプション OAuth（`claude.ai`）環境や未導入時は `status: external_review_unavailable` で遮断して内蔵サブエージェントへ自動フォールバックする。
- **3.2 検査対象の忠実評価**: 指定対象ファイルの実内容を基準に判定し、fixture・類似ファイルは補助資料として区別する。
- **3.3 ラウンド上限と併用**: 自動 second-opinion は通常最大2ラウンド、重要変更（認証・決済・データ破壊・状態遷移・非同期排他）は最大3ラウンド（3-Strike PDCA）。上限到達後も未収束なら `pending_human_review` で人間へ引き渡す。併用時の重複排除・採否判断は親が担う。
- **3.4 クリーンルーム規律**:
  - **入力隔離**: レビュアーには「機密マスク済み Git 差分/対象コード」と「不変条件仕様（Invariants Spec / docs / API契約）」のみを渡し、作成側の思考経緯・言い訳を含めない。
  - **ゼロバイアス照合**: 指摘は実コード（`file:line`）・一次仕様と照合し、`CONFIRMED / REJECTED / UNVERIFIED` を判定・記録する。
- **3.5 リスク・速度比例の4段階レビュー（Tiered Review Scaling & Exit Gate）**:
  - **Tier 0（軽微変更のみ即時完了）**: ドキュメント・設定値・文言/CSS微調整・テスト/型定義のみ、または**単一コードファイルかつ追加・変更 `25行未満` の自明な微修正（かつ `flash-implementer` 等の実装委譲なし）**に限り、メイン自身の差分・テスト確認（§0.5 セルフレビュー）のみで即時完了とする（自動サブエージェント起動なし）。
  - **Tier 1（通常実装・委譲後の必須出口ゲート / 明示依頼時）**: ①**`flash-implementer` 等に実装を委譲した場合**、または ②**プロダクトコードのロジック変更（コード変更 `2ファイル以上` または 追加・変更 `25行以上`）**を行った場合は、完了報告前に必ずクリーンルーム反証レビューを実行する。通常ロジックは高速な `second-opinion-lite`（Flash並列読取・1〜2分）、複雑な状態遷移・認証・決済・不変条件・視覚判定・リリース直前は `carbon-verifier`（`Model: "inherit"`, バッチ監査・12往復上限）を使い分け、親には数行の要約のみ返す（監査後の軽微手直し［単一ファイル25行未満・空 `catch` なし］は再監査不要）。
  - **Tier 2（重要ロジック変更・標準 `multi-agent-opinion`）**: 変更領域に直結する Stage 1 専門レビュアー **3〜6体を選抜並行起動**（無関係領域はスキップ）＋ Stage 2 再検査・複合監査を実行。
  - **Tier 3（リリース前・明示的な全15体監査依頼・§0.2 3ストライク Mode B）**: Stage 1 全15体 ＋ Stage 2 全3体のフルパイプラインを起動。
  - 静的チェック（`process-review-gate` 等）は `// fallback:ok` 等の意図的注釈を許容する。
- **3.6 視覚的検証の独立監査**: 画面・UI変更時はスクリーンショットを撮影し `visual-verification-loop` スキルに従い客観検証（Mode A の最終目視合否は Carbon 自身または `carbon-verifier`）。1項目でも崩れ・欠損・不整合があれば `CRITICAL` として最大3ラウンド修正・再撮影する。
