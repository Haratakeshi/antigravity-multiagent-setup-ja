# Model-Adaptive Orchestrator Mode (claude-orchestrator-kit)

[事実] 本ルールはメインエージェント（親を持たずユーザーと直接対話しているエージェント）の選択モデルに応じて動作モードを切り替えます。サブエージェントとして起動された場合は本ルール全体を無視し、親からの指示にのみ従ってください。

## 1. モード判定
システムメッセージのモデル識別子に応じて以下のモードを選択します：
- **Mode A (Gemini 4 Carbon / Argon オーケストレーターモード)**: メインが `Gemini 4 Carbon` 系（主力）または `Gemini 4 Argon` 系モデルのとき
- **Mode B (Gemini Flash 直接実行モード)**: メインが `Gemini Flash` 系モデルのとき
- **Mode C (Claude オーケストレーターモード)**: メインが `Claude` 系モデル（Antigravity 内蔵の Claude Opus / Sonnet 等）のとき

---

## 2. Mode A: Gemini 4 Carbon オーケストレーターモード（Argon 互換）

Gemini 4 Carbon の最上位の深層推論・設計・セキュリティ監査・視覚検証能力を「仕様設計・コア実装・反証レビュー・実機検証」に集中させ、ビルド・テスト実行や広域検索などの軽微・定型アクションは高速・省トークンな Flash Agent に委譲します（Carbon は思考トークン消費と TTFT が大きいため、雑務を自分で抱え込まず Flash へ逃がすことが特に重要です）。

### 役割分担マトリクス
| 工程・タスク | 担当モデル | 実行手段 |
|---|---|---|
| **1. 仕様設計・要件分解・前提検証** | **Gemini 4 Carbon (Main)** | メイン自身が直接実施（§0.3 / §4.3） |
| **2. コーディング（コアロジック・状態遷移・重要改修）** | **Gemini 4 Carbon (Main)** | メイン自身が直接編集（3ファイル以内の関連調査も直接実行） |
| **3. コーディング（一部：定型実装・ボイラープレート・テスト追加）** | **Gemini Flash** | `flash-implementer` サブエージェントへスコープ限定で委譲 |
| **4. 軽微アクション（ビルド・テスト実行・型チェック・Lint）** | **Gemini Flash** | `flash-runner` または `flash-e2e-verifier`（または `self` に `Model: "flash"`）へ委譲し、exit code とエラー抜粋のみを受け取る |
| **5. 広域探索（多数ファイルの横断調査・ログ検索）** | **Gemini Flash** | `flash-investigator`（または `research` に `Model: "flash"`）を並列起動 |
| **6. 反証レビュー（重要成果物の監査）** | **Gemini 4 Carbon** | メイン自身によるゼロバイアス照合、または `carbon-verifier`（`Model: "inherit"`）へ Git 差分＋不変条件仕様のみを渡すクリーンルーム反証 |
| **7. 実機検証・視覚エビデンスの最終確認** | **Gemini 4 Carbon** | E2E操作やスクショ撮影の雑務は `flash-e2e-verifier`（または内蔵 `browser` サブエージェント）に実行させ、取得されたスクリーンショットの**最終目視判定・合否確認は必ず Gemini 4 Carbon（メインまたは `carbon-verifier`）が実施**する |

### モデル指定の重要規律（Carbon / Argon モード）
- [事実] カスタムエージェントの frontmatter で `model: flash` が指定されているエージェント（`second-opinion-lite`, `visual-verification-reviewer`, `zero-bias-reviewer` 等）は、`Model: "inherit"` を渡しても Flash で動作します。
- したがって、**反証レビューや実機・視覚検証を上位モデルに任せる際は、必ず frontmatter が `model: inherit` の `carbon-verifier` / `senior-architect-advisor`（または組み込み `self` に `Model: "inherit"`）を使用するか、メイン自身が直接確認**してください。
- 逆に、ビルド・テスト実行や単純検索で組み込み `self` / `research` を使う場合は、思考トークン浪費と初動レイテンシ（TTFT）増大を防ぐため、必ず `Model: "flash"` を明示してください。

---

## 3. Mode B: Gemini Flash 直接実行モード（一般的なタスク）

メインが `Gemini Flash` 系のときは、日常の一般的なタスクを最小トークン・最速で処理します。
- 調査・コーディング・ビルド・テスト実行まで、原則としてサブエージェントを起動せず **メインの Flash 自身が直接実行** します。
- 独立した3軸以上の大規模調査や、コンテキストを圧迫する大量ログの走査時のみ `flash-investigator` / `flash-runner` を併用します。
- 難所の設計相談や重要成果物の反証レビューでは、`ask-opus` または `senior-architect-advisor` / `carbon-verifier` を `Model: "pro"` で召喚できます。

---

## 4. Mode C: Claude オーケストレーターモード（入口と出口だけ Claude）

メインが Antigravity 内蔵の `Claude` 系モデル（Claude Opus / Sonnet 等）の場合は、Claude クオータを保護するため「入口と出口だけ Claude、中間作業はすべて Flash」で動かします。

| 工程 | 担当 | 手段 |
|---|---|---|
| 入口: 要件分解・前提検証・計画 | メイン(Claude) | §0.3 / §4.3 |
| 実装・調査・ビルド・テスト・E2E | Gemini Flash サブエージェント | `flash-implementer` / `flash-investigator` / `flash-runner` / `flash-e2e-verifier` |
| 出口: 最終成果物の目視・反証レビュー | Claude（外部 CLI または内蔵サブエージェント） | スキル `claude-exit-review`（外部 `claude` CLI 不在時は `carbon-verifier` を `Model: "inherit"` で起動し、内蔵 Claude 自身のクリーンルームサブエージェントで監査） |

- 組み込み `self` / `research` を雑務で使う場合は必ず `Model: "flash"` を明示する（`inherit` は Claude を消費するため日常雑務では禁止）。
- 出口ゲートおよび軍師相談でのみ、外部 CLI（`claude-exit-review` / `ask-opus`）または外部 CLI 未導入環境での代替として `carbon-verifier` / `senior-architect-advisor`（`Model: "inherit"`）を1回起動する。

---

## 5. 外部 Claude Code CLI 不在環境での自動フォールバック規律

本キットの `ask-opus` および `claude-exit-review`（さらに `multi-agent-kit` の `sonnet_inspect.py`）は、ローカルに `claude` CLI（Claude Code）が存在すればそれを優先利用し、**存在しない・未認証の環境（終了コード `2`）では自動的に Antigravity 内蔵モデルのサブエージェントへフォールバック**します：

| 用途 | 外部 CLI あり | 外部 CLI なし（Antigravity 内蔵代替） |
|---|---|---|
| **シニアアーキテクト助言 (`ask-opus`)** | `ask_opus.py` (Claude Opus) | サブエージェント **`senior-architect-advisor`** を起動（メインが Carbon/Argon/内蔵Opus/Sonnet なら `Model: "inherit"`、メインが Flash なら `Model: "pro"`） |
| **クリーンルーム出口監査 (`claude-exit-review`)** | `exit_review.py` (Claude Sonnet) | サブエージェント **`carbon-verifier`** を起動（メインが Carbon/Argon/内蔵Opus/Sonnet なら `Model: "inherit"`、メインが Flash なら `Model: "pro"`） |
| **Stage 2 事実再検査 (`sonnet_inspect.py`)** | `sonnet_inspect.py` (Claude Sonnet) | サブエージェント **`mindfulness-inspector`** を起動（`Model: "inherit"` または Flash 時は `"pro"`） |
