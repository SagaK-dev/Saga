from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from saga import ResourceBudget, UNTRUSTED_RESOURCE_BUDGET, compile_file, compile_source, run_source
from saga.errors import LexLimitError, ParseLimitError, RuntimeResourceError
from saga.limits import NORMATIVE_RESOURCE_LIMITS, RESOURCE_MODEL, source_size_bytes


class ResourceBudgetTests(unittest.TestCase):
    def test_resource_budgets_are_deployment_policy_not_language_limits(self):
        self.assertEqual(NORMATIVE_RESOURCE_LIMITS, {})
        self.assertEqual(RESOURCE_MODEL, "no-fixed-normative-ceilings")
        self.assertIsNotNone(UNTRUSTED_RESOURCE_BUDGET.max_source_bytes)
        self.assertIsNotNone(UNTRUSTED_RESOURCE_BUDGET.max_steps)

    def test_invalid_budget_values_fail_at_configuration_time(self):
        with self.assertRaises(ValueError):
            ResourceBudget(max_source_bytes=0)
        with self.assertRaises(ValueError):
            ResourceBudget(max_import_depth=-1)
        with self.assertRaises(ValueError):
            ResourceBudget(max_steps=True)
        self.assertEqual(ResourceBudget(max_import_depth=0).max_import_depth, 0)

    def test_source_size_uses_utf8_bytes_without_ascii_assumptions(self):
        self.assertEqual(source_size_bytes("aあ😀"), 8)

    def test_source_byte_budget_is_opt_in(self):
        source = 'print("0123456789")'
        compile_source(source)
        with self.assertRaises(LexLimitError):
            compile_source(source, resource_budget=ResourceBudget(max_source_bytes=8))

    def test_file_source_byte_budget_checks_raw_file_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = Path(tmp) / "main.saga"
            entry.write_text('print("0123456789")\n', encoding="utf-8")
            with self.assertRaises(LexLimitError):
                compile_file(
                    str(entry),
                    resource_budget=ResourceBudget(max_source_bytes=8),
                )

    def test_token_budget_is_checked_before_parsing(self):
        with self.assertRaises(LexLimitError):
            compile_source("let value = 42", resource_budget=ResourceBudget(max_tokens=3))

    def test_ast_budget_rejects_large_program_only_when_requested(self):
        source = "let a = 1\nlet b = 2\nprint(a + b)"
        compile_source(source)
        with self.assertRaises(ParseLimitError):
            compile_source(source, resource_budget=ResourceBudget(max_ast_nodes=2))

    def test_import_depth_budget_counts_entry_as_depth_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.saga").write_text('use "a.saga"\nprint(value())\n', encoding="utf-8")
            (root / "a.saga").write_text('use "b.saga"\nfn value() -> int = nested()\n', encoding="utf-8")
            (root / "b.saga").write_text('fn nested() -> int = 42\n', encoding="utf-8")

            compile_file(str(root / "main.saga"))
            with self.assertRaises(ParseLimitError):
                compile_file(
                    str(root / "main.saga"),
                    resource_budget=ResourceBudget(max_import_depth=1),
                )

    def test_module_budget_caps_broad_import_graphs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.saga").write_text(
                'use "a.saga"\nuse "b.saga"\nprint(a() + b())\n',
                encoding="utf-8",
            )
            (root / "a.saga").write_text('fn a() -> int = 20\n', encoding="utf-8")
            (root / "b.saga").write_text('fn b() -> int = 22\n', encoding="utf-8")

            with self.assertRaises(ParseLimitError):
                compile_file(
                    str(root / "main.saga"),
                    resource_budget=ResourceBudget(max_modules=2),
                )

    def test_resource_step_budget_cannot_be_relaxed_by_explicit_step_limit(self):
        source = "var n = 0\nwhile true { n = n + 1 }"
        with self.assertRaises(RuntimeResourceError):
            run_source(
                source,
                step_limit=10_000,
                resource_budget=ResourceBudget(max_steps=8),
            )


if __name__ == "__main__":
    unittest.main()
