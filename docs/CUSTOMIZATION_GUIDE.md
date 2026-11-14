# カスタマイズ・環境別運用ガイド (Customization & Fallback Guide)

他の PC や異なる開発環境へ導入した際のカスタマイズ方法と、外部ツール有無による動作の違いをまとめています。

---

## 1. Claude Code (`claude` CLI) がある環境 vs 無い環境

本セットアップは **Claude Code がインストールされていない PC でも 100% 動作** します。

### 仕組み
`ask_opus.py`、`exit_review.py`、`sonnet_inspect.py` は実行時に以下の順序で `claude` バイナリを自動探索します：
1. 環境変数 `CLAUDE_CLI_PATH`
2. `PATH` 上の `claude` (`shutil.which("claude")`)
3. `~/.local/bin/claude`, `/opt/homebrew/bin/claude`, `/usr/local/bin/claude`, Windows 標準パス

もし見つからない場合（または未ログインの場合）、スクリプトはクラッシュせず `exit code 2` と `[FALLBACK_TO_NATIVE_SUBAGENT]` 指示を返し、Antigravity エージェントは自動的に以下の **Antigravity 内蔵サブエージェント** を起動します：

- `ask-opus` ➔ **`senior-architect-advisor`** サブエージェント
- `claude-exit-review` ➔ **`carbon-verifier`** サブエージェント
- `multi-agent-opinion` Stage 2 ➔ **`mindfulness-inspector`** サブエージェント

### 💡 Antigravity 内蔵の Opus / Sonnet を使いたい場合のおすすめ運用
Claude Code CLI を入れていない環境で Claude Opus や Sonnet の視点が欲しい場合は、**Antigravity のモデルピッカーで内蔵の `Claude Opus` または `Claude Sonnet` を選択（Mode C）** してください。
- 日常の調査・実装・テストは `claude-orchestrator-kit` のルールにより自動的に高速な `Gemini Flash` サブエージェントへ委譲されるため、内蔵 Claude のクオータをほとんど消費しません。
- そして設計相談（`senior-architect-advisor`）や出口レビュー（`carbon-verifier`）を `Model: "inherit"` で呼んだ瞬間だけ、**Antigravity 内蔵の Claude Opus / Sonnet 自身が独立コンテキストのサブエージェントとして起動**します。

---

## 2. ブラウザ操作環境（`cdp-relay` vs 内蔵 `browser` サブエージェント）

- Chrome / Edge 144 以降では、MCP の `chrome_devtools` / `edge_devtools` 直結を使うとブラウザ側にモーダル承認ダイアログが出て無人実行がデッドロックするため、`GEMINI.md §2.3` で使用を禁止しています。
- 標準では **Antigravity 内蔵の `browser` サブエージェント**（または Playwright 等のヘッドレス E2E）がそのまま使われます。
- もし普段使いのログイン済み Chrome プロファイルを直接操作する拡張機能中継 MCP（`cdp-relay` 等）を別途導入している環境では、自動的にそちらを優先利用できます。

---

## 3. ルールファイルを自分好みに編集する際の鉄則（24,000バイト制限）

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

## 4. `agy-harness` の Hook を一部調整・無効化したい場合

- **特定ドメインを信頼境界（URL アクセス許可リスト）に追加したい場合**:
  自社や個人プロジェクト固有のドメインは `~/.gemini/config/allowed_domains.local.txt` を新規作成して1行1ドメインで記載するのが推奨です（サブドメインも自動許可され、`install.py` の再実行でも上書きされません。共通リスト `~/.gemini/config/plugins/agy-harness/scripts/allowed_domains.txt` への追記も可能です）。
- **完了マーカー引き止めだけを一時スキップしたい場合**:
  未追跡バイナリストーム検知を維持したまま `GOAL_COMPLETE` マーカー確認のみをスキップしたい場合は、環境変数 `AGY_SKIP_STOP_MARKER_GATE=1` を設定できます。
- **特定の Hook だけを一時的に OFF にしたい場合**:
  `~/.gemini/config/plugins/agy-harness/hooks.json`（または `~/.gemini/config/hooks.json`）内の該当フックオブジェクト（例: `"completion-gate"` や `"process-review-gate"`）に `"enabled": false` を設定すると、プラグイン全体を維持したまま個別の Hook だけを無効化できます。
