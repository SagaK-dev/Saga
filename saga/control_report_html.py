from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any


def _short_file(value: Any) -> str:
    text = str(value or "")
    if text.startswith("<"):
        return text
    return Path(text).name


def _function_card(function: dict[str, Any]) -> str:
    name = escape(str(function["name"]))
    line = int(function["line"])
    source = escape(_short_file(function.get("file")))
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
    elif function["role"] == "tick":
        timing_html = (
            '<p class="timing-note"><strong>周期・実行予算は未宣言</strong><br>'
            '互換形式の @control_tick です。制御プロファイル検査は行いますが、周波数や実行予算の主張はしません。</p>'
        )

    return f"""
    <article class="card">
      <div class="card-title"><code>{name}</code><span>{role} · {source}:{line}</span></div>
      {timing_html}
    </article>
    """


def _check_list(report: dict[str, Any]) -> str:
    symbols = {
        "pass": ("✓", "checked", "違反なし"),
        "fail": ("×", "failed", "要修正"),
        "partial": ("!", "noted", "一部のみ宣言"),
        "not-declared": ("!", "noted", "未宣言"),
        "not-applicable": ("–", "covered", "対象外"),
    }
    rows: list[str] = []
    for item in report["checks"]:
        symbol, marker_class, label = symbols.get(item["status"], ("•", "covered", item["status"]))
        rows.append(
            f'<li><span class="{marker_class}" aria-hidden="true">{symbol}</span>'
            f'<div>{escape(str(item["label"]))}<small>{escape(label)}</small></div></li>'
        )
    return "".join(rows)


def _issues(report: dict[str, Any]) -> str:
    items = list(report["issues"])
    language = report.get("language_check", {})
    diagnostic = language.get("diagnostic")
    if language.get("status") == "fail" and diagnostic:
        duplicate = any(
            item["code"] == diagnostic["code"]
            and item.get("file") == diagnostic.get("file")
            and item["line"] == diagnostic["line"]
            and item["column"] == diagnostic["column"]
            for item in items
        )
        if not duplicate:
            items.append({**diagnostic, "language_issue": True})

    if not items:
        return '<p class="quiet">検出された制御プロファイル違反はありません。</p>'

    rendered: list[str] = []
    for item in items:
        prefix = "Saga言語検査" if item.get("language_issue") else "制御プロファイル"
        hint = escape(str(item.get("hint") or "確認してください"))
        location = f"{escape(_short_file(item.get('file')))}:{int(item['line'])}:{int(item['column'])}"
        rendered.append(
            f"""
            <article class="issue">
              <div><code>{escape(str(item['code']))}</code><span>{prefix} · {location}</span></div>
              <p>{escape(str(item['message']))}</p>
              <small>修正案: {hint}</small>
            </article>
            """
        )
    return "".join(rendered)


def _language_badge(report: dict[str, Any]) -> str:
    status = report.get("language_check", {}).get("status", "not-run")
    text = {
        "pass": "LANGUAGE CHECK PASS",
        "fail": "LANGUAGE CHECK FAIL",
        "not-run": "LANGUAGE CHECK NOT RUN",
    }.get(status, f"LANGUAGE CHECK {str(status).upper()}")
    css = {"pass": "pass", "fail": "fail", "not-run": "na"}.get(status, "na")
    return f'<span class="status {css}">{text}</span>'


def _analysis_notice(report: dict[str, Any]) -> str:
    scope = report.get("analysis_scope", "program")
    if scope != "entry-only-after-load-failure":
        return ""
    return (
        '<div class="notice danger"><strong>Project analysis is incomplete.</strong> '
        '依存ソースの読み込みまたは言語検査に失敗したため、表示中の制御サーフェスはエントリファイルで確認できた範囲だけです。'
        '診断は保持しますが、このレポートをプロジェクト全体のPASS根拠にはできません。</div>'
    )


def render_control_report_html(report: dict[str, Any]) -> str:
    verdict = report["verdict"]
    status_text = {
        "pass": "CONTROL PROFILE PASS",
        "fail": "REVIEW NEEDED",
        "invalid": "INVALID SAGA SOURCE",
        "not-applicable": "NO CONTROL SURFACE",
    }[verdict]
    status_class = {
        "pass": "pass",
        "fail": "fail",
        "invalid": "fail",
        "not-applicable": "na",
    }[verdict]

    cards = "".join(_function_card(item) for item in report["control_functions"])
    if not cards:
        cards = '<article class="card"><p>@control_tick / @control_safe は見つかりませんでした。</p></article>'

    timing = report.get("timing_contract", {})
    timing_notice = ""
    timing_status = timing.get("status")
    if timing_status in {"not-declared", "partial"}:
        timing_notice = (
            '<div class="notice"><strong>Timing contract is incomplete.</strong> '
            f"周期・実行予算を宣言しているtickは {int(timing.get('declared_ticks', 0))}/"
            f"{int(timing.get('total_ticks', 0))} です。PASSは周波数・WCETの保証を意味しません。</div>"
        )
    elif timing_status in {"invalid", "partial-invalid"}:
        timing_notice = (
            '<div class="notice danger"><strong>Timing contract is invalid.</strong> '
            f"不正な周期契約を持つtickが {int(timing.get('invalid_ticks', 0))} 件あります。"
            '表示された数値を実行周期の根拠として使用しないでください。</div>'
        )

    source_units = report.get("source_units", [])
    source_summary = f" · {len(source_units)} source unit(s)" if len(source_units) > 1 else ""

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
.status {{ display: inline-flex; padding: 8px 12px; border-radius: 999px; font-weight: 800; font-size: 12px; letter-spacing: .04em; }}
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
.card-title span {{ color: #64748b; font-size: 12px; text-align: right; }}
.timing {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 22px; }}
.timing div {{ display: flex; flex-direction: column; gap: 3px; }}
.timing strong {{ font-size: 22px; }}
.timing span, .meter-label, .quiet {{ color: #64748b; font-size: 12px; }}
.timing-note {{ margin: 18px 0 0; padding: 12px 14px; border-radius: 12px; background: #fff7ed; color: #9a3412; font-size: 12px; line-height: 1.55; }}
.meter {{ height: 10px; margin-top: 18px; background: #e5e7eb; border-radius: 999px; overflow: hidden; }}
.meter span {{ display: block; height: 100%; background: #2563eb; border-radius: inherit; }}
.meter-label {{ margin: 7px 0 0; }}
.checks {{ list-style: none; margin: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px 18px; }}
.checks li {{ display: flex; gap: 10px; align-items: start; }}
.checks li > span {{ display: inline-grid; place-items: center; width: 22px; height: 22px; flex: 0 0 22px; border-radius: 50%; font-weight: 900; }}
.checks li div {{ display: flex; flex-direction: column; gap: 2px; }}
.checks li small {{ color: #64748b; font-size: 11px; }}
.checks .checked {{ background: #d1fae5; color: #065f46; }}
.checks .failed {{ background: #fee2e2; color: #991b1b; }}
.checks .noted {{ background: #ffedd5; color: #9a3412; }}
.checks .covered {{ background: #e5e7eb; color: #475569; }}
.issue {{ margin-bottom: 10px; border-left: 5px solid #dc2626; }}
.issue div {{ display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }}
.issue code {{ font-weight: 800; color: #991b1b; }}
.issue div span, .issue small {{ color: #64748b; }}
.issue p {{ margin: 10px 0 8px; }}
.notice {{ margin-top: 16px; padding: 14px 16px; border-radius: 14px; background: #fff7ed; color: #9a3412; font-size: 13px; line-height: 1.55; }}
.notice.danger {{ background: #fef2f2; color: #991b1b; }}
.boundary {{ padding: 16px 18px; border-radius: 14px; background: #eef2ff; color: #3730a3; font-size: 13px; line-height: 1.55; }}
footer {{ margin-top: 24px; color: #64748b; font-size: 12px; }}
@media (max-width: 560px) {{ .hero {{ padding: 24px; }} .timing {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<main>
  <header class="hero">
    <p class="eyebrow">Saga {escape(str(report['implementation_version']))} · explainable machine control{source_summary}</p>
    <h1>Control<br>Report</h1>
    <div class="hero-row">
      <span class="status {status_class}">{status_text}</span>
      {_language_badge(report)}
      <span class="file">{escape(str(report['file']))}</span>
    </div>
  </header>

  {_analysis_notice(report)}
  {timing_notice}

  <section>
    <h2>制御サーフェス</h2>
    <div class="grid">{cards}</div>
  </section>

  <section>
    <h2>静的に確認した項目</h2>
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
