---
name: ask-opus
description: 解決困難なバグ、状態遷移や非同期の設計判断、技術選定のトレードオフ、3ストライク時の壁に直面した際に、Antigravity 内蔵の最新 Claude Opus (High) を `swarm(action="models")` で動的選択し独立クリーンコンテキストで召喚して、別ベンダー目線のセカンドオピニオン・設計助言を得る標準軍師スキル（外部 CLI 不使用・規約100%準拠）。
---

# ask-opus: 標準軍師・セカンドオピニオン召喚スキル（Antigravity 内蔵 最新 Claude Opus High）

「設計の難所」「状態遷移・非同期排他のトレードオフ」「3連続失敗（3ストライク）で視野狭窄に陥った時」に、**Antigravity 内蔵の最新 Claude Opus (High)（`swarm(action="models")` で動的選択、例: `claude-opus-5-5-high`）** を独立コンテキストで召喚し、Gemini 系オーケストレーターとは異なるモデル目線から真因分析と推奨設計を得る**標準（デフォルト）スキル**です。

- **100% Antigravity 内蔵ファーストパーティ実行**: 外部 `claude` CLI や個人向けサブスクリプション OAuth を一切使用しないため、Anthropic / Google 双方の利用規約に完全準拠し、追加の従量 API 課金も発生しません。
- 外部 API 経由の **Claude Fable 5.2 (`ask-fable`)** は、**人間（Human）がチャットで明示的に `ask-fable`（外部 Fable 5.2 API 利用）を指示した重要局面のみ** 使用し、通常の自律判断・3ストライク時は必ず本スキル（`ask-opus`）を使用します。
- **用途の使い分け**: **実装着手前の計画・複数モジュール横断の設計助言**には **`planning-advisor`**（Single Opus High / Carbon High / Dual High プリセット）、**実装途中の詰まり・3ストライク・単発トレードオフ**には本スキル（`ask-opus`）を使用してください。

---

## 実行ルート 1: メインが Gemini 系（Neon / Carbon / Hydrogen / Argon / Flash 等）の場合（Mode A / B — 標準）

`invoke_subagent` の `Model` 引数（`inherit / flash_lite / flash / pro`）では別ベンダーモデルを指名できないため、モデル固定に対応した Antigravity 標準の **`swarm` 単一ワーカー起動** を使用して最新内蔵 Claude Opus (High) をピン留め召喚します。

### 手順
1. **最新内蔵 Opus とクォータの動的確認（ハードコード禁止）**:
   `swarm(action="models", all=True)` を呼び出し、一覧にある**最新の `claude-opus-*-high`**（例: `claude-opus-5-5-high`。新バージョン登場時は最新世代を自動選択）に `quota_remaining` があるか確認します。
   - もし最新 `claude-opus-*-high` がクォータ上限（`0%`）の場合は、フォールバック順（最新の `claude-opus-*-medium` → 最新の `claude-sonnet-*-high`）へ切り替え、その旨をユーザーに1行伝えます。
2. **単一クリーンルーム軍師ワーカーの起動（ナレーター・モニター不要）**:
   以下の形式で1体のみ `swarm(action="add", ...)` を呼び出し（`model` には手順1で動的選択した ID を設定）、**そのターンは他のボードツールを呼ばずに終了**して返信を待ちます（ボードツールは初回 `add` の次ターンから有効化され、`@coordinator` メンションで自動起床します）：
   ```json
   {
     "action": "add",
     "name": "opus-advisor",
     "model": "<手順1で動的選択した最新 claude-opus-*-high>",
     "workspace": "inherit",
     "brief": "あなたは独立したシニアソフトウェアアーキテクト（軍師）です。製品コードや設定ファイルの編集・破壊的コマンド実行は一切禁止（read_file / grep / list_dir 等の読取検証のみ可）です。\n\n【相談トピック / 壁の内容】\n<解決したい課題・設計のトレードオフ・3ストライクの事実>\n\n【参照対象ファイル（実コードを確認すること）】\n<対象ファイルの絶対パス一覧>\n\n迎合や推測を排して実ファイルを精読し、以下を構造化して `@coordinator` 宛にボードへ1投稿で回答してください（ナレーター・モニター・追加ヘルパーは起動しないこと）：\n1. 【結論 / 推奨アプローチ】（最もシンプルかつ堅牢な解法）\n2. 【現状の盲点・落とし穴】（認知的バイアス・境界条件・非同期リスク）\n3. 【具体的設計 / 実装指針】（データ構造・状態遷移・API契約・file:line根拠）\n4. 【代替案とのトレードオフ比較】"
   }
   ```
   （※同一会話内で過去に `opus-advisor` を `kill` 済みの場合は `opus-advisor-2`, `opus-advisor-3` のように連番にします。`waiting` 状態で残っている場合は新規 `add` せず `post_message` で `@opus-advisor` に追加相談できます。）
3. **助言の受領・事実照合と後始末**:
   - 起床後、`read_board(since=0)` で `opus-advisor` の回答を読み取り、指摘された `file:line` を実コードと照合（§3.4）して設計・実装へ反映します。
   - 相談トピックが完了したら `swarm(action="kill", name="opus-advisor")` でクリーンに終了します（直後に追加質問が見込まれる場合のみ `waiting` で待機可＝待機中トークン消費ゼロ）。

---

## 実行ルート 2: メインが既に Antigravity 内蔵 Claude Opus 系の場合（Mode C）、または `swarm` 非対応コンテキストの場合

メイン自身が内蔵 Claude Opus 等で動いている場合、または `swarm` ツールを持たないサブエージェント内から呼ぶ場合は、`invoke_subagent` で **`senior-architect-advisor`**（`Model: "inherit"`、親が Flash 時は `Model: "pro"`）を起動します：

```json
{
  "TypeName": "senior-architect-advisor",
  "Role": "Opus Senior Architect Advisor",
  "Model": "inherit",
  "Prompt": "以下の設計課題・トレードオフについて、対象ファイルを並列精読したうえで独立したセカンドオピニオンを提示してください。\n\n- 相談トピック: <課題>\n- 対象パス: <絶対パス一覧>\n\n結論・盲点・推奨設計（file:line根拠つき）・代替案比較をまとめ、send_message で報告してください。"
}
```
