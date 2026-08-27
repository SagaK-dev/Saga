from __future__ import annotations

import unittest

from saga.api import SagaSession
from saga.errors import RuntimeLanguageError


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
