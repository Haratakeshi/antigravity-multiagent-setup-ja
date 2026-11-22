#!/usr/bin/env python3
# agy-harness: セッションGrounding注入 (PreInvocation)
# 初回(n==0)と以後40回のモデル呼び出しごとに、本日日付と最小リマインドを公式 systemMessage 形式で注入する。
import datetime
import json
import os
import re
import sys
import time

GC_INTERVAL_SEC = 86400
DEFAULT_TTL_DAYS = 14


def prune_stale_state(target_dir: str) -> None:
    marker = os.path.join(target_dir, ".last_gc")
    now = time.time()
    try:
        if os.path.exists(marker) and (now - os.path.getmtime(marker)) < GC_INTERVAL_SEC:
            return
    except OSError:
        pass
    try:
        ttl_days = max(1, int(os.environ.get("AGY_STATE_TTL_DAYS", str(DEFAULT_TTL_DAYS))))
    except ValueError:
        ttl_days = DEFAULT_TTL_DAYS
    cutoff = now - (ttl_days * 86400)
    try:
        with os.scandir(target_dir) as it:
            for entry in it:
                if not entry.name.endswith((".count", ".stopgate", ".bingate", ".procgate", ".catchgate", ".hgturn")):
                    continue
                try:
                    if entry.stat(follow_symlinks=False).st_mtime < cutoff:
                        os.unlink(entry.path)
                except OSError:
                    pass
        with open(marker, "w", encoding="utf-8") as mf:
            mf.write(str(int(now)))
    except OSError:
        pass


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.exit(0)


try:
    payload = json.load(sys.stdin)
    raw_cid = str(payload.get("conversationId", "")).strip()
except Exception:
    emit({})

cid = re.sub(r"[^A-Za-z0-9_-]", "_", raw_cid)
if not cid:
    emit({})

state_dir = os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
    "agy-harness",
)
os.makedirs(state_dir, exist_ok=True)
prune_stale_state(state_dir)
state_file = os.path.join(state_dir, cid + ".count")

n = 0
if os.path.exists(state_file):
    try:
        with open(state_file, encoding="utf-8") as f:
            n = int(f.read().strip())
    except Exception:
        n = 0
with open(state_file, "w", encoding="utf-8") as f:
    f.write(str(n + 1))

today = datetime.date.today().isoformat()

if n == 0:
    msg = (
        f"[agy-harness] 本日: {today}。"
        "高速変化領域(API・モデル名・CLI・価格)は§4.6 Groundingで一次確認し、"
        "完了報告は§0.5の証拠(コマンド+exit code / file:line)と完了マーカーを添えること。"
    )
elif n % 40 == 0:
    msg = f"[agy-harness] リマインド({today}): 期待値は仕様から導出 / 報告は客観証拠つき / 削除・上書き前に影響確認。"
else:
    emit({})

emit({"injectSteps": [{"systemMessage": {"systemMessage": msg}}]})
