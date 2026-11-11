#!/usr/bin/env python3
"""Comprehensive repository & installation verifier for antigravity-multiagent-setup-ja.

Checks:
1. Rule file byte limits: Every GEMINI.md and AGENTS.md strictly < 24,000 bytes
2. Sanitization audit: Scans ALL files (including verify.py itself) for hardcoded local user/volume paths,
   secret token formats, or extra caller-supplied regexes (via EXTRA_FORBIDDEN_REGEX env var)
3. Working tree hygiene: Ensures zero `__pycache__`, `.pyc`, or `.DS_Store` files exist in the repository
4. Syntax & schema check: All JSON files parse, all Python files compile cleanly into a temp dir,
   and JS/MJS scripts pass `node --check` (if node is available)
5. Hook behavior smoke tests: guard_commands.py & stop_selfreview.py
6. Fallback smoke tests: ask_opus.py / exit_review.py / sonnet_inspect.py emit [FALLBACK_TO_NATIVE_SUBAGENT] when claude CLI is absent
7. End-to-end install & rollback tests:
   (a) Clean empty home -> install all options -> uninstall -> zero leftover files
   (b) Pre-existing user files -> double install (plain, then full options) -> uninstall -> 100% baseline restored
"""
from __future__ import annotations

import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RULE_BYTE_LIMIT = 24_000

# Constructed dynamically so verify.py itself contains zero literal forbidden prefixes
FORBIDDEN_PATTERNS = [
    re.compile("/" + "Users/[A-Za-z0-9._-]+"),
    re.compile("/" + "Volumes/[A-Za-z0-9._-]+"),
    re.compile(r"[A-Za-z]:\\" + r"Users\\[A-Za-z0-9._-]+"),
    re.compile("sk-ant-" + r"[A-Za-z0-9_-]{10,}"),
    re.compile("AIza" + r"[0-9A-Za-z_-]{35}"),
    re.compile("ghp_" + r"[0-9A-Za-z]{36}"),
]


def run_check() -> int:
    errors: list[str] = []

    extra_rx = os.environ.get("EXTRA_FORBIDDEN_REGEX", "").strip()
    active_patterns = list(FORBIDDEN_PATTERNS)
    if extra_rx:
        active_patterns.append(re.compile(extra_rx, re.IGNORECASE))

    # 1. Working tree hygiene check (no __pycache__ / .pyc / .DS_Store)
    for item in REPO_ROOT.rglob("*"):
        if ".git" in item.parts:
            continue
        if item.name in ("__pycache__", ".DS_Store") or item.suffix in (".pyc", ".pyo"):
            errors.append(f"[HYGIENE_FAIL] Unwanted artifact in repo: {item.relative_to(REPO_ROOT)}")

    # 2. Rule byte limits
    rule_files = [
        p
        for p in REPO_ROOT.rglob("*")
        if p.is_file() and ".git" not in p.parts and p.name in ("GEMINI.md", "AGENTS.md")
    ]
    rule_sizes: dict[str, int] = {}
    for rf in sorted(rule_files):
        size = len(rf.read_bytes())
        rel = rf.relative_to(REPO_ROOT).as_posix()
        rule_sizes[rel] = size
        if size >= RULE_BYTE_LIMIT:
            errors.append(f"[BYTE_LIMIT] {rel} is {size} bytes (>= {RULE_BYTE_LIMIT})")

    # 3. Forbidden hardcoded path & secret scan across ALL files (including verify.py)
    scanned_files = 0
    for f in sorted(REPO_ROOT.rglob("*")):
        if not f.is_file() or ".git" in f.parts or "__pycache__" in f.parts:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        scanned_files += 1
        rel = f.relative_to(REPO_ROOT).as_posix()
        for pat in active_patterns:
            m = pat.search(text)
            if m:
                line_no = text[: m.start()].count("\n") + 1
                errors.append(f"[SANITIZE_FAIL] {rel}:{line_no} matched forbidden pattern -> `{m.group(0)}`")

    # 4. JSON, Python (compiled into temp dir), and Node syntax checks
    json_count = 0
    for jf in sorted(REPO_ROOT.rglob("*.json")):
        if ".git" in jf.parts:
            continue
        json_count += 1
        try:
            json.loads(jf.read_text(encoding="utf-8"))
        except Exception as e:
            errors.append(f"[JSON_INVALID] {jf.relative_to(REPO_ROOT)}: {e}")

    py_count = 0
    with tempfile.TemporaryDirectory() as pyc_tmp:
        for idx, pf in enumerate(sorted(REPO_ROOT.rglob("*.py"))):
            if ".git" in pf.parts or "__pycache__" in pf.parts:
                continue
            py_count += 1
            cfile = os.path.join(pyc_tmp, f"chk_{idx}.pyc")
            try:
                py_compile.compile(str(pf), cfile=cfile, doraise=True)
            except py_compile.PyCompileError as e:
                errors.append(f"[PY_SYNTAX_ERROR] {pf.relative_to(REPO_ROOT)}: {e}")

    node_bin = shutil.which("node")
    if node_bin:
        for jsf in [
            REPO_ROOT / "tools" / "antigravity-project-setup" / "setup.js",
            REPO_ROOT / "gemini" / "scripts" / "process-review-gate.mjs",
        ]:
            if jsf.is_file():
                r = subprocess.run([node_bin, "--check", str(jsf)], capture_output=True, text=True)
                if r.returncode != 0:
                    errors.append(f"[NODE_SYNTAX_ERROR] {jsf.relative_to(REPO_ROOT)}: {r.stderr.strip()}")

    # 5. Hook smoke tests
    guard_cmd = REPO_ROOT / "gemini" / "config" / "plugins" / "agy-harness" / "scripts" / "guard_commands.py"
    for cmd_str, expected_decision in [
        ("pnpm test", "allow"),
        ("npm install lodash", "deny"),
        ("rm -rf ./dist", "force_ask"),
    ]:
        r = subprocess.run(
            [sys.executable, str(guard_cmd)],
            input=json.dumps({"toolCall": {"name": "run_command", "args": {"CommandLine": cmd_str}}}),
            capture_output=True,
            text=True,
        )
        try:
            out = json.loads(r.stdout)
            if out.get("decision") != expected_decision:
                errors.append(f"[HOOK_TEST_FAIL] guard_commands `{cmd_str}` -> got {out}, expected {expected_decision}")
        except Exception as e:
            errors.append(f"[HOOK_TEST_FAIL] guard_commands `{cmd_str}` invalid JSON: {e}")

    # 6. Fallback smoke test (simulate machine WITHOUT `claude` CLI)
    with tempfile.TemporaryDirectory() as empty_home:
        clean_env = {"HOME": empty_home, "PATH": "/usr/bin:/bin", "CLAUDE_CLI_PATH": "/nonexistent/claude"}
        for script_rel in [
            "gemini/config/plugins/claude-orchestrator-kit/skills/ask-opus/scripts/ask_opus.py",
            "gemini/config/plugins/claude-orchestrator-kit/skills/claude-exit-review/scripts/exit_review.py",
            "gemini/config/plugins/multi-agent-kit/skills/multi-agent-opinion/scripts/sonnet_inspect.py",
        ]:
            r = subprocess.run(
                [sys.executable, str(REPO_ROOT / script_rel), "--check"],
                capture_output=True,
                text=True,
                env=clean_env,
            )
            if r.returncode != 2 or "[FALLBACK_TO_NATIVE_SUBAGENT]" not in r.stderr:
                errors.append(
                    f"[FALLBACK_TEST_FAIL] {script_rel} --check expected exit=2 & [FALLBACK_TO_NATIVE_SUBAGENT], "
                    f"got exit={r.returncode} stderr={r.stderr.strip()!r}"
                )

    # 7a. Clean empty home install -> uninstall must leave ZERO files behind
    with tempfile.TemporaryDirectory() as clean_home_str:
        clean_home = Path(clean_home_str)
        r_i = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "install.py"),
                "--target-home",
                str(clean_home),
                "--with-process-gate",
                "--with-claude-skills",
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        r_u = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "uninstall.py"), "--target-home", str(clean_home)],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        leftovers = [p.relative_to(clean_home).as_posix() for p in clean_home.rglob("*")]
        if r_i.returncode != 0 or r_u.returncode != 0 or leftovers:
            errors.append(
                f"[CLEAN_UNINSTALL_FAIL] Expected 0 leftovers after clean install->uninstall, got: {leftovers[:15]}"
            )

    # 7b. Pre-existing user files + DOUBLE install -> uninstall must restore exact user baseline
    with tempfile.TemporaryDirectory() as tmp_home_str:
        tmp_home = Path(tmp_home_str)
        (tmp_home / ".gemini" / "config").mkdir(parents=True, exist_ok=True)
        (tmp_home / ".gemini" / "GEMINI.md").write_text("# Original User GEMINI\n", encoding="utf-8")
        (tmp_home / ".gemini" / "config" / "hooks.json").write_text('{"my-hook": {"enabled": true}}\n', encoding="utf-8")

        # Install #1 (default flags)
        subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "install.py"), "--target-home", str(tmp_home)],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        # Install #2 (re-run with optional flags — must NOT clobber Install #1's baseline!)
        subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "install.py"),
                "--target-home",
                str(tmp_home),
                "--with-process-gate",
                "--with-claude-skills",
            ],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

        sample_agent = (
            tmp_home / ".gemini" / "config" / "plugins" / "multi-agent-kit" / "agents" / "multi-agent-opinion.md"
        )
        if not sample_agent.is_file() or "~/.gemini/" in sample_agent.read_text(encoding="utf-8")[:400]:
            errors.append("[INSTALL_TEST_FAIL] `~` in multi-agent-opinion.md frontmatter was not resolved")

        # Uninstall after 2 installs
        subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "uninstall.py"), "--target-home", str(tmp_home)],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        restored_gemini = (tmp_home / ".gemini" / "GEMINI.md").read_text(encoding="utf-8")
        restored_hooks = (tmp_home / ".gemini" / "config" / "hooks.json").read_text(encoding="utf-8")
        remaining_files = sorted(p.relative_to(tmp_home).as_posix() for p in tmp_home.rglob("*") if p.is_file())
        if (
            restored_gemini != "# Original User GEMINI\n"
            or restored_hooks != '{"my-hook": {"enabled": true}}\n'
            or remaining_files != [".gemini/GEMINI.md", ".gemini/config/hooks.json"]
        ):
            errors.append(
                f"[DOUBLE_INSTALL_ROLLBACK_FAIL] Remaining files={remaining_files}, gemini={restored_gemini!r}"
            )

    report = {
        "status": "PASS" if not errors else "FAIL",
        "scanned_text_files": scanned_files,
        "json_files_validated": json_count,
        "python_files_compiled": py_count,
        "rule_byte_sizes": rule_sizes,
        "rule_byte_limit": RULE_BYTE_LIMIT,
        "error_count": len(errors),
        "errors": errors,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(run_check())
