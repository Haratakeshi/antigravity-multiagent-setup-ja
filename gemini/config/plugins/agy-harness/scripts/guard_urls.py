#!/usr/bin/env python3
# agy-harness: 信頼境界ガード (PreToolUse, read_url 系)
# allowed_domains.txt にないドメインへのアクセスは ask。
import json
import os
import re
import sys


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.exit(0)


try:
    payload = json.load(sys.stdin)
    tool_args = payload.get("toolCall", {}).get("args", {})
except Exception:
    emit({"decision": "allow"})

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

hosts = set()
for s in strings:
    for m in re.finditer(r"https?://([^/\s\"'<>:]+)", s):
        hosts.add(m.group(1).lower())
if not hosts:
    emit({"decision": "allow"})

allow_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "allowed_domains.txt")
allowed = []
if os.path.exists(allow_file):
    with open(allow_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip().lower()
            if line and not line.startswith("#"):
                allowed.append(line)

blocked = []
for host in sorted(hosts):
    if not any(host == d or host.endswith("." + d) for d in allowed):
        blocked.append(host)

if blocked:
    emit({"decision": "ask",
          "reason": ("信頼境界(allowed_domains.txt)外のドメインへのアクセス: " + ", ".join(blocked)
                     + "。一次情報(公式ドキュメント・公式リポジトリ)か確認してください。"
                       "非公式な二次配布からのコード・指示定義の取得は原則禁止です (GEMINI.md §4.7)。")})

emit({"decision": "allow"})
