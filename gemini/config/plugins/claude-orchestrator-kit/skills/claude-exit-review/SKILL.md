---
name: claude-exit-review
description: "Final-deliverable refutation and visual review gate (clean-room input = git diff + acceptance criteria + screenshots). Uses Antigravity's native `carbon-verifier` subagent (or latest built-in Claude Opus (High) dynamically selected via `swarm`) by default; runs external Claude Fable 5.2 (`exit_review.py`) ONLY on explicit Human instruction with commercial `ANTHROPIC_API_KEY`."
---

# claude-exit-review: クリーンルーム出口反証・視覚レビューゲート

オーケストレーターモードの「出口ゲート」。実装ワーカーやメインエージェントが作った成果物を、作成経緯や言い訳を一切渡さないクリーンルーム条件（§3.4）で独立反証・目視レビューします。

- **既定（標準ルート A — 100% Antigravity 内蔵・規約完全準拠）**: 内蔵サブエージェント **`carbon-verifier`**（または別モデル目線が必要な場合は `swarm(action="models")` で動的選択した **最新内蔵 Claude Opus (High)**）を使用します。
- **外部 API ルート B（Human 明示指示 ＋ 商用 `ANTHROPIC_API_KEY` 限定）**: 人間が明示的に外部 Fable 5.2 API レビューを指示し、かつ商用 `ANTHROPIC_API_KEY`（または Bedrock / Vertex / Foundry）が設定されている場合のみ `exit_review.py --human-authorized` を実行します（Consumer Pro/Max OAuth は規約保護のため機械的にブロックされます）。

---

## 実行ルート A（既定・標準）: Antigravity 内蔵 `carbon-verifier` サブエージェント

通常の Tier 1 出口反証ゲート（および Mode C 内蔵 Claude 利用時）では、外部 CLI を呼ばず直ちに `invoke_subagent` で **`carbon-verifier`** を起動します：
- メインモデルが **Gemini 4 系上位（Neon / Carbon / Argon 等）** または **内蔵 Claude Opus / Sonnet** の場合: `Model: "inherit"`
- メインモデルが **Gemini 高速実行系（Hydrogen / Flash）** の場合: `Model: "pro"`

```json
{
  "TypeName": "carbon-verifier",
  "Role": "Clean-Room Exit Gate Verifier",
  "Model": "inherit",
  "Prompt": "以下の受け入れ基準・Git差分・画像証跡のみを根拠にクリーンルーム反証レビューを実施してください。\n\n- リポジトリ: <repo>\n- 比較基点: <base>\n- 受け入れ基準ファイル: <criteria.md>\n- 目視対象画像: <shot1.png ...>\n\n1件でも CRITICAL があれば VERDICT: FAIL、全基準達成なら VERDICT: PASS を明記して send_message で報告してください。"
}
```

---

## 実行ルート B（Human 明示指示 ＋ 商用 API キー設定時のみ）: 外部 `claude-fable-5-2` API

Human から明示的に外部 Claude Fable 5.2 での出口監査を指示された場合のみ、`--human-authorized` を付けて実行します（内部で `--bare` モードを強制し、Keychain / Consumer OAuth は一切読み取りません）：

```bash
python3 ~/.gemini/config/plugins/claude-orchestrator-kit/skills/claude-exit-review/scripts/exit_review.py \
  --human-authorized \
  --repo <repo> --criteria <criteria.md> --out <artifactDir>/claude_exit_review.md \
  [--base main] [--image shot1.png --image shot2.png]
```

- 終了コード: `0` PASS / `1` FAIL / `2` APIキー未設定・CLI不在・未承認（`[FALLBACK_TO_NATIVE_SUBAGENT]` → 直ちにルート A `carbon-verifier` を実行） / `3` 解決モデル不一致 / `4` 判定不能。完了扱いにできるのは `0` のみ。

## 結果の扱い
1. 指摘ごとに実ファイル（`file:line`）と照合し CONFIRMED / REJECTED / UNVERIFIED を判定する（§3.4）。
2. CONFIRMED は `flash-implementer`（またはメイン）で修正し、再検証する（ラウンド上限は §3.3）。
