#!/usr/bin/env python3
# agy-harness: ファイル編集ガード (PreToolUse)
# 1) ファイル削除ツールは /tmp・$TMPDIR・scratch・アクティブ workspacePaths 配下の安全対象のみ allow、
#    それ以外（ワークスペース外・保護対象・シンボリックリンク等）は非ブロッキング deny ([DEFER_HUMAN_GATE])
# 2) テストファイルへの編集に「テストを都合よく通すためのパターン」を検出したら deny ([DEFER_HUMAN_GATE])
import json
import os
import re
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
from _workspace_guard import (
    FORBIDDEN_WS_CHARS,
    SCRATCH_ROOT_RE,
    is_safe_scratch_target,
    is_safe_tmp_target,
    is_safe_workspace_target,
    is_valid_env_tmpdir,
    normalize_safe_workspace_roots,
    strip_leading_dotslash,
    validate_initial_cwd_in_workspace,
    validate_rel_subpath_under_root,
    validate_workspace_rel_subpath,
    ws_relpath,
)

PATH_KEY_RE = re.compile(r"(path|file|target|dir|uri|dest|source|src)", re.IGNORECASE)
SAFE_DELETE_META_KEYS = frozenset({"toolsummary", "description", "reason", "summary"})
DEFER_DELETE_DENY_MSG = (
    "[DEFER_HUMAN_GATE] ワークスペース外・保護対象（.git/.env/ルート等）・または安全性未確認のファイル削除のため自動実行を拒否しました。"
    "ここで停止・回避工作せず、この削除操作はスキップして残りのゴール作業を進め、"
    "最終報告の「Human Gate（手動確認・実行依頼）」セクションに対象パスと理由をまとめて提示してください（pending_human_review）。"
    "ユーザーが次ターンで削除を明示承認した場合は再実行して構いません（Human Gate フックが承認を自動検証します）。"
)


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.exit(0)


def _is_safe_delete_target(
    raw_path: str | None,
    ws_roots: list[tuple[str, str]],
    cwd_pair: tuple[str, str] | None,
    env_tmpdir: str,
    env_tmpdir_valid: bool,
) -> bool:
    """Return True iff raw_path is a concrete safe path under /tmp, $TMPDIR, scratch, or active workspace."""
    if not raw_path or any(c in FORBIDDEN_WS_CHARS for c in raw_path):
        return False
    if is_safe_tmp_target(raw_path):
        return True
    if env_tmpdir_valid and env_tmpdir and raw_path.startswith(env_tmpdir + "/"):
        if validate_rel_subpath_under_root(env_tmpdir, raw_path[len(env_tmpdir) + 1 :], allow_glob=False):
            return True
    m_scr = SCRATCH_ROOT_RE.match(raw_path)
    if m_scr and m_scr.group(2) and is_safe_scratch_target(m_scr.group(1), m_scr.group(2)):
        return True
    if raw_path.startswith("/"):
        return bool(
            ws_roots and is_safe_workspace_target(raw_path, ws_roots, allow_glob=False, check_protected_anchors=True)
        )
    if cwd_pair is None or raw_path.startswith(("~", "-", "$")) or raw_path in (".", "./"):
        return False
    clean = strip_leading_dotslash(raw_path)
    if not clean:
        return False
    ws_real, cwd_real = cwd_pair
    base_rel = ws_relpath(cwd_real, ws_real)
    if base_rel is None:
        return False
    full_rel = f"{base_rel}/{clean}" if base_rel else clean
    return validate_workspace_rel_subpath(ws_real, full_rel, allow_glob=False, check_protected_anchors=True)


def _extract_delete_targets(args: dict) -> list[str] | None:
    """Collect every target path from delete/remove tool args; return None on malformed/empty path values."""
    targets: list[str] = []

    def walk_value(val, require_valid_str: bool) -> bool:
        if isinstance(val, str):
            if not val:
                return not require_valid_str
            targets.append(val)
            return True
        if isinstance(val, (list, tuple)):
            if require_valid_str and not val:
                return False
            return all(walk_value(item, require_valid_str) for item in val)
        if isinstance(val, dict):
            for k, v in val.items():
                k_str = str(k)
                if k_str.lower() in SAFE_DELETE_META_KEYS:
                    continue
                if not walk_value(v, bool(PATH_KEY_RE.search(k_str))):
                    return False
            return True
        if isinstance(val, bool) or val is None:
            return not require_valid_str
        return not require_valid_str

    if not walk_value(args, False):
        return None
    return targets if targets else None


try:
    payload = json.load(sys.stdin)
    tool_name = str(payload.get("toolCall", {}).get("name", ""))
    tool_args = payload.get("toolCall", {}).get("args", {})
    raw_ws_paths = payload.get("workspacePaths")
    ws_roots = normalize_safe_workspace_roots(raw_ws_paths)
    initial_cwd = str(payload.get("cwd") or "")
except Exception:
    emit({"decision": "allow"})

if not isinstance(tool_args, dict):
    emit({"decision": "allow"})

# --- ファイル削除は /tmp・$TMPDIR・scratch・アクティブ workspacePaths 配下の安全対象のみ allow、他は非ブロッキング deny ---
if re.search(r"delete|remove", tool_name, re.IGNORECASE):
    del_targets = _extract_delete_targets(tool_args)
    raw_env_tmpdir = os.environ.get("TMPDIR", "").rstrip("/")
    env_tmpdir_valid = is_valid_env_tmpdir(raw_env_tmpdir)
    cwd_pair = validate_initial_cwd_in_workspace(initial_cwd, ws_roots)
    if del_targets and all(
        _is_safe_delete_target(t, ws_roots, cwd_pair, raw_env_tmpdir, env_tmpdir_valid)
        for t in del_targets
    ):
        emit({"decision": "allow"})
    if del_targets:
        from _human_gate_decision import evaluate_human_gate_override

        override = evaluate_human_gate_override(
            payload,
            tool_name or "delete_file",
            json.dumps(del_targets, ensure_ascii=False),
            DEFER_DELETE_DENY_MSG,
            catastrophic_check_text="\n".join(del_targets),
            catastrophic_paths_only=True,
        )
        if override is not None:
            emit(override)
    emit({"decision": "deny", "reason": DEFER_DELETE_DENY_MSG})

strings = []


def collect(obj):
    if isinstance(obj, str):
        strings.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            collect(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            collect(v)


collect(tool_args)

path = None
for key in ("TargetFile", "AbsolutePath", "FilePath", "TargetPath", "Path",
            "file_path", "path", "FileName", "filename"):
    value = tool_args.get(key)
    if isinstance(value, str) and value:
        path = value
        break
if not path:
    for s in strings:
        if re.match(r"^(/|~|[A-Za-z]:[\\/])[^\r\n]+\.\w+$", s):
            path = s
            break
if not path:
    emit({"decision": "allow"})

TEST_PATH = (r"(^|[\\/])(__tests__|tests?|spec|e2e)([\\/])"
             r"|\.(test|spec)\.[cmjt]sx?$"
             r"|(^|[\\/])test_[^\\/]*\.py$"
             r"|_test\.(py|go|rb|exs)$"
             r"|Tests?\.(cs|java|kt|swift)$")
if not re.search(TEST_PATH, path, re.IGNORECASE):
    emit({"decision": "allow"})

content = "\n".join(strings)
PATTERNS = [
    (r"\.only\s*\(", ".only() による対象テストの絞り込み"),
    (r"\.skip\s*\(|\bxit\s*\(|\bxdescribe\s*\(|\btest\.todo\b", "テストの skip / 無効化"),
    (r"@pytest\.mark\.skip|@unittest\.skip|pytest\.skip\s*\(", "テストの skip (Python)"),
    (r"expect\s*\(\s*true\s*\)\s*\.\s*toBe\s*\(\s*true\s*\)"
     r"|expect\s*\(\s*1\s*\)\s*\.\s*toBe\s*\(\s*1\s*\)", "恒真アサーション (偽陽性)"),
    (r"\bassert\s+True\b|assertTrue\s*\(\s*(true|True)\s*\)", "恒真アサーション (偽陽性)"),
    (r"except\s*(Exception)?\s*:\s*(pass|\.\.\.)", "テスト内での例外の握りつぶし"),
    (r"process\.env\.(CI|GITHUB_ACTIONS)"
     r"|os\.environ(\.get)?\s*[\(\[]\s*.?(CI|GITHUB_ACTIONS)", "CI 環境でのみ挙動を変える分岐"),
]
hits = []
for pattern, name in PATTERNS:
    if re.search(pattern, content, re.IGNORECASE):
        if name not in hits:
            hits.append(name)

if hits:
    test_defer_reason = (
        "[DEFER_HUMAN_GATE] テストファイルへの編集に要注意パターンを検出: " + " / ".join(hits)
        + "。テストを通すためのテスト側改変は禁止です。最適解はアプリケーションコード側の修正です。"
          "どうしても意図的な skip 等が必要な場合のみここで停止せず該当編集をスキップし、最終報告の「Human Gate（手動確認・実行依頼）」へ理由を提示してください（pending_human_review）。"
          "ユーザーが次ターンで編集を明示承認した場合は再実行して構いません（Human Gate フックが承認を自動検証します）。"
    )
    from _human_gate_decision import evaluate_human_gate_override

    override = evaluate_human_gate_override(
        payload,
        tool_name or "edit_file",
        json.dumps({"path": path, "hits": hits, "tool_args": tool_args}, ensure_ascii=False),
        test_defer_reason,
        catastrophic_check_text=path,
        catastrophic_paths_only=True,
    )
    if override is not None:
        emit(override)
    emit({"decision": "deny", "reason": test_defer_reason})

emit({"decision": "allow"})
