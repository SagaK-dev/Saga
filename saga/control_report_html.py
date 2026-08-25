from __future__ import annotations

from html import escape
from typing import Any


def _function_card(function: dict[str, Any]) -> str:
    name = escape(str(function["name"]))
    line = int(function["line"])
    role = "周期制御" if function["role"] == "tick" else "検査済みヘルパー"
    timing = function.get("timing")
    timing_html = ""

    if timing:
        percent = max(0.0, min(float(timing["budget_percent"]), 100.0))
        timing_html = f"""
        <div class="timing">
          <div><strong>{timing['rate_hz']:,} Hz</strong><span>周期 {timing['period_us']} µs</span></div>
          <div><strong>{timing['budget_us']} µs</strong><span>実行予算 / 余白 {timing['headroom_us']} µs</span></div>
        </div>
        <div class="meter" aria-label="周期に対する実行予算 {timing['budget_percent']}%">
          <span style="width:{percent:.1f}%"></span>
        </div>
        <p class="meter-label">1周期の {timing['budget_percent']}% を実行予算として宣言</p>
        """

    return f"""
    <article class="card">
      <div class="card-title"><code>{name}</code><span>{role} · line {line}</span></div>
      {timing_html}
    </article>
    """


def _check_list(report: dict[str, Any]) -> str:
    passed = report["verdict"] == "pass"
    symbol = "✓" if passed else "•"
    marker_class = "checked" if passed else "covered"
    return "".join(
        f'<li><span class="{marker_class}" aria-hidden="true">{symbol}</span>{escape(str(item["label"]))}</li>'
        for item in report["checks"]
    )


def _issues(report: dict[str, Any]) -> str:
    if not report["issues"]:
        return '<p class="quiet">検出された制御プロファイル違反はありません。</p>'

    return "".join(
        f"""
        <article class="issue">
          <div><code>{escape(str(item['code']))}</code><span>line {int(item['line'])}:{int(item['column'])}</span></div>
          <p>{escape(str(item['message']))}</p>
          <small>修正案: {escape(str(item['hint']))}</small>
        </article>
        """
        for item in report["issues"]
    )


def render_control_report_html(report: dict[str, Any]) -> str:
    verdict = report["verdict"]
    status_text = {
        "pass": "CONTROL PROFILE PASS",
        "fail": "REVIEW NEEDED",
        "not-applicable": "NO CONTROL SURFACE",
    }[verdict]
    status_class = {"pass": "pass", "fail": "fail", "not-applicable": "na"}[verdict]

    cards = "".join(_function_card(item) for item in report["control_functions"])
    if not cards:
        cards = '<article class="card"><p>@control_tick / @control_safe は見つかりませんでした。</p></article>'

    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Saga Control Report</title>
<style>
:root {{ color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #f4f6f8; color: #18212f; }}
main {{ width: min(1040px, calc(100% - 32px)); margin: 32px auto 56px; }}
.hero {{ padding: 32px; border-radius: 24px; background: #111827; color: white; box-shadow: 0 18px 45px rgba(15, 23, 42, .12); }}
.eyebrow {{ margin: 0 0 8px; font-size: 13px; letter-spacing: .14em; text-transform: uppercase; opacity: .72; }}
h1 {{ margin: 0; font-size: clamp(34px, 7vw, 68px); line-height: .98; letter-spacing: -.045em; }}
.hero-row {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-top: 24px; }}
.status {{ display: inline-flex; padding: 8px 12px; border-radius: 999px; font-weight: 800; font-size: 13px; letter-spacing: .04em; }}
.status.pass {{ background: #d1fae5; color: #065f46; }}
.status.fail {{ background: #fee2e2; color: #991b1b; }}
.status.na {{ background: #e5e7eb; color: #374151; }}
.file {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 13px; opacity: .78; overflow-wrap: anywhere; }}
section {{ margin-top: 28px; }}
section > h2 {{ margin: 0 0 12px; font-size: 18px; letter-spacing: -.02em; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }}
.card, .issue, .checks {{ background: white; border: 1px solid #e5e7eb; border-radius: 18px; padding: 20px; box-shadow: 0 8px 24px rgba(15, 23, 42, .045); }}
.card-title {{ display: flex; justify-content: space-between; gap: 12px; align-items: baseline; }}
.card-title code {{ font-size: 18px; font-weight: 800; }}
.card-title span {{ color: #64748b; font-size: 12px; }}
.timing {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 22px; }}
.timing div {{ display: flex; flex-direction: column; gap: 3px; }}
.timing strong {{ font-size: 22px; }}
.timing span, .meter-label, .quiet {{ color: #64748b; font-size: 12px; }}
.meter {{ height: 10px; margin-top: 18px; background: #e5e7eb; border-radius: 999px; overflow: hidden; }}
.meter span {{ display: block; height: 100%; background: #2563eb; border-radius: inherit; }}
.meter-label {{ margin: 7px 0 0; }}
.checks {{ list-style: none; margin: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px 18px; }}
.checks li {{ display: flex; gap: 10px; align-items: start; }}
.checks li span {{ display: inline-grid; place-items: center; width: 22px; height: 22px; flex: 0 0 22px; border-radius: 50%; font-weight: 900; }}
.checks .checked {{ background: #d1fae5; color: #065f46; }}
.checks .covered {{ background: #e5e7eb; color: #475569; }}
.issue {{ margin-bottom: 10px; border-left: 5px solid #dc2626; }}
.issue div {{ display: flex; gap: 10px; align-items: baseline; }}
.issue code {{ font-weight: 800; color: #991b1b; }}
.issue div span, .issue small {{ color: #64748b; }}
.issue p {{ margin: 10px 0 8px; }}
.boundary {{ padding: 16px 18px; border-radius: 14px; background: #eef2ff; color: #3730a3; font-size: 13px; line-height: 1.55; }}
footer {{ margin-top: 24px; color: #64748b; font-size: 12px; }}
@media (max-width: 560px) {{ .hero {{ padding: 24px; }} .timing {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<main>
  <header class="hero">
    <p class="eyebrow">Saga {escape(str(report['implementation_version']))} · explainable machine control</p>
    <h1>Control<br>Report</h1>
    <div class="hero-row">
      <span class="status {status_class}">{status_text}</span>
      <span class="file">{escape(str(report['file']))}</span>
    </div>
  </header>

  <section>
    <h2>制御サーフェス</h2>
    <div class="grid">{cards}</div>
  </section>

  <section>
    <h2>静的に確認すること</h2>
    <ul class="checks">{_check_list(report)}</ul>
  </section>

  <section>
    <h2>検出結果</h2>
    {_issues(report)}
  </section>

  <section>
    <h2>判定の境界</h2>
    <div class="boundary">{escape(str(report['boundary']))}</div>
  </section>

  <footer>Generated locally by Saga. Source text, keystrokes, credentials and telemetry are not uploaded by this report command.</footer>
</main>
</body>
</html>
"""
