package main

import "strings"

type Type struct {
	Name   string
	Args   []Type
	Result *Type
}

func (t Type) String() string {
	if t.Name == "fn" {
		a := []string{}
		for _, x := range t.Args {
			a = append(a, x.String())
		}
		r := "unit"
		if t.Result != nil {
			r = t.Result.String()
		}
		return "fn(" + strings.Join(a, ", ") + ") -> " + r
	}
	if strings.HasPrefix(t.Name, "typector:") {
		return strings.TrimPrefix(t.Name, "typector:")
	}
	if t.Name == "typeapply" && len(t.Args) > 0 {
		a := []string{}
		for _, x := range t.Args[1:] {
			a = append(a, x.String())
		}
		return t.Args[0].String() + "[" + strings.Join(a, ", ") + "]"
	}
	if len(t.Args) > 0 {
		a := []string{}
		for _, x := range t.Args {
			a = append(a, x.String())
		}
		return t.Name + "[" + strings.Join(a, ", ") + "]"
	}
	return t.Name
}

var TInt = Type{Name: "int"}
var TInt8 = Type{Name: "int8"}
var TInt16 = Type{Name: "int16"}
var TInt32 = Type{Name: "int32"}
var TInt64 = Type{Name: "int64"}
var TUInt8 = Type{Name: "uint8"}
var TUInt16 = Type{Name: "uint16"}
var TUInt32 = Type{Name: "uint32"}
var TUInt64 = Type{Name: "uint64"}
var TDecimal = Type{Name: "decimal"}
var TRational = Type{Name: "rational"}
var TFloat32 = Type{Name: "float32"}
var TFloat64 = Type{Name: "float64"}
var TBool = Type{Name: "bool"}
var TText = Type{Name: "text"}
var TUnit = Type{Name: "unit"}
var TRange = Type{Name: "range"}
var TAny = Type{Name: "any"}
var TBytes = Type{Name: "bytes"}
var TError = Type{Name: "error"}
var TClass = Type{Name: "class"}
var TBuiltin = Type{Name: "builtin"}

func listT(x Type) Type                   { return Type{Name: "list", Args: []Type{x}} }
func mapT(k, v Type) Type                 { return Type{Name: "map", Args: []Type{k, v}} }
func setT(x Type) Type                    { return Type{Name: "set", Args: []Type{x}} }
func optionT(x Type) Type                 { return Type{Name: "option", Args: []Type{x}} }
func resultT(okT, errT Type) Type         { return Type{Name: "result", Args: []Type{okT, errT}} }
func futureT(x Type) Type                 { return Type{Name: "future", Args: []Type{x}} }
func channelT(x Type) Type                { return Type{Name: "channel", Args: []Type{x}} }
func actorT(in, out Type) Type            { return Type{Name: "actor", Args: []Type{in, out}} }
func objectT(n string, args ...Type) Type { return Type{Name: "object:" + n, Args: args} }
func fnT(a []Type, r Type) Type           { rr := r; return Type{Name: "fn", Args: a, Result: &rr} }
func typeVar(n string) Type               { return Type{Name: "$" + n} }
func isTypeVar(t Type) bool               { return strings.HasPrefix(t.Name, "$") }
func typeCtor(n string) Type              { return Type{Name: "typector:" + n} }
func isTypeCtor(t Type) bool              { return strings.HasPrefix(t.Name, "typector:") }
func typeApply(ctor Type, args ...Type) Type {
	return Type{Name: "typeapply", Args: append([]Type{ctor}, args...)}
}
func isExactNumeric(t Type) bool {
	switch t.Name {
	case "int", "int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64", "decimal", "rational":
		return true
	}
	return false
}
func isFloat(t Type) bool   { return t.Name == "float32" || t.Name == "float64" }
func isNumeric(t Type) bool { return isExactNumeric(t) || isFloat(t) }
func commonNumeric(a, b Type) Type {
	if a.Name == "float64" || b.Name == "float64" {
		return TFloat64
	}
	if a.Name == "float32" || b.Name == "float32" {
		return TFloat32
	}
	if a.Name == "decimal" || b.Name == "decimal" {
		return TDecimal
	}
	if a.Name == "rational" || b.Name == "rational" {
		return TRational
	}
	// Fixed-width integers are boundary/storage types. Arithmetic promotes to
	// arbitrary-precision int so overflow is never silent; narrow explicitly.
	return TInt
}
func sameType(a, b Type) bool {
	if a.Name != b.Name || len(a.Args) != len(b.Args) {
		return false
	}
	for i := range a.Args {
		if !sameType(a.Args[i], b.Args[i]) {
			return false
		}
	}
	if (a.Result == nil) != (b.Result == nil) {
		return false
	}
	return a.Result == nil || sameType(*a.Result, *b.Result)
}
func substitute(t Type, m map[string]Type) Type {
	if isTypeVar(t) {
		if x, ok := m[strings.TrimPrefix(t.Name, "$")]; ok {
			return x
		}
		return t
	}
	if t.Name == "typeapply" && len(t.Args) > 0 {
		ctor := substitute(t.Args[0], m)
		applied := []Type{}
		for _, a := range t.Args[1:] {
			applied = append(applied, substitute(a, m))
		}
		if isTypeCtor(ctor) {
			return Type{Name: strings.TrimPrefix(ctor.Name, "typector:"), Args: applied}
		}
		return typeApply(ctor, applied...)
	}
	r := Type{Name: t.Name}
	for _, a := range t.Args {
		r.Args = append(r.Args, substitute(a, m))
	}
	if t.Result != nil {
		x := substitute(*t.Result, m)
		r.Result = &x
	}
	return r
}
func typeFromRef(r TypeRef, vars map[string]bool) Type {
	n := r.Name
	aliases := map[string]Type{"int": TInt, "Int": TInt, "integer": TInt, "int8": TInt8, "int16": TInt16, "int32": TInt32, "int64": TInt64, "uint8": TUInt8, "uint16": TUInt16, "uint32": TUInt32, "uint64": TUInt64, "decimal": TDecimal, "Decimal": TDecimal, "number": TDecimal, "rational": TRational, "Rational": TRational, "fraction": TRational, "float32": TFloat32, "Float32": TFloat32, "float64": TFloat64, "Float64": TFloat64, "bool": TBool, "Bool": TBool, "boolean": TBool, "text": TText, "Text": TText, "string": TText, "String": TText, "unit": TUnit, "Unit": TUnit, "range": TRange, "Range": TRange, "any": TAny, "Any": TAny, "bytes": TBytes, "Bytes": TBytes, "error": TError, "Error": TError}
	args := []Type{}
	for _, a := range r.Args {
		args = append(args, typeFromRef(a, vars))
	}
	if vars[n] {
		if len(args) > 0 {
			return typeApply(typeVar(n), args...)
		}
		return typeVar(n)
	}
	if dot := strings.IndexByte(n, '.'); dot > 0 {
		prefix, assoc := n[:dot], n[dot+1:]
		if vars[prefix] && assoc != "" {
			return Type{Name: "assoc:$" + prefix + "." + assoc}
		}
	}
	if a, ok := aliases[n]; ok {
		return a
	}
	switch strings.ToLower(n) {
	case "list":
		if len(args) == 1 {
			return listT(args[0])
		}
	case "map":
		if len(args) == 2 {
			return mapT(args[0], args[1])
		}
	case "set":
		if len(args) == 1 {
			return setT(args[0])
		}
	case "option":
		if len(args) == 1 {
			return optionT(args[0])
		}
	case "result":
		if len(args) == 2 {
			return resultT(args[0], args[1])
		}
	case "future":
		if len(args) == 1 {
			return futureT(args[0])
		}
	case "channel":
		if len(args) == 1 {
			return channelT(args[0])
		}
	case "actor":
		if len(args) == 2 {
			return actorT(args[0], args[1])
		}
	case "fn":
		if len(args) >= 1 {
			return fnT(args[:len(args)-1], args[len(args)-1])
		}
	}
	return objectT(n, args...)
}
func unify(pattern, actual Type, m map[string]Type) bool {
	if pattern.Name == "typeapply" && len(pattern.Args) > 0 {
		ctor := pattern.Args[0]
		applied := pattern.Args[1:]
		if !isTypeVar(ctor) || actual.Name == "fn" || len(applied) != len(actual.Args) {
			return false
		}
		name := strings.TrimPrefix(ctor.Name, "$")
		candidate := typeCtor(actual.Name)
		if existing, ok := m[name]; ok {
			if !sameType(existing, candidate) {
				return false
			}
		} else {
			m[name] = candidate
		}
		for idx := range applied {
			if !unify(applied[idx], actual.Args[idx], m) {
				return false
			}
		}
		return true
	}
	if isTypeVar(pattern) {
		n := strings.TrimPrefix(pattern.Name, "$")
		if x, ok := m[n]; ok {
			return sameType(x, actual)
		}
		m[n] = actual
		return true
	}
	if pattern.Name == "any" || actual.Name == "any" {
		return true
	}
	if pattern.Name != actual.Name || len(pattern.Args) != len(actual.Args) {
		return false
	}
	for i := range pattern.Args {
		if !unify(pattern.Args[i], actual.Args[i], m) {
			return false
		}
	}
	if (pattern.Result == nil) != (actual.Result == nil) {
		return false
	}
	if pattern.Result != nil && !unify(*pattern.Result, *actual.Result, m) {
		return false
	}
	return true
}

func objectTypeName(t Type) string {
	if strings.HasPrefix(t.Name, "object:") {
		return strings.TrimPrefix(t.Name, "object:")
	}
	return ""
}

func typeParamMap(params []string, args []Type) map[string]Type {
	m := map[string]Type{}
	for i, n := range params {
		if i < len(args) {
			m[n] = args[i]
		}
	}
	return m
}
