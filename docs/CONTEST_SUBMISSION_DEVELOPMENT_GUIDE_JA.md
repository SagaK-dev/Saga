# Saga コンテスト提出用 開発手順書

対象: DIFF Shizuoka 2026 プログラミング部門  
目的: Saga を「自作プログラミング言語」として、第三者が短時間で内容・独自性・動作を確認できる提出物へ整える。

## 0. 完成条件

提出版は次の条件をすべて満たした時点で完成とする。

1. 言語本体のソースコードが提出 ZIP に含まれる。
2. `README.md` と `SUBMISSION_README_JA.md` から、目的・特徴・実行方法へ到達できる。
3. 字句解析 → 構文解析 → 型/意味検査 → 安全性検査 → 実行、という Saga の処理の流れを説明できる。
4. 既存のコンテストデモを再現できる。
5. 提出 ZIP は同一ソースから何度生成しても同じ内容になる。
6. ZIP 内の各ファイルに SHA-256 が付与され、提出物の同一性を確認できる。
7. キャッシュ、仮想環境、ビルド生成物、動画など、ソース提出に不要なファイルを含めない。
8. 提出直前チェックリストを通過する。

## 1. 現状調査

最初にリポジトリを読み、以下を確認する。

- 言語実装: `saga/`
- パッケージ/実行設定: `pyproject.toml`
- 一般向け説明: `README.md`
- コンテスト実演: `CONTEST_DEMO.md`, `saga/contest_demo.py`
- 仕様・設計資料: `docs/`
- テスト: `tests/`

この段階では言語仕様そのものを変更しない。提出直前の作業で新機能を増やすと、既存機能を壊す可能性が高いためである。

## 2. 審査員向け入口を作る

`SUBMISSION_README_JA.md` を提出物の最初の入口とする。ここには次だけを短くまとめる。

- Saga は何のための言語か
- 何が自作部分か
- どのファイルを見れば実装を確認できるか
- 最短のセットアップ/実行方法
- 2分デモの実行方法
- 詳細仕様と処理フローへのリンク

長い歴史や全機能一覧より、「何を作り、どう確認できるか」を優先する。

## 3. 言語処理系の流れを可視化する

`docs/PROGRAMMING_FLOW_JA.md` に処理フローを書く。

最低限、次の関係を説明する。

```text
.saga ソース
   ↓
Lexer（字句解析）
   ↓
Parser（構文解析 / AST）
   ↓
Type / Semantic Check
   ↓
Safety / Control Check
   ↓
Interpreter / Runtime
   ↓
実行結果・制御出力
```

これは「Python スクリプトを寄せ集めたもの」ではなく、「ソース言語を解析して意味を与え、検査し、実行する処理系」であることを確認しやすくするための資料でもある。

## 4. 再現可能な提出 ZIP を作る

`tools/build_contest_submission.py` を使用する。

### 4.1 事前検査だけ行う

```bash
python tools/build_contest_submission.py --check-only
```

`[OK] Saga contest submission preflight` が表示されること。

### 4.2 ZIP を生成する

```bash
python tools/build_contest_submission.py
```

標準出力先:

```text
dist/Saga-DIFF-Shizuoka-2026.zip
```

ZIP は以下の方針で生成する。

- パスを辞書順に固定
- ZIP の時刻情報を固定
- ファイル権限を固定
- `dist/`, `build/`, `.venv/`, キャッシュ類を除外
- コンパイル済みバイナリや動画を除外
- ZIP 内に `SUBMISSION_SHA256SUMS.txt` を生成

これにより、同一のリポジトリ状態から生成した ZIP は再現可能になる。

## 5. 自動テスト

提出用パッケージ生成機能のテストを実行する。

```bash
python -m pytest tests/test_build_contest_submission.py -q
```

その後、可能ならプロジェクト全体のテストも実行する。

```bash
python -m pytest -q
```

失敗がある場合は、提出用変更による回帰か、既存の失敗かを区別する。

## 6. デモ確認

既存の `CONTEST_DEMO.md` に従って実演する。

確認ポイント:

1. 実行開始から結果確認までを2分以内に収められる。
2. Saga のソースコードが画面で確認できる。
3. 「変更前 → 1か所変更 → 結果が変わる」の因果関係が見える。
4. 最後に成功/検証済みを明確に示す。
5. 編集箇所と実行結果が同じ画面、または連続した画面で追える。

## 7. 提出資料の対応関係

| 提出時に確認したい内容 | Saga 側のファイル |
|---|---|
| 最初に読む説明 | `SUBMISSION_README_JA.md` |
| プログラムのソース | `saga/`, `examples/`, その他リポジトリ内ソース |
| 2分デモ手順 | `CONTEST_DEMO.md` |
| プログラミングフロー | `docs/PROGRAMMING_FLOW_JA.md` |
| 言語仕様・設計 | `docs/` |
| 再現可能な提出 ZIP | `tools/build_contest_submission.py` |
| 提出前確認 | `docs/SUBMISSION_CHECKLIST_JA.md` |

## 8. 提出直前の固定手順

提出するコミットを決めたら、以後は原則として言語機能を追加しない。

```bash
python tools/build_contest_submission.py --check-only
python -m pytest tests/test_build_contest_submission.py -q
python tools/build_contest_submission.py
```

表示された ZIP の SHA-256 をメモする。その後 ZIP を一度展開し、少なくとも次を目視する。

- `Saga-DIFF-Shizuoka-2026/SUBMISSION_README_JA.md`
- `Saga-DIFF-Shizuoka-2026/saga/`
- `Saga-DIFF-Shizuoka-2026/docs/PROGRAMMING_FLOW_JA.md`
- `Saga-DIFF-Shizuoka-2026/CONTEST_DEMO.md`
- `Saga-DIFF-Shizuoka-2026/SUBMISSION_SHA256SUMS.txt`

最後に `docs/SUBMISSION_CHECKLIST_JA.md` を上から順に確認する。

## 9. 今回の作業順序

この手順書に基づき、今回の提出整備では以下の順序で作業する。

1. 既存実装・既存デモの確認
2. 審査員向け README の追加
3. プログラミングフロー資料の追加
4. 再現可能 ZIP 生成スクリプトの追加
5. ZIP 生成スクリプトの自動テスト追加
6. 提出前チェックリスト追加
7. 変更差分レビュー
8. CI / テスト確認
9. 提出用 ZIP 生成と SHA-256 確認
10. 2分以内の動画撮影・応募フォームへのアップロード（人が行う最終工程）

この順序を崩さないことで、提出直前の変更範囲を「提出品質」に限定し、言語本体の安定性を保つ。
