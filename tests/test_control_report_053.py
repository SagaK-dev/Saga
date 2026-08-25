from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from saga.api import compile_source, parse_source
from saga.control_report import (
    analyze_control_file,
    analyze_control_source,
    build_control_report,
    render_control_report,
    render_control_report_html,
)


class ControlReport053Tests(unittest.TestCase):
    def test_safe_tick_explains_timing_contract(self):
        source = '''
@control_safe
fn clamp(value: decimal) -> decimal {
    if value > 1.0 { return 1.0 }
    if value < -1.0 { return -1.0 }
    return value
}

@control_tick(20000, 35)
fn tick(error: decimal) -> decimal {
    return clamp(error * 0.5)
}
'''
        report = analyze_control_source(source, '<safe-control>')

        self.assertEqual(report['verdict'], 'pass')
        self.assertEqual(report['language_check']['status'], 'pass')
        self.assertEqual(report['timing_contract']['status'], 'declared')
        self.assertEqual(len(report['control_functions']), 2)
        tick = next(item for item in report['control_functions'] if item['name'] == 'tick')
        self.assertEqual(tick['timing']['rate_hz'], 20000)
        self.assertEqual(tick['timing']['period_us'], 50.0)
        self.assertEqual(tick['timing']['budget_percent'], 70.0)
        timing_check = next(item for item in report['checks'] if item['id'] == 'timing-contract')
        self.assertEqual(timing_check['status'], 'pass')
        self.assertIn('PASS', render_control_report(report))

        html = render_control_report_html(report)
        self.assertIn('CONTROL PROFILE PASS', html)
        self.assertIn('LANGUAGE CHECK PASS', html)
        self.assertIn('20,000 Hz', html)
        self.assertIn('70.0%', html)

    def test_report_keeps_actionable_control_violation(self):
        source = '''
use machine

@control_tick(1000, 200)
fn tick(error: decimal) -> decimal {
    let now = machine.monotonic_ns()
    return error
}
'''
        report = analyze_control_source(source, '<unsafe-control>')

        self.assertEqual(report['verdict'], 'fail')
        self.assertEqual(report['language_check']['status'], 'fail')
        codes = {item['code'] for item in report['issues']}
        self.assertIn('SAGA-C492', codes)
        io_check = next(item for item in report['checks'] if item['id'] == 'no-hidden-io')
        self.assertEqual(io_check['status'], 'fail')
        rendered = render_control_report(report)
        self.assertIn('FAIL', rendered)
        self.assertIn('SAGA-C492', rendered)
        self.assertIn('fix:', rendered)

        html = render_control_report_html(report)
        self.assertIn('REVIEW NEEDED', html)
        self.assertIn('SAGA-C492', html)

    def test_bare_tick_keeps_legacy_compatibility_without_timing_claim(self):
        source = '''
@control_tick
fn tick(error: decimal) -> decimal {
    return error
}
'''
        report = analyze_control_source(source, '<legacy-control>')

        self.assertEqual(report['verdict'], 'pass')
        self.assertEqual(report['language_check']['status'], 'pass')
        self.assertEqual(report['timing_contract']['status'], 'not-declared')
        tick = next(item for item in report['control_functions'] if item['name'] == 'tick')
        self.assertNotIn('timing', tick)
        self.assertEqual(tick['timing_contract'], 'legacy-untimed')
        timing_check = next(item for item in report['checks'] if item['id'] == 'timing-contract')
        self.assertEqual(timing_check['status'], 'not-declared')
        text = render_control_report(report)
        self.assertIn('timing contract not declared', text)
        self.assertIn('0/1 tick(s)', text)
        html = render_control_report_html(report)
        self.assertIn('周期・実行予算は未宣言', html)
        compile_source(source, '<legacy-control>')

    def test_report_includes_local_tick_restrictions(self):
        source = '''
@control_tick
fn tick(error: decimal) -> decimal {
    var value = error
    while value < 1.0 {
        value = value + 0.1
    }
    return value
}
'''
        report = analyze_control_source(source, '<unbounded-control>')

        self.assertEqual(report['verdict'], 'fail')
        self.assertIn('SAGA-C477', {item['code'] for item in report['issues']})
        bounded = next(item for item in report['checks'] if item['id'] == 'bounded-work')
        self.assertEqual(bounded['status'], 'fail')

    def test_semantically_invalid_source_never_gets_control_pass(self):
        source = '''
@control_tick(1000, 200)
fn tick(error: decimal) -> decimal {
    return "not a decimal"
}
'''
        report = analyze_control_source(source, '<type-error>')

        self.assertEqual(report['verdict'], 'invalid')
        self.assertEqual(report['language_check']['status'], 'fail')
        self.assertIsNotNone(report['language_check']['diagnostic'])
        self.assertIn('INVALID', render_control_report(report))
        self.assertIn('INVALID SAGA SOURCE', render_control_report_html(report))

    def test_parse_error_is_reported_without_fake_control_conclusion(self):
        report = analyze_control_source('@control_tick(1000, 200)\nfn tick(', '<parse-error>')

        self.assertEqual(report['verdict'], 'invalid')
        self.assertEqual(report['language_check']['status'], 'fail')
        self.assertEqual(report['control_functions'], [])
        self.assertTrue(all(item['status'] == 'not-applicable' for item in report['checks']))

    def test_direct_ast_api_marks_language_check_as_not_run(self):
        program = parse_source(
            '@control_tick(1000, 200)\nfn tick(error: decimal) -> decimal { return error }',
            '<ast-only>',
        )
        report = build_control_report(program, '<ast-only>')

        self.assertEqual(report['verdict'], 'pass')
        self.assertEqual(report['language_check']['status'], 'not-run')
        self.assertIn('Language check: NOT-RUN', render_control_report(report))

    def test_project_report_follows_namespaced_source_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module = root / 'controller.saga'
            module.write_text(
                '''
module controller

@control_tick(1000, 200)
fn tick(error: decimal) -> decimal {
    return error * 0.5
}
'''.strip() + '\n',
                encoding='utf-8',
            )
            main = root / 'main.saga'
            main.write_text('use "controller.saga" as ctrl\nprint(1)\n', encoding='utf-8')

            report = analyze_control_file(main)

            self.assertEqual(report['verdict'], 'pass')
            self.assertEqual(report['analysis_scope'], 'loaded-program')
            self.assertEqual(len(report['source_units']), 2)
            tick = next(item for item in report['control_functions'] if item['name'] == 'ctrl.tick')
            self.assertEqual(Path(tick['file']).name, 'controller.saga')
            self.assertEqual(tick['timing']['rate_hz'], 1000)
            self.assertIn('Source units: 2', render_control_report(report))

    def test_project_load_failure_keeps_module_control_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module = root / 'controller.saga'
            module.write_text(
                '''
module controller

@control_tick(1000, 200)
fn tick(error: decimal) -> decimal {
    var value = error
    while value < 1.0 {
        value = value + 0.1
    }
    return value
}
'''.strip() + '\n',
                encoding='utf-8',
            )
            main = root / 'main.saga'
            main.write_text('use "controller.saga" as ctrl\nprint(1)\n', encoding='utf-8')

            report = analyze_control_file(main)

            self.assertEqual(report['verdict'], 'fail')
            self.assertEqual(report['analysis_scope'], 'entry-only-after-load-failure')
            self.assertEqual(report['language_check']['status'], 'fail')
            issue = next(item for item in report['issues'] if item['code'] == 'SAGA-C477')
            self.assertEqual(Path(issue['file']).name, 'controller.saga')
            bounded = next(item for item in report['checks'] if item['id'] == 'bounded-work')
            self.assertEqual(bounded['status'], 'fail')
            self.assertIn('controller.saga', render_control_report(report))

    def test_non_control_program_is_not_misrepresented_as_safe(self):
        report = analyze_control_source(
            'fn add(a: int, b: int) -> int { return a + b }',
            '<ordinary>',
        )

        self.assertEqual(report['verdict'], 'not-applicable')
        self.assertEqual(report['language_check']['status'], 'pass')
        self.assertEqual(report['control_functions'], [])
        self.assertIn('No @control_tick', render_control_report(report))
        self.assertIn('NO CONTROL SURFACE', render_control_report_html(report))


if __name__ == '__main__':
    unittest.main()
