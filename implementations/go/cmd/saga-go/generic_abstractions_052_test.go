package main

import "testing"

func TestGenericAbstractions052OptionAndResultADTs(t *testing.T) {
	src := `let value = Option.Some(42)
match value {
case Option.Some(item) { print(item) }
case Option.None { print(0) }
}
let result: Result[int, text] = Result.Ok(7)
match result {
case Result.Ok(item) { print(item) }
case Result.Err(message) { print(message) }
}`
	out, err := runSagaForTest(t, src)
	if err != nil {
		t.Fatal(err)
	}
	if out != `42
7` {
		t.Fatalf("output=%q", out)
	}
}

func TestGenericAbstractions052LegacyWrappersMatchNewADTPatterns(t *testing.T) {
	src := `let value = some(5)
match value {
case Option.Some(item) { print(item) }
case Option.None { print(0) }
}
let result = err("boom")
match result {
case Result.Ok(item) { print(item) }
case Result.Err(message) { print(message) }
}`
	out, err := runSagaForTest(t, src)
	if err != nil {
		t.Fatal(err)
	}
	if out != `5
boom` {
		t.Fatalf("output=%q", out)
	}
}

func TestGenericAbstractions052HigherKindedInference(t *testing.T) {
	src := `fn keep[F, A](value: F[A]) -> F[A] = value
let values = keep([1, 2, 3])
print(len(values))
let maybe = keep(Option.Some(9))
match maybe {
case Option.Some(item) { print(item) }
case Option.None { print(0) }
}`
	out, err := runSagaForTest(t, src)
	if err != nil {
		t.Fatal(err)
	}
	if out != `3
9` {
		t.Fatalf("output=%q", out)
	}
}

func TestGenericAbstractions052GenericInterfaceMethodAlphaEquivalence(t *testing.T) {
	src := `interface Transformer[T] {
fn transform[U](value: T, mapper: fn[T, U]) -> U
}
class Identity[T] implements Transformer[T] {
override fn transform[V](value: T, mapper: fn[T, V]) -> V = mapper(value)
}
print(1)`
	out, err := runSagaForTest(t, src)
	if err != nil {
		t.Fatal(err)
	}
	if out != "1" {
		t.Fatalf("output=%q", out)
	}
}
