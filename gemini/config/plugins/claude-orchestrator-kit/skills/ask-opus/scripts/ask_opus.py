#!/usr/bin/env python3
"""Ask Claude Opus for architectural advice, breakthrough ideas, and second opinions via external CLI.

If external `claude` CLI is not installed or unavailable on this machine, exits with code 2 and
emits a structured `[FALLBACK_TO_NATIVE_SUBAGENT]` instruction so Antigravity seamlessly invokes
the built-in `senior-architect-advisor` subagent (running built-in Opus/Sonnet/Carbon/Pro).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE.parent / "config.json").read_text(encoding="utf-8"))

SYSTEM_PROMPT = """あなたはシニアソフトウェアアーキテクト・軍師です。
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
        "Invoke subagent `senior-architect-advisor` via `invoke_subagent` "
        "(use Model='inherit' when main model is Carbon/Argon/Claude Opus/Sonnet, or Model='pro' when main model is Flash).",
        file=sys.stderr,
    )
    return 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="Claude CLI の存在確認のみを実行")
    ap.add_argument("--topic", help="相談したい論点・質問・壁の内容")
    ap.add_argument("--context-file", type=Path, help="背景説明や試行錯誤ログのファイル (任意)")
    ap.add_argument("--target", action="append", default=[], type=Path, help="参照すべきファイル/ディレクトリ")
    ap.add_argument("--repo", type=Path, help="作業ディレクトリ (Read の基点)")
    ap.add_argument("--out", type=Path, help="助言レポート出力先 (.md)")
    a = ap.parse_args()

    cli_bin = resolve_claude_cli(CONFIG.get("cli_path", "claude"))
    if a.check:
        if cli_bin:
            print(f"claude_cli: available ({cli_bin})")
            return 0
        return unavailable("claude CLI binary not found")

    if not a.topic or not a.repo or not a.out:
        ap.error("--topic, --repo, and --out are required unless --check is passed")

    if not cli_bin:
        return unavailable("claude CLI binary not found")

    if not a.repo.is_dir():
        return unavailable(f"repo directory not found: {a.repo}")

    context = ""
    if a.context_file:
        try:
            context = a.context_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
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
    cmd = [
        cli_bin,
        "-p",
        "--model", CONFIG["model"],
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
            cwd=a.repo.resolve(),
            timeout=CONFIG["timeout_sec"],
        )
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
    model_ok = bool(models) and any(expected in m.lower() for m in models)
    code = 0 if model_ok else 3

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(
        f"# Claude Opus Architectural Advice\n\n"
        f"- date: {datetime.now().isoformat(timespec='seconds')}\n"
        f"- topic: {a.topic}\n"
        f"- repo: {a.repo.resolve()}\n"
        f"- resolved_models: {models}\n"
        f"- model_check: {'OK' if model_ok else 'MISMATCH'}\n"
        f"- cost_usd: {data.get('total_cost_usd')}\n\n"
        f"---\n\n{result}\n",
        encoding="utf-8",
    )

    print(f"ask_opus: status={'OK' if model_ok else 'MODEL_MISMATCH'} | exit={code} | models={models} | cost=${data.get('total_cost_usd')} | report={a.out}")
    return code


if __name__ == "__main__":
    sys.exit(main())
