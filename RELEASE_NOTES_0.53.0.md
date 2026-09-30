# Saga 0.53.0 — Machine & Drone Control Focus

- Saga 0.53.0 は、**機械制御・ロボティクス・ドローン・自律システムを主要な開発方向として明確化した開発マイルストーン**です。
- 一般用途の言語機能は削除せず、以下の用途向けに継続して保持します。
  - tooling
  - telemetry
  - configuration
  - simulation
  - application integration

## 0.53 の主な変更点

- **理論上 60 kHz の制御プロファイルに対応**
  - `@control_tick` が小数 µs の予算を受け付けます。
  - 例:
    - `@control_tick(60000, 12.5)`
  - `machine.cyclic_clock` は、丸めたナノ秒周期を毎回加算する方式ではなく、正確な有理数周波数の位相から deadline を導出します。

- **高レート機械制御向けプリミティブを拡張**
  - `machine.deadband` を追加。
  - `machine.integrate_clamped` を追加。
  - 既存の `machine.slew` を checked control tick 内で利用可能に変更。
  - 既存の `machine.low_pass` を checked control tick 内で利用可能に変更。

- **native-friendly な Q1.31 制御経路を追加**
  - saturating add
  - saturating subtract
  - saturating multiply
  - saturating MAC
  - ratio construction
  - Native Codegen では、これらを固定幅整数 C helper へ直接 lower します。
  - hot path で hosted decimal/runtime call を避ける設計です。

- **Saga の位置付けを明確化**
  - 「汎用言語に制御ライブラリを追加したもの」ではなく、**制御システム向け言語**として開発方向を整理。
  - 一般用途の言語機能は、tooling・telemetry・configuration・simulation・integration 用として継続。

- **machine / drone 機能を言語・ツールチェーンの中心機能として整理**
  - `machine` module
  - `drone` module
  - control annotation
  - hardware capability
  - resource lifetime rule
  - production qualification path

- **説明可能な Control Report を追加**
  - 通常の Saga 言語検査と control-profile analysis を統合。
  - timing contract の要約を表示。
  - 安定した diagnostics を表示。
  - software evidence と target-specific physical evidence の境界を明示。

- **control contract の適用範囲を統一**
  - class method に適用。
  - checked helper に適用。
  - Python 参照実装と独立した Go 実装の両方で整合性を強化。

- **machine / drone 回帰テストを core CI に常設**
  - Python 参照実装を対象。
  - 独立した Go 実装を対象。

- **isolated Python plugin bridge を強化**
  - plugin manifest は external module export を要求できます。
  - ただし manifest 自身では許可できません。
  - embedding host が、正確な module / export pair を独立して承認する必要があります。

- **incremental `SagaSession` の transactional 性を強化**
  - language-visible enum registration を transaction 対象に変更。
  - decimal precision を transaction 対象に変更。
  - REPL / notebook の submission が runtime failure した場合、後続 submission へ変更が漏れないようにします。

- **`SagaSession` の recursion error 処理を統一**
  - parsing 中の host recursion exhaustion を Saga の stable diagnostic へ変換。
  - type checking 中も同様に変換。
  - execution 中も同様に変換。
  - raw Python `RecursionError` をそのまま利用者へ漏らさない方針です。

- **namespaced-module interface をデフォルト安全側へ変更**
  - 通常の `compile_file` / `run_file` では、見た目上新しい `.smi.json` があっても compiler validation の証拠として無条件には信頼しません。
  - 通常経路では source を再チェックします。
  - trusted build pipeline のみ、明示的に `trust_module_interfaces=True` を指定できます。

- **コンテスト向け safe / unsafe デモを再現可能化**
  - safe / unsafe の差分が、制御経路への正確な1行追加であることを機械的に検証。
  - expected runtime output を fail-closed で確認。
  - timing contract を確認。
  - analysis scope を確認。
  - 正確な `SAGA-C492` diagnostic を確認。
  - clean wheel installation smoke test を追加。
  - source-distribution installation smoke test を追加。

- **制御向けサンプル・設計資料・コンテスト資料を追加**
  - software evidence の範囲を説明。
  - target-specific physical evidence との違いを説明。

## この開発ラインで継続する機械制御機能

- Saga 0.53 系では、既存の以下の機能を継続します。
  - PID
  - 2-DOF PID
  - filtering
  - observation
  - advanced motion / control primitive
  - PWM
  - servo
  - motor
  - encoder
  - I2C
  - SPI
  - UART
  - CAN
  - CAN FD
  - Modbus
  - EtherCAT
  - CANopen / CiA 402
  - PLC / process image
  - watchdog
  - deadline / control guard
  - explicit safety latch behavior

## この開発ラインで継続するドローン機能

- Saga 0.53 系では、既存の以下の機能を継続します。
  - attitude control
  - quaternion control
  - rate control
  - position control
  - geofencing
  - mission
  - RTL helper
  - landing helper
  - MAVLink 2
  - MAVLink signing
  - DroneCAN
  - DShot / PWM ESC helper
  - 3D jerk-limited trajectory
  - multirotor control allocation
  - link monitoring
  - visual servoing
  - VIO / SLAM
  - multi-drone coordination
  - offboard / SITL integration path

## 言語としての方向性

- Saga が目標とする組み合わせ:
  - **C のようなハードウェア到達性**
  - **Rust のような明示的 resource / authority boundary**
  - **Python のような読みやすさ**
- これは設計目標です。
- 以下を主張するものではありません。
  - C / Rust / Python と同等の ecosystem maturity
  - 同等の optimization depth
  - 同等の hardware coverage
  - Rust と同等の memory-safety guarantee
  - 既存成熟言語と同等の certification history

## Qualification の境界

- 0.53.0 へのバージョン更新やソフトウェアテストの追加だけでは、新しい凍結 production release にはなりません。
- 最新の凍結リリース:
  - **Saga 0.50.0**
- 0.50.0 は、後続 release candidate が独自の source manifest と qualification evidence を取得するまで最新凍結版として扱います。
- 以下は、それぞれ別の evidence level として扱います。
  - software CI
  - native-host execution
  - SITL
  - physical HIL
  - WCET analysis
  - functional-safety certification
  - regulatory certification
- Saga 0.53.0 は、物理システムや安全性に関する認証済み状態を意味しません。

## 関連資料

- プロジェクト概要:
  - `README.md`
- リリース情報の入口:
  - `RELEASE_NOTES.md`
- コンテスト提出概要:
  - `SUBMISSION_README_JA.md`
- コンテスト向け設計資料:
  - `docs/DIFF_SHIZUOKA_2026.md`
- プログラミングフロー:
  - `docs/PROGRAMMING_FLOW_JA.md`
