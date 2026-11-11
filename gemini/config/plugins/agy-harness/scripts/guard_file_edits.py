#!/usr/bin/env python3
# agy-harness: ファイル編集ガード (PreToolUse)
# 1) ファイル削除ツールは force_ask (暗黙のデータ破棄対策)
# 2) テストファイルへの編集に「テストを都合よく通すためのパターン」を検出したら ask
import json
import re
import sys


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.exit(0)


try:
    payload = json.load(sys.stdin)
    tool_name = str(payload.get("toolCall", {}).get("name", ""))
    tool_args = payload.get("toolCall", {}).get("args", {})
except Exception:
    emit({"decision": "allow"})

if not isinstance(tool_args, dict):
    emit({"decision": "allow"})

if re.search(r"delete|remove", tool_name, re.IGNORECASE):
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
