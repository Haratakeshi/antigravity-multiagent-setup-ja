#!/usr/bin/env python3
# agy-harness: スマート完了ゲート (Stop)
# Antigravity v2.30+ ("NO_TOOL_CALL" / 旧 "model_stop") 対応。
# 以下のケースでは無駄な再推論ターンを発生させず即座に allow 素通しする:
#   1. サブエージェントのトランスクリプト（USER_INPUT が存在しない — CHECKPOINT 有無に関わらず全件素通し）
#   2. 直近の USER_INPUT 以降に実ファイル変更（直接編集・パッチ適用・実装サブエージェント委譲）を行っていない会話
#   3. 最終 PLANNER_RESPONSE に既に `GOAL_COMPLETE` / `pending_human_review` / `status: unverified` が含まれている場合
import json
import os
import re
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


def allow():
    sys.stdout.write(json.dumps({"decision": "allow"}, ensure_ascii=False))
    sys.exit(0)


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


def inspect_turn(transcript_path: str, artifact_dir: str, ws_dir: str) -> tuple[bool, bool]:
    """Return (mutated_real_file_in_turn, last_response_has_marker)."""
    tp = Path(transcript_path) if transcript_path else None
    if not tp or not tp.is_file():
        return False, True

    try:
        lines = tp.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False, True

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
        return False, True

    turn_steps = steps[last_user_idx + 1 :]
    art_prefixes = build_artifact_prefixes(artifact_dir)
    norm_ws = os.path.realpath(os.path.expanduser(ws_dir)) if ws_dir else ""
    mutated = False
    last_planner = None

    for st in turn_steps:
        if st.get("type") == "PLANNER_RESPONSE":
            last_planner = st
        for tc in st.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            name = str(tc.get("name") or tc.get("toolName") or "")
            args = tc.get("args") if isinstance(tc.get("args"), dict) else {}

            if name == "invoke_subagent":
                sub_type = unwrap_arg(args.get("TypeName"))
                if sub_type in DELEGATED_MUTATION_SUBAGENTS:
                    mutated = True
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

    if not mutated:
        return False, True

    content = str((last_planner or {}).get("content") or "")
    if last_planner and "content" in (last_planner.get("truncated_fields") or []):
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
    return True, has_marker


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

ws_paths = payload.get("workspacePaths")
ws_first = str(ws_paths[0]) if isinstance(ws_paths, list) and ws_paths else ""

mutated, has_marker = inspect_turn(
    str(payload.get("transcriptPath", "")),
    str(payload.get("artifactDirectoryPath", "")),
    ws_first,
)
if not mutated or has_marker:
    allow()

state_dir = os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
    "agy-harness",
)
os.makedirs(state_dir, exist_ok=True)
gate_file = os.path.join(state_dir, cid + ".stopgate")

now = int(time.time())
if os.path.exists(gate_file):
    try:
        with open(gate_file, encoding="utf-8") as f:
            last = int(f.read().strip())
        if now - last < 1800:
            allow()
    except Exception:
        pass
with open(gate_file, "w", encoding="utf-8") as f:
    f.write(str(now))

reason = (
    "[agy-harness 完了ゲート] ファイル変更を伴う作業ですが、完了前セルフレビューマーカーが未出力です。"
    "GEMINI.md §0.5 に従い、(1) 要求項目ごとの 済/未/不能、(2) コマンド+exit code または file:line の客観証拠、"
    "(3) 完了マーカー (`<!-- GOAL_COMPLETE -->` / `pending_human_review` / `status: unverified`) を含めて報告してください。"
)
sys.stdout.write(json.dumps({"decision": "continue", "reason": reason}, ensure_ascii=False))
sys.exit(0)
