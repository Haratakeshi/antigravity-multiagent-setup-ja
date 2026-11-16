#!/usr/bin/env python3
"""Clean-room exit review of a git working tree by Claude Fable 5.2 strictly under Commercial API auth.

Compliance & Billing Guardrails:
1. Standard exit reviews use Antigravity built-in `carbon-verifier` / `second-opinion-lite` by default.
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

CONFIG = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text(encoding="utf-8"))

PLACEHOLDER_KEYS = {"", "none", "null", "false", "your_api_key", "your-api-key", "<your_anthropic_api_key>"}
CLOUD_PROVIDER_VARS = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")

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


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=True,
    ).stdout


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
        "[FALLBACK_TO_NATIVE_SUBAGENT] External `claude` API review is unavailable or not authorized. "
        "Invoke subagent `carbon-verifier` via `invoke_subagent` with the same clean-room criteria, repo diff, and screenshot paths "
        "(use Model='inherit' when main model is Carbon/Argon/Claude, or Model='pro' when main model is Flash).",
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
            help="Human が明示的に外部 Fable API 監査を指示したことを示す必須フラグ",
        )
        ap.add_argument("--repo", type=Path)
        ap.add_argument("--criteria", type=Path, help="受け入れ基準/不変条件を書いたファイル")
        ap.add_argument("--base", default="HEAD", help="比較基点 (default: HEAD = 未コミット差分)")
        ap.add_argument("--model", default=CONFIG["model"], help="使用モデル (default: config.json の model)")
        ap.add_argument("--expected-model", default=None, help="期待する解決モデル名部分文字列")
        ap.add_argument("--image", action="append", default=[], type=Path)
        ap.add_argument("--out", type=Path, help="レポート出力先 .md")
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
                "missing --human-authorized flag; use built-in carbon-verifier by default unless Human explicitly requests external API review"
            )

        if not a.repo or not a.criteria or not a.out:
            ap.error("--repo, --criteria, and --out are required unless --check is passed")

        repo = a.repo.resolve()
        images = [p.resolve() for p in a.image]
        missing = [str(p) for p in images if not p.is_file()]
        if missing:
            return unavailable(f"image not found: {missing}")
        try:
            criteria = a.criteria.read_text(encoding="utf-8")
            diff = build_diff(repo, a.base)
            status = git(repo, "status", "--porcelain")
        except (OSError, UnicodeDecodeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            return unavailable(f"input error: {e}")

        limit = CONFIG["max_diff_bytes"]
        truncated = len(diff.encode("utf-8")) > limit
        if truncated:
            diff = diff.encode("utf-8")[:limit].decode("utf-8", errors="ignore") + "\n[... diff truncated; Read the files directly ...]"
        prompt = PROMPT.format(
            criteria=criteria,
            images="\n".join(f"- {p}" for p in images) or "(なし)",
            status=status or "(clean)",
            base=a.base,
            diff=diff or "(差分なし)",
        )

        tools = ",".join(CONFIG["allowed_tools"])
        cmd = [cli_bin]
        if CONFIG.get("use_bare", True):
            cmd.append("--bare")
        cmd += [
            "-p", "--model", a.model, "--output-format", "json",
            "--tools", tools, "--allowedTools", tools,
        ]
        for d in sorted({str(p.parent) for p in images}):
            cmd += ["--add-dir", d]
        try:
            r = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=repo,
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
        verdict = parse_verdict(result)
        if not model_ok:
            verdict, code = "INVALID(model mismatch)", 3
        elif verdict == "PASS" and truncated:
            verdict, code = "INCONCLUSIVE(PASS on truncated diff)", 4
        else:
            code = {"PASS": 0, "FAIL": 1}.get(verdict, 4)

        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(
            f"# Claude Exit Review ({a.model})\n\n- date: {datetime.now().isoformat(timespec='seconds')}\n"
            f"- auth_mode: {auth_info} (--bare)\n- repo: {repo}\n"
            f"- base: {a.base}\n- requested_model: {a.model}\n- diff_truncated: {truncated}\n- images: {[str(p) for p in images]}\n"
            f"- resolved_models: {models}\n- model_check: {'OK' if model_ok else 'MISMATCH'}\n"
            f"- verdict: {verdict}\n- exit_code: {code}\n- cost_usd: {data.get('total_cost_usd')}\n\n---\n\n{result}\n",
            encoding="utf-8",
        )
        print(f"external_cli_review_verdict: {verdict} | exit={code} | auth={auth_info} | models={models} | report={a.out}")
        return code
    finally:
        wd.cancel()


if __name__ == "__main__":
    sys.exit(main())
