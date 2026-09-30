# Saga コンテスト提出前チェックリスト

提出直前に上から順に確認します。`[ ]` を `[x]` に変えて保存する必要はありません。最終確認用の固定手順です。

## A. ソースコード

- [ ] `saga/` が存在し、言語処理系のソースが入っている
- [ ] `pyproject.toml` が入っている
- [ ] `README.md` が入っている
- [ ] `LICENSE` が入っている
- [ ] `SUBMISSION_README_JA.md` が入っている
- [ ] `CONTEST_DEMO.md` が入っている
- [ ] `docs/PROGRAMMING_FLOW_JA.md` が入っている
- [ ] `tests/` が提出 ZIP に含まれている
- [ ] キャッシュ・仮想環境・生成バイナリが提出 ZIP に入っていない

## B. 自動検証

```bash
python tools/build_contest_submission.py --check-only
```

- [ ] `[OK] Saga contest submission preflight` が表示される

```bash
python -m pytest tests/test_build_contest_submission.py -q
```

- [ ] 提出 ZIP 生成機能のテストが成功する

可能なら:

```bash
python -m pytest -q
```

- [ ] プロジェクト全体のテスト結果を確認した
- [ ] 失敗がある場合、今回の提出整備で新しく発生したものではないことを確認した

## C. 提出 ZIP

```bash
python tools/build_contest_submission.py
```

- [ ] `dist/Saga-DIFF-Shizuoka-2026.zip` が生成された
- [ ] 生成時に表示された ZIP の SHA-256 を記録した
- [ ] ZIP を一度展開できることを確認した
- [ ] ZIP の最上位ディレクトリ名が `Saga-DIFF-Shizuoka-2026/` になっている
- [ ] ZIP 内に `SUBMISSION_SHA256SUMS.txt` がある
- [ ] `SUBMISSION_SHA256SUMS.txt` に提出ファイルのハッシュ一覧がある

## D. 審査員が迷わないか

- [ ] ZIP を開いた直後に `SUBMISSION_README_JA.md` が見つかる
- [ ] 30秒程度で「Saga が何の言語か」を説明できる
- [ ] 「どこが自作言語の実装か」と聞かれたとき `saga/` と処理フローを示せる
- [ ] Lexer / Parser / AST / 型・意味検査 / Runtime の流れを説明できる
- [ ] 制御・安全性検査が「実機の安全認証そのもの」ではなく、ソースレベルの検査であることを説明できる

## E. 2分以内の実行動画

- [ ] `CONTEST_DEMO.md` に沿って撮影した
- [ ] 動画が2分以内である
- [ ] Saga のソースコードが映る
- [ ] 実際にコマンドを実行する場面が映る
- [ ] 変更前の状態が分かる
- [ ] ソースを1か所変更する場面が分かる
- [ ] 再実行によって結果が変わることが分かる
- [ ] 最後に成功結果または診断結果を明確に見せる
- [ ] 文字が読める解像度になっている
- [ ] 個人情報、トークン、パスワード、不要な通知が映っていない

## F. 応募フォームへ提出するもの

- [ ] 2分以内の実行動画
- [ ] Saga のプログラムソースが分かる提出物
- [ ] 必要に応じてプログラミングフロー資料
- [ ] 応募フォーム上の作品名・説明と `SUBMISSION_README_JA.md` の説明が矛盾していない
- [ ] 提出ファイルを間違えて古い ZIP にしていない

## G. 最終固定

- [ ] 提出に使うコミットを確定した
- [ ] そのコミットから ZIP を作り直した
- [ ] ZIP の SHA-256 を最終版として保存した
- [ ] 提出後に「どのソースを提出したか」再現できる

### 最終コマンド

```bash
python tools/build_contest_submission.py --check-only
python -m pytest tests/test_build_contest_submission.py -q
python tools/build_contest_submission.py
```

この3つが成功した状態の ZIP を提出版とします。
