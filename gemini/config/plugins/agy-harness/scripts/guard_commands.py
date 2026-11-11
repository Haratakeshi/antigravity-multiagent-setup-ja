#!/usr/bin/env python3
# agy-harness: run_command ガード (PreToolUse)
# 禁止コマンドを deny (代替の最適解を提示)、破壊的コマンドを force_ask / ask に落とす。
import json
import re
import sys


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.exit(0)


try:
    payload = json.load(sys.stdin)
    cmd = str(payload.get("toolCall", {}).get("args", {}).get("CommandLine", ""))
except Exception:
    emit({"decision": "allow"})

if not cmd.strip():
    emit({"decision": "allow"})

# --- deny: 禁止事項。reason には代替となる最適解を必ず示す ---
B = r"(^|[\s;&|('\"`])"
DENY = [
    (B + r"npm\s+(install|i|ci|add|update|up|uninstall|rm)\b",
     "npm の直接実行は禁止です。最適解: pnpm を使う (`pnpm add <pkg>@<x.y.z>` / `pnpm install --frozen-lockfile`)。既存リポジトリが package-lock.json 運用の場合はユーザーに確認してください。"),
    (B + r"pip3?\s+install\b",
     "pip install は禁止です。最適解: uv を使う (`uv add <pkg>==<x.y.z>` / `uv sync`)。既存リポジトリが requirements.txt 運用の場合は `uv pip` またはユーザー確認を行ってください。"),
    (B + r"(npm|pnpm|pnpx|npx|yarn|bun|uv|uvx|pipx)\b[^|;&]*@latest\b",
     "@latest 等の非固定バージョン指定は禁止です。最適解: バージョンを明示的に固定して指定する。"),
    (r"\s--no-verify\b",
     "--no-verify によるフック回避は禁止です。最適解: フックが失敗する真因を修正してから再実行する。"),
    (B + r"(curl|wget)\b[^|;]*\|\s*(sh|bash|zsh)\b",
     "リモートスクリプトのパイプ実行は禁止です。最適解: ファイルに保存し内容を確認した上で、実行をユーザーに提案する。"),
    (B + r"(crontab|launchctl|schtasks)\b",
     "ホストOSスケジューラの直接操作は禁止です (GEMINI.md §2.1)。必要ならユーザーに手動実行を依頼してください。"),
]
for pattern, reason in DENY:
    if re.search(pattern, cmd, re.IGNORECASE):
        emit({"decision": "deny", "reason": reason})

# --- force_ask: 不可逆・破壊的。キャッシュ済み許可を無視して必ず確認 ---
FORCE_ASK = [
    B + r"rm\s+(-{1,2}[^\s;|&]+\s+)*(-\w*r|--recursive)",
    B + r"rm\s+(-{1,2}[^\s;|&]+\s+)*(-\w*f|--force)\b",
    r"git\s+reset\s+--hard",
    r"git\s+clean\b[^|;]*\s-\w*f",
    r"git\s+push\b[^|;]*(--force(\s|$)|\s-f(\s|$))",
    r"git\s+stash\s+(drop|clear)\b",
    B + r"truncate\b",
    B + r"diskutil\s+(erase|reformat)",
]
for pattern in FORCE_ASK:
    if re.search(pattern, cmd, re.IGNORECASE):
        emit({"decision": "force_ask",
              "reason": "不可逆または破壊的な操作の可能性があります。対象・影響範囲・復元手段を確認してください。"})

# --- ask: 要確認 (Always Allow キャッシュは尊重) ---
ASK = [
    B + r"npx\s",
    r"git\s+checkout\s+--\s",
    r"git\s+restore\b",
    r"git\s+branch\s+-D\b",
]
for pattern in ASK:
    if re.search(pattern, cmd, re.IGNORECASE):
        emit({"decision": "ask",
              "reason": "未検証パッケージの実行、または作業内容を破棄しうる操作です。意図どおりか確認してください。"})

emit({"decision": "allow"})
