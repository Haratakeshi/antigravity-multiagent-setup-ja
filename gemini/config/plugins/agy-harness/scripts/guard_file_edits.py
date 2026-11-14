#!/usr/bin/env python3
# agy-harness: ファイル編集ガード (PreToolUse)
# 1) ファイル削除ツールは /tmp 配下の安全な具体パスを除き force_ask (暗黙のデータ破棄対策)
# 2) テストファイルへの編集に「テストを都合よく通すためのパターン」を検出したら ask
import json
import os
import re
import sys

FORBIDDEN_TMP_CHARS = frozenset("$`\n\r\x00~*?[]{}()\\|;&<>")
PATH_KEY_RE = re.compile(r"(path|file|target|dir|uri|dest|source|src)", re.IGNORECASE)
SAFE_DELETE_META_KEYS = frozenset({"toolsummary", "description", "reason", "summary"})


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.exit(0)


def _is_safe_tmp_path(raw_path: str | None) -> bool:
    if not raw_path or any(c in FORBIDDEN_TMP_CHARS for c in raw_path):
        return False
    if not (raw_path.startswith("/tmp/") or raw_path.startswith("/private/tmp/")):
        return False
    parts = [p for p in raw_path.split("/") if p != ""]
    if "." in parts or ".." in parts:
        return False
    if parts[0] == "tmp":
        if len(parts) < 2:
            return False
        root_prefix = "/tmp"
        sub_parts = parts[1:]
    elif len(parts) >= 3 and parts[0] == "private" and parts[1] == "tmp":
        root_prefix = "/private/tmp"
        sub_parts = parts[2:]
    else:
        return False

    tmp_root_real = os.path.realpath("/tmp")
    deepest_existing = root_prefix
    cur = root_prefix
    for part in sub_parts:
        cur = cur + "/" + part
        if os.path.islink(cur):
            return False
        if os.path.exists(cur):
            deepest_existing = cur

    if os.path.exists(deepest_existing):
        real_anc = os.path.realpath(deepest_existing)
        if real_anc != tmp_root_real and not real_anc.startswith(tmp_root_real + os.sep):
            return False
    return True


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
except Exception:
    emit({"decision": "allow"})

if not isinstance(tool_args, dict):
    emit({"decision": "allow"})

# --- ファイル削除は /tmp 配下の安全な具体パスのみ allow、それ以外はすべて force_ask ---
if re.search(r"delete|remove", tool_name, re.IGNORECASE):
    del_targets = _extract_delete_targets(tool_args)
    if del_targets and all(_is_safe_tmp_path(t) for t in del_targets):
        emit({"decision": "allow"})
    emit({"decision": "force_ask",
          "reason": "ファイル削除です。対象がユーザー資産・生成物・一時ファイルのいずれか、復元手段の有無を確認してください。"})

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
    emit({"decision": "ask",
          "reason": ("テストファイルへの編集に要注意パターンを検出: " + " / ".join(hits)
                     + "。テストを通すためのテスト側改変は禁止です。最適解はアプリケーションコード側の修正。"
                       "期待値は仕様から導出し、意図的な skip 等は理由をユーザーに説明してください。")})

emit({"decision": "allow"})
