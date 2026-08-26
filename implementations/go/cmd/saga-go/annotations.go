package main

func annotationHasStringArg(items []Annotation, name, arg string) bool {
	for _, a := range items {
		if a.Name != name {
			continue
		}
		for _, raw := range a.Args {
			if lit, ok := raw.(*Literal); ok {
				if s, ok := lit.Value.(string); ok && s == arg {
					return true
				}
			}
		}
	}
	return false
}

func classDerives(ci *ClassInfo, capability string) bool {
	return ci != nil && ci.Decl != nil && annotationHasStringArg(ci.Decl.Annotations, "derive", capability)
}

// bindControlScopes records the lexical function/method scope directly on AST
// nodes after a complete source unit has been parsed. Keep this in a Standard
// Core file because the mobile runtime embeds the same parser/checker surface.
func bindControlScopes(stmts []Stmt) {
	functions := map[string]*FnDecl{}
	for _, stmt := range stmts {
		if fn, ok := stmt.(*FnDecl); ok {
			functions[fn.Name] = fn
		}
	}

	for _, stmt := range stmts {
		classDecl, ok := stmt.(*ClassDecl)
		if !ok {
			continue
		}
		methods := map[string]*FnDecl{}
		for _, method := range classDecl.Methods {
			methods[method.Name] = method
		}
		for _, method := range classDecl.Methods {
			method.controlOwner = classDecl.Name
			method.controlFunctions = functions
			method.controlMethods = methods
		}
	}

	for _, fn := range functions {
		fn.controlFunctions = functions
	}
}
