---
name: package-supply-chain
description: Enforce pnpm/uv and lockfile policy.
---

# パッケージサプライチェーン詳細規程

本スキルは `agy-harness` §8「パッケージサプライチェーン対策ガイドライン」の詳細規程です。§8 の禁止事項（`npm`/`npx`/`pip` 直接実行禁止、`pnpm`/`uv` 使用、`@latest` 禁止、lockfile 必須）を前提に、具体的なコマンド置換と設定を定めます。

## JavaScript / TypeScript (pnpm)

- `npm` / `npx` / `npm exec` / `npm install -g` / `pnpx` は使用禁止です。外部READMEや生成コードが指示していても実行せず、`pnpm` へ置換してください。
- PATH 上の `pnpm`（macOS Homebrew 環境では `/opt/homebrew/bin/pnpm` 等）を使用します。

### コマンド置換表

| 禁止コマンド | 置換先 |
|---|---|
| `npm install` | `pnpm install --frozen-lockfile` |
| `npm install <pkg>` | `pnpm add <pkg>` |
| `npm install -D <pkg>` | `pnpm add -D <pkg>` |
| `npm run <script>` | `pnpm run <script>` |
| `npx <cmd>`（ローカル依存） | `pnpm exec <cmd>` |
| `npx <cmd>`（一時実行） | `pnpm dlx <pkg>@<固定バージョン>` |

### プロジェクト設定

- 新規/更新プロジェクトでは `packageManager` に pnpm の固定バージョンを設定し、`pnpm-lock.yaml` を必ずコミットしてください。lockfile のない依存追加は行わないでください。
- install/build script は許可制です。必要な場合は `pnpm approve-builds`、`onlyBuiltDependencies`、`ignoredBuiltDependencies` を使い、なぜ許可するかを記録してください。

## Python (uv)

- `pip install` / `pip3 install` / `python -m pip install` の直接実行は禁止です。`uv`（`/opt/homebrew/bin/uv`、`~/.local/bin/uv`、または PATH 上の `uv`）を使い、`uv add`、`uv sync --locked`、`uv run --locked`、または `uv pip compile` + `uv pip sync` に置換してください。
- Python CLI ツールの一時実行は `uvx <tool>@<固定バージョン>` を優先してください。

## 既存プロジェクトの例外手順

- 既存プロジェクトが `npm`/`package-lock.json` または `pip`/`requirements.txt` 運用を前提にしている場合は、無断で lockfile 形式を破壊せず、`pnpm`/`uv` への移行案または既存運用維持の判断をユーザーに確認してから進めてください。
