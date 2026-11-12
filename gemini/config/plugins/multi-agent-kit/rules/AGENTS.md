# Multi-Agent Subagent Orchestration & Audit Rules (§3)

詳細手順は `skills/multi-agent-opinion/SKILL.md` を SSOT とする。サブエージェント生ログは親コンテキストへ展開せずアーティファクトへ出力する。

## 3. Audit Independence

- **3.1 外部レビューと自動フォールバック**: 外部CLIレビュー（Claude CLI 等）は導入済み環境または明示指定時に実行。未導入・認証不可時は `status: external_review_unavailable`（`external_cli_review_verdict: unverified`）を記録し、内蔵サブエージェント（`carbon-verifier` / `mindfulness-inspector` / `second-opinion-lite`）へ自動フォールバックして独立検証を完遂する。
- **3.2 検査対象の忠実評価**: 指定対象ファイルの実内容を基準に判定し、fixture・類似ファイルは補助資料として区別する。
- **3.3 ラウンド上限と併用**: 自動 second-opinion は通常最大2ラウンド、重要変更（認証・決済・データ破壊・状態遷移・非同期排他）は最大3ラウンド（3-Strike PDCA）。上限到達後も未収束なら `pending_human_review` で人間へ引き渡す。併用時の重複排除・採否判断は親が担う。
- **3.4 クリーンルーム規律**:
  - **入力隔離**: レビュアーには「機密マスク済み Git 差分/対象コード」と「不変条件仕様（Invariants Spec / docs / API契約）」のみを渡し、作成側の思考経緯・言い訳を含めない。
  - **ゼロバイアス照合**: 指摘は実コード（`file:line`）・一次仕様と照合し、`CONFIRMED / REJECTED / UNVERIFIED` を判定・記録する。
- **3.5 リスク・速度比例の3段階レビュー（Tiered Review Scaling）**:
  - **Tier 1（日常・小〜中規模変更の既定）**: 通常変更は高速な `second-opinion-lite`（Flash並列読取・1〜2分）、複雑な状態遷移・不変条件・視覚判定・リリース直前は `carbon-verifier`（`Model: "inherit"`, バッチ監査・12往復上限）を使い分け、親には数行の要約のみ返す。
  - **Tier 2（重要ロジック変更・標準 `multi-agent-opinion`）**: 変更領域に直結する Stage 1 専門レビュアー **3〜6体を選抜並行起動**（無関係領域はスキップ）＋ Stage 2 再検査・複合監査を実行。
  - **Tier 3（リリース前・明示的な全15体監査依頼・§0.2 3ストライク Mode B）**: Stage 1 全15体 ＋ Stage 2 全3体のフルパイプラインを起動。
  - 静的チェックは `// fallback:ok` 等の意図的注釈を許容する。
- **3.6 視覚的検証の独立監査**: 画面・UI変更時はスクリーンショットを撮影し `visual-verification-loop` スキルに従い客観検証（Mode A の最終目視合否は Carbon 自身または `carbon-verifier`）。1項目でも崩れ・欠損・不整合があれば `CRITICAL` として最大3ラウンド修正・再撮影する。
