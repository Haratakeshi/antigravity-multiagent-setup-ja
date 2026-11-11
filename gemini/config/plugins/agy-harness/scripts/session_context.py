#!/usr/bin/env python3
# agy-harness: セッションGrounding注入 (PreInvocation)
# 会話の最初のモデル呼び出しで「今日の日付 + 最適解プロトコル」を注入。
# 以後は25回のモデル呼び出しごとに短いリマインドを再注入 (Context Decay 対策)。
import datetime
import json
import os
import sys


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.exit(0)


try:
    payload = json.load(sys.stdin)
    cid = str(payload.get("conversationId", ""))
except Exception:
    emit({})

if not cid.strip():
    emit({})

state_dir = os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
    "agy-harness")
os.makedirs(state_dir, exist_ok=True)
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
    msg = f"""[agy-harness] 本日: {today}。あなたの学習知識は本日時点では陳腐化している前提で作業する。
最適解プロトコル:
1. ライブラリ/API/モデル名/CLIフラグは、着手前にWeb検索または公式ドキュメントで実在と最新仕様を確認してから使うのが最適解。
2. 最初に発見したドキュメント・コードは「候補」にすぎない。更新日時・公式性・参照元を確認し、独立した2つ目のソースで裏取りしてから採用するのが最適解。deprecated / 旧API / どこからも参照されないコードは Legacy の兆候。
3. テスト失敗時の修正対象はアプリケーションコード。テストの期待値は仕様から導出するのが最適解 (実装出力のコピー・skip/only による回避をしない)。
4. 「動作した」報告には実行コマンドと exit code を添えるのが最適解。未検証の事項は unverified と明記する。
5. 削除・上書きの前に対象を読み、影響範囲 (Aを変更するとBとCに影響) を報告するのが最適解。"""
elif n % 25 == 0:
    msg = (f"[agy-harness] リマインド (本日: {today}): テストの期待値は仕様から / "
           "報告は証拠 (コマンド+exit code) つき / 未確認のAPI・モデル名は使用前にGrounding / "
           "削除・上書き前に影響範囲を報告。")
else:
    emit({})

emit({"injectSteps": [{"systemMessage": {"systemMessage": msg}}]})
