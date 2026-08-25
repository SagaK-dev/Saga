package main

// bindControlScopes records the lexical function/method scope directly on AST
// nodes after a complete source unit has been parsed. Control validation can
// then resolve a call without depending on mutable checker state or flattening
// class methods into the top-level namespace.
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
