#!/usr/bin/env python3
"""Run a multi-agent-kit Stage 2 inspector on Claude Fable 5.2 strictly under Commercial API auth.

Compliance & Billing Guardrails:
1. Standard Stage 2 inspection uses Antigravity native `mindfulness-inspector` subagent by default.
2. External execution requires BOTH `--human-authorized` AND commercial credentials (`ANTHROPIC_API_KEY`
   in env or `~/.gemini/.env`, or Bedrock/Vertex/Foundry). Consumer `claude.ai` OAuth is strictly blocked.
3. Runs `claude --bare -p` with `CLAUDE_CODE_OAUTH_TOKEN` stripped so Keychain/OAuth tokens are never read.
"""
import argparse
import faulthandler
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
DEFAULT_AGENT = HERE.parent.parent.parent / "agents" / "mindfulness-inspector.md"
VERDICTS = ("CONFIRMED", "UNVERIFIED", "REJECTED")

PLACEHOLDER_KEYS = {"", "none", "null", "false", "your_api_key", "your-api-key", "<your_anthropic_api_key>"}
CLOUD_PROVIDER_VARS = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")

PROMPT = """Reviewer Findings to Inspect:
<reviewer_findings>
{findings}
</reviewer_findings>

Target Code / Specification (Read / Grep / Glob で実ファイルを確認すること):
<target_code>
{targets}
</target_code>

指示:
上記の全指摘について、実際のコードベースおよび仕様の客観的事実と照合し、再検査（事実確認・反証・矛盾特定・3分類判定）を実行してください。
結果は標準出力にそのまま出力してください（send_message ツールは使えません）。
"""


def strip_frontmatter(text: str) -> str:
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    return text[m.end():] if m else text


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
        "[FALLBACK_TO_NATIVE_SUBAGENT] External `claude` API inspection is unavailable or not authorized. "
        "Invoke native subagent `mindfulness-inspector` via `invoke_subagent` "
        "(use Model='inherit' when main model is Carbon/Argon/Claude, or Model='pro' when main model is Flash) "
        "to complete Stage 2 CONFIRMED / UNVERIFIED / REJECTED fact inspection.",
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
            help="Human が明示的に外部 Fable API 再検査を指示したことを示す必須フラグ",
        )
        ap.add_argument("--findings", type=Path, help="ステージ1の全指摘を書いたファイル")
        ap.add_argument("--target", action="append", default=[], type=Path, help="検査対象のファイル/ディレクトリ (複数可)")
        ap.add_argument("--repo", type=Path, help="作業ディレクトリ (Read の基点)")
        ap.add_argument("--model", default=CONFIG["model"], help="使用モデル (default: config.json の model)")
        ap.add_argument("--expected-model", default=None, help="期待する解決モデル名部分文字列")
        ap.add_argument("--agent-file", type=Path, default=DEFAULT_AGENT)
        ap.add_argument("--out", type=Path)
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
                "missing --human-authorized flag; use native mindfulness-inspector by default unless Human explicitly requests external API inspection"
            )

        if not a.findings or not a.repo or not a.out:
            ap.error("--findings, --repo, and --out are required unless --check is passed")

        try:
            findings = a.findings.read_text(encoding="utf-8")
            system_prompt = strip_frontmatter(a.agent_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as e:
            return unavailable(f"input error: {e}")
        if len(findings.encode("utf-8")) > CONFIG["max_findings_bytes"]:
            return unavailable("findings too large; split the findings file")
        if not a.repo.is_dir():
            return unavailable(f"repo not found: {a.repo}")

        targets = [p.resolve() for p in a.target]
        missing = [str(p) for p in targets if not p.exists()]
        if missing:
            return unavailable(f"target not found: {missing}")

        prompt = PROMPT.format(findings=findings, targets="\n".join(f"- {p}" for p in targets) or "(repo 全体)")
        tools = ",".join(CONFIG["tools"])
        cmd = [cli_bin]
        if CONFIG.get("use_bare", True):
            cmd.append("--bare")
        cmd += [
            "-p", "--model", a.model, "--output-format", "json",
            "--tools", tools, "--allowedTools", tools, "--append-system-prompt", system_prompt,
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
        counts = {v: len(re.findall(rf"\[VERIFICATION:\s*{v}\]", result)) for v in VERDICTS}
        code = 0 if (model_ok and sum(counts.values()) > 0) else (3 if not model_ok else 4)

        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(
            f"# Stage 2 Fact Inspection ({a.model})\n\n- date: {datetime.now().isoformat(timespec='seconds')}\n"
            f"- auth_mode: {auth_info} (--bare)\n- repo: {a.repo.resolve()}\n- requested_model: {a.model}\n"
            f"- resolved_models: {models}\n- model_check: {'OK' if model_ok else 'MISMATCH'}\n"
            f"- counts: {counts}\n- exit_code: {code}\n- cost_usd: {data.get('total_cost_usd')}\n\n---\n\n{result}\n",
            encoding="utf-8",
        )
        print(f"sonnet_inspect: counts={counts} | exit={code} | auth={auth_info} | models={models} | report={a.out}")
        return code
    finally:
        wd.cancel()


if __name__ == "__main__":
    sys.exit(main())
