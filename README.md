# Saga

- **Saga は、機械制御・ロボティクス・自律システム・ドローン向けの自作プログラミング言語です。**
- 目標:
  - 制御ソフトウェアを、人がレビュー・調整しやすい読みやすさで記述できるようにする。
  - 時間制約やハードウェアへの権限を、便利な API の裏側に隠さない。
  - 制御上重要な条件を、言語処理系が解析・検査できる形でソースコード上に明示する。
- 主な設計方針:
  - **読みやすい制御コード**
    - Python に近い簡潔な記法を採用。
  - **明示的な制御境界**
    - `@control_tick`
    - `@control_safe`
    - capability
    - move-only resource
    - deterministic cleanup
  - **ネイティブシステムへの接続性**
    - machine / drone ライブラリ
    - デバイスバス
    - ネイティブコード生成
    - 独立した Go 実装
- 注意:
  - Saga は現在も開発中の言語プロジェクトです。
  - 機能安全認証済み製品ではありません。
  - ソフトウェア検査は危険なソースパターンの検出に役立ちますが、以下を置き換えるものではありません。
    - 対象ハードウェア上での WCET 測定
    - 物理 HIL
    - E-stop / STO / interlock の検証
    - 航空・機械分野の認証
    - その他の実機固有の安全性証拠

## 60秒で分かる Saga

- 周期制御コードでは、時間契約をソースコード上に直接記述できます。

```saga
@control_safe
fn clamp_command(value: decimal) -> decimal {
    if value > 1.0 { return 1.0 }
    if value < -1.0 { return -1.0 }
    return value
}

@control_tick(20000, 35)
fn current_tick(error: decimal) -> decimal {
    return clamp_command(error * 0.5)
}
```

- `@control_tick(20000, 35)` の意味:
  - 制御周期: 20 kHz
  - 1周期: 50 µs
  - ソースレベル実行予算: 35 µs
  - 予算使用率: 70%
- Saga が制御経路で検査する代表例:
  - 隠れたブロッキング処理
  - 外部 I/O
  - 上限を定めにくい処理
  - 共有可変状態
  - 間接呼び出し
  - 未検証ヘルパー
  - 再帰
- 制御解析をレポートとして表示できます。

```bash
saga-control-report examples/contest/diff_safe_control.saga
saga-control-report examples/contest/diff_safe_control.saga --html build/control-report.html
```

- レポートで確認できる内容:
  - 宣言された制御周波数
  - 周期時間
  - 実行予算
  - 予算使用率
  - 実施されたソースレベル検査
  - ソース解析と実機安全性証拠の境界
- 意図的に危険な例も確認できます。

```bash
saga-control-report examples/contest/diff_unsafe_control.saga
```

- 危険な例では:
  - 問題のあるソース位置を表示。
  - 安定した `SAGA-C...` 診断コードを表示。
  - 時間依存処理や raw I/O を周期経路の外へ移すための修正方針を示す。
- コンテスト向け資料:
  - [`docs/DIFF_SHIZUOKA_2026.md`](docs/DIFF_SHIZUOKA_2026.md)
  - [`SUBMISSION_README_JA.md`](SUBMISSION_README_JA.md)
  - [`docs/PROGRAMMING_FLOW_JA.md`](docs/PROGRAMMING_FLOW_JA.md)

## プロジェクト状況

- 最新の凍結リリース:
  - **Saga 0.50.0 — Production GA Control Hardening**
- 現在の開発版:
  - **Saga 0.53.0 — Machine & Drone Control Focus**
- 凍結リリースブランチ:
  - `release/0.50.0-production-ga`
- 開発ブランチ:
  - `main`
- ライセンス:
  - MIT
- Python 要件:
  - Python 3.13 以上
- `Production GA` という名称について:
  - 言語・ツールチェーン上の制御プロファイルを示す名称です。
  - 物理的な機械や航空機に対する機能安全認証を意味しません。

## なぜ「制御ライブラリ」ではなく「言語」なのか

- 一般的な制御ライブラリでも、以下の機能は提供できます。
  - PID コントローラ
  - CAN API
  - モータ制御 API
- Saga が重視している点:
  - 制御上重要な制約を、ライブラリ呼び出しだけでなく言語処理系から見える形にする。
  - Parser / Checker / Diagnostics / Build tooling が制御契約を理解できるようにする。
- Saga の処理系が確認できる代表例:
  - どの関数が周期制御経路に含まれるか。
  - 作者が何 Hz・何 µs の予算を宣言したか。
  - ヘルパー関数がブロッキング I/O を隠していないか。
  - 共有可変状態や再帰が制御コールグラフに入っていないか。
  - デバイス操作に明示的 capability が必要か。
  - なぜ拒否されたか、何を直せばよいかを診断できるか。
- これらは単なる数学ライブラリではなく、言語・ツールチェーンの責務として扱います。

## クイックスタート

- リポジトリを取得します。

```bash
git clone https://github.com/SagaK-dev/Saga.git
cd Saga
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

- Windows PowerShell の場合:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
```

- 制御デモを確認します。

```bash
saga check examples/contest/diff_safe_control.saga
saga run examples/contest/diff_safe_control.saga
saga-control-report examples/contest/diff_safe_control.saga
```

- 制御レポート関連の回帰テストを実行します。

```bash
python -m unittest tests.test_control_report_053 tests.test_control_ga_050
```

## 機械制御向け機能

- Saga には、以下のソフトウェア実装・アダプタがあります。
  - PID / 2-DOF PID
  - フィルタリング
  - オブザーバ
  - Kalman / RLS
  - state-space
  - MPC 向けプリミティブ
  - jerk / acceleration 制限付きモーション
  - 同期軸制御
  - control allocation
  - kinematics
  - encoder
  - PWM
  - servo
  - DC motor
  - field-oriented control の構成要素
  - control cycle
  - deadline budget
  - watchdog
  - guard
  - safety latch
  - I2C
  - SPI
  - UART
  - CAN / CAN FD
  - Modbus
  - raw EtherCAT
  - CANopen / CiA 402
  - PLC / process image
  - capability による host / device access 制御
  - 明示的な resource lifetime
- 設計上の境界:
  - ソースレベル検査と、特定コントローラ・アクチュエータ・フィールドバス・安全回路の安全性主張は分離しています。

## ドローン・自律制御向け機能

- drone レイヤーには、以下の再利用可能な機能があります。
  - 姿勢制御
  - body-rate 制御
  - position 制御
  - quaternion / RPY 制御
  - multirotor allocation
  - flight-state 遷移
  - health transition
  - geofence
  - waypoint
  - RTL
  - landing helper
  - MAVLink 2 framing
  - MAVLink 検証
  - signing / verification
  - telemetry decode
  - DroneCAN
  - DShot / PWM
  - trajectory
  - link quality
  - vision / media
  - coordination integration
- 安全側の設計:
  - health observation が暗黙に arm / disarm / mode change を実行しない。
  - flight-state の変更は明示的に扱う。

## 言語・ツールチェーン

- 自作言語としての主要構成:
  - Lexer
  - Parser
  - AST
  - 静的型検査
  - algebraic data type
  - `Option[T]`
  - `Result[T, E]`
  - generics
  - match checking
  - namespaced module
  - separate-compilation interface
  - `async` / `await`
  - task group
  - `defer`
  - `using`
  - resource-oriented `move`
  - native code generation
  - WASM code generation
  - native ABI
  - deterministic package / workspace locking
  - diagnostics
  - LSP
  - debugger
  - profiler
  - capability audit
- 実装系:
  - Python による参照実装
  - 独立した Go 実装
- 2つの実装について:
  - 内部実装が同一である必要はありません。
  - 共通仕様で定義された観測可能な挙動は一致させる方針です。

## 主なコマンド

- `run`
  - 検査済み Saga ソースを実行。
- `check`
  - 実行せずに parse / type-check。
- `repl`
  - 対話型 REPL。
- `new`
  - 新規プロジェクト作成。
- `lint` / `fmt`
  - スタイル・ソース検査。
- `module`
  - separate compilation interface。
- `test`
  - Saga テストを実行。
- `lock` / `verify`
  - 再現可能な project locking。
- `production-check`
  - project / workspace の production gate。
- `pack`
  - deterministic `.sagapkg` を生成。
- `build`
  - native executable / WebAssembly をビルド。
- `conformance`
  - Standard Core self-conformance。
- `lsp`
  - Language Server Protocol server。
- `debug` / `profile`
  - デバッグ・プロファイリング。
- `capabilities`
  - 静的 capability audit。
- `doctor`
  - 環境診断。
- `saga-control-report`
  - ソースレベル control profile を説明・可視化。

## リポジトリ構成

- `saga/`
  - Python 参照実装
  - 制御ライブラリ
- `implementations/go/`
  - 独立した Go 実装
  - native control runtime
- `spec/`
  - 言語仕様
  - control profile 仕様
- `docs/`
  - 設計資料
  - 制御資料
  - qualification 資料
  - コンテスト資料
- `tests/`
  - 言語・制御回帰テスト
- `tools/`
  - qualification / release / developer tooling
- `validation/`
  - validation / qualification evidence
- `release/`
  - 凍結 source manifest
- `examples/`
  - Saga プログラム
  - 制御デモ
- `.github/workflows/`
  - CI
  - qualification workflow

## Qualification と証拠の境界

- より厳格な machine-production gate:

```bash
saga production-check --native --machine
```

- この gate の設計方針:
  - 必要な source-bound timing 証拠が無ければ失敗させる。
  - hazard 証拠が無ければ失敗させる。
  - WCET 証拠が無ければ失敗させる。
  - HIL 証拠が無ければ失敗させる。
  - hardware safety 証拠が無ければ失敗させる。
- 重要:
  - ソフトウェア CI が green であることは、実行されたソフトウェアテストの証拠です。
  - 任意のロボット・モータ・PLC・ドローン・無線リンク・安全回路の物理的安全性を証明するものではありません。
- 関連資料:
  - `docs/MACHINE_DRONE_CONTROL_0.53.md`
  - `spec/SAGA_PRODUCTION_GA_CONTROL_0.50.md`
  - `docs/PRODUCTION_GA_CONTROL_0.50.md`
  - `RELEASE_NOTES_0.50.0.md`
  - `saga-REVIEW_REPORT-0.50.0.md`
  - `saga-VALIDATION-0.50.0.md`

## コントリビューション

- 基本方針:
  - `CONTRIBUTING.md` を参照。
- machine / drone 関連の変更:
  - 影響する制御面に対する回帰テストを追加する。
  - テストを通すためだけに authority / safety check を弱めない。
  - 開発版に合わせる目的で、過去の凍結リリース証拠を書き換えない。
- 最低限確認する Go 側のテスト:

```bash
cd implementations/go
go test ./...
go vet ./...
```

## セキュリティ境界

- 脆弱性報告と machine-control safety boundary:
  - `SECURITY.md` を参照。
- リポジトリへ含めてはいけないもの:
  - secrets
  - MAVLink signing key
  - device credential
  - production token
  - 機密性の高い第三者データ
- 表現上の注意:
  - simulated validation を physical HIL と表現しない。
  - hosted validation を physical HIL と表現しない。
  - software-only validation を certification と表現しない。

## コンテスト提出用

- 審査員向け概要:
  - `SUBMISSION_README_JA.md`
- 2分デモ:
  - `CONTEST_DEMO.md`
- 開発・提出手順:
  - `docs/CONTEST_SUBMISSION_DEVELOPMENT_GUIDE_JA.md`
- プログラミングフロー:
  - `docs/PROGRAMMING_FLOW_JA.md`
- 提出前チェック:
  - `docs/SUBMISSION_CHECKLIST_JA.md`
- 提出 ZIP の生成:

```bash
python tools/build_contest_submission.py --check-only
python tools/build_contest_submission.py
```
