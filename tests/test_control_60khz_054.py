from __future__ import annotations

import json
import unittest

from saga.api import compile_source, run_source
from saga.control_report import analyze_control_source
from saga.errors import TypeCheckError
from saga.stdlib.fine_control import CyclicClock


CONTROL_60KHZ = r"""
use machine

@control_tick(60000, 12.5)
fn current_tick(previous: decimal, sample: decimal) -> decimal {
    let limited = machine.slew(previous, sample, 1000.0, 0.0000166666666666667)
    let filtered = machine.low_pass(previous, limited, 0.25)
    let centered = machine.deadband(filtered, 0.001)
    return machine.integrate_clamped(previous, centered, 0.0000166666666666667, -1.0, 1.0)
}
"""


class Control60kHz054Tests(unittest.TestCase):
    def test_60000_hz_contract_accepts_fractional_microsecond_budget(self):
        compile_source(CONTROL_60KHZ, "<60khz>")

        report = analyze_control_source(CONTROL_60KHZ, "<60khz>")
        self.assertEqual(report["verdict"], "pass")
        tick = next(item for item in report["control_functions"] if item["name"] == "current_tick")
        timing = tick["timing"]
        self.assertEqual(timing["rate_hz"], 60000)
        self.assertEqual(timing["budget_us"], 12.5)
        self.assertAlmostEqual(timing["period_us"], 16.666667, places=6)
        self.assertAlmostEqual(timing["headroom_us"], 4.166667, places=6)

    def test_60000_hz_contract_rejects_budget_over_period(self):
        source = """
@control_tick(60000, 16.7)
fn tick(value: decimal) -> decimal { return value }
"""
        with self.assertRaises(TypeCheckError) as ctx:
            compile_source(source, "<60khz-overbudget>")
        self.assertEqual(ctx.exception.diagnostic_id, "SAGA-C483")

    def test_frequency_clock_has_zero_theoretical_one_second_phase_drift(self):
        clock = CyclicClock(60000)
        try:
            self.assertEqual(clock._deadline_ns(60000) - clock._anchor_ns, 1_000_000_000)
            self.assertEqual(clock._deadline_ns(120000) - clock._anchor_ns, 2_000_000_000)
            stats = json.loads(clock.stats_json())
            self.assertEqual(stats["frequency_hz"], 60000)
            self.assertEqual(stats["phase_model"], "exact-rational-frequency")
            self.assertAlmostEqual(stats["period_us"], 16.6666666667, places=6)
        finally:
            clock.close()

    def test_high_rate_signal_primitives_execute(self):
        output: list[str] = []
        run_source(
            """
use machine
print(machine.slew(0.0, 1.0, 10.0, 0.02))
print(machine.low_pass(0.0, 1.0, 0.25))
print(machine.deadband(0.5, 0.1))
print(machine.integrate_clamped(0.9, 1.0, 0.2, -1.0, 1.0))
""",
            output=output.append,
        )
        self.assertEqual(output, ["0.2", "0.25", "0.4", "1"])


if __name__ == "__main__":
    unittest.main()
