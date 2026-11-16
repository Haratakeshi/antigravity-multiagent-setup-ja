---
name: ask-fable
description: 【Human明示指示＋商用APIキー限定】人間がチャットで明示的に「ask-fable（外部 Claude Fable 5.2 API）」の使用を指示した最重要タスクに限り、`ANTHROPIC_API_KEY`（商用従量課金・`--bare` モード）経由で `claude-fable-5-2` に読取専用相談を行うスキル。通常運用・3ストライク時は自動実行せず必ず標準の `ask-opus`（Antigravity内蔵 Opus 5.5 High）を使用すること。
---

# ask-fable: 外部 Claude Fable 5.2 API 召喚スキル（Human明示指示 ＆ 商用APIキー厳格限定）

> [!IMPORTANT]
> **利用規約（ToS）および課金保護のための絶対ルール**
> 1. **エージェントの自律判断による自動起動は厳禁**: 3ストライク時や通常の設計相談では本スキルを呼ばず、必ず標準の **`ask-opus`（Antigravity 内蔵 `Claude Opus 5.5 (High)`）** を使用してください。本スキルは **人間（Human）がプロンプトで明示的に `ask-fable` / 外部 Fable 5.2 の使用を指示したときのみ** 実行できます。
> 2. **個人サブスクリプション OAuth（`claude.ai` Free/Pro/Max/Ultra）の完全遮断**: Anthropic Consumer Terms of Service（外部エージェントからの自動化アクセス禁止）を遵守するため、`ask_fable.py` は **商用の `ANTHROPIC_API_KEY`**（環境変数または `~/.gemini/.env` 内の設定）または商用クラウド認証（Bedrock / Vertex AI / Foundry）が存在しない限り **機械的にブロック（`exit 2`）** します。また実行時は公式 `--bare` モードを強制し、Keychain や `claude.ai` OAuth トークンを一切読み取りません。
> 3. **データ学習設定の確認**: 外部 Claude API の出力を Antigravity に取り込む際は、競合モデル学習禁止条項への配慮として Antigravity 側のデータ学習利用オプトアウト設定を推奨します。

---

## 1. 事前設定（初回のみ・Human 作業）

重要な局面で `ask-fable` を使いたい場合は、Anthropic Console で発行した商用 API キーを `~/.gemini/.env`（パーミッション `600`）に保存しておきます（チャット画面やコマンド引数にキー平文を書く必要はありません）：

```bash
# ~/.gemini/.env の記載例（通常時はエージェントが勝手に呼ぶことはありません）
ANTHROPIC_API_KEY=<YOUR_ANTHROPIC_API_KEY>
```

確認コマンド（キー値は出力されません）：
```bash
python3 ~/.gemini/config/plugins/claude-orchestrator-kit/skills/ask-fable/scripts/ask_fable.py --check
```

---

## 2. 実行手順（Human から明示指示を受けた場合のみ）

Human から明示的に `ask-fable` の実行指示を受けた場合のみ、`--human-authorized` フラグを付与して実行します：

```bash
python3 ~/.gemini/config/plugins/claude-orchestrator-kit/skills/ask-fable/scripts/ask_fable.py \
  --human-authorized \
  --topic "相談したい課題・設計の悩み・トレードオフ" \
  --repo <作業リポジトリのパス> \
  --out <artifactDir>/fable_advice.md \
  [--target <参照させたいファイルパス> ...] \
  [--context-file <試行錯誤ログや背景説明.md>]
```

- **終了コード**:
  - `0`: 助言取得成功（`resolved_models` に `fable-5-2` を確認済み）
  - `2`: 未設定・ブロック（`ANTHROPIC_API_KEY` 未設定による Consumer OAuth 遮断、`claude` CLI 不在、または `--human-authorized` 欠落）
  - `3`: 解決モデル不一致

---

## 3. 終了コード `2` 時のフォールバック（`ask-opus` への切替）

`ANTHROPIC_API_KEY` が未設定等で終了コード `2`（`[FALLBACK_TO_NATIVE_SUBAGENT]`）が返った場合は、Consumer OAuth で無理に実行しようとせず、ユーザーに「商用 `ANTHROPIC_API_KEY` 未設定のため外部 API 呼び出しはスキップし、Antigravity 内蔵 `Claude Opus 5.5 (High)`（`ask-opus`）で代替する」旨を伝えて **`ask-opus` スキル** を実行してください。
