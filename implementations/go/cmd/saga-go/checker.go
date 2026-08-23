package main

import (
	"fmt"
	"strings"
)

type VarInfo struct {
	Typ     Type
	Mutable bool
	Moved   bool
}
type FuncInfo struct {
	Params     []Type
	Ret        Type
	HasRet     bool
	TypeParams []string
	Decl       *FnDecl
	Owner      string
	Abstract   bool
}
type FieldInfo struct {
	Typ              Type
	Mutable, Private bool
	Owner            string
}
type ClassInfo struct {
	Name                string
	Decl                *ClassDecl
	TypeParams          []string
	Base                Type
	Interfaces          []Type
	OwnFields           map[string]FieldInfo
	Fields              map[string]FieldInfo
	OwnFieldOrder       []string
	FieldOrder          []string
	OwnMethods          map[string]FuncInfo
	Methods             map[string]FuncInfo
	Abstract, Interface bool
	Record, Resource    bool
}

type SourceModuleInfo struct {
	Name      string
	Members   map[string]Type
	Functions map[string]FuncInfo
}

type Checker struct {
	Scopes             []map[string]VarInfo
	Functions          map[string]FuncInfo
	Classes            map[string]*ClassInfo
	CurrentRet         *Type
	CurrentFn          *FnDecl
	CurrentClass       string
	LoopDepth          int
	LocalFunctions     map[*FnDecl]FuncInfo
	Enums              map[string]map[string]bool
	EnumPayloads       map[string]map[string][]Type
	EnumTypeParams     map[string][]string
	SourceModules      map[string]SourceModuleInfo
	UnsafeDepth        int
	CurrentConstraints map[string][]Type
}

func NewChecker() *Checker {
	c := &Checker{Scopes: []map[string]VarInfo{{}}, Functions: map[string]FuncInfo{}, Classes: map[string]*ClassInfo{}, LocalFunctions: map[*FnDecl]FuncInfo{}, Enums: map[string]map[string]bool{}, EnumPayloads: map[string]map[string][]Type{}, EnumTypeParams: map[string][]string{}, SourceModules: map[string]SourceModuleInfo{}, CurrentConstraints: map[string][]Type{}}
	c.Enums["Option"] = map[string]bool{"Some": true, "None": true}
	c.EnumPayloads["Option"] = map[string][]Type{"Some": {typeVar("T")}, "None": {}}
	c.EnumTypeParams["Option"] = []string{"T"}
	c.Enums["Result"] = map[string]bool{"Ok": true, "Err": true}
	c.EnumPayloads["Result"] = map[string][]Type{"Ok": {typeVar("T")}, "Err": {typeVar("E")}}
	c.EnumTypeParams["Result"] = []string{"T", "E"}
	c.Scopes[0]["Option"] = VarInfo{Typ: Type{Name: "enumtype:Option"}}
	c.Scopes[0]["Result"] = VarInfo{Typ: Type{Name: "enumtype:Result"}}
	return c
}

func sagaEditDistance(a, b string) int {
	ar, br := []rune(a), []rune(b)
	prev := make([]int, len(br)+1)
	for j := range prev {
		prev[j] = j
	}
	for i, ra := range ar {
		cur := make([]int, len(br)+1)
		cur[0] = i + 1
		for j, rb := range br {
			cost := 0
			if ra != rb {
				cost = 1
			}
			ins := cur[j] + 1
			del := prev[j+1] + 1
			sub := prev[j] + cost
			cur[j+1] = ins
			if del < cur[j+1] {
				cur[j+1] = del
			}
			if sub < cur[j+1] {
				cur[j+1] = sub
			}
		}
		prev = cur
	}
	return prev[len(br)]
}

func nearestSagaName(want string, candidates map[string]bool) string {
	best, bestD := "", 1<<30
	limit := 2
	if len([]rune(want)) >= 8 {
		limit = 3
	}
	for candidate := range candidates {
		if candidate == want {
			continue
		}
		d := sagaEditDistance(want, candidate)
		if d < bestD || (d == bestD && candidate < best) {
			best, bestD = candidate, d
		}
	}
	if bestD <= limit {
		return best
	}
	return ""
}

func (c *Checker) nearestVisibleName(want string) string {
	candidates := map[string]bool{}
	for _, scope := range c.Scopes {
		for name := range scope {
			candidates[name] = true
		}
	}
	for name := range c.Functions {
		candidates[name] = true
	}
	for name := range c.Classes {
		candidates[name] = true
	}
	for name := range coreBuiltins {
		candidates[name] = true
	}
	return nearestSagaName(want, candidates)
}

func (c *Checker) validateAnnotations(items []Annotation, tok Token) error {
	seen := map[string]bool{}
	for _, a := range items {
		if seen[a.Name] {
			return c.err(a.Tok, "SAGA-T108", "duplicate annotation "+a.Name)
		}
		seen[a.Name] = true
		if a.Name == "derive" {
			if len(a.Args) == 0 {
				return c.err(a.Tok, "SAGA-T174", "@derive requires one or more string capabilities")
			}
			for _, raw := range a.Args {
				lit, ok := raw.(*Literal)
				if !ok {
					return c.err(a.Tok, "SAGA-T174", "@derive arguments must be strings")
				}
				name, ok := lit.Value.(string)
				if !ok || (name != "Equal" && name != "Hash" && name != "Debug") {
					return c.err(a.Tok, "SAGA-T174", "unknown @derive capability; expected Equal, Hash, or Debug")
				}
			}
		}
	}
	return nil
}
func hasDupStrings(items []string) bool {
	seen := map[string]bool{}
	for _, v := range items {
		if seen[v] {
			return true
		}
		seen[v] = true
	}
	return false
}
func isHashableType(t Type) bool {
	if t.Name == "any" {
		return true
	}
	if isNumeric(t) || t.Name == "bool" || t.Name == "text" || t.Name == "bytes" {
		return true
	}
	if t.Name == "option" && len(t.Args) == 1 {
		return isHashableType(t.Args[0])
	}
	return false
}

func (c *Checker) isHashableTypeDeep(t Type) bool {
	if isHashableType(t) {
		return true
	}
	if ci := c.classFor(t); ci != nil && classDerives(ci, "Hash") {
		for _, f := range ci.Fields {
			if !c.isHashableTypeDeep(f.Typ) {
				return false
			}
		}
		return true
	}
	return false
}

var coreBuiltins = map[string]bool{"print": true, "len": true, "text": true, "decimal": true, "float32": true, "float64": true, "ratio": true, "abs": true, "sqrt": true, "round": true, "min": true, "max": true, "sum": true, "mean": true, "append": true, "prepend": true, "get": true, "contains": true, "assert": true, "precision": true, "floor": true, "ceil": true, "slice": true, "reverse": true, "sort": true, "unique": true, "transform": true, "filter": true, "reduce": true, "find": true, "any": true, "all": true, "split": true, "join": true, "trim": true, "upper": true, "lower": true, "replace": true, "starts_with": true, "ends_with": true, "find_text": true, "substring": true, "map_of": true, "map_get": true, "map_put": true, "map_remove": true, "map_keys": true, "map_values": true, "map_contains": true, "set_of": true, "set_add": true, "set_remove": true, "set_contains": true, "set_union": true, "set_intersection": true, "int": true, "int8": true, "int16": true, "int32": true, "int64": true, "uint8": true, "uint16": true, "uint32": true, "uint64": true, "repeat": true, "set_at": true, "some": true, "none": true, "is_some": true, "is_none": true, "unwrap": true, "unwrap_or": true, "ok": true, "err": true, "is_ok": true, "is_err": true, "unwrap_ok": true, "unwrap_err": true, "unwrap_result_or": true, "Option": true, "Result": true}

func (c *Checker) Check(stmts []Stmt) error {
	// Source-module interfaces are name-resolution inputs. Load their public
	// surfaces before local class/function shells so a local declaration may use
	// `m.Type` in a base relation or signature without re-checking m's body.
	for _, s := range stmts {
		if x, ok := s.(*SourceModuleStmt); ok {
			if e := c.loadSourceModule(x); e != nil {
				return e
			}
		}
	}
	for _, s := range stmts {
		switch x := s.(type) {
		case *EnumDecl:
			if e := c.declareEnum(x); e != nil {
				return e
			}
		case *ClassDecl:
			if e := c.declareClassShell(x); e != nil {
				return e
			}
		case *FnDecl:
			if e := c.declareFn(x, ""); e != nil {
				return e
			}
		}
	}
	for _, ci := range c.Classes {
		if ci.Decl != nil && len(ci.OwnFields) == 0 && len(ci.OwnMethods) == 0 {
			if e := c.declareMembers(ci); e != nil {
				return e
			}
		}
	}
	if e := c.resolveInheritance(); e != nil {
		return e
	}
	c.refreshSourceModuleConstructors()
	if e := c.validateContracts(); e != nil {
		return e
	}
	for _, s := range stmts {
		if e := c.checkStmt(s); e != nil {
			return e
		}
	}
	hasModule := false
	for _, s := range stmts {
		if _, ok := s.(*ModuleDecl); ok {
			hasModule = true
			break
		}
	}
	if hasModule {
		if e := c.validateModulePublicSurface(stmts); e != nil {
			return e
		}
	}
	return nil
}
func (c *Checker) err(t Token, id, msg string) error { return diag("SAGA-T001", id, msg, t) }
func (c *Checker) declareEnum(d *EnumDecl) error {
	if coreBuiltins[d.Name] || c.Classes[d.Name] != nil || c.Enums[d.Name] != nil {
		return c.err(d.Tok, "SAGA-T108", "duplicate enum name "+d.Name)
	}
	if _, ok := c.Functions[d.Name]; ok {
		return c.err(d.Tok, "SAGA-T108", "duplicate name "+d.Name)
	}
	if hasDupStrings(d.TypeParams) {
		return c.err(d.Tok, "SAGA-T108", "duplicate enum type parameter")
	}
	vars := map[string]bool{}
	for _, name := range d.TypeParams {
		vars[name] = true
	}
	variants := map[string]bool{}
	payloads := map[string][]Type{}
	for _, v := range d.Variants {
		if variants[v.Name] {
			return c.err(v.Tok, "SAGA-T108", "duplicate enum variant "+v.Name)
		}
		variants[v.Name] = true
		for _, r := range v.Payload {
			payloads[v.Name] = append(payloads[v.Name], typeFromRef(r, vars))
		}
	}
	c.Enums[d.Name] = variants
	c.EnumPayloads[d.Name] = payloads
	c.EnumTypeParams[d.Name] = append([]string{}, d.TypeParams...)
	return c.define(d.Name, VarInfo{Typ: Type{Name: "enumtype:" + d.Name}}, d.Tok)
}
func (c *Checker) declareClassShell(d *ClassDecl) error {
	if e := c.validateAnnotations(d.Annotations, d.Tok); e != nil {
		return e
	}
	if hasDupStrings(d.TypeParams) {
		return c.err(d.Tok, "SAGA-T108", "duplicate class type parameter")
	}
	if coreBuiltins[d.Name] || c.Classes[d.Name] != nil || c.Enums[d.Name] != nil {
		return c.err(d.Tok, "SAGA-T108", "duplicate class name "+d.Name)
	}
	if _, ok := c.Functions[d.Name]; ok {
		return c.err(d.Tok, "SAGA-T108", "duplicate name "+d.Name)
	}
	vars := map[string]bool{}
	for _, n := range d.TypeParams {
		vars[n] = true
	}
	base := Type{}
	if d.Base != nil {
		base = typeFromRef(*d.Base, vars)
	}
	interfaces := []Type{}
	for _, r := range d.Interfaces {
		interfaces = append(interfaces, typeFromRef(r, vars))
	}
	c.Classes[d.Name] = &ClassInfo{Name: d.Name, Decl: d, TypeParams: d.TypeParams, Base: base, Interfaces: interfaces, OwnFields: map[string]FieldInfo{}, Fields: map[string]FieldInfo{}, OwnFieldOrder: []string{}, FieldOrder: []string{}, OwnMethods: map[string]FuncInfo{}, Methods: map[string]FuncInfo{}, Abstract: d.Abstract, Interface: d.Interface, Record: d.Record, Resource: d.Resource}
	return nil
}

var comptimePureBuiltins = map[string]bool{
	"len": true, "text": true, "decimal": true, "float32": true, "float64": true, "ratio": true,
	"abs": true, "sqrt": true, "round": true, "min": true, "max": true, "sum": true, "mean": true,
	"floor": true, "ceil": true, "slice": true, "reverse": true, "sort": true, "unique": true, "int": true,
	"split": true, "join": true, "trim": true, "upper": true, "lower": true, "replace": true,
	"starts_with": true, "ends_with": true, "find_text": true, "substring": true,
}

func isCompileTimeExpr(e Expr) bool {
	switch x := e.(type) {
	case *Literal:
		return true
	case *Unary:
		return isCompileTimeExpr(x.Right)
	case *Binary:
		return isCompileTimeExpr(x.Left) && isCompileTimeExpr(x.Right)
	case *RangeExpr:
		return isCompileTimeExpr(x.Start) && isCompileTimeExpr(x.End)
	case *ListExpr:
		for _, q := range x.Items {
			if !isCompileTimeExpr(q) {
				return false
			}
		}
		return true
	case *InterpolatedString:
		for _, q := range x.Exprs {
			if !isCompileTimeExpr(q) {
				return false
			}
		}
		return true
	case *Index:
		return isCompileTimeExpr(x.Target) && isCompileTimeExpr(x.Index)
	case *Call:
		q, ok := x.Callee.(*Variable)
		if !ok || !comptimePureBuiltins[q.Name] {
			return false
		}
		for _, a := range x.Args {
			if !isCompileTimeExpr(a) {
				return false
			}
		}
		return true
	default:
		return false
	}
}

func validateComptimeBody(d *FnDecl) error {
	if !d.Comptime {
		return nil
	}
	if d.Async || d.ExternABI != "" || d.Abstract {
		return diag("SAGA-T001", "SAGA-T179", "comptime function cannot be async, extern, or abstract", d.Tok)
	}
	if d.ExprBody == nil || d.Body != nil {
		return diag("SAGA-T001", "SAGA-T179", "comptime function must use an expression body (`= expression`)", d.Tok)
	}
	// Parameters are permitted in a comptime body. The stricter call-site check
	// guarantees that they are replaced by compile-time values before execution.
	return nil
}

func (c *Checker) declareFn(d *FnDecl, owner string) error {
	if e := c.validateAnnotations(d.Annotations, d.Tok); e != nil {
		return e
	}
	if e := validateComptimeBody(d); e != nil {
		return e
	}
	if hasDupStrings(d.TypeParams) {
		return c.err(d.Tok, "SAGA-T108", "duplicate function type parameter")
	}
	seenParams := map[string]bool{}
	for _, p := range d.Params {
		if seenParams[p.Name] {
			return c.err(p.Tok, "SAGA-T108", "duplicate parameter "+p.Name)
		}
		seenParams[p.Name] = true
	}
	if owner == "" {
		if coreBuiltins[d.Name] || c.Classes[d.Name] != nil || c.Enums[d.Name] != nil {
			return c.err(d.Tok, "SAGA-T108", "duplicate function name "+d.Name)
		}
		if _, ok := c.Functions[d.Name]; ok {
			return c.err(d.Tok, "SAGA-T108", "duplicate function "+d.Name)
		}
	}
	vars := map[string]bool{}
	for _, n := range d.TypeParams {
		vars[n] = true
	}
	if owner != "" {
		for _, n := range c.Classes[owner].TypeParams {
			vars[n] = true
		}
	}
	params := []Type{}
	for _, p := range d.Params {
		params = append(params, typeFromRef(p.Type, vars))
	}
	ret := TUnit
	has := d.Return != nil
	if d.Return != nil {
		ret = typeFromRef(*d.Return, vars)
	} else if d.ExprBody != nil {
		ret = TAny
	}
	if d.Abstract && !has {
		return c.err(d.Tok, "SAGA-T103", "abstract methods require an explicit return type")
	}
	fi := FuncInfo{Params: params, Ret: ret, HasRet: has, TypeParams: d.TypeParams, Decl: d, Owner: owner, Abstract: d.Abstract}
	if owner == "" {
		c.Functions[d.Name] = fi
	}
	return nil
}
func memberTypeFromRef(r TypeRef, vars map[string]bool, assoc map[string]bool) Type {
	copy := r
	if assoc[r.Name] {
		copy.Name = "Self." + r.Name
	}
	copy.Args = nil
	for _, a := range r.Args {
		// Preserve nesting while qualifying any associated names.
		qualified := a
		if assoc[a.Name] {
			qualified.Name = "Self." + a.Name
		}
		copy.Args = append(copy.Args, qualified)
	}
	vars2 := map[string]bool{}
	for k, v := range vars {
		vars2[k] = v
	}
	vars2["Self"] = true
	return typeFromRef(copy, vars2)
}

func (c *Checker) resolveSelfAssociatedForClass(t Type, ci *ClassInfo) (Type, error) {
	if strings.HasPrefix(t.Name, "assoc:$Self.") {
		name := strings.TrimPrefix(t.Name, "assoc:$Self.")
		ref := ci.Decl.AssociatedTypes[name]
		if ref == nil {
			return TAny, c.err(ci.Decl.Tok, "SAGA-T171", "associated type required: "+name)
		}
		vars := map[string]bool{}
		for _, n := range ci.TypeParams {
			vars[n] = true
		}
		return typeFromRef(*ref, vars), nil
	}
	r := Type{Name: t.Name}
	for _, a := range t.Args {
		x, e := c.resolveSelfAssociatedForClass(a, ci)
		if e != nil {
			return TAny, e
		}
		r.Args = append(r.Args, x)
	}
	if t.Result != nil {
		x, e := c.resolveSelfAssociatedForClass(*t.Result, ci)
		if e != nil {
			return TAny, e
		}
		r.Result = &x
	}
	return r, nil
}

func (c *Checker) declareMembers(ci *ClassInfo) error {
	vars := map[string]bool{}
	for _, n := range ci.TypeParams {
		vars[n] = true
	}
	assoc := map[string]bool{}
	for n := range ci.Decl.AssociatedTypes {
		assoc[n] = true
	}
	for _, f := range ci.Decl.Fields {
		if _, ok := ci.OwnFields[f.Name]; ok {
			return c.err(f.Tok, "SAGA-T108", "duplicate field "+f.Name)
		}
		ci.OwnFields[f.Name] = FieldInfo{typeFromRef(f.Type, vars), f.Mutable, f.Private, ci.Name}
		ci.OwnFieldOrder = append(ci.OwnFieldOrder, f.Name)
	}
	for _, m := range ci.Decl.Methods {
		if _, ok := ci.OwnMethods[m.Name]; ok {
			return c.err(m.Tok, "SAGA-T108", "duplicate method "+m.Name)
		}
		if e := c.declareFn(m, ci.Name); e != nil {
			return e
		}
		vars2 := map[string]bool{}
		for _, n := range ci.TypeParams {
			vars2[n] = true
		}
		for _, n := range m.TypeParams {
			vars2[n] = true
		}
		ps := []Type{}
		for _, p := range m.Params {
			ps = append(ps, memberTypeFromRef(p.Type, vars2, assoc))
		}
		ret := TUnit
		has := m.Return != nil
		if m.Return != nil {
			ret = memberTypeFromRef(*m.Return, vars2, assoc)
		} else if m.ExprBody != nil {
			ret = TAny
		}
		ci.OwnMethods[m.Name] = FuncInfo{ps, ret, has, m.TypeParams, m, ci.Name, m.Abstract}
	}
	return nil
}
func specializeFieldInfo(v FieldInfo, m map[string]Type) FieldInfo {
	v.Typ = substitute(v.Typ, m)
	return v
}
func specializeFuncInfo(v FuncInfo, m map[string]Type) FuncInfo {
	ps := make([]Type, len(v.Params))
	for i, p := range v.Params {
		ps[i] = substitute(p, m)
	}
	v.Params = ps
	v.Ret = substitute(v.Ret, m)
	return v
}
func (c *Checker) relationTarget(rel Type, tok Token, wantInterface bool) (*ClassInfo, map[string]Type, error) {
	name := objectTypeName(rel)
	if name == "" {
		return nil, nil, c.err(tok, "SAGA-T103", "inheritance target must be a class or interface type")
	}
	target := c.Classes[name]
	if target == nil {
		return nil, nil, c.err(tok, "SAGA-T102", "inheritance target not found: "+name)
	}
	if len(rel.Args) != len(target.TypeParams) {
		return nil, nil, c.err(tok, "SAGA-T103", fmt.Sprintf("%s expects %d type arguments, got %d", name, len(target.TypeParams), len(rel.Args)))
	}
	if wantInterface && !target.Interface {
		return nil, nil, c.err(tok, "SAGA-T103", name+" is not an interface")
	}
	if !wantInterface && target.Interface {
		return nil, nil, c.err(tok, "SAGA-T103", "interface cannot be used with extends")
	}
	return target, typeParamMap(target.TypeParams, rel.Args), nil
}
func (c *Checker) resolveInheritance() error {
	vis := map[string]int{}
	var rec func(string) error
	rec = func(n string) error {
		if vis[n] == 2 {
			return nil
		}
		if vis[n] == 1 {
			return c.err(c.Classes[n].Decl.Tok, "SAGA-T103", "cyclic class inheritance")
		}
		vis[n] = 1
		ci := c.Classes[n]
		fields := map[string]FieldInfo{}
		methods := map[string]FuncInfo{}
		order := []string{}
		if ci.Base.Name != "" {
			base, mapping, e := c.relationTarget(ci.Base, ci.Decl.Tok, false)
			if e != nil {
				return e
			}
			if e = rec(base.Name); e != nil {
				return e
			}
			for k, v := range base.Fields {
				fields[k] = specializeFieldInfo(v, mapping)
			}
			order = append(order, base.FieldOrder...)
			for k, v := range base.Methods {
				methods[k] = specializeFuncInfo(v, mapping)
			}
		}
		for _, k := range ci.OwnFieldOrder {
			v := ci.OwnFields[k]
			if _, ok := fields[k]; ok {
				return c.err(ci.Decl.Tok, "SAGA-T108", "inherited field cannot be redeclared: "+k)
			}
			fields[k] = v
			order = append(order, k)
		}
		for k, v := range ci.OwnMethods {
			if parent, ok := methods[k]; ok {
				if !v.Decl.Override {
					return c.err(v.Decl.Tok, "SAGA-T110", "override keyword required for "+k)
				}
				if e := c.overrideCompatible(parent, v, v.Decl.Tok); e != nil {
					return e
				}
			} else if v.Decl.Override {
				matched := false
				for _, rel := range ci.Interfaces {
					iface, mapping, e := c.relationTarget(rel, ci.Decl.Tok, true)
					if e != nil {
						return e
					}
					if req, ok := iface.OwnMethods[k]; ok {
						matched = true
						req = specializeFuncInfo(req, mapping)
						for j, p := range req.Params {
							x, er := c.resolveSelfAssociatedForClass(p, ci)
							if er != nil {
								return er
							}
							req.Params[j] = x
						}
						if req.HasRet {
							x, er := c.resolveSelfAssociatedForClass(req.Ret, ci)
							if er != nil {
								return er
							}
							req.Ret = x
						}
						if e := c.overrideCompatible(req, v, v.Decl.Tok); e != nil {
							return e
						}
					}
				}
				if !matched {
					return c.err(v.Decl.Tok, "SAGA-T110", "override has no matching base/interface method: "+k)
				}
			}
			methods[k] = v
		}
		ci.Fields = fields
		ci.FieldOrder = order
		ci.Methods = methods
		vis[n] = 2
		return nil
	}
	for n := range c.Classes {
		if e := rec(n); e != nil {
			return e
		}
	}
	return nil
}
func (c *Checker) overrideCompatible(a, b FuncInfo, t Token) error {
	if len(a.TypeParams) != len(b.TypeParams) {
		return c.err(t, "SAGA-T103", "override generic method type-parameter count differs")
	}
	alpha := map[string]Type{}
	for idx, childName := range b.TypeParams {
		alpha[childName] = typeVar(a.TypeParams[idx])
	}
	params := make([]Type, 0, len(b.Params))
	for _, param := range b.Params {
		params = append(params, substitute(param, alpha))
	}
	ret := b.Ret
	if b.HasRet {
		ret = substitute(b.Ret, alpha)
	}
	if len(a.Params) != len(params) {
		return c.err(t, "SAGA-T103", "override parameter count differs")
	}
	for i := range a.Params {
		if !sameType(a.Params[i], params[i]) {
			return c.err(t, "SAGA-T103", "override parameter type differs")
		}
	}
	if a.HasRet && b.HasRet && !c.assignable(a.Ret, ret) {
		return c.err(t, "SAGA-T103", fmt.Sprintf("override return type is incompatible: contract %s, implementation %s", a.Ret, ret))
	}
	return nil
}
func (c *Checker) validateContracts() error {
	for _, ci := range c.Classes {
		for _, rel := range ci.Interfaces {
			iface, mapping, e := c.relationTarget(rel, ci.Decl.Tok, true)
			if e != nil {
				return e
			}
			for n, req := range iface.OwnMethods {
				req = specializeFuncInfo(req, mapping)
				for j, p := range req.Params {
					x, er := c.resolveSelfAssociatedForClass(p, ci)
					if er != nil {
						return er
					}
					req.Params[j] = x
				}
				if req.HasRet {
					x, er := c.resolveSelfAssociatedForClass(req.Ret, ci)
					if er != nil {
						return er
					}
					req.Ret = x
				}
				act, ok := ci.Methods[n]
				if !ok {
					return c.err(ci.Decl.Tok, "SAGA-T106", "interface method required: "+n)
				}
				if act.Owner == ci.Name && !act.Decl.Override {
					return c.err(act.Decl.Tok, "SAGA-T110", "override required to implement interface method "+n)
				}
				if e := c.overrideCompatible(req, act, act.Decl.Tok); e != nil {
					return e
				}
			}
			for _, assoc := range iface.Decl.RequiredAssocTypes {
				if ci.Decl.AssociatedTypes == nil || ci.Decl.AssociatedTypes[assoc] == nil {
					return c.err(ci.Decl.Tok, "SAGA-T171", "associated type required by "+iface.Name+": "+assoc)
				}
			}
		}
		if !ci.Abstract && !ci.Interface {
			for n, m := range ci.Methods {
				if m.Abstract {
					return c.err(ci.Decl.Tok, "SAGA-T106", "abstract method must be implemented: "+n)
				}
			}
		}
	}
	return nil
}

func (c *Checker) satisfiesConstraint(actual Type, constraint Type) bool {
	switch constraint.Name {
	case "object:Numeric", "object:Number":
		return isNumeric(actual)
	case "object:ExactNumeric":
		return isExactNumeric(actual)
	case "object:Float":
		return isFloat(actual)
	case "object:Comparable":
		return isNumeric(actual) || actual.Name == "text" || actual.Name == "bool"
	case "object:Hashable":
		return c.isHashableTypeDeep(actual)
	case "object:Send":
		return c.isSendType(actual)
	}
	if constraint.Name == actual.Name || sameType(constraint, actual) {
		return true
	}
	return c.classSubtypeType(actual, constraint)
}

func (c *Checker) isSendType(t Type) bool {
	if isNumeric(t) || t.Name == "bool" || t.Name == "text" || t.Name == "bytes" || t.Name == "unit" || t.Name == "error" {
		return true
	}
	switch t.Name {
	case "option", "list", "set", "future":
		for _, a := range t.Args {
			if !c.isSendType(a) {
				return false
			}
		}
		return true
	case "result", "map":
		for _, a := range t.Args {
			if !c.isSendType(a) {
				return false
			}
		}
		return true
	}
	if ci := c.classFor(t); ci != nil {
		if ci.Resource {
			return false
		}
		for _, f := range ci.Fields {
			if !c.isSendType(f.Typ) {
				return false
			}
		}
		return true
	}
	return false
}

func (c *Checker) isResourceType(t Type) bool {
	switch t.Name {
	case "db_connection", "socket", "native:task_pool", "window", "gamepad", "renderer", "renderer2d", "shader", "audio_device", "native_resource",
		"native:machine_i2c", "native:machine_spi", "native:machine_uart", "native:machine_can", "native:machine_pwm", "native:machine_servo", "native:machine_motor", "native:machine_modbus_rtu", "native:machine_modbus_tcp", "native:machine_ethercat":
		return true
	}
	if ci := c.classFor(t); ci != nil {
		return ci.Resource
	}
	return false
}

func (c *Checker) resolveAssociatedType(t Type, mapping map[string]Type, tok Token) (Type, error) {
	if strings.HasPrefix(t.Name, "assoc:$") {
		q := strings.TrimPrefix(t.Name, "assoc:$")
		dot := strings.IndexByte(q, '.')
		if dot <= 0 || dot == len(q)-1 {
			return TAny, c.err(tok, "SAGA-T173", "malformed associated type "+t.Name)
		}
		param, assoc := q[:dot], q[dot+1:]
		actual, ok := mapping[param]
		if !ok {
			return t, nil
		}
		ci := c.classFor(actual)
		if ci == nil || ci.Decl.AssociatedTypes == nil || ci.Decl.AssociatedTypes[assoc] == nil {
			return TAny, c.err(tok, "SAGA-T173", fmt.Sprintf("type %s does not define associated type %s", actual, assoc))
		}
		vars := map[string]bool{}
		for _, n := range ci.TypeParams {
			vars[n] = true
		}
		base := typeFromRef(*ci.Decl.AssociatedTypes[assoc], vars)
		classMap := map[string]Type{}
		for i, n := range ci.TypeParams {
			if i < len(actual.Args) {
				classMap[n] = actual.Args[i]
			}
		}
		return substitute(base, classMap), nil
	}
	r := Type{Name: t.Name}
	for _, a := range t.Args {
		x, err := c.resolveAssociatedType(substitute(a, mapping), mapping, tok)
		if err != nil {
			return TAny, err
		}
		r.Args = append(r.Args, x)
	}
	if t.Result != nil {
		x, err := c.resolveAssociatedType(substitute(*t.Result, mapping), mapping, tok)
		if err != nil {
			return TAny, err
		}
		r.Result = &x
	}
	return r, nil
}

func (c *Checker) validateGenericConstraints(d *FnDecl, mapping map[string]Type, tok Token) error {
	for _, group := range d.Constraints {
		actual, ok := mapping[group.Param]
		if !ok {
			continue
		}
		for _, ref := range group.Types {
			required := typeFromRef(ref, map[string]bool{})
			if !c.satisfiesConstraint(actual, required) {
				return c.err(tok, "SAGA-T172", fmt.Sprintf("type %s does not satisfy constraint %s for %s", actual, required, group.Param))
			}
		}
	}
	return nil
}
func (c *Checker) push() { c.Scopes = append(c.Scopes, map[string]VarInfo{}) }
func (c *Checker) pop()  { c.Scopes = c.Scopes[:len(c.Scopes)-1] }
func (c *Checker) find(n string) (VarInfo, bool) {
	for i := len(c.Scopes) - 1; i >= 0; i-- {
		if v, ok := c.Scopes[i][n]; ok {
			return v, true
		}
	}
	return VarInfo{}, false
}
func (c *Checker) markMoved(n string, moved bool) bool {
	for i := len(c.Scopes) - 1; i >= 0; i-- {
		if v, ok := c.Scopes[i][n]; ok {
			v.Moved = moved
			c.Scopes[i][n] = v
			return true
		}
	}
	return false
}

func (c *Checker) define(n string, v VarInfo, t Token) error {
	s := c.Scopes[len(c.Scopes)-1]
	if _, ok := s[n]; ok {
		return c.err(t, "SAGA-T108", "duplicate declaration "+n)
	}
	s[n] = v
	return nil
}
func (c *Checker) declareLocalFn(d *FnDecl) error {
	if d.Abstract || d.Override {
		return c.err(d.Tok, "SAGA-T103", "nested function cannot be abstract/override")
	}
	scope := c.Scopes[len(c.Scopes)-1]
	if _, exists := scope[d.Name]; exists {
		return c.err(d.Tok, "SAGA-T108", "duplicate local name "+d.Name)
	}
	vars := map[string]bool{}
	for _, n := range d.TypeParams {
		vars[n] = true
	}
	ps := []Type{}
	for _, p := range d.Params {
		ps = append(ps, typeFromRef(p.Type, vars))
	}
	var ret Type
	if d.Return != nil {
		ret = typeFromRef(*d.Return, vars)
	} else if d.Body != nil {
		ret = TUnit
	} else {
		return c.err(d.Tok, "SAGA-T103", "nested expression function requires explicit return type")
	}
	fi := FuncInfo{Params: ps, Ret: ret, HasRet: true, TypeParams: d.TypeParams, Decl: d}
	c.LocalFunctions[d] = fi
	scope[d.Name] = VarInfo{Typ: fnT(ps, ret)}
	return nil
}
func (c *Checker) predeclareLocalFns(stmts []Stmt) error {
	for _, s := range stmts {
		if d, ok := s.(*FnDecl); ok {
			if _, done := c.LocalFunctions[d]; !done {
				if e := c.declareLocalFn(d); e != nil {
					return e
				}
			}
		}
	}
	return nil
}

func qualifySourceModuleType(t Type, bind string, publicClasses map[string]bool) Type {
	out := Type{Name: t.Name}
	if strings.HasPrefix(t.Name, "object:") {
		name := strings.TrimPrefix(t.Name, "object:")
		if publicClasses[name] {
			out.Name = "object:" + bind + "." + name
		}
	}
	for _, a := range t.Args {
		out.Args = append(out.Args, qualifySourceModuleType(a, bind, publicClasses))
	}
	if t.Result != nil {
		r := qualifySourceModuleType(*t.Result, bind, publicClasses)
		out.Result = &r
	}
	return out
}

func qualifySourceModuleTypeRef(r TypeRef, bind string, publicClasses map[string]bool) TypeRef {
	out := r
	if publicClasses[r.Name] {
		out.Name = bind + "." + r.Name
	}
	out.Args = nil
	for _, a := range r.Args {
		out.Args = append(out.Args, qualifySourceModuleTypeRef(a, bind, publicClasses))
	}
	return out
}

func cloneSourceModuleFn(f FuncInfo, bind string, publicClasses map[string]bool) FuncInfo {
	out := f
	out.Params = nil
	for _, p := range f.Params {
		out.Params = append(out.Params, qualifySourceModuleType(p, bind, publicClasses))
	}
	out.Ret = qualifySourceModuleType(f.Ret, bind, publicClasses)
	if f.Decl != nil {
		decl := *f.Decl
		decl.Params = append([]Param(nil), f.Decl.Params...)
		for i := range decl.Params {
			decl.Params[i].Type = qualifySourceModuleTypeRef(decl.Params[i].Type, bind, publicClasses)
		}
		if f.Decl.Return != nil {
			q := qualifySourceModuleTypeRef(*f.Decl.Return, bind, publicClasses)
			decl.Return = &q
		}
		decl.Constraints = append([]TypeConstraint(nil), f.Decl.Constraints...)
		for i := range decl.Constraints {
			decl.Constraints[i].Types = append([]TypeRef(nil), f.Decl.Constraints[i].Types...)
			for j := range decl.Constraints[i].Types {
				decl.Constraints[i].Types[j] = qualifySourceModuleTypeRef(decl.Constraints[i].Types[j], bind, publicClasses)
			}
		}
		out.Decl = &decl
	}
	return out
}

func cloneSourceModuleClass(cl *ClassInfo, qualified, bind string, publicClasses map[string]bool) *ClassInfo {
	clone := *cl
	clone.Name = qualified
	clone.Base = qualifySourceModuleType(cl.Base, bind, publicClasses)
	clone.Interfaces = nil
	for _, rel := range cl.Interfaces {
		clone.Interfaces = append(clone.Interfaces, qualifySourceModuleType(rel, bind, publicClasses))
	}
	clone.OwnFields = map[string]FieldInfo{}
	for name, f := range cl.OwnFields {
		f.Typ = qualifySourceModuleType(f.Typ, bind, publicClasses)
		if f.Owner == cl.Name {
			f.Owner = qualified
		}
		clone.OwnFields[name] = f
	}
	clone.Fields = map[string]FieldInfo{}
	for name, f := range cl.Fields {
		f.Typ = qualifySourceModuleType(f.Typ, bind, publicClasses)
		if f.Owner == cl.Name {
			f.Owner = qualified
		} else if publicClasses[f.Owner] {
			f.Owner = bind + "." + f.Owner
		}
		clone.Fields[name] = f
	}
	qualifyFn := func(f FuncInfo) FuncInfo {
		for i := range f.Params {
			f.Params[i] = qualifySourceModuleType(f.Params[i], bind, publicClasses)
		}
		f.Ret = qualifySourceModuleType(f.Ret, bind, publicClasses)
		if f.Owner == cl.Name {
			f.Owner = qualified
		} else if publicClasses[f.Owner] {
			f.Owner = bind + "." + f.Owner
		}
		return f
	}
	clone.OwnMethods = map[string]FuncInfo{}
	for name, f := range cl.OwnMethods {
		clone.OwnMethods[name] = qualifyFn(f)
	}
	clone.Methods = map[string]FuncInfo{}
	for name, f := range cl.Methods {
		clone.Methods[name] = qualifyFn(f)
	}
	if cl.Decl != nil {
		decl := *cl.Decl
		if cl.Decl.Base != nil {
			q := qualifySourceModuleTypeRef(*cl.Decl.Base, bind, publicClasses)
			decl.Base = &q
		}
		decl.Interfaces = append([]TypeRef(nil), cl.Decl.Interfaces...)
		for i := range decl.Interfaces {
			decl.Interfaces[i] = qualifySourceModuleTypeRef(decl.Interfaces[i], bind, publicClasses)
		}
		decl.Constraints = append([]TypeConstraint(nil), cl.Decl.Constraints...)
		for i := range decl.Constraints {
			decl.Constraints[i].Types = append([]TypeRef(nil), cl.Decl.Constraints[i].Types...)
			for j := range decl.Constraints[i].Types {
				decl.Constraints[i].Types[j] = qualifySourceModuleTypeRef(decl.Constraints[i].Types[j], bind, publicClasses)
			}
		}
		if cl.Decl.AssociatedTypes != nil {
			decl.AssociatedTypes = map[string]*TypeRef{}
			for name, ref := range cl.Decl.AssociatedTypes {
				if ref == nil {
					decl.AssociatedTypes[name] = nil
					continue
				}
				q := qualifySourceModuleTypeRef(*ref, bind, publicClasses)
				decl.AssociatedTypes[name] = &q
			}
		}
		clone.Decl = &decl
	}
	return &clone
}

func (c *Checker) publicModuleTypeExportable(t Type, publicClasses map[string]bool, tok Token) error {
	if t.Name == "fn" {
		for _, p := range t.Args {
			if err := c.publicModuleTypeExportable(p, publicClasses, tok); err != nil {
				return err
			}
		}
		if t.Result != nil {
			return c.publicModuleTypeExportable(*t.Result, publicClasses, tok)
		}
		return nil
	}
	for _, a := range t.Args {
		if err := c.publicModuleTypeExportable(a, publicClasses, tok); err != nil {
			return err
		}
	}
	if strings.HasPrefix(t.Name, "object:") {
		name := strings.TrimPrefix(t.Name, "object:")
		if strings.Contains(name, ".") {
			return c.err(tok, "SAGA-T118", "public API directly exposes dependency module type "+name+"; wrap it in a public type owned by this module")
		}
		if !publicClasses[name] {
			return c.err(tok, "SAGA-T118", "public API exposes internal type "+name)
		}
	}
	return nil
}

func (c *Checker) validateModulePublicSurface(stmts []Stmt) error {
	publicClasses := map[string]bool{}
	for _, st := range stmts {
		switch d := st.(type) {
		case *ClassDecl:
			if d.Visibility == "public" {
				publicClasses[d.Name] = true
			}
		case *EnumDecl:
			if d.Visibility == "public" {
				publicClasses[d.Name] = true
			}
		}
	}
	for _, st := range stmts {
		switch d := st.(type) {
		case *VarDecl:
			if d.Visibility == "public" {
				if v, ok := c.find(d.Name); ok {
					if err := c.publicModuleTypeExportable(v.Typ, publicClasses, d.Tok); err != nil {
						return err
					}
				}
			}
		case *FnDecl:
			if d.Visibility == "public" {
				if f, ok := c.Functions[d.Name]; ok {
					if err := c.publicModuleTypeExportable(fnT(f.Params, f.Ret), publicClasses, d.Tok); err != nil {
						return err
					}
				}
			}
		case *ClassDecl:
			if d.Visibility != "public" {
				continue
			}
			ci := c.Classes[d.Name]
			if ci == nil {
				continue
			}
			if ci.Base.Name != "" {
				if err := c.publicModuleTypeExportable(ci.Base, publicClasses, d.Tok); err != nil {
					return err
				}
			}
			for _, rel := range ci.Interfaces {
				if err := c.publicModuleTypeExportable(rel, publicClasses, d.Tok); err != nil {
					return err
				}
			}
			for _, field := range ci.OwnFields {
				if err := c.publicModuleTypeExportable(field.Typ, publicClasses, d.Tok); err != nil {
					return err
				}
			}
			for _, method := range ci.OwnMethods {
				if err := c.publicModuleTypeExportable(fnT(method.Params, method.Ret), publicClasses, d.Tok); err != nil {
					return err
				}
			}
		}
	}
	return nil
}

func sourceModuleBind(x *SourceModuleStmt) string {
	if x.BindName != "" {
		return x.BindName
	}
	return x.Name
}

func interfaceStringList(v interface{}) []string {
	out := []string{}
	switch xs := v.(type) {
	case []interface{}:
		for _, item := range xs {
			if text, ok := item.(string); ok {
				out = append(out, text)
			}
		}
	case []string:
		out = append(out, xs...)
	}
	return out
}

func interfaceMapList(v interface{}) []map[string]interface{} {
	out := []map[string]interface{}{}
	switch xs := v.(type) {
	case []interface{}:
		for _, item := range xs {
			if m, ok := item.(map[string]interface{}); ok {
				out = append(out, m)
			}
		}
	case []map[string]interface{}:
		out = append(out, xs...)
	}
	return out
}

func interfaceBool(v interface{}) bool {
	b, _ := v.(bool)
	return b
}

func moduleInterfaceType(text string, typeParams []string) (Type, error) {
	// Reuse the language's own type parser so `.smi.json` cannot grow a second,
	// subtly different type grammar. The initializer is never type-checked.
	tokens, err := lex("let __smi_value: "+text+" = 0", "<module-interface>")
	if err != nil {
		return TAny, err
	}
	stmts, err := parse(tokens)
	if err != nil || len(stmts) != 1 {
		if err == nil {
			err = fmt.Errorf("invalid module-interface type %q", text)
		}
		return TAny, err
	}
	decl, ok := stmts[0].(*VarDecl)
	if !ok || decl.Type == nil {
		return TAny, fmt.Errorf("invalid module-interface type %q", text)
	}
	vars := map[string]bool{}
	for _, name := range typeParams {
		vars[name] = true
	}
	return typeFromRef(*decl.Type, vars), nil
}

func (c *Checker) loadSourceModuleInterface(x *SourceModuleStmt) error {
	bind := sourceModuleBind(x)
	iface := x.Interface
	if iface == nil {
		return fmt.Errorf("missing module interface")
	}
	publicClasses := map[string]bool{}
	for _, item := range iface.Exports {
		kind, _ := item["kind"].(string)
		name, _ := item["name"].(string)
		if (kind == "class" || kind == "interface" || kind == "enum") && name != "" {
			publicClasses[name] = true
		}
	}

	// Class shells first so signatures may refer forward to another public class
	// from the same module without loading the implementation body.
	for _, item := range iface.Exports {
		kind, _ := item["kind"].(string)
		if kind != "class" && kind != "interface" {
			continue
		}
		name, _ := item["name"].(string)
		if name == "" {
			return fmt.Errorf("module interface contains unnamed class")
		}
		qualified := bind + "." + name
		typeParams := interfaceStringList(item["type_params"])
		base := Type{}
		if raw, ok := item["base"].(string); ok && raw != "" {
			t, err := moduleInterfaceType(raw, typeParams)
			if err != nil {
				return err
			}
			base = qualifySourceModuleType(t, bind, publicClasses)
		}
		interfaces := []Type{}
		for _, raw := range interfaceStringList(item["interfaces"]) {
			t, err := moduleInterfaceType(raw, typeParams)
			if err != nil {
				return err
			}
			interfaces = append(interfaces, qualifySourceModuleType(t, bind, publicClasses))
		}
		decl := &ClassDecl{Name: name, TypeParams: append([]string{}, typeParams...), Visibility: "public", Abstract: interfaceBool(item["abstract"]), Interface: kind == "interface", Tok: x.Tok}
		c.Classes[qualified] = &ClassInfo{Name: qualified, Decl: decl, TypeParams: append([]string{}, typeParams...), Base: base, Interfaces: interfaces, OwnFields: map[string]FieldInfo{}, Fields: map[string]FieldInfo{}, OwnFieldOrder: []string{}, FieldOrder: []string{}, OwnMethods: map[string]FuncInfo{}, Methods: map[string]FuncInfo{}, Abstract: decl.Abstract, Interface: decl.Interface}
	}

	members := map[string]Type{}
	functions := map[string]FuncInfo{}
	for _, item := range iface.Exports {
		kind, _ := item["kind"].(string)
		name, _ := item["name"].(string)
		switch kind {
		case "class", "interface":
			qualified := bind + "." + name
			ci := c.Classes[qualified]
			if ci == nil {
				return fmt.Errorf("module interface class shell missing for %s", qualified)
			}
			for _, f := range interfaceMapList(item["fields"]) {
				fieldName, _ := f["name"].(string)
				raw, _ := f["type"].(string)
				t, err := moduleInterfaceType(raw, ci.TypeParams)
				if err != nil {
					return err
				}
				t = qualifySourceModuleType(t, bind, publicClasses)
				fi := FieldInfo{Typ: t, Mutable: interfaceBool(f["mutable"]), Private: interfaceBool(f["private"]), Owner: qualified}
				ci.OwnFields[fieldName] = fi
				ci.Fields[fieldName] = fi
				ci.OwnFieldOrder = append(ci.OwnFieldOrder, fieldName)
				ci.FieldOrder = append(ci.FieldOrder, fieldName)
				ci.Decl.Fields = append(ci.Decl.Fields, FieldDecl{Name: fieldName, Mutable: fi.Mutable, Private: fi.Private, Tok: x.Tok})
			}
			for _, m := range interfaceMapList(item["methods"]) {
				methodName, _ := m["name"].(string)
				methodTypeParams := interfaceStringList(m["type_params"])
				visibleVars := append(append([]string{}, ci.TypeParams...), methodTypeParams...)
				params := []Type{}
				for _, raw := range interfaceStringList(m["params"]) {
					t, err := moduleInterfaceType(raw, visibleVars)
					if err != nil {
						return err
					}
					params = append(params, qualifySourceModuleType(t, bind, publicClasses))
				}
				rawRet, _ := m["return"].(string)
				if rawRet == "" {
					rawRet = "unit"
				}
				ret, err := moduleInterfaceType(rawRet, visibleVars)
				if err != nil {
					return err
				}
				ret = qualifySourceModuleType(ret, bind, publicClasses)
				decl := &FnDecl{Name: methodName, TypeParams: append([]string{}, methodTypeParams...), Visibility: "public", Abstract: interfaceBool(m["abstract"]), Tok: x.Tok}
				fi := FuncInfo{Params: params, Ret: ret, HasRet: true, TypeParams: append([]string{}, methodTypeParams...), Decl: decl, Owner: qualified, Abstract: decl.Abstract}
				ci.OwnMethods[methodName] = fi
				ci.Methods[methodName] = fi
				ci.Decl.Methods = append(ci.Decl.Methods, decl)
			}
			retArgs := []Type{}
			for _, n := range ci.TypeParams {
				retArgs = append(retArgs, typeVar(n))
			}
			ps := []Type{}
			for _, n := range ci.FieldOrder {
				ps = append(ps, ci.Fields[n].Typ)
			}
			members[name] = fnT(ps, objectT(qualified, retArgs...))
		case "enum":
			qualified := bind + "." + name
			typeParams := interfaceStringList(item["type_params"])
			variants := map[string]bool{}
			payloads := map[string][]Type{}
			for _, rawVariant := range interfaceMapList(item["variants"]) {
				variantName, _ := rawVariant["name"].(string)
				if variantName == "" {
					continue
				}
				variants[variantName] = true
				for _, raw := range interfaceStringList(rawVariant["payload"]) {
					t, err := moduleInterfaceType(raw, typeParams)
					if err != nil {
						return err
					}
					payloads[variantName] = append(payloads[variantName], qualifySourceModuleType(t, bind, publicClasses))
				}
			}
			c.Enums[qualified] = variants
			c.EnumPayloads[qualified] = payloads
			c.EnumTypeParams[qualified] = append([]string{}, typeParams...)
			members[name] = Type{Name: "enumtype:" + qualified}
		case "var":
			raw, _ := item["type"].(string)
			t, err := moduleInterfaceType(raw, nil)
			if err != nil {
				return err
			}
			members[name] = qualifySourceModuleType(t, bind, publicClasses)
		case "fn":
			typeParams := interfaceStringList(item["type_params"])
			params := []Type{}
			for _, raw := range interfaceStringList(item["params"]) {
				t, err := moduleInterfaceType(raw, typeParams)
				if err != nil {
					return err
				}
				params = append(params, qualifySourceModuleType(t, bind, publicClasses))
			}
			rawRet, _ := item["return"].(string)
			if rawRet == "" {
				rawRet = "unit"
			}
			ret, err := moduleInterfaceType(rawRet, typeParams)
			if err != nil {
				return err
			}
			ret = qualifySourceModuleType(ret, bind, publicClasses)
			decl := &FnDecl{Name: name, TypeParams: append([]string{}, typeParams...), Visibility: "public", Tok: x.Tok}
			fi := FuncInfo{Params: params, Ret: ret, HasRet: true, TypeParams: append([]string{}, typeParams...), Decl: decl}
			functions[name] = fi
			members[name] = fnT(params, ret)
		default:
			return fmt.Errorf("unsupported module-interface export kind %q", kind)
		}
	}
	c.SourceModules[bind] = SourceModuleInfo{Name: x.Name, Members: members, Functions: functions}
	return c.define(bind, VarInfo{Typ: Type{Name: "srcmodule:" + bind}}, x.Tok)
}

func (c *Checker) loadSourceModuleBody(x *SourceModuleStmt) error {
	child := NewChecker()
	if err := child.Check(x.Stmts); err != nil {
		return err
	}
	if err := child.validateModulePublicSurface(x.Stmts); err != nil {
		return err
	}
	bind := sourceModuleBind(x)
	publicClasses := map[string]bool{}
	for _, st := range x.Stmts {
		switch d := st.(type) {
		case *ClassDecl:
			if d.Visibility == "public" {
				publicClasses[d.Name] = true
			}
		case *EnumDecl:
			if d.Visibility == "public" {
				publicClasses[d.Name] = true
			}
		}
	}
	members := map[string]Type{}
	functions := map[string]FuncInfo{}
	for _, st := range x.Stmts {
		switch d := st.(type) {
		case *VarDecl:
			if d.Visibility == "public" {
				if v, ok := child.find(d.Name); ok {
					members[d.Name] = qualifySourceModuleType(v.Typ, bind, publicClasses)
				}
			}
		case *FnDecl:
			if d.Visibility == "public" {
				if f, ok := child.Functions[d.Name]; ok {
					qf := cloneSourceModuleFn(f, bind, publicClasses)
					r := qf.Ret
					if d.Async {
						r = futureT(r)
					}
					members[d.Name] = fnT(qf.Params, r)
					functions[d.Name] = qf
				}
			}
		case *ClassDecl:
			if d.Visibility == "public" {
				if cl := child.Classes[d.Name]; cl != nil {
					qualified := bind + "." + d.Name
					clone := cloneSourceModuleClass(cl, qualified, bind, publicClasses)
					c.Classes[qualified] = clone
					ps := []Type{}
					for _, n := range clone.FieldOrder {
						ps = append(ps, clone.Fields[n].Typ)
					}
					retArgs := []Type{}
					for _, name := range clone.TypeParams {
						retArgs = append(retArgs, typeVar(name))
					}
					members[d.Name] = fnT(ps, objectT(qualified, retArgs...))
				}
			}
		case *EnumDecl:
			if d.Visibility == "public" {
				qualified := bind + "." + d.Name
				variants := map[string]bool{}
				payloads := map[string][]Type{}
				for name, present := range child.Enums[d.Name] {
					variants[name] = present
				}
				for name, ps := range child.EnumPayloads[d.Name] {
					for _, pt := range ps {
						payloads[name] = append(payloads[name], qualifySourceModuleType(pt, bind, publicClasses))
					}
				}
				c.Enums[qualified] = variants
				c.EnumPayloads[qualified] = payloads
				c.EnumTypeParams[qualified] = append([]string{}, child.EnumTypeParams[d.Name]...)
				members[d.Name] = Type{Name: "enumtype:" + qualified}
			}
		}
	}
	c.SourceModules[bind] = SourceModuleInfo{Name: x.Name, Members: members, Functions: functions}
	return c.define(bind, VarInfo{Typ: Type{Name: "srcmodule:" + bind}}, x.Tok)
}

func (c *Checker) loadSourceModule(x *SourceModuleStmt) error {
	bind := sourceModuleBind(x)
	if _, ok := c.SourceModules[bind]; ok {
		return nil
	}
	if _, exists := c.find(bind); exists {
		return c.err(x.Tok, "SAGA-T108", "duplicate module alias "+bind)
	}
	if x.Interface != nil {
		return c.loadSourceModuleInterface(x)
	}
	return c.loadSourceModuleBody(x)
}

func (c *Checker) refreshSourceModuleConstructors() {
	for bind, mod := range c.SourceModules {
		for name := range mod.Members {
			qualified := bind + "." + name
			ci := c.Classes[qualified]
			if ci == nil {
				continue
			}
			ps := []Type{}
			for _, fieldName := range ci.FieldOrder {
				if f, ok := ci.Fields[fieldName]; ok {
					ps = append(ps, f.Typ)
				}
			}
			retArgs := []Type{}
			for _, typeParam := range ci.TypeParams {
				retArgs = append(retArgs, typeVar(typeParam))
			}
			mod.Members[name] = fnT(ps, objectT(qualified, retArgs...))
		}
		c.SourceModules[bind] = mod
	}
}

func (c *Checker) enumIdentity(t Type) (string, []Type, bool) {
	if t.Name == "option" && len(t.Args) == 1 {
		return "Option", t.Args, true
	}
	if t.Name == "result" && len(t.Args) == 2 {
		return "Result", t.Args, true
	}
	name := objectTypeName(t)
	if name != "" && c.Enums[name] != nil {
		return name, t.Args, true
	}
	return "", nil, false
}

func (c *Checker) enumMatchPattern(e Expr, enumType Type) (string, map[string]VarInfo, bool, error) {
	enumName, enumArgs, ok := c.enumIdentity(enumType)
	if !ok {
		return "", nil, false, nil
	}
	callee := e
	args := []Expr{}
	if call, isCall := e.(*Call); isCall {
		callee = call.Callee
		args = call.Args
	}
	q, ok := sourceQualifiedExprName(callee)
	if !ok || !strings.Contains(q, ".") {
		return "", nil, false, nil
	}
	idx := strings.LastIndex(q, ".")
	owner, variant := q[:idx], q[idx+1:]
	if owner != enumName || !c.Enums[enumName][variant] {
		return "", nil, false, nil
	}
	mapping := typeParamMap(c.EnumTypeParams[enumName], enumArgs)
	rawPayload := c.EnumPayloads[enumName][variant]
	payload := make([]Type, 0, len(rawPayload))
	for _, typ := range rawPayload {
		payload = append(payload, substitute(typ, mapping))
	}
	if len(args) != len(payload) {
		return "", nil, true, c.err(e.token(), "SAGA-T103", fmt.Sprintf("enum variant %s.%s expects %d payload values", enumName, variant, len(payload)))
	}
	bindings := map[string]VarInfo{}
	for idx, arg := range args {
		v, isVariable := arg.(*Variable)
		if !isVariable {
			return "", nil, true, c.err(arg.token(), "SAGA-T103", "match payload pattern must be a variable or _")
		}
		if v.Name == "_" {
			continue
		}
		if _, exists := bindings[v.Name]; exists {
			return "", nil, true, c.err(v.Tok, "SAGA-T108", "duplicate match payload variable "+v.Name)
		}
		bindings[v.Name] = VarInfo{Typ: payload[idx]}
	}
	return variant, bindings, true, nil
}

func sourceQualifiedExprName(e Expr) (string, bool) {
	switch x := e.(type) {
	case *Variable:
		return x.Name, true
	case *Member:
		base, ok := sourceQualifiedExprName(x.Target)
		if !ok {
			return "", false
		}
		return base + "." + x.Name, true
	default:
		return "", false
	}
}

func (c *Checker) checkStmt(s Stmt) error {
	switch x := s.(type) {
	case *EditionDecl:
		if x.Edition != "2027" && x.Edition != "1" {
			return c.err(x.Tok, "SAGA-T173", "unsupported language edition "+x.Edition+"; supported editions are 1.0 and 2027")
		}
		return nil
	case *ModuleDecl:
		return nil
	case *SourceModuleStmt:
		return c.loadSourceModule(x)
	case *UseStmt:
		if x.Module != "" {
			switch x.Module {
			case "task", "sys", "compiler", "io", "json", "time", "math", "random", "crypto", "security", "game", "net", "http", "web", "app", "db", "process", "regex", "ffi", "jit", "embedded", "machine", "drone", "vision":
				bind := x.Module
				if x.Alias != "" {
					bind = x.Alias
				}
				return c.define(bind, VarInfo{Typ: Type{Name: "module:" + x.Module}}, x.Tok)
			default:
				return c.err(x.Tok, "SAGA-T106", "hosted module is outside Standard Core: "+x.Module)
			}
		}
		return nil
	case *EnumDecl:
		return nil
	case *TestDecl:
		return c.checkStmt(x.Body)
	case *MatchStmt:
		vt, e := c.checkExpr(x.Value, nil)
		if e != nil {
			return e
		}
		seen := map[string]bool{}
		enumName, _, _ := c.enumIdentity(vt)
		covered := map[string]bool{}
		for _, mc := range x.Cases {
			variant, bindings, matched, pe := c.enumMatchPattern(mc.Pattern, vt)
			if pe != nil {
				return pe
			}
			if matched {
				key := enumName + "." + variant
				if seen[key] {
					return c.err(mc.Tok, "SAGA-T108", "duplicate match case")
				}
				seen[key] = true
				covered[variant] = true
				c.push()
				for name, info := range bindings {
					c.Scopes[len(c.Scopes)-1][name] = info
				}
				pe = c.checkStmt(mc.Body)
				c.pop()
				if pe != nil {
					return pe
				}
				continue
			}
			pt, pe := c.checkExpr(mc.Pattern, &vt)
			if pe != nil {
				return pe
			}
			if !c.assignable(vt, pt) && !c.assignable(pt, vt) {
				return c.err(mc.Tok, "SAGA-T103", "match case type does not match value")
			}
			key := fmt.Sprintf("%T:%v", mc.Pattern, mc.Pattern.token())
			if seen[key] {
				return c.err(mc.Tok, "SAGA-T108", "duplicate match case")
			}
			seen[key] = true
			if enumName != "" {
				if m, ok := mc.Pattern.(*Member); ok {
					if targetName, ok := sourceQualifiedExprName(m.Target); ok && targetName == enumName {
						covered[m.Name] = true
					}
				}
			}
			if pe = c.checkStmt(mc.Body); pe != nil {
				return pe
			}
		}
		if x.Default != nil {
			if e = c.checkStmt(x.Default); e != nil {
				return e
			}
		} else if enumName != "" {
			for v := range c.Enums[enumName] {
				if !covered[v] {
					return c.err(x.Tok, "SAGA-T112", "non-exhaustive match; missing "+enumName+"."+v)
				}
			}
		}
		return nil
	case *VarDecl:
		if e := c.validateAnnotations(x.Annotations, x.Tok); e != nil {
			return e
		}
		var expected *Type
		if x.Type != nil {
			want := typeFromRef(*x.Type, map[string]bool{})
			expected = &want
		}
		t, e := c.checkExpr(x.Init, expected)
		if e != nil {
			return e
		}
		if x.Type != nil {
			want := *expected
			if !c.assignable(want, t) {
				return c.err(x.Tok, "SAGA-T103", fmt.Sprintf("type mismatch: expected %s, got %s", want, t))
			}
			t = want
		}
		return c.define(x.Name, VarInfo{Typ: t, Mutable: x.Mutable}, x.Tok)
	case *Assign:
		return c.checkAssign(x)
	case *ExprStmt:
		_, e := c.checkExpr(x.Expr, nil)
		return e
	case *DeferStmt:
		_, e := c.checkExpr(x.Value, nil)
		return e
	case *UsingStmt:
		t, e := c.checkExpr(x.Init, nil)
		if e != nil {
			return e
		}
		if !c.isResourceType(t) && t.Name != "any" {
			return c.err(x.Tok, "SAGA-T174", "using requires a resource or value with deterministic close semantics")
		}
		c.push()
		c.Scopes[len(c.Scopes)-1][x.Name] = VarInfo{Typ: t}
		e = c.checkStmt(x.Body)
		c.pop()
		return e
	case *UnsafeStmt:
		c.UnsafeDepth++
		e := c.checkStmt(x.Body)
		c.UnsafeDepth--
		return e
	case *TaskGroupStmt:
		return c.checkStmt(x.Body)
	case *Block:
		c.push()
		defer c.pop()
		if e := c.predeclareLocalFns(x.Stmts); e != nil {
			return e
		}
		for _, q := range x.Stmts {
			if e := c.checkStmt(q); e != nil {
				return e
			}
		}
		return nil
	case *IfStmt:
		t, e := c.checkExpr(x.Cond, nil)
		if e != nil {
			return e
		}
		if t.Name != "bool" {
			return c.err(x.Tok, "SAGA-T104", "if condition must be bool")
		}
		if e = c.checkStmt(x.Then); e != nil {
			return e
		}
		if x.Else != nil {
			return c.checkStmt(x.Else)
		}
		return nil
	case *WhileStmt:
		t, e := c.checkExpr(x.Cond, nil)
		if e != nil {
			return e
		}
		if t.Name != "bool" {
			return c.err(x.Tok, "SAGA-T104", "while condition must be bool")
		}
		c.LoopDepth++
		e = c.checkStmt(x.Body)
		c.LoopDepth--
		return e
	case *ForStmt:
		it, e := c.checkExpr(x.Iterable, nil)
		if e != nil {
			return e
		}
		elem := TAny
		switch it.Name {
		case "range":
			elem = TInt
		case "list", "set":
			elem = it.Args[0]
		case "text":
			elem = TText
		default:
			return c.err(x.Tok, "SAGA-T103", "for requires range, list, set, or text")
		}
		c.LoopDepth++
		c.push()
		c.Scopes[len(c.Scopes)-1][x.Name] = VarInfo{Typ: elem}
		e = c.checkStmt(x.Body)
		c.pop()
		c.LoopDepth--
		return e
	case *BreakStmt, *ContinueStmt:
		if c.LoopDepth == 0 {
			return c.err(s.token(), "SAGA-T103", "break/continue is only valid in a loop")
		}
		return nil
	case *ReturnStmt:
		if c.CurrentFn == nil {
			return c.err(x.Tok, "SAGA-T103", "return is only valid in a function")
		}
		got := TUnit
		var e error
		if x.Value != nil {
			got, e = c.checkExpr(x.Value, c.CurrentRet)
			if e != nil {
				return e
			}
		}
		if c.CurrentRet != nil && !c.assignable(*c.CurrentRet, got) {
			return c.err(x.Tok, "SAGA-T103", fmt.Sprintf("return type mismatch: expected %s, got %s", *c.CurrentRet, got))
		}
		return nil
	case *ThrowStmt:
		_, e := c.checkExpr(x.Value, nil)
		return e
	case *TryStmt:
		if e := c.checkStmt(x.Try); e != nil {
			return e
		}
		if x.Catch != nil {
			c.push()
			c.Scopes[len(c.Scopes)-1][x.CatchName] = VarInfo{Typ: TError}
			e := c.checkStmt(x.Catch)
			c.pop()
			if e != nil {
				return e
			}
		}
		if x.Finally != nil {
			return c.checkStmt(x.Finally)
		}
		return nil
	case *FnDecl:
		return c.checkFnBody(x, "")
	case *ClassDecl:
		old := c.CurrentClass
		c.CurrentClass = x.Name
		defer func() { c.CurrentClass = old }()
		for _, m := range x.Methods {
			if e := c.checkFnBody(m, x.Name); e != nil {
				return e
			}
		}
		return nil
	}
	return nil
}
func (c *Checker) typeVarHasConstraint(t Type, names ...string) bool {
	if !isTypeVar(t) {
		return false
	}
	param := strings.TrimPrefix(t.Name, "$")
	for _, q := range c.CurrentConstraints[param] {
		for _, name := range names {
			if q.Name == "object:"+name || q.Name == name {
				return true
			}
		}
	}
	return false
}

func constraintsFor(groups []TypeConstraint) map[string][]Type {
	out := map[string][]Type{}
	for _, g := range groups {
		for _, r := range g.Types {
			out[g.Param] = append(out[g.Param], typeFromRef(r, map[string]bool{}))
		}
	}
	return out
}

func (c *Checker) checkFnBody(d *FnDecl, owner string) error {
	if e := validateControlTick047(d); e != nil {
		return e
	}
	if owner == "" {
		if e := c.validateControlTransitive050(d); e != nil {
			return e
		}
	}
	if d.Abstract {
		return nil
	}
	fi := FuncInfo{}
	if owner == "" {
		if local, ok := c.LocalFunctions[d]; ok {
			fi = local
		} else {
			fi = c.Functions[d.Name]
		}
	} else {
		fi = c.Classes[owner].OwnMethods[d.Name]
	}
	oldFn, oldRet, oldConstraints := c.CurrentFn, c.CurrentRet, c.CurrentConstraints
	c.CurrentFn = d
	c.CurrentRet = &fi.Ret
	c.CurrentConstraints = constraintsFor(d.Constraints)
	if owner != "" {
		for k, vals := range constraintsFor(c.Classes[owner].Decl.Constraints) {
			c.CurrentConstraints[k] = append(c.CurrentConstraints[k], vals...)
		}
	}
	c.push()
	defer func() { c.pop(); c.CurrentFn = oldFn; c.CurrentRet = oldRet; c.CurrentConstraints = oldConstraints }()
	if owner != "" {
		c.Scopes[len(c.Scopes)-1]["self"] = VarInfo{Typ: objectT(owner)}
	}
	for i, p := range d.Params {
		c.Scopes[len(c.Scopes)-1][p.Name] = VarInfo{Typ: fi.Params[i]}
	}
	if d.Body != nil {
		if e := c.predeclareLocalFns(d.Body.Stmts); e != nil {
			return e
		}
	}
	if d.ExprBody != nil {
		got, e := c.checkExpr(d.ExprBody, nil)
		if e != nil {
			return e
		}
		if d.Return != nil && !c.assignable(fi.Ret, got) {
			return c.err(d.Tok, "SAGA-T103", fmt.Sprintf("return type mismatch: expected %s, got %s", fi.Ret, got))
		}
		if d.Return == nil {
			fi.Ret = got
			fi.HasRet = true
			if owner == "" {
				c.Functions[d.Name] = fi
			} else {
				c.Classes[owner].OwnMethods[d.Name] = fi
				c.Classes[owner].Methods[d.Name] = fi
			}
		}
		return nil
	}
	if d.Body != nil {
		if e := c.checkStmt(d.Body); e != nil {
			return e
		}
		if d.Return != nil && fi.Ret.Name != "unit" && !alwaysReturns(d.Body) {
			return c.err(d.Tok, "SAGA-T109", "not all paths return a value")
		}
	}
	return nil
}
func alwaysReturns(b *Block) bool {
	for _, s := range b.Stmts {
		switch x := s.(type) {
		case *ReturnStmt:
			return true
		case *ThrowStmt:
			return true
		case *IfStmt:
			if x.Else != nil && alwaysReturns(x.Then) {
				if eb, ok := x.Else.(*Block); ok && alwaysReturns(eb) {
					return true
				}
				if ei, ok := x.Else.(*IfStmt); ok && ifReturns(ei) {
					return true
				}
			}
		}
	}
	return false
}
func ifReturns(x *IfStmt) bool {
	if !alwaysReturns(x.Then) || x.Else == nil {
		return false
	}
	if b, ok := x.Else.(*Block); ok {
		return alwaysReturns(b)
	}
	if i, ok := x.Else.(*IfStmt); ok {
		return ifReturns(i)
	}
	return false
}
func (c *Checker) checkAssign(x *Assign) error {
	switch t := x.Target.(type) {
	case *Variable:
		vi, ok := c.find(t.Name)
		if !ok {
			v, e := c.checkExpr(x.Value, nil)
			if e != nil {
				return e
			}
			// Natural binding: the first simple assignment is an inferred,
			// immutable lexical binding. Mutation remains explicit via `var`.
			return c.define(t.Name, VarInfo{Typ: v, Mutable: false}, t.Tok)
		}
		if !vi.Mutable {
			return c.err(t.Tok, "SAGA-T101", "cannot assign to immutable binding "+t.Name)
		}
		v, e := c.checkExpr(x.Value, &vi.Typ)
		if e != nil {
			return e
		}
		if !c.assignable(vi.Typ, v) {
			return c.err(t.Tok, "SAGA-T103", fmt.Sprintf("assignment type mismatch: expected %s, got %s", vi.Typ, v))
		}
		c.markMoved(t.Name, false)
		return nil
	case *Member:
		obj, e := c.checkExpr(t.Target, nil)
		if e != nil {
			return e
		}
		ci := c.classFor(obj)
		if ci == nil {
			return c.err(t.Tok, "SAGA-T103", "member assignment requires object")
		}
		f, ok := ci.Fields[t.Name]
		if !ok {
			return c.err(t.Tok, "SAGA-T106", "unknown field "+t.Name)
		}
		if f.Private && c.CurrentClass != f.Owner {
			return c.err(t.Tok, "SAGA-T107", "private member access")
		}
		if !f.Mutable {
			return c.err(t.Tok, "SAGA-T101", "field is immutable")
		}
		v, e := c.checkExpr(x.Value, &f.Typ)
		if e != nil {
			return e
		}
		if !c.assignable(f.Typ, v) {
			return c.err(t.Tok, "SAGA-T103", fmt.Sprintf("field assignment type mismatch: expected %s, got %s", f.Typ, v))
		}
		return nil
	}
	return c.err(x.Tok, "SAGA-T103", "invalid assignment target")
}
func (c *Checker) checkExpr(x Expr, expected *Type) (Type, error) {
	switch v := x.(type) {
	case *InterpolatedString:
		for _, ex := range v.Exprs {
			if _, err := c.checkExpr(ex, nil); err != nil {
				return TAny, err
			}
		}
		return TText, nil
	case *Literal:
		switch q := v.Value.(type) {
		case Number:
			return map[string]Type{"int": TInt, "decimal": TDecimal, "rational": TRational}[q.Kind], nil
		case FloatValue:
			if q.Bits == 32 {
				return TFloat32, nil
			}
			return TFloat64, nil
		case bool:
			return TBool, nil
		case string:
			return TText, nil
		}
		return TAny, nil
	case *Variable:
		if z, ok := c.find(v.Name); ok {
			if z.Moved {
				return TAny, c.err(v.Tok, "SAGA-T180", "use of moved resource "+v.Name)
			}
			return z.Typ, nil
		}
		if f, ok := c.Functions[v.Name]; ok {
			r := f.Ret
			if f.Decl != nil && f.Decl.Async {
				r = futureT(r)
			}
			return fnT(f.Params, r), nil
		}
		if cl := c.Classes[v.Name]; cl != nil {
			ps := []Type{}
			for _, name := range cl.FieldOrder {
				ps = append(ps, cl.Fields[name].Typ)
			}
			retArgs := []Type{}
			for _, name := range cl.TypeParams {
				retArgs = append(retArgs, typeVar(name))
			}
			return fnT(ps, objectT(v.Name, retArgs...)), nil
		}
		if coreBuiltins[v.Name] {
			return TBuiltin, nil
		}
		msg := "unknown name " + v.Name
		if suggestion := c.nearestVisibleName(v.Name); suggestion != "" {
			msg += "; did you mean `" + suggestion + "`?"
		}
		return TAny, c.err(v.Tok, "SAGA-T102", msg)
	case *ListExpr:
		if len(v.Items) == 0 {
			if expected != nil && expected.Name == "list" {
				return *expected, nil
			}
			return TAny, c.err(v.Tok, "SAGA-T103", "empty list requires an explicit list type")
		}
		var elem Type
		for i, a := range v.Items {
			var exp *Type
			if expected != nil && expected.Name == "list" {
				exp = &expected.Args[0]
			}
			t, e := c.checkExpr(a, exp)
			if e != nil {
				return TAny, e
			}
			if i == 0 {
				elem = t
			} else if isNumeric(elem) && isNumeric(t) {
				elem = commonNumeric(elem, t)
			} else if !sameType(elem, t) {
				return TAny, c.err(v.Tok, "SAGA-T103", "list element types must match")
			}
		}
		return listT(elem), nil
	case *Unary:
		t, e := c.checkExpr(v.Right, nil)
		if e != nil {
			return TAny, e
		}
		if v.Op.Kind == BANG || v.Op.Kind == NOT {
			if t.Name != "bool" {
				return TAny, c.err(v.Op, "SAGA-T103", "not requires bool")
			}
			return TBool, nil
		}
		if !isNumeric(t) {
			return TAny, c.err(v.Op, "SAGA-T103", "unary minus requires number")
		}
		return t, nil
	case *AwaitExpr:
		t, e := c.checkExpr(v.Value, nil)
		if e != nil {
			return TAny, e
		}
		if t.Name != "future" || len(t.Args) != 1 {
			return TAny, c.err(v.Tok, "SAGA-T175", "await requires future[T]")
		}
		return t.Args[0], nil
	case *MoveExpr:
		q, ok := v.Value.(*Variable)
		if !ok {
			return TAny, c.err(v.Tok, "SAGA-T176", "move requires a named resource binding")
		}
		vi, found := c.find(q.Name)
		if !found {
			return TAny, c.err(q.Tok, "SAGA-T102", "unknown name "+q.Name)
		}
		if vi.Moved {
			return TAny, c.err(q.Tok, "SAGA-T180", "resource already moved: "+q.Name)
		}
		if !c.isResourceType(vi.Typ) {
			return TAny, c.err(v.Tok, "SAGA-T176", "move is reserved for move-only resource values")
		}
		c.markMoved(q.Name, true)
		return vi.Typ, nil
	case *PropagateExpr:
		t, e := c.checkExpr(v.Value, nil)
		if e != nil {
			return TAny, e
		}
		if c.CurrentFn == nil || c.CurrentRet == nil {
			return TAny, c.err(v.Tok, "SAGA-T177", "? propagation is only valid inside a function")
		}
		if t.Name == "result" && len(t.Args) == 2 {
			if c.CurrentRet.Name != "result" || len(c.CurrentRet.Args) != 2 || !c.assignable(c.CurrentRet.Args[1], t.Args[1]) {
				return TAny, c.err(v.Tok, "SAGA-T177", "result ? requires the enclosing function to return result with a compatible error type")
			}
			return t.Args[0], nil
		}
		if t.Name == "option" && len(t.Args) == 1 {
			if c.CurrentRet.Name != "option" {
				return TAny, c.err(v.Tok, "SAGA-T177", "option ? requires the enclosing function to return option")
			}
			return t.Args[0], nil
		}
		return TAny, c.err(v.Tok, "SAGA-T177", "? requires option[T] or result[T,E]")
	case *ClosureExpr:
		return c.checkClosure(v, expected)
	case *Binary:
		return c.checkBinary(v)
	case *RangeExpr:
		a, e := c.checkExpr(v.Start, nil)
		if e != nil {
			return TAny, e
		}
		b, e := c.checkExpr(v.End, nil)
		if e != nil {
			return TAny, e
		}
		if a.Name != "int" || b.Name != "int" {
			return TAny, c.err(v.Op, "SAGA-T103", "range endpoints must be int")
		}
		return TRange, nil
	case *Index:
		t, e := c.checkExpr(v.Target, nil)
		if e != nil {
			return TAny, e
		}
		i, e := c.checkExpr(v.Index, nil)
		if e != nil {
			return TAny, e
		}
		if i.Name != "int" {
			return TAny, c.err(v.Tok, "SAGA-T103", "index must be int")
		}
		if t.Name == "list" {
			return t.Args[0], nil
		}
		if t.Name == "text" {
			return TText, nil
		}
		return TAny, c.err(v.Tok, "SAGA-T103", "indexing requires list or text")
	case *Member:
		return c.checkMember(v, expected)
	case *Call:
		return c.checkCall(v, expected)
	}
	return TAny, c.err(x.token(), "SAGA-T103", "unknown expression")
}
func (c *Checker) checkBinary(v *Binary) (Type, error) {
	l, e := c.checkExpr(v.Left, nil)
	if e != nil {
		return TAny, e
	}
	if v.Op.Kind == AND || v.Op.Kind == OR {
		r, e := c.checkExpr(v.Right, nil)
		if e != nil {
			return TAny, e
		}
		if l.Name != "bool" || r.Name != "bool" {
			return TAny, c.err(v.Op, "SAGA-T103", "and/or require bool")
		}
		return TBool, nil
	}
	r, e := c.checkExpr(v.Right, nil)
	if e != nil {
		return TAny, e
	}
	switch v.Op.Kind {
	case PLUS, MINUS, STAR, SLASH, PERCENT, POWER:
		if v.Op.Kind == PLUS && l.Name == "text" && r.Name == "text" {
			return TText, nil
		}
		if !isNumeric(l) || !isNumeric(r) {
			return TAny, c.err(v.Op, "SAGA-T103", "arithmetic requires numbers")
		}
		if (isFloat(l) && isExactNumeric(r)) || (isExactNumeric(l) && isFloat(r)) {
			return TAny, c.err(v.Op, "SAGA-T170", "exact and floating-point numbers require an explicit conversion")
		}
		if v.Op.Kind == PERCENT {
			if l.Name != "int" || r.Name != "int" {
				return TAny, c.err(v.Op, "SAGA-T103", "% requires int")
			}
			return TInt, nil
		}
		if isFloat(l) || isFloat(r) {
			return commonNumeric(l, r), nil
		}
		if v.Op.Kind == SLASH {
			if l.Name == "decimal" || r.Name == "decimal" {
				return TDecimal, nil
			}
			return TRational, nil
		}
		if v.Op.Kind == POWER {
			if l.Name == "decimal" {
				return TDecimal, nil
			}
			return TRational, nil
		}
		return commonNumeric(l, r), nil
	case LESS, LESSEQ, GREATER, GREATEREQ:
		if (isFloat(l) && isExactNumeric(r)) || (isExactNumeric(l) && isFloat(r)) {
			return TAny, c.err(v.Op, "SAGA-T170", "exact and floating-point comparison requires an explicit conversion")
		}
		comparableVars := isTypeVar(l) && isTypeVar(r) && l.Name == r.Name && c.typeVarHasConstraint(l, "Comparable", "Numeric", "Number", "ExactNumeric", "Float")
		if !(isNumeric(l) && isNumeric(r)) && !(l.Name == "text" && r.Name == "text") && !comparableVars {
			return TAny, c.err(v.Op, "SAGA-T103", "comparison requires numbers, text, or a Comparable generic constraint")
		}
		return TBool, nil
	case EQEQ, BANGEQ:
		if (isFloat(l) && isExactNumeric(r)) || (isExactNumeric(l) && isFloat(r)) {
			return TAny, c.err(v.Op, "SAGA-T170", "exact and floating-point equality requires an explicit conversion")
		}
		if !(isNumeric(l) && isNumeric(r)) && !c.assignable(l, r) && !c.assignable(r, l) {
			return TAny, c.err(v.Op, "SAGA-T103", "equality operands are incompatible")
		}
		return TBool, nil
	}
	return TAny, c.err(v.Op, "SAGA-T103", "unsupported operator")
}
func (c *Checker) checkMember(v *Member, expected *Type) (Type, error) {
	t, e := c.checkExpr(v.Target, nil)
	if e != nil {
		return TAny, e
	}
	if t.Name == "any" {
		return TAny, nil
	}
	if isTypeVar(t) {
		param := strings.TrimPrefix(t.Name, "$")
		for _, constraint := range c.CurrentConstraints[param] {
			name := objectTypeName(constraint)
			ci := c.Classes[name]
			if ci == nil || !ci.Interface {
				continue
			}
			if m, ok := ci.Methods[v.Name]; ok {
				ps := append([]Type{}, m.Params...)
				r := m.Ret
				rewrite := func(q Type) Type {
					if strings.HasPrefix(q.Name, "assoc:$Self.") {
						return Type{Name: "assoc:$" + param + "." + strings.TrimPrefix(q.Name, "assoc:$Self.")}
					}
					o := Type{Name: q.Name}
					for _, a := range q.Args {
						o.Args = append(o.Args, a)
					}
					if q.Result != nil {
						z := *q.Result
						o.Result = &z
					}
					return o
				}
				for j := range ps {
					ps[j] = rewrite(ps[j])
				}
				r = rewrite(r)
				if m.Decl != nil && m.Decl.Async {
					r = futureT(r)
				}
				return fnT(ps, r), nil
			}
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "generic type parameter has no constrained member "+v.Name)
	}
	if strings.HasPrefix(t.Name, "srcmodule:") {
		bind := strings.TrimPrefix(t.Name, "srcmodule:")
		m, ok := c.SourceModules[bind]
		if !ok {
			return TAny, c.err(v.Tok, "SAGA-T106", "unknown source module "+bind)
		}
		mt, ok := m.Members[v.Name]
		if !ok {
			msg := "module member is not public or does not exist: " + bind + "." + v.Name
			candidates := map[string]bool{}
			for name := range m.Members {
				candidates[name] = true
			}
			if suggestion := nearestSagaName(v.Name, candidates); suggestion != "" {
				msg += "; did you mean `" + bind + "." + suggestion + "`?"
			}
			return TAny, c.err(v.Tok, "SAGA-T106", msg)
		}
		return mt, nil
	}
	if strings.HasPrefix(t.Name, "enumtype:") {
		n := strings.TrimPrefix(t.Name, "enumtype:")
		if c.Enums[n][v.Name] {
			ps := c.EnumPayloads[n][v.Name]
			params := c.EnumTypeParams[n]
			retArgs := []Type{}
			for _, name := range params {
				retArgs = append(retArgs, typeVar(name))
			}
			result := objectT(n, retArgs...)
			if n == "Option" {
				result = optionT(typeVar("T"))
			}
			if n == "Result" {
				result = resultT(typeVar("T"), typeVar("E"))
			}
			if len(ps) > 0 {
				return fnT(ps, result), nil
			}
			if len(params) == 0 {
				return result, nil
			}
			if expected != nil && expected.Name == result.Name && len(expected.Args) == len(params) {
				return *expected, nil
			}
			return TAny, c.err(v.Tok, "SAGA-T113", "cannot infer generic enum variant "+n+"."+v.Name+"; add a "+n+"[...] type annotation")
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown enum variant "+n+"."+v.Name)
	}
	if t.Name == "module:sys" {
		switch v.Name {
		case "args":
			return fnT([]Type{}, listT(TText)), nil
		case "version", "platform", "arch":
			return fnT([]Type{}, TText), nil
		case "cpu_count", "page_size":
			return fnT([]Type{}, TInt), nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown sys member "+v.Name)
	}
	if t.Name == "module:compiler" {
		switch v.Name {
		case "build", "self_build":
			return fnT([]Type{TText, TText}, TText), nil
		case "check":
			return fnT([]Type{TText}, TBool), nil
		case "version":
			return fnT([]Type{}, TText), nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown compiler member "+v.Name)
	}
	if t.Name == "module:task" {
		switch v.Name {
		case "spawn":
			return fnT([]Type{TAny}, futureT(TAny)), nil
		case "await":
			return fnT([]Type{futureT(TAny)}, TAny), nil
		case "all":
			return fnT([]Type{listT(futureT(TAny))}, listT(TAny)), nil
		case "pool":
			return fnT([]Type{TInt}, Type{Name: "native:task_pool"}), nil
		case "submit":
			return fnT([]Type{Type{Name: "native:task_pool"}, TAny}, futureT(TAny)), nil
		case "shutdown":
			return fnT([]Type{Type{Name: "native:task_pool"}}, TUnit), nil
		case "parallel_map":
			return fnT([]Type{TAny, listT(TAny), TInt}, listT(TAny)), nil
		case "await_timeout":
			return fnT([]Type{futureT(TAny), TInt}, resultT(TAny, TError)), nil
		case "cancel":
			return fnT([]Type{futureT(TAny)}, TUnit), nil
		case "cancelled":
			return fnT([]Type{futureT(TAny)}, TBool), nil
		case "channel", "stream":
			return fnT([]Type{TInt}, channelT(TAny)), nil
		case "send":
			return fnT([]Type{channelT(TAny), TAny}, TUnit), nil
		case "recv":
			return fnT([]Type{channelT(TAny)}, optionT(TAny)), nil
		case "close":
			return fnT([]Type{channelT(TAny)}, TUnit), nil
		case "actor":
			return fnT([]Type{TAny}, actorT(TAny, TAny)), nil
		case "ask":
			return fnT([]Type{actorT(TAny, TAny), TAny}, futureT(TAny)), nil
		case "stop":
			return fnT([]Type{actorT(TAny, TAny)}, TUnit), nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown task member "+v.Name)
	}
	if t.Name == "module:embedded" {
		switch v.Name {
		case "mmio_read8", "mmio_read16", "mmio_read32":
			return fnT([]Type{TUInt32}, TUInt32), nil
		case "mmio_write8", "mmio_write16", "mmio_write32", "mmio_set_bits32", "mmio_clear_bits32":
			return fnT([]Type{TUInt32, TUInt32}, TUnit), nil
		case "irq_enable", "irq_disable", "barrier", "wfi":
			return fnT([]Type{}, TUnit), nil
		case "nvic_enable", "nvic_disable":
			return fnT([]Type{TUInt32}, TUnit), nil
		case "nvic_set_priority":
			return fnT([]Type{TUInt32, TUInt32}, TUnit), nil
		case "critical_enter", "ticks":
			return fnT([]Type{}, TUInt32), nil
		case "critical_exit", "delay_ticks", "panic":
			return fnT([]Type{TUInt32}, TUnit), nil
		case "os_tick", "yield", "system_reset":
			return fnT([]Type{}, TUnit), nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown embedded member "+v.Name)
	}
	if t.Name == "module:ffi" {
		if v.Name == "available" {
			return fnT([]Type{}, TBool), nil
		}
		if c.UnsafeDepth == 0 {
			return TAny, c.err(v.Tok, "SAGA-T178", "FFI functions are only available inside unsafe { ... }")
		}
		switch v.Name {
		case "call_i64":
			return fnT([]Type{TText, TText, listT(TInt)}, TInt), nil
		case "call_f64":
			return fnT([]Type{TText, TText, listT(TFloat64)}, TFloat64), nil
		case "profile":
			return fnT([]Type{}, TText), nil
		case "ptr_null":
			return fnT([]Type{}, Type{Name: "native:ffi_ptr"}), nil
		case "alloc":
			return fnT([]Type{TInt}, Type{Name: "native:ffi_ptr"}), nil
		case "free":
			return fnT([]Type{Type{Name: "native:ffi_ptr"}}, TUnit), nil
		case "ptr_add":
			return fnT([]Type{Type{Name: "native:ffi_ptr"}, TInt}, Type{Name: "native:ffi_ptr"}), nil
		case "ptr_address":
			return fnT([]Type{Type{Name: "native:ffi_ptr"}}, TInt), nil
		case "borrow", "adopt":
			return fnT([]Type{Type{Name: "native:ffi_ptr"}, TInt}, Type{Name: "native:ffi_ptr"}), nil
		case "layout":
			return fnT([]Type{listT(TText)}, Type{Name: "native:ffi_layout"}), nil
		case "layout_size":
			return fnT([]Type{Type{Name: "native:ffi_layout"}}, TInt), nil
		case "struct_alloc":
			return fnT([]Type{Type{Name: "native:ffi_layout"}}, Type{Name: "native:ffi_ptr"}), nil
		case "struct_get":
			return fnT([]Type{Type{Name: "native:ffi_layout"}, Type{Name: "native:ffi_ptr"}, TText}, TAny), nil
		case "struct_set":
			return fnT([]Type{Type{Name: "native:ffi_layout"}, Type{Name: "native:ffi_ptr"}, TText, TAny}, TUnit), nil
		case "load":
			return fnT([]Type{Type{Name: "native:ffi_ptr"}, TInt, TText}, TAny), nil
		case "store":
			return fnT([]Type{Type{Name: "native:ffi_ptr"}, TInt, TText, TAny}, TUnit), nil
		case "call":
			return fnT([]Type{TText, TText, TText, listT(TText), listT(TAny)}, TAny), nil
		case "callback":
			return fnT([]Type{TAny, TText, listT(TText)}, Type{Name: "native:ffi_callback"}), nil
		case "callback_ptr":
			return fnT([]Type{Type{Name: "native:ffi_callback"}}, Type{Name: "native:ffi_ptr"}), nil
		case "callback_close":
			return fnT([]Type{Type{Name: "native:ffi_callback"}}, TUnit), nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown ffi member "+v.Name)
	}
	if t.Name == "module:machine" {
		pid := Type{Name: "native:machine_pid"}
		pid2 := Type{Name: "native:machine_pid2"}
		alphaBeta := Type{Name: "native:machine_alpha_beta"}
		biquad := Type{Name: "native:machine_biquad"}
		deadlineBudget := Type{Name: "native:machine_deadline_budget"}
		controlGuard := Type{Name: "native:machine_control_guard"}
		focCurrent := Type{Name: "native:machine_foc_current"}
		encoderUnified := Type{Name: "native:machine_encoder_unified"}
		rls2 := Type{Name: "native:machine_rls2"}
		mpc2 := Type{Name: "native:machine_mpc2"}
		dob := Type{Name: "native:machine_dob"}
		axisSync := Type{Name: "native:machine_axis_sync"}
		profile := Type{Name: "native:machine_profile"}
		watchdog := Type{Name: "native:machine_watchdog"}
		safety := Type{Name: "native:machine_safety"}
		cycle := Type{Name: "native:machine_cycle"}
		i2c := Type{Name: "native:machine_i2c"}
		spi := Type{Name: "native:machine_spi"}
		uart := Type{Name: "native:machine_uart"}
		can := Type{Name: "native:machine_can"}
		ethercat := Type{Name: "native:machine_ethercat"}
		pwm := Type{Name: "native:machine_pwm"}
		servo := Type{Name: "native:machine_servo"}
		encoder := Type{Name: "native:machine_encoder"}
		motor := Type{Name: "native:machine_motor"}
		scurve := Type{Name: "native:machine_scurve"}
		axis := Type{Name: "native:machine_axis"}
		modbusRTU := Type{Name: "native:machine_modbus_rtu"}
		modbusTCP := Type{Name: "native:machine_modbus_tcp"}
		switch v.Name {
		case "timing_class":
			return fnT([]Type{}, TText), nil
		case "hard_realtime_available":
			return fnT([]Type{}, TBool), nil
		case "monotonic_ns":
			return fnT([]Type{}, TInt), nil
		case "bytes_from_hex":
			return fnT([]Type{TText}, TBytes), nil
		case "bytes_to_hex":
			return fnT([]Type{TBytes}, TText), nil
		case "pid":
			return fnT([]Type{TDecimal, TDecimal, TDecimal, TDecimal, TDecimal}, pid), nil
		case "pid_step":
			return fnT([]Type{pid, TDecimal, TDecimal, TDecimal}, TDecimal), nil
		case "pid_reset":
			return fnT([]Type{pid}, TUnit), nil
		case "pid_integral_limits":
			return fnT([]Type{pid, TDecimal, TDecimal}, TUnit), nil
		case "pid2":
			return fnT([]Type{TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal}, pid2), nil
		case "pid2_step":
			return fnT([]Type{pid2, TDecimal, TDecimal, TDecimal, TDecimal}, TDecimal), nil
		case "pid2_reset":
			return fnT([]Type{pid2}, TUnit), nil
		case "motor_feedforward":
			return fnT([]Type{TDecimal, TDecimal, TDecimal, TDecimal, TDecimal}, TDecimal), nil
		case "alpha_beta":
			return fnT([]Type{TDecimal, TDecimal, TDecimal, TDecimal}, alphaBeta), nil
		case "alpha_beta_step":
			return fnT([]Type{alphaBeta, TDecimal, TDecimal}, listT(TDecimal)), nil
		case "alpha_beta_reset":
			return fnT([]Type{alphaBeta, TDecimal, TDecimal}, TUnit), nil
		case "notch":
			return fnT([]Type{TDecimal, TDecimal, TDecimal}, biquad), nil
		case "filter_step":
			return fnT([]Type{biquad, TDecimal}, TDecimal), nil
		case "filter_reset":
			return fnT([]Type{biquad}, TUnit), nil
		case "control_guard":
			return fnT([]Type{TInt, TInt, TInt, TInt}, controlGuard), nil
		case "control_guard_begin":
			return fnT([]Type{controlGuard, TInt, TInt}, TBool), nil
		case "control_guard_end":
			return fnT([]Type{controlGuard, TInt}, TBool), nil
		case "control_guard_ok":
			return fnT([]Type{controlGuard}, TBool), nil
		case "control_guard_stats_json":
			return fnT([]Type{controlGuard}, TText), nil
		case "control_guard_reset":
			return fnT([]Type{controlGuard}, TUnit), nil
		case "deadline_budget":
			return fnT([]Type{TInt, TInt}, deadlineBudget), nil
		case "budget_begin", "budget_reset":
			return fnT([]Type{deadlineBudget}, TUnit), nil
		case "budget_end":
			return fnT([]Type{deadlineBudget}, TBool), nil
		case "budget_stats_json":
			return fnT([]Type{deadlineBudget}, TText), nil
		case "clarke", "park", "inverse_park", "svpwm":
			return fnT([]Type{TDecimal, TDecimal, TDecimal}, listT(TDecimal)), nil
		case "foc_current":
			return fnT([]Type{TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal}, focCurrent), nil
		case "foc_step":
			return fnT([]Type{focCurrent, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal}, TUnit), nil
		case "foc_reset":
			return fnT([]Type{focCurrent}, TUnit), nil
		case "foc_id", "foc_iq", "foc_vd", "foc_vq":
			return fnT([]Type{focCurrent}, TDecimal), nil
		case "foc_duty":
			return fnT([]Type{focCurrent, TInt}, TDecimal), nil
		case "encoder_integrated":
			return fnT([]Type{TInt, TDecimal, TInt, TInt, TDecimal}, encoderUnified), nil
		case "encoder_sample":
			return fnT([]Type{encoderUnified, TInt, TInt}, TUnit), nil
		case "encoder_align_absolute":
			return fnT([]Type{encoderUnified, TInt, TDecimal}, TUnit), nil
		case "encoder_position_deg", "encoder_velocity_deg_s", "encoder_integrated_velocity_rpm":
			return fnT([]Type{encoderUnified}, TDecimal), nil
		case "rls2":
			return fnT([]Type{TDecimal, TDecimal}, rls2), nil
		case "rls2_update":
			return fnT([]Type{rls2, TDecimal, TDecimal, TDecimal}, TUnit), nil
		case "rls2_theta0", "rls2_theta1", "rls2_error":
			return fnT([]Type{rls2}, TDecimal), nil
		case "mpc2":
			return fnT([]Type{TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TInt, TDecimal, TDecimal}, mpc2), nil
		case "mpc2_step":
			return fnT([]Type{mpc2, TDecimal, TDecimal, TDecimal, TDecimal}, TDecimal), nil
		case "mpc2_reset":
			return fnT([]Type{mpc2}, TUnit), nil
		case "disturbance_observer":
			return fnT([]Type{TDecimal, TDecimal, TDecimal}, dob), nil
		case "disturbance_step":
			return fnT([]Type{dob, TDecimal, TDecimal, TDecimal}, TDecimal), nil
		case "disturbance_reset":
			return fnT([]Type{dob, TDecimal}, TUnit), nil
		case "friction_compensation":
			return fnT([]Type{TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal}, TDecimal), nil
		case "axis_sync":
			return fnT([]Type{TInt, TDecimal, TDecimal, TDecimal}, axisSync), nil
		case "axis_sync_config":
			return fnT([]Type{axisSync, TInt, TDecimal, TDecimal}, TUnit), nil
		case "axis_sync_begin":
			return fnT([]Type{axisSync, TDecimal}, TUnit), nil
		case "axis_sync_correction":
			return fnT([]Type{axisSync, TInt, TDecimal}, TDecimal), nil
		case "axis_sync_error":
			return fnT([]Type{axisSync, TInt}, TDecimal), nil
		case "axis_sync_ok":
			return fnT([]Type{axisSync}, TBool), nil
		case "ethercat_datagram":
			return fnT([]Type{TText, TInt, TInt, TInt, TBytes, TInt, TBool}, TBytes), nil
		case "ethercat_frame":
			return fnT([]Type{TBytes}, TBytes), nil
		case "ethercat_lrw":
			return fnT([]Type{TInt, TInt, TBytes}, TBytes), nil
		case "ethercat_first_datagram_json":
			return fnT([]Type{TBytes}, TText), nil
		case "allocation_free_profile_json":
			return fnT([]Type{}, TText), nil
		case "slew":
			return fnT([]Type{TDecimal, TDecimal, TDecimal, TDecimal}, TDecimal), nil
		case "low_pass":
			return fnT([]Type{TDecimal, TDecimal, TDecimal}, TDecimal), nil
		case "profile":
			return fnT([]Type{TDecimal, TDecimal, TDecimal, TDecimal, TDecimal}, profile), nil
		case "profile_step":
			return fnT([]Type{profile, TDecimal}, TDecimal), nil
		case "profile_velocity":
			return fnT([]Type{profile}, TDecimal), nil
		case "profile_done":
			return fnT([]Type{profile}, TBool), nil
		case "profile_retarget":
			return fnT([]Type{profile, TDecimal}, TUnit), nil
		case "watchdog":
			return fnT([]Type{TInt}, watchdog), nil
		case "watchdog_feed":
			return fnT([]Type{watchdog}, TUnit), nil
		case "watchdog_expired":
			return fnT([]Type{watchdog}, TBool), nil
		case "watchdog_remaining_ms":
			return fnT([]Type{watchdog}, TInt), nil
		case "watchdog_check":
			return fnT([]Type{watchdog, safety, TText}, TBool), nil
		case "safety_latch":
			return fnT([]Type{}, safety), nil
		case "safety_trip":
			return fnT([]Type{safety, TText}, TUnit), nil
		case "safety_clear":
			return fnT([]Type{safety}, TUnit), nil
		case "safety_tripped":
			return fnT([]Type{safety}, TBool), nil
		case "safety_reason":
			return fnT([]Type{safety}, TText), nil
		case "safety_check":
			return fnT([]Type{safety, TBool, TText}, TBool), nil
		case "cycle", "cyclic_clock":
			return fnT([]Type{TInt}, cycle), nil
		case "cycle_wait":
			return fnT([]Type{cycle}, TUnit), nil
		case "cycle_wait_due":
			return fnT([]Type{cycle}, TInt), nil
		case "cycle_stats_json":
			return fnT([]Type{cycle}, TText), nil
		case "cycle_overruns", "cycle_jitter_us":
			return fnT([]Type{cycle}, TInt), nil
		case "servo_duty":
			return fnT([]Type{TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal}, TDecimal), nil
		case "i2c_open":
			return fnT([]Type{TText, TInt}, i2c), nil
		case "i2c_write":
			return fnT([]Type{i2c, TBytes}, TUnit), nil
		case "i2c_read":
			return fnT([]Type{i2c, TInt}, TBytes), nil
		case "i2c_write_read":
			return fnT([]Type{i2c, TBytes, TInt}, TBytes), nil
		case "i2c_close":
			return fnT([]Type{i2c}, TUnit), nil
		case "spi_open":
			return fnT([]Type{TText, TInt, TInt, TInt}, spi), nil
		case "spi_transfer":
			return fnT([]Type{spi, TBytes}, TBytes), nil
		case "spi_close":
			return fnT([]Type{spi}, TUnit), nil
		case "uart_open":
			return fnT([]Type{TText, TInt, TInt}, uart), nil
		case "uart_write":
			return fnT([]Type{uart, TBytes}, TUnit), nil
		case "uart_read":
			return fnT([]Type{uart, TInt}, TBytes), nil
		case "uart_close":
			return fnT([]Type{uart}, TUnit), nil
		case "can_open":
			return fnT([]Type{TText, TBool}, can), nil
		case "can_send":
			return fnT([]Type{can, TInt, TBytes}, TUnit), nil
		case "can_recv":
			return fnT([]Type{can, TInt}, TText), nil
		case "can_timestamping":
			return fnT([]Type{can, TBool}, TUnit), nil
		case "canfd_send":
			return fnT([]Type{can, TInt, TBytes, TBool}, TUnit), nil
		case "canfd_recv":
			return fnT([]Type{can, TInt}, TText), nil
		case "can_close":
			return fnT([]Type{can}, TUnit), nil
		case "ethercat_open":
			return fnT([]Type{TText, TBytes, TBool}, ethercat), nil
		case "ethercat_exchange":
			return fnT([]Type{ethercat, TBytes, TInt}, TText), nil
		case "ethercat_close":
			return fnT([]Type{ethercat}, TUnit), nil
		case "pwm_open":
			return fnT([]Type{TInt, TInt, TInt}, pwm), nil
		case "pwm_write":
			return fnT([]Type{pwm, TDecimal}, TUnit), nil
		case "pwm_enable", "pwm_disable", "pwm_close":
			return fnT([]Type{pwm}, TUnit), nil
		case "servo":
			return fnT([]Type{pwm, TDecimal, TDecimal, TDecimal, TDecimal}, servo), nil
		case "servo_write":
			return fnT([]Type{servo, TDecimal}, TUnit), nil
		case "servo_guard":
			return fnT([]Type{servo, safety}, TUnit), nil
		case "encoder":
			return fnT([]Type{TInt, TDecimal}, encoder), nil
		case "encoder_wrap":
			return fnT([]Type{encoder, TInt}, TUnit), nil
		case "encoder_unwrapped_count":
			return fnT([]Type{encoder}, TInt), nil
		case "encoder_update":
			return fnT([]Type{encoder, TInt, TInt}, TUnit), nil
		case "encoder_update_now":
			return fnT([]Type{encoder, TInt}, TUnit), nil
		case "encoder_position_degrees", "encoder_velocity_rpm":
			return fnT([]Type{encoder}, TDecimal), nil
		case "encoder_reset":
			return fnT([]Type{encoder, TInt}, TUnit), nil
		case "motor":
			return fnT([]Type{pwm, pwm, TDecimal, safety}, motor), nil
		case "motor_write":
			return fnT([]Type{motor, TDecimal}, TUnit), nil
		case "motor_stop":
			return fnT([]Type{motor}, TUnit), nil
		case "motor_command":
			return fnT([]Type{motor}, TDecimal), nil
		case "s_curve":
			return fnT([]Type{TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal}, scurve), nil
		case "s_curve_step":
			return fnT([]Type{scurve, TDecimal}, TDecimal), nil
		case "s_curve_velocity", "s_curve_acceleration":
			return fnT([]Type{scurve}, TDecimal), nil
		case "s_curve_done":
			return fnT([]Type{scurve}, TBool), nil
		case "s_curve_retarget":
			return fnT([]Type{scurve, TDecimal}, TUnit), nil
		case "axis":
			return fnT([]Type{TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, safety}, axis), nil
		case "axis_target":
			return fnT([]Type{axis, TDecimal}, TUnit), nil
		case "axis_step":
			return fnT([]Type{axis, TDecimal, TDecimal}, TDecimal), nil
		case "axis_command", "axis_planned_position":
			return fnT([]Type{axis}, TDecimal), nil
		case "axis_done":
			return fnT([]Type{axis, TDecimal}, TBool), nil
		case "modbus_crc16":
			return fnT([]Type{TBytes}, TInt), nil
		case "modbus_rtu_open":
			return fnT([]Type{TText, TInt, TInt, TInt}, modbusRTU), nil
		case "modbus_tcp_open":
			return fnT([]Type{TText, TInt, TInt, TInt}, modbusTCP), nil
		case "modbus_read_holding", "modbus_read_input":
			return fnT([]Type{TAny, TInt, TInt}, listT(TInt)), nil
		case "modbus_read_coils":
			return fnT([]Type{TAny, TInt, TInt}, listT(TBool)), nil
		case "modbus_write_register":
			return fnT([]Type{TAny, TInt, TInt}, TUnit), nil
		case "modbus_write_registers":
			return fnT([]Type{TAny, TInt, listT(TInt)}, TUnit), nil
		case "modbus_write_coil":
			return fnT([]Type{TAny, TInt, TBool}, TUnit), nil
		case "modbus_close":
			return fnT([]Type{TAny}, TUnit), nil
		case "iio_read":
			return fnT([]Type{TText, TDecimal}, TDecimal), nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown machine member "+v.Name)
	}

	if t.Name == "module:drone" {
		estimator := Type{Name: "native:drone_attitude_estimator"}
		attitude := Type{Name: "native:drone_attitude_controller"}
		quaternion := Type{Name: "native:drone_quaternion_controller"}
		rate := Type{Name: "native:drone_rate_controller"}
		position := Type{Name: "native:drone_position_controller"}
		mixer := Type{Name: "native:drone_mixer"}
		fence := Type{Name: "native:drone_geofence"}
		mission := Type{Name: "native:drone_mission"}
		flight := Type{Name: "native:drone_flight_manager"}
		rtl := Type{Name: "native:drone_rtl"}
		mavstream := Type{Name: "native:drone_mavlink_stream"}
		trajectory := Type{Name: "native:drone_trajectory"}
		allocator := Type{Name: "native:drone_allocator"}
		linkmon := Type{Name: "native:drone_link_monitor"}
		safety := Type{Name: "native:machine_safety"}
		dec3 := listT(TDecimal)
		switch v.Name {
		case "profile":
			return fnT([]Type{}, TText), nil
		case "hard_realtime_available":
			return fnT([]Type{}, TBool), nil
		case "attitude_estimator":
			return fnT([]Type{TDecimal}, estimator), nil
		case "attitude_update":
			return fnT([]Type{estimator, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal}, dec3), nil
		case "attitude_rpy":
			return fnT([]Type{estimator}, dec3), nil
		case "attitude_healthy":
			return fnT([]Type{estimator}, TBool), nil
		case "attitude_controller":
			return fnT([]Type{TDecimal, TDecimal, TDecimal, TDecimal}, attitude), nil
		case "attitude_step":
			return fnT([]Type{attitude, dec3, dec3}, dec3), nil
		case "quaternion_from_rpy":
			return fnT([]Type{TDecimal, TDecimal, TDecimal}, listT(TDecimal)), nil
		case "quaternion_controller":
			return fnT([]Type{TDecimal, TDecimal, TDecimal, TDecimal}, quaternion), nil
		case "quaternion_step":
			return fnT([]Type{quaternion, listT(TDecimal), listT(TDecimal)}, dec3), nil
		case "rate_controller":
			return fnT([]Type{TDecimal, TDecimal, TDecimal, TDecimal}, rate), nil
		case "rate_step":
			return fnT([]Type{rate, dec3, dec3, TDecimal}, dec3), nil
		case "rate_reset":
			return fnT([]Type{rate}, TUnit), nil
		case "position_controller":
			return fnT([]Type{TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal}, position), nil
		case "position_step":
			return fnT([]Type{position, dec3, dec3, dec3, dec3, TDecimal}, dec3), nil
		case "quad_x_mixer":
			return fnT([]Type{TDecimal, TDecimal}, mixer), nil
		case "mix_quad_x":
			return fnT([]Type{mixer, TDecimal, TDecimal, TDecimal, TDecimal}, dec3), nil
		case "geofence":
			return fnT([]Type{TDecimal, TDecimal, TDecimal, TDecimal, TDecimal}, fence), nil
		case "geofence_contains":
			return fnT([]Type{fence, TDecimal, TDecimal, TDecimal}, TBool), nil
		case "geofence_distance_m":
			return fnT([]Type{fence, TDecimal, TDecimal}, TDecimal), nil
		case "geofence_predict_breach":
			return fnT([]Type{fence, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal}, TBool), nil
		case "mission":
			return fnT([]Type{}, mission), nil
		case "mission_add":
			return fnT([]Type{mission, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal}, TUnit), nil
		case "mission_reset":
			return fnT([]Type{mission}, TUnit), nil
		case "mission_update":
			return fnT([]Type{mission, TDecimal, TDecimal, TDecimal, TDecimal}, TText), nil
		case "mission_target_json":
			return fnT([]Type{mission}, TText), nil
		case "mission_complete":
			return fnT([]Type{mission}, TBool), nil
		case "flight_manager":
			return fnT([]Type{safety, TDecimal}, flight), nil
		case "health_update":
			return fnT([]Type{flight, TBool, TBool, TDecimal, TBool, TBool, TBool}, TUnit), nil
		case "prearm_reason":
			return fnT([]Type{flight, TBool}, TText), nil
		case "arm":
			return fnT([]Type{flight, TBool}, TUnit), nil
		case "disarm":
			return fnT([]Type{flight}, TUnit), nil
		case "set_mode":
			return fnT([]Type{flight, TText}, TUnit), nil
		case "flight_mode":
			return fnT([]Type{flight}, TText), nil
		case "flight_state":
			return fnT([]Type{flight}, TText), nil
		case "flight_allowed", "control_allowed":
			return fnT([]Type{flight}, TBool), nil
		case "rtl":
			return fnT([]Type{TDecimal, TDecimal, TDecimal, TDecimal, TDecimal}, rtl), nil
		case "rtl_target_json":
			return fnT([]Type{rtl, TDecimal, TDecimal, TDecimal}, TText), nil
		case "landing_vertical_velocity":
			return fnT([]Type{TDecimal, TDecimal, TDecimal, TDecimal}, TDecimal), nil
		case "dronecan_crc16":
			return fnT([]Type{TBytes}, TInt), nil
		case "dronecan_single_frame_json":
			return fnT([]Type{TInt, TInt, TInt, TInt, TBytes}, TText), nil
		case "dronecan_multiframe_json":
			return fnT([]Type{TInt, TInt, TInt, TInt, TBytes, TBytes}, TText), nil
		case "dronecan_decode_json":
			return fnT([]Type{TInt, TBytes}, TText), nil
		case "mavlink_encode":
			return fnT([]Type{TInt, TInt, TBytes, TInt, TInt, TInt}, TBytes), nil
		case "mavlink_decode_json":
			return fnT([]Type{TBytes, TInt}, TText), nil
		case "mavlink_encode_signed":
			return fnT([]Type{TInt, TInt, TBytes, TInt, TInt, TInt, TBytes, TInt, TInt}, TBytes), nil
		case "mavlink_verify_signed_json":
			return fnT([]Type{TBytes, TInt, TBytes, TInt}, TText), nil
		case "mavlink_signing_timestamp":
			return fnT([]Type{}, TInt), nil
		case "mavlink_heartbeat":
			return fnT([]Type{TInt, TInt, TInt, TInt, TInt, TInt, TInt, TInt}, TBytes), nil
		case "mavlink_set_attitude_target":
			return fnT([]Type{TInt, TInt, TInt, TInt, TInt, TInt, listT(TDecimal), listT(TDecimal), TDecimal, TInt}, TBytes), nil
		case "mavlink_set_position_target_local_ned":
			return fnT([]Type{TInt, TInt, TInt, TInt, TInt, TInt, TInt, listT(TDecimal), listT(TDecimal), listT(TDecimal), TDecimal, TDecimal, TInt}, TBytes), nil
		case "mavlink_command_long":
			return fnT([]Type{TInt, TInt, TInt, TInt, TInt, TInt, TInt, listT(TDecimal)}, TBytes), nil
		case "mavlink_common_decode_json":
			return fnT([]Type{TBytes}, TText), nil
		case "mavlink_stream":
			return fnT([]Type{}, mavstream), nil
		case "mavlink_stream_feed_json":
			return fnT([]Type{mavstream, TBytes}, TText), nil
		case "mavlink_stream_stats_json":
			return fnT([]Type{mavstream}, TText), nil
		case "dshot_frame":
			return fnT([]Type{TDecimal, TBool}, TInt), nil
		case "pwm_esc_duty":
			return fnT([]Type{TDecimal, TDecimal, TDecimal, TDecimal}, TDecimal), nil
		case "trajectory3d":
			return fnT([]Type{dec3, dec3, TDecimal, TDecimal, TDecimal}, trajectory), nil
		case "trajectory_retarget":
			return fnT([]Type{trajectory, dec3}, TUnit), nil
		case "trajectory_step_json":
			return fnT([]Type{trajectory, TDecimal}, TText), nil
		case "trajectory_done":
			return fnT([]Type{trajectory}, TBool), nil
		case "quad_x_allocator":
			return fnT([]Type{TDecimal, TDecimal}, allocator), nil
		case "allocator":
			return fnT([]Type{listT(listT(TDecimal)), TDecimal, TDecimal}, allocator), nil
		case "allocator_disable":
			return fnT([]Type{allocator, listT(TInt)}, TUnit), nil
		case "allocate":
			return fnT([]Type{allocator, listT(TDecimal)}, listT(TDecimal)), nil
		case "allocation_report_json":
			return fnT([]Type{allocator, listT(TDecimal)}, TText), nil
		case "link_monitor":
			return fnT([]Type{TDecimal}, linkmon), nil
		case "link_observe":
			return fnT([]Type{linkmon, TInt, TDecimal}, TUnit), nil
		case "link_stats_json":
			return fnT([]Type{linkmon}, TText), nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown drone member "+v.Name)
	}
	if t.Name == "module:vision" {
		tracker := Type{Name: "native:vision_tracker"}
		camera := Type{Name: "native:vision_camera"}
		switch v.Name {
		case "nms_json":
			return fnT([]Type{TText, TDecimal}, TText), nil
		case "tracker":
			return fnT([]Type{TDecimal, TInt}, tracker), nil
		case "track_json":
			return fnT([]Type{tracker, TText}, TText), nil
		case "camera":
			return fnT([]Type{TDecimal, TDecimal, TDecimal, TDecimal}, camera), nil
		case "pixel_to_bearing":
			return fnT([]Type{camera, TDecimal, TDecimal}, listT(TDecimal)), nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown vision member "+v.Name)
	}
	if t.Name == "module:io" {
		switch v.Name {
		case "read_text":
			return fnT([]Type{TText}, TText), nil
		case "write_text":
			return fnT([]Type{TText, TText}, TUnit), nil
		case "exists":
			return fnT([]Type{TText}, TBool), nil
		case "remove":
			return fnT([]Type{TText}, TUnit), nil
		case "list":
			return fnT([]Type{TText}, listT(TText)), nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown io member "+v.Name)
	}
	if t.Name == "module:json" {
		switch v.Name {
		case "encode":
			return fnT([]Type{TAny}, TText), nil
		case "decode":
			return fnT([]Type{TText}, TAny), nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown json member "+v.Name)
	}
	if t.Name == "module:time" {
		switch v.Name {
		case "unix_ms":
			return fnT([]Type{}, TInt), nil
		case "sleep_ms":
			return fnT([]Type{TInt}, TUnit), nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown time member "+v.Name)
	}
	if t.Name == "module:math" {
		switch v.Name {
		case "pi":
			return fnT([]Type{}, TDecimal), nil
		case "sin", "cos", "tan":
			return fnT([]Type{TDecimal}, TDecimal), nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown math member "+v.Name)
	}
	if t.Name == "module:random" {
		switch v.Name {
		case "int":
			return fnT([]Type{TInt, TInt}, TInt), nil
		case "decimal":
			return fnT([]Type{}, TDecimal), nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown random member "+v.Name)
	}
	if t.Name == "module:crypto" {
		switch v.Name {
		case "sha256", "sha512", "password_hash":
			return fnT([]Type{TText}, TText), nil
		case "hmac_sha256", "constant_equal", "password_verify":
			if v.Name == "constant_equal" || v.Name == "password_verify" {
				return fnT([]Type{TText, TText}, TBool), nil
			}
			return fnT([]Type{TText, TText}, TText), nil
		case "random_hex":
			return fnT([]Type{TInt}, TText), nil
		case "aes_gcm_encrypt", "aes_gcm_decrypt":
			return fnT([]Type{TText, TText, TText}, resultT(TText, TText)), nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown crypto member "+v.Name)
	}
	if t.Name == "module:security" {
		switch v.Name {
		case "sha512", "password_hash":
			return fnT([]Type{TText}, TText), nil
		case "hmac_sha256":
			return fnT([]Type{TText, TText}, TText), nil
		case "constant_equal", "password_verify":
			return fnT([]Type{TText, TText}, TBool), nil
		case "random_hex":
			return fnT([]Type{TInt}, TText), nil
		case "file_sha256", "certificate_info":
			return fnT([]Type{TText}, resultT(TText, TText)), nil
		case "ip_valid":
			return fnT([]Type{TText}, TBool), nil
		case "cidr_contains":
			return fnT([]Type{TText, TText}, resultT(TBool, TText)), nil
		case "tls_probe":
			return fnT([]Type{TText, TInt, TText, TText, TInt}, resultT(TText, TText)), nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown security member "+v.Name)
	}
	if t.Name == "module:net" {
		conn := Type{Name: "native:tcp_conn"}
		listener := Type{Name: "native:tcp_listener"}
		udp := Type{Name: "native:udp_socket"}
		switch v.Name {
		case "connect":
			return fnT([]Type{TText, TInt}, resultT(conn, TText)), nil
		case "listen":
			return fnT([]Type{TText, TInt}, resultT(listener, TText)), nil
		case "accept":
			return fnT([]Type{listener}, resultT(conn, TText)), nil
		case "send":
			return fnT([]Type{conn, TText}, resultT(TInt, TText)), nil
		case "recv":
			return fnT([]Type{conn, TInt}, resultT(TText, TText)), nil
		case "udp":
			return fnT([]Type{}, udp), nil
		case "udp_bind":
			return fnT([]Type{udp, TText, TInt}, TUnit), nil
		case "udp_send":
			return fnT([]Type{udp, TBytes, TText, TInt}, TInt), nil
		case "udp_receive":
			return fnT([]Type{udp, TInt}, TBytes), nil
		case "udp_receive_from_json":
			return fnT([]Type{udp, TInt}, TText), nil
		case "set_timeout_ms":
			return fnT([]Type{TAny, TInt}, TUnit), nil
		case "close":
			return fnT([]Type{TAny}, TUnit), nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown net member "+v.Name)
	}
	if t.Name == "module:http" {
		server := Type{Name: "native:http_server"}
		request := Type{Name: "native:http_request"}
		switch v.Name {
		case "get":
			return fnT([]Type{TText}, resultT(TText, TText)), nil
		case "post":
			return fnT([]Type{TText, TText, TText}, resultT(TText, TText)), nil
		case "status":
			return fnT([]Type{TText}, resultT(TInt, TText)), nil
		case "listen":
			return fnT([]Type{TText, TInt}, resultT(server, TText)), nil
		case "server_port":
			return fnT([]Type{server}, TInt), nil
		case "accept":
			return fnT([]Type{server}, resultT(request, TText)), nil
		case "request_method", "request_path", "request_body":
			return fnT([]Type{request}, TText), nil
		case "request_header", "request_query":
			return fnT([]Type{request, TText}, optionT(TText)), nil
		case "respond":
			return fnT([]Type{request, TInt, TText, TText}, resultT(TUnit, TText)), nil
		case "server_close":
			return fnT([]Type{server}, TUnit), nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown http member "+v.Name)
	}
	if t.Name == "module:app" {
		switch v.Name {
		case "host":
			return fnT([]Type{}, TText), nil
		case "capability", "operation_supported":
			return fnT([]Type{TText}, TBool), nil
		case "capabilities", "operations":
			return fnT([]Type{}, listT(TText)), nil
		case "invoke":
			return fnT([]Type{TText, TText}, resultT(TText, TText)), nil
		case "invoke_async":
			return fnT([]Type{TText, TText, TText}, resultT(TInt, TText)), nil
		case "cancel", "off":
			return fnT([]Type{TInt}, resultT(TUnit, TText)), nil
		case "on":
			return fnT([]Type{TText, TText}, resultT(TInt, TText)), nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown app member "+v.Name)
	}
	if t.Name == "module:web" {
		switch v.Name {
		case "escape", "url_encode":
			return fnT([]Type{TText}, TText), nil
		case "element":
			return fnT([]Type{TText, mapT(TText, TText), TText}, TText), nil
		case "document":
			return fnT([]Type{TText, TText}, TText), nil
		case "route":
			return fnT([]Type{TText, TText}, optionT(mapT(TText, TText))), nil
		case "query":
			return fnT([]Type{TText}, mapT(TText, TText)), nil
		case "browser_available":
			return fnT([]Type{}, TBool), nil
		case "capability":
			return fnT([]Type{TText}, TBool), nil
		case "exists", "query_exists":
			return fnT([]Type{TText}, resultT(TBool, TText)), nil
		case "query_count":
			return fnT([]Type{TText}, resultT(TInt, TText)), nil
		case "title":
			return fnT([]Type{}, resultT(TText, TText)), nil
		case "set_title":
			return fnT([]Type{TText}, resultT(TUnit, TText)), nil
		case "set_text", "set_html", "set_value", "append_html", "prepend_html", "add_class", "remove_class":
			return fnT([]Type{TText, TText}, resultT(TUnit, TText)), nil
		case "text", "html", "value":
			return fnT([]Type{TText}, resultT(TText, TText)), nil
		case "create":
			return fnT([]Type{TText, TText, TText}, resultT(TUnit, TText)), nil
		case "clear", "remove", "focus", "blur", "click", "scroll_into_view":
			return fnT([]Type{TText}, resultT(TUnit, TText)), nil
		case "set_attr", "set_style", "on_event", "dispatch_event":
			return fnT([]Type{TText, TText, TText}, resultT(TUnit, TText)), nil
		case "remove_attr":
			return fnT([]Type{TText, TText}, resultT(TUnit, TText)), nil
		case "attr":
			return fnT([]Type{TText, TText}, resultT(optionT(TText), TText)), nil
		case "style":
			return fnT([]Type{TText, TText}, resultT(TText, TText)), nil
		case "toggle_class", "has_class":
			return fnT([]Type{TText, TText}, resultT(TBool, TText)), nil
		case "set_checked", "set_disabled":
			return fnT([]Type{TText, TBool}, resultT(TUnit, TText)), nil
		case "checked", "disabled":
			return fnT([]Type{TText}, resultT(TBool, TText)), nil
		case "set_selected_index":
			return fnT([]Type{TText, TInt}, resultT(TUnit, TText)), nil
		case "selected_index":
			return fnT([]Type{TText}, resultT(TInt, TText)), nil
		case "rect":
			return fnT([]Type{TText}, resultT(listT(TText), TText)), nil
		case "on_click":
			return fnT([]Type{TText, TText}, resultT(TUnit, TText)), nil
		case "storage_set", "session_set":
			return fnT([]Type{TText, TText}, resultT(TUnit, TText)), nil
		case "storage_get", "session_get", "cookie_get":
			return fnT([]Type{TText}, resultT(optionT(TText), TText)), nil
		case "storage_remove", "session_remove", "cookie_remove":
			return fnT([]Type{TText}, resultT(TUnit, TText)), nil
		case "storage_clear", "session_clear":
			return fnT([]Type{}, resultT(TUnit, TText)), nil
		case "cookie_set":
			return fnT([]Type{TText, TText, TInt}, resultT(TUnit, TText)), nil
		case "href", "path", "search", "hash":
			return fnT([]Type{}, resultT(TText, TText)), nil
		case "set_hash", "navigate", "replace_url", "history_push", "history_replace":
			return fnT([]Type{TText}, resultT(TUnit, TText)), nil
		case "reload", "history_back", "history_forward":
			return fnT([]Type{}, resultT(TUnit, TText)), nil
		case "set_timeout", "set_interval":
			return fnT([]Type{TInt, TText}, resultT(TInt, TText)), nil
		case "animation_frame":
			return fnT([]Type{TText}, resultT(TInt, TText)), nil
		case "clear_timer", "abort_fetch":
			return fnT([]Type{TInt}, resultT(TUnit, TText)), nil
		case "online":
			return fnT([]Type{}, resultT(TBool, TText)), nil
		case "fetch":
			return fnT([]Type{TText, TText, TText, TText, TText}, resultT(TInt, TText)), nil
		case "ws_open":
			return fnT([]Type{TText, TText}, resultT(TInt, TText)), nil
		case "ws_send":
			return fnT([]Type{TInt, TText}, resultT(TUnit, TText)), nil
		case "ws_close":
			return fnT([]Type{TInt, TInt, TText}, resultT(TUnit, TText)), nil
		case "ws_ready_state":
			return fnT([]Type{TInt}, resultT(TInt, TText)), nil
		case "canvas_set_size":
			return fnT([]Type{TText, TInt, TInt}, resultT(TUnit, TText)), nil
		case "canvas_clear":
			return fnT([]Type{TText, TText}, resultT(TUnit, TText)), nil
		case "canvas_fill_rect", "canvas_stroke_rect":
			return fnT([]Type{TText, TInt, TInt, TInt, TInt, TText}, resultT(TUnit, TText)), nil
		case "canvas_line":
			return fnT([]Type{TText, TInt, TInt, TInt, TInt, TText}, resultT(TUnit, TText)), nil
		case "canvas_circle":
			return fnT([]Type{TText, TInt, TInt, TInt, TText}, resultT(TUnit, TText)), nil
		case "canvas_text":
			return fnT([]Type{TText, TText, TInt, TInt, TText, TText}, resultT(TUnit, TText)), nil
		case "canvas_data_url":
			return fnT([]Type{TText, TText}, resultT(TText, TText)), nil
		case "media_play", "media_pause":
			return fnT([]Type{TText}, resultT(TUnit, TText)), nil
		case "media_current_time", "media_volume":
			return fnT([]Type{TText}, resultT(TText, TText)), nil
		case "media_set_current_time", "media_set_volume":
			return fnT([]Type{TText, TText}, resultT(TUnit, TText)), nil
		case "clipboard_write":
			return fnT([]Type{TText, TText}, resultT(TUnit, TText)), nil
		case "clipboard_read", "geolocate":
			return fnT([]Type{TText}, resultT(TUnit, TText)), nil
		case "viewport_width", "viewport_height":
			return fnT([]Type{}, resultT(TInt, TText)), nil
		case "pixel_ratio", "language", "user_agent", "visibility":
			return fnT([]Type{}, resultT(TText, TText)), nil
		case "request_fullscreen":
			return fnT([]Type{TText}, resultT(TUnit, TText)), nil
		case "exit_fullscreen":
			return fnT([]Type{}, resultT(TUnit, TText)), nil
		case "fullscreen_active":
			return fnT([]Type{}, resultT(TBool, TText)), nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown web member "+v.Name)
	}
	if t.Name == "module:db" {
		db := Type{Name: "native:kv_db"}
		tx := Type{Name: "native:kv_tx"}
		switch v.Name {
		case "open":
			return fnT([]Type{TText}, resultT(db, TText)), nil
		case "put":
			return fnT([]Type{db, TText, TAny}, resultT(TUnit, TText)), nil
		case "get":
			return fnT([]Type{db, TText}, optionT(TAny)), nil
		case "delete":
			return fnT([]Type{db, TText}, resultT(TUnit, TText)), nil
		case "keys":
			return fnT([]Type{db}, listT(TText)), nil
		case "close":
			return fnT([]Type{db}, TUnit), nil
		case "begin":
			return fnT([]Type{db}, resultT(tx, TText)), nil
		case "tx_put":
			return fnT([]Type{tx, TText, TAny}, TUnit), nil
		case "tx_get":
			return fnT([]Type{tx, TText}, optionT(TAny)), nil
		case "tx_delete":
			return fnT([]Type{tx, TText}, TUnit), nil
		case "commit":
			return fnT([]Type{tx}, resultT(TUnit, TText)), nil
		case "rollback":
			return fnT([]Type{tx}, TUnit), nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown db member "+v.Name)
	}
	if t.Name == "module:process" {
		if v.Name == "run" {
			return fnT([]Type{TText, listT(TText)}, resultT(TText, TText)), nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown process member "+v.Name)
	}
	if t.Name == "module:regex" {
		switch v.Name {
		case "is_match":
			return fnT([]Type{TText, TText}, TBool), nil
		case "find_all":
			return fnT([]Type{TText, TText}, listT(TText)), nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown regex member "+v.Name)
	}

	if t.Name == "module:jit" {
		compiled := Type{Name: "native:jit_function"}
		switch v.Name {
		case "available":
			return fnT([]Type{}, TBool), nil
		case "compile_i64":
			if c.UnsafeDepth == 0 {
				return TAny, c.err(v.Tok, "SAGA-T178", "JIT compilation requires unsafe { ... }")
			}
			return fnT([]Type{TAny}, resultT(compiled, TError)), nil
		case "call_i64":
			if c.UnsafeDepth == 0 {
				return TAny, c.err(v.Tok, "SAGA-T178", "JIT execution requires unsafe { ... }")
			}
			return fnT([]Type{compiled, listT(TInt)}, TInt), nil
		case "close":
			return fnT([]Type{compiled}, TUnit), nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown jit member "+v.Name)
	}
	if t.Name == "module:game" {
		canvas := Type{Name: "native:game_canvas"}
		fb := Type{Name: "native:game_framebuffer"}
		texture := Type{Name: "native:game_texture"}
		animation := Type{Name: "native:game_animation"}
		camera := Type{Name: "native:game_camera"}
		tilemap := Type{Name: "native:game_tilemap"}
		particles := Type{Name: "native:game_particles"}
		world := Type{Name: "native:physics_world"}
		body := Type{Name: "native:physics_body"}
		audio := Type{Name: "native:audio_clip"}
		assets := Type{Name: "native:asset_manager"}
		window := Type{Name: "native:game_window"}
		gamepad := Type{Name: "native:gamepad"}
		renderer := Type{Name: "native:game_renderer"}
		shader := Type{Name: "native:game_shader"}
		mesh3d := Type{Name: "native:mesh3d"}
		camera3d := Type{Name: "native:camera3d"}
		switch v.Name {
		case "canvas":
			return fnT([]Type{TInt, TInt}, canvas), nil
		case "clear":
			return fnT([]Type{canvas, TText}, TUnit), nil
		case "set":
			return fnT([]Type{canvas, TInt, TInt, TText}, TUnit), nil
		case "text":
			return fnT([]Type{canvas, TInt, TInt, TText}, TUnit), nil
		case "render":
			return fnT([]Type{canvas}, TText), nil
		case "present":
			return fnT([]Type{canvas}, TUnit), nil
		case "frame":
			return fnT([]Type{TInt}, TUnit), nil
		case "box", "fill_rect", "line":
			return fnT([]Type{canvas, TInt, TInt, TInt, TInt, TText}, TUnit), nil
		case "circle":
			return fnT([]Type{canvas, TInt, TInt, TInt, TText}, TUnit), nil
		case "sprite":
			return fnT([]Type{canvas, TInt, TInt, TText}, TUnit), nil
		case "point_in_rect":
			return fnT([]Type{TInt, TInt, TInt, TInt, TInt, TInt}, TBool), nil
		case "overlap":
			return fnT([]Type{TInt, TInt, TInt, TInt, TInt, TInt, TInt, TInt}, TBool), nil
		case "input":
			return fnT([]Type{TText}, TText), nil
		case "clock_ms":
			return fnT([]Type{}, TInt), nil
		case "width", "height":
			return fnT([]Type{canvas}, TInt), nil
		case "mesh3d_cube":
			return fnT([]Type{TDecimal}, mesh3d), nil
		case "mesh3d":
			return fnT([]Type{listT(TAny), listT(TInt)}, mesh3d), nil
		case "mesh3d_obj":
			return fnT([]Type{TText}, resultT(mesh3d, TText)), nil
		case "mesh3d_translate", "mesh3d_rotate", "mesh3d_scale":
			return fnT([]Type{mesh3d, TDecimal, TDecimal, TDecimal}, TUnit), nil
		case "camera3d":
			return fnT([]Type{TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal}, camera3d), nil
		case "draw_mesh3d", "draw_wireframe3d":
			return fnT([]Type{fb, mesh3d, camera3d, TInt, TInt, TInt, TInt}, TInt), nil
		case "framebuffer":
			return fnT([]Type{TInt, TInt}, fb), nil
		case "fb_clear":
			return fnT([]Type{fb, TInt, TInt, TInt, TInt}, TUnit), nil
		case "fb_pixel":
			return fnT([]Type{fb, TInt, TInt, TInt, TInt, TInt, TInt}, TUnit), nil
		case "fb_rect", "fb_line":
			return fnT([]Type{fb, TInt, TInt, TInt, TInt, TInt, TInt, TInt, TInt}, TUnit), nil
		case "fb_circle":
			return fnT([]Type{fb, TInt, TInt, TInt, TInt, TInt, TInt, TInt}, TUnit), nil
		case "fb_width", "fb_height":
			return fnT([]Type{fb}, TInt), nil
		case "texture_load":
			return fnT([]Type{TText}, resultT(texture, TText)), nil
		case "texture_width", "texture_height":
			return fnT([]Type{texture}, TInt), nil
		case "draw_texture":
			return fnT([]Type{fb, texture, TInt, TInt}, TUnit), nil
		case "draw_texture_region":
			return fnT([]Type{fb, texture, TInt, TInt, TInt, TInt, TInt, TInt, TInt, TInt}, TUnit), nil
		case "animation":
			return fnT([]Type{texture, TInt, TInt, TInt, TDecimal}, animation), nil
		case "animation_frame":
			return fnT([]Type{animation, TInt}, TInt), nil
		case "draw_animation":
			return fnT([]Type{fb, animation, TInt, TInt, TInt, TInt}, TUnit), nil
		case "camera":
			return fnT([]Type{TDecimal, TDecimal, TDecimal}, camera), nil
		case "camera_set":
			return fnT([]Type{camera, TDecimal, TDecimal, TDecimal}, TUnit), nil
		case "tilemap":
			return fnT([]Type{TInt, TInt, TInt, TInt}, tilemap), nil
		case "tile_set":
			return fnT([]Type{tilemap, TInt, TInt, TInt}, TUnit), nil
		case "tile_get":
			return fnT([]Type{tilemap, TInt, TInt}, optionT(TInt)), nil
		case "tile_draw":
			return fnT([]Type{fb, tilemap, texture, camera, TInt}, TUnit), nil
		case "particles":
			return fnT([]Type{}, particles), nil
		case "particle_emit":
			return fnT([]Type{particles, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TInt, TInt, TInt, TInt, TInt}, TUnit), nil
		case "particles_update":
			return fnT([]Type{particles, TDecimal, TDecimal}, TUnit), nil
		case "particles_draw":
			return fnT([]Type{fb, particles, camera}, TUnit), nil
		case "particle_count":
			return fnT([]Type{particles}, TInt), nil
		case "physics_world":
			return fnT([]Type{TDecimal, TDecimal}, world), nil
		case "physics_body":
			return fnT([]Type{world, TDecimal, TDecimal, TDecimal, TDecimal, TDecimal, TBool}, body), nil
		case "body_velocity":
			return fnT([]Type{body, TDecimal, TDecimal}, TUnit), nil
		case "body_position", "body_force", "body_impulse":
			return fnT([]Type{body, TDecimal, TDecimal}, TUnit), nil
		case "body_restitution":
			return fnT([]Type{body, TDecimal}, TUnit), nil
		case "physics_step":
			return fnT([]Type{world, TDecimal}, TUnit), nil
		case "body_x", "body_y", "body_vx", "body_vy":
			return fnT([]Type{body}, TDecimal), nil
		case "body_overlaps":
			return fnT([]Type{body, body}, TBool), nil
		case "audio_load":
			return fnT([]Type{TText}, resultT(audio, TText)), nil
		case "audio_play":
			return fnT([]Type{audio}, resultT(TUnit, TText)), nil
		case "asset_manager":
			return fnT([]Type{}, assets), nil
		case "asset_texture":
			return fnT([]Type{assets, TText}, resultT(texture, TText)), nil
		case "asset_audio":
			return fnT([]Type{assets, TText}, resultT(audio, TText)), nil
		case "desktop_available":
			return fnT([]Type{}, TBool), nil
		case "desktop_backend":
			return fnT([]Type{}, TText), nil
		case "graphics_backends":
			return fnT([]Type{}, listT(TText)), nil
		case "window_open":
			return fnT([]Type{TText, TInt, TInt}, resultT(window, TText)), nil
		case "window_close":
			return fnT([]Type{window}, TUnit), nil
		case "window_poll":
			return fnT([]Type{window}, TBool), nil
		case "key_down":
			return fnT([]Type{window, TText}, TBool), nil
		case "mouse_x", "mouse_y":
			return fnT([]Type{window}, TInt), nil
		case "mouse_button":
			return fnT([]Type{window, TText}, TBool), nil
		case "gamepad_count":
			return fnT([]Type{}, TInt), nil
		case "gamepad_open":
			return fnT([]Type{TInt}, resultT(gamepad, TText)), nil
		case "gamepad_close":
			return fnT([]Type{gamepad}, TUnit), nil
		case "gamepad_button":
			return fnT([]Type{gamepad, TText}, TBool), nil
		case "gamepad_axis":
			return fnT([]Type{gamepad, TText}, TDecimal), nil
		case "renderer":
			return fnT([]Type{window}, resultT(renderer, TText)), nil
		case "vulkan_probe":
			return fnT([]Type{}, resultT(TText, TText)), nil
		case "renderer_backend":
			return fnT([]Type{window, TText}, resultT(renderer, TText)), nil
		case "renderer_info":
			return fnT([]Type{renderer}, TText), nil
		case "renderer_close":
			return fnT([]Type{renderer}, TUnit), nil
		case "shader":
			return fnT([]Type{renderer, TText}, resultT(shader, TText)), nil
		case "shader_program":
			return fnT([]Type{renderer, TText, TText}, resultT(shader, TText)), nil
		case "shader_ir_validate":
			return fnT([]Type{TText}, resultT(TUnit, TText)), nil
		case "shader_ir_compile":
			return fnT([]Type{TText, TText}, resultT(TText, TText)), nil
		case "shader_ir_compute_reference":
			return fnT([]Type{TText, listT(TFloat64)}, resultT(listT(TFloat64), TText)), nil
		case "shader_ir":
			return fnT([]Type{renderer, TText}, resultT(shader, TText)), nil
		case "shader_close":
			return fnT([]Type{shader}, TUnit), nil
		case "present_rgba":
			return fnT([]Type{renderer, fb}, resultT(TUnit, TText)), nil
		case "present_shader":
			return fnT([]Type{renderer, fb, shader}, resultT(TUnit, TText)), nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "unknown game member "+v.Name)
	}

	if t.Name == "error" {
		if v.Name == "message" || v.Name == "kind" {
			return TText, nil
		}
		return TAny, c.err(v.Tok, "SAGA-T106", "error only has message and kind")
	}
	ci := c.classFor(t)
	if ci == nil {
		return TAny, c.err(v.Tok, "SAGA-T106", "member access requires object")
	}
	mapping := map[string]Type{}
	for i, n := range ci.TypeParams {
		if i < len(t.Args) {
			mapping[n] = t.Args[i]
		}
	}
	if f, ok := ci.Fields[v.Name]; ok {
		if f.Private && c.CurrentClass != f.Owner {
			return TAny, c.err(v.Tok, "SAGA-T107", "private member access")
		}
		return substitute(f.Typ, mapping), nil
	}
	if m, ok := ci.Methods[v.Name]; ok {
		ps := []Type{}
		for _, p := range m.Params {
			ps = append(ps, substitute(p, mapping))
		}
		r := substitute(m.Ret, mapping)
		if m.Decl != nil && m.Decl.Async {
			r = futureT(r)
		}
		return fnT(ps, r), nil
	}
	return TAny, c.err(v.Tok, "SAGA-T106", "unknown member "+v.Name)
}
func (c *Checker) checkCall(v *Call, expected *Type) (Type, error) {
	if m, ok := v.Callee.(*Member); ok {
		target, e := c.checkExpr(m.Target, nil)
		if e != nil {
			return TAny, e
		}
		if target.Name == "list" && len(target.Args) == 1 {
			if result, handled, e := c.checkNaturalListCall(target.Args[0], m.Name, v.Args, v.Tok); handled {
				return result, e
			}
		}
		if result, handled, e := c.checkNaturalValueCall(target, m.Name, v.Args, v.Tok); handled {
			return result, e
		}
	}
	if m, ok := v.Callee.(*Member); ok {
		if q, ok := m.Target.(*Variable); ok && q.Name == "task" {
			return c.checkTaskCall(m.Name, v.Args, v.Tok)
		}
	}
	if q, ok := v.Callee.(*Variable); ok && coreBuiltins[q.Name] {
		return c.checkBuiltin(q.Name, v.Args, v.Tok, expected)
	}
	if q, ok := v.Callee.(*Variable); ok {
		if f, ok := c.Functions[q.Name]; ok && f.Decl != nil && f.Decl.ExternABI != "" && c.UnsafeDepth == 0 {
			return TAny, c.err(v.Tok, "SAGA-T178", "extern function calls require an unsafe { ... } block")
		}
	}
	if q, ok := v.Callee.(*Variable); ok {
		if cl := c.Classes[q.Name]; cl != nil && (cl.Abstract || cl.Interface) {
			return TAny, c.err(q.Tok, "SAGA-T111", "abstract/interface cannot be constructed")
		}
	}
	ct, e := c.checkExpr(v.Callee, nil)
	if e != nil {
		return TAny, e
	}
	args := []Type{}
	for i, a := range v.Args {
		var exp *Type
		if ct.Name == "fn" && i < len(ct.Args) {
			exp = &ct.Args[i]
		}
		t, e := c.checkExpr(a, exp)
		if e != nil {
			return TAny, e
		}
		args = append(args, t)
	}
	if q, ok := v.Callee.(*Variable); ok {
		if f, ok := c.Functions[q.Name]; ok && f.Decl != nil && f.Decl.Comptime {
			for _, a := range v.Args {
				if !isCompileTimeExpr(a) {
					return TAny, c.err(a.token(), "SAGA-T179", "comptime call requires compile-time constant arguments")
				}
			}
		}
	}
	if ct.Name == "any" {
		for _, a := range v.Args {
			if _, e := c.checkExpr(a, nil); e != nil {
				return TAny, e
			}
		}
		return TAny, nil
	}
	if ct.Name != "fn" {
		return TAny, c.err(v.Tok, "SAGA-T105", "value is not callable")
	}
	if len(args) != len(ct.Args) {
		return TAny, c.err(v.Tok, "SAGA-T105", fmt.Sprintf("expected %d arguments", len(ct.Args)))
	}
	m := map[string]Type{}
	for i := range args {
		if !unify(ct.Args[i], args[i], m) && !c.assignable(ct.Args[i], args[i]) {
			return TAny, c.err(v.Args[i].token(), "SAGA-T105", fmt.Sprintf("argument %d type mismatch: expected %s, got %s", i+1, ct.Args[i], args[i]))
		}
	}
	enumConstructor := ""
	if member, ok := v.Callee.(*Member); ok {
		if targetType, err := c.checkExpr(member.Target, nil); err == nil && strings.HasPrefix(targetType.Name, "enumtype:") {
			enumConstructor = strings.TrimPrefix(targetType.Name, "enumtype:")
		}
	}
	if enumConstructor != "" && expected != nil && ct.Result != nil && expected.Name == ct.Result.Name {
		unify(*ct.Result, *expected, m)
	}
	if q, ok := v.Callee.(*Variable); ok {
		if f, ok := c.Functions[q.Name]; ok && f.Decl != nil {
			if e := c.validateGenericConstraints(f.Decl, m, v.Tok); e != nil {
				return TAny, e
			}
		}
		if cl := c.Classes[q.Name]; cl != nil && len(cl.Decl.Constraints) > 0 {
			fake := &FnDecl{Constraints: cl.Decl.Constraints}
			if e := c.validateGenericConstraints(fake, m, v.Tok); e != nil {
				return TAny, e
			}
		}
	}
	if member, ok := v.Callee.(*Member); ok {
		if target, ok := member.Target.(*Variable); ok {
			if vi, found := c.find(target.Name); found && strings.HasPrefix(vi.Typ.Name, "srcmodule:") {
				bind := strings.TrimPrefix(vi.Typ.Name, "srcmodule:")
				if mod, found := c.SourceModules[bind]; found {
					if f, found := mod.Functions[member.Name]; found && f.Decl != nil {
						if e := c.validateGenericConstraints(f.Decl, m, v.Tok); e != nil {
							return TAny, e
						}
					}
				}
				if cl := c.Classes[bind+"."+member.Name]; cl != nil && cl.Decl != nil && len(cl.Decl.Constraints) > 0 {
					fake := &FnDecl{Constraints: cl.Decl.Constraints}
					if e := c.validateGenericConstraints(fake, m, v.Tok); e != nil {
						return TAny, e
					}
				}
			}
		}
	}
	if ct.Result == nil {
		return TUnit, nil
	}
	resolved := substitute(*ct.Result, m)
	if enumConstructor != "" && containsTypeVar(resolved) {
		return TAny, c.err(v.Tok, "SAGA-T113", "cannot fully infer generic enum constructor "+enumConstructor+"; add an explicit "+enumConstructor+"[...] result type")
	}
	return c.resolveAssociatedType(resolved, m, v.Tok)
}

func containsTypeVar(t Type) bool {
	if isTypeVar(t) {
		return true
	}
	for _, arg := range t.Args {
		if containsTypeVar(arg) {
			return true
		}
	}
	return t.Result != nil && containsTypeVar(*t.Result)
}

func (c *Checker) checkClosure(v *ClosureExpr, expected *Type) (Type, error) {
	paramTypes := []Type{}
	resultType := TAny
	hasExpectedResult := false
	if expected != nil && expected.Name == "fn" {
		if v.Implicit {
			if len(expected.Args) > 1 {
				return TAny, c.err(v.Tok, "SAGA-T103", "implicit closure can accept at most one contextual parameter")
			}
			paramTypes = append(paramTypes, expected.Args...)
		} else {
			if len(v.Params) != len(expected.Args) {
				return TAny, c.err(v.Tok, "SAGA-T105", fmt.Sprintf("closure expects %d parameters", len(expected.Args)))
			}
			paramTypes = append(paramTypes, expected.Args...)
		}
		if expected.Result != nil {
			resultType = *expected.Result
			hasExpectedResult = resultType.Name != "any"
		}
	} else if !v.Implicit {
		for range v.Params {
			paramTypes = append(paramTypes, TAny)
		}
	}

	c.push()
	oldFn, oldRet, oldLoop := c.CurrentFn, c.CurrentRet, c.LoopDepth
	synthetic := &FnDecl{Tok: v.Tok}
	c.CurrentFn = synthetic
	contract := resultType
	c.CurrentRet = &contract
	c.LoopDepth = 0
	defer func() {
		c.CurrentFn, c.CurrentRet, c.LoopDepth = oldFn, oldRet, oldLoop
		c.pop()
	}()
	if v.Implicit {
		if len(paramTypes) == 1 {
			c.Scopes[len(c.Scopes)-1]["it"] = VarInfo{Typ: paramTypes[0]}
		}
	} else {
		for idx, tok := range v.Params {
			c.Scopes[len(c.Scopes)-1][tok.Lex] = VarInfo{Typ: paramTypes[idx]}
		}
	}
	if e := c.predeclareLocalFns(v.Body.Stmts); e != nil {
		return TAny, e
	}
	inferred := TUnit
	for idx, stmt := range v.Body.Stmts {
		last := idx == len(v.Body.Stmts)-1
		if last {
			if es, ok := stmt.(*ExprStmt); ok {
				var exp *Type
				if hasExpectedResult {
					exp = &resultType
				}
				t, e := c.checkExpr(es.Expr, exp)
				if e != nil {
					return TAny, e
				}
				inferred = t
				continue
			}
		}
		if e := c.checkStmt(stmt); e != nil {
			return TAny, e
		}
	}
	if hasExpectedResult {
		if inferred.Name != "unit" && !c.assignable(resultType, inferred) {
			return TAny, c.err(v.Tok, "SAGA-T103", fmt.Sprintf("closure result mismatch: expected %s, got %s", resultType, inferred))
		}
		inferred = resultType
	} else if resultType.Name != "any" {
		inferred = resultType
	}
	return fnT(paramTypes, inferred), nil
}

func (c *Checker) checkNaturalListCall(item Type, name string, args []Expr, tok Token) (Type, bool, error) {
	need := func(n int) error {
		if len(args) != n {
			return c.err(tok, "SAGA-T105", fmt.Sprintf("%s requires %d arguments", name, n))
		}
		return nil
	}
	callback := func(params []Type, result Type, expr Expr) (Type, error) {
		exp := fnT(params, result)
		ft, e := c.checkExpr(expr, &exp)
		if e != nil {
			return TAny, e
		}
		if ft.Name != "fn" || len(ft.Args) != len(params) || ft.Result == nil {
			return TAny, c.err(tok, "SAGA-T103", name+" requires a closure/function with the expected arity")
		}
		return ft, nil
	}
	switch name {
	case "map":
		if e := need(1); e != nil {
			return TAny, true, e
		}
		ft, e := callback([]Type{item}, TAny, args[0])
		if e != nil {
			return TAny, true, e
		}
		return listT(*ft.Result), true, nil
	case "filter", "any", "all", "none":
		if e := need(1); e != nil {
			return TAny, true, e
		}
		ft, e := callback([]Type{item}, TBool, args[0])
		if e != nil {
			return TAny, true, e
		}
		if ft.Result == nil || ft.Result.Name != "bool" {
			return TAny, true, c.err(tok, "SAGA-T103", name+" requires a bool predicate")
		}
		if name == "filter" {
			return listT(item), true, nil
		}
		return TBool, true, nil
	case "each":
		if e := need(1); e != nil {
			return TAny, true, e
		}
		if _, e := callback([]Type{item}, TAny, args[0]); e != nil {
			return TAny, true, e
		}
		return TUnit, true, nil
	case "reduce", "fold":
		if e := need(2); e != nil {
			return TAny, true, e
		}
		initial, e := c.checkExpr(args[0], nil)
		if e != nil {
			return TAny, true, e
		}
		ft, e := callback([]Type{initial, item}, initial, args[1])
		if e != nil {
			return TAny, true, e
		}
		if ft.Result == nil || !c.assignable(initial, *ft.Result) {
			return TAny, true, c.err(tok, "SAGA-T103", name+" closure contract mismatch")
		}
		return initial, true, nil
	case "find":
		if e := need(1); e != nil {
			return TAny, true, e
		}
		ft, e := callback([]Type{item}, TBool, args[0])
		if e != nil {
			return TAny, true, e
		}
		if ft.Result == nil || ft.Result.Name != "bool" {
			return TAny, true, c.err(tok, "SAGA-T103", "find requires a bool predicate")
		}
		return optionT(item), true, nil
	case "sorted", "distinct":
		if e := need(0); e != nil {
			return TAny, true, e
		}
		return listT(item), true, nil
	case "sortedBy":
		if e := need(1); e != nil {
			return TAny, true, e
		}
		if _, e := callback([]Type{item}, TAny, args[0]); e != nil {
			return TAny, true, e
		}
		return listT(item), true, nil
	case "take", "skip":
		if e := need(1); e != nil {
			return TAny, true, e
		}
		n, e := c.checkExpr(args[0], &TInt)
		if e != nil {
			return TAny, true, e
		}
		if n.Name != "int" {
			return TAny, true, c.err(tok, "SAGA-T103", name+" requires int")
		}
		return listT(item), true, nil
	case "zip":
		if e := need(1); e != nil {
			return TAny, true, e
		}
		other, e := c.checkExpr(args[0], nil)
		if e != nil {
			return TAny, true, e
		}
		if other.Name != "list" {
			return TAny, true, c.err(tok, "SAGA-T103", "zip requires list")
		}
		return listT(TAny), true, nil
	case "flatten":
		if e := need(0); e != nil {
			return TAny, true, e
		}
		if item.Name != "list" || len(item.Args) != 1 {
			return TAny, true, c.err(tok, "SAGA-T103", "flatten requires a list of lists")
		}
		return listT(item.Args[0]), true, nil
	case "flatMap":
		if e := need(1); e != nil {
			return TAny, true, e
		}
		ft, e := callback([]Type{item}, TAny, args[0])
		if e != nil {
			return TAny, true, e
		}
		if ft.Result.Name == "list" {
			return *ft.Result, true, nil
		}
		if ft.Result.Name == "any" {
			return listT(TAny), true, nil
		}
		return TAny, true, c.err(tok, "SAGA-T103", "flatMap closure must return list")
	case "chunk", "window":
		if e := need(1); e != nil {
			return TAny, true, e
		}
		n, e := c.checkExpr(args[0], &TInt)
		if e != nil {
			return TAny, true, e
		}
		if n.Name != "int" {
			return TAny, true, c.err(tok, "SAGA-T103", name+" size must be int")
		}
		return listT(listT(item)), true, nil
	case "group":
		if e := need(0); e != nil {
			return TAny, true, e
		}
		if !c.isHashableTypeDeep(item) {
			return TAny, true, c.err(tok, "SAGA-T103", "group elements must be hashable")
		}
		return mapT(item, listT(item)), true, nil
	case "groupBy":
		if e := need(1); e != nil {
			return TAny, true, e
		}
		ft, e := callback([]Type{item}, TAny, args[0])
		if e != nil {
			return TAny, true, e
		}
		key := *ft.Result
		if key.Name != "any" && !c.isHashableTypeDeep(key) {
			return TAny, true, c.err(tok, "SAGA-T103", "groupBy key must be hashable")
		}
		return mapT(key, listT(item)), true, nil
	case "sum":
		if e := need(0); e != nil {
			return TAny, true, e
		}
		if !isNumeric(item) {
			return TAny, true, c.err(tok, "SAGA-T103", "sum requires numeric list")
		}
		return item, true, nil
	case "contains":
		if e := need(1); e != nil {
			return TAny, true, e
		}
		got, e := c.checkExpr(args[0], &item)
		if e != nil {
			return TAny, true, e
		}
		if !c.assignable(item, got) {
			return TAny, true, c.err(tok, "SAGA-T103", "contains value type mismatch")
		}
		return TBool, true, nil
	}
	return TAny, false, nil
}

func (c *Checker) checkNaturalValueCall(target Type, name string, args []Expr, tok Token) (Type, bool, error) {
	need := func(n int) error {
		if len(args) != n {
			return c.err(tok, "SAGA-T105", fmt.Sprintf("%s requires %d arguments", name, n))
		}
		return nil
	}
	if target.Name == "text" {
		switch name {
		case "trim", "upper", "lower":
			if e := need(0); e != nil {
				return TAny, true, e
			}
			return TText, true, nil
		case "split":
			if e := need(1); e != nil {
				return TAny, true, e
			}
			q, e := c.checkExpr(args[0], &TText)
			if e != nil {
				return TAny, true, e
			}
			if q.Name != "text" {
				return TAny, true, c.err(tok, "SAGA-T103", "split separator must be text")
			}
			return listT(TText), true, nil
		case "startsWith", "endsWith", "contains":
			if e := need(1); e != nil {
				return TAny, true, e
			}
			q, e := c.checkExpr(args[0], &TText)
			if e != nil {
				return TAny, true, e
			}
			if q.Name != "text" {
				return TAny, true, c.err(tok, "SAGA-T103", name+" requires text")
			}
			return TBool, true, nil
		case "length":
			if e := need(0); e != nil {
				return TAny, true, e
			}
			return TInt, true, nil
		}
	}
	if target.Name == "map" && len(target.Args) == 2 {
		key, val := target.Args[0], target.Args[1]
		switch name {
		case "keys":
			if e := need(0); e != nil {
				return TAny, true, e
			}
			return listT(key), true, nil
		case "values":
			if e := need(0); e != nil {
				return TAny, true, e
			}
			return listT(val), true, nil
		case "containsKey":
			if e := need(1); e != nil {
				return TAny, true, e
			}
			q, e := c.checkExpr(args[0], &key)
			if e != nil {
				return TAny, true, e
			}
			if !c.assignable(key, q) {
				return TAny, true, c.err(tok, "SAGA-T103", "map key type mismatch")
			}
			return TBool, true, nil
		case "get":
			if len(args) != 1 && len(args) != 2 {
				return TAny, true, c.err(tok, "SAGA-T105", "map.get requires 1 or 2 arguments")
			}
			q, e := c.checkExpr(args[0], &key)
			if e != nil {
				return TAny, true, e
			}
			if !c.assignable(key, q) {
				return TAny, true, c.err(tok, "SAGA-T103", "map key type mismatch")
			}
			if len(args) == 2 {
				fb, e := c.checkExpr(args[1], &val)
				if e != nil {
					return TAny, true, e
				}
				if !c.assignable(val, fb) {
					return TAny, true, c.err(tok, "SAGA-T103", "map fallback type mismatch")
				}
				return val, true, nil
			}
			return optionT(val), true, nil
		}
	}
	if target.Name == "set" && len(target.Args) == 1 {
		item := target.Args[0]
		switch name {
		case "contains":
			if e := need(1); e != nil {
				return TAny, true, e
			}
			q, e := c.checkExpr(args[0], &item)
			if e != nil {
				return TAny, true, e
			}
			if !c.assignable(item, q) {
				return TAny, true, c.err(tok, "SAGA-T103", "set value type mismatch")
			}
			return TBool, true, nil
		case "toList":
			if e := need(0); e != nil {
				return TAny, true, e
			}
			return listT(item), true, nil
		}
	}
	return TAny, false, nil
}

func (c *Checker) checkTaskCall(name string, args []Expr, t Token) (Type, error) {
	if name == "pool" {
		if len(args) != 1 {
			return TAny, c.err(t, "SAGA-T105", "task.pool requires a worker count")
		}
		wt, e := c.checkExpr(args[0], nil)
		if e != nil {
			return TAny, e
		}
		if wt.Name != "int" {
			return TAny, c.err(t, "SAGA-T103", "task.pool worker count must be int")
		}
		return Type{Name: "native:task_pool"}, nil
	}
	if name == "submit" {
		if len(args) < 2 {
			return TAny, c.err(t, "SAGA-T105", "task.submit(pool, callable, ...args)")
		}
		pt, e := c.checkExpr(args[0], nil)
		if e != nil {
			return TAny, e
		}
		if pt.Name != "native:task_pool" {
			return TAny, c.err(t, "SAGA-T103", "task.submit requires task_pool")
		}
		ft, e := c.checkExpr(args[1], nil)
		if e != nil {
			return TAny, e
		}
		if ft.Name != "fn" {
			return TAny, c.err(t, "SAGA-T103", "task.submit requires a function")
		}
		if len(args)-2 != len(ft.Args) {
			return TAny, c.err(t, "SAGA-T105", fmt.Sprintf("task function expects %d arguments", len(ft.Args)))
		}
		mapping := map[string]Type{}
		for idx, a := range args[2:] {
			at, er := c.checkExpr(a, &ft.Args[idx])
			if er != nil {
				return TAny, er
			}
			if !unify(ft.Args[idx], at, mapping) && !c.assignable(ft.Args[idx], at) {
				return TAny, c.err(t, "SAGA-T105", "task argument type mismatch")
			}
		}
		if ft.Result == nil {
			return futureT(TUnit), nil
		}
		return futureT(substitute(*ft.Result, mapping)), nil
	}
	if name == "shutdown" {
		if len(args) != 1 {
			return TAny, c.err(t, "SAGA-T105", "task.shutdown(pool)")
		}
		pt, e := c.checkExpr(args[0], nil)
		if e != nil {
			return TAny, e
		}
		if pt.Name != "native:task_pool" {
			return TAny, c.err(t, "SAGA-T103", "task.shutdown requires task_pool")
		}
		return TUnit, nil
	}
	if name == "spawn" {
		if len(args) < 1 {
			return TAny, c.err(t, "SAGA-T105", "task.spawn requires a callable")
		}
		ft, e := c.checkExpr(args[0], nil)
		if e != nil {
			return TAny, e
		}
		if ft.Name != "fn" {
			return TAny, c.err(t, "SAGA-T103", "task.spawn requires a function")
		}
		if len(args)-1 != len(ft.Args) {
			return TAny, c.err(t, "SAGA-T105", fmt.Sprintf("task function expects %d arguments", len(ft.Args)))
		}
		mapping := map[string]Type{}
		for idx, a := range args[1:] {
			at, e := c.checkExpr(a, &ft.Args[idx])
			if e != nil {
				return TAny, e
			}
			if !unify(ft.Args[idx], at, mapping) && !c.assignable(ft.Args[idx], at) {
				return TAny, c.err(t, "SAGA-T105", "task argument type mismatch")
			}
		}
		if ft.Result == nil {
			return futureT(TUnit), nil
		}
		r := substitute(*ft.Result, mapping)
		return futureT(r), nil
	}
	if name == "await" {
		if len(args) != 1 {
			return TAny, c.err(t, "SAGA-T105", "task.await requires one future")
		}
		ft, e := c.checkExpr(args[0], nil)
		if e != nil {
			return TAny, e
		}
		if ft.Name != "future" || len(ft.Args) != 1 {
			return TAny, c.err(t, "SAGA-T103", "task.await requires future")
		}
		return ft.Args[0], nil
	}
	if name == "all" {
		if len(args) != 1 {
			return TAny, c.err(t, "SAGA-T105", "task.all requires one list")
		}
		lt, e := c.checkExpr(args[0], nil)
		if e != nil {
			return TAny, e
		}
		if lt.Name != "list" || len(lt.Args) != 1 || lt.Args[0].Name != "future" || len(lt.Args[0].Args) != 1 {
			return TAny, c.err(t, "SAGA-T103", "task.all requires list[future[T]]")
		}
		return listT(lt.Args[0].Args[0]), nil
	}
	if name == "await_timeout" {
		if len(args) != 2 {
			return TAny, c.err(t, "SAGA-T105", "task.await_timeout(future, milliseconds)")
		}
		ft, e := c.checkExpr(args[0], nil)
		if e != nil {
			return TAny, e
		}
		mt, e := c.checkExpr(args[1], nil)
		if e != nil {
			return TAny, e
		}
		if ft.Name != "future" || len(ft.Args) != 1 || mt.Name != "int" {
			return TAny, c.err(t, "SAGA-T103", "await_timeout requires future[T] and int")
		}
		return resultT(ft.Args[0], TError), nil
	}
	if name == "cancel" || name == "cancelled" {
		if len(args) != 1 {
			return TAny, c.err(t, "SAGA-T105", "task."+name+" requires one future")
		}
		ft, e := c.checkExpr(args[0], nil)
		if e != nil {
			return TAny, e
		}
		if ft.Name != "future" {
			return TAny, c.err(t, "SAGA-T103", "future required")
		}
		if name == "cancel" {
			return TUnit, nil
		}
		return TBool, nil
	}
	if name == "channel" || name == "stream" {
		if len(args) != 1 {
			return TAny, c.err(t, "SAGA-T105", "task."+name+" requires capacity")
		}
		ct, e := c.checkExpr(args[0], nil)
		if e != nil {
			return TAny, e
		}
		if ct.Name != "int" {
			return TAny, c.err(t, "SAGA-T103", "channel capacity must be int")
		}
		return channelT(TAny), nil
	}
	if name == "send" {
		if len(args) != 2 {
			return TAny, c.err(t, "SAGA-T105", "task.send(channel,value)")
		}
		ch, e := c.checkExpr(args[0], nil)
		if e != nil {
			return TAny, e
		}
		v, e := c.checkExpr(args[1], nil)
		if e != nil {
			return TAny, e
		}
		if ch.Name != "channel" || len(ch.Args) != 1 {
			return TAny, c.err(t, "SAGA-T103", "channel required")
		}
		if ch.Args[0].Name != "any" && !c.assignable(ch.Args[0], v) {
			return TAny, c.err(t, "SAGA-T103", "channel element type mismatch")
		}
		return TUnit, nil
	}
	if name == "recv" {
		if len(args) != 1 {
			return TAny, c.err(t, "SAGA-T105", "task.recv(channel)")
		}
		ch, e := c.checkExpr(args[0], nil)
		if e != nil {
			return TAny, e
		}
		if ch.Name != "channel" || len(ch.Args) != 1 {
			return TAny, c.err(t, "SAGA-T103", "channel required")
		}
		return optionT(ch.Args[0]), nil
	}
	if name == "close" {
		if len(args) != 1 {
			return TAny, c.err(t, "SAGA-T105", "task.close(channel)")
		}
		ch, e := c.checkExpr(args[0], nil)
		if e != nil {
			return TAny, e
		}
		if ch.Name != "channel" {
			return TAny, c.err(t, "SAGA-T103", "channel required")
		}
		return TUnit, nil
	}
	if name == "actor" {
		if len(args) != 1 {
			return TAny, c.err(t, "SAGA-T105", "task.actor(handler)")
		}
		ft, e := c.checkExpr(args[0], nil)
		if e != nil {
			return TAny, e
		}
		if ft.Name != "fn" || len(ft.Args) != 1 || ft.Result == nil {
			return TAny, c.err(t, "SAGA-T103", "actor handler must be fn[Message,Reply]")
		}
		return actorT(ft.Args[0], *ft.Result), nil
	}
	if name == "stop" {
		if len(args) != 1 {
			return TAny, c.err(t, "SAGA-T105", "task.stop expects actor")
		}
		a, e := c.checkExpr(args[0], nil)
		if e != nil {
			return TAny, e
		}
		if a.Name != "actor" && a.Name != "any" {
			return TAny, c.err(t, "SAGA-T103", "task.stop requires actor")
		}
		return TUnit, nil
	}
	if name == "ask" {
		if len(args) != 2 {
			return TAny, c.err(t, "SAGA-T105", "task.ask(actor,message)")
		}
		at, e := c.checkExpr(args[0], nil)
		if e != nil {
			return TAny, e
		}
		mt, e := c.checkExpr(args[1], nil)
		if e != nil {
			return TAny, e
		}
		if at.Name != "actor" || len(at.Args) != 2 || !c.assignable(at.Args[0], mt) {
			return TAny, c.err(t, "SAGA-T103", "actor message type mismatch")
		}
		return futureT(at.Args[1]), nil
	}
	if name == "parallel_map" {
		if len(args) != 3 {
			return TAny, c.err(t, "SAGA-T105", "task.parallel_map(function,list,workers)")
		}
		ft, e := c.checkExpr(args[0], nil)
		if e != nil {
			return TAny, e
		}
		lt, e := c.checkExpr(args[1], nil)
		if e != nil {
			return TAny, e
		}
		wt, e := c.checkExpr(args[2], nil)
		if e != nil {
			return TAny, e
		}
		if ft.Name != "fn" || len(ft.Args) != 1 || ft.Result == nil {
			return TAny, c.err(t, "SAGA-T103", "parallel_map requires unary function")
		}
		if lt.Name != "list" || len(lt.Args) != 1 {
			return TAny, c.err(t, "SAGA-T103", "parallel_map requires list")
		}
		if wt.Name != "int" {
			return TAny, c.err(t, "SAGA-T103", "worker count must be int")
		}
		m := map[string]Type{}
		if !unify(ft.Args[0], lt.Args[0], m) && !c.assignable(ft.Args[0], lt.Args[0]) {
			return TAny, c.err(t, "SAGA-T103", "parallel_map element type mismatch")
		}
		return listT(substitute(*ft.Result, m)), nil
	}
	return TAny, c.err(t, "SAGA-T106", "unknown task member "+name)
}

func (c *Checker) checkBuiltin(n string, a []Expr, t Token, expected *Type) (Type, error) {
	// Higher-order builtins contextually type closure arguments.  This is
	// particularly important after pipeline lowering, where `{ it * 2 }` must
	// learn `it` from the collection element type rather than being checked as
	// an unbound name.
	types := make([]Type, len(a))
	contextual := false
	switch n {
	case "transform", "filter", "any", "all":
		if len(a) == 2 {
			listType, e := c.checkExpr(a[1], nil)
			if e != nil {
				return TAny, e
			}
			types[1] = listType
			if listType.Name == "list" && len(listType.Args) == 1 {
				ret := TAny
				if n != "transform" {
					ret = TBool
				}
				exp := fnT([]Type{listType.Args[0]}, ret)
				q, e := c.checkExpr(a[0], &exp)
				if e != nil {
					return TAny, e
				}
				types[0] = q
				contextual = true
			}
		}
	case "reduce":
		if len(a) == 3 {
			listType, e := c.checkExpr(a[1], nil)
			if e != nil {
				return TAny, e
			}
			initType, e := c.checkExpr(a[2], nil)
			if e != nil {
				return TAny, e
			}
			types[1], types[2] = listType, initType
			if listType.Name == "list" && len(listType.Args) == 1 {
				exp := fnT([]Type{initType, listType.Args[0]}, initType)
				q, e := c.checkExpr(a[0], &exp)
				if e != nil {
					return TAny, e
				}
				types[0] = q
				contextual = true
			}
		}
	case "find":
		if len(a) == 3 {
			listType, e := c.checkExpr(a[1], nil)
			if e != nil {
				return TAny, e
			}
			fallback, e := c.checkExpr(a[2], nil)
			if e != nil {
				return TAny, e
			}
			types[1], types[2] = listType, fallback
			if listType.Name == "list" && len(listType.Args) == 1 {
				exp := fnT([]Type{listType.Args[0]}, TBool)
				q, e := c.checkExpr(a[0], &exp)
				if e != nil {
					return TAny, e
				}
				types[0] = q
				contextual = true
			}
		}
	}
	if !contextual {
		for j, x := range a {
			q, e := c.checkExpr(x, nil)
			if e != nil {
				return TAny, e
			}
			types[j] = q
		}
	}
	arity := func(k int) error {
		if len(types) != k {
			return c.err(t, "SAGA-T105", fmt.Sprintf("%s requires %d arguments", n, k))
		}
		return nil
	}
	req := func(ok bool, msg string) error {
		if !ok {
			return c.err(t, "SAGA-T103", msg)
		}
		return nil
	}
	if n == "some" {
		if len(a) != 1 {
			return TAny, c.err(t, "SAGA-T105", "some requires one argument")
		}
		return optionT(types[0]), nil
	}
	if n == "none" {
		if len(a) != 0 {
			return TAny, c.err(t, "SAGA-T105", "none requires no arguments")
		}
		if expected != nil && expected.Name == "option" {
			return *expected, nil
		}
		return optionT(TAny), nil
	}
	if n == "is_some" || n == "is_none" {
		if e := arity(1); e != nil {
			return TAny, e
		}
		if types[0].Name != "option" && types[0].Name != "any" {
			return TAny, c.err(t, "SAGA-T103", "option required")
		}
		return TBool, nil
	}
	if n == "unwrap" {
		if e := arity(1); e != nil {
			return TAny, e
		}
		if types[0].Name != "option" {
			return TAny, c.err(t, "SAGA-T103", "unwrap requires option")
		}
		return types[0].Args[0], nil
	}
	if n == "unwrap_or" {
		if e := arity(2); e != nil {
			return TAny, e
		}
		if types[0].Name != "option" {
			return TAny, c.err(t, "SAGA-T103", "unwrap_or requires option")
		}
		if !c.assignable(types[0].Args[0], types[1]) {
			return TAny, c.err(t, "SAGA-T103", "fallback type mismatch")
		}
		return types[0].Args[0], nil
	}
	switch n {
	case "print":
		return TUnit, nil
	case "len":
		if e := arity(1); e != nil {
			return TAny, e
		}
		if !(types[0].Name == "text" || types[0].Name == "bytes" || types[0].Name == "list" || types[0].Name == "map" || types[0].Name == "set") {
			return TAny, c.err(t, "SAGA-T103", "len unsupported for type")
		}
		return TInt, nil
	case "text":
		if e := arity(1); e != nil {
			return TAny, e
		}
		return TText, nil
	case "int", "int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64":
		if e := arity(1); e != nil {
			return TAny, e
		}
		if !(isNumeric(types[0]) || types[0].Name == "text") {
			return TAny, c.err(t, "SAGA-T103", n+" conversion requires number or text")
		}
		switch n {
		case "int8":
			return TInt8, nil
		case "int16":
			return TInt16, nil
		case "int32":
			return TInt32, nil
		case "int64":
			return TInt64, nil
		case "uint8":
			return TUInt8, nil
		case "uint16":
			return TUInt16, nil
		case "uint32":
			return TUInt32, nil
		case "uint64":
			return TUInt64, nil
		}
		return TInt, nil
	case "decimal":
		if e := arity(1); e != nil {
			return TAny, e
		}
		if !isNumeric(types[0]) {
			return TAny, c.err(t, "SAGA-T103", "decimal requires number")
		}
		return TDecimal, nil
	case "float32":
		if e := arity(1); e != nil {
			return TAny, e
		}
		if !(isNumeric(types[0]) || types[0].Name == "text") {
			return TAny, c.err(t, "SAGA-T103", "float32 conversion requires number or text")
		}
		return TFloat32, nil
	case "float64":
		if e := arity(1); e != nil {
			return TAny, e
		}
		if !(isNumeric(types[0]) || types[0].Name == "text") {
			return TAny, c.err(t, "SAGA-T103", "float64 conversion requires number or text")
		}
		return TFloat64, nil
	case "ratio":
		if e := arity(2); e != nil {
			return TAny, e
		}
		if types[0].Name != "int" || types[1].Name != "int" {
			return TAny, c.err(t, "SAGA-T103", "ratio requires ints")
		}
		return TRational, nil
	case "abs":
		if e := arity(1); e != nil {
			return TAny, e
		}
		if !isNumeric(types[0]) {
			return TAny, c.err(t, "SAGA-T103", "abs requires number")
		}
		return types[0], nil
	case "sqrt":
		if e := arity(1); e != nil {
			return TAny, e
		}
		if !isNumeric(types[0]) {
			return TAny, c.err(t, "SAGA-T103", "sqrt requires number")
		}
		if isFloat(types[0]) {
			return types[0], nil
		}
		return TDecimal, nil
	case "round":
		if e := arity(2); e != nil {
			return TAny, e
		}
		if !isNumeric(types[0]) || types[1].Name != "int" {
			return TAny, c.err(t, "SAGA-T103", "round(value,int)")
		}
		return TDecimal, nil
	case "floor", "ceil":
		if e := arity(1); e != nil {
			return TAny, e
		}
		if !isNumeric(types[0]) {
			return TAny, c.err(t, "SAGA-T103", n+" requires number")
		}
		return TInt, nil
	case "min", "max":
		if e := arity(2); e != nil {
			return TAny, e
		}
		if !isNumeric(types[0]) || !isNumeric(types[1]) {
			return TAny, c.err(t, "SAGA-T103", n+" requires numbers")
		}
		return commonNumeric(types[0], types[1]), nil
	case "sum", "mean":
		if e := arity(1); e != nil {
			return TAny, e
		}
		if types[0].Name != "list" || !isNumeric(types[0].Args[0]) {
			return TAny, c.err(t, "SAGA-T103", n+" requires numeric list")
		}
		if n == "mean" {
			if types[0].Args[0].Name == "decimal" {
				return TDecimal, nil
			}
			return TRational, nil
		}
		return types[0].Args[0], nil
	case "append", "prepend":
		if e := arity(2); e != nil {
			return TAny, e
		}
		if types[0].Name != "list" || !c.assignable(types[0].Args[0], types[1]) {
			return TAny, c.err(t, "SAGA-T103", n+" list element mismatch")
		}
		return types[0], nil
	case "repeat":
		if e := arity(2); e != nil {
			return TAny, e
		}
		if types[1].Name != "int" {
			return TAny, c.err(t, "SAGA-T103", "repeat count is int")
		}
		return listT(types[0]), nil
	case "set_at":
		if e := arity(3); e != nil {
			return TAny, e
		}
		if types[0].Name != "list" || types[1].Name != "int" || !c.assignable(types[0].Args[0], types[2]) {
			return TAny, c.err(t, "SAGA-T103", "set_at type mismatch")
		}
		return types[0], nil
	case "get":
		if e := arity(3); e != nil {
			return TAny, e
		}
		if types[0].Name != "list" || types[1].Name != "int" || !c.assignable(types[0].Args[0], types[2]) {
			return TAny, c.err(t, "SAGA-T103", "get type mismatch")
		}
		return types[0].Args[0], nil
	case "contains":
		if e := arity(2); e != nil {
			return TAny, e
		}
		return TBool, nil
	case "assert":
		if len(types) != 1 && len(types) != 2 {
			return TAny, c.err(t, "SAGA-T105", "assert takes 1 or 2 arguments")
		}
		if types[0].Name != "bool" {
			return TAny, c.err(t, "SAGA-T103", "assert condition must be bool")
		}
		return TUnit, nil
	case "precision":
		if e := arity(1); e != nil {
			return TAny, e
		}
		if types[0].Name != "int" {
			return TAny, c.err(t, "SAGA-T103", "precision requires int")
		}
		return TUnit, nil
	case "slice":
		if e := arity(3); e != nil {
			return TAny, e
		}
		if types[0].Name != "list" || types[1].Name != "int" || types[2].Name != "int" {
			return TAny, c.err(t, "SAGA-T103", "slice(list,int,int)")
		}
		return types[0], nil
	case "reverse", "sort", "unique":
		if e := arity(1); e != nil {
			return TAny, e
		}
		if types[0].Name != "list" {
			return TAny, c.err(t, "SAGA-T103", n+" requires list")
		}
		return types[0], nil
	case "transform", "filter":
		if e := arity(2); e != nil {
			return TAny, e
		}
		if types[1].Name != "list" || len(types[1].Args) != 1 {
			return TAny, c.err(t, "SAGA-T103", n+" requires list")
		}
		if types[0].Name != "fn" || len(types[0].Args) != 1 || types[0].Result == nil {
			return TAny, c.err(t, "SAGA-T103", n+" requires unary function")
		}
		mapping := map[string]Type{}
		if !unify(types[0].Args[0], types[1].Args[0], mapping) && !c.assignable(types[0].Args[0], types[1].Args[0]) {
			return TAny, c.err(t, "SAGA-T103", n+" function input type mismatch")
		}
		result := substitute(*types[0].Result, mapping)
		if n == "filter" {
			if result.Name != "bool" {
				return TAny, c.err(t, "SAGA-T103", "filter predicate must return bool")
			}
			return types[1], nil
		}
		return listT(result), nil
	case "reduce":
		if e := arity(3); e != nil {
			return TAny, e
		}
		if types[1].Name != "list" || len(types[1].Args) != 1 {
			return TAny, c.err(t, "SAGA-T103", "reduce requires list")
		}
		if types[0].Name != "fn" || len(types[0].Args) != 2 || types[0].Result == nil {
			return TAny, c.err(t, "SAGA-T103", "reduce requires binary function")
		}
		if !c.assignable(types[0].Args[0], types[2]) || !c.assignable(types[0].Args[1], types[1].Args[0]) || !c.assignable(types[2], *types[0].Result) {
			return TAny, c.err(t, "SAGA-T103", "reduce function contract mismatch")
		}
		return types[2], nil
	case "find":
		if e := arity(3); e != nil {
			return TAny, e
		}
		if types[1].Name != "list" || len(types[1].Args) != 1 {
			return TAny, c.err(t, "SAGA-T103", "find requires list")
		}
		if types[0].Name != "fn" || len(types[0].Args) != 1 || types[0].Result == nil || types[0].Result.Name != "bool" || !c.assignable(types[0].Args[0], types[1].Args[0]) {
			return TAny, c.err(t, "SAGA-T103", "find predicate contract mismatch")
		}
		if !c.assignable(types[1].Args[0], types[2]) {
			return TAny, c.err(t, "SAGA-T103", "find fallback type mismatch")
		}
		return types[1].Args[0], nil
	case "any", "all":
		if e := arity(2); e != nil {
			return TAny, e
		}
		if types[1].Name != "list" || len(types[1].Args) != 1 {
			return TAny, c.err(t, "SAGA-T103", n+" requires list")
		}
		if types[0].Name != "fn" || len(types[0].Args) != 1 || types[0].Result == nil || types[0].Result.Name != "bool" || !c.assignable(types[0].Args[0], types[1].Args[0]) {
			return TAny, c.err(t, "SAGA-T103", n+" predicate contract mismatch")
		}
		return TBool, nil
	case "ok":
		if e := arity(1); e != nil {
			return TAny, e
		}
		return resultT(types[0], TAny), nil
	case "err":
		if e := arity(1); e != nil {
			return TAny, e
		}
		return resultT(TAny, types[0]), nil
	case "is_ok", "is_err":
		if e := arity(1); e != nil {
			return TAny, e
		}
		if types[0].Name != "result" {
			return TAny, c.err(t, "SAGA-T103", n+" requires result")
		}
		return TBool, nil
	case "unwrap_ok":
		if e := arity(1); e != nil {
			return TAny, e
		}
		if types[0].Name != "result" || len(types[0].Args) != 2 {
			return TAny, c.err(t, "SAGA-T103", "unwrap_ok requires result")
		}
		return types[0].Args[0], nil
	case "unwrap_err":
		if e := arity(1); e != nil {
			return TAny, e
		}
		if types[0].Name != "result" || len(types[0].Args) != 2 {
			return TAny, c.err(t, "SAGA-T103", "unwrap_err requires result")
		}
		return types[0].Args[1], nil
	case "unwrap_result_or":
		if e := arity(2); e != nil {
			return TAny, e
		}
		if types[0].Name != "result" || len(types[0].Args) != 2 || !c.assignable(types[0].Args[0], types[1]) {
			return TAny, c.err(t, "SAGA-T103", "unwrap_result_or type mismatch")
		}
		return types[0].Args[0], nil
	case "split":
		if e := arity(2); e != nil {
			return TAny, e
		}
		return listT(TText), nil
	case "join", "replace", "substring":
		return TText, nil
	case "trim", "upper", "lower":
		if e := arity(1); e != nil {
			return TAny, e
		}
		return TText, nil
	case "starts_with", "ends_with":
		return TBool, nil
	case "find_text":
		return TInt, nil
	case "map_of":
		if len(types)%2 != 0 {
			return TAny, c.err(t, "SAGA-T105", "map_of requires key/value pairs")
		}
		if len(types) == 0 {
			return mapT(TAny, TAny), nil
		}
		kt, vt := types[0], types[1]
		if !c.isHashableTypeDeep(kt) {
			return TAny, c.err(t, "SAGA-T103", "map key type is not hashable")
		}
		for idx := 2; idx < len(types); idx += 2 {
			if !c.assignable(kt, types[idx]) || !c.assignable(vt, types[idx+1]) {
				return TAny, c.err(t, "SAGA-T103", "map entries must use consistent key/value types")
			}
		}
		return mapT(kt, vt), nil
	case "map_get":
		if e := arity(3); e != nil {
			return TAny, e
		}
		if types[0].Name != "map" {
			return TAny, c.err(t, "SAGA-T103", "map_get requires map")
		}
		if !c.assignable(types[0].Args[0], types[1]) || !c.assignable(types[0].Args[1], types[2]) {
			return TAny, c.err(t, "SAGA-T103", "map_get type mismatch")
		}
		return types[0].Args[1], nil
	case "map_put":
		if e := arity(3); e != nil {
			return TAny, e
		}
		if types[0].Name != "map" || !c.assignable(types[0].Args[0], types[1]) || !c.assignable(types[0].Args[1], types[2]) {
			return TAny, c.err(t, "SAGA-T103", "map_put type mismatch")
		}
		return types[0], nil
	case "map_remove":
		if e := arity(2); e != nil {
			return TAny, e
		}
		if types[0].Name != "map" || !c.assignable(types[0].Args[0], types[1]) {
			return TAny, c.err(t, "SAGA-T103", "map_remove type mismatch")
		}
		return types[0], nil
	case "map_keys":
		return listT(types[0].Args[0]), nil
	case "map_values":
		return listT(types[0].Args[1]), nil
	case "map_contains":
		if e := arity(2); e != nil {
			return TAny, e
		}
		if types[0].Name != "map" || !c.assignable(types[0].Args[0], types[1]) {
			return TAny, c.err(t, "SAGA-T103", "map_contains key type mismatch")
		}
		return TBool, nil
	case "set_of":
		if len(types) == 0 {
			return setT(TAny), nil
		}
		et := types[0]
		if !c.isHashableTypeDeep(et) {
			return TAny, c.err(t, "SAGA-T103", "set element type is not hashable")
		}
		for _, x := range types[1:] {
			if !c.assignable(et, x) {
				return TAny, c.err(t, "SAGA-T103", "set elements must have one type")
			}
		}
		return setT(et), nil
	case "set_add", "set_remove":
		if e := arity(2); e != nil {
			return TAny, e
		}
		if types[0].Name != "set" || !c.assignable(types[0].Args[0], types[1]) {
			return TAny, c.err(t, "SAGA-T103", n+" type mismatch")
		}
		return types[0], nil
	case "set_union", "set_intersection":
		if e := arity(2); e != nil {
			return TAny, e
		}
		if types[0].Name != "set" || types[1].Name != "set" || !c.assignable(types[0], types[1]) {
			return TAny, c.err(t, "SAGA-T103", n+" type mismatch")
		}
		return types[0], nil
	case "set_contains":
		if e := arity(2); e != nil {
			return TAny, e
		}
		if types[0].Name != "set" || !c.assignable(types[0].Args[0], types[1]) {
			return TAny, c.err(t, "SAGA-T103", "set_contains type mismatch")
		}
		return TBool, nil
	}
	_ = req
	return TAny, c.err(t, "SAGA-T106", "unknown builtin "+n)
}
func (c *Checker) assignable(exp, act Type) bool {
	if exp.Name == "any" || act.Name == "any" || isTypeVar(exp) {
		return true
	}
	if sameType(exp, act) {
		return true
	}
	if exp.Name == "decimal" && isNumeric(act) {
		return true
	}
	if exp.Name == "rational" && act.Name == "int" {
		return true
	}
	if strings.HasPrefix(exp.Name, "object:") && strings.HasPrefix(act.Name, "object:") {
		en := strings.TrimPrefix(exp.Name, "object:")
		an := strings.TrimPrefix(act.Name, "object:")
		if en == an {
			if len(exp.Args) != len(act.Args) {
				return false
			}
			for i := range exp.Args {
				if !sameType(exp.Args[i], act.Args[i]) {
					return false
				}
			}
			return true
		}
		return c.classSubtypeType(act, exp)
	}
	if exp.Name == act.Name && len(exp.Args) == len(act.Args) {
		for i := range exp.Args {
			if !c.assignable(exp.Args[i], act.Args[i]) {
				return false
			}
		}
		return true
	}
	return false
}
func (c *Checker) classSubtypeType(actual, expected Type) bool {
	if sameType(actual, expected) {
		return true
	}
	ci := c.classFor(actual)
	if ci == nil {
		return false
	}
	mapping := typeParamMap(ci.TypeParams, actual.Args)
	for _, rel := range ci.Interfaces {
		spec := substitute(rel, mapping)
		if sameType(spec, expected) || c.classSubtypeType(spec, expected) {
			return true
		}
	}
	if ci.Base.Name != "" {
		spec := substitute(ci.Base, mapping)
		if sameType(spec, expected) || c.classSubtypeType(spec, expected) {
			return true
		}
	}
	return false
}
func (c *Checker) classFor(t Type) *ClassInfo {
	if !strings.HasPrefix(t.Name, "object:") {
		return nil
	}
	return c.Classes[strings.TrimPrefix(t.Name, "object:")]
}
