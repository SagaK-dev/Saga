from __future__ import annotations

import unittest

from saga.api import compile_source
from saga.control_report import analyze_control_source


class ControlMethod054Tests(unittest.TestCase):
    def assert_control_error(self, source: str, diagnostic: str) -> None:
        with self.assertRaises(Exception) as ctx:
            compile_source(source, "<control-method-test>")
        self.assertIn(diagnostic, str(ctx.exception))

    def test_checked_same_receiver_helper_is_allowed(self):
        compile_source('''
class Controller() {
    @control_safe
    fn clamp(value: int) -> int { return value }

    @control_tick(1000, 500)
    fn tick(value: int) -> int { return self.clamp(value) }
}
''')

    def test_unchecked_same_receiver_helper_is_rejected(self):
        self.assert_control_error('''
class Controller() {
    fn helper(value: int) -> int { return value }

    @control_tick(1000, 500)
    fn tick(value: int) -> int { return self.helper(value) }
}
''', "SAGA-C490")

    def test_checked_method_helper_is_locally_restricted(self):
        self.assert_control_error('''
class Controller() {
    @control_safe
    fn helper(value: int) -> int {
        while false { }
        return value
    }

    @control_tick(1000, 500)
    fn tick(value: int) -> int { return self.helper(value) }
}
''', "SAGA-C477")

    def test_method_control_graph_rejects_recursion(self):
        self.assert_control_error('''
class Controller() {
    @control_safe
    fn helper(value: int) -> int { return self.helper(value) }

    @control_tick(1000, 500)
    fn tick(value: int) -> int { return self.helper(value) }
}
''', "SAGA-C485")

    def test_control_method_can_call_checked_same_unit_function(self):
        compile_source('''
@control_safe
fn clamp(value: int) -> int { return value }

class Controller() {
    @control_tick(1000, 500)
    fn tick(value: int) -> int { return clamp(value) }
}
''')

    def test_standalone_control_safe_contract_is_enforced(self):
        self.assert_control_error('''
@control_safe
fn helper(value: int) -> int {
    while false { }
    return value
}
''', "SAGA-C477")

    def test_control_report_lists_class_control_surface(self):
        report = analyze_control_source('''
class Controller() {
    @control_safe
    fn clamp(value: int) -> int { return value }

    @control_tick(1000, 500)
    fn tick(value: int) -> int { return self.clamp(value) }
}
''', "controller.saga")
        self.assertEqual(report["verdict"], "pass")
        self.assertEqual(
            {item["name"] for item in report["control_functions"]},
            {"Controller.clamp", "Controller.tick"},
        )


if __name__ == "__main__":
    unittest.main()
