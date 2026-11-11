---
name: claude-exit-review
description: Final-deliverable refutation and visual review gate (clean-room input = git diff + acceptance criteria + screenshots). Uses Claude Sonnet via external Claude CLI when installed, or automatically falls back to Antigravity's native `carbon-verifier` subagent (inheriting built-in Claude Sonnet/Opus, Gemini 4 Carbon, or Pro) when external CLI is absent.
---

# claude-exit-review: クリーンルーム出口反証・視覚レビューゲート

オーケストレーターモードの「出口ゲート」。実装ワーカーが作った成果物を、作成経緯や言い訳を一切渡さないクリーンルーム条件で独立に反証・目視レビューします。

## 入力（クリーンルーム §3.4）
- `--repo`: 対象 git リポジトリ
- `--criteria`: 受け入れ基準 / 不変条件（INV-ID）を書いたファイル。作成経緯や言い訳は書かない
- `--base`: 比較基点（既定 `HEAD` = 未コミット差分。コミット済みなら `main` 等）
- `--image`: 目視確認するスクリーンショット（複数指定可）
- `--out`: レポート出力先（会話の artifact ディレクトリ推奨）

## 実行ルート A: 外部 `claude` CLI が存在する環境
```bash
python3 ~/.gemini/config/plugins/claude-orchestrator-kit/skills/claude-exit-review/scripts/exit_review.py \
  --repo <repo> --criteria <criteria.md> --out <artifactDir>/claude_exit_review.md \
  [--base main] [--image shot1.png --image shot2.png]
```
- 終了コード: `0` PASS / `1` FAIL / `2` CLI不在・エラー（`[FALLBACK_TO_NATIVE_SUBAGENT]`）/ `3` 解決モデル不一致 / `4` 判定不能。
- 終了コードが `2`（`claude` CLI 未導入・未認証）だった場合は、**即座にルート B へ自動フォールバック**します。

## 実行ルート B: 外部 `claude` CLI が存在しない環境（Antigravity 内蔵サブエージェント代替）
`invoke_subagent` で **`carbon-verifier`** を起動し、同じクリーンルーム入力（差分＋受け入れ基準＋画像パスのみ、作成経緯なし）を渡して監査します：
- メインモデルが **内蔵 Claude Sonnet / Opus** または **Gemini 4 Carbon / Argon** の場合: `Model: "inherit"`
- メインモデルが **Gemini Flash** の場合: `Model: "pro"`

```json
{
  "TypeName": "carbon-verifier",
  "Role": "Clean-Room Exit Gate Verifier",
  "Model": "inherit",
  "Prompt": "以下の受け入れ基準・Git差分・画像証跡のみを根拠にクリーンルーム反証レビューを実施してください。\n\n- リポジトリ: <repo>\n- 比較基点: <base>\n- 受け入れ基準ファイル: <criteria.md>\n- 目視対象画像: <shot1.png ...>\n\n1件でも CRITICAL があれば VERDICT: FAIL、全基準達成なら VERDICT: PASS を明記して send_message で報告してください。"
}
```

## 結果の扱い
1. 指摘ごとに実ファイル（`file:line`）と照合し CONFIRMED / REJECTED / UNVERIFIED を判定する（§3.4）。
2. CONFIRMED は `flash-implementer`（またはメイン）で修正し、再検証する（ラウンド上限は §3.3）。
