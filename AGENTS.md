# AGENTS.md — Antigravity 自動セットアップ実行手順書 (Agent Runbook)

[事実] このファイルは、ユーザーがあなた（Antigravity エージェント）に対して **「このリポジトリの中身を見て私の環境をセットアップして」** と依頼した際に、あなたが安全かつ確実に環境構築を完遂するための公式手順書です。

---

## 1. あなたがセットアップする全体像

本リポジトリ（`antigravity-multiagent-setup-ja`）は、以下のコア理念とツール群を `~/.gemini/`（および `~/.agent/antigravity-project-setup/`）へ配備します：

1. **`~/.gemini/GEMINI.md`（グローバル憲法 §0〜§2）**:
   - Tiered PAC（Predict → Act → Compare）、着手前の前提検証、確信度の校正、3ストライク時の視点リセット、完了前セルフレビュー、副作用境界（Human Gate）、Chrome 144+ DevTools 直結デッドロック禁止。
2. **4つのコアプラグイン（`~/.gemini/config/plugins/` — 配置のみで自動ロード）**:
   - `agy-harness`: 実装規律（§4〜§11）＋ 5つの機械的 Lifecycle Hooks（`npm`/`pip`/`@latest` ブロック、テスト skip/恒真検知、信頼境界ドメイン確認、日付Grounding注入、完了前セルフレビュー強制）＋ `second-opinion-lite` スキル。
   - `multi-agent-kit`: 監査独立性規律（§3）＋ 15体の単一責任専門レビュアー ＋ 3体の複眼監査（`multi-agent-opinion` 3-Strike PDCA、`mindfulness`、`visual-verification-loop`、Web/Stripe本番監査）。
   - `multi-axis-investigation-kit`: 認知的閉鎖を防ぐ直交多軸探索・内外両面観測規律（`multi-axis-investigation`）。
   - `claude-orchestrator-kit`: メインモデル（Gemini 4 Carbon / Argon / Flash / 内蔵 Claude）に応じた適応オーケストレーション ＋ `flash-runner` / `flash-implementer` / `flash-investigator` / `flash-e2e-verifier` / `carbon-verifier` / `senior-architect-advisor` ＋ `ask-opus` / `claude-exit-review`。
3. **4つの日常グローバルスキル（`~/.gemini/config/skills/`）**:
   - `grill-me` / `grilling`: 9ステップの段階的ゴール合意・1項目1Ask方針すり合わせ・反証レビュー統合ワークフロー。
   - `package-supply-chain`: `pnpm` / `uv`・固定バージョン・lockfile 規律。
   - `antigravity-project-setup`（＋ `~/.agent/antigravity-project-setup/setup.js`）: 新規プロジェクト用のルール・サブエージェント自動ブートストラッパ。

---

## 2. セットアップ実行ステップ（エージェント用手順）

ユーザーからセットアップを依頼されたら、以下のステップを順番に実行してください。

### Step 1: リポジトリの取得確認（まだローカルに無い場合のみ）
- すでにローカルにクローン済みのパスが開かれている場合はそのディレクトリを使用します。
- GitHub URL のみが渡された場合は、作業ディレクトリ（例: `~/antigravity-multiagent-setup-ja` または scratch 領域）へ `git clone https://github.com/Haratakeshi/antigravity-multiagent-setup-ja.git` を実行します。

### Step 2: ターゲット環境の事前診断（`--check`）
まず書き込みを行わずに、マシンの環境（OS、Python、Node、`pnpm`、`uv`、`claude` CLI の有無、既存 `~/.gemini/` の有無）を診断します：

```bash
python3 scripts/install.py --check
```
（※Windows 等で `python3` が無い場合は `python scripts/install.py --check` を使用してください。）

診断結果から以下を確認します：
- **既存の `~/.gemini/GEMINI.md` や同名プラグインが存在する場合**: インストーラが自動で `~/.gemini/backups/setup-baseline/` に初回ベースラインバックアップを作成することをユーザーに伝えます（もしユーザーが独自のカスタム記述を `GEMINI.md` に書いている場合は、後でマージするかそのまま置き換えるか確認しても構いません）。
- **`claude_cli` が `null`（Claude Code 未導入）の場合**: **追加インストールは不要**です。本セットアップの全スキル（`ask-opus`, `claude-exit-review`, `multi-agent-opinion` Stage 2）は、外部 `claude` CLI が無い環境では自動的に **Antigravity 内蔵の上位モデル（内蔵 Claude Opus / Sonnet、Gemini 4 Carbon、または Pro）を使ったネイティブ・サブエージェント（`senior-architect-advisor`, `carbon-verifier`, `mindfulness-inspector`）へフォールバック**するように設計されています。
- **`node` が存在する場合**: `--with-process-gate` を付けることで `src/` 変更時の空 `catch` 検知＆監査証跡チェック Stop Hook も導入できます。
- **`claude_cli` が存在する場合**: `--with-claude-skills` を付けることで `~/.claude/skills/grilling/` にも同じ 9ステップ `grilling` スキルを同期できます。

### Step 3: インストールの実行
環境に応じたフラグでインストーラを実行します（重要: `~/.gemini/config/config.json` は Antigravity Language Server がアトミック管理しているため、インストーラは `config.json` を直接書き換えません）：

```bash
# 標準インストール（推奨）
python3 scripts/install.py

# Node.js があり、Claude Code も併用している場合のフルインストール例
python3 scripts/install.py --with-process-gate --with-claude-skills
```

### Step 4: 整合性・バイト上限の自動検証
インストール後、以下の検証スクリプトを実行して `status: "PASS"`（exit code `0`）を確認します：

```bash
python3 scripts/verify.py
```

### Step 5: プラグイン有効状態の確認とユーザーへの完了報告
1. Step 2/3 の出力 JSON の `previously_disabled_plugins` が空であれば、4つのプラグインはすべて **次ターンから自動で有効化** されます（再起動不要）。
2. もし過去に手動で無効化されていたプラグインがある場合、またはユーザーに管理画面を案内する場合は、以下の公式リンク形式で案内してください：
   - [agy-harness](customization://agy-harness)
   - [multi-agent-kit](customization://multi-agent-kit)
   - [multi-axis-investigation-kit](customization://multi-axis-investigation-kit)
   - [claude-orchestrator-kit](customization://claude-orchestrator-kit)
3. `pnpm` または `uv` が未導入だった場合は、`agy-harness` の `command-guard` が `npm install` / `pip install` をブロックして `pnpm` / `uv` を推奨するため、必要に応じて `pnpm` / `uv` の導入方法（例: macOS なら `brew install pnpm uv`、Windows なら `winget install pnpm.pnpm astral-sh.uv` 等）を一言添えて完了報告してください。
4. ロールバック方法を案内する際は `python3 scripts/uninstall.py` でいつでも直前のバックアップへ戻せることを伝えてください。
