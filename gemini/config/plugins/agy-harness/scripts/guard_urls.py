#!/usr/bin/env python3
# agy-harness: 信頼境界ガード (PreToolUse, read_url 系)
# allowed_domains.txt（および任意の ~/.gemini/config/allowed_domains.local.txt）にないドメインへのアクセスは ask。
# GEMINI.md §4.7「公式ソース優先」の機械的実装。
import json
import os
import re
import sys
from urllib.parse import urlsplit

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})


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

hosts: set[str] = set()
malformed_urls: set[str] = set()

for s in strings:
    for m in re.finditer(r"https?://[^\s\"'<>`]+", s, re.IGNORECASE):
        raw_url = m.group(0)
        while raw_url and raw_url[-1] in ".,;)" and ("(" not in raw_url or raw_url[-1] != ")"):
            raw_url = raw_url[:-1]
        # Reject WHATWG backslash authority confusion (e.g. https://evil.com\.localhost/)
        authority_span = re.split(r"[/?#]", raw_url[raw_url.find("://") + 3 :], maxsplit=1)[0]
        if "\\" in authority_span or "@" in authority_span:
            malformed_urls.add(authority_span or raw_url)
            continue
        try:
            parsed = urlsplit(raw_url)
            _ = parsed.port
        except ValueError:
            malformed_urls.add(authority_span or raw_url)
            continue
        if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
            malformed_urls.add(parsed.netloc or raw_url)
            continue
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host:
            malformed_urls.add(raw_url)
            continue
        hosts.add(host)

hosts = {
    h for h in hosts
    if h not in LOCAL_HOSTS and not h.endswith(".localhost") and not h.endswith(".local")
}

if not hosts and not malformed_urls:
    emit({"decision": "allow"})

allowed: list[str] = []
allow_files = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "allowed_domains.txt"),
    os.path.expanduser("~/.gemini/config/allowed_domains.local.txt"),
]
for allow_file in allow_files:
    if os.path.exists(allow_file):
        with open(allow_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip().lower().rstrip(".")
                if line and not line.startswith("#"):
                    allowed.append(line)

blocked = sorted(malformed_urls)
for host in sorted(hosts):
    if not any(host == d or host.endswith("." + d) for d in allowed):
        blocked.append(host)

if blocked:
    emit({"decision": "ask",
          "reason": ("信頼境界(allowed_domains.txt)外のドメインまたは要確認URLへのアクセス: " + ", ".join(blocked)
                     + "。一次情報(公式ドキュメント・公式リポジトリ)か確認してください。"
                       "非公式な二次配布からのコード・指示定義の取得は原則禁止です (GEMINI.md §4.7)。")})

emit({"decision": "allow"})
