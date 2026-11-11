# Antigravity Gemini Project Setup Tool

Google Antigravity Gemini をあらゆるプロジェクトで最大出力かつ理想的な挙動で動作させるための、ゼロ依存型・超高速プロジェクト自動セットアップツールです。

このフォルダ (`antigravity-project-setup`) を `.agent/` に配置するだけで、どの環境からでも一発で利用可能になります。

---

## 🌟 主な機能

1. **技術スタック自動検出 (Zero-Configuration Scan)**
   - プロジェクト内のファイル構成をスキャンし、使用言語、Webフレームワーク、パッケージマネージャー（pnpmやuvなど）、デプロイ環境、データベースを自動で特定します。
2. **サブエージェント並行自律開発の強制セットアップ**
   - 検出した技術スタックに合わせ、フロントエンド、バックエンド、データベース、デプロイに特化した専門サブエージェント定義を公式 workspace plugin（`.agents/plugins/project-agent-kit/agents/`）へ自動生成。
   - メインプロセスを「監督（オーケストレーター）」として動かし、高負荷タスクを細分化してサブエージェント群へ並列委任・自走させる仕組みを構築します。
3. **極限ベストプラクティスを統合した `GEMINI.md` / `AGENTS.md` 生成**
   - **パッケージサプライチェーン安全対策**：pnpm（JavaScript）や uv（Python）の厳格な利用を求める安全ルールを組み込みます。
   - **計画ファースト (Planning Mode)**：複数ファイルの編集時は必ず `implementation_plan.md` と `task.md` の作成とサインオフを強制。
   - **力づく改変（Brute-force）の完全禁止**：テスト失敗時にアサーションコードを勝手に直すのを禁じ、本質的なアプリケーションコード修正を徹底させます。
4. **検証・デプロイスキルの自動構築**
   - 環境に応じたビルド・検証・デプロイの手順をまとめた `deploy-skill/SKILL.md` を自動配備。
   - クリーンインストール、静的解析、単体テスト、ビルド、デプロイを一気通貫で安全に実行する実行可能ラッパースクリプト `scripts/deploy.sh` を自動生成。
5. **能力棚卸しとサービス提供準備**
   - Claude CLI / Antigravity AGY、AGY native image generation、second-opinion などの有無を検出し、`GEMINI.md` / `AGENTS.md` に記録します。
   - Web の OGP/SEO/DB/Auth/Deploy、Native のアイコン/ストア素材、Game のスプライト/透過/既存スタイル踏襲を扱う専門 Skill を生成します。
   - 公式ドキュメントに `npx` が含まれる場合も直接実行せず、固定バージョンの `pnpm dlx` または `pnpm exec` へ置換する調査契約を生成します。
6. **セカンドオピニオンの内包**
   - `second-opinion-skill` と `scripts/second-opinion-review.sh` を生成します。スキルは Antigravity 内蔵のレビュー用サブエージェント (`second-opinion-lite` / `multi-agent-opinion`) を第 1 選択とし、ランナーは Claude CLI、次いで Antigravity AGY の非対話実行を探索してフォールバックします。

---

## 🚀 使い方

### 1. 導入方法
他のユーザーが同じ環境を利用したい場合、このフォルダを対象マシンの `.agent/` ディレクトリ配下にコピーします。

```bash
# 例: リポジトリまたは共通ドライブからコピー
cp -r /path/to/antigravity-project-setup ~/.agent/
```

### 2. セットアップの実行
セットアップを行いたいプロジェクトフォルダのルートに移動し、以下のコマンドを実行します。

```bash
node ~/.agent/antigravity-project-setup/setup.js
```

### 3. 対話型ウィザードの進行
1. **自動検出結果の確認**：
   自動検出された言語、パッケージマネージャー、フレームワークが表示されます。
   `Would you like to initiate setup with this stack? [Y/n]` に対し、`y`（またはEnter）を押します。
2. **追加の要求仕様（任意）**：
   プロジェクトの目的や、自動検出されなかった特別な構成（例：「データベースはPostgreSQL」「Vercelにデプロイする」など）があれば入力します。
3. **アセット生成完了**：
   以下のファイルが一瞬で自動生成され、Antigravity Gemini が理想的な並行開発を行えるようになります。

---

## 📁 生成されるファイル構成

```text
PROJECT_ROOT/
├── GEMINI.md               # サブエージェント限界活用、サプライチェーン対策、確実な動作の憲法
├── AGENTS.md               # 検出された技術スタック、ディレクトリ構成、コーディング規約の定義
├── .agents/
│   └── plugins/
│       └── project-agent-kit/
│           ├── plugin.json             # 公式 workspace plugin marker
│           ├── agents/
│           │   ├── capability-researcher.md
│           │   ├── asset-pipeline-specialist.md
│           │   ├── quality-systems-specialist.md
│           │   ├── frontend-developer.md
│           │   ├── backend-developer.md
│           │   ├── db-specialist.md
│           │   ├── game-developer.md
│           │   └── deploy-specialist.md
│           └── skills/
│               ├── capability-discovery-skill/
│               ├── asset-pipeline-skill/
│               ├── web-service-readiness-skill/
│               ├── db-setup-skill/
│               ├── security-privacy-skill/
│               ├── observability-skill/
│               ├── test-strategy-skill/
│               ├── environment-secrets-skill/
│               ├── release-store-skill/
│               ├── accessibility-skill/
│               ├── deploy-skill/
│               └── second-opinion-skill/
└── scripts/
    ├── deploy.sh                   # [実行可能] lockfile 準拠インストール、テスト、ビルド、デプロイのラッパー
    └── second-opinion-review.sh    # [実行可能] Claude CLI / Antigravity(Gemini) レビューランナー
```

---

## 🛠 カスタマイズについて
`.agent/antigravity-project-setup/templates/` 配下にあるテンプレートファイルを編集することで、プロジェクト共通のデフォルトルールや専門エージェントのプロンプトを自由に変更可能です。

---

## 💡 重要な設計概念：グローバルプラグイン と プロジェクトカスタムプラグイン

Antigravity Geminiにおける拡張（SkillsやAgents）には、**「グローバルプラグイン」**と**「プロジェクトカスタムプラグイン（ローカル）」**の2つのレイヤーが存在します。この違いを理解することが、再現性の高い開発環境を構築する上で極めて重要です。

### 1. プロジェクトカスタムプラグイン (ローカル)
- **配置例**: `PROJECT_ROOT/.agents/skills/`
- **特徴**: エージェントは**「アクティブなワークスペースのルートディレクトリ」から相対的**に探索を行います。
- **メリット**: そのプロジェクト特有のルールや特定のモジュール用のスキルなどをカプセル化してコミットするのに適しています。
- **注意点**: 別のプロジェクトフォルダに移動すると、探索パスが切り替わるため、これらのローカルカスタムスキルは一切ロードされなくなります（ロードされるスキル数が減少する原因になります）。

### 2. グローバルプラグイン
- **配置例**: `~/.gemini/config/plugins/` (例: `~/.gemini/config/plugins/antigravity-custom-plugin/skills/`)
- **特徴**: どのワークスペースを開いていても、エージェントの起動時に基本システムの一部として**常に100%確実にグローバルロード**されます。
- **メリット**: `antigravity-project-setup` や `second-opinion` など、プロジェクトを問わず横断的に利用したい強力なツール・検証プロセスを登録するのに最適です。
- **構成方法**:
  1. `~/.gemini/config/plugins/<plugin-name>/` を作成。
  2. `plugin.json` （名前、バージョン、概要などの最小構成メタデータ）を配置。
  3. `skills/<skill-name>/SKILL.md` にスキルを配置。

この二重レイヤー構造を正しく活用し、プロジェクト固有の局所的なスキルはローカルへ、開発効率を底上げする強力な共通ツールや憲法ブートストラップはグローバルプラグインへと整理配備してください！
