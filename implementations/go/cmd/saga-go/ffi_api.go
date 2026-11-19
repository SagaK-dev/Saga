package main

import "fmt"

func (i *Interpreter) callFFI(name string, args []Value, t Token) (Value, error) {
	if name == "available" {
		if len(args) != 0 {
			return nil, i.rerr(t, "SAGA-R150", "ffi.available()")
		}
		return ffiAvailable(), nil
	}
	if i.UnsafeDepth == 0 {
		return nil, i.rerr(t, "SAGA-R188", "FFI calls require unsafe { ... }")
	}
	if err := i.requireHostUnsafe(); err != nil {
		return nil, i.rerr(t, "SAGA-R103", err.Error())
	}
	if v, handled, err := i.callFFIProfile2(name, args, t); handled {
		return v, err
	}
	if len(args) != 3 {
		return nil, i.rerr(t, "SAGA-R150", "ffi.call_i64/call_f64(library,symbol,args)")
	}
	lib, lok := args[0].(string)
	sym, sok := args[1].(string)
	vals, vok := args[2].([]Value)
	if !lok || !sok || !vok {
		return nil, i.rerr(t, "SAGA-R150", "library/symbol text and list args required")
	}
	switch name {
	case "call_i64":
		av := make([]int64, len(vals))
		for j, v := range vals {
			n, ok := v.(Number)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "i64 FFI arguments must be int")
			}
			x, ok := n.Int()
			if !ok || !x.IsInt64() {
				return nil, i.rerr(t, "SAGA-R150", "i64 FFI argument out of range")
			}
			av[j] = x.Int64()
		}
		r, e := ffiCallI64(lib, sym, av)
		if e != nil {
			return nil, i.rerr(t, "SAGA-R190", e.Error())
		}
		return numberFromInt64(r), nil
	case "call_f64":
		av := make([]float64, len(vals))
		for j, v := range vals {
			f, ok := v.(FloatValue)
			if !ok || f.Bits != 64 {
				return nil, i.rerr(t, "SAGA-R150", "f64 FFI arguments must be float64")
			}
			av[j] = f.V
		}
		r, e := ffiCallF64(lib, sym, av)
		if e != nil {
			return nil, i.rerr(t, "SAGA-R190", e.Error())
		}
		return FloatValue{V: r, Bits: 64}, nil
	}
	return nil, i.rerr(t, "SAGA-R123", "unknown ffi member: "+name)
}

func annotationStrings(d *FnDecl, name string) ([]string, bool) {
	for _, a := range d.Annotations {
		if a.Name != name {
			continue
		}
		out := []string{}
		for _, x := range a.Args {
			l, ok := x.(*Literal)
			if !ok {
				return nil, false
			}
			s, ok := l.Value.(string)
			if !ok {
				return nil, false
			}
			out = append(out, s)
		}
		return out, true
	}
	return nil, false
}
func (i *Interpreter) callExtern(d *FnDecl, args []Value, t Token) (Value, error) {
	if err := i.requireHostUnsafe(); err != nil {
		return nil, i.rerr(t, "SAGA-R103", err.Error())
	}
	if d.ExternABI != "C" {
		return nil, i.rerr(t, "SAGA-R190", "only extern \"C\" is supported by the current Expert FFI profile")
	}
	link, ok := annotationStrings(d, "link")
	if !ok || len(link) != 2 {
		return nil, i.rerr(t, "SAGA-R190", "extern C function requires @link(\"library\",\"symbol\")")
	}
	if len(args) > 4 {
		return nil, i.rerr(t, "SAGA-R190", "scalar C ABI profile supports at most 4 arguments")
	}
	if d.Return == nil {
		return nil, i.rerr(t, "SAGA-R190", "extern C function requires explicit return type")
	}
	ret := typeFromRef(*d.Return, map[string]bool{})
	if ret.Name == "int" || ret.Name == "int64" {
		av := make([]int64, len(args))
		for j, v := range args {
			n, ok := v.(Number)
			if !ok {
				return nil, i.rerr(t, "SAGA-R190", "extern int arguments required")
			}
			x, ok := n.Int()
			if !ok || !x.IsInt64() {
				return nil, i.rerr(t, "SAGA-R190", "extern int argument out of i64 range")
			}
			av[j] = x.Int64()
		}
		r, e := ffiCallI64(link[0], link[1], av)
		if e != nil {
			return nil, i.rerr(t, "SAGA-R190", e.Error())
		}
		return numberFromInt64(r), nil
	}
	if ret.Name == "float64" {
		av := make([]float64, len(args))
		for j, v := range args {
			f, ok := v.(FloatValue)
			if !ok || f.Bits != 64 {
				return nil, i.rerr(t, "SAGA-R190", "extern float64 arguments required")
			}
			av[j] = f.V
		}
		r, e := ffiCallF64(link[0], link[1], av)
		if e != nil {
			return nil, i.rerr(t, "SAGA-R190", e.Error())
		}
		return FloatValue{V: r, Bits: 64}, nil
	}
	return nil, i.rerr(t, "SAGA-R190", fmt.Sprintf("unsupported extern return type %s; scalar profile supports int/int64 and float64", ret))
}
