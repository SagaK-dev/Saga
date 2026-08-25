from __future__ import annotations

import unittest

from saga.api import compile_source, run_source
from saga.errors import TypeCheckError
from saga.typesys import FUNCTION, INT, TEXT, OPTION, TYPECTOR, parse_type, substitute, unify


class GenericAbstractions052Tests(unittest.TestCase):
    def run_program(self, source: str) -> list[str]:
        output: list[str] = []
        run_source(source, output=output.append)
        return output

    def test_option_adt_constructor_and_exhaustive_match(self):
        source = """
        let value = Option.Some(42)
        match value {
            case Option.Some(item) { print(item) }
            case Option.None { print(0) }
        }
        """
        self.assertEqual(self.run_program(source), ["42"])

    def test_option_none_uses_context_and_legacy_some_shares_representation(self):
        source = """
        let empty: Option[int] = Option.None
        match empty {
            case Option.Some(item) { print(item) }
            case Option.None { print("empty") }
        }
        let legacy = some(5)
        match legacy {
            case Option.Some(item) { print(item) }
            case Option.None { print(0) }
        }
        print(is_some(Option.Some(9)))
        """
        self.assertEqual(self.run_program(source), ["empty", "5", "true"])

    def test_result_adt_constructor_and_legacy_helpers_share_representation(self):
        source = """
        let outcome: Result[int, text] = Result.Ok(7)
        match outcome {
            case Result.Ok(value) { print(value) }
            case Result.Err(message) { print(message) }
        }
        let legacy = err("boom")
        match legacy {
            case Result.Ok(value) { print(value) }
            case Result.Err(message) { print(message) }
        }
        let checked: Result[int, text] = Result.Ok(1)
        print(is_ok(checked))
        """
        self.assertEqual(self.run_program(source), ["7", "boom", "true"])

    def test_legacy_question_propagation_remains_compatible(self):
        source = """
        fn increment(value: option[int]) -> option[int] {
            let item = value?
            return some(item + 1)
        }
        let result = increment(Option.Some(4))
        match result {
            case Option.Some(value) { print(value) }
            case Option.None { print(0) }
        }
        """
        self.assertEqual(self.run_program(source), ["5"])

    def test_higher_kinded_type_application_unifies_constructor_and_argument(self):
        pattern = parse_type("F[A]", {"F", "A"})
        mapping = {}
        self.assertTrue(unify(pattern, OPTION(INT), mapping))
        self.assertEqual(mapping["F"], TYPECTOR("option"))
        self.assertEqual(mapping["A"], INT)
        result = substitute(parse_type("F[B]", {"F", "B"}), {**mapping, "B": TEXT})
        self.assertEqual(result, OPTION(TEXT))

    def test_language_level_hkt_inference_works_for_list_and_option(self):
        source = """
        fn keep[F, A](value: F[A]) -> F[A] = value
        let values = keep([1, 2, 3])
        print(len(values))
        let maybe = keep(Option.Some(9))
        match maybe {
            case Option.Some(value) { print(value) }
            case Option.None { print(0) }
        }
        """
        self.assertEqual(self.run_program(source), ["3", "9"])

    def test_generic_interface_method_is_alpha_equivalent(self):
        source = """
        interface Transformer[T] {
            fn transform[U](value: T, mapper: fn[T, U]) -> U
        }
        class Identity[T] implements Transformer[T] {
            override fn transform[V](value: T, mapper: fn[T, V]) -> V = mapper(value)
        }
        """
        compile_source(source)


if __name__ == "__main__":
    unittest.main()

# Review-hardening regressions are intentionally kept in the 0.52 suite.

def _install_review_hardening_tests() -> None:
    def test_hkt_signature_rejects_inconsistent_constructor_arity(self):
        source = """
        fn bad[F, A, B](value: F[A, B]) -> F[A] = value
        """
        with self.assertRaises(TypeCheckError):
            compile_source(source)

    def test_hkt_rejects_function_constructor_until_function_kinds_are_modeled(self):
        pattern = parse_type("F[A]", {"F", "A"})
        mapping = {}
        self.assertFalse(unify(pattern, FUNCTION([INT], INT), mapping))
        source = """
        fn keep[F, A](value: F[A]) -> F[A] = value
        fn inc(value: int) -> int = value + 1
        let kept = keep(inc)
        """
        with self.assertRaises(TypeCheckError):
            compile_source(source)

    GenericAbstractions052Tests.test_hkt_signature_rejects_inconsistent_constructor_arity = test_hkt_signature_rejects_inconsistent_constructor_arity
    GenericAbstractions052Tests.test_hkt_rejects_function_constructor_until_function_kinds_are_modeled = test_hkt_rejects_function_constructor_until_function_kinds_are_modeled


_install_review_hardening_tests()
