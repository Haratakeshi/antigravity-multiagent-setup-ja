#!/usr/bin/env python3
# agy-harness: スマート完了ゲート & 未追跡バイナリ大量放置ガード (Stop)
# Antigravity v2.30+ ("NO_TOOL_CALL" / 旧 "model_stop") 対応。
# 以下のケースでは無駄な再推論ターンを発生させず即座に allow 素通しする:
#   1. サブエージェントのトランスクリプト（USER_INPUT が存在しない — CHECKPOINT 有無に関わらず全件素通し）
#   2. 直近の USER_INPUT 以降に実ファイル変更もコマンド実行（委譲ランナー含む）も行っていない会話
#   3. 未追跡(??)バイナリの大量放置（30件以上）がなく、かつ実ファイル変更がないか完了マーカー出力済み
#      （または AGY_SKIP_STOP_MARKER_GATE=1 指定時）の場合
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

MUTATION_TOOLS = {
    "write_file",
    "write_to_file",
    "create_file",
    "edit_file",
    "replace_file_content",
    "multi_replace_file_content",
    "multi_edit",
    "apply_diff",
    "apply_patch",
    "delete_file",
    "remove_file",
}
DELEGATED_MUTATION_SUBAGENTS = {"flash-implementer", "self"}
DELEGATED_COMMAND_SUBAGENTS = {"flash-runner", "flash-e2e-verifier"}
COMMAND_TOOLS = {"run_command"}
PATH_ARG_KEYS = (
    "Path",
    "TargetFile",
    "AbsolutePath",
    "FilePath",
    "TargetPath",
    "path",
    "file_path",
)
COMPLETION_MARKERS = (
    "GOAL_COMPLETE",
    "pending_human_review",
    "status: unverified",
)
BINARY_EXTS = {
    ".gz",
    ".tgz",
    ".bz2",
    ".xz",
    ".zst",
    ".zip",
    ".7z",
    ".rar",
    ".tar",
    ".bin",
    ".dat",
    ".wasm",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".ico",
    ".mp3",
    ".ogg",
    ".wav",
    ".mp4",
    ".webm",
    ".pdf",
    ".sqlite",
    ".db",
    ".parquet",
    ".npy",
    ".npz",
    ".onnx",
    ".pt",
    ".safetensors",
}
UNTRACKED_BINARY_THRESHOLD = 30
MAX_UNTRACKED_WALK_ENTRIES = 10_000


def allow():
    sys.stdout.write(json.dumps({"decision": "allow"}, ensure_ascii=False))
    sys.exit(0)


def check_cooldown(state_dir: str, cid: str, suffix: str) -> bool:
    os.makedirs(state_dir, exist_ok=True)
    gate_file = os.path.join(state_dir, f"{cid}.{suffix}")
    now = int(time.time())
    if os.path.exists(gate_file):
        try:
            with open(gate_file, encoding="utf-8") as f:
                last = int(f.read().strip())
            if now - last < 1800:
                return True
        except Exception:
            pass
    try:
        with open(gate_file, "w", encoding="utf-8") as f:
            f.write(str(now))
    except Exception:
        pass
    return False


def unwrap_arg(val) -> str:
    """Unwrap protojson/stringified JSON values in transcript.jsonl tool_calls.args (e.g. '"foo"' -> 'foo')."""
    if not isinstance(val, str):
        return ""
    s = val.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        try:
            parsed = json.loads(s)
            if isinstance(parsed, str):
                return parsed.strip()
        except Exception:
            return s[1:-1].strip()
    return s


def resolve_target_path(raw_target: str, ws_dir: str) -> str:
    expanded = os.path.expanduser(raw_target)
    if not os.path.isabs(expanded) and ws_dir:
        expanded = os.path.join(ws_dir, expanded)
    return os.path.realpath(expanded)


def build_artifact_prefixes(artifact_dir: str) -> tuple[str, ...]:
    if not artifact_dir:
        return ()
    real_art = Path(os.path.realpath(os.path.expanduser(artifact_dir)))
    prefixes = [str(real_art) + os.sep]
    if real_art.parent.name == "brain":
        prefixes.append(str(real_art.parent) + os.sep)
    return tuple(prefixes)


def detect_untracked_binaries(ws_dir: str) -> list[str]:
    """Return untracked ('?? ') binary/archive file paths in workspace (tracked & gitignored files are naturally excluded)."""
    if not ws_dir:
        return []
    ws_real = os.path.realpath(os.path.expanduser(ws_dir))
    if not os.path.isdir(ws_real):
        return []
    git_bin = shutil.which("git")
    if not git_bin:
        return []
    cmd = [
        git_bin,
        "--no-optional-locks",
        "-C",
        ws_real,
        "-c",
        "core.quotePath=false",
        "status",
        "--porcelain",
        "-z",
        "-unormal",
    ]
    try:
        # Single fast git call (-z avoids C-quoting bugs; -unormal avoids full external SSD directory scans)
        fast = subprocess.run(
            cmd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=2,
        )
    except Exception:
        return []
    if fast.returncode != 0:
        return []

    found: list[str] = []
    untracked_dirs: list[str] = []
    for entry in fast.stdout.split("\x00"):
        if not entry.startswith("?? "):
            continue
        rel = entry[3:]
        if not rel:
            continue
        if rel.endswith("/"):
            untracked_dirs.append(rel.rstrip("/"))
            continue
        ext = os.path.splitext(rel)[1].lower()
        if ext in BINARY_EXTS:
            found.append(rel)
            if len(found) >= UNTRACKED_BINARY_THRESHOLD:
                return found

    # Also inspect top-level untracked dirs containing a nested .git/ (which `git status -unormal` omits silently)
    try:
        with os.scandir(ws_real) as it:
            for de in it:
                if de.name == ".git" or de.is_symlink() or not de.is_dir(follow_symlinks=False):
                    continue
                if de.name not in untracked_dirs and os.path.exists(os.path.join(de.path, ".git")):
                    r_trk = subprocess.run(
                        [git_bin, "--no-optional-locks", "-C", ws_real, "ls-files", "--error-unmatch", "--", de.name],
                        capture_output=True,
                        timeout=1,
                    )
                    if r_trk.returncode != 0:
                        untracked_dirs.append(de.name)
    except Exception:
        pass

    # Bounded walk only inside untracked directories (handles nested .git/ and stops immediately at threshold)
    scanned = 0
    for rel_dir in untracked_dirs:
        abs_dir = os.path.join(ws_real, rel_dir)
        if os.path.islink(abs_dir) or not os.path.isdir(abs_dir):
            continue
        for root, dirs, files in os.walk(abs_dir, followlinks=False):
            dirs[:] = [d for d in dirs if d != ".git"]
            for fname in files:
                scanned += 1
                ext = os.path.splitext(fname)[1].lower()
                if ext in BINARY_EXTS:
                    full_p = os.path.join(root, fname)
                    found.append(os.path.relpath(full_p, ws_real))
                    if len(found) >= UNTRACKED_BINARY_THRESHOLD:
                        return found
                if scanned >= MAX_UNTRACKED_WALK_ENTRIES:
                    return found
    return found


def inspect_turn(transcript_path: str, artifact_dir: str, ws_dir: str) -> tuple[bool, bool, bool, bool, bool]:
    """Return (is_main_turn, mutated_real_file, ran_command, has_marker, is_human_gate)."""
    tp = Path(transcript_path) if transcript_path else None
    if not tp or not tp.is_file():
        return False, False, False, True, False

    try:
        lines = tp.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False, False, False, True, False

    steps = []
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            steps.append(json.loads(raw))
        except Exception:
            continue

    last_user_idx = -1
    for i, st in enumerate(steps):
        if st.get("type") == "USER_INPUT":
            last_user_idx = i

    # Subagent transcripts never contain USER_INPUT (even when CHECKPOINT is present)
    if last_user_idx < 0:
        return False, False, False, True, False

    turn_steps = steps[last_user_idx + 1 :]
    art_prefixes = build_artifact_prefixes(artifact_dir)
    norm_ws = os.path.realpath(os.path.expanduser(ws_dir)) if ws_dir else ""
    mutated = False
    ran_command = False
    last_planner = None

    for st in turn_steps:
        if st.get("type") == "PLANNER_RESPONSE":
            last_planner = st
        for tc in st.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            name = str(tc.get("name") or tc.get("toolName") or "")
            args = tc.get("args") if isinstance(tc.get("args"), dict) else {}

            if name in COMMAND_TOOLS:
                ran_command = True
                continue

            if name == "invoke_subagent":
                sub_type = unwrap_arg(args.get("TypeName"))
                if sub_type in DELEGATED_MUTATION_SUBAGENTS:
                    mutated = True
                elif sub_type in DELEGATED_COMMAND_SUBAGENTS:
                    ran_command = True
                continue

            if name not in MUTATION_TOOLS:
                continue

            target = ""
            for k in PATH_ARG_KEYS:
                v = unwrap_arg(args.get(k))
                if v:
                    target = v
                    break

            if target and art_prefixes:
                abs_t = resolve_target_path(target, norm_ws)
                if any(abs_t.startswith(pfx) for pfx in art_prefixes):
                    continue
            mutated = True

    content = str((last_planner or {}).get("content") or "")
    if (mutated or ran_command) and last_planner and "content" in (last_planner.get("truncated_fields") or []):
        full_tp = tp.with_name("transcript_full.jsonl")
        target_idx = last_planner.get("step_index")
        if full_tp.is_file():
            try:
                for raw in reversed(full_tp.read_text(encoding="utf-8", errors="replace").splitlines()):
                    if not raw.strip():
                        continue
                    obj = json.loads(raw)
                    if obj.get("step_index") == target_idx and obj.get("type") == "PLANNER_RESPONSE":
                        content = str(obj.get("content") or content)
                        break
            except Exception:
                pass

    has_marker = any(m in content for m in COMPLETION_MARKERS)
    is_human_gate = "pending_human_review" in content
    return True, mutated, ran_command, has_marker, is_human_gate


try:
    payload = json.load(sys.stdin)
except Exception:
    allow()

term = str(payload.get("terminationReason", ""))
if term not in ("NO_TOOL_CALL", "model_stop"):
    allow()
if payload.get("fullyIdle") is not True:
    allow()

raw_cid = str(payload.get("conversationId", "")).strip()
cid = re.sub(r"[^A-Za-z0-9_-]", "_", raw_cid)
if not cid:
    allow()

raw_ws_paths = payload.get("workspacePaths")
ws_list = [str(p) for p in raw_ws_paths if p] if isinstance(raw_ws_paths, list) else []
ws_first = ws_list[0] if ws_list else ""

is_main, mutated, ran_command, has_marker, is_human_gate = inspect_turn(
    str(payload.get("transcriptPath", "")),
    str(payload.get("artifactDirectoryPath", "")),
    ws_first,
)
if not is_main or is_human_gate or (not mutated and not ran_command):
    allow()

state_dir = os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
    "agy-harness",
)

# 1. Untracked bulk binary storm guard across all workspace roots (protects against Language Server repo.go UTF-8 patch storm)
untracked_bins: list[str] = []
for ws_root in ws_list:
    untracked_bins.extend(detect_untracked_binaries(ws_root))
    if len(untracked_bins) >= UNTRACKED_BINARY_THRESHOLD:
        break

if len(untracked_bins) >= UNTRACKED_BINARY_THRESHOLD:
    if not check_cooldown(state_dir, cid, "bingate"):
        samples = ", ".join(untracked_bins[:3])
        bin_reason = (
            f"[agy-harness 未追跡バイナリ検知] ワークスペース内に未追跡(??)のバイナリ/圧縮ファイルが {len(untracked_bins)} 件以上放置されています"
            f"（例: {samples}）。Antigravity の UTF-8 差分生成ストーム(repo.go)を防ぐため、"
            "(A) リポジトリへ収録する意図のデータなら `git add`（必要に応じ `.gitattributes` で `binary` 指定）、"
            "(B) 一時生成物・キャッシュなら `/tmp` 等への移動または該当ディレクトリの `.gitignore` 追加を行ってから完了してください。"
        )
        sys.stdout.write(json.dumps({"decision": "continue", "reason": bin_reason}, ensure_ascii=False))
        sys.exit(0)

# 2. Completion self-review marker gate (only when real workspace files were mutated; opt-out via AGY_SKIP_STOP_MARKER_GATE=1)
if os.environ.get("AGY_SKIP_STOP_MARKER_GATE") == "1":
    allow()

if not mutated or has_marker:
    allow()

if check_cooldown(state_dir, cid, "stopgate"):
    allow()

reason = (
    "[agy-harness 完了ゲート] ファイル変更を伴う作業ですが、完了前セルフレビューマーカーが未出力です。"
    "GEMINI.md §0.5 に従い、(1) 要求項目ごとの 済/未/不能、(2) コマンド+exit code または file:line の客観証拠、"
    "(3) 完了マーカー (`<!-- GOAL_COMPLETE -->` / `pending_human_review` / `status: unverified`) を含めて報告してください。"
)
sys.stdout.write(json.dumps({"decision": "continue", "reason": reason}, ensure_ascii=False))
sys.exit(0)
