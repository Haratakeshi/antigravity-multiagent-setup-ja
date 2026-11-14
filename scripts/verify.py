#!/usr/bin/env python3
"""Comprehensive repository & installation verifier for antigravity-multiagent-setup-ja.

Checks:
1. Rule file byte limits: Every GEMINI.md and AGENTS.md strictly < 24,000 bytes
2. Sanitization audit: Scans ALL files (including verify.py itself) for hardcoded local user/volume paths,
   secret token formats, or extra caller-supplied regexes (via EXTRA_FORBIDDEN_REGEX env var)
3. Working tree hygiene: Ensures zero `__pycache__`, `.pyc`, or `.DS_Store` files exist in the repository
4. Syntax & schema check: All JSON files parse, all Markdown YAML frontmatters parse cleanly,
   all Python files compile cleanly into a temp dir, and JS/MJS scripts pass `node --check`
5. Hook behavior smoke tests: guard_commands.py, session_context.py, stop_selfreview.py, and process-review-gate.mjs
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
    re.compile("brain/" + r"<parent-conversation-id>"),
]


def validate_markdown_frontmatters(errors: list[str]) -> int:
    md_files = [
        p
        for p in sorted(REPO_ROOT.rglob("*.md"))
        if p.is_file() and ".git" not in p.parts
    ]
    checked = 0
    ruby_bin = shutil.which("ruby")
    for md in md_files:
        text = md.read_text(encoding="utf-8")
        if not text.startswith("---\n") and not text.startswith("---\r\n"):
            continue
        lines = text.splitlines()
        end_idx = None
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                end_idx = idx
                break
        if end_idx is None:
            errors.append(f"[FRONTMATTER_UNCLOSED] {md.relative_to(REPO_ROOT)}")
            continue
        fm_text = "\n".join(lines[1:end_idx])
        checked += 1
        if ruby_bin:
            r = subprocess.run(
                [ruby_bin, "-W0", "-ryaml", "-e", "YAML.safe_load(STDIN.read)"],
                input=fm_text,
                capture_output=True,
                text=True,
            )
            if r.returncode != 0:
                errors.append(f"[YAML_INVALID] {md.relative_to(REPO_ROOT)}: {r.stderr.strip()}")
        else:
            # Minimal cross-platform check for unquoted `: ` inside scalar values
            for lno, raw_line in enumerate(lines[1:end_idx], start=2):
                s = raw_line.strip()
                if not s or s.startswith("#") or s.startswith("-"):
                    continue
                if ":" in s:
                    _, val = s.split(":", 1)
                    val_s = val.strip()
                    if val_s and val_s not in (">-", ">", "|", "|-") and not (
                        (val_s.startswith('"') and val_s.endswith('"'))
                        or (val_s.startswith("'") and val_s.endswith("'"))
                    ):
                        if ": " in val_s:
                            errors.append(
                                f"[YAML_UNQUOTED_COLON] {md.relative_to(REPO_ROOT)}:{lno}: quote value containing `: `"
                            )
    return checked


def run_hook_smoke_tests(errors: list[str], node_bin: str | None) -> None:
    scripts_dir = REPO_ROOT / "gemini" / "config" / "plugins" / "agy-harness" / "scripts"
    guard_cmd = scripts_dir / "guard_commands.py"
    for cmd_str, expected_decision in [
        ("pnpm test", "allow"),
        ("rm -rf /tmp/agy_verify_tmp_ok", "allow"),
        ("/bin/rm -rf /private/tmp/agy_verify_tmp_ok", "allow"),
        ("mkdir -p /tmp/agy_verify_tmp_ok && rm -rf /tmp/agy_verify_tmp_ok/sub", "allow"),
        ("git commit -m 'allow /tmp rm -rf'", "allow"),
        ("npm install lodash", "deny"),
        ("schtasks /create /tn test", "deny"),
        ("rm -rf ./dist", "force_ask"),
        ("/bin/rm -rf ./dist", "force_ask"),
        ("\\rm -rf ./dist", "force_ask"),
        ('"rm" -rf ./dist', "force_ask"),
        ("rm ./important.txt", "force_ask"),
        ("rm -rf /tmp/{a,..}/etc", "force_ask"),
        ("rm -rf /tmp/[a-z]*", "force_ask"),
        ("rm -rf /tmp/*", "force_ask"),
        ("sh -c 'rm -rf ./dist' && rm -rf /tmp/ok", "force_ask"),
        ("rm -rf /tmp/a &", "force_ask"),
        ("find ./dist -delete", "force_ask"),
        ("! rm -rf ./dist", "force_ask"),
        ("{ rm -rf ./dist; }", "force_ask"),
        ("if true; then rm -rf ./dist; fi", "force_ask"),
        (">/dev/null rm -rf ./dist", "force_ask"),
        ("=rm -rf ./dist", "force_ask"),
        ("$'rm' -rf ./dist", "force_ask"),
        ("{rm,-rf,./dist}", "force_ask"),
        ("/bin/r? -rf ./dist", "force_ask"),
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

    guard_edit = scripts_dir / "guard_file_edits.py"
    for tc_obj, expected_decision in [
        ({"name": "delete_file", "args": {"Path": "/tmp/agy_verify_del_ok.txt", "toolSummary": "Clean temp"}}, "allow"),
        ({"name": "delete_file", "args": {"Paths": ["/tmp/ok.txt", "./secret.txt"]}}, "force_ask"),
        ({"name": "delete_file", "args": {"Path": "/tmp/{a,..}/secret.txt"}}, "force_ask"),
    ]:
        r_ed = subprocess.run(
            [sys.executable, str(guard_edit)],
            input=json.dumps({"toolCall": tc_obj}),
            capture_output=True,
            text=True,
        )
        try:
            out_ed = json.loads(r_ed.stdout)
            if out_ed.get("decision") != expected_decision:
                errors.append(f"[HOOK_TEST_FAIL] guard_file_edits `{tc_obj}` -> got {out_ed}, expected {expected_decision}")
        except Exception as e:
            errors.append(f"[HOOK_TEST_FAIL] guard_file_edits `{tc_obj}` invalid JSON: {e}")

    guard_url = scripts_dir / "guard_urls.py"
    for url_str, expected_decision in [
        ("http://localhost:3000/api", "allow"),
        ("http://[::1]:3000/api", "allow"),
        ("https://github.com/Haratakeshi/antigravity-multiagent-setup-ja", "allow"),
        ("http://evil.example?x=.localhost", "ask"),
        ("http://localhost:80@evil.example/pwn", "ask"),
        ("https://evil.example\\.localhost/", "ask"),
        ("HTTPS://evil.example/payload", "ask"),
    ]:
        r_u = subprocess.run(
            [sys.executable, str(guard_url)],
            input=json.dumps({"toolCall": {"name": "read_url_content", "args": {"Url": url_str}}}),
            capture_output=True,
            text=True,
        )
        try:
            out_u = json.loads(r_u.stdout)
            if out_u.get("decision") != expected_decision:
                errors.append(f"[HOOK_TEST_FAIL] guard_urls `{url_str}` -> got {out_u}, expected {expected_decision}")
        except Exception as e:
            errors.append(f"[HOOK_TEST_FAIL] guard_urls `{url_str}` invalid JSON: {e}")

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        env = {**os.environ, "XDG_STATE_HOME": str(tmp / "state"), "PYTHONDONTWRITEBYTECODE": "1"}

        # session_context.py test (n==0 injects systemMessage, n==1 emits {})
        sess_py = scripts_dir / "session_context.py"
        r0 = subprocess.run(
            [sys.executable, str(sess_py)],
            input=json.dumps({"conversationId": "conv-smoke-1"}),
            capture_output=True,
            text=True,
            env=env,
        )
        r1 = subprocess.run(
            [sys.executable, str(sess_py)],
            input=json.dumps({"conversationId": "conv-smoke-1"}),
            capture_output=True,
            text=True,
            env=env,
        )
        try:
            o0 = json.loads(r0.stdout)
            o1 = json.loads(r1.stdout)
            sm = (((o0.get("injectSteps") or [{}])[0]).get("systemMessage") or {}).get("systemMessage", "")
            if "[agy-harness]" not in sm or o1 != {}:
                errors.append(f"[HOOK_TEST_FAIL] session_context unexpected output: r0={o0}, r1={o1}")
        except Exception as e:
            errors.append(f"[HOOK_TEST_FAIL] session_context JSON error: {e}")

        # stop_selfreview.py test (Q&A allow, mutating without marker continue, mutating with GOAL_COMPLETE allow)
        stop_py = scripts_dir / "stop_selfreview.py"
        ws_dir = tmp / "ws"
        art_dir = tmp / "art"
        ws_dir.mkdir()
        art_dir.mkdir()
        tp = tmp / "transcript.jsonl"

        # Case A: Q&A only -> allow
        tp.write_text(
            "\n".join([
                json.dumps({"type": "USER_INPUT", "content": "hello"}),
                json.dumps({"type": "PLANNER_RESPONSE", "content": "hi"}),
            ]) + "\n",
            encoding="utf-8",
        )
        ra = subprocess.run(
            [sys.executable, str(stop_py)],
            input=json.dumps({
                "terminationReason": "NO_TOOL_CALL",
                "fullyIdle": True,
                "conversationId": "cid-qa",
                "transcriptPath": str(tp),
                "artifactDirectoryPath": str(art_dir),
                "workspacePaths": [str(ws_dir)],
            }),
            capture_output=True,
            text=True,
            env=env,
        )
        if json.loads(ra.stdout).get("decision") != "allow":
            errors.append(f"[HOOK_TEST_FAIL] stop_selfreview Q&A expected allow, got {ra.stdout}")

        # Case B: Real file mutation (double-quoted Path) without GOAL_COMPLETE -> continue
        target_f = str(ws_dir / "index.ts")
        tp.write_text(
            "\n".join([
                json.dumps({"type": "USER_INPUT", "content": "edit"}),
                json.dumps({
                    "type": "PLANNER_RESPONSE",
                    "content": "done without marker",
                    "tool_calls": [{"name": "write_file", "args": {"Path": json.dumps(target_f)}}],
                }),
            ]) + "\n",
            encoding="utf-8",
        )
        rb = subprocess.run(
            [sys.executable, str(stop_py)],
            input=json.dumps({
                "terminationReason": "NO_TOOL_CALL",
                "fullyIdle": True,
                "conversationId": "cid-mut-1",
                "transcriptPath": str(tp),
                "artifactDirectoryPath": str(art_dir),
                "workspacePaths": [str(ws_dir)],
            }),
            capture_output=True,
            text=True,
            env=env,
        )
        if json.loads(rb.stdout).get("decision") != "continue":
            errors.append(f"[HOOK_TEST_FAIL] stop_selfreview unreviewed mutation expected continue, got {rb.stdout}")

        # Case B2: Opt-out via AGY_SKIP_STOP_MARKER_GATE=1 -> allow
        rb_skip = subprocess.run(
            [sys.executable, str(stop_py)],
            input=json.dumps({
                "terminationReason": "NO_TOOL_CALL",
                "fullyIdle": True,
                "conversationId": "cid-mut-skip",
                "transcriptPath": str(tp),
                "artifactDirectoryPath": str(art_dir),
                "workspacePaths": [str(ws_dir)],
            }),
            capture_output=True,
            text=True,
            env={**env, "AGY_SKIP_STOP_MARKER_GATE": "1"},
        )
        if json.loads(rb_skip.stdout).get("decision") != "allow":
            errors.append(f"[HOOK_TEST_FAIL] stop_selfreview AGY_SKIP_STOP_MARKER_GATE=1 expected allow, got {rb_skip.stdout}")

        # Case C: Real file mutation WITH GOAL_COMPLETE -> allow
        tp.write_text(
            "\n".join([
                json.dumps({"type": "USER_INPUT", "content": "edit"}),
                json.dumps({
                    "type": "PLANNER_RESPONSE",
                    "content": "all verified <!-- GOAL_COMPLETE -->",
                    "tool_calls": [{"name": "write_file", "args": {"Path": json.dumps(target_f)}}],
                }),
            ]) + "\n",
            encoding="utf-8",
        )
        rc = subprocess.run(
            [sys.executable, str(stop_py)],
            input=json.dumps({
                "terminationReason": "NO_TOOL_CALL",
                "fullyIdle": True,
                "conversationId": "cid-mut-2",
                "transcriptPath": str(tp),
                "artifactDirectoryPath": str(art_dir),
                "workspacePaths": [str(ws_dir)],
            }),
            capture_output=True,
            text=True,
            env=env,
        )
        if json.loads(rc.stdout).get("decision") != "allow":
            errors.append(f"[HOOK_TEST_FAIL] stop_selfreview GOAL_COMPLETE expected allow, got {rc.stdout}")

        # Case D: Subagent transcript (NO USER_INPUT, WITH CHECKPOINT + file edit) -> allow
        tp.write_text(
            "\n".join([
                json.dumps({"type": "SYSTEM_MESSAGE", "content": "subagent prompt"}),
                json.dumps({"type": "CHECKPOINT", "content": "compacted"}),
                json.dumps({
                    "type": "PLANNER_RESPONSE",
                    "content": "finished subagent edit without marker",
                    "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": json.dumps(target_f)}}],
                }),
            ]) + "\n",
            encoding="utf-8",
        )
        rd = subprocess.run(
            [sys.executable, str(stop_py)],
            input=json.dumps({
                "terminationReason": "NO_TOOL_CALL",
                "fullyIdle": True,
                "conversationId": "cid-subagent-cp",
                "transcriptPath": str(tp),
                "artifactDirectoryPath": str(art_dir),
                "workspacePaths": [str(ws_dir)],
            }),
            capture_output=True,
            text=True,
            env=env,
        )
        if json.loads(rd.stdout).get("decision") != "allow":
            errors.append(f"[HOOK_TEST_FAIL] stop_selfreview subagent+CHECKPOINT expected allow, got {rd.stdout}")

        # Case E: Bulk untracked binaries (>=30 .json.gz) after run_command -> continue; once git-added (tracked) -> allow
        git_bin_stop = shutil.which("git")
        if git_bin_stop:
            bin_ws = tmp / "binws"
            (bin_ws / "pool").mkdir(parents=True)
            subprocess.run([git_bin_stop, "init", "-q"], cwd=bin_ws, check=True)
            for idx_b in range(32):
                (bin_ws / "pool" / f"{idx_b:04d}.json.gz").write_bytes(b"\x1f\x8b\x08\x00")
            tp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "generate boards"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "content": "generated <!-- GOAL_COMPLETE -->",
                        "tool_calls": [{"name": "run_command", "args": {"CommandLine": json.dumps("python3 gen.py")}}],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            re_untracked = subprocess.run(
                [sys.executable, str(stop_py)],
                input=json.dumps({
                    "terminationReason": "NO_TOOL_CALL",
                    "fullyIdle": True,
                    "conversationId": "cid-bin-untracked",
                    "transcriptPath": str(tp),
                    "artifactDirectoryPath": str(art_dir),
                    "workspacePaths": [str(bin_ws)],
                }),
                capture_output=True,
                text=True,
                env=env,
            )
            out_untracked = json.loads(re_untracked.stdout)
            if out_untracked.get("decision") != "continue" or "未追跡バイナリ検知" not in out_untracked.get("reason", ""):
                errors.append(f"[HOOK_TEST_FAIL] stop_selfreview 32 untracked .gz expected continue, got {re_untracked.stdout}")

            # Intentionally staged/tracked binaries in repo -> must NOT trigger (allow)
            subprocess.run([git_bin_stop, "add", "pool"], cwd=bin_ws, check=True)
            re_tracked = subprocess.run(
                [sys.executable, str(stop_py)],
                input=json.dumps({
                    "terminationReason": "NO_TOOL_CALL",
                    "fullyIdle": True,
                    "conversationId": "cid-bin-tracked",
                    "transcriptPath": str(tp),
                    "artifactDirectoryPath": str(art_dir),
                    "workspacePaths": [str(bin_ws)],
                }),
                capture_output=True,
                text=True,
                env=env,
            )
            if json.loads(re_tracked.stdout).get("decision") != "allow":
                errors.append(f"[HOOK_TEST_FAIL] stop_selfreview git-added binaries expected allow, got {re_tracked.stdout}")

            # Delegated flash-runner + quoted/nested-.git untracked dir binaries -> continue
            q_dir = bin_ws / 'dir "quoted" space'
            q_dir.mkdir()
            (q_dir / ".git").mkdir()
            for idx_q in range(31):
                (q_dir / f"item {idx_q}.gz").write_bytes(b"\x1f\x8b\x08\x00")
            tp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "delegate gen"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "content": "delegated <!-- GOAL_COMPLETE -->",
                        "tool_calls": [{"name": "invoke_subagent", "args": {"TypeName": json.dumps("flash-runner")}}],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            rf_delegated = subprocess.run(
                [sys.executable, str(stop_py)],
                input=json.dumps({
                    "terminationReason": "NO_TOOL_CALL",
                    "fullyIdle": True,
                    "conversationId": "cid-bin-delegated",
                    "transcriptPath": str(tp),
                    "artifactDirectoryPath": str(art_dir),
                    "workspacePaths": [str(bin_ws)],
                }),
                capture_output=True,
                text=True,
                env=env,
            )
            if json.loads(rf_delegated.stdout).get("decision") != "continue":
                errors.append(f"[HOOK_TEST_FAIL] stop_selfreview delegated flash-runner binaries expected continue, got {rf_delegated.stdout}")

        # process-review-gate.mjs test (un-audited src/ edit -> continue; subagent+CHECKPOINT -> allow; Round-2 send_message audit -> allow)
        git_bin = shutil.which("git")
        proc_mjs = REPO_ROOT / "gemini" / "scripts" / "process-review-gate.mjs"
        if node_bin and git_bin and proc_mjs.is_file():
            gws = tmp / "gitws"
            (gws / "src").mkdir(parents=True)
            subprocess.run([git_bin, "init", "-q"], cwd=gws, check=True)
            (gws / "src" / "app.ts").write_text("export const x = 1;\n", encoding="utf-8")
            ptp = tmp / "proc_transcript.jsonl"
            src_file = str(gws / "src" / "app.ts")

            # Subagent transcript (NO USER_INPUT, WITH CHECKPOINT + src/ edit) -> allow
            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "SYSTEM_MESSAGE", "content": "subagent task"}),
                    json.dumps({"type": "CHECKPOINT", "content": "compacted"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": json.dumps(src_file)}}],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            rp_sub = subprocess.run(
                [node_bin, str(proc_mjs)],
                input=json.dumps({
                    "terminationReason": "NO_TOOL_CALL",
                    "fullyIdle": True,
                    "conversationId": "proc-subagent-cp",
                    "transcriptPath": str(ptp),
                    "artifactDirectoryPath": str(art_dir),
                    "workspacePaths": [str(gws)],
                }),
                capture_output=True,
                text=True,
                env=env,
            )
            if json.loads(rp_sub.stdout).get("decision") != "allow":
                errors.append(
                    f"[HOOK_TEST_FAIL] process-review-gate subagent+CHECKPOINT expected allow, got {rp_sub.stdout}"
                )

            # Un-audited src/ edit -> continue
            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "implement"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "write_file", "args": {"Path": json.dumps(src_file)}}],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            rp1 = subprocess.run(
                [node_bin, str(proc_mjs)],
                input=json.dumps({
                    "terminationReason": "NO_TOOL_CALL",
                    "fullyIdle": True,
                    "conversationId": "proc-1",
                    "transcriptPath": str(ptp),
                    "artifactDirectoryPath": str(art_dir),
                    "workspacePaths": [str(gws)],
                }),
                capture_output=True,
                text=True,
                env=env,
            )
            if json.loads(rp1.stdout).get("decision") != "continue":
                errors.append(f"[HOOK_TEST_FAIL] process-review-gate un-audited expected continue, got {rp1.stdout}")

            # Round 1 invoke_subagent ("\"carbon-verifier\"") -> fix -> Round 2 send_message -> allow
            audit_id = "11111111-2222-3333-4444-555555555555"
            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "implement"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "invoke_subagent", "args": {"TypeName": json.dumps("carbon-verifier")}}],
                    }),
                    json.dumps({"type": "GENERIC", "content": json.dumps({"conversationId": audit_id})}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "edit_file", "args": {"Path": json.dumps(src_file)}}],
                    }),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "send_message", "args": {"Recipient": json.dumps(audit_id)}}],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            rp2 = subprocess.run(
                [node_bin, str(proc_mjs)],
                input=json.dumps({
                    "terminationReason": "NO_TOOL_CALL",
                    "fullyIdle": True,
                    "conversationId": "proc-2",
                    "transcriptPath": str(ptp),
                    "artifactDirectoryPath": str(art_dir),
                    "workspacePaths": [str(gws)],
                }),
                capture_output=True,
                text=True,
                env=env,
            )
            if json.loads(rp2.stdout).get("decision") != "allow":
                errors.append(f"[HOOK_TEST_FAIL] process-review-gate Round-2 send_message expected allow, got {rp2.stdout}")


def run_check() -> int:
    errors: list[str] = []

    extra_rx = os.environ.get("EXTRA_FORBIDDEN_REGEX", "").strip()
    active_patterns = list(FORBIDDEN_PATTERNS)
    if extra_rx:
        active_patterns.append(re.compile(extra_rx, re.IGNORECASE))

    # 1. Working tree & git tracking hygiene check (no __pycache__/.pyc anywhere; no tracked .DS_Store/Thumbs.db)
    for item in REPO_ROOT.rglob("*"):
        if ".git" in item.parts:
            continue
        if item.name == "__pycache__" or item.suffix in (".pyc", ".pyo"):
            errors.append(f"[HYGIENE_FAIL] Unwanted Python bytecode artifact in repo: {item.relative_to(REPO_ROOT)}")
    git_bin_hygiene = shutil.which("git")
    if git_bin_hygiene and (REPO_ROOT / ".git").exists():
        r_ls = subprocess.run(
            [git_bin_hygiene, "-C", str(REPO_ROOT), "ls-files"],
            capture_output=True,
            text=True,
        )
        if r_ls.returncode == 0:
            for tracked in r_ls.stdout.splitlines():
                tname = Path(tracked).name
                if tname in (".DS_Store", "Thumbs.db") or tracked.endswith((".pyc", ".pyo")):
                    errors.append(f"[HYGIENE_FAIL] Unwanted OS/bytecode file tracked in git: {tracked}")

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

    # 4. JSON, YAML frontmatter, Python, and Node syntax checks
    json_count = 0
    for jf in sorted(REPO_ROOT.rglob("*.json")):
        if ".git" in jf.parts:
            continue
        json_count += 1
        try:
            json.loads(jf.read_text(encoding="utf-8"))
        except Exception as e:
            errors.append(f"[JSON_INVALID] {jf.relative_to(REPO_ROOT)}: {e}")

    yaml_fm_count = validate_markdown_frontmatters(errors)

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
    run_hook_smoke_tests(errors, node_bin)

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

        subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "install.py"), "--target-home", str(tmp_home)],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
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
        "yaml_frontmatters_validated": yaml_fm_count,
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
