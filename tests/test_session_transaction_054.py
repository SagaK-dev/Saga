from __future__ import annotations

import unittest
from unittest import mock

from saga.api import SagaSession
from saga.errors import ParseLimitError, RuntimeLanguageError, RuntimeResourceError, TypeLimitError


class SagaSessionTransaction054Tests(unittest.TestCase):
    def test_failed_submission_restores_numeric_context_and_enum_registry(self):
        with SagaSession() as session:
            original_precision = session.interpreter.context.prec
            original_enum_names = set(session.interpreter.enums)

            with self.assertRaises(RuntimeLanguageError):
                session.execute(
                    "enum Temp { A }\n"
                    "precision(7)\n"
                    "assert(false)\n"
                )

            self.assertEqual(session.interpreter.context.prec, original_precision)
            self.assertEqual(set(session.interpreter.enums), original_enum_names)
            self.assertNotIn("Temp", session.interpreter.globals.values)

    def test_parse_recursion_is_reported_as_saga_limit_error(self):
        with SagaSession(filename="repl.saga") as session:
            with (
                mock.patch("saga.api.Parser.parse", side_effect=RecursionError("host stack")),
                self.assertRaises(ParseLimitError) as ctx,
            ):
                session.execute("print(1)\n")

        self.assertEqual(ctx.exception.filename, "repl.saga")
        self.assertEqual(ctx.exception.code, "SAGA-P002")

    def test_type_recursion_is_reported_as_saga_limit_error(self):
        with SagaSession(filename="repl.saga") as session:
            with (
                mock.patch("saga.api.TypeChecker.check", side_effect=RecursionError("host stack")),
                self.assertRaises(TypeLimitError) as ctx,
            ):
                session.execute("print(1)\n")

        self.assertEqual(ctx.exception.filename, "repl.saga")
        self.assertEqual(ctx.exception.code, "SAGA-T002")

    def test_checker_snapshot_recursion_is_reported_as_type_limit_error(self):
        with SagaSession(filename="repl.saga") as session:
            with (
                mock.patch("saga.api.copy.deepcopy", side_effect=RecursionError("host stack")),
                self.assertRaises(TypeLimitError) as ctx,
            ):
                session.execute("print(1)\n")

        self.assertEqual(ctx.exception.filename, "repl.saga")
        self.assertEqual(ctx.exception.code, "SAGA-T002")

    def test_runtime_recursion_is_reported_as_saga_resource_error(self):
        with SagaSession(filename="repl.saga") as session:
            with (
                mock.patch.object(
                    session.interpreter,
                    "interpret_incremental",
                    side_effect=RecursionError("host stack"),
                ),
                self.assertRaises(RuntimeResourceError) as ctx,
            ):
                session.execute("print(1)\n")

        self.assertEqual(ctx.exception.filename, "repl.saga")
        self.assertEqual(ctx.exception.code, "SAGA-R002")

    def test_successful_submission_commits_numeric_context_and_enum_registry(self):
        with SagaSession() as session:
            session.execute(
                "enum Stable { A }\n"
                "precision(9)\n"
            )

            self.assertEqual(session.interpreter.context.prec, 9)
            self.assertIn("Stable", session.interpreter.enums)
            self.assertIn("Stable", session.interpreter.globals.values)


if __name__ == "__main__":
    unittest.main()
