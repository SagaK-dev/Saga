from __future__ import annotations

import unittest

from saga.api import compile_source, run_source
from saga.stdlib.machine_control import (
    MachineControlError,
    Q31_MAX,
    Q31_MIN,
    q31_add_sat,
    q31_from_ratio,
    q31_mac_sat,
    q31_mul_sat,
    q31_sub_sat,
)


class Q31Control054Tests(unittest.TestCase):
    def test_q31_arithmetic_is_fixed_width_and_saturating(self):
        half = q31_from_ratio(1, 2)
        negative_half = q31_from_ratio(-1, 2)

        self.assertEqual(half, 1 << 30)
        self.assertEqual(negative_half, -(1 << 30))
        self.assertEqual(q31_mul_sat(half, half), 1 << 29)
        self.assertEqual(q31_add_sat(Q31_MAX, 1), Q31_MAX)
        self.assertEqual(q31_sub_sat(Q31_MIN, 1), Q31_MIN)
        self.assertEqual(q31_mul_sat(Q31_MIN, Q31_MIN), Q31_MAX)
        self.assertEqual(q31_mac_sat(1 << 29, half, half), 1 << 30)

    def test_q31_rejects_values_outside_q1_31_domain(self):
        with self.assertRaises(MachineControlError):
            q31_add_sat(Q31_MAX + 1, 0)
        with self.assertRaises(MachineControlError):
            q31_from_ratio(1, 0)

    def test_q31_primitives_are_allowed_in_60000_hz_control_tick(self):
        source = r"""
use machine

@control_tick(60000, 8)
fn current_tick(error: int, gain: int, accumulator: int) -> int {
    let proportional = machine.q31_mul_sat(error, gain)
    return machine.q31_add_sat(accumulator, proportional)
}
"""
        compile_source(source, "<q31-control>")

    def test_q31_machine_surface_executes_without_decimal_state(self):
        output: list[str] = []
        run_source(
            r"""
use machine
let half = machine.q31_from_ratio(1, 2)
print(half)
print(machine.q31_mul_sat(half, half))
print(machine.q31_add_sat(2147483647, 1))
print(machine.q31_sub_sat(-2147483648, 1))
print(machine.q31_mac_sat(536870912, half, half))
""",
            output=output.append,
        )
        self.assertEqual(
            output,
            ["1073741824", "536870912", "2147483647", "-2147483648", "1073741824"],
        )


if __name__ == "__main__":
    unittest.main()
