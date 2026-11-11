#!/usr/bin/env python3
"""Run a multi-agent-kit inspector agent definition on Claude Sonnet via the external CLI.

If external `claude` CLI is not installed on this machine, exits with code 2 and emits a
`[FALLBACK_TO_NATIVE_SUBAGENT]` directive so Antigravity invokes `mindfulness-inspector`
natively via `invoke_subagent` (inheriting built-in Claude/Carbon/Argon or Pro).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
DEFAULT_AGENT = HERE.parent.parent.parent / "agents" / "mindfulness-inspector.md"
VERDICTS = ("CONFIRMED", "UNVERIFIED", "REJECTED")

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


def resolve_claude_cli(configured: str) -> str | None:
    candidates = []
    env_cli = os.environ.get("CLAUDE_CLI_PATH", "").strip()
    if env_cli:
        candidates.append(env_cli)
    if configured and configured != "claude":
        candidates.append(os.path.expanduser(configured))
    which_hit = shutil.which("claude")
    if which_hit:
        candidates.append(which_hit)
    home = Path.home()
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
        "[FALLBACK_TO_NATIVE_SUBAGENT] External `claude` CLI is unavailable on this machine. "
        "Invoke native subagent `mindfulness-inspector` via `invoke_subagent` "
        "(use Model='inherit' when main model is Carbon/Argon/Claude, or Model='pro' when main model is Flash) "
        "to complete Stage 2 CONFIRMED / UNVERIFIED / REJECTED fact inspection.",
        file=sys.stderr,
    )
    return 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="Claude CLI の存在確認のみを実行")
    ap.add_argument("--findings", type=Path, help="ステージ1の全指摘を書いたファイル")
    ap.add_argument("--target", action="append", default=[], type=Path, help="検査対象のファイル/ディレクトリ (複数可)")
    ap.add_argument("--repo", type=Path, help="作業ディレクトリ (Read の基点)")
    ap.add_argument("--agent-file", type=Path, default=DEFAULT_AGENT)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()

    cli_bin = resolve_claude_cli(CONFIG.get("cli_path", "claude"))
    if a.check:
        if cli_bin:
            print(f"claude_cli: available ({cli_bin})")
            return 0
        return unavailable("claude CLI binary not found")

    if not a.findings or not a.repo or not a.out:
        ap.error("--findings, --repo, and --out are required unless --check is passed")

    if not cli_bin:
        return unavailable("claude CLI binary not found")

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
    cmd = [cli_bin, "-p", "--model", CONFIG["model"], "--output-format", "json",
           "--tools", tools, "--allowedTools", tools, "--append-system-prompt", system_prompt]
    for d in sorted({str(p if p.is_dir() else p.parent) for p in targets}):
        cmd += ["--add-dir", d]
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                           cwd=a.repo.resolve(), timeout=CONFIG["timeout_sec"])
        data = json.loads(r.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        return unavailable(type(e).__name__)
    if not isinstance(data, dict):
        return unavailable("unexpected CLI output")
    result = data.get("result") or ""
    if r.returncode != 0 or data.get("is_error"):
        return unavailable(f"exit={r.returncode} {result[:300]}")

    models = list((data.get("modelUsage") or {}).keys())
    expected = CONFIG["expected_model_substring"].lower()
    model_ok = bool(models) and all(expected in m.lower() for m in models)
    counts = {v: len(re.findall(rf"VERIFICATION:\s*\**\s*{v}", result)) for v in VERDICTS}
    code = 3 if not model_ok else (0 if any(counts.values()) else 4)

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(
        f"# Sonnet Inspection Report\n\n- date: {datetime.now().isoformat(timespec='seconds')}\n"
        f"- agent_file: {a.agent_file}\n- resolved_models: {models}\n- model_check: {'OK' if model_ok else 'MISMATCH'}\n"
        f"- counts: {counts}\n- exit_code: {code}\n- cost_usd: {data.get('total_cost_usd')}\n\n---\n\n{result}\n",
        encoding="utf-8",
    )
    print(f"sonnet_inspect: counts={counts} | exit={code} | models={models} | report={a.out}")
    return code


if __name__ == "__main__":
    sys.exit(main())
