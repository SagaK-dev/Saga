package main

import (
	"strings"
	"testing"
)

func checkControlMethodSource054(src string) error {
	tokens, err := lex(src, "<control-method-test>")
	if err != nil {
		return err
	}
	stmts, err := parse(tokens)
	if err != nil {
		return err
	}
	return NewChecker().Check(stmts)
}

func requireControlDiagnostic054(t *testing.T, src, diagnostic string) {
	t.Helper()
	err := checkControlMethodSource054(src)
	if err == nil {
		t.Fatalf("expected %s, got success", diagnostic)
	}
	if !strings.Contains(err.Error(), diagnostic) {
		t.Fatalf("expected %s, got %v", diagnostic, err)
	}
}

func TestControlMethod054AllowsCheckedSameReceiverHelper(t *testing.T) {
	err := checkControlMethodSource054(`
class Controller() {
    @control_safe
    fn clamp(value: int) -> int { return value }

    @control_tick(1000, 500)
    fn tick(value: int) -> int { return self.clamp(value) }
}
`)
	if err != nil {
		t.Fatal(err)
	}
}

func TestControlMethod054RejectsUncheckedSameReceiverHelper(t *testing.T) {
	requireControlDiagnostic054(t, `
class Controller() {
    fn helper(value: int) -> int { return value }

    @control_tick(1000, 500)
    fn tick(value: int) -> int { return self.helper(value) }
}
`, "SAGA-C490")
}

func TestControlMethod054RestrictsCheckedMethodHelper(t *testing.T) {
	requireControlDiagnostic054(t, `
class Controller() {
    @control_safe
    fn helper(value: int) -> int {
        while false { }
        return value
    }

    @control_tick(1000, 500)
    fn tick(value: int) -> int { return self.helper(value) }
}
`, "SAGA-C477")
}

func TestControlMethod054RejectsMethodRecursion(t *testing.T) {
	requireControlDiagnostic054(t, `
class Controller() {
    @control_safe
    fn helper(value: int) -> int { return self.helper(value) }

    @control_tick(1000, 500)
    fn tick(value: int) -> int { return self.helper(value) }
}
`, "SAGA-C485")
}

func TestControlMethod054AllowsCheckedSameUnitFunction(t *testing.T) {
	err := checkControlMethodSource054(`
@control_safe
fn clamp(value: int) -> int { return value }

class Controller() {
    @control_tick(1000, 500)
    fn tick(value: int) -> int { return clamp(value) }
}
`)
	if err != nil {
		t.Fatal(err)
	}
}

func TestControlMethod054EnforcesStandaloneSafeContract(t *testing.T) {
	requireControlDiagnostic054(t, `
@control_safe
fn helper(value: int) -> int {
    while false { }
    return value
}
`, "SAGA-C477")
}
