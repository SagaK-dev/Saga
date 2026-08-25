from __future__ import annotations

import unittest

from saga.api import parse_source
from saga.control_report import build_control_report, render_control_report, render_control_report_html


class ControlReport053Tests(unittest.TestCase):
    def test_safe_tick_explains_timing_contract(self):
        program = parse_source(
            '''
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
''',
            '<safe-control>',
        )

        report = build_control_report(program, '<safe-control>')

        self.assertEqual(report['verdict'], 'pass')
        self.assertEqual(len(report['control_functions']), 2)
        tick = next(item for item in report['control_functions'] if item['name'] == 'tick')
        self.assertEqual(tick['timing']['rate_hz'], 20000)
        self.assertEqual(tick['timing']['period_us'], 50.0)
        self.assertEqual(tick['timing']['budget_percent'], 70.0)
        self.assertIn('PASS', render_control_report(report))

        html = render_control_report_html(report)
        self.assertIn('CONTROL PROFILE PASS', html)
        self.assertIn('20,000 Hz', html)
        self.assertIn('70.0%', html)

    def test_report_keeps_actionable_control_violation(self):
        program = parse_source(
            '''
use machine

@control_tick(1000, 200)
fn tick(error: decimal) -> decimal {
    let now = machine.monotonic_ns()
    return error
}
''',
            '<unsafe-control>',
        )

        report = build_control_report(program, '<unsafe-control>')

        self.assertEqual(report['verdict'], 'fail')
        codes = {item['code'] for item in report['issues']}
        self.assertIn('SAGA-C492', codes)
        rendered = render_control_report(report)
        self.assertIn('FAIL', rendered)
        self.assertIn('SAGA-C492', rendered)
        self.assertIn('fix:', rendered)

        html = render_control_report_html(report)
        self.assertIn('REVIEW NEEDED', html)
        self.assertIn('SAGA-C492', html)

    def test_non_control_program_is_not_misrepresented_as_safe(self):
        program = parse_source('fn add(a: int, b: int) -> int { return a + b }', '<ordinary>')
        report = build_control_report(program, '<ordinary>')

        self.assertEqual(report['verdict'], 'not-applicable')
        self.assertEqual(report['control_functions'], [])
        self.assertIn('No @control_tick', render_control_report(report))
        self.assertIn('NO CONTROL SURFACE', render_control_report_html(report))


if __name__ == '__main__':
    unittest.main()
