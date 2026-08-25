# Saga 0.52 Generic Abstraction Foundations

Saga 0.52 builds on the 0.51 Generic ADT implementation in three connected areas.

## Option and Result are intrinsic Generic ADTs

`Option[T]` and `Result[T, E]` now participate in the same constructor and exhaustive-match model as user-defined generic enums:

```saga
let value = Option.Some(42)
let empty: Option[int] = Option.None
let outcome: Result[int, text] = Result.Ok(7)

match value {
    case Option.Some(item) { print(item) }
    case Option.None { print(0) }
}
```

The established `some`, `none`, `ok`, `err`, `is_some`, `is_ok`, unwrap helpers, and `?` propagation remain source-compatible. Both spellings use the same runtime wrappers, so existing APIs do not fork into two representations.

## Generic method and interface contracts

Method-local generic parameter names are alpha-equivalent across an interface contract and its implementation. An interface may call its parameter `U` while an implementation calls the corresponding parameter `V`; compatibility depends on structure and generic arity rather than spelling.

Saga continues to use `interface` as its trait-style contract surface instead of introducing a second overlapping `trait` keyword.

## Higher-kinded type foundation

A declared type variable may now appear in constructor position:

```saga
fn keep[F, A](value: F[A]) -> F[A] = value
```

Calling `keep([1, 2, 3])` infers `F` as the `list` type constructor and `A` as `int`. Calling it with `Option.Some(42)` infers the `Option`/`option` constructor and `int`.

Internally, Saga distinguishes a type-constructor binding from an ordinary type binding and reconstructs applied result types after substitution. The arity (kind) is inferred from each `F[...]` application and checked during unification.

### Deliberate boundary

0.52 is the higher-kinded *foundation*, not a claim of a finished kind calculus. Explicit kind annotation syntax such as `F[_]`, higher-rank kinds, type lambdas, and a dedicated trait/type-class declaration syntax are intentionally deferred. A constructor variable must use one consistent arity throughout a signature, and function types are not inferred as higher-kinded constructors until Saga has an explicit function-kind representation that preserves both parameter and result structure. The new representation and inference path are designed so those features can be added without replacing the 0.51 Generic ADT model.
