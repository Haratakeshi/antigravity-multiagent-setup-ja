---
name: ask-opus
description: 解決困難なバグ、状態遷移や非同期の設計判断、技術選定のトレードオフ、3ストライク時の壁に直面した際に、シニアアーキテクト（軍師）をワンショット召喚して独立したセカンドオピニオン・助言を得るスキル。Claude Code CLI がある環境では Claude Opus を呼び出し、無い環境では Antigravity 内蔵の上位モデル（Opus / Sonnet / Gemini 4 Carbon / Pro）による senior-architect-advisor サブエージェントへ自動フォールバックする。
---

# ask-opus: 軍師（シニアアーキテクト）アドバイザー召喚スキル

日常の開発・並列実装は高速に進めつつ、**「設計の難所」「複雑なトレードオフ」「3連続失敗で壁にぶつかった時」にのみ、上位のシニアアーキテクトをワンショットで召喚**して深い見立てと推奨設計を得るスキルです。

## 2段階の実行ルート（Claude CLI 優先 ＋ Antigravity 内蔵サブエージェント自動代替）

### ルート A: 外部 `claude` CLI（Claude Code）が存在する環境
まず以下のスクリプトを実行します（`PATH` や `~/.local/bin/claude` 等から自動で `claude` バイナリを検出します）：

```bash
python3 ~/.gemini/config/plugins/claude-orchestrator-kit/skills/ask-opus/scripts/ask_opus.py \
  --topic "相談したい課題・設計の悩み・エラーの状況" \
  --repo <作業リポジトリのパス> \
  --out <artifactDir>/opus_advice.md \
  [--target <参照させたいファイルパス> ...] \
  [--context-file <試行錯誤ログや背景説明.md>]
```

- 終了コード `0`: 成功（Claude Opus による助言取得完了、`--out` にレポート出力済み）。レポートを読んで設計判断に活かします。
- 終了コード `2`（`[FALLBACK_TO_NATIVE_SUBAGENT]` が出力された場合 = `claude` CLI 未導入・未認証）: **即座にルート B へ進みます**。

---

### ルート B: `claude` CLI が存在しない環境（Antigravity 内蔵モデル代替）
外部 `claude` CLI が未導入のマシンでは、`invoke_subagent` で内蔵カスタムサブエージェント **`senior-architect-advisor`** を起動します：

- **モデル指定ルール**:
  - 現在のメインモデルが **Antigravity 内蔵の Claude Opus / Sonnet**、または **Gemini 4 Carbon / Argon** の場合: `Model: "inherit"` を指定（内蔵の Opus / Sonnet / Carbon 自身が独立コンテキストの軍師として動作します）。
  - 現在のメインモデルが **Gemini Flash** の場合: `Model: "pro"` を指定（内蔵の上位推論モデルで動作します）。

```json
{
  "TypeName": "senior-architect-advisor",
  "Role": "Senior Architect Second-Opinion Advisor",
  "Model": "inherit",
  "Prompt": "相談テーマ:\n<topic>\n[相談したい論点・設計トレードオフ・3ストライクの状況]\n</topic>\n\n参照対象:\n- リポジトリ: [作業リポジトリパス]\n- 重点ファイル: [対象ファイルパス一覧]\n\n実際のコードを並列バッチで直接確認した上で、迎合のないシニアアーキテクトとしての推奨設計・盲点・代替案比較をあなた自身の artifact ディレクトリ (opus_advice.md, ArtifactMetadata 付き) に write_to_file で書き出し、結論3行要約と保存先絶対パスを send_message で返してください。"
}
```

## 特徴と安全境界
1. **安全な読み取り専用境界**: ルート A（`Read,Grep,Glob` 限定）・ルート B ともにプロダクションコードの書き換えやシェル実行は行いません。
2. **コンテキスト保護**: 詳細な助言はアーティファクトファイルに出力させ、メインスレッドは返却されたパスを必要な箇所だけ参照します（Antigravity v2.30+ ではサブエージェントは自身の `brain/<subagent-id>/` に出力し絶対パスを親へ共有します）。
