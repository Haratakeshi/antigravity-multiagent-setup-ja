#!/usr/bin/env python3
"""Ask Claude Fable 5.2 via external CLI strictly under explicit Human authorization + Commercial API auth.

Compliance & Billing Guardrails:
1. Requires `--human-authorized` on execution so the agent never auto-triggers paid external API calls.
2. Requires commercial credentials (`ANTHROPIC_API_KEY` in env or `~/.gemini/.env`, or Bedrock/Vertex/Foundry).
   Consumer `claude.ai` subscription OAuth (Free/Pro/Max/Ultra) is strictly blocked to comply with Anthropic ToS.
3. Runs `claude --bare -p` with `CLAUDE_CODE_OAUTH_TOKEN` stripped so Keychain/OAuth tokens are never read.
4. Tools are restricted to read-only (Read, Grep, Glob).

Exit codes:
  0 = advice obtained and target model verified
  2 = unavailable / blocked (missing API key, missing --human-authorized, CLI absent, or timeout -> fallback to ask-opus)
  3 = model mismatch (did not run on expected model)
"""
import argparse
import faulthandler
import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE.parent / "config.json").read_text(encoding="utf-8"))

PLACEHOLDER_KEYS = {"", "none", "null", "false", "your_api_key", "your-api-key", "<your_anthropic_api_key>"}
CLOUD_PROVIDER_VARS = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")

SYSTEM_PROMPT = """あなたは最高峰のシニアソフトウェアアーキテクト・軍師です。
開発現場で発生した設計上の難所、3ストライク時の壁、または技術選定・アーキテクチャのトレードオフについて、独立したセカンドオピニオン・助言を求められています。

## 行動原則
1. **迎合の排除**: 質問者の見立てや前提が誤っている、あるいは不合理な複雑さ（過剰抽象化・YAGNI違反）を抱えている場合は、率直かつ客観的に指摘してください。
2. **事実検証**: 提示されたファイルやリポジトリの構造を、必要に応じて Read / Grep / Glob で確認し、想像や一般論ではなく「実際のコード事実」に基づいて回答してください（編集ツールは許可されていません）。
3. **構造化された助言**:
   - **【結論 / 推奨アプローチ】**: 最もシンプルかつ堅牢な解法を最初に明示。
   - **【現状の盲点・落とし穴】**: 質問者が陥っている可能性のある認知的バイアスや技術的リスク。
   - **【具体的設計 / 実装指針】**: 採用すべきデータ構造、状態遷移、API契約、あるいは捨てるべきコードの具体例。
   - **【代替案とのトレードオフ比較】**: 他の選択肢とそれを退ける理由。
"""

PROMPT_TEMPLATE = """相談テーマ / 解決したい課題:
<topic>
{topic}
</topic>

背景コンテキスト / これまでの経緯:
<context>
{context}
</context>

参照対象コード / 仕様 (Read / Grep / Glob で確認可能):
<target_files>
{targets}
</target_files>

指示:
上記について、シニアアーキテクト（軍師）としての見立て、落とし穴、および最も合理的・シンプルな推奨設計を構造化して回答してください。
"""


def _parse_dotenv_file(dotenv_path: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    if not dotenv_path.is_file():
        return parsed
    try:
        for raw in dotenv_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()
            if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
                v = v[1:-1].strip()
            elif " #" in v:
                v = v.split(" #", 1)[0].strip()
            if k:
                parsed[k] = v
    except OSError:
        pass
    return parsed


def resolve_commercial_auth_env() -> tuple[dict[str, str] | None, str]:
    home_dir = Path(os.environ.get("HOME") or str(Path.home()))
    dotenv_override = os.environ.get("AGY_DOTENV_PATH", "").strip()
    dotenv_path = Path(dotenv_override).expanduser() if dotenv_override else (home_dir / ".gemini" / ".env")
    dotenv_vals = _parse_dotenv_file(dotenv_path)

    child_env = dict(os.environ)
    child_env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)

    api_key = child_env.get("ANTHROPIC_API_KEY", "").strip()
    key_source = "env:ANTHROPIC_API_KEY"
    if not api_key and "ANTHROPIC_API_KEY" in dotenv_vals:
        api_key = dotenv_vals["ANTHROPIC_API_KEY"].strip()
        if api_key:
            child_env["ANTHROPIC_API_KEY"] = api_key
            key_source = f"dotenv:{dotenv_path.name}"

    if api_key and api_key.lower() not in PLACEHOLDER_KEYS and len(api_key) >= 12:
        return child_env, key_source

    for pvar in CLOUD_PROVIDER_VARS:
        pval = child_env.get(pvar, "").strip() or dotenv_vals.get(pvar, "").strip()
        if pval.lower() in ("1", "true", "yes"):
            child_env[pvar] = pval
            return child_env, f"cloud:{pvar}"

    return (
        None,
        "[BLOCKED_CONSUMER_OAUTH] Commercial API credentials (ANTHROPIC_API_KEY or Bedrock/Vertex/Foundry) are not set; "
        "Consumer Pro/Max subscription OAuth is blocked to comply with Anthropic Terms of Service",
    )


def resolve_claude_cli(configured: str) -> str | None:
    env_cli = os.environ.get("CLAUDE_CLI_PATH", "").strip()
    if env_cli:
        expanded = os.path.expanduser(env_cli)
        return expanded if (os.path.isfile(expanded) and os.access(expanded, os.X_OK)) else None
    candidates = []
    if configured and configured != "claude":
        candidates.append(os.path.expanduser(configured))
    which_hit = shutil.which("claude")
    if which_hit:
        candidates.append(which_hit)
    home = Path(os.environ.get("HOME") or str(Path.home()))
    candidates.extend([
        str(home / ".local" / "bin" / "claude"),
        "/opt/homebrew/bin/claude",
        "/usr/local/bin/claude",
        str(home / "AppData" / "Local" / "Programs" / "claude" / "claude.exe"),
    ])
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def unavailable(reason: str) -> int:
    print(f"status: external_review_unavailable ({reason})", file=sys.stderr)
    print(
        "[FALLBACK_TO_NATIVE_SUBAGENT] External `claude` API mode is unavailable or not authorized. "
        "Use the `ask-opus` skill instead: pin Antigravity built-in `claude-opus-5-5-high` via `swarm(action='add', model='claude-opus-5-5-high', ...)` "
        "(or invoke `senior-architect-advisor` when already running on built-in Opus).",
        file=sys.stderr,
    )
    return 2


def _watchdog_abort() -> None:
    unavailable("watchdog hard timeout")
    faulthandler.dump_traceback()
    os._exit(2)


def main() -> int:
    timeout_sec = int(CONFIG.get("timeout_sec", 900))
    wd = threading.Timer(timeout_sec + 60, _watchdog_abort)
    wd.daemon = True
    wd.start()
    try:
        ap = argparse.ArgumentParser()
        ap.add_argument("--check", action="store_true", help="Claude CLI と商用 API 認証の設定状態のみを確認")
        ap.add_argument(
            "--human-authorized",
            action="store_true",
            help="Human が明示的に外部 Fable API 呼び出しを指示したことを示す必須フラグ",
        )
        ap.add_argument("--topic", help="相談したい論点・質問・壁の内容")
        ap.add_argument("--context-file", type=Path, help="背景説明や試行錯誤ログのファイル (任意)")
        ap.add_argument("--target", action="append", default=[], type=Path, help="参照すべきファイル/ディレクトリ")
        ap.add_argument("--repo", type=Path, help="作業ディレクトリ (Read の基点)")
        ap.add_argument("--out", type=Path, help="助言レポート出力先 (.md)")
        ap.add_argument("--model", default=CONFIG["model"], help="使用モデル (default: config.json の model)")
        ap.add_argument("--expected-model", default=None, help="期待する解決モデル名部分文字列")
        a = ap.parse_args()

        cli_bin = resolve_claude_cli(CONFIG.get("cli_path", "claude"))
        if not cli_bin:
            return unavailable("claude CLI binary not found")

        child_env, auth_info = resolve_commercial_auth_env()
        if child_env is None:
            return unavailable(auth_info)

        if a.check:
            print(f"claude_cli: available ({cli_bin}, auth={auth_info})")
            return 0

        if not a.human_authorized:
            return unavailable(
                "missing --human-authorized flag; external ask-fable may only run on explicit Human instruction (use ask-opus by default)"
            )

        if not a.topic or not a.repo or not a.out:
            ap.error("--topic, --repo, and --out are required unless --check is passed")

        if not a.repo.is_dir():
            return unavailable(f"repo directory not found: {a.repo}")

        context = ""
        if a.context_file:
            try:
                context = a.context_file.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                return unavailable(f"context file error: {e}")

        max_ctx = int(CONFIG.get("max_context_bytes", 400000))
        if len(context.encode("utf-8")) > max_ctx:
            context = context.encode("utf-8")[:max_ctx].decode("utf-8", errors="ignore") + "\n[...truncated...]"

        targets = [p.resolve() for p in a.target]
        missing = [str(p) for p in targets if not p.exists()]
        if missing:
            return unavailable(f"target not found: {missing}")

        prompt = PROMPT_TEMPLATE.format(
            topic=a.topic,
            context=context or "(特記事項なし。コードベースおよび課題から自律判断してください)",
            targets="\n".join(f"- {p}" for p in targets) or "(作業リポジトリ全体)",
        )

        tools = ",".join(CONFIG["tools"])
        cmd = [cli_bin]
        if CONFIG.get("use_bare", True):
            cmd.append("--bare")
        cmd += [
            "-p",
            "--model", a.model,
            "--output-format", "json",
            "--tools", tools,
            "--allowedTools", tools,
            "--append-system-prompt", SYSTEM_PROMPT,
        ]
        for d in sorted({str(p if p.is_dir() else p.parent) for p in targets}):
            cmd += ["--add-dir", d]

        try:
            r = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=a.repo.resolve(),
                env=child_env,
                timeout=timeout_sec,
            )
            data = json.loads(r.stdout)
        except subprocess.TimeoutExpired:
            return unavailable("TimeoutExpired")
        except (OSError, json.JSONDecodeError) as e:
            detail = ""
            if isinstance(e, json.JSONDecodeError):
                detail = f" {(r.stderr or r.stdout)[:200].strip()}"
            return unavailable(f"{type(e).__name__}{detail}")

        if not isinstance(data, dict):
            return unavailable("unexpected CLI output")

        result = data.get("result") or ""
        if r.returncode != 0 or data.get("is_error"):
            return unavailable(f"exit={r.returncode} {result[:300]}")

        models = list((data.get("modelUsage") or {}).keys())
        expected = (
            a.expected_model
            if a.expected_model is not None
            else (a.model.split("[", 1)[0] if a.model != CONFIG["model"] else CONFIG.get("expected_model_substring", a.model))
        ).lower()
        model_ok = bool(models) and any(expected in m.lower() for m in models)
        code = 0 if model_ok else 3

        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(
            f"# Claude Architectural Advice ({a.model})\n\n"
            f"- date: {datetime.now().isoformat(timespec='seconds')}\n"
            f"- auth_mode: {auth_info} (--bare)\n"
            f"- topic: {a.topic}\n"
            f"- repo: {a.repo.resolve()}\n"
            f"- requested_model: {a.model}\n"
            f"- resolved_models: {models}\n"
            f"- model_check: {'OK' if model_ok else 'MISMATCH'}\n"
            f"- cost_usd: {data.get('total_cost_usd')}\n\n"
            f"---\n\n{result}\n",
            encoding="utf-8",
        )

        print(
            f"ask_fable: status={'OK' if model_ok else 'MODEL_MISMATCH'} | exit={code} "
            f"| auth={auth_info} | models={models} | cost=${data.get('total_cost_usd')} | report={a.out}"
        )
        return code
    finally:
        wd.cancel()


if __name__ == "__main__":
    sys.exit(main())
