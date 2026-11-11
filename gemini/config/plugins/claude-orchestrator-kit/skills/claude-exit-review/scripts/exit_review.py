#!/usr/bin/env python3
"""Clean-room exit review of a git working tree by Claude (external CLI, read-only tools).

If external `claude` CLI is not installed on this machine, exits with code 2 and emits a
`[FALLBACK_TO_NATIVE_SUBAGENT]` directive instructing Antigravity to run `carbon-verifier`
natively (inheriting built-in Claude Sonnet/Opus, Gemini 4 Carbon, or Pro).
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

CONFIG = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text(encoding="utf-8"))

PROMPT = """あなたは独立したクリーンルーム・レビュアーです。作成者の意図や経緯は知らされていません。
以下の「受け入れ基準 / 不変条件」と「差分」だけを根拠に、最終成果物を反証的にレビューしてください。
- 必要に応じて Read / Grep / Glob で実ファイルを確認してよい（編集は不可）。
- 画像パスが与えられた場合は必ず Read で開いて目視確認し、基準ごとに見えた事実を書く。
- 各指摘は「重大度(CRITICAL/MAJOR/MINOR) / 根拠 file:line または画像 / 再現・反証手順」で書く。
- 最後の行に装飾なしで `VERDICT: PASS` または `VERDICT: FAIL` のどちらかを書く（CRITICAL が1件でもあれば FAIL）。

## 受け入れ基準 / 不変条件
{criteria}

## 画像（目視確認対象）
{images}

## git status
{status}

## 差分 (git diff {base} + untracked files)
{diff}
"""


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


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def build_diff(repo: Path, base: str) -> str:
    parts = [git(repo, "diff", base)]
    for rel in git(repo, "ls-files", "--others", "--exclude-standard").splitlines():
        try:
            body = (repo / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            body = "(binary or unreadable; inspect with Read if relevant)"
        parts.append(f"\n--- /dev/null\n+++ b/{rel} (untracked)\n" + "\n".join("+" + l for l in body.splitlines()))
    return "\n".join(parts)


def parse_verdict(text: str) -> str:
    for line in reversed(text.splitlines()):
        m = re.match(r"^[\s*`_>#-]*VERDICT:\s*[*`_]*\s*(PASS|FAIL)\b", line, re.I)
        if m:
            return m.group(1).upper()
    return "UNKNOWN"


def unavailable(reason: str) -> int:
    print(f"status: external_review_unavailable ({reason})", file=sys.stderr)
    print(
        "[FALLBACK_TO_NATIVE_SUBAGENT] External `claude` CLI is unavailable on this machine. "
        "Invoke subagent `carbon-verifier` via `invoke_subagent` with the same clean-room criteria, repo diff, and screenshot paths "
        "(use Model='inherit' when main model is Carbon/Argon/Claude, or Model='pro' when main model is Flash).",
        file=sys.stderr,
    )
    return 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="Claude CLI の存在確認のみを実行")
    ap.add_argument("--repo", type=Path)
    ap.add_argument("--criteria", type=Path, help="受け入れ基準/不変条件を書いたファイル")
    ap.add_argument("--base", default="HEAD", help="比較基点 (default: HEAD = 未コミット差分)")
    ap.add_argument("--image", action="append", default=[], type=Path)
    ap.add_argument("--out", type=Path, help="レポート出力先 .md")
    a = ap.parse_args()

    cli_bin = resolve_claude_cli(CONFIG.get("cli_path", "claude"))
    if a.check:
        if cli_bin:
            print(f"claude_cli: available ({cli_bin})")
            return 0
        return unavailable("claude CLI binary not found")

    if not a.repo or not a.criteria or not a.out:
        ap.error("--repo, --criteria, and --out are required unless --check is passed")

    if not cli_bin:
        return unavailable("claude CLI binary not found")

    repo = a.repo.resolve()
    images = [p.resolve() for p in a.image]
    missing = [str(p) for p in images if not p.is_file()]
    if missing:
        return unavailable(f"image not found: {missing}")
    try:
        criteria = a.criteria.read_text(encoding="utf-8")
        diff = build_diff(repo, a.base)
        status = git(repo, "status", "--porcelain")
    except (OSError, UnicodeDecodeError, subprocess.CalledProcessError) as e:
        return unavailable(f"input error: {e}")

    limit = CONFIG["max_diff_bytes"]
    truncated = len(diff.encode("utf-8")) > limit
    if truncated:
        diff = diff.encode("utf-8")[:limit].decode("utf-8", errors="ignore") + "\n[... diff truncated; Read the files directly ...]"
    prompt = PROMPT.format(criteria=criteria, images="\n".join(f"- {p}" for p in images) or "(なし)",
                           status=status or "(clean)", base=a.base, diff=diff or "(差分なし)")

    tools = ",".join(CONFIG["allowed_tools"])
    cmd = [cli_bin, "-p", "--model", CONFIG["model"], "--output-format", "json",
           "--tools", tools, "--allowedTools", tools]
    for d in sorted({str(p.parent) for p in images}):
        cmd += ["--add-dir", d]
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=repo, timeout=CONFIG["timeout_sec"])
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
    verdict = parse_verdict(result)
    if not model_ok:
        verdict, code = "INVALID(model mismatch)", 3
    elif verdict == "PASS" and truncated:
        verdict, code = "INCONCLUSIVE(PASS on truncated diff)", 4
    else:
        code = {"PASS": 0, "FAIL": 1}.get(verdict, 4)

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(
        f"# Claude Exit Review\n\n- date: {datetime.now().isoformat(timespec='seconds')}\n- repo: {repo}\n"
        f"- base: {a.base}\n- diff_truncated: {truncated}\n- images: {[str(p) for p in images]}\n"
        f"- resolved_models: {models}\n- model_check: {'OK' if model_ok else 'MISMATCH'}\n"
        f"- verdict: {verdict}\n- exit_code: {code}\n- cost_usd: {data.get('total_cost_usd')}\n\n---\n\n{result}\n",
        encoding="utf-8",
    )
    print(f"external_cli_review_verdict: {verdict} | exit={code} | models={models} | report={a.out}")
    return code


if __name__ == "__main__":
    sys.exit(main())
