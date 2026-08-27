from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
from typing import Any

from . import __version__
from .api import run_file
from .control_report import analyze_control_file, render_control_report
from .control_report_html import render_control_report_html


SAFE_SOURCE = """use machine

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

print(current_tick(0.6))
"""

RISKY_LINE = "    let sampled_at = machine.monotonic_ns()\n"
_TICK_OPEN = "fn current_tick(error: decimal) -> decimal {\n"
UNSAFE_SOURCE = SAFE_SOURCE.replace(_TICK_OPEN, _TICK_OPEN + RISKY_LINE, 1)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _single_change_is_exact() -> bool:
    return (
        UNSAFE_SOURCE.count(RISKY_LINE) == 1
        and UNSAFE_SOURCE.replace(RISKY_LINE, "", 1) == SAFE_SOURCE
    )


def _first_issue(report: dict[str, Any]) -> dict[str, Any] | None:
    issues = report.get("issues") or []
    if issues:
        return issues[0]

    language = report.get("language_check") or {}
    diagnostic = language.get("diagnostic")
    return diagnostic if isinstance(diagnostic, dict) else None


def _timing_data(report: dict[str, Any]) -> dict[str, Any] | None:
    functions = report.get("control_functions") or []
    for item in functions:
        if item.get("role") == "tick" and item.get("timing"):
            return item["timing"]
    return None


def _display_number(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _report_card(
    title: str,
    description: str,
    report: dict[str, Any],
    report_name: str,
) -> str:
    verdict = str(report.get("verdict", "unknown")).lower()
    verdict_label = {
        "pass": "PASS",
        "fail": "要修正",
        "invalid": "無効",
        "not-applicable": "対象外",
    }.get(verdict, verdict.upper())
    verdict_class = "pass" if verdict == "pass" else "fail" if verdict in {"fail", "invalid"} else "neutral"

    timing = _timing_data(report)
    timing_html = '<p class="muted">周期契約はありません。</p>'
    if timing:
        timing_html = f"""
        <dl class="metrics">
          <div><dt>制御周期</dt><dd>{_display_number(timing['period_us'])} µs</dd></div>
          <div><dt>実行予算</dt><dd>{_display_number(timing['budget_us'])} µs</dd></div>
          <div><dt>余白</dt><dd>{_display_number(timing['headroom_us'])} µs</dd></div>
        </dl>
        """

    issue = _first_issue(report)
    issue_html = '<p class="diagnostic ok">制御プロファイル違反は検出されませんでした。</p>'
    if issue:
        location = f"{issue.get('line', '?')}:{issue.get('column', '?')}"
        hint = str(issue.get("hint") or "該当箇所を見直してください。")
        issue_html = f"""
        <div class="diagnostic problem">
          <div class="diag-head">
            <code>{html.escape(str(issue.get('code') or 'SAGA-C?'))}</code>
            <span>行 {html.escape(location)}</span>
          </div>
          <p>{html.escape(str(issue.get('message') or '制御プロファイル違反'))}</p>
          <small><strong>修正案:</strong> {html.escape(hint)}</small>
        </div>
        """

    return f"""
    <article class="result-card {verdict_class}">
      <div class="card-head">
        <div>
          <p class="eyebrow">{html.escape(title)}</p>
          <p class="card-description">{html.escape(description)}</p>
        </div>
        <span class="verdict {verdict_class}">{html.escape(verdict_label)}</span>
      </div>
      {timing_html}
      {issue_html}
      <a class="detail-link" href="{html.escape(report_name)}">詳細な Control Report を開く →</a>
    </article>
    """


def _render_index(
    safe_report: dict[str, Any],
    unsafe_report: dict[str, Any],
    safe_runtime_output: list[str],
) -> str:
    safe_card = _report_card(
        "安全な例",
        "周期制御の中には、追跡できる計算と検査済みヘルパーだけがあります。",
        safe_report,
        "safe-report.html",
    )
    unsafe_card = _report_card(
        "危険な1行を追加",
        "同じコードにホスト時刻の取得を1行だけ加え、同じ解析経路で検査します。",
        unsafe_report,
        "unsafe-report.html",
    )

    risky_line = html.escape(RISKY_LINE.strip())
    safe_source = html.escape(SAFE_SOURCE)
    timing = _timing_data(safe_report) or {}
    period = _display_number(timing.get("period_us", "?"))
    budget = _display_number(timing.get("budget_us", "?"))
    headroom = _display_number(timing.get("headroom_us", "?"))
    unsafe_issue = _first_issue(unsafe_report) or {}
    unsafe_code = html.escape(str(unsafe_issue.get("code") or "SAGA-C?"))
    runtime_text = html.escape(" / ".join(safe_runtime_output) or "(出力なし)")

    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>Saga | DIFF SHIZUOKA contest demo</title>
  <style>
    :root {{
      font-family: Inter, "Noto Sans JP", "Yu Gothic", "Hiragino Kaku Gothic ProN", system-ui, sans-serif;
      color: #172033;
      background: #f5f7fb;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; line-height: 1.65; }}
    a {{ color: inherit; }}
    code, pre {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    .wrap {{ width: min(1120px, calc(100% - 32px)); margin: 0 auto; }}
    .hero {{ padding: 56px 0 34px; }}
    .hero-grid {{ display: grid; grid-template-columns: 1.4fr .8fr; gap: 24px; align-items: stretch; }}
    .hero-main, .hero-proof, .panel, .result-card {{
      background: white;
      border: 1px solid #dfe5ef;
      border-radius: 24px;
      box-shadow: 0 12px 34px rgba(23, 32, 51, .06);
    }}
    .hero-main {{ padding: clamp(26px, 5vw, 52px); }}
    .hero-proof {{ padding: 28px; display: flex; flex-direction: column; justify-content: center; }}
    .eyebrow {{ margin: 0 0 8px; font-size: 12px; font-weight: 800; letter-spacing: .11em; text-transform: uppercase; color: #54627a; }}
    h1 {{ margin: 0; font-size: clamp(34px, 6vw, 64px); line-height: 1.08; letter-spacing: -.04em; }}
    .lead {{ margin: 20px 0 0; max-width: 780px; font-size: 18px; color: #48566d; }}
    .proof-number {{ margin: 0; font-size: clamp(44px, 8vw, 72px); line-height: 1; font-weight: 900; }}
    .proof-copy {{ margin: 12px 0 0; color: #54627a; }}
    .section {{ padding: 16px 0 34px; }}
    .section-title {{ margin: 0 0 14px; font-size: 24px; letter-spacing: -.02em; }}
    .problem-grid, .results, .value-grid {{ display: grid; gap: 16px; }}
    .problem-grid {{ grid-template-columns: repeat(3, 1fr); }}
    .problem-item {{ padding: 18px; border-radius: 18px; background: #eef3ff; }}
    .problem-item strong {{ display: block; font-size: 22px; margin-bottom: 4px; }}
    .problem-item p {{ margin: 0; color: #526079; font-size: 14px; }}
    .results {{ grid-template-columns: repeat(2, 1fr); }}
    .result-card {{ padding: 24px; border-top-width: 6px; }}
    .result-card.pass {{ border-top-color: #138a5b; }}
    .result-card.fail {{ border-top-color: #c43d3d; }}
    .card-head {{ display: flex; justify-content: space-between; gap: 16px; align-items: start; }}
    .card-description {{ margin: 0; color: #536179; }}
    .verdict {{ flex: 0 0 auto; border-radius: 999px; padding: 7px 11px; font-size: 12px; font-weight: 900; }}
    .verdict.pass {{ background: #dcf7eb; color: #0b6843; }}
    .verdict.fail {{ background: #fde6e6; color: #8e2424; }}
    .verdict.neutral {{ background: #e9edf3; color: #44516a; }}
    .metrics {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: 22px 0; }}
    .metrics div {{ padding: 12px; background: #f6f8fb; border-radius: 14px; }}
    .metrics dt {{ font-size: 11px; color: #65738b; }}
    .metrics dd {{ margin: 3px 0 0; font-size: 20px; font-weight: 850; }}
    .diagnostic {{ margin: 14px 0; padding: 14px; border-radius: 14px; font-size: 14px; }}
    .diagnostic.ok {{ background: #edf9f3; color: #135a40; }}
    .diagnostic.problem {{ background: #fff1f1; color: #702929; }}
    .diagnostic p {{ margin: 8px 0; }}
    .diag-head {{ display: flex; flex-wrap: wrap; gap: 9px; align-items: center; }}
    .diag-head code {{ padding: 3px 7px; border-radius: 8px; background: #8f2f2f; color: white; font-weight: 800; }}
    .detail-link {{ display: inline-block; margin-top: 4px; font-weight: 800; text-underline-offset: 3px; }}
    .panel {{ padding: 26px; }}
    .diff {{ margin: 14px 0 0; padding: 18px; overflow-x: auto; border-radius: 16px; background: #111827; color: #f8fafc; }}
    .added {{ display: inline-block; min-width: 100%; margin: 5px 0; padding: 5px 8px; background: #4b1f26; color: #ffd8dc; }}
    .source-note {{ margin: 14px 0 0; color: #55647d; }}
    .value-grid {{ grid-template-columns: repeat(3, 1fr); }}
    .value-item {{ padding: 20px; border: 1px solid #dfe5ef; border-radius: 18px; background: white; }}
    .value-item h3 {{ margin: 0 0 8px; font-size: 17px; }}
    .value-item p {{ margin: 0; color: #55647d; font-size: 14px; }}
    .boundary {{ background: #fff8e7; border-color: #ead79b; }}
    .boundary strong {{ color: #714f00; }}
    .commands {{ margin: 14px 0 0; padding: 16px; overflow-x: auto; border-radius: 14px; background: #111827; color: white; }}
    footer {{ padding: 10px 0 46px; color: #66748b; font-size: 13px; }}
    @media (max-width: 820px) {{
      .hero-grid, .results {{ grid-template-columns: 1fr; }}
      .problem-grid, .value-grid {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 560px) {{
      .wrap {{ width: min(100% - 22px, 1120px); }}
      .hero {{ padding-top: 22px; }}
      .hero-main, .hero-proof, .panel, .result-card {{ border-radius: 18px; }}
      .metrics {{ grid-template-columns: 1fr; }}
      .card-head {{ flex-direction: column; }}
    }}
    @media print {{
      body {{ background: white; }}
      .hero-main, .hero-proof, .panel, .result-card {{ box-shadow: none; }}
    }}
  </style>
</head>
<body>
  <header class="hero">
    <div class="wrap hero-grid">
      <section class="hero-main">
        <p class="eyebrow">Saga {html.escape(__version__)} · DIFF SHIZUOKA 2026</p>
        <h1>機械制御の「危ない1行」を、<br>実行前に説明する言語。</h1>
        <p class="lead">Sagaは、制御周期と実行予算をソースコードに書き、周期制御の中へ入り込んだ追跡しにくい処理を、場所・理由・修正案つきで示します。このデモは安全例と危険例を同じ解析器でその場で検査した結果です。</p>
      </section>
      <aside class="hero-proof" aria-label="デモの比較条件">
        <p class="eyebrow">比較条件</p>
        <p class="proof-number">1行</p>
        <p class="proof-copy">2つのプログラムの差は、周期制御内に追加したホスト時刻取得の1行だけ。削除すると安全例へ完全に戻ることも自動検証しています。</p>
      </aside>
    </div>
  </header>

  <main class="wrap">
    <section class="section">
      <h2 class="section-title">なぜ必要か</h2>
      <div class="problem-grid">
        <article class="problem-item"><strong>{period} µs</strong><p>20,000 Hzなら1周期はわずか{period}マイクロ秒です。</p></article>
        <article class="problem-item"><strong>{budget} µs</strong><p>この例では周期内の実行予算をソース上で{budget}マイクロ秒と宣言します。</p></article>
        <article class="problem-item"><strong>{headroom} µs</strong><p>宣言上の余白は{headroom}マイクロ秒。周期内に何を入れるかを見える形でレビューできます。</p></article>
      </div>
      <p class="source-note"><strong>実際のSaga実行結果:</strong> <code>{runtime_text}</code> — 安全例はデモ生成時に処理系で実行し、その出力をこのページへ埋め込んでいます。</p>
    </section>

    <section class="section">
      <h2 class="section-title">同じ解析器で比較</h2>
      <div class="results">{safe_card}{unsafe_card}</div>
    </section>

    <section class="section">
      <article class="panel">
        <p class="eyebrow">唯一の変更点</p>
        <h2 class="section-title">周期制御の中に、時刻取得を1行だけ追加</h2>
        <pre class="diff" aria-label="安全例と危険例のコード差分"><code>@control_tick(20000, 35)
fn current_tick(error: decimal) -> decimal {{
<span class="added">+ {risky_line}</span>
    return clamp_command(error * 0.5)
}}</code></pre>
        <p class="source-note">周波数、実行予算、ヘルパー、戻り値は同一です。Sagaはこの1行を <code>{unsafe_code}</code> として実際のファイル解析経路から検出します。</p>
      </article>
    </section>

    <section class="section">
      <h2 class="section-title">この作品で見てほしい3点</h2>
      <div class="value-grid">
        <article class="value-item"><h3>1. 言語機能として実装</h3><p>単なる警告画面ではなく、lexer・parser・型検査・制御呼び出し経路の検査・安定した診断コードまで処理系に組み込んでいます。</p></article>
        <article class="value-item"><h3>2. 初学者にも理由が見える</h3><p>「ダメ」だけで終わらず、どの行が、なぜ問題で、どう直すかをControl Reportにまとめます。</p></article>
        <article class="value-item"><h3>3. 誇張しない設計</h3><p>ソース解析で証明できることと、実機WCET・物理HIL・安全認証が必要なことを明確に分けています。</p></article>
      </div>
    </section>

    <section class="section">
      <article class="panel">
        <p class="eyebrow">再現方法</p>
        <h2 class="section-title">審査用デモはオフラインで再生成できます</h2>
        <pre class="commands"><code>saga-contest-demo --output build/contest-demo
saga check build/contest-demo/diff_safe_control.saga
saga-control-report build/contest-demo/diff_safe_control.saga</code></pre>
        <p class="source-note">生成時に安全例PASS、危険例FAIL、1行差分、診断コードの存在を検証し、条件が崩れた場合はコマンド自体が失敗します。</p>
      </article>
    </section>

    <section class="section">
      <article class="panel boundary">
        <p class="eyebrow">判定の境界</p>
        <strong>PASSは「Sagaが対応しているソースレベルの制御規則に違反が見つからなかった」という意味です。</strong>
        <p class="source-note">実機での最悪実行時間（WCET）、物理HIL、非常停止/STO/インターロック、飛行安全性、機能安全認証を証明するものではありません。それらは対象機器ごとの計測と検証が別途必要です。</p>
      </article>
    </section>

    <details class="section">
      <summary><strong>安全例の全ソースを表示</strong></summary>
      <pre class="commands"><code>{safe_source}</code></pre>
    </details>
  </main>

  <footer>
    <div class="wrap">Generated locally by <code>saga-contest-demo</code>. 外部通信は不要です。Control Reportはソースコードや入力内容をアップロードしません。</div>
  </footer>
</body>
</html>
"""


def run_demo(output_dir: str | Path) -> dict[str, Any]:
    output = Path(output_dir).expanduser()
    output.mkdir(parents=True, exist_ok=True)

    safe_source_path = output / "diff_safe_control.saga"
    unsafe_source_path = output / "diff_unsafe_control.saga"
    _write_text(safe_source_path, SAFE_SOURCE)
    _write_text(unsafe_source_path, UNSAFE_SOURCE)

    safe_report = analyze_control_file(safe_source_path)
    unsafe_report = analyze_control_file(unsafe_source_path)
    safe_runtime_output: list[str] = []
    run_file(str(safe_source_path), output=safe_runtime_output.append)

    _write_text(output / "safe-report.txt", render_control_report(safe_report) + "\n")
    _write_text(output / "unsafe-report.txt", render_control_report(unsafe_report) + "\n")
    _write_text(output / "safe-report.json", json.dumps(safe_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    _write_text(output / "unsafe-report.json", json.dumps(unsafe_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    _write_text(output / "safe-report.html", render_control_report_html(safe_report))
    _write_text(output / "unsafe-report.html", render_control_report_html(unsafe_report))
    _write_text(output / "index.html", _render_index(safe_report, unsafe_report, safe_runtime_output))

    unsafe_codes = [str(item.get("code") or "") for item in unsafe_report.get("issues", [])]
    exact_single_change = _single_change_is_exact()
    valid = (
        exact_single_change
        and safe_report.get("verdict") == "pass"
        and unsafe_report.get("verdict") == "fail"
        and any(code.startswith("SAGA-C") for code in unsafe_codes)
        and len(safe_runtime_output) == 1
    )

    manifest = {
        "schema": 2,
        "demo": "safe-vs-unsafe-control",
        "language": "Saga",
        "implementation_version": __version__,
        "valid": valid,
        "expected": {"safe": "pass", "unsafe": "fail"},
        "single_change": {
            "verified": exact_single_change,
            "kind": "added-line",
            "line": RISKY_LINE.strip(),
        },
        "source_sha256": {
            "safe": _sha256_text(SAFE_SOURCE),
            "unsafe": _sha256_text(UNSAFE_SOURCE),
        },
        "observed": {
            "safe": safe_report.get("verdict"),
            "unsafe": unsafe_report.get("verdict"),
            "safe_analysis_scope": safe_report.get("analysis_scope"),
            "unsafe_analysis_scope": unsafe_report.get("analysis_scope"),
            "unsafe_diagnostics": unsafe_codes,
            "safe_runtime_output": safe_runtime_output,
        },
        "judge_summary": {
            "category": "programming-middle-school-problem-solving",
            "one_sentence_ja": "機械制御の危ない1行を、実行前に場所・理由・修正案つきで説明するプログラミング言語",
            "target_users": [
                "ロボットや機械制御を学ぶ学生",
                "学校のロボット・ものづくり活動",
                "制御コードをレビューする開発者",
            ],
            "proof": [
                "safe/unsafe sources differ by exactly one added line",
                "both examples use Saga's normal file-analysis path",
                "unsafe result contains a stable SAGA-C diagnostic",
            ],
        },
        "artifacts": [
            "index.html",
            "diff_safe_control.saga",
            "diff_unsafe_control.saga",
            "safe-report.txt",
            "unsafe-report.txt",
            "safe-report.json",
            "unsafe-report.json",
            "safe-report.html",
            "unsafe-report.html",
        ],
        "boundary": (
            "This demo proves the exact one-line source-analysis contrast only. It does not provide target WCET, "
            "physical HIL, emergency-stop/interlock, airworthiness, or functional-safety certification evidence."
        ),
    }
    _write_text(output / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="saga-contest-demo",
        description="Generate the reproducible Saga safe-vs-unsafe machine-control contest demo.",
    )
    parser.add_argument("--output", default="build/contest-demo", help="artifact directory")
    parser.add_argument("--json", action="store_true", help="print the demo manifest as JSON")
    args = parser.parse_args(argv)

    manifest = run_demo(args.output)
    if args.json:
        print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Saga {__version__} contest demo")
        print(f"single change: {'VERIFIED' if manifest['single_change']['verified'] else 'INVALID'}")
        print(f"safe:   {str(manifest['observed']['safe']).upper()}")
        print(f"unsafe: {str(manifest['observed']['unsafe']).upper()}")
        if manifest["observed"]["unsafe_diagnostics"]:
            print("diagnostic: " + ", ".join(manifest["observed"]["unsafe_diagnostics"]))
        print(f"open: {Path(args.output).expanduser() / 'index.html'}")

    return 0 if manifest["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
