# ~/.gemini 設定引き継ぎ履歴 (HANDOFF)

本ファイルは `~/.gemini/GEMINI.md` および `~/.gemini/config/plugins/` 配下のルール・スキル・エージェント構成の変更履歴と判断背景を記録する SSOT です。
（各ルールファイルは 24,000 バイト上限を維持するため、改定の経緯や過去の議論は本ファイルに集約します。）

## 初期導入記録
- `antigravity-multiagent-setup-ja` より初期構成を導入:
  - `~/.gemini/GEMINI.md`: 推論プロトコル (§0)、出力規律 (§1)、副作用境界 (§2)
  - `agy-harness`: 実装・運用規律 (§4〜§11) + Lifecycle Hooks + `second-opinion-lite`
  - `multi-agent-kit`: 監査独立性規律 (§3) + 15+3体複眼レビュー (`multi-agent-opinion`) + `mindfulness` + `visual-verification-loop`
  - `multi-axis-investigation-kit`: 直交多軸探索規律 + `multi-axis-investigation`
  - `claude-orchestrator-kit`: モデル適応オーケストレーター (Mode A: Carbon/Argon, Mode B: Flash, Mode C: Claude) + 外部CLI不在時の内蔵モデル自動フォールバック
  - グローバルスキル: `grill-me`, `grilling`, `package-supply-chain`, `antigravity-project-setup`
