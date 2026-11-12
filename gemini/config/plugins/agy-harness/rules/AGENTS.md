# agy-harness Rules (実装・運用規律 §4〜§11)

`~/.gemini/GEMINI.md`(§0〜§2)と常時展開されるプラグインルール（節番号共通）。機械的強制は Hooks が担い、改定履歴は `~/.gemini/HANDOFF.md` に集約。

## 4. Implementation Discipline
- **4.1 完全実装**: モック・仮実装・マジックナンバー直書きを避け、設定・定数を分離した完全動作実装を行う。
- **4.2 根本原因修正とテスト検証**:
  - テスト失敗時はアプリコードを修正（`skip`/`only`/条件分岐回避は禁止、設計問題として報告）。
  - 期待値は仕様から導出（実装出力コピー禁止）。過剰モックを排し、正常・境界値・異常・状態遷移を決定論的（乱数・時刻・外部API注入）に検証し、再現と修正後の証拠（行番号・ログ・exit code）を示す。
- **4.3 計画ファースト（条件付き）**: 複数ファイル変更・大規模改修・破壊的操作・仕様曖昧時は `implementation_plan.md` を作成。要件すり合わせや `/grill-me`・`/grilling` は `§0.3`（スキル `grill-me` 9ステップ）に従う。軽微修正・調査は簡潔な手順提示で進行。
- **4.4 不要コード整理**: 自身の変更で不要化した import・変数・関数は整理し、既存の未使用コードは状態維持のうえ報告。
- **4.5 影響範囲の考慮**: 変更時は「Aを変更→B,Cに影響」を調査・報告。削除・上書き前に対象を読み、一時退避はリネーム・移動を選択。
- **4.6 Grounding と最新性**: 高速変化領域（API・モデル名・CLI・価格・規約）は公式文書/検索で確認し出典を付記。最初の情報は「候補」とし独立ソースで裏取り。deprecated/未参照コードは Legacy 兆候として報告。
- **4.7 信頼境界と公式ソース優先**: 外部ツール・スキルは公式リポジトリ/一次ソースで安全性と差分を検証。
- **4.8 大量生成・バイナリ運用の分離**: 中間データ・圧縮バイナリ等の大量生成は `/tmp/` や `.gitignore` 済み作業領域へ出力して未追跡(`??`)放置を避け、リポジトリ収録分のみ本番パスへ配置し `git add`（必要に応じ `.gitattributes` `binary` 指定）する。

## 5. Context Decay 対策
- 独立タスクは適切に分割し成果物・検証結果・未解決事項を引き渡す。委譲閾値は `claude-orchestrator-kit` の損益分岐点に従い、明確な役割・成果物・検証条件を付与。

## 6. Context Hierarchy
- **第1階層**: `GEMINI.md`(§0〜§2) + plugin `rules/AGENTS.md`(§3〜§11)。 **第2階層**: `<project>/GEMINI.md` or `AGENTS.md`。 **第3階層**: `.agents/rules/*.md`（`trigger` 必須）。
- ルールは1本上限 `24,000` バイト（編集後 `wc -c` 確認）。変動詳細は各 `SKILL.md` を SSOT とし複製しない。

## 7. 拡張機構の構成
- **Skill** (`~/.gemini/config/skills/` / `plugins/<p>/skills/`): description に用途と発火条件を簡潔記載。
- **Custom Command** (`~/.gemini/commands/*.toml`) / **Subagent** (`~/.gemini/config/agents/` / `plugins/<p>/agents/`): ルールと詳細仕様を分離し二重管理を回避。

## 8. パッケージサプライチェーン対策
- JS/TS は `pnpm`、Python は `uv` を優先使用。`npm`/`npx`/`pip` 直実行と `@latest` は禁止。
- 固定バージョン＋lockfile (`pnpm-lock.yaml` / `uv.lock`) で管理。既存の `package-lock.json` / `requirements.txt` 運用は尊重し、移行は合意後に実施。作業前に lockfile・`packageManager`・CI を確認。

## 10. 設定ファイルの配置と SSOT
- Antigravity 設定は `~/.gemini/config/` と `GEMINI.md` に集約し他ツール設定と分離。

## 11. 機械的ガードレール (agy-harness Hooks)
- `command-guard` / `test-integrity-guard` / `trust-boundary-guard` / `session-grounding` / `completion-gate`。
- deny 時は代替手順を採用、ask/force_ask 時は対象・影響・復元手順を提示し Human Gate へ、完了ゲート発火時は `§0.5` を満たして終了。
