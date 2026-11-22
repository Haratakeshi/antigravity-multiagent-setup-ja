# カスタマイズ・環境別運用ガイド (Customization & Compliance Guide)

他の PC や異なる開発環境へ導入した際のカスタマイズ方法と、標準の内蔵 Opus 運用および外部 API 利用時の規約保護設計をまとめています。

---

## 1. 標準の内蔵 Claude Opus 動的運用 (`ask-opus` / 2レーン監査) と、重要局面限定の外部 API (`ask-fable`)

本セットアップは **外部 CLI や API キーが一切なくても、Antigravity 内蔵モデルだけで 100% 動作** し、かつ Anthropic / Google 双方の利用規約（ToS）を厳格に遵守する2段構成を採っています。

### ① 日常・標準運用: `planning-advisor` / `ask-opus` ＆ 2レーン複眼監査（100% Antigravity 内蔵・モデル世代自動追従）
- メインモデルが `Gemini 4 Neon` / `Carbon` 等の場合でも、計画着手前の **`planning-advisor`** スキル（Single Opus High / Carbon High / Dual High 並列）および実装中の **`ask-opus`** スキルは毎回 **`swarm(action="models", all=True)` からその時点の最新内蔵 `Claude Opus (High)` / `Gemini 4 Carbon (High)` を動的選択** して独立クリーンコンテキスト召喚し、別ベンダー目線・長考視点の助言を取得します（メインが既に内蔵 Opus の Mode C では `planning-advisor` / `senior-architect-advisor` を起動）。
- 出口反証ゲート（Tier 1）は内蔵 `second-opinion-lite` / `carbon-verifier`、日常 Tier 2 と Stage 2 事実再検査は `invoke_subagent`（**Lane 1**: 最新版 Antigravity で `flash` → `Hydrogen High` へ自動追従）が標準で動作します。
- 節目の大型設計や異種ベンダー（Gemini × Claude）複眼レビューでは `multi-agent-opinion` §4 の **Swarm 動的モデル解決パネル（Lane 2）** を起動でき、固定世代 ID なしで常に最新 Fast / Frontier / Claude モデルへ理念（`agents/*.md` SSOT）を渡せます。

### ② 重要タスク限定の外部 API 呼び出し: `ask-fable`（Human 明示指示 ＋ 商用 API キー必須）
Anthropic の個人向け利用規約（Consumer Terms of Service）では、個人定額サブスクリプション（`claude.ai` Free/Pro/Max/Ultra の OAuth セッション）へ他社エージェントやスクリプトから自動アクセスすることが禁止されています。そのため `ask_fable.py`、`exit_review.py`、`sonnet_inspect.py` には以下の **二重のハードウェア級ロック** が組み込まれています：

1. **Human 明示承認ロック（`--human-authorized` 必須）**:
   エージェントが自律判断で勝手に外部有料 API を叩くことはできません。人間がプロンプトで明示的に `ask-fable`（外部 Fable 5.2 API 利用）を指示したときのみ実行されます。
2. **商用 API 認証ロック ＆ 公式 `--bare` モード強制**:
   環境変数 `ANTHROPIC_API_KEY` または `~/.gemini/.env`（パーミッション `600` 推奨）内の `ANTHROPIC_API_KEY=<YOUR_ANTHROPIC_API_KEY>`（もしくは Bedrock / Vertex AI / Foundry 商用認証）が設定されていない場合、スクリプトは `claude` バイナリを起動する前に即座に `exit code 2`（`[BLOCKED_CONSUMER_OAUTH]`）で停止し、内蔵の `ask-opus` / 内蔵サブエージェントへフォールバックします。実行時も公式 `--bare` フラグを強制するため、Keychain や `claude.ai` OAuth トークンは一切読み取られません。

---

## 2. Human Gate 第2フェーズ自動通過（Gemini Decision 1）の設定

`agy-harness`（v1.8+）では、ワークスペース外・保護対象・`git branch -D` などの要注意操作を「確認ダイアログで即停止」させず、まず非ブロッキング `deny`（`[DEFER_HUMAN_GATE]`）でスキップして残りのゴール作業を完遂し、最終報告の **「Human Gate（手動確認・実行依頼）」**（`pending_human_review`）へ集約提示します。

### ① `GEMINI_API_KEY` 未設定時 vs 設定時の挙動

- **未設定時（そのままでも利用可能）**:
  - ユーザーが次ターンで承認・実行依頼した場合、Phase 2 フォールバックとして **`force_ask`**（Antigravity 標準の確認ボタンを1回表示）へ切り替わり、安全に実行できます（自動スキップのまま完全 `deny` にしたい場合は環境変数 `AGY_HUMAN_GATE_FALLBACK=deny` を設定します）。
- **設定時（チャット承認だけで自動通過 — 推奨）**:
  - Google AI Studio 等で取得した `GEMINI_API_KEY` を `~/.gemini/.env`（パーミッション `600` 推奨）に設定すると、`PreToolUse` フックが **`gemini-decision-1`**（Gemini Decisions API: `POST https://generativelanguage.googleapis.com/v1beta/decisions`）で前ターンの Human Gate 提案・最新の `USER_EXPLICIT` 返信・実行予定コマンドの3点を照合し、**4問の `noul` 判定（承認・スコープ一致・難読化なし・非壊滅的）がすべて高確信で Yes** の場合のみ自動で `allow` します。

```bash
# ~/.gemini/.env に追記する例（絶対にリポジトリへコミットしないこと）
GEMINI_API_KEY="YOUR_GEMINI_API_KEY"
```

### ② コスト・レイテンシ・セキュリティ設計

- **平時コスト・オーバーヘッドゼロ**: 通常の許可コマンド（`allow`）や禁止コマンド（`DENY`）では `_human_gate_decision` や `urllib` を遅延インポートで一切読み込まず、API も呼びません。前ターンに `pending_human_review` がある承認返信時のみ発火します。
- **低コスト・高速**: `gemini-decision-1` はテキスト生成を行わず確率のみを返すため **出力トークン 0**（入力単価も低コスト）、実測 **約 300〜500 ms** で判定が完了します。
- **バイパス不能境界**: `curl | sh` / `npm install` / `pip install` / `--no-verify` / `crontab` 等の Hard `DENY`、およびルート `/`・ホーム `~`・システム領域・`~/.ssh` 等や `dd of=/dev/*`・`mkfs*`・`diskutil erase*/apfs delete*` 等の壊滅的レッドラインは Decision 1 を呼ばず常に `deny` を維持します。
- **監査ログ**: 第2フェーズの判定結果（`decision`, `latency_ms`, 各問スコア。API キーは記録対象外）は `~/.local/state/agy-harness/human_gate_decisions.jsonl` に記録されます。

---

## 3. ブラウザ操作環境（`cdp-relay` vs 内蔵 `browser` サブエージェント）

- Chrome / Edge 144 以降では、MCP の `chrome_devtools` / `edge_devtools` 直結を使うとブラウザ側にモーダル承認ダイアログが出て無人実行がデッドロックするため、`GEMINI.md §2.3` で使用を禁止しています。
- 標準では **Antigravity 内蔵の `browser` サブエージェント**（または Playwright 等のヘッドレス E2E）がそのまま使われます。
- もし普段使いのログイン済み Chrome プロファイルを直接操作する拡張機能中継 MCP（`cdp-relay` 等）を別途導入している環境では、自動的にそちらを優先利用できます。

---

## 4. ルールファイルを自分好みに編集する際の鉄則（24,000バイト制限）

Antigravity のルールファイル（`GEMINI.md` および各 `AGENTS.md`）には **1ファイルあたり 24,000 バイトの上限** があり、超過分は警告なしで末尾から切り捨てられます。

ルールを追記・カスタマイズした後は、必ず以下のいずれかでサイズを確認してください：

```bash
# 単体確認
wc -c ~/.gemini/GEMINI.md

# リポジトリ側で編集した場合の一括検証
python3 scripts/verify.py
```

- 特定ライブラリやフレームワークの詳細手順は `GEMINI.md` に書かず、`~/.gemini/config/skills/<skill-name>/SKILL.md` として切り出す（Progressive Disclosure）のがベストプラクティスです。

---

## 5. `agy-harness` の Hook を一部調整・無効化したい場合

- **特定ドメインを信頼境界（URL アクセス許可リスト）に追加したい場合**:
  自社や個人プロジェクト固有のドメインは `~/.gemini/config/allowed_domains.local.txt` を新規作成して1行1ドメインで記載するのが推奨です（サブドメインも自動許可され、`install.py` の再実行でも上書きされません。共通リスト `~/.gemini/config/plugins/agy-harness/scripts/allowed_domains.txt` への追記も可能です）。
- **完了マーカー引き止めだけを一時スキップしたい場合**:
  未追跡バイナリストーム検知を維持したまま `GOAL_COMPLETE` マーカー確認のみをスキップしたい場合は、環境変数 `AGY_SKIP_STOP_MARKER_GATE=1` を設定できます。
- **特定の Hook だけを一時的に OFF にしたい場合**:
  `~/.gemini/config/plugins/agy-harness/hooks.json`（または `~/.gemini/config/hooks.json`）内の該当フックオブジェクト（例: `"completion-gate"` や `"process-review-gate"`）に `"enabled": false` を設定すると、プラグイン全体を維持したまま個別の Hook だけを無効化できます。
