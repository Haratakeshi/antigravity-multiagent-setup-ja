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
6. Fallback smoke tests: ask_fable.py / exit_review.py / sonnet_inspect.py emit [FALLBACK_TO_NATIVE_SUBAGENT] when claude CLI is absent
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
        ("timeout 30 rm -rf ./dist", "force_ask"),
        ("gtimeout 10 rm -rf ./dist", "force_ask"),
        ("pnpm --dir ./app exec vitest src/foo.test.ts", "deny"),
        ("pnpm --dir ./app exec vitest run src/foo.test.ts", "allow"),
        ("vitest --watch", "deny"),
        ("pnpm exec jest --watch", "deny"),
        ("pnpm exec playwright test --ui", "deny"),
        ("pnpm exec playwright test --timeout=30000", "allow"),
        ("uv run pytest --pdb", "deny"),
        ("uv run pytest -q", "allow"),
        ("tail -f /tmp/server.log", "deny"),
        ("tail -100f /tmp/server.log", "deny"),
        ("env FOO=1 nohup tail -f /tmp/server.log", "deny"),
        ("bash -c 'tail -f /tmp/server.log'", "deny"),
        ("python3 -m pytest --pdb", "deny"),
        ("uv run python -m pytest --pdb", "deny"),
        ("pnpm exec playwright test --ui-port=8080", "deny"),
        ("pnpm exec vitest -t run", "deny"),
        ("pnpm dlx vitest@2.1.0", "deny"),
        ("pnpm dlx vitest@2.1.0 run", "allow"),
        ("tail -n 50 /tmp/server.log", "allow"),
        ("timeout 5 tail -f /tmp/server.log", "allow"),
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

        # Seed stale (>14d) and fresh state files to verify 24h-gated GC in session_context.py
        agy_state = tmp / "state" / "agy-harness"
        agy_state.mkdir(parents=True, exist_ok=True)
        stale_f = agy_state / "stale-old.count"
        fresh_f = agy_state / "fresh-keep.stopgate"
        stale_f.write_text("3", encoding="utf-8")
        fresh_f.write_text("1", encoding="utf-8")
        old_ts = os.path.getmtime(stale_f) - (20 * 86400)
        os.utime(stale_f, (old_ts, old_ts))

        # session_context.py test (n==0 injects systemMessage + prunes stale state, n==1 emits {})
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
            if stale_f.exists() or not fresh_f.exists() or not (agy_state / ".last_gc").exists():
                errors.append(
                    f"[HOOK_TEST_FAIL] session_context GC failed: stale_exists={stale_f.exists()}, fresh_exists={fresh_f.exists()}"
                )
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

        # process-review-gate.mjs test (Smart Exit Gate: Tier 0 allow, empty catch, >=25 lines, >=2 files, flash-implementer, Round-2, post-audit minor fix, dirty-worktree isolation)
        git_bin = shutil.which("git")
        proc_mjs = REPO_ROOT / "gemini" / "scripts" / "process-review-gate.mjs"
        if node_bin and git_bin and proc_mjs.is_file():
            gws = tmp / "gitws"
            (gws / "src").mkdir(parents=True)
            subprocess.run([git_bin, "init", "-q"], cwd=gws, check=True)
            ptp = tmp / "proc_transcript.jsonl"
            src_file = str(gws / "src" / "app.ts")
            src_file2 = str(gws / "src" / "helper.ts")
            readme_file = str(gws / "README.md")

            def run_proc(cid: str) -> dict:
                rp = subprocess.run(
                    [node_bin, str(proc_mjs)],
                    input=json.dumps({
                        "terminationReason": "NO_TOOL_CALL",
                        "fullyIdle": True,
                        "conversationId": cid,
                        "transcriptPath": str(ptp),
                        "artifactDirectoryPath": str(art_dir),
                        "workspacePaths": [str(gws)],
                    }),
                    capture_output=True,
                    text=True,
                    env=env,
                )
                return json.loads(rp.stdout)

            # 1. Subagent transcript (NO USER_INPUT, WITH CHECKPOINT + 30-line src/ edit) -> allow
            thirty_lines = "\n".join(f"export const v{idx} = {idx};" for idx in range(30)) + "\n"
            (gws / "src" / "app.ts").write_text(thirty_lines, encoding="utf-8")
            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "SYSTEM_MESSAGE", "content": "subagent task"}),
                    json.dumps({"type": "CHECKPOINT", "content": "compacted"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": json.dumps(src_file), "CodeContent": json.dumps(thirty_lines)}}],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            if run_proc("proc-subagent-cp").get("decision") != "allow":
                errors.append("[HOOK_TEST_FAIL] process-review-gate subagent+CHECKPOINT expected allow")

            # 2. Tier 0: Single-file trivial edit (<25 lines, no flash-implementer, no empty catch) -> allow
            small_code = "export const x = 1;\nexport const y = 2;\n"
            (gws / "src" / "app.ts").write_text(small_code, encoding="utf-8")
            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "tweak"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "write_file", "args": {"Path": json.dumps(src_file), "Content": json.dumps(small_code)}}],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            if run_proc("proc-tier0-small").get("decision") != "allow":
                errors.append("[HOOK_TEST_FAIL] process-review-gate Tier 0 small single-file edit expected allow")

            # 3. Empty catch {} even in a tiny 2-line edit -> continue (catchgate)
            bad_catch_code = "try { work(); }\ncatch (e) {}\n"
            (gws / "src" / "app.ts").write_text(bad_catch_code, encoding="utf-8")
            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "quick fix"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "write_file", "args": {"Path": json.dumps(src_file), "Content": json.dumps(bad_catch_code)}}],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            out_catch = run_proc("proc-empty-catch")
            if out_catch.get("decision") != "continue" or "catch" not in out_catch.get("reason", ""):
                errors.append(f"[HOOK_TEST_FAIL] process-review-gate empty catch expected continue, got {out_catch}")

            # 4. Single-file >=25 lines logic edit without audit -> continue (procgate)
            (gws / "src" / "app.ts").write_text(thirty_lines, encoding="utf-8")
            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "implement feature"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "write_file", "args": {"Path": json.dumps(src_file), "Content": json.dumps(thirty_lines)}}],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            out_lines = run_proc("proc-30-lines")
            if out_lines.get("decision") != "continue" or "30 行" not in out_lines.get("reason", ""):
                errors.append(f"[HOOK_TEST_FAIL] process-review-gate >=25 lines expected continue, got {out_lines}")

            # 5. Multi-file (>=2 code files, even 2 lines each) without audit -> continue
            (gws / "src" / "app.ts").write_text(small_code, encoding="utf-8")
            (gws / "src" / "helper.ts").write_text(small_code, encoding="utf-8")
            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "edit two files"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [
                            {"name": "write_file", "args": {"Path": json.dumps(src_file), "Content": json.dumps(small_code)}},
                            {"name": "write_file", "args": {"Path": json.dumps(src_file2), "Content": json.dumps(small_code)}},
                        ],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            out_multi = run_proc("proc-multi-file")
            if out_multi.get("decision") != "continue" or "2 ファイル" not in out_multi.get("reason", ""):
                errors.append(f"[HOOK_TEST_FAIL] process-review-gate 2-file edit expected continue, got {out_multi}")

            # 6. Delegated flash-implementer without exit audit (even small code diff) -> continue
            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "delegate impl"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "invoke_subagent", "args": {"TypeName": json.dumps("flash-implementer")}}],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            out_deleg = run_proc("proc-flash-impl")
            if out_deleg.get("decision") != "continue" or "flash-implementer" not in out_deleg.get("reason", ""):
                errors.append(f"[HOOK_TEST_FAIL] process-review-gate flash-implementer expected continue, got {out_deleg}")

            # 7. Round 1 invoke_subagent ("\"carbon-verifier\"") -> large 30-line edit -> Round 2 send_message -> allow
            audit_id = "11111111-2222-3333-4444-555555555555"
            (gws / "src" / "app.ts").write_text(thirty_lines, encoding="utf-8")
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
                        "tool_calls": [{"name": "edit_file", "args": {"Path": json.dumps(src_file), "Target": json.dumps("export const x = 1;"), "Replacement": json.dumps(thirty_lines)}}],
                    }),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "send_message", "args": {"Recipient": json.dumps(audit_id)}}],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            if run_proc("proc-round2-ok").get("decision") != "allow":
                errors.append("[HOOK_TEST_FAIL] process-review-gate Round-2 send_message expected allow")

            # 8. Post-audit minor 1-line fix (<25 lines, 1 file, no empty catch) -> allow without redundant bounce
            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "implement"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "write_file", "args": {"Path": json.dumps(src_file), "Content": json.dumps(thirty_lines)}}],
                    }),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "invoke_subagent", "args": {"TypeName": json.dumps("second-opinion-lite")}}],
                    }),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "edit_file", "args": {"Path": json.dumps(src_file), "Target": json.dumps("export const v0 = 0;"), "Replacement": json.dumps("export const v0 = 100;")}}],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            if run_proc("proc-post-audit-minor").get("decision") != "allow":
                errors.append("[HOOK_TEST_FAIL] process-review-gate post-audit minor fix expected allow")

            # 9. Pre-existing dirty src/*.ts files in worktree + Turn 2 edits only README.md -> allow
            (gws / "README.md").write_text("# Docs\n", encoding="utf-8")
            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "update docs only"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "write_file", "args": {"Path": json.dumps(readme_file), "Content": json.dumps("# Docs\n")}}],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            if run_proc("proc-dirty-wt-docs").get("decision") != "allow":
                errors.append("[HOOK_TEST_FAIL] process-review-gate docs edit on dirty worktree expected allow")

            # 10. edit_file with Edits chunk array / OldString+NewString (>=25 lines) -> continue (Fail-Closed & chunk support)
            (gws / "src" / "helper.ts").unlink(missing_ok=True)
            (gws / "src" / "app.ts").write_text(thirty_lines, encoding="utf-8")
            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "chunk edit"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{
                            "name": "edit_file",
                            "args": {
                                "Path": json.dumps(src_file),
                                "Edits": json.dumps([{"OldString": "export const v0 = 0;", "NewString": thirty_lines}]),
                            },
                        }],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            if run_proc("proc-edits-chunk").get("decision") != "continue":
                errors.append("[HOOK_TEST_FAIL] process-review-gate edit_file Edits chunk >=25 lines expected continue")

            # 11. Parallel [read_file, invoke_subagent(audit)] index alignment + Round-2 send_message -> allow;
            #     and send_message re-delegation to existing flash-implementer -> continue
            impl_id = "99999999-8888-7777-6666-555555555555"
            audit_id2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "parallel subagent"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [
                            {"name": "read_file", "args": {"Path": json.dumps(src_file)}},
                            {"name": "invoke_subagent", "args": {"TypeName": json.dumps("second-opinion-lite")}},
                            {"name": "invoke_subagent", "args": {"TypeName": json.dumps("flash-implementer")}},
                        ],
                    }),
                    json.dumps({"type": "VIEW_FILE", "content": "file body"}),
                    json.dumps({"type": "GENERIC", "content": json.dumps({"conversationId": audit_id2})}),
                    json.dumps({"type": "GENERIC", "content": json.dumps({"conversationId": impl_id})}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "edit_file", "args": {"Path": json.dumps(src_file), "Target": json.dumps("a"), "Replacement": json.dumps(thirty_lines)}}],
                    }),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "send_message", "args": {"Recipient": json.dumps(audit_id2)}}],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            if run_proc("proc-parallel-idx").get("decision") != "allow":
                errors.append("[HOOK_TEST_FAIL] process-review-gate parallel [read_file, invoke_subagent] Round-2 expected allow")

            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "turn 1"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "invoke_subagent", "args": {"TypeName": json.dumps("flash-implementer")}}],
                    }),
                    json.dumps({"type": "GENERIC", "content": json.dumps({"conversationId": impl_id})}),
                    json.dumps({"type": "USER_INPUT", "content": "turn 2 re-delegate"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "send_message", "args": {"Recipient": json.dumps(impl_id), "Message": json.dumps("implement more")}}],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            if run_proc("proc-redelegate-impl").get("decision") != "continue":
                errors.append("[HOOK_TEST_FAIL] process-review-gate send_message to flash-implementer expected continue")

            # 12. Multi-line Prettier empty catch (`catch (e) {\n}`) -> continue; comment `// avoid catch {}` -> allow
            multiline_catch = "try {\n  run();\n} catch (e) {\n}\n"
            (gws / "src" / "app.ts").write_text(multiline_catch, encoding="utf-8")
            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "multiline catch"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "write_file", "args": {"Path": json.dumps(src_file), "Content": json.dumps(multiline_catch)}}],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            out_ml_catch = run_proc("proc-ml-catch")
            if out_ml_catch.get("decision") != "continue" or "catch" not in out_ml_catch.get("reason", ""):
                errors.append(f"[HOOK_TEST_FAIL] process-review-gate multi-line empty catch expected continue, got {out_ml_catch}")

            comment_catch = "// Note: never write catch (e) {} here\nexport const safe = 1;\n"
            (gws / "src" / "app.ts").write_text(comment_catch, encoding="utf-8")
            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "comment only"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "write_file", "args": {"Path": json.dumps(src_file), "Content": json.dumps(comment_catch)}}],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            if run_proc("proc-comment-catch").get("decision") != "allow":
                errors.append("[HOOK_TEST_FAIL] process-review-gate comment mentioning catch {} expected allow")

            # 13. JSX/Vue apostrophe ("Don't") + template `${...}` empty catch -> both must be caught (continue)
            vue_file = str(gws / "src" / "Comp.vue")
            vue_catch = "<template><p>Don't close</p></template>\n<script setup>\ntry { save(); } catch (e) {}\n</script>\n"
            (gws / "src" / "Comp.vue").write_text(vue_catch, encoding="utf-8")
            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "vue catch"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "write_file", "args": {"Path": json.dumps(vue_file), "Content": json.dumps(vue_catch)}}],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            if run_proc("proc-vue-apostrophe-catch").get("decision") != "continue":
                errors.append("[HOOK_TEST_FAIL] process-review-gate Vue apostrophe + empty catch expected continue")

            tpl_catch = "export const s = `${(() => { try { return fn(); } catch {} })()}`;\n"
            (gws / "src" / "app.ts").write_text(tpl_catch, encoding="utf-8")
            (gws / "src" / "Comp.vue").unlink(missing_ok=True)
            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "tpl catch"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "write_file", "args": {"Path": json.dumps(src_file), "Content": json.dumps(tpl_catch)}}],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            if run_proc("proc-tpl-expr-catch").get("decision") != "continue":
                errors.append("[HOOK_TEST_FAIL] process-review-gate template ${...} empty catch expected continue")

            # 14. Same-line contraction + multiple JSX closing tags (`Don't` + `</div><span>`) + empty catch -> continue
            jsx_same_line = "export const View = () => { try { work(); } catch {} return <div><p>Don't</p><span>ok</span></div>; };\n"
            (gws / "src" / "app.ts").write_text(jsx_same_line, encoding="utf-8")
            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "jsx same line"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "write_file", "args": {"Path": json.dumps(src_file), "Content": json.dumps(jsx_same_line)}}],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            if run_proc("proc-jsx-sameline-catch").get("decision") != "continue":
                errors.append("[HOOK_TEST_FAIL] process-review-gate JSX same-line Don't + closing tags + catch {} expected continue")

            # 15. Committed git repository tests (M-1 split-brace / deleted-body empty catch, untouched legacy catch, M-2 same-turn git commit, MINOR-1/2/3)
            cws = tmp / "committed_ws"
            (cws / "src").mkdir(parents=True)
            subprocess.run([git_bin, "init", "-q"], cwd=cws, check=True)
            subprocess.run([git_bin, "config", "user.email", "verify@example.com"], cwd=cws, check=True)
            subprocess.run([git_bin, "config", "user.name", "Verify"], cwd=cws, check=True)
            c_app = cws / "src" / "app.ts"
            c_sub = cws / "src" / "sub.ts"
            base_app = (
                "export function legacy() {\n  try { oldCall(); } catch {}\n}\n"
                "export function runA() {\n  foo();\n}\n"
                "export function runB() {\n  try {\n    bar();\n  } catch (e) {\n    console.error(e);\n  }\n}\n"
            )
            c_app.write_text(base_app, encoding="utf-8")
            c_sub.write_text("export const sub = 1;\n", encoding="utf-8")
            subprocess.run([git_bin, "add", "."], cwd=cws, check=True)
            subprocess.run([git_bin, "commit", "-q", "-m", "init"], cwd=cws, check=True)

            def run_cproc(cid: str, include_idle: bool = True) -> dict:
                pl: dict = {
                    "terminationReason": "NO_TOOL_CALL",
                    "conversationId": cid,
                    "transcriptPath": str(ptp),
                    "artifactDirectoryPath": str(art_dir),
                    "workspacePaths": [str(cws)],
                }
                if include_idle:
                    pl["fullyIdle"] = True
                rp = subprocess.run([node_bin, str(proc_mjs)], input=json.dumps(pl), capture_output=True, text=True, env=env)
                return json.loads(rp.stdout)

            # 15a. Editing unrelated function in file that has untouched legacy `catch {}` in HEAD -> allow (Tier 0)
            c_app.write_text(base_app.replace("foo();", "foo(1);"), encoding="utf-8")
            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "small edit with legacy catch"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{
                            "name": "edit_file",
                            "args": {"Path": json.dumps(str(c_app)), "Target": json.dumps("foo();"), "Replacement": json.dumps("foo(1);")},
                        }],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            if run_cproc("proc-legacy-catch-untouched").get("decision") != "allow":
                errors.append("[HOOK_TEST_FAIL] process-review-gate untouched legacy catch in HEAD expected allow")

            # 15b. M-1 Repro A: split closing brace (`foo();\n}` -> `try { foo(); } catch (e) {\n}`) -> continue
            c_app.write_text(base_app.replace("  foo();\n}", "  try { foo(); } catch (e) {\n}"), encoding="utf-8")
            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "split brace catch"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{
                            "name": "edit_file",
                            "args": {
                                "Path": json.dumps(str(c_app)),
                                "Target": json.dumps("  foo();\n}"),
                                "Replacement": json.dumps("  try { foo(); } catch (e) {\n}"),
                            },
                        }],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            if run_cproc("proc-m1-repro-a").get("decision") != "continue":
                errors.append("[HOOK_TEST_FAIL] process-review-gate M-1 split closing brace empty catch expected continue")

            # 15c. M-1 Repro B2: pure inner deletion (`console.error(e);` -> ``) leaving surrounding `catch (e) {\n}` empty -> continue
            c_app.write_text(base_app.replace("    console.error(e);\n", ""), encoding="utf-8")
            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "delete catch body"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{
                            "name": "edit_file",
                            "args": {
                                "Path": json.dumps(str(c_app)),
                                "Target": json.dumps("    console.error(e);\n"),
                                "Replacement": json.dumps(""),
                            },
                        }],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            if run_cproc("proc-m1-repro-b2").get("decision") != "continue":
                errors.append("[HOOK_TEST_FAIL] process-review-gate M-1 deleted catch body expected continue")

            # 15d. MINOR-2/3: `senior-architect-advisor` or `exit_review.py --check` must NOT count as audit;
            #      same-step `[invoke implementer, invoke auditor]` and pathless `apply_patch` must require review
            c_app.write_text(base_app.replace("foo();", "foo(2);"), encoding="utf-8")
            c_sub.write_text("export const sub = 2;\n", encoding="utf-8")
            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "advisor + check probe"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [
                            {"name": "edit_file", "args": {"Path": json.dumps(str(c_app)), "Target": json.dumps("foo();"), "Replacement": json.dumps("foo(2);")}},
                            {"name": "edit_file", "args": {"Path": json.dumps(str(c_sub)), "Target": json.dumps("export const sub = 1;"), "Replacement": json.dumps("export const sub = 2;")}},
                        ],
                    }),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [
                            {"name": "invoke_subagent", "args": {"TypeName": json.dumps("senior-architect-advisor")}},
                            {"name": "run_command", "args": {"CommandLine": json.dumps("python3 exit_review.py --check")}},
                        ],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            if run_cproc("proc-no-fake-audit", include_idle=False).get("decision") != "continue":
                errors.append("[HOOK_TEST_FAIL] process-review-gate advisor/--check or omitted fullyIdle expected continue")

            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "same step parallel"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [
                            {"name": "invoke_subagent", "args": {"TypeName": json.dumps("flash-implementer")}},
                            {"name": "invoke_subagent", "args": {"TypeName": json.dumps("second-opinion-lite")}},
                        ],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            if run_cproc("proc-same-step-parallel").get("decision") != "continue":
                errors.append("[HOOK_TEST_FAIL] process-review-gate concurrent [implementer, auditor] expected continue")

            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "pathless patch"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "apply_patch", "args": {"raw": json.dumps("opaque patch")}}],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            if run_cproc("proc-pathless-patch").get("decision") != "continue":
                errors.append("[HOOK_TEST_FAIL] process-review-gate pathless apply_patch expected continue")

            # 15e. M-2: Same-turn edit of 2 logic files + `git commit` before stopping -> continue (not bypassed!)
            subprocess.run([git_bin, "add", "."], cwd=cws, check=True)
            subprocess.run([git_bin, "commit", "-q", "-m", "unreviewed turn commit"], cwd=cws, check=True)
            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "implement and commit"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [
                            {"name": "edit_file", "args": {"Path": json.dumps(str(c_app)), "Target": json.dumps("foo();"), "Replacement": json.dumps("foo(2);")}},
                            {"name": "edit_file", "args": {"Path": json.dumps(str(c_sub)), "Target": json.dumps("export const sub = 1;"), "Replacement": json.dumps("export const sub = 2;")}},
                        ],
                    }),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "run_command", "args": {"CommandLine": json.dumps("git commit -m 'unreviewed turn commit'")}}],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            if run_cproc("proc-m2-same-turn-commit").get("decision") != "continue":
                errors.append("[HOOK_TEST_FAIL] process-review-gate M-2 same-turn git commit expected continue")

            # 15f. CLI fallback unavailable (`[FALLBACK_TO_NATIVE_SUBAGENT]` on exit=2) must NOT count as completed audit -> continue
            ptp.write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "cli fallback"}),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [
                            {"name": "edit_file", "args": {"Path": json.dumps(str(c_app)), "Target": json.dumps("foo();"), "Replacement": json.dumps("foo(2);")}},
                            {"name": "edit_file", "args": {"Path": json.dumps(str(c_sub)), "Target": json.dumps("export const sub = 1;"), "Replacement": json.dumps("export const sub = 2;")}},
                            {"name": "run_command", "args": {"CommandLine": json.dumps("git commit -m 'unreviewed turn commit'")}},
                        ],
                    }),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "run_command", "args": {"CommandLine": json.dumps("python3 exit_review.py --repo . --criteria c.md --out o.md")}}],
                    }),
                    json.dumps({
                        "type": "GENERIC",
                        "content": "Command exited with code 2.\n[FALLBACK_TO_NATIVE_SUBAGENT] External `claude` CLI is unavailable",
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            if run_cproc("proc-cli-unavailable-fallback").get("decision") != "continue":
                errors.append("[HOOK_TEST_FAIL] process-review-gate CLI exit=2 fallback expected continue")


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

    # 6. Fallback & Anthropic ToS Guard smoke tests (6a: CLI absent, 6b: Consumer OAuth blocked, 6c: missing --human-authorized blocked, 6d: commercial API + --human-authorized runs --bare with OAuth stripped)
    external_scripts = [
        "gemini/config/plugins/claude-orchestrator-kit/skills/ask-fable/scripts/ask_fable.py",
        "gemini/config/plugins/claude-orchestrator-kit/skills/claude-exit-review/scripts/exit_review.py",
        "gemini/config/plugins/multi-agent-kit/skills/multi-agent-opinion/scripts/sonnet_inspect.py",
    ]
    if not (REPO_ROOT / "gemini/config/plugins/claude-orchestrator-kit/skills/ask-opus/SKILL.md").is_file():
        errors.append("[MISSING_SKILL] ask-opus/SKILL.md is missing")

    with tempfile.TemporaryDirectory() as empty_home:
        # 6a. CLI absent -> exit=2 & [FALLBACK_TO_NATIVE_SUBAGENT]
        clean_env = {"HOME": empty_home, "PATH": "/usr/bin:/bin", "CLAUDE_CLI_PATH": "/nonexistent/claude"}
        for script_rel in external_scripts:
            r = subprocess.run(
                [sys.executable, str(REPO_ROOT / script_rel), "--check"],
                capture_output=True,
                text=True,
                env=clean_env,
            )
            if r.returncode != 2 or "[FALLBACK_TO_NATIVE_SUBAGENT]" not in r.stderr:
                errors.append(
                    f"[FALLBACK_TEST_FAIL] 6a {script_rel} --check expected exit=2 & [FALLBACK_TO_NATIVE_SUBAGENT], "
                    f"got exit={r.returncode} stderr={r.stderr.strip()!r}"
                )

        # Prepare mock `claude` binary + git repo fixture for 6b / 6c / 6d
        eh = Path(empty_home)
        mock_cli = eh / "mock_claude"
        marker_file = eh / "claude_invoked.json"
        mock_cli.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            f"pathlib.Path({str(marker_file)!r}).write_text(json.dumps({{\n"
            "    'argv': sys.argv[1:],\n"
            "    'has_oauth': 'CLAUDE_CODE_OAUTH_TOKEN' in os.environ,\n"
            "    'has_api_key': bool(os.environ.get('ANTHROPIC_API_KEY')),\n"
            "}), encoding='utf-8')\n"
            "print(json.dumps({\n"
            "    'result': 'Mock advice\\n[VERIFICATION: CONFIRMED] [LOGIC] [MAJOR] a.ts:1 - ok\\nVERDICT: PASS',\n"
            "    'is_error': False,\n"
            "    'modelUsage': {'claude-fable-5-2': {'inputTokens': 10, 'outputTokens': 10}},\n"
            "    'total_cost_usd': 0.01,\n"
            "}))\n",
            encoding="utf-8",
        )
        mock_cli.chmod(0o755)

        fx_repo = eh / "fx_repo"
        fx_repo.mkdir()
        git_bin_fx = shutil.which("git")
        if git_bin_fx:
            subprocess.run([git_bin_fx, "init", "-q"], cwd=fx_repo, check=True)
            subprocess.run([git_bin_fx, "config", "user.email", "t@example.com"], cwd=fx_repo, check=True)
            subprocess.run([git_bin_fx, "config", "user.name", "T"], cwd=fx_repo, check=True)
            (fx_repo / "a.ts").write_text("export const a = 1;\n", encoding="utf-8")
            subprocess.run([git_bin_fx, "add", "."], cwd=fx_repo, check=True)
            subprocess.run([git_bin_fx, "commit", "-q", "-m", "init"], cwd=fx_repo, check=True)

        criteria_md = eh / "criteria.md"
        criteria_md.write_text("- INV-1: ok\n", encoding="utf-8")
        findings_md = eh / "findings.md"
        findings_md.write_text("- finding 1\n", encoding="utf-8")
        out_md = eh / "out.md"

        exec_args_map = {
            external_scripts[0]: ["--topic", "arch check", "--repo", str(fx_repo), "--out", str(out_md)],
            external_scripts[1]: ["--repo", str(fx_repo), "--criteria", str(criteria_md), "--out", str(out_md)],
            external_scripts[2]: ["--findings", str(findings_md), "--repo", str(fx_repo), "--out", str(out_md)],
        }

        # 6b. Consumer OAuth only (NO commercial API key, even with --human-authorized & CLAUDE_CODE_OAUTH_TOKEN) -> MUST block without spawning claude
        oauth_only_env = {
            "HOME": empty_home,
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "CLAUDE_CLI_PATH": str(mock_cli),
            "CLAUDE_CODE_OAUTH_TOKEN": "consumer-oauth-token-must-never-be-used",
        }
        for script_rel in external_scripts:
            for extra in (["--check"], ["--human-authorized", *exec_args_map[script_rel]]):
                if git_bin_fx or "--repo" not in extra:
                    r_b = subprocess.run(
                        [sys.executable, str(REPO_ROOT / script_rel), *extra],
                        capture_output=True,
                        text=True,
                        env=oauth_only_env,
                    )
                    if (
                        r_b.returncode != 2
                        or "[BLOCKED_CONSUMER_OAUTH]" not in r_b.stderr
                        or "[FALLBACK_TO_NATIVE_SUBAGENT]" not in r_b.stderr
                        or marker_file.exists()
                    ):
                        errors.append(
                            f"[TOS_GUARD_FAIL] 6b {script_rel} {extra[0]} expected Consumer OAuth block (exit=2, zero CLI spawn), "
                            f"got exit={r_b.returncode} spawned={marker_file.exists()} stderr={r_b.stderr.strip()!r}"
                        )

        # 6c. Commercial API key in ~/.gemini/.env: --check returns 0 (no key leak), execution WITHOUT --human-authorized -> blocked (exit=2, zero spawn)
        fake_secret = "commercial-api-key-fixture-987654321"
        (eh / ".gemini").mkdir(parents=True, exist_ok=True)
        (eh / ".gemini" / ".env").write_text(f'ANTHROPIC_API_KEY="{fake_secret}"\n', encoding="utf-8")
        comm_env = {
            "HOME": empty_home,
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "CLAUDE_CLI_PATH": str(mock_cli),
            "CLAUDE_CODE_OAUTH_TOKEN": "should-be-stripped-from-child",
        }
        for script_rel in external_scripts:
            r_c_chk = subprocess.run(
                [sys.executable, str(REPO_ROOT / script_rel), "--check"],
                capture_output=True,
                text=True,
                env=comm_env,
            )
            if r_c_chk.returncode != 0 or fake_secret in (r_c_chk.stdout + r_c_chk.stderr) or marker_file.exists():
                errors.append(
                    f"[TOS_GUARD_FAIL] 6c {script_rel} --check with .env API key expected exit=0 & zero secret leak, "
                    f"got exit={r_c_chk.returncode} stdout={r_c_chk.stdout!r}"
                )
            if git_bin_fx:
                r_c_noauth = subprocess.run(
                    [sys.executable, str(REPO_ROOT / script_rel), *exec_args_map[script_rel]],
                    capture_output=True,
                    text=True,
                    env=comm_env,
                )
                if r_c_noauth.returncode != 2 or "--human-authorized" not in r_c_noauth.stderr or marker_file.exists():
                    errors.append(
                        f"[TOS_GUARD_FAIL] 6c {script_rel} without --human-authorized expected exit=2 & zero CLI spawn, "
                        f"got exit={r_c_noauth.returncode} spawned={marker_file.exists()}"
                    )

                # 6d. Commercial API key + --human-authorized -> exit=0, --bare passed, CLAUDE_CODE_OAUTH_TOKEN stripped
                marker_file.unlink(missing_ok=True)
                r_d = subprocess.run(
                    [sys.executable, str(REPO_ROOT / script_rel), "--human-authorized", *exec_args_map[script_rel]],
                    capture_output=True,
                    text=True,
                    env=comm_env,
                )
                if r_d.returncode != 0 or not marker_file.is_file():
                    errors.append(
                        f"[TOS_GUARD_FAIL] 6d {script_rel} authorized commercial run expected exit=0, "
                        f"got exit={r_d.returncode} stderr={r_d.stderr.strip()!r}"
                    )
                else:
                    inv = json.loads(marker_file.read_text(encoding="utf-8"))
                    if "--bare" not in inv.get("argv", []) or inv.get("has_oauth") is not False or inv.get("has_api_key") is not True:
                        errors.append(f"[TOS_GUARD_FAIL] 6d {script_rel} expected --bare & stripped OAuth, got {inv}")
                marker_file.unlink(missing_ok=True)

    # 7a. Clean empty home default install (verifies process-review-gate auto-installed when node exists) -> uninstall must leave ZERO files behind
    with tempfile.TemporaryDirectory() as clean_home_str:
        clean_home = Path(clean_home_str)
        r_i = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "install.py"),
                "--target-home",
                str(clean_home),
                "--with-claude-skills",
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if node_bin:
            if not (clean_home / ".gemini" / "scripts" / "process-review-gate.mjs").is_file() or not (
                clean_home / ".gemini" / "config" / "hooks.json"
            ).is_file():
                errors.append("[DEFAULT_INSTALL_FAIL] Expected process-review-gate.mjs and hooks.json installed by default when node is available")
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
