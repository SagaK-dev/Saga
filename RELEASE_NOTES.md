# Saga リリースノート

- このファイルは、Saga のリリース情報へアクセスするための安定した入口です。
- バージョンごとの詳細は、それぞれ独立した `RELEASE_NOTES_<version>.md` に保存します。
- `main` が更新されても、過去バージョンのリリース証拠・履歴を後から書き換えない方針です。

## 現在の開発ライン — Saga 0.53.0

- バージョン:
  - **Saga 0.53.0 — Machine & Drone Control Focus**
- 位置付け:
  - 開発中のマイルストーン。
  - 機械制御・ロボティクス・自律システム・ドローンを主要な開発対象として強化。
  - 一般用途の言語機能も継続して保持。
- 一般用途で想定する利用:
  - tooling
  - telemetry
  - configuration
  - simulation
  - integration
- 詳細:
  - [`RELEASE_NOTES_0.53.0.md`](RELEASE_NOTES_0.53.0.md)
- 注意:
  - 0.53.0 は、新しい凍結 production release ではありません。
  - 以下を意味しません。
    - physical HIL 実施済み
    - target WCET 証明済み
    - functional-safety 認証済み
    - airworthiness 認証済み
    - その他の実機固有認証済み

## 最新の凍結リリース — Saga 0.50.0

- バージョン:
  - **Saga 0.50.0 — Production GA Control Hardening**
- 状態:
  - 現在も最新の凍結リリース。
- 凍結ブランチ:
  - `release/0.50.0-production-ga`
- 詳細:
  - [`RELEASE_NOTES_0.50.0.md`](RELEASE_NOTES_0.50.0.md)
- 0.50.0 の資料では:
  - 凍結されたソース状態を確認できます。
  - qualification / evidence の境界を確認できます。

## 過去のリリース

- `RELEASE_NOTES_<version>.md` は、各バージョンの履歴記録です。
- 過去のリリースノートに書かれている内容を、そのまま現在の `main` の状態と解釈しないでください。
- 現在の状態として扱うのは、このファイルで明示的に「現在の開発ライン」または「最新の凍結リリース」と示したバージョンです。

## 関連ファイル

- 現在の開発版:
  - `RELEASE_NOTES_0.53.0.md`
- 最新の凍結版:
  - `RELEASE_NOTES_0.50.0.md`
- プロジェクト概要:
  - `README.md`
- コンテスト提出概要:
  - `SUBMISSION_README_JA.md`
