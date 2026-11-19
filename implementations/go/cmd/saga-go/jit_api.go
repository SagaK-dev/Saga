package main

import (
	"encoding/binary"
	"fmt"
	"math/big"
)

type JITFunctionValue struct {
	Handle uintptr
	Arity  int
	closed bool
}

func emitMovParam(out *[]byte, idx int) error {
	seq := [][]byte{{0x48, 0x89, 0xf8}, {0x48, 0x89, 0xf0}, {0x48, 0x89, 0xd0}, {0x48, 0x89, 0xc8}}
	if idx < 0 || idx >= len(seq) {
		return fmt.Errorf("JIT scalar profile supports at most four parameters")
	}
	*out = append(*out, seq[idx]...)
	return nil
}
func emitImm64(out *[]byte, v int64) {
	*out = append(*out, 0x48, 0xb8)
	var b [8]byte
	binary.LittleEndian.PutUint64(b[:], uint64(v))
	*out = append(*out, b[:]...)
}
func compileJITI64Expr(e Expr, params map[string]int, out *[]byte) error {
	switch q := e.(type) {
	case *Literal:
		n, ok := q.Value.(Number)
		if !ok {
			return fmt.Errorf("JIT i64 profile requires integer literals")
		}
		x, ok := n.Int()
		if !ok || !x.IsInt64() {
			return fmt.Errorf("JIT integer literal is outside i64")
		}
		emitImm64(out, x.Int64())
		return nil
	case *Variable:
		idx, ok := params[q.Name]
		if !ok {
			return fmt.Errorf("JIT function may only read its scalar parameters; capture %q is not supported", q.Name)
		}
		return emitMovParam(out, idx)
	case *Unary:
		if q.Op.Kind != MINUS {
			return fmt.Errorf("JIT scalar profile supports unary minus only")
		}
		if err := compileJITI64Expr(q.Right, params, out); err != nil {
			return err
		}
		*out = append(*out, 0x48, 0xf7, 0xd8)
		return nil
	case *Binary:
		if q.Op.Kind != PLUS && q.Op.Kind != MINUS && q.Op.Kind != STAR {
			return fmt.Errorf("JIT scalar profile supports +, - and *")
		}
		if err := compileJITI64Expr(q.Left, params, out); err != nil {
			return err
		}
		*out = append(*out, 0x50)
		if err := compileJITI64Expr(q.Right, params, out); err != nil {
			return err
		}
		*out = append(*out, 0x59)
		switch q.Op.Kind {
		case PLUS:
			*out = append(*out, 0x48, 0x01, 0xc8)
		case MINUS:
			*out = append(*out, 0x48, 0x29, 0xc1, 0x48, 0x89, 0xc8)
		case STAR:
			*out = append(*out, 0x48, 0x0f, 0xaf, 0xc1)
		}
		return nil
	default:
		return fmt.Errorf("expression is outside the Edition 2027 scalar JIT profile")
	}
}
func compileJITI64Function(f *Function) (*JITFunctionValue, error) {
	if f == nil || f.Decl == nil || f.Decl.ExprBody == nil {
		return nil, fmt.Errorf("JIT requires an expression-body Saga function")
	}
	if len(f.Decl.Params) > 4 {
		return nil, fmt.Errorf("JIT scalar profile supports at most four parameters")
	}
	params := map[string]int{}
	for j, p := range f.Decl.Params {
		if p.Type.Name != "int" && p.Type.Name != "int64" {
			return nil, fmt.Errorf("JIT i64 parameters must be int/int64")
		}
		params[p.Name] = j
	}
	if f.Decl.Return == nil || (f.Decl.Return.Name != "int" && f.Decl.Return.Name != "int64") {
		return nil, fmt.Errorf("JIT i64 return type must be int/int64")
	}
	code := []byte{}
	if err := compileJITI64Expr(f.Decl.ExprBody, params, &code); err != nil {
		return nil, err
	}
	code = append(code, 0xc3)
	h, err := jitAlloc(code)
	if err != nil {
		return nil, err
	}
	return &JITFunctionValue{Handle: h, Arity: len(f.Decl.Params)}, nil
}
func (i *Interpreter) callJIT(name string, args []Value, t Token) (Value, error) {
	if name == "available" {
		if len(args) != 0 {
			return nil, i.rerr(t, "SAGA-R150", "jit.available()")
		}
		return jitAvailable(), nil
	}
	if i.UnsafeDepth == 0 {
		return nil, i.rerr(t, "SAGA-R188", "JIT execution requires unsafe { ... }")
	}
	if err := i.requireHostUnsafe(); err != nil {
		return nil, i.rerr(t, "SAGA-R103", err.Error())
	}
	switch name {
	case "compile_i64":
		if len(args) != 1 {
			return nil, i.rerr(t, "SAGA-R150", "jit.compile_i64(function)")
		}
		f, ok := args[0].(*Function)
		if !ok {
			return nil, i.rerr(t, "SAGA-R150", "Saga function required")
		}
		j, err := compileJITI64Function(f)
		if err != nil {
			return ResultValue{OK: false, Value: ErrorValue{Kind: "jit", Message: err.Error()}}, nil
		}
		return ResultValue{OK: true, Value: j}, nil
	case "call_i64":
		if len(args) != 2 {
			return nil, i.rerr(t, "SAGA-R150", "jit.call_i64(compiled,args)")
		}
		j, ok := args[0].(*JITFunctionValue)
		if !ok || j.closed || j.Handle == 0 {
			return nil, i.rerr(t, "SAGA-R150", "open JIT function required")
		}
		vals, ok := args[1].([]Value)
		if !ok || len(vals) != j.Arity {
			return nil, i.rerr(t, "SAGA-R150", "JIT argument list has wrong arity")
		}
		av := make([]int64, len(vals))
		for k, v := range vals {
			n, ok := v.(Number)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "JIT args must be int")
			}
			x, ok := n.Int()
			if !ok || !x.IsInt64() {
				return nil, i.rerr(t, "SAGA-R150", "JIT arg outside i64")
			}
			av[k] = x.Int64()
		}
		r, err := jitInvoke(j.Handle, av)
		if err != nil {
			return nil, i.rerr(t, "SAGA-R191", err.Error())
		}
		return numberFromBigInt(big.NewInt(r)), nil
	case "close":
		if len(args) != 1 {
			return nil, i.rerr(t, "SAGA-R150", "jit.close(compiled)")
		}
		j, ok := args[0].(*JITFunctionValue)
		if !ok {
			return nil, i.rerr(t, "SAGA-R150", "JIT function required")
		}
		if !j.closed {
			jitRelease(j.Handle)
			j.Handle = 0
			j.closed = true
		}
		return nil, nil
	}
	return nil, i.rerr(t, "SAGA-R123", "unknown jit member: "+name)
}
