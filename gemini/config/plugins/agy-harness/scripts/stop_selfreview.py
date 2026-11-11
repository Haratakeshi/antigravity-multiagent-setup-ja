#!/usr/bin/env python3
# agy-harness: 完了ゲート (Stop)
# モデルが通常停止 (NO_TOOL_CALL / model_stop) しようとしたとき、30分に1回まで停止をブロックし、
# 証拠つきセルフレビューを強制する。虚偽の完了報告への対策。
import json
import os
import sys
import time


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.exit(0)


try:
    payload = json.load(sys.stdin)
except Exception:
    emit({})

# 対象: モデル自身による正常ターン終了で、バックグラウンドタスクも完了しているケースのみ
if str(payload.get("terminationReason", "")) not in ("NO_TOOL_CALL", "model_stop"):
    emit({})
if payload.get("fullyIdle") is not True:
    emit({})

cid = str(payload.get("conversationId", ""))
if not cid.strip():
    emit({})

state_dir = os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
    "agy-harness")
os.makedirs(state_dir, exist_ok=True)
gate_file = os.path.join(state_dir, cid + ".stopgate")

# 30分以内に既に発火していたら素通し (無限ループ防止)
now = int(time.time())
if os.path.exists(gate_file):
    try:
        with open(gate_file, encoding="utf-8") as f:
            last = int(f.read().strip())
        if now - last < 1800:
            emit({})
    except Exception:
        pass
with open(gate_file, "w", encoding="utf-8") as f:
    f.write(str(now))

reason = ("[agy-harness 完了ゲート] 停止前セルフレビューを実施してください: "
          "(1) 当初の指示を再読し、要求項目ごとに 済/未/不能 を明記する "
          "(2) 「動作した/修正した」と主張する箇所すべてに実行コマンド+exit code または file:line の証拠を併記する "
          "(3) 対象コードにテストが存在するのに未実行なら、いま実行して生の結果を報告する "
          "(4) 検証できないまま残った仮定は status: unverified と申告する。"
          "以上を満たした報告を出力すれば停止してよい。")

emit({"decision": "continue", "reason": reason})
