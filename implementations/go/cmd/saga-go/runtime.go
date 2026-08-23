package main

import (
	"fmt"
	"math"
	"math/big"
	"sort"
	"strings"
	"sync"
	"unicode/utf8"
)

type returnSignal struct{ value Value }

func (e returnSignal) Error() string { return "return" }

type breakSignal struct{}

func (e breakSignal) Error() string { return "break" }

type continueSignal struct{}

func (e continueSignal) Error() string { return "continue" }

type thrownSignal struct{ value Value }

func (e thrownSignal) Error() string { return "throw" }

type Interpreter struct {
	Global      *Env
	Env         *Env
	Functions   map[string]*Function
	Classes     map[string]*Class
	Checker     *Checker
	Precision   int
	output      func(string)
	outMu       sync.Mutex
	owner       []string
	DebugHook   func(Token, *Env)
	deferFrames [][]Expr
	taskGroups  []*TaskGroupValue
	UnsafeDepth int
	AllowDevice bool
	NetHosts    []string
}

func NewInterpreter(checker *Checker, output func(string)) *Interpreter {
	if output == nil {
		output = func(s string) { fmt.Println(s) }
	}
	g := newEnv(nil)
	in := &Interpreter{Global: g, Env: g, Functions: map[string]*Function{}, Classes: map[string]*Class{}, Checker: checker, Precision: 50, output: output}
	for name := range coreBuiltins {
		n := name
		if n == "Option" || n == "Result" {
			continue
		}
		g.define(n, &NativeFunc{Name: n, Call: func(i *Interpreter, args []Value) (Value, error) { return i.callBuiltin(n, args) }}, false)
	}
	g.define("Option", EnumType{Name: "Option", Variants: map[string]int{"Some": 1, "None": 0}}, false)
	g.define("Result", EnumType{Name: "Result", Variants: map[string]int{"Ok": 1, "Err": 1}}, false)
	return in
}

func (i *Interpreter) emit(s string) { i.outMu.Lock(); defer i.outMu.Unlock(); i.output(s) }

func (i *Interpreter) resolveRuntimeClass(name string) (*Class, bool) {
	if cl := i.Classes[name]; cl != nil {
		return cl, true
	}
	dot := strings.IndexByte(name, '.')
	if dot <= 0 || dot == len(name)-1 {
		return nil, false
	}
	bind, member := name[:dot], name[dot+1:]
	value, err := i.Env.get(bind)
	if err != nil {
		return nil, false
	}
	module, ok := value.(SourceModuleValue)
	if !ok {
		return nil, false
	}
	cl, _ := module.Exports[member].(*Class)
	return cl, false
}

func (i *Interpreter) Interpret(stmts []Stmt) error {
	stmts = optimizeProgram(stmts)
	// Source modules initialize once before importer class resolution so a local
	// class can inherit methods from `m.Base` without flattening module state.
	for _, s := range stmts {
		if _, ok := s.(*SourceModuleStmt); ok {
			if err := i.exec(s); err != nil {
				return err
			}
		}
	}
	// Enum and class shells first.
	for _, s := range stmts {
		if d, ok := s.(*EnumDecl); ok {
			vs := map[string]int{}
			for _, v := range d.Variants {
				vs[v.Name] = len(v.Payload)
			}
			i.Global.define(d.Name, EnumType{Name: d.Name, Variants: vs}, false)
		}
	}
	// Class shells first.
	for _, s := range stmts {
		if d, ok := s.(*ClassDecl); ok {
			ci := i.Checker.Classes[d.Name]
			cl := &Class{Info: ci, Decl: d, Methods: map[string]*Function{}}
			i.Classes[d.Name] = cl
			i.Global.define(d.Name, cl, false)
		}
	}
	for _, s := range stmts {
		if d, ok := s.(*FnDecl); ok {
			fn := &Function{Decl: d, Closure: i.Global}
			i.Functions[d.Name] = fn
			i.Global.define(d.Name, fn, false)
		}
	}
	// Build class methods, inheriting definitions.
	built := map[string]bool{}
	var build func(string) error
	build = func(name string) error {
		if built[name] {
			return nil
		}
		cl := i.Classes[name]
		if cl == nil {
			return fmt.Errorf("missing class %s", name)
		}
		baseName := objectTypeName(cl.Info.Base)
		if baseName != "" {
			baseClass, local := i.resolveRuntimeClass(baseName)
			if baseClass == nil {
				return fmt.Errorf("missing class %s", baseName)
			}
			if local {
				if err := build(baseName); err != nil {
					return err
				}
			}
			for n, m := range baseClass.Methods {
				cl.Methods[n] = m
			}
		}
		for _, d := range cl.Decl.Methods {
			cl.Methods[d.Name] = &Function{Decl: d, Closure: i.Global, Owner: name}
		}
		built[name] = true
		return nil
	}
	for n := range i.Classes {
		if err := build(n); err != nil {
			return err
		}
	}
	for _, s := range stmts {
		switch s.(type) {
		case *FnDecl, *ClassDecl, *EnumDecl, *TestDecl, *SourceModuleStmt:
			continue
		}
		if err := i.exec(s); err != nil {
			return normalizeControl(err)
		}
	}
	return nil
}
func normalizeControl(err error) error {
	switch err.(type) {
	case returnSignal:
		return &SagaError{Code: "SAGA-R001", ID: "SAGA-R112", Message: "return outside function"}
	case breakSignal, continueSignal:
		return &SagaError{Code: "SAGA-R001", ID: "SAGA-R113", Message: "loop control outside loop"}
	}
	return err
}

func enumRuntimeParts(v Value) (string, string, []Value, bool) {
	switch q := v.(type) {
	case EnumValue:
		return q.Enum, q.Variant, q.Payload, true
	case OptionValue:
		if q.Present {
			return "Option", "Some", []Value{q.Value}, true
		}
		return "Option", "None", nil, true
	case ResultValue:
		if q.OK {
			return "Result", "Ok", []Value{q.Value}, true
		}
		return "Result", "Err", []Value{q.Value}, true
	default:
		return "", "", nil, false
	}
}

func (i *Interpreter) exec(s Stmt) error {
	if i.DebugHook != nil {
		i.DebugHook(s.token(), i.Env)
	}
	switch x := s.(type) {
	case *EditionDecl, *ModuleDecl:
		return nil
	case *SourceModuleStmt:
		childChecker := NewChecker()
		if err := childChecker.Check(x.Stmts); err != nil {
			return err
		}
		child := NewInterpreter(childChecker, i.output)
		child.AllowDevice = i.AllowDevice
		child.NetHosts = append([]string(nil), i.NetHosts...)
		if err := child.Interpret(x.Stmts); err != nil {
			return err
		}
		bind := x.BindName
		if bind == "" {
			bind = x.Name
		}
		exports := map[string]Value{}
		for _, st := range x.Stmts {
			name, public := "", false
			switch d := st.(type) {
			case *VarDecl:
				name, public = d.Name, d.Visibility == "public"
			case *FnDecl:
				name, public = d.Name, d.Visibility == "public"
			case *ClassDecl:
				name, public = d.Name, d.Visibility == "public"
			case *EnumDecl:
				name, public = d.Name, d.Visibility == "public"
			}
			if public {
				if v, e := child.Global.get(name); e == nil {
					if et, ok := v.(EnumType); ok {
						et.Name = bind + "." + name
						v = et
						if cell, ok := child.Global.getCell(name); ok {
							cell.V = et
						}
					}
					exports[name] = v
				}
			}
		}
		i.Env.define(bind, SourceModuleValue{Name: x.Name, Exports: exports}, false)
		return nil
	case *UseStmt:
		if x.Module == "task" || x.Module == "sys" || x.Module == "compiler" || x.Module == "io" || x.Module == "json" || x.Module == "time" || x.Module == "math" || x.Module == "random" || x.Module == "crypto" || x.Module == "security" || x.Module == "game" || x.Module == "net" || x.Module == "http" || x.Module == "web" || x.Module == "app" || x.Module == "db" || x.Module == "process" || x.Module == "regex" || x.Module == "ffi" || x.Module == "jit" || x.Module == "embedded" || x.Module == "machine" || x.Module == "drone" || x.Module == "vision" {
			bind := x.Module
			if x.Alias != "" {
				bind = x.Alias
			}
			i.Env.define(bind, CoreModule{Name: x.Module}, false)
			return nil
		}
		if x.SourcePath != "" {
			return nil
		} // flattened by loader
		return &SagaError{Code: "SAGA-R001", ID: "SAGA-R120", Message: "hosted module is not available in Standard Core: " + x.Module, File: x.Tok.File, Line: x.Tok.Line, Col: x.Tok.Col}
	case *VarDecl:
		v, e := i.eval(x.Init)
		if e != nil {
			return e
		}
		i.Env.define(x.Name, v, x.Mutable)
		return nil
	case *Assign:
		return i.execAssign(x)
	case *ExprStmt:
		_, e := i.eval(x.Expr)
		return e
	case *DeferStmt:
		if len(i.deferFrames) == 0 {
			return i.rerr(x.Tok, "SAGA-R183", "defer is only valid inside a lexical block")
		}
		idx := len(i.deferFrames) - 1
		i.deferFrames[idx] = append(i.deferFrames[idx], x.Value)
		return nil
	case *UsingStmt:
		v, e := i.eval(x.Init)
		if e != nil {
			return e
		}
		env := newEnv(i.Env)
		env.define(x.Name, v, false)
		bodyErr := i.execBlock(x.Body.Stmts, env)
		closeErr := i.closeResource(v, x.Tok)
		if bodyErr != nil {
			return bodyErr
		}
		return closeErr
	case *UnsafeStmt:
		i.UnsafeDepth++
		e := i.exec(x.Body)
		i.UnsafeDepth--
		return e
	case *TaskGroupStmt:
		g := &TaskGroupValue{}
		i.taskGroups = append(i.taskGroups, g)
		e := i.exec(x.Body)
		i.taskGroups = i.taskGroups[:len(i.taskGroups)-1]
		g.mu.Lock()
		futures := append([]*FutureValue{}, g.Futures...)
		g.mu.Unlock()
		if e != nil {
			for _, f := range futures {
				f.cancelled.Store(true)
			}
		}
		for _, f := range futures {
			if _, ae := f.await(); e == nil && ae != nil {
				e = ae
			}
		}
		return e
	case *Block:
		return i.execBlock(x.Stmts, newEnv(i.Env))
	case *IfStmt:
		v, e := i.eval(x.Cond)
		if e != nil {
			return e
		}
		b, ok := v.(bool)
		if !ok {
			return i.rerr(x.Tok, "SAGA-R121", "condition is not bool")
		}
		if b {
			return i.exec(x.Then)
		}
		if x.Else != nil {
			return i.exec(x.Else)
		}
		return nil
	case *WhileStmt:
		for {
			v, e := i.eval(x.Cond)
			if e != nil {
				return e
			}
			b, ok := v.(bool)
			if !ok {
				return i.rerr(x.Tok, "SAGA-R121", "condition is not bool")
			}
			if !b {
				return nil
			}
			e = i.exec(x.Body)
			switch e.(type) {
			case nil:
			case continueSignal:
				continue
			case breakSignal:
				return nil
			default:
				return e
			}
		}
	case *ForStmt:
		it, e := i.eval(x.Iterable)
		if e != nil {
			return e
		}
		vals, e := iterValues(it)
		if e != nil {
			return e
		}
		for _, v := range vals {
			env := newEnv(i.Env)
			env.define(x.Name, v, false)
			e = i.execBlock(x.Body.Stmts, env)
			switch e.(type) {
			case nil:
			case continueSignal:
				continue
			case breakSignal:
				return nil
			default:
				return e
			}
		}
		return nil
	case *BreakStmt:
		return breakSignal{}
	case *ContinueStmt:
		return continueSignal{}
	case *ReturnStmt:
		var v Value = nil
		var e error
		if x.Value != nil {
			v, e = i.eval(x.Value)
			if e != nil {
				return e
			}
		}
		return returnSignal{v}
	case *ThrowStmt:
		v, e := i.eval(x.Value)
		if e != nil {
			return e
		}
		if ev, ok := v.(ErrorValue); ok {
			return thrownSignal{ev}
		}
		return thrownSignal{ErrorValue{Kind: "Thrown", Message: formatValue(v, false)}}
	case *TryStmt:
		e := i.exec(x.Try)
		var pending error = e
		if e != nil && x.Catch != nil {
			var caught Value
			switch q := e.(type) {
			case thrownSignal:
				caught = q.value
			case *SagaError:
				caught = ErrorValue{Kind: q.ID, Message: q.Message}
			default:
				caught = nil
			}
			if caught != nil {
				env := newEnv(i.Env)
				env.define(x.CatchName, caught, false)
				pending = i.execBlock(x.Catch.Stmts, env)
			}
		}
		if x.Finally != nil {
			if fe := i.exec(x.Finally); fe != nil {
				return fe
			}
		}
		return pending
	case *MatchStmt:
		v, e := i.eval(x.Value)
		if e != nil {
			return e
		}
		for _, mc := range x.Cases {
			if enumName, enumVariant, enumPayload, ok := enumRuntimeParts(v); ok {
				if call, ok := mc.Pattern.(*Call); ok {
					if m, ok := call.Callee.(*Member); ok {
						owner, qok := sourceQualifiedExprName(m.Target)
						if qok && (enumName == owner || strings.HasSuffix(enumName, "."+owner)) {
							// A payload pattern belonging to the matched enum is
							// syntactic data, not an expression to evaluate. If its
							// variant does not match, continue to the next case instead
							// of resolving binding names as ordinary variables.
							if enumVariant != m.Name || len(enumPayload) != len(call.Args) {
								continue
							}
							env := newEnv(i.Env)
							valid := true
							for idx, a := range call.Args {
								vr, vok := a.(*Variable)
								if !vok {
									valid = false
									break
								}
								if vr.Name != "_" {
									env.define(vr.Name, enumPayload[idx], false)
								}
							}
							if valid {
								return i.execBlock(mc.Body.Stmts, env)
							}
							continue
						}
					}
				}
			}
			p, pe := i.eval(mc.Pattern)
			if pe != nil {
				return pe
			}
			if equalValues(v, p, nil) {
				return i.exec(mc.Body)
			}
		}
		if x.Default != nil {
			return i.exec(x.Default)
		}
		return nil
	case *FnDecl, *ClassDecl, *EnumDecl, *TestDecl:
		return nil
	}
	return i.rerr(s.token(), "SAGA-R199", "unsupported statement")
}
func (i *Interpreter) execBlock(stmts []Stmt, env *Env) error {
	old := i.Env
	i.Env = env
	defer func() { i.Env = old }()
	i.deferFrames = append(i.deferFrames, nil)
	frame := len(i.deferFrames) - 1
	defer func() { i.deferFrames = i.deferFrames[:frame] }()
	for _, s := range stmts {
		if d, ok := s.(*FnDecl); ok {
			if _, exists := env.Values[d.Name]; !exists {
				env.define(d.Name, &Function{Decl: d, Closure: env}, false)
			}
		}
	}
	var mainErr error
	for _, s := range stmts {
		if e := i.exec(s); e != nil {
			mainErr = e
			break
		}
	}
	for j := len(i.deferFrames[frame]) - 1; j >= 0; j-- {
		if _, e := i.eval(i.deferFrames[frame][j]); e != nil && mainErr == nil {
			mainErr = e
		}
	}
	return mainErr
}
func (i *Interpreter) execAssign(x *Assign) error {
	switch t := x.Target.(type) {
	case *Variable:
		cell, ok := i.Env.getCell(t.Name)
		if !ok {
			// Natural first assignment is an immutable binding, equivalent to an
			// inferred let.  The initializer is evaluated only after name
			// resolution has established that this is a new binding.
			v, err := i.eval(x.Value)
			if err != nil {
				return err
			}
			i.Env.define(t.Name, v, false)
			return nil
		}
		if !cell.Mutable {
			return i.rerr(t.Tok, "SAGA-R111", "cannot assign to immutable binding: "+t.Name)
		}
		v, err := i.eval(x.Value)
		if err != nil {
			return err
		}
		cell.V = v
		cell.Moved = false
		return nil
	case *Member:
		// Saga commits an assignment only after the target has been resolved.
		// Evaluate and validate the receiver before any RHS side effect.
		o, err := i.eval(t.Target)
		if err != nil {
			return err
		}
		ins, ok := o.(*Instance)
		if !ok {
			return i.rerr(t.Tok, "SAGA-R122", "member assignment requires object")
		}
		f, ok := ins.Class.Info.Fields[t.Name]
		if !ok {
			return i.rerr(t.Tok, "SAGA-R123", "unknown field: "+t.Name)
		}
		if f.Private && i.currentOwner() != f.Owner {
			return i.rerr(t.Tok, "SAGA-R124", "private member access")
		}
		if !f.Mutable {
			return i.rerr(t.Tok, "SAGA-R111", "field is immutable")
		}
		v, err := i.eval(x.Value)
		if err != nil {
			return err
		}
		ins.Fields[t.Name] = v
		return nil
	default:
		return i.rerr(x.Target.token(), "SAGA-R125", "invalid assignment target")
	}
}

func (i *Interpreter) currentOwner() string {
	if len(i.owner) == 0 {
		return ""
	}
	return i.owner[len(i.owner)-1]
}
func (i *Interpreter) rerr(t Token, id, msg string) error {
	return &SagaError{Code: "SAGA-R001", ID: id, Message: msg, File: t.File, Line: t.Line, Col: t.Col}
}

func (i *Interpreter) eval(e Expr) (Value, error) {
	switch x := e.(type) {
	case *Literal:
		return cloneValue(x.Value), nil
	case *InterpolatedString:
		var b strings.Builder
		for j, text := range x.Texts {
			b.WriteString(text)
			if j < len(x.Exprs) {
				v, e := i.eval(x.Exprs[j])
				if e != nil {
					return nil, e
				}
				b.WriteString(formatValue(v, false))
			}
		}
		return b.String(), nil
	case *Variable:
		return i.Env.get(x.Name)
	case *ClosureExpr:
		return &ClosureValue{Expr: x, Env: i.Env}, nil
	case *ListExpr:
		out := make([]Value, 0, len(x.Items))
		for _, a := range x.Items {
			v, e := i.eval(a)
			if e != nil {
				return nil, e
			}
			out = append(out, v)
		}
		return out, nil
	case *Unary:
		v, e := i.eval(x.Right)
		if e != nil {
			return nil, e
		}
		switch x.Op.Kind {
		case BANG, NOT:
			b, ok := v.(bool)
			if !ok {
				return nil, i.rerr(x.Op, "SAGA-R126", "not requires bool")
			}
			return !b, nil
		case MINUS:
			if n, ok := v.(Number); ok {
				return Number{R: new(big.Rat).Neg(n.R), Kind: n.Kind}, nil
			}
			if f, ok := v.(FloatValue); ok {
				f.V = -f.V
				return f, nil
			}
			return nil, i.rerr(x.Op, "SAGA-R127", "unary minus requires number")
		}
		return nil, i.rerr(x.Op, "SAGA-R199", "unknown unary operator")
	case *AwaitExpr:
		v, e := i.eval(x.Value)
		if e != nil {
			return nil, e
		}
		f, ok := v.(*FutureValue)
		if !ok {
			return nil, i.rerr(x.Tok, "SAGA-R184", "await requires future")
		}
		return f.await()
	case *MoveExpr:
		if q, ok := x.Value.(*Variable); ok {
			return i.Env.move(q.Name)
		}
		return nil, i.rerr(x.Tok, "SAGA-R181", "move requires a named resource binding")
	case *PropagateExpr:
		v, e := i.eval(x.Value)
		if e != nil {
			return nil, e
		}
		switch q := v.(type) {
		case ResultValue:
			if q.OK {
				return q.Value, nil
			}
			return nil, returnSignal{value: q}
		case OptionValue:
			if q.Present {
				return q.Value, nil
			}
			return nil, returnSignal{value: q}
		default:
			return nil, i.rerr(x.Tok, "SAGA-R185", "? requires option or result")
		}
	case *Binary:
		return i.evalBinary(x)
	case *RangeExpr:
		a, e := i.eval(x.Start)
		if e != nil {
			return nil, e
		}
		b, e := i.eval(x.End)
		if e != nil {
			return nil, e
		}
		an, ok := a.(Number)
		if !ok {
			return nil, i.rerr(x.Op, "SAGA-R128", "range endpoints must be int")
		}
		bn, ok := b.(Number)
		if !ok {
			return nil, i.rerr(x.Op, "SAGA-R128", "range endpoints must be int")
		}
		ai, aok := an.Int()
		bi, bok := bn.Int()
		if !aok || !bok {
			return nil, i.rerr(x.Op, "SAGA-R128", "range endpoints must be int")
		}
		return RangeValue{Start: ai, End: bi}, nil
	case *Index:
		o, e := i.eval(x.Target)
		if e != nil {
			return nil, e
		}
		iv, e := i.eval(x.Index)
		if e != nil {
			return nil, e
		}
		n, ok := iv.(Number)
		if !ok {
			return nil, i.rerr(x.Tok, "SAGA-R129", "index must be int")
		}
		idx, ok := n.Int()
		if !ok || !idx.IsInt64() {
			return nil, i.rerr(x.Tok, "SAGA-R101", "index out of range")
		}
		k := idx.Int64()
		switch q := o.(type) {
		case []Value:
			if k < 0 || k >= int64(len(q)) {
				return nil, i.rerr(x.Tok, "SAGA-R101", "index out of range")
			}
			return q[k], nil
		case string:
			r := []rune(q)
			if k < 0 || k >= int64(len(r)) {
				return nil, i.rerr(x.Tok, "SAGA-R101", "index out of range")
			}
			return string(r[k]), nil
		default:
			return nil, i.rerr(x.Tok, "SAGA-R130", "value is not indexable")
		}
	case *Member:
		o, e := i.eval(x.Target)
		if e != nil {
			return nil, e
		}
		return i.member(o, x.Name, x.Tok)
	case *Call:
		cal, e := i.eval(x.Callee)
		if e != nil {
			return nil, e
		}
		args := []Value{}
		for _, a := range x.Args {
			v, e := i.eval(a)
			if e != nil {
				return nil, e
			}
			args = append(args, v)
		}
		return i.invoke(cal, args, x.Tok)
	}
	return nil, i.rerr(e.token(), "SAGA-R199", "unsupported expression")
}
func (i *Interpreter) evalBinary(x *Binary) (Value, error) {
	l, e := i.eval(x.Left)
	if e != nil {
		return nil, e
	}
	if x.Op.Kind == AND {
		b, ok := l.(bool)
		if !ok {
			return nil, i.rerr(x.Op, "SAGA-R126", "and requires bool")
		}
		if !b {
			return false, nil
		}
		r, e := i.eval(x.Right)
		if e != nil {
			return nil, e
		}
		rb, ok := r.(bool)
		if !ok {
			return nil, i.rerr(x.Op, "SAGA-R126", "and requires bool")
		}
		return rb, nil
	}
	if x.Op.Kind == OR {
		b, ok := l.(bool)
		if !ok {
			return nil, i.rerr(x.Op, "SAGA-R126", "or requires bool")
		}
		if b {
			return true, nil
		}
		r, e := i.eval(x.Right)
		if e != nil {
			return nil, e
		}
		rb, ok := r.(bool)
		if !ok {
			return nil, i.rerr(x.Op, "SAGA-R126", "or requires bool")
		}
		return rb, nil
	}
	r, e := i.eval(x.Right)
	if e != nil {
		return nil, e
	}
	if x.Op.Kind == EQEQ {
		return equalValues(l, r, nil), nil
	}
	if x.Op.Kind == BANGEQ {
		return !equalValues(l, r, nil), nil
	}
	if x.Op.Kind == PLUS {
		if a, ok := l.(string); ok {
			b, ok := r.(string)
			if !ok {
				return nil, i.rerr(x.Op, "SAGA-R131", "text can only be added to text")
			}
			return a + b, nil
		}
	}
	if ln, ok := l.(Number); ok {
		rn, ok := r.(Number)
		if !ok {
			return nil, i.rerr(x.Op, "SAGA-R127", "arithmetic requires numbers")
		}
		return numericOp(ln, rn, x.Op.Kind, i.Precision, x.Op)
	}
	if lf, ok := l.(FloatValue); ok {
		rf, ok := r.(FloatValue)
		if !ok {
			return nil, i.rerr(x.Op, "SAGA-R186", "exact and floating-point numbers require explicit conversion")
		}
		return floatOp(lf, rf, x.Op.Kind, x.Op)
	}
	if ls, ok := l.(string); ok && (x.Op.Kind == LESS || x.Op.Kind == LESSEQ || x.Op.Kind == GREATER || x.Op.Kind == GREATEREQ) {
		rs, ok := r.(string)
		if !ok {
			return nil, i.rerr(x.Op, "SAGA-R132", "text comparison requires text")
		}
		c := strings.Compare(ls, rs)
		switch x.Op.Kind {
		case LESS:
			return c < 0, nil
		case LESSEQ:
			return c <= 0, nil
		case GREATER:
			return c > 0, nil
		case GREATEREQ:
			return c >= 0, nil
		}
	}
	return nil, i.rerr(x.Op, "SAGA-R127", "unsupported operands")
}

func floatOp(a, b FloatValue, k Kind, t Token) (Value, error) {
	bits := a.Bits
	if b.Bits > bits {
		bits = b.Bits
	}
	val := func(v float64) FloatValue {
		if bits == 32 {
			v = float64(float32(v))
		}
		return FloatValue{V: v, Bits: bits}
	}
	switch k {
	case PLUS:
		return val(a.V + b.V), nil
	case MINUS:
		return val(a.V - b.V), nil
	case STAR:
		return val(a.V * b.V), nil
	case SLASH:
		// IEEE division by zero intentionally yields Inf/NaN rather than the exact-number divide-by-zero diagnostic.
		return val(a.V / b.V), nil
	case PERCENT:
		return val(math.Mod(a.V, b.V)), nil
	case POWER:
		return val(math.Pow(a.V, b.V)), nil
	case LESS:
		return a.V < b.V, nil
	case LESSEQ:
		return a.V <= b.V, nil
	case GREATER:
		return a.V > b.V, nil
	case GREATEREQ:
		return a.V >= b.V, nil
	}
	return nil, &SagaError{Code: "SAGA-R001", ID: "SAGA-R199", Message: "unknown floating-point operator", File: t.File, Line: t.Line, Col: t.Col}
}

func numericOp(a, b Number, k Kind, precision int, t Token) (Value, error) {
	ar, br := new(big.Rat).Set(a.R), new(big.Rat).Set(b.R)
	kind := "int"
	if a.Kind == "decimal" || b.Kind == "decimal" {
		kind = "decimal"
	} else if a.Kind == "rational" || b.Kind == "rational" {
		kind = "rational"
	}
	switch k {
	case PLUS:
		return Number{new(big.Rat).Add(ar, br), kind}, nil
	case MINUS:
		return Number{new(big.Rat).Sub(ar, br), kind}, nil
	case STAR:
		return Number{new(big.Rat).Mul(ar, br), kind}, nil
	case SLASH:
		if br.Sign() == 0 {
			return nil, &SagaError{Code: "SAGA-R001", ID: "SAGA-R102", Message: "division by zero", File: t.File, Line: t.Line, Col: t.Col}
		}
		if kind != "decimal" {
			kind = "rational"
		}
		return Number{new(big.Rat).Quo(ar, br), kind}, nil
	case PERCENT:
		ai, aok := a.Int()
		bi, bok := b.Int()
		if !aok || !bok || bi.Sign() == 0 {
			return nil, &SagaError{Code: "SAGA-R001", ID: "SAGA-R102", Message: "invalid remainder", File: t.File, Line: t.Line, Col: t.Col}
		}
		return numberFromBigInt(new(big.Int).Rem(ai, bi)), nil
	case POWER:
		exp, ok := b.Int()
		if !ok {
			return nil, &SagaError{Code: "SAGA-R001", ID: "SAGA-R133", Message: "exponent must be an exact integer", File: t.File, Line: t.Line, Col: t.Col}
		}
		neg := exp.Sign() < 0
		eabs := new(big.Int).Abs(exp)
		num := new(big.Int).Exp(ar.Num(), eabs, nil)
		den := new(big.Int).Exp(ar.Denom(), eabs, nil)
		rr := new(big.Rat).SetFrac(num, den)
		if neg {
			if rr.Sign() == 0 {
				return nil, &SagaError{Code: "SAGA-R001", ID: "SAGA-R102", Message: "zero cannot have a negative exponent", File: t.File, Line: t.Line, Col: t.Col}
			}
			rr.Inv(rr)
		}
		if kind == "int" && !rr.IsInt() {
			kind = "rational"
		}
		return Number{rr, kind}, nil
	case LESS:
		return ar.Cmp(br) < 0, nil
	case LESSEQ:
		return ar.Cmp(br) <= 0, nil
	case GREATER:
		return ar.Cmp(br) > 0, nil
	case GREATEREQ:
		return ar.Cmp(br) >= 0, nil
	}
	_ = precision
	return nil, &SagaError{Code: "SAGA-R001", ID: "SAGA-R199", Message: "unknown numeric operator"}
}

func (i *Interpreter) member(o Value, name string, t Token) (Value, error) {
	switch q := o.(type) {
	case SourceModuleValue:
		v, ok := q.Exports[name]
		if !ok {
			return nil, i.rerr(t, "SAGA-R187", "module member is not public or does not exist: "+q.Name+"."+name)
		}
		return v, nil
	case EnumType:
		if arity, ok := q.Variants[name]; ok {
			if arity > 0 {
				return &EnumConstructor{Enum: q.Name, Variant: name, Arity: arity}, nil
			}
			if q.Name == "Option" && name == "None" {
				return OptionValue{Present: false}, nil
			}
			return EnumValue{Enum: q.Name, Variant: name}, nil
		}
		return nil, i.rerr(t, "SAGA-R123", "unknown enum variant: "+q.Name+"."+name)
	case CoreModule:
		switch q.Name {
		case "task":
			return &NativeFunc{Name: "task." + name, Call: func(ii *Interpreter, args []Value) (Value, error) { return ii.callTask(name, args, t) }}, nil
		case "sys":
			return &NativeFunc{Name: "sys." + name, Call: func(ii *Interpreter, args []Value) (Value, error) { return ii.callSys(name, args, t) }}, nil
		case "compiler":
			return &NativeFunc{Name: "compiler." + name, Call: func(ii *Interpreter, args []Value) (Value, error) { return ii.callCompiler(name, args, t) }}, nil
		case "io", "json", "time", "math", "random", "crypto", "security", "game", "net", "http", "web", "app", "db", "process", "regex", "embedded", "machine", "drone", "vision":
			return &NativeFunc{Name: q.Name + "." + name, Call: func(ii *Interpreter, args []Value) (Value, error) { return ii.callNativeModule(q.Name, name, args, t) }}, nil
		case "ffi":
			return &NativeFunc{Name: "ffi." + name, Call: func(ii *Interpreter, args []Value) (Value, error) { return ii.callFFI(name, args, t) }}, nil
		case "jit":
			return &NativeFunc{Name: "jit." + name, Call: func(ii *Interpreter, args []Value) (Value, error) { return ii.callJIT(name, args, t) }}, nil
		}
	case []Value:
		switch name {
		case "map", "filter", "each", "reduce", "fold", "find", "any", "all", "none", "sorted", "sortedBy", "distinct", "take", "skip", "zip", "flatten", "flatMap", "chunk", "window", "group", "groupBy", "sum", "contains":
			return &ExtensionMethod{Receiver: q, Name: name}, nil
		}
	case string:
		switch name {
		case "trim", "upper", "lower", "split", "startsWith", "endsWith", "contains", "length":
			return &ExtensionMethod{Receiver: q, Name: name}, nil
		}
	case MapValue:
		switch name {
		case "keys", "values", "containsKey", "get":
			return &ExtensionMethod{Receiver: q, Name: name}, nil
		}
	case SetValue:
		switch name {
		case "contains", "toList":
			return &ExtensionMethod{Receiver: q, Name: name}, nil
		}
	case ErrorValue:
		if name == "message" {
			return q.Message, nil
		}
		if name == "kind" {
			return q.Kind, nil
		}
	case *Instance:
		if f, ok := q.Class.Info.Fields[name]; ok {
			if f.Private && i.currentOwner() != f.Owner {
				return nil, i.rerr(t, "SAGA-R124", "private member access")
			}
			return q.Fields[name], nil
		}
		if m, ok := q.Class.Methods[name]; ok {
			return &BoundMethod{Receiver: q, Function: m}, nil
		}
	}
	return nil, i.rerr(t, "SAGA-R123", "unknown member: "+name)
}
func (i *Interpreter) invoke(c Value, args []Value, t Token) (Value, error) {
	if f, ok := c.(*Function); ok && f.Decl != nil && f.Decl.Async {
		p, err := i.prepareIsolatedCall(c, args)
		if err != nil {
			return nil, err
		}
		future := newFuture(p.run)
		if len(i.taskGroups) > 0 {
			g := i.taskGroups[len(i.taskGroups)-1]
			g.mu.Lock()
			g.Futures = append(g.Futures, future)
			g.mu.Unlock()
		}
		return future, nil
	}
	if b, ok := c.(*BoundMethod); ok && b.Function != nil && b.Function.Decl != nil && b.Function.Decl.Async {
		p, err := i.prepareIsolatedCall(c, args)
		if err != nil {
			return nil, err
		}
		future := newFuture(p.run)
		if len(i.taskGroups) > 0 {
			g := i.taskGroups[len(i.taskGroups)-1]
			g.mu.Lock()
			g.Futures = append(g.Futures, future)
			g.mu.Unlock()
		}
		return future, nil
	}
	return i.invokeDirect(c, args, t)
}

func (i *Interpreter) invokeDirect(c Value, args []Value, t Token) (Value, error) {
	switch f := c.(type) {
	case *NativeFunc:
		v, err := f.Call(i, args)
		if err != nil {
			if _, ok := err.(*SagaError); ok {
				return nil, err
			}
			return nil, i.rerr(t, "SAGA-R150", err.Error())
		}
		return v, nil
	case *ClosureValue:
		return i.callClosure(f, args, t)
	case *ExtensionMethod:
		return i.callExtension(f, args, t)
	case *Function:
		if f.Decl != nil && f.Decl.ExternABI != "" {
			if i.UnsafeDepth == 0 {
				return nil, i.rerr(t, "SAGA-R188", "extern calls require unsafe block")
			}
			return i.callExtern(f.Decl, args, t)
		}
		return i.callFunction(f, nil, args, t)
	case *BoundMethod:
		if f.Function != nil && f.Function.Decl != nil && f.Function.Decl.ExternABI != "" {
			return nil, i.rerr(t, "SAGA-R188", "extern methods are not supported")
		}
		return i.callFunction(f.Function, f.Receiver, args, t)
	case *Class:
		return i.construct(f, args, t)
	case *EnumConstructor:
		if len(args) != f.Arity {
			return nil, i.rerr(t, "SAGA-R136", fmt.Sprintf("%s.%s expects %d payload values", f.Enum, f.Variant, f.Arity))
		}
		if f.Enum == "Option" && f.Variant == "Some" {
			return OptionValue{Present: true, Value: args[0]}, nil
		}
		if f.Enum == "Result" && f.Variant == "Ok" {
			return ResultValue{OK: true, Value: args[0]}, nil
		}
		if f.Enum == "Result" && f.Variant == "Err" {
			return ResultValue{OK: false, Value: args[0]}, nil
		}
		return EnumValue{Enum: f.Enum, Variant: f.Variant, Payload: append([]Value{}, args...)}, nil
	default:
		return nil, i.rerr(t, "SAGA-R134", "value is not callable")
	}
}
func (i *Interpreter) callClosure(c *ClosureValue, args []Value, t Token) (Value, error) {
	if c == nil || c.Expr == nil {
		return nil, i.rerr(t, "SAGA-R134", "invalid closure")
	}
	expr := c.Expr
	if expr.Implicit {
		if len(args) > 1 {
			return nil, i.rerr(t, "SAGA-R136", "implicit closure expects at most 1 argument")
		}
	} else if len(args) != len(expr.Params) {
		return nil, i.rerr(t, "SAGA-R136", fmt.Sprintf("closure expects %d arguments", len(expr.Params)))
	}

	env := newEnv(c.Env)
	if expr.Implicit {
		if len(args) == 1 {
			env.define("it", args[0], false)
		}
	} else {
		for j, p := range expr.Params {
			env.define(p.Lex, args[j], false)
		}
	}

	old := i.Env
	i.Env = env
	defer func() { i.Env = old }()
	i.deferFrames = append(i.deferFrames, nil)
	frame := len(i.deferFrames) - 1
	defer func() { i.deferFrames = i.deferFrames[:frame] }()

	for _, st := range expr.Body.Stmts {
		if d, ok := st.(*FnDecl); ok {
			if _, exists := env.Values[d.Name]; !exists {
				env.define(d.Name, &Function{Decl: d, Closure: env}, false)
			}
		}
	}

	var result Value
	var mainErr error
	for j, st := range expr.Body.Stmts {
		if es, ok := st.(*ExprStmt); ok && j == len(expr.Body.Stmts)-1 {
			result, mainErr = i.eval(es.Expr)
		} else {
			mainErr = i.exec(st)
		}
		if mainErr != nil {
			if rs, ok := mainErr.(returnSignal); ok {
				result, mainErr = rs.value, nil
			}
			break
		}
	}

	for j := len(i.deferFrames[frame]) - 1; j >= 0; j-- {
		if _, err := i.eval(i.deferFrames[frame][j]); err != nil && mainErr == nil {
			mainErr = err
		}
	}
	if mainErr != nil {
		switch mainErr.(type) {
		case breakSignal, continueSignal:
			return nil, i.rerr(t, "SAGA-R138", "break/continue cannot cross a closure boundary")
		default:
			return nil, mainErr
		}
	}
	return result, nil
}

func (i *Interpreter) callExtension(m *ExtensionMethod, args []Value, t Token) (Value, error) {
	arity := func(n int) error {
		if len(args) != n {
			return i.rerr(t, "SAGA-R136", fmt.Sprintf("%s expects %d arguments", m.Name, n))
		}
		return nil
	}
	intArg := func(v Value, label string) (int, error) {
		n, ok := v.(Number)
		if !ok {
			return 0, i.rerr(t, "SAGA-R128", label+" must be int")
		}
		bi, ok := n.Int()
		if !ok || !bi.IsInt64() {
			return 0, i.rerr(t, "SAGA-R128", label+" must be int")
		}
		return int(bi.Int64()), nil
	}

	switch receiver := m.Receiver.(type) {
	case []Value:
		vals := receiver
		switch m.Name {
		case "map":
			if e := arity(1); e != nil {
				return nil, e
			}
			out := make([]Value, 0, len(vals))
			for _, v := range vals {
				x, e := i.invoke(args[0], []Value{v}, t)
				if e != nil {
					return nil, e
				}
				out = append(out, x)
			}
			return out, nil
		case "filter", "any", "all", "none":
			if e := arity(1); e != nil {
				return nil, e
			}
			out := []Value{}
			for _, v := range vals {
				x, e := i.invoke(args[0], []Value{v}, t)
				if e != nil {
					return nil, e
				}
				b, ok := x.(bool)
				if !ok {
					return nil, i.rerr(t, "SAGA-R126", m.Name+" predicate must return bool")
				}
				if m.Name == "any" && b {
					return true, nil
				}
				if m.Name == "all" && !b {
					return false, nil
				}
				if m.Name == "none" && b {
					return false, nil
				}
				if m.Name == "filter" && b {
					out = append(out, v)
				}
			}
			if m.Name == "any" {
				return false, nil
			}
			if m.Name == "all" || m.Name == "none" {
				return true, nil
			}
			return out, nil
		case "each":
			if e := arity(1); e != nil {
				return nil, e
			}
			for _, v := range vals {
				if _, e := i.invoke(args[0], []Value{v}, t); e != nil {
					return nil, e
				}
			}
			return nil, nil
		case "reduce", "fold":
			if e := arity(2); e != nil {
				return nil, e
			}
			acc := args[0]
			for _, v := range vals {
				x, e := i.invoke(args[1], []Value{acc, v}, t)
				if e != nil {
					return nil, e
				}
				acc = x
			}
			return acc, nil
		case "find":
			if e := arity(1); e != nil {
				return nil, e
			}
			for _, v := range vals {
				x, e := i.invoke(args[0], []Value{v}, t)
				if e != nil {
					return nil, e
				}
				b, ok := x.(bool)
				if !ok {
					return nil, i.rerr(t, "SAGA-R126", "find predicate must return bool")
				}
				if b {
					return OptionValue{Present: true, Value: v}, nil
				}
			}
			return OptionValue{}, nil
		case "sorted":
			if e := arity(0); e != nil {
				return nil, e
			}
			return sortValues(vals)
		case "sortedBy":
			if e := arity(1); e != nil {
				return nil, e
			}
			type keyed struct{ value, key Value }
			pairs := make([]keyed, 0, len(vals))
			for _, v := range vals {
				k, e := i.invoke(args[0], []Value{v}, t)
				if e != nil {
					return nil, e
				}
				pairs = append(pairs, keyed{v, k})
			}
			sort.SliceStable(pairs, func(a, b int) bool { return extensionLess(pairs[a].key, pairs[b].key) })
			out := make([]Value, len(pairs))
			for j, p := range pairs {
				out[j] = p.value
			}
			return out, nil
		case "distinct":
			if e := arity(0); e != nil {
				return nil, e
			}
			out := []Value{}
			for _, v := range vals {
				found := false
				for _, q := range out {
					if equalValues(v, q, nil) {
						found = true
						break
					}
				}
				if !found {
					out = append(out, v)
				}
			}
			return out, nil
		case "take", "skip":
			if e := arity(1); e != nil {
				return nil, e
			}
			n, e := intArg(args[0], m.Name+" count")
			if e != nil {
				return nil, e
			}
			if n < 0 {
				n = 0
			}
			if n > len(vals) {
				n = len(vals)
			}
			if m.Name == "take" {
				return append([]Value{}, vals[:n]...), nil
			}
			return append([]Value{}, vals[n:]...), nil
		case "zip":
			if e := arity(1); e != nil {
				return nil, e
			}
			other, ok := args[0].([]Value)
			if !ok {
				return nil, i.rerr(t, "SAGA-R130", "zip requires list")
			}
			n := len(vals)
			if len(other) < n {
				n = len(other)
			}
			out := make([]Value, 0, n)
			for j := 0; j < n; j++ {
				out = append(out, []Value{vals[j], other[j]})
			}
			return out, nil
		case "flatten":
			if e := arity(0); e != nil {
				return nil, e
			}
			out := []Value{}
			for _, v := range vals {
				part, ok := v.([]Value)
				if !ok {
					return nil, i.rerr(t, "SAGA-R130", "flatten requires a list of lists")
				}
				out = append(out, part...)
			}
			return out, nil
		case "flatMap":
			if e := arity(1); e != nil {
				return nil, e
			}
			out := []Value{}
			for _, v := range vals {
				x, e := i.invoke(args[0], []Value{v}, t)
				if e != nil {
					return nil, e
				}
				part, ok := x.([]Value)
				if !ok {
					return nil, i.rerr(t, "SAGA-R130", "flatMap closure must return list")
				}
				out = append(out, part...)
			}
			return out, nil
		case "chunk", "window":
			if e := arity(1); e != nil {
				return nil, e
			}
			size, e := intArg(args[0], m.Name+" size")
			if e != nil {
				return nil, e
			}
			if size <= 0 {
				return nil, i.rerr(t, "SAGA-R128", m.Name+" size must be at least 1")
			}
			out := []Value{}
			if m.Name == "chunk" {
				for j := 0; j < len(vals); j += size {
					end := j + size
					if end > len(vals) {
						end = len(vals)
					}
					out = append(out, append([]Value{}, vals[j:end]...))
				}
				return out, nil
			}
			if size > len(vals) {
				return out, nil
			}
			for j := 0; j+size <= len(vals); j++ {
				out = append(out, append([]Value{}, vals[j:j+size]...))
			}
			return out, nil
		case "group", "groupBy":
			if m.Name == "group" {
				if e := arity(0); e != nil {
					return nil, e
				}
			} else {
				if e := arity(1); e != nil {
					return nil, e
				}
			}
			out := MapValue{}
			for _, v := range vals {
				key := v
				if m.Name == "groupBy" {
					x, e := i.invoke(args[0], []Value{v}, t)
					if e != nil {
						return nil, e
					}
					key = x
				}
				if !isHashable(key) {
					return nil, i.rerr(t, "SAGA-R130", m.Name+" key must be hashable")
				}
				group, _ := mapLookup(out, key)
				bucket, _ := group.([]Value)
				bucket = append(append([]Value{}, bucket...), v)
				out = mapPut(out, key, bucket)
			}
			return out, nil
		case "sum":
			if e := arity(0); e != nil {
				return nil, e
			}
			return i.callBuiltin("sum", []Value{vals})
		case "contains":
			if e := arity(1); e != nil {
				return nil, e
			}
			return i.callBuiltin("contains", []Value{vals, args[0]})
		}
	case string:
		switch m.Name {
		case "trim":
			if e := arity(0); e != nil {
				return nil, e
			}
			return strings.TrimSpace(receiver), nil
		case "upper":
			if e := arity(0); e != nil {
				return nil, e
			}
			return strings.ToUpper(receiver), nil
		case "lower":
			if e := arity(0); e != nil {
				return nil, e
			}
			return strings.ToLower(receiver), nil
		case "split":
			if e := arity(1); e != nil {
				return nil, e
			}
			sep, ok := args[0].(string)
			if !ok {
				return nil, i.rerr(t, "SAGA-R130", "split separator must be text")
			}
			parts := strings.Split(receiver, sep)
			out := make([]Value, len(parts))
			for j, p := range parts {
				out[j] = p
			}
			return out, nil
		case "startsWith", "endsWith", "contains":
			if e := arity(1); e != nil {
				return nil, e
			}
			q, ok := args[0].(string)
			if !ok {
				return nil, i.rerr(t, "SAGA-R130", m.Name+" requires text")
			}
			if m.Name == "startsWith" {
				return strings.HasPrefix(receiver, q), nil
			}
			if m.Name == "endsWith" {
				return strings.HasSuffix(receiver, q), nil
			}
			return strings.Contains(receiver, q), nil
		case "length":
			if e := arity(0); e != nil {
				return nil, e
			}
			return numberFromInt64(int64(utf8.RuneCountInString(receiver))), nil
		}
	case MapValue:
		switch m.Name {
		case "keys":
			if e := arity(0); e != nil {
				return nil, e
			}
			out := make([]Value, 0, len(receiver.Entries))
			for _, e := range receiver.Entries {
				out = append(out, e.Key)
			}
			return out, nil
		case "values":
			if e := arity(0); e != nil {
				return nil, e
			}
			out := make([]Value, 0, len(receiver.Entries))
			for _, e := range receiver.Entries {
				out = append(out, e.Value)
			}
			return out, nil
		case "containsKey":
			if e := arity(1); e != nil {
				return nil, e
			}
			_, ok := mapLookup(receiver, args[0])
			return ok, nil
		case "get":
			if len(args) != 1 && len(args) != 2 {
				return nil, i.rerr(t, "SAGA-R136", "map.get expects 1 or 2 arguments")
			}
			v, ok := mapLookup(receiver, args[0])
			if ok {
				return v, nil
			}
			if len(args) == 2 {
				return args[1], nil
			}
			return OptionValue{}, nil
		}
	case SetValue:
		switch m.Name {
		case "contains":
			if e := arity(1); e != nil {
				return nil, e
			}
			return setHas(receiver, args[0]), nil
		case "toList":
			if e := arity(0); e != nil {
				return nil, e
			}
			return sortValues(receiver.Items)
		}
	}
	return nil, i.rerr(t, "SAGA-R123", "unknown extension method: "+m.Name)
}

func extensionLess(a, b Value) bool {
	if x, ok := a.(Number); ok {
		if y, ok := b.(Number); ok {
			return x.R.Cmp(y.R) < 0
		}
	}
	if x, ok := a.(FloatValue); ok {
		if y, ok := b.(FloatValue); ok {
			return x.V < y.V
		}
	}
	if x, ok := a.(string); ok {
		if y, ok := b.(string); ok {
			return x < y
		}
	}
	if x, ok := a.(bool); ok {
		if y, ok := b.(bool); ok {
			return !x && y
		}
	}
	return formatValue(a, false) < formatValue(b, false)
}

func (i *Interpreter) construct(cl *Class, args []Value, t Token) (Value, error) {
	if cl.Info.Abstract || cl.Info.Interface {
		return nil, i.rerr(t, "SAGA-R135", "abstract/interface cannot be constructed")
	}
	if len(args) != len(cl.Info.FieldOrder) {
		return nil, i.rerr(t, "SAGA-R136", fmt.Sprintf("constructor expects %d arguments", len(cl.Info.FieldOrder)))
	}
	ins := &Instance{Class: cl, Fields: map[string]Value{}}
	for j, n := range cl.Info.FieldOrder {
		ins.Fields[n] = args[j]
	}
	return ins, nil
}

func (i *Interpreter) closeResource(v Value, t Token) error {
	switch q := v.(type) {
	case *FFIPointer:
		if q.Owner && !q.Freed {
			return ffiFreePointer(q)
		}
		return nil
	case *FFICallback:
		ffiCloseCallbackValue(q)
		return nil
	case *KVDBValue:
		q.Closed = true
		return nil
	case *TCPConnValue:
		if q.Conn != nil {
			return q.Conn.Close()
		}
		return nil
	case *TCPListenerValue:
		if q.Listener != nil {
			return q.Listener.Close()
		}
		return nil
	case *GameWindow:
		if q.Renderer != nil {
			destroyGameRenderer(q.Renderer)
			q.Renderer = nil
		}
		if q.Handle != 0 {
			desktopCloseWindow(q.Handle)
			q.Handle = 0
		}
		q.Closed = true
		q.ShouldClose = true
		return nil
	case *GameRenderer:
		destroyGameRenderer(q)
		return nil
	case *GameShader:
		if q.Handle != 0 && q.Renderer != nil {
			desktopShaderDestroy(q.Renderer.Handle, q.Handle)
			q.Handle = 0
		}
		return nil
	case *GamepadHandle:
		if q.Handle != 0 {
			desktopCloseGamepad(q.Handle)
			q.Handle = 0
		}
		return nil
	case *JITFunctionValue:
		if !q.closed {
			jitRelease(q.Handle)
			q.Handle = 0
			q.closed = true
		}
		return nil
	case *TaskPoolValue:
		q.close()
		return nil
	case *ChannelValue:
		if q.closed.CompareAndSwap(false, true) {
			close(q.Ch)
		}
		return nil
	case interface{ sagaMachineClose() error }:
		return q.sagaMachineClose()
	case *Instance:
		if q.Class != nil && q.Class.Info != nil && q.Class.Info.Resource {
			if m, ok := q.Class.Methods["close"]; ok {
				_, e := i.callFunction(m, q, nil, t)
				return e
			}
			return nil
		}
	}
	return i.rerr(t, "SAGA-R189", "value does not have deterministic close semantics")
}
func (i *Interpreter) callFunction(f *Function, self *Instance, args []Value, t Token) (Value, error) {
	if len(args) != len(f.Decl.Params) {
		return nil, i.rerr(t, "SAGA-R136", fmt.Sprintf("function expects %d arguments", len(f.Decl.Params)))
	}
	env := newEnv(f.Closure)
	if self != nil {
		env.define("self", self, false)
	}
	for j, p := range f.Decl.Params {
		env.define(p.Name, args[j], false)
	}
	old := i.Env
	i.Env = env
	i.owner = append(i.owner, f.Owner)
	defer func() { i.Env = old; i.owner = i.owner[:len(i.owner)-1] }()
	if f.Decl.ExprBody != nil {
		v, e := i.eval(f.Decl.ExprBody)
		if rs, ok := e.(returnSignal); ok {
			return rs.value, nil
		}
		return v, e
	}
	if f.Decl.Body != nil {
		e := i.execBlock(f.Decl.Body.Stmts, env)
		if rs, ok := e.(returnSignal); ok {
			return rs.value, nil
		}
		if e != nil {
			return nil, e
		}
	}
	return nil, nil
}

func iterValues(v Value) ([]Value, error) {
	switch q := v.(type) {
	case []Value:
		return append([]Value{}, q...), nil
	case SetValue:
		return append([]Value{}, q.Items...), nil
	case string:
		out := []Value{}
		for _, r := range q {
			out = append(out, string(r))
		}
		return out, nil
	case RangeValue:
		out := []Value{}
		cur := new(big.Int).Set(q.Start)
		step := big.NewInt(1)
		if cur.Cmp(q.End) > 0 {
			step = big.NewInt(-1)
		}
		for {
			cmp := cur.Cmp(q.End)
			if (step.Sign() > 0 && cmp > 0) || (step.Sign() < 0 && cmp < 0) {
				break
			}
			out = append(out, numberFromBigInt(new(big.Int).Set(cur)))
			cur.Add(cur, step)
		}
		return out, nil
	}
	return nil, &SagaError{Code: "SAGA-R001", ID: "SAGA-R137", Message: "value is not iterable"}
}

func cloneValue(v Value) Value { return snapshotValue(v, map[*Instance]*Instance{}) }
func snapshotValue(v Value, memo map[*Instance]*Instance) Value {
	switch q := v.(type) {
	case Number:
		return q.clone()
	case string, bool, nil, []byte:
		return q
	case []Value:
		o := make([]Value, len(q))
		for j, x := range q {
			o[j] = snapshotValue(x, memo)
		}
		return o
	case OptionValue:
		if !q.Present {
			return q
		}
		return OptionValue{Present: true, Value: snapshotValue(q.Value, memo)}
	case ResultValue:
		return ResultValue{OK: q.OK, Value: snapshotValue(q.Value, memo)}
	case MapValue:
		o := MapValue{}
		for _, e := range q.Entries {
			o.Entries = append(o.Entries, MapEntry{snapshotValue(e.Key, memo), snapshotValue(e.Value, memo)})
		}
		return o
	case SetValue:
		o := SetValue{}
		for _, x := range q.Items {
			o.Items = append(o.Items, snapshotValue(x, memo))
		}
		return o
	case *Instance:
		if x, ok := memo[q]; ok {
			return x
		}
		n := &Instance{Class: q.Class, Fields: map[string]Value{}}
		memo[q] = n
		for k, x := range q.Fields {
			n.Fields[k] = snapshotValue(x, memo)
		}
		return n
	case *Function:
		return q
	case *Class:
		return q
	case *NativeFunc:
		return q
	case CoreModule:
		return q
	case EnumType, EnumValue:
		return q
	default:
		return q
	}
}

func snapshotEnv(src *Env, memo map[*Env]*Env, imemo map[*Instance]*Instance) *Env {
	if src == nil {
		return nil
	}
	if e, ok := memo[src]; ok {
		return e
	}
	p := snapshotEnv(src.Parent, memo, imemo)
	out := newEnv(p)
	memo[src] = out
	for n, c := range src.Values {
		var v Value
		switch q := c.V.(type) {
		case *Function:
			v = &Function{Decl: q.Decl, Owner: q.Owner}
		case *Class:
			v = q
		case *NativeFunc:
			v = q
		case CoreModule:
			v = q
		default:
			v = snapshotValue(q, imemo)
		}
		out.define(n, v, c.Mutable)
	}
	for _, c := range out.Values {
		if f, ok := c.V.(*Function); ok {
			orig, _ := src.get(f.Decl.Name)
			if of, ok := orig.(*Function); ok {
				f.Closure = snapshotEnv(of.Closure, memo, imemo)
			}
		}
	}
	return out
}

func isSendValue(v Value, seen map[*Instance]bool) bool {
	switch q := v.(type) {
	case *FutureValue, *TaskPoolValue, *Class, *Function, *NativeFunc, CoreModule, SourceModuleValue, *GameCanvas, *TCPConnValue, *TCPListenerValue, *KVDBValue, *ChannelValue, *ActorValue, *JITFunctionValue, *GameWindow, *GameRenderer, *GameShader, *GamepadHandle:
		return false
	case OptionValue:
		return !q.Present || isSendValue(q.Value, seen)
	case ResultValue:
		return isSendValue(q.Value, seen)
	case []Value:
		for _, x := range q {
			if !isSendValue(x, seen) {
				return false
			}
		}
		return true
	case MapValue:
		for _, e := range q.Entries {
			if !isSendValue(e.Key, seen) || !isSendValue(e.Value, seen) {
				return false
			}
		}
		return true
	case SetValue:
		for _, x := range q.Items {
			if !isSendValue(x, seen) {
				return false
			}
		}
		return true
	case *Instance:
		if q.Class != nil && q.Class.Info != nil && q.Class.Info.Resource {
			return false
		}
		if seen == nil {
			seen = map[*Instance]bool{}
		}
		if seen[q] {
			return true
		}
		seen[q] = true
		for _, x := range q.Fields {
			if !isSendValue(x, seen) {
				return false
			}
		}
		return true
	default:
		return true
	}
}

type isolatedInvocation struct {
	cal   Value
	args  []Value
	child *Interpreter
}

// prepareIsolatedCall performs every read of the caller's mutable lexical
// environment before a worker goroutine is started.  This makes task creation
// the snapshot boundary required by the Saga memory model instead of letting
// a worker race with later top-level declarations in the parent interpreter.
func (i *Interpreter) prepareIsolatedCall(callable Value, args []Value) (*isolatedInvocation, error) {
	imemo := map[*Instance]*Instance{}
	var cal Value
	switch f := callable.(type) {
	case *Function:
		memo := map[*Env]*Env{}
		cal = &Function{Decl: f.Decl, Owner: f.Owner, Closure: snapshotEnv(f.Closure, memo, imemo)}
	case *BoundMethod:
		memo := map[*Env]*Env{}
		cal = &BoundMethod{Receiver: snapshotValue(f.Receiver, imemo).(*Instance), Function: &Function{Decl: f.Function.Decl, Owner: f.Function.Owner, Closure: snapshotEnv(f.Function.Closure, memo, imemo)}}
	case *NativeFunc:
		cal = f
	default:
		return nil, &SagaError{Code: "SAGA-R001", ID: "SAGA-R140", Message: "task callable is not Send"}
	}
	a := make([]Value, len(args))
	for j, x := range args {
		if !isSendValue(x, nil) {
			return nil, &SagaError{Code: "SAGA-R001", ID: "SAGA-R140", Message: "task argument is not Send"}
		}
		a[j] = snapshotValue(x, imemo)
	}
	childGlobal := snapshotEnv(i.Global, map[*Env]*Env{}, imemo)
	child := &Interpreter{Global: childGlobal, Env: childGlobal, Functions: i.Functions, Classes: i.Classes, Checker: i.Checker, Precision: i.Precision, output: i.output}
	return &isolatedInvocation{cal: cal, args: a, child: child}, nil
}

func (p *isolatedInvocation) run() (Value, error) {
	v, e := p.child.invokeDirect(p.cal, p.args, Token{})
	if e != nil {
		return nil, e
	}
	if !isSendValue(v, nil) {
		return nil, &SagaError{Code: "SAGA-R001", ID: "SAGA-R140", Message: "task result is not Send"}
	}
	return snapshotValue(v, map[*Instance]*Instance{}), nil
}

func (i *Interpreter) isolatedCall(callable Value, args []Value) (Value, error) {
	p, err := i.prepareIsolatedCall(callable, args)
	if err != nil {
		return nil, err
	}
	return p.run()
}

func validUTF8String(s string) bool { return utf8.ValidString(s) }
func sortValues(vals []Value) ([]Value, error) {
	out := append([]Value{}, vals...)
	sort.SliceStable(out, func(a, b int) bool {
		x, y := out[a], out[b]
		xn, xok := x.(Number)
		yn, yok := y.(Number)
		if xok && yok {
			return xn.R.Cmp(yn.R) < 0
		}
		xs, xok := x.(string)
		ys, yok := y.(string)
		if xok && yok {
			return xs < ys
		}
		return formatValue(x, false) < formatValue(y, false)
	})
	return out, nil
}

func (i *Interpreter) RunTest(d *TestDecl) error { return i.exec(d.Body) }
