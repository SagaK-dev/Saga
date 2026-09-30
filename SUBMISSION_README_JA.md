# Saga — コンテスト提出版 まずここを読んでください

Saga は、**安全性を検査しながら実行できる制御・システム向けの自作プログラミング言語**です。

この提出物には、言語の字句解析・構文解析・型/意味検査・安全性検査・実行系、言語仕様、サンプル、テスト、コンテスト用デモを含めています。

## 1. 何を自作したか

Saga では、単に既存言語のコードを実行するだけではなく、Saga のソースコードを入力として処理する言語処理系を実装しています。

主な確認場所:

- `saga/` — Saga の言語処理系本体
- `saga/contest_demo.py` — コンテスト実演用の検証コード
- `docs/` — 言語仕様・設計・安全性・提出資料
- `tests/` — 言語処理系と周辺機能のテスト
- `examples/` — Saga のサンプルコード（存在する構成ではここから実行例を確認できます）

処理の全体像は `docs/PROGRAMMING_FLOW_JA.md` を参照してください。

## 2. Saga の特徴

Saga の提出版では、特に次の点を見てください。

1. **独自のソース言語を解析する処理系**  
   Lexer / Parser / AST / 型・意味検査 / Runtime という言語実装の基本部分を持ちます。

2. **制御用途を意識した安全性検査**  
   実行してから危険を発見するのではなく、実行前に検査できる設計を重視しています。

3. **仕様と実装とテストを同じリポジトリで確認できる**  
   「説明だけ」ではなく、実装コードと検証コードを追える構成です。

4. **再現可能なコンテスト提出 ZIP**  
   `tools/build_contest_submission.py` により、不要な生成物を除外し、SHA-256 付きのソース ZIP を生成します。

## 3. 最短の確認方法

Python が利用できる環境でリポジトリを開きます。

### 提出物の事前検査

```bash
python tools/build_contest_submission.py --check-only
```

成功時:

```text
[OK] Saga contest submission preflight
```

### 提出 ZIP の生成

```bash
python tools/build_contest_submission.py
```

生成先:

```text
dist/Saga-DIFF-Shizuoka-2026.zip
```

実行後、ZIP 全体の SHA-256 も表示されます。

## 4. コンテスト用デモ

2分以内の実演については、まず次を開いてください。

```text
CONTEST_DEMO.md
```

デモでは、Saga のコードと実行結果の関係が分かるように、変更前の確認、コードの1か所変更、再実行、結果変化の確認という流れを使います。

## 5. プログラミングフロー

```text
Saga ソース (.saga)
      ↓
Lexer / Tokenize
      ↓
Parser / AST
      ↓
Type・Semantic Check
      ↓
Safety / Control Check
      ↓
Interpreter / Runtime
      ↓
実行結果・制御出力
```

より詳しい説明: `docs/PROGRAMMING_FLOW_JA.md`

## 6. どこから読むか

審査・確認の目的別に、次の順番がおすすめです。

| 確認したいこと | ファイル |
|---|---|
| 30秒で概要を知る | この `SUBMISSION_README_JA.md` |
| 実際の言語実装を見る | `saga/` |
| 言語全体の説明を見る | `README.md` |
| 処理の流れを見る | `docs/PROGRAMMING_FLOW_JA.md` |
| 2分デモを再現する | `CONTEST_DEMO.md` |
| 開発・提出手順を見る | `docs/CONTEST_SUBMISSION_DEVELOPMENT_GUIDE_JA.md` |
| 提出前確認をする | `docs/SUBMISSION_CHECKLIST_JA.md` |

## 7. 提出 ZIP の真正性確認

ZIP のルートにある次のファイルには、ZIP に入った各ソースファイルの SHA-256 が記録されます。

```text
SUBMISSION_SHA256SUMS.txt
```

また、ZIP 生成時には ZIP 自体の SHA-256 も端末に表示されます。

## 8. 開発者向け

提出版を更新する場合は、先に `docs/CONTEST_SUBMISSION_DEVELOPMENT_GUIDE_JA.md` の手順に従ってください。提出直前は新機能追加よりも、デモ再現性・テスト・説明の一致を優先します。
