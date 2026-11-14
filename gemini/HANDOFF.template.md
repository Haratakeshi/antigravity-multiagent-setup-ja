# ~/.gemini 設定引き継ぎ履歴 (HANDOFF)

本ファイルは `~/.gemini/GEMINI.md` および `~/.gemini/config/plugins/` 配下のルール・スキル・エージェント構成の変更履歴と判断背景を記録する SSOT です。
（各ルールファイルは 24,000 バイト上限を維持するため、改定の経緯や過去の議論は本ファイルに集約します。）

## 初期導入記録
- `antigravity-multiagent-setup-ja` (v1.4 ハーネス高速チューニング版) より構成を導入:
  - `~/.gemini/GEMINI.md`: Internal-First PAC (§0.1)、前提検証・9ステップ grilling (§0.3)、完了前セルフレビュー (§0.5)、出力規律 (§1)、副作用境界 (§2)
  - `agy-harness`: 実装・運用規律 (§4〜§11) + v2.30+ スマート Lifecycle Hooks（`/tmp` 安全削除許容・`localhost` URL 素通し・外付けSSD高速バイナリ検知） + `second-opinion-lite`
  - `multi-agent-kit`: 監査独立性規律 (§3) + Tier 0〜3 リスク比例複眼レビュー (`multi-agent-opinion`) + `mindfulness` + `visual-verification-loop`
  - `multi-axis-investigation-kit`: 直交多軸探索・並列 Flash 調査規律 + `multi-axis-investigation`
  - `claude-orchestrator-kit`: 損益分岐点＆並列バッチ最適化オーケストレーター (Mode A: Carbon/Argon, Mode B: Flash, Mode C: Claude) + 外部CLI不在時の内蔵サブエージェント自動フォールバック
  - グローバルスキル: `grill-me`, `grilling`, `package-supply-chain`, `antigravity-project-setup`
