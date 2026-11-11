---
name: grilling
description: Structured 9-step requirement alignment, multi-axis investigation, second-opinion audit, and TODO planning workflow (/grilling).
---

# Grilling: 段階的ゴール合意・方針すり合わせ・反証レビュー統合ワークフロー

ユーザーが `/grilling` を指定した際、または曖昧・複合的な要件のすり合わせを行う際は、単純な「調査→1問質問→調査→1問質問」の小出しループやゴール未合意のままのTODO作成を**禁止**し、必ず以下の **9ステップ・ワークフロー** を順番通りに実行してください。

## 全体ワークフロー（9 Steps）

1. **Step 1: 初期調査（Initial Discovery）**
   - 対象プロジェクトの全体像・関連仕様書・現行コードの骨格を把握する。
2. **Step 2: Ask ① — ゴールの再確認と提示（Goal Alignment Ask）**
   - 達成すべき最終ゴール・完了条件・スコープ（やる事／やらない事）を整理して提示し、`AskUserQuestion`（または `ask_question`）で合意を得る。推奨案には `(Recommended)` を付ける。
3. **Step 3: 複数の調査（Multi-Axis Investigation）**
   - 合意したゴールに基づき、複数の直交軸（ロジック・UI・データ・テスト等）を並行調査する。コードを読めばわかる事実をユーザーに聞かない。
4. **Step 4: Ask ② — 方針すり合わせの Ask（1項目 1 Ask 厳守）**
   - **1つのセクション・論点につき必ず1つの独立した質問**を作成し、複数のセクションを1つの質問に混ぜない。複数の質問を実行して基本方針を丁寧にすり合わせる。
5. **Step 5: 詳細調査（Targeted Deep-Dive）**
   - 確定方針に基づき、変更対象の `file:line`・スキーマ・波及範囲を特定する。
6. **Step 6: 調査結果の軽い反証レビュー（Lightweight Second-Opinion）**
   - サブエージェント（クリーンルーム反証）を用いて、既存実装の見落としや前提誤認がないか検証する。
7. **Step 7: Ask ③ — TODO に落とし込むための詳細な Ask（1項目 1 Ask 厳守）**
   - 具体的な数値・文言・境界条件・フォールバック等を 1項目 1 Ask で確定させる。
8. **Step 8: Ask 結果を元に反証レビュー ＆ Plan 策定**
   - 回答を統合して Plan と TODO を作成し、再度反証レビューを通して抜け漏れ・矛盾を解消する。
9. **Step 9: 実装開始**
   - 確定した Plan に基づき実装へ進む（明示的な GO 待ち指定がある場合のみ Plan 提示で待機）。
