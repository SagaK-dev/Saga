//go:build !sagaruntime

package main

import (
	"encoding/json"
	"strings"
)

type goSelfCase struct{ ID, Source, Output, ErrorID string }
type goSelfRecord struct {
	ID             string `json:"id"`
	Pass           bool   `json:"pass"`
	ExpectedOutput string `json:"expected_output,omitempty"`
	ActualOutput   string `json:"actual_output,omitempty"`
	ExpectedError  string `json:"expected_error,omitempty"`
	ActualError    string `json:"actual_error,omitempty"`
}

var goSelfCases = []goSelfCase{
	{"GSC001-exact", "print(0.1 + 0.2 == 0.3)\nprint(1 / 3 + 1 / 6)", "true\n1/2", ""},
	{"GSC002-control", "var s: int=0\nfor n in 1..5 { if n==3 { continue } s=s+n }\nprint(s)", "12", ""},
	{"GSC003-generic", "fn first[T](xs: list[T]) -> T { return xs[0] }\nprint(first([7,8]))", "7", ""},
	{"GSC004-option", "let x: option[int]=none()\nprint(unwrap_or(x,7))", "7", ""},
	{"GSC005-oop", "interface N { fn n() -> text }\nclass A(let v: text) implements N { override fn n() -> text=self.v }\nlet x: N=A(\"ok\")\nprint(x.n())", "ok", ""},
	{"GSC006-exception", "try { throw \"boom\" } catch e { print(e.message) }", "boom", ""},
	{"GSC007-task", "use task\nfn sq(x:int)->int=x*x\nprint(task.await(task.spawn(sq,9)))", "81", ""},
	{"GSC008-immutable", "let x=1\nx=2", "", "SAGA-T101"},
	{"GSC009-private", "class A(private let x:int) {}\nlet a=A(1)\nprint(a.x)", "", "SAGA-T107"},
	{"GSC010-invariance", "class B[T](let v:T) {}\nlet a:B[int]=B(1)\nlet b:B[text]=a", "", "SAGA-T103"},
	{"GSC011-closure", "fn make(start:int)->fn[int]{ var n=start fn next()->int { n=n+1 return n } return next }\nlet c=make(5)\nprint(c())\nprint(c())", "6\n7", ""},
	{"GSC012-generic-interface", "interface R[T]{ fn save(v:T)->T }\nclass M[T](let seed:T) implements R[T]{ override fn save(v:T)->T=v }\nlet r:R[int]=M(0)\nprint(r.save(7))", "7", ""},
	{"GSC013-generic-base", "class Box[T](let value:T) {}\nclass IntBox() extends Box[int] {}\nlet b=IntBox(9)\nprint(b.value)", "9", ""},
	{"GSC014-result", "let r:result[int,text]=ok(7)\nprint(is_ok(r))\nprint(unwrap_ok(r))", "true\n7", ""},
	{"GSC015-enum-match", "enum C{A,B}\nmatch C.B { case C.A { print(1) } case C.B { print(2) } }", "2", ""},
	{"GSC016-record", "record P(x:int,y:int)\nprint(P(1,2)==P(1,2))", "true", ""},
	{"GSC017-interpolation", "let n=4\nprint($\"n=${n}, next=${n+1}\")", "n=4, next=5", ""},
	{"GSC018-natural-binding", "name=\"Saga\"\nprint(name)", "Saga", ""},
	{"GSC019-natural-map", "values=[1,2,3]\nprint(values.map { it*2 })", "[2, 4, 6]", ""},
	{"GSC020-natural-fold", "values=[1,2,3]\nprint(values.fold(0) { total,n -> total+n })", "6", ""},
	{"GSC021-natural-pipeline", "values=[1,2,3]\nprint(values |> filter { it>1 } |> transform { it*2 })", "[4, 6]", ""},
	{"GSC022-first-class-closure", "greet={ print(\"Hello\") }\ngreet()", "Hello", ""},
	{"GSC023-closure-return", "values=[1,2,3]\nprint(values.map { if it>1 { return it*10 } return it })", "[1, 20, 30]", ""},
	{"GSC024-natural-pipeline-names", "values=[3,1,2,2]\nprint(values |> map { it*2 } |> distinct |> sorted |> take(2))", "[2, 4]", ""},
	{"GSC025-natural-duplicate-params", "let f:fn[int,int,int]={ x,x -> x }", "", "SAGA-P001"},
	{"GSC026-natural-each", "values=[1,2,3]\nvar total=0\nvalues.each { total=total+it }\nprint(total)", "6", ""},
	{"GSC027-natural-find", "values=[1,2,3]\nprint(unwrap_or(values.find { it>1 },0))", "2", ""},
	{"GSC028-natural-none", "values=[1,2,3]\nprint(values.none { it>3 })", "true", ""},
	{"GSC029-natural-sorted-by", "values=[3,1,2]\nprint(values.sortedBy { -it })", "[3, 2, 1]", ""},
	{"GSC030-natural-skip", "values=[1,2,3]\nprint(values.skip(1))", "[2, 3]", ""},
	{"GSC031-natural-zip", "print([1,2].zip([3,4]))", "[[1, 3], [2, 4]]", ""},
	{"GSC032-natural-flatten", "print([[1,2],[3]].flatten())", "[1, 2, 3]", ""},
	{"GSC033-natural-flat-map", "print([1,2].flatMap { [it,it] })", "[1, 1, 2, 2]", ""},
	{"GSC034-natural-chunk", "print([1,2,3].chunk(2))", "[[1, 2], [3]]", ""},
	{"GSC035-natural-window", "print([1,2,3].window(2))", "[[1, 2], [2, 3]]", ""},
	{"GSC036-natural-text", "print(\" Saga \".trim().upper())\nprint(\"a,b\".split(\",\"))", "SAGA\n[a, b]", ""},
	{"GSC037-natural-map-value", "let m=map_of(\"a\",1)\nprint(m.containsKey(\"a\"))\nprint(unwrap_or(m.get(\"b\"),9))", "true\n9", ""},
	{"GSC038-natural-set", "let s=set_of(1,2)\nprint(s.contains(2))\nprint(s.toList())", "true\n[1, 2]", ""},
	{"GSC039-natural-group", "print([1,1,2].group())", "{1: [1, 1], 2: [2]}", ""},
	{"GSC040-natural-group-by", "print([1,2,3].groupBy { it % 2 })", "{1: [1, 3], 0: [2]}", ""},
	{"GSC041-natural-bare-call", "print \"Hello\"\nfn add(a:int,b:int)->int{return a+b}\nprint add(2,3)", "Hello\n5", ""},
	{"GSC042-natural-bare-call-block", "fn panel(title:text,body:fn[unit]){print(title) body()}\npanel \"Todo\" { print(\"inside\") }", "Todo\ninside", ""},
	{"GSC043-bare-call-subtraction-guard", "let n=3\nprint(n - 1)", "2", ""},
	{"GSC044-remainder-zero-diagnostic", "print(1 % 0)", "", "SAGA-R102"},
	{"GSC045-unless", "let ready=false\nunless ready { print \"not ready\" }", "not ready", ""},
	{"GSC046-enum-exhaustive-diagnostic", "enum State{Ready,Running,Done}\nmatch State.Ready { case State.Ready { print(1) } case State.Done { print(3) } }", "", "SAGA-T112"},
	{"GSC047-tagged-union-payload", "enum Outcome{Ok(int),Err(text)}\nlet value:Outcome=Outcome.Ok(42)\nmatch value { case Outcome.Ok(number) { print(number) } case Outcome.Err(message) { print(message) } }", "42", ""},
	{"GSC048-tagged-union-equality", "enum Pair{Value(int,text),Empty}\nprint(Pair.Value(7,\"x\")==Pair.Value(7,\"x\"))\nprint(Pair.Value(7,\"x\")==Pair.Value(8,\"x\"))", "true\nfalse", ""},
}

func runGoSelfConformance() map[string]any {
	records := []goSelfRecord{}
	passed := 0
	for _, tc := range goSelfCases {
		rec := goSelfRecord{ID: tc.ID, ExpectedOutput: tc.Output, ExpectedError: tc.ErrorID}
		toks, err := lex(tc.Source, "<"+tc.ID+">")
		var stmts []Stmt
		if err == nil {
			stmts, err = parse(toks)
		}
		var c *Checker
		if err == nil {
			c = NewChecker()
			err = c.Check(stmts)
		}
		out := []string{}
		// Conformance cases may expect parse, type, or runtime diagnostics.
		// If parsing/checking succeeds we must still execute the program; skipping
		// execution merely because an error is expected makes runtime-error cases
		// impossible to validate and can create false negatives in the harness.
		if err == nil {
			it := NewInterpreter(c, func(s string) { out = append(out, s) })
			err = it.Interpret(stmts)
		}
		actualID := ""
		if se, ok := err.(*SagaError); ok {
			actualID = se.ID
		} else if err != nil {
			actualID = "INTERNAL"
		}
		actualOut := strings.Join(out, "\n")
		rec.ActualOutput = actualOut
		rec.ActualError = actualID
		if tc.ErrorID != "" {
			rec.Pass = actualID == tc.ErrorID
		} else {
			rec.Pass = err == nil && actualOut == tc.Output
		}
		if rec.Pass {
			passed++
		}
		records = append(records, rec)
	}
	return map[string]any{"schema": 1, "implementation": "saga-native", "implementation_version": sagaGoVersion, "language": "Saga", "language_version": "1.0", "language_spec_target": "1.0", "language_edition": "Native Runtime ABI 0.35 Preview", "natural_core_version": "0.29", "module_core_version": "0.30", "native_object_core_version": "0.31", "native_codegen_abi_version": "0.32", "native_value_abi_version": "0.33", "native_aggregate_abi_version": "0.35", "gc_preview_version": "0.35", "profile": "Standard Core", "total": len(records), "passed": passed, "pass": passed == len(records), "cases": records}
}
func encodeGoSelfConformance() string { b, _ := json.Marshal(runGoSelfConformance()); return string(b) }

var edition2027SelfCases = []goSelfCase{
	{"E27-001-float", "print(1.5f32 + float32(0.5))\nprint(2.25f64 + float64(0.75))", "2\n3", ""},
	{"E27-002-fixed", "let x:int32=int32(2147483647)\nprint(x+int32(1))", "2147483648", ""},
	{"E27-003-exact-float-boundary", "let x=1+1.0f64", "", "SAGA-T170"},
	{"E27-004-constraints", "fn bigger[T](a:T,b:T)->T where T:Comparable { if a>b{return a}; return b }\nprint(bigger(4,9))", "9", ""},
	{"E27-005-associated", "interface S{type Item; fn get()->Item}\nclass I(let v:int) implements S{type Item=int; override fn get()->int=self.v}\nfn first[T](x:T)->T.Item where T:S=x.get()\nprint(first(I(7)))", "7", ""},
	{"E27-006-propagate", "fn b(okay:bool)->result[int,text]{if okay{return ok(4)};return err(\"x\")}\nfn p(okay:bool)->result[int,text]{let x=b(okay)?;return ok(x+1)}\nprint(unwrap_ok(p(true)))\nprint(is_err(p(false)))", "5\ntrue", ""},
	{"E27-007-resource", "resource class H(let x:int){fn close()->unit{print(\"closed\")}}\nlet h=H(3)\nusing owned=move h{print(owned.x)}", "3\nclosed", ""},
	{"E27-008-move-static", "resource class H(let x:int){}\nlet h=H(1)\nlet y=move h\nprint(h.x)", "", "SAGA-T180"},
	{"E27-009-async", "async fn add(a:int,b:int)->int=a+b\nprint(await add(20,22))", "42", ""},
	{"E27-010-derive", "@derive(\"Equal\",\"Hash\") class K(let x:int){}\nlet a=K(1)\nlet b=K(1)\nprint(a==b)\nprint(map_get(map_of(a,\"ok\"),b,\"bad\"))", "true\nok", ""},
	{"E27-011-comptime", "comptime fn sq(x:int)->int=x*x\nprint(sq(9))", "81", ""},
	{"E27-012-unsafe-ffi", "use ffi\nprint(ffi.call_i64(\"x\",\"y\",[]))", "", "SAGA-T178"},
	{"E27-013-actor-state", "use task\nfn make()->fn[int,int]{var n=0; fn h(x:int)->int{n=n+x;return n};return h}\nlet a=task.actor(make())\nprint(await task.ask(a,2))\nprint(await task.ask(a,3))\ntask.stop(a)", "2\n5", ""},
	{"E27-014-compute-ir", "use game\nlet ir=\"SIR1\\nstage compute\\nscale 2\\nadd -1\\n\"\nprint(unwrap_ok(game.shader_ir_compute_reference(ir,[float64(2),float64(3)])))", "[3, 5]", ""},
}

func runEdition2027Conformance() map[string]any {
	records := []goSelfRecord{}
	passed := 0
	for _, tc := range edition2027SelfCases {
		rec := goSelfRecord{ID: tc.ID, ExpectedOutput: tc.Output, ExpectedError: tc.ErrorID}
		toks, err := lex(tc.Source, "<"+tc.ID+">")
		var stmts []Stmt
		if err == nil {
			stmts, err = parse(toks)
		}
		var c *Checker
		if err == nil {
			c = NewChecker()
			err = c.Check(stmts)
		}
		out := []string{}
		// Conformance cases may expect parse, type, or runtime diagnostics.
		// If parsing/checking succeeds we must still execute the program; skipping
		// execution merely because an error is expected makes runtime-error cases
		// impossible to validate and can create false negatives in the harness.
		if err == nil {
			it := NewInterpreter(c, func(s string) { out = append(out, s) })
			err = it.Interpret(stmts)
		}
		actualID := ""
		if se, ok := err.(*SagaError); ok {
			actualID = se.ID
		} else if err != nil {
			actualID = "INTERNAL"
		}
		rec.ActualOutput = strings.Join(out, "\n")
		rec.ActualError = actualID
		if tc.ErrorID != "" {
			rec.Pass = actualID == tc.ErrorID
		} else {
			rec.Pass = err == nil && rec.ActualOutput == tc.Output
		}
		if rec.Pass {
			passed++
		}
		records = append(records, rec)
	}
	return map[string]any{"schema": 1, "implementation": "saga-native", "implementation_version": sagaGoVersion, "language": "Saga", "language_version": "2027-preview", "profile": "Edition 2027 Preview", "total": len(records), "passed": passed, "pass": passed == len(records), "cases": records}
}
func encodeEdition2027Conformance() string {
	b, _ := json.Marshal(runEdition2027Conformance())
	return string(b)
}
