# Nova — エージェントのコンピュータ

[English](./README.md) | [中文](./README_zh.md) | 日本語 | [Français](./README_fr.md) | [Русский](./README_ru.md)

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](./backend/pyproject.toml)
[![Node.js](https://img.shields.io/badge/Node.js-22%2B-339933?logo=node.js&logoColor=white)](./Makefile)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

**Nova** は、リサーチ、コーディング、クリエイティブを行えるフルスタックの**コンピュータエージェント**です。**サブエージェント**、**メモリ**、**スレッドごとのサンドボックス**をオーケストレーションし、**拡張可能なスキル**とエージェント自身のコンピュータのライブストリーミングビューで、ほぼあらゆるタスクを実行できます——ターミナル、エディタ、ブラウザプレビュー、タスク進捗をリアルタイムで表示。

Nova は **[Ali Technologies](https://www.alilabsx.com)** がオープンソースの [DeerFlow](https://github.com/bytedance/deer-flow) スーパーエージェントハーネスの上に構築しました。上流 MIT ライセンスとすべての元の著作権表示は保持されています——[ライセンス](#ライセンス) と [NOTICE.md](./NOTICE.md) を参照。

> 挨拶しましょう：*"私はNova、コンピュータエージェントです。"*

![Nova ワークスペース — エージェントがチップ計算機を構築し、Agent's Computer ブラウザタブでライブプレビュー](./docs/images/nova-workspace.png)

## Nova が DeerFlow の上に追加したもの

Nova は DeerFlow 2.0 のフルスタックリファクタリングです——**338 ファイルで +35,738 行**（検証済み：`git diff --shortstat v2.0.0-rc1 HEAD`）。主な追加機能はすべて Nova 用に構築：

- **Agent's Computer** — 6タブライブパネル（ターミナル、赤/緑ライブ差分付きエディタ、ブラウザプレビュー、アクティビティタイムライン、ファイル、レビュー）がエージェントの操作をリアルタイムでストリーミング。
- **検証ループ** — エージェントがビルドをテスト：ヘッドレスChromiumがランニング開発サーバー against のセルフチェック（コンソエラー、空白レンダリング検出、スクリーンショット）、開発サーバー準備完了時とHTML納品時に自動トリガー。
- **決定論的コードレビュー** — LLM不要のレビューエンジン。非エンジニアにプレーンイングリッシュの判定、開発者にファイルごとの統計とリスクフラグ。
- **自己修正ミドルウェア** — ランタイム強制反復予算、デッドエンドループ検出、プリフライトクォータチェック、エラーメッセージ汚染除去、ライブタスク進捗。
- **32個のエージェントツール** — シェルセッション、ブラウザナビゲーション/クリック/入力/eval、スクリーンショット、スキャフォールド、開発サーバーライフサイクル、dev_verify、code_review、スキル保存など。
- **iGIN0 プライバシーリサーチ** — リトライ、サーキットブレーカー、LRU+TTLキャッシュ、オプションTORルーティング、プライバシーアウドトレイル付きの强化されたSearXNGクライアント。
- **ランタイムモデル管理** — APIと設定UIでモデルを追加/切り替え、Config ファイルの編集不要。
- **オペレーション層** — 11プローブ自己修復ウォッチドッグ、PM2管理Dockerライフサイクル、再起動永続化。
- **ローカル + 無料モデル via LiteLLM** — 設定のOllamaプリセットとPM2管理LiteLLMプロキシで、4つの無料Ollamaクラウドモデル（MiniMax M3、Nemotron 3 Super、Qwen3 Coder 480B、GPT-OSS 120B）と有料プロバイダーを公開。
- **8,112行の新テスト** — 37の新しいバックエンドテストファイルにまたがる。

上流DeerFlowはエージェントハーネス（サブエージェント、メモリ、LangGraphランタイム）、スキルシステム、スレッドごとのDockerサンドボックスを提供——功劳は上流に。完全で再現可能な帰属マップは **[NOVA_VS_DEERFLOW.md](./NOVA_VS_DEERFLOW.md)** にあります。

## AMD コンピュータで実行

Nova は **AMD Instinct** GPU で推論を提供し、それをワンクリックの初等選択肢として構築——**AMD 開発者ハッカソン（Act II）** 用。

- **2つのAMDパス、設定のプリセットとして** → モデル：**Fireworks AI**（マネージド、AMD Instinct MI300X上で提供）と **AMD 開発者クラウド**（Nova独自の推論 via **vLLM on ROCm**、`scripts/amd-serve-vllm.sh`）。AMD コンピュータの追加は設定であり、コードではありません。
- **検証可能な使用量** — `GET /api/models/amd-usage` が機械可読のAMD使用量サマリーを返し、AMD対応モデルには **AMD** バッジを表示。
- **Track 1 エージェント** — [`hackathon/track1/`](./hackathon/track1/) のリーンでトークン効率の良いFireworksバッチハーネス；`make hackathon-track1` でビルド＆スモークテスト。
- 完全なセットアップ + AMD コンピュータドキュメント：**[docs/AMD_INTEGRATION.md](./docs/AMD_INTEGRATION.md)**。

## 目次

- [Nova — エージェントのコンピュータ](#nova--エージェントのコンピュータ)
  - [目次](#目次)
  - [ワンクリックエージェントセットアップ](#ワンクリックエージェントセットアップ)
  - [クイックスタート](#クイックスタート)
    - [設定](#設定)
    - [アプリケーションの実行](#アプリケーションの実行)
      - [デプロイサイズ](#デプロイサイズ)
      - [方法1：Docker（推奨）](#方法1docker推奨)
      - [方法2：ローカル開発](#方法2ローカル開発)
    - [高度な設定](#高度な設定)
      - [サンドボックスモード](#サンドボックスモード)
      - [MCPサーバー](#mcpサーバー)
      - [IMチャンネル](#imチャンネル)
      - [LangSmithトレーシング](#langsmithトレーシング)
      - [Langfuseトレーシング](#langfuseトレーシング)
  - [ディープリサーチからスーパーエージェントハーネスへ](#ディープリサーチからスーパーエージェントハーネスへ)
  - [コア機能](#コア機能)
    - [スキルとツール](#スキルとツール)
    - [サブエージェント](#サブエージェント)
    - [サンドボックスとファイルシステム](#サンドボックスとファイルシステム)
    - [コンテキストエンジニアリング](#コンテキストエンジニアリング)
    - [長期メモリ](#長期メモリ)
  - [推奨モデル](#推奨モデル)
  - [組み込みPythonクライアント](#組み込みpythonクライアント)
  - [ドキュメント](#ドキュメント)
  - [⚠️ セキュリティに関する注意](#️-セキュリティに関する注意)
  - [コントリビューション](#コントリビューション)
  - [ライセンス](#ライセンス)

## ワンクリックエージェントセットアップ

Claude Code、Codex、Cursor、Windsurf、または他のコーディングエージェントを使用している場合、セットアップ手順を一文で渡すことができます：

```text
Help me clone Nova if needed, then bootstrap it for local development by following https://raw.githubusercontent.com/Jahanzaib211/nova/main/docs/Install.md
```

このプロンプトはコーディングエージェントを対象としています。エージェントにリポジトリのクローン（必要に応じて）、Dockerの選択（可能な場合）、必要なユーザー入力と不足している設定で停止するよう指示します。

## クイックスタート

### 設定

1. **Novaリポジトリをクローン**

   ```bash
   git clone https://github.com/Jahanzaib211/nova.git
   cd nova
   ```

2. **セットアップウィザードを実行**

   プロジェクトルート（`nova/`）から：

   ```bash
   make setup
   ```

   対話式ウィザードがLLMプロバイダー、オプションのWeb検索、実行/サンドボックスモードなどの設定をガイドします。`config.yaml`を生成し、`.env`にキーを書き込みます。約2分で完了。

   いつでも `make doctor` を実行してセットアップを検証し、修正ヒントを取得できます。

### アプリケーションの実行

#### デプロイサイズ

| デプロイターゲット | スターティングポイント | 推奨 | 注意 |
|---------|-----------|------------|-------|
| ローカル評価 / `make dev` | 4 vCPU, 8 GB RAM, 20 GB SSD | 8 vCPU, 16 GB RAM | 1人の開発者または1つのライトセッションに最適 |
| Docker開発 / `make docker-start` | 4 vCPU, 8 GB RAM, 25 GB SSD | 8 vCPU, 16 GB RAM | イメージビルド、バインドマウント、サンドボックスコンテナにはヘッドルームが必要 |
| 長時間稼働サーバー / `make up` | 8 vCPU, 16 GB RAM, 40 GB SSD | 16 vCPU, 32 GB RAM | 共有使用、マルチエージェント実行に推奨 |

#### 方法1：Docker（推奨）

**開発**（ホットリロード、ソースマウント）：

```bash
make docker-init    # サンドボックスイメージをプル（初回または更新時のみ）
make docker-start   # サービスを起動（config.yamlからサンドボックスモードを自動検出）
```

**本番**（ローカルでイメージをビルド、ランタイムConfigとデータをマウント）：

```bash
make up     # イメージをビルドし、すべての本番サービスを起動
make down   # サービスを停止し、コンテナを削除
```

アクセス：<http://localhost:2026>

#### 方法2：ローカル開発

1. **前提条件を確認**：

   ```bash
   make check  # Node.js 22+, pnpm, uv, nginx を検証
   ```

2. **依存関係をインストール**：

   ```bash
   make install  # バックエンド + フロントエンドの依存関係をインストール
   ```

3. **サービスを起動**：

   ```bash
   make dev
   ```

4. **アクセス**：<http://localhost:2026>

### 高度な設定

#### サンドボックスモード

Nova は複数のサンドボックス実行モードをサポート：

- **ローカル実行**（ホスト上で直接コードを実行）
- **Docker実行**（分離されたDockerコンテナで実行）
- **Docker + Kubernetes実行**（provisionerサービス経由でK8s Podで実行）

詳細は [サンドボックス設定ガイド](backend/docs/CONFIGURATION.md#sandbox) を参照。

#### MCPサーバー

Nova は設定可能なMCPサーバーとスキルをサポートし、機能を拡張します。HTTP/SSE MCPサーバーはOAuthトークンフローをサポート（`client_credentials`、`refresh_token`）。詳細は [MCPサーバーガイド](backend/docs/MCP_SERVER.md) を参照。

#### IMチャンネル

Nova はメッセージングアプリからタスクを受信できます。チャンネルは設定後自動起動——パブリックIPは不要。

| チャンネル | トランスポート | 難易度 |
|---------|-----------|------------|
| Telegram | Bot API（長ポーリング） | 簡単 |
| Slack | Socket Mode | 中程度 |
| Feishu / Lark | WebSocket | 中程度 |
| WeChat | Tencent iLink（長ポーリング） | 中程度 |
| WeCom | WebSocket | 中程度 |
| DingTalk | Stream Push（WebSocket） | 中程度 |

`config.yaml` で設定し、`.env` に対応するAPIキーを設定します。

#### LangSmithトレーシング

Nova は組み込みの [LangSmith](https://smith.langchain.com) 統合を提供。有効にすると、すべてのLLM呼び出し、エージェント実行、ツール実行がトレースされ、LangSmithダッシュボードに表示されます。

```bash
LANGSMITH_TRACING=true
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_API_KEY=lsv2_pt_xxxxxxxxxxxxxxxx
LANGSMITH_PROJECT=xxx
```

#### Langfuseトレーシング

Nova は [Langfuse](https://langfuse.com) 可観測性もサポート。

```bash
LANGFUSE_TRACING=true
LANGFUSE_PUBLIC_KEY=pk-lf-xxxxxxxxxxxxxxxx
LANGFUSE_SECRET_KEY=sk-lf-xxxxxxxxxxxxxxxx
LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

## ディープリサーチからスーパーエージェントハーネスへ

Nova はディープリサーチフレームワークとして始まりました——コミュニティがそれをさらに推し進めました。開発者はデータパイプラインの構築、スライドデッキの生成、ダッシュボードの作成、コンテンツワークフローの自動化に使用。私たちが予想していなかったことです。

それは重要なことを伝えました：Nova は単なるリサーチツールではありません。それは**ハーネス**——エージェントが実際に作業を遂行するためのインフラストラクチャを提供するランタイムです。

だからゼロから作り直しました。

Nova 2.0 はもはやWire togetherするフレームワークではありません。バッテリー付き、完全に拡張可能なスーパーエージェントハーネスです。LangGraphとLangChain上に構築され、エージェントに必要なすべてを出荷時から搭載：ファイルシステム、メモリ、スキル、サンドボックス認識実行、複雑なマルチステップタスクの計画とサブエージェント生成能力。

そのまま使う。または分解してカスタマイズ。

## コア機能

### スキルとツール

スキルがNova を*ほぼ何でも*できるようにします。

標準エージェントスキルは構造化された機能モジュール——ワークフロー、ベストプラクティス、サポートリソースの参照を定義するMarkdownファイルです。Nova はリサーチ、レポート生成、スライド作成、Webページ、画像・動画生成などのビルトインスキルを同梱。しかし真の力は拡張性：独自のスキルを追加、ビルトインを置換、複合ワークフローに組み合わせ。

スキルは段階的に読み込まれ——タスクが必要な時だけ、まとめてではありません。これによりコンテキストウィンドウがスリムに保たれ、トークン敏感なモデルでも良好に動作します。

ツールも同じ哲学。Nova にはコアツールセット——Web検索、Webフェッチ、ファイル操作、bash実行——が同梱され、MCPサーバーとPython関数でカスタムツールをサポート。何でも置換。何でも追加。

### サブエージェント

複雑なタスクは単一のパスで処理できないことが多い。Nova はそれを分解。

リードエージェントは動的にサブエージェントを生成可能——それぞれが独立したスコープのコンテキスト、ツール、終了条件を持つ。サブエージェントは可能な限り並列実行し、構造化された結果を報告。リードエージェントはすべてを統合して一貫した出力を生成。

これがNova が数分から数時間のタスクを処理する方法：リサーチタスクがダズンものサブエージェントに分散し、それぞれが異なる角度を探索し、単一のレポートに収束——またはウェブサイト——または生成されたビジュアルを含むスライドデッキ。一つのハーネス、複数の手。

### サンドボックスとファイルシステム

Nova は単に*物事を行う*と*話す*だけでなく、独自のコンピュータを持っています。

各タスクは完全なファイルシステムビューを持つ実行環境を取得——スキル、ワークスペース、アップロード、出力。エージェントはファイルを読み取り、書き込み、編集。画像を表示し、安全に設定された場合シェルコマンドを実行可能。

`AioSandboxProvider`使用時、シェル実行は分離されたコンテナで実行。`LocalSandboxProvider`使用時、ファイルツールはホスト上のスレッド別ディレクトリにマッピングされますが、ホスト`bash`はデフォルトで無効（安全な分離境界ではないため）。

これがチャットボットのツールアクセスと実際の実行環境を持つエージェントの違いです。

### コンテキストエンジニアリング

**分離されたサブエージェントコンテキスト**：各サブエージェントは独自の分離されたコンテキストで実行。メインエージェントや他のサブエージェントのコンテキストを参照不可。

**要約**：セッション内、Nova はコンテキストを積極的に管理——完了したサブタスクを要約、中間結果をファイルシステムにオフロード、直ちに必要でないものを圧縮。これにより長時間のマルチステップタスクでもコンテキストウィンドウを超過せずにシャープを維持。

**厳格なツール呼び出し回復**：プロバイダーまたはミドルウェアがツール呼び出しループを中断した場合、Nova は強制停止アシスタントメッセージからプロバイダーレベルの生ツール呼び出しメタデータを剥ぎ取り、次のモデル呼び出し前にダングリング呼び出しにプレースホルダーツール結果を注入。

### 長期メモリ

ほとんどのエージェントは会話が終わるとすべてを忘れる。Nova は覚えている。

セッションを越えて、Nova はプロファイル、設定、蓄積された知識の永続的なメモリを構築。使用するほど、 Writing style、技術スタック、繰り返しワークフローを理解。メモリはローカルに保存され、ユーザーの管理下。

## 推奨モデル

Nova はモデル非依存——OpenAI互換APIを実装するすべてのLLMで動作。ただし、以下をサポートするモデルで最適：

- **長いコンテキストウィンドウ**（100k+トークン）でディープリサーチとマルチステップタスク
- **推論能力**でアダプティブプランニングと複雑な分解
- **マルチモーダル入力**で画像理解とビデオ理解
- **強力なツール使用**で信頼性の高い関数呼び出しと構造化出力

## 組み込みPythonクライアント

Nova は完全なHTTPサービスを実行せずに、組み込みPythonライブラリとして使用可能。`DeerFlowClient`はすべてのエージェントとGateway機能への直接プロセス内アクセスを提供し、HTTP Gateway APIと同じレスポンススキーマを返却：

```python
from deerflow.client import DeerFlowClient

client = DeerFlowClient()

# チャット
response = client.chat("この論文を分析して", thread_id="my-thread")

# ストリーミング（LangGraph SSEプロトコル：values, messages-tuple, end）
for event in client.stream("hello"):
    if event.type == "messages-tuple" and event.data.get("type") == "ai":
        print(event.data["content"])

# 設定と管理 — Gatewayアライン辞書を返却
models = client.list_models()        # {"models": [...]}
skills = client.list_skills()        # {"skills": [...]}
client.update_skill("web-search", enabled=True)
client.upload_files("thread-1", ["./report.pdf"])  # {"success": True, "files": [...]}
```

完全なAPIドキュメントは `backend/packages/harness/deerflow/client.py` を参照。

## ドキュメント

- [コントリビューションガイド](CONTRIBUTING.md) - 開発環境セットアップとワークフロー
- [設定ガイド](backend/docs/CONFIGURATION.md) - セットアップと設定手順
- [アーキテクチャ概述](backend/CLAUDE.md) - 技術アーキテクチャの詳細
- [バックエンドアーキテクチャ](backend/README.md) - バックエンドアーキテクチャとAPIリファレンス
- [エンタープライズ監査](docs/AUDIT.md) - フルスタック監査と改善計画
- [フロントエンド監査](docs/FRONTEND_AUDIT.md) - フロントエンドコンポーネントツリーとデータフロー

## ⚠️ セキュリティに関する注意

### 不適切なデプロイによりセキュリティリスクが生じる可能性

Nova は**システムコマンド実行、リソース操作、ビジネスロジック呼び出し**などの高権限機能を持ち、デフォルトでは**ローカル信頼環境（127.0.0.1ループバックインターフェースのみからアクセス可能）でデプロイ**されるよう設計。信頼できない環境——LANネットワーク、パブリッククラウドサーバー、その他のマルチエンドポイントアクセス可能環境——に厳格なセキュリティ対策なしでデプロイすると、セキュリティリスクが生じる可能性。

**ローカル信頼ネットワーク環境でのデプロイを強く推奨します。** クロスデバイスまたはクロスネットワークデプロイが必要な場合、IP許可リスト、認証ゲートウェイ、ネットワーク分離などの厳格なセキュリティ対策を実装する必要があります。

## コントリビューション

コントリビューションを歓迎します！開発セットアップ、ワークフロー、ガイドラインについては [CONTRIBUTING.md](CONTRIBUTING.md) を参照。

## ライセンス

DeerFlow ファウンデーションプロジェクトは [MITライセンス](./LICENSE) の下でオープンソース。元のライセンステキストとすべての上流著作権通知は完全に保持。

Nova固有の追加（Agent's Computer UI、スレッドごとのコンテナ分離、ウォッチドッグ、レシートおよび関連ツール）は **Ali Technologiesの专有** — [NOTICE.md](./NOTICE.md) を参照。

## 致謝

Nova は ByteDance の [DeerFlow](https://github.com/bytedance/deer-flow) とそのコミュニティの上に構築。Nova を可能にしているプロジェクトとコントリビューターに深く感謝——本当に、私たちは巨人の肩の上に立っています。

- **[DeerFlow](https://github.com/bytedance/deer-flow)**: Nova が構築されているスーパーエージェントハーネス。
- **[LangChain](https://github.com/langchain-ai/langchain)**: LLM インタラクションとチェーンを駆動。
- **[LangGraph](https://github.com/langchain-ai/langgraph)**: 複雑なマルチエージェントオーケストレーションを実現。
