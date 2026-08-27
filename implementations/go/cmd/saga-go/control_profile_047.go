package main

import (
	"math/big"
	"strings"
)

func machineControlTickAnnotated(d *FnDecl) bool {
	for _, a := range d.Annotations {
		if a.Name == "control_tick" {
			return true
		}
	}
	return false
}

func controlExprPath(e Expr) string {
	switch x := e.(type) {
	case *Variable:
		return x.Name
	case *Member:
		p := controlExprPath(x.Target)
		if p != "" {
			return p + "." + x.Name
		}
	}
	return ""
}

var controlForbiddenCalls = map[string]bool{
	"task.await": true, "task.pool": true, "task.submit": true, "task.shutdown": true,
	"machine.can_recv": true, "machine.canfd_recv": true, "machine.ethercat_exchange": true,
	"machine.uart_read": true, "machine.spi_transfer": true, "machine.i2c_read": true,
	"machine.i2c_write_read": true, "machine.modbus_read_holding": true, "machine.modbus_read_input": true,
}

func controlCallForbidden(name string) bool {
	if controlForbiddenCalls[name] {
		return true
	}
	for _, p := range []string{"net.", "process.", "database.", "cloud."} {
		if strings.HasPrefix(name, p) {
			return true
		}
	}
	return false
}

func controlAnnotationInt(e Expr) (int64, bool) {
	lit, ok := e.(*Literal)
	if !ok {
		return 0, false
	}
	n, ok := lit.Value.(Number)
	if !ok || n.Kind != "int" {
		return 0, false
	}
	i, ok := n.Int()
	if !ok || !i.IsInt64() {
		return 0, false
	}
	return i.Int64(), true
}

func controlAnnotationNumber(e Expr) (*big.Rat, bool) {
	lit, ok := e.(*Literal)
	if !ok {
		return nil, false
	}
	n, ok := lit.Value.(Number)
	if !ok || (n.Kind != "int" && n.Kind != "decimal") || n.R == nil {
		return nil, false
	}
	return new(big.Rat).Set(n.R), true
}

func validateControlTickContract047(d *FnDecl) error {
	for _, a := range d.Annotations {
		if a.Name != "control_tick" || len(a.Args) == 0 {
			continue
		}
		if len(a.Args) != 2 {
			return diag("SAGA-T001", "SAGA-C480", "@control_tick period contract requires (rate_hz, budget_us)", a.Tok)
		}
		rate, ok := controlAnnotationInt(a.Args[0])
		if !ok || rate <= 0 || rate > 1000000 {
			return diag("SAGA-T001", "SAGA-C481", "@control_tick rate_hz must be an integer literal in 1..1000000", a.Args[0].token())
		}
		budget, ok := controlAnnotationNumber(a.Args[1])
		if !ok || budget.Sign() <= 0 {
			return diag("SAGA-T001", "SAGA-C482", "@control_tick budget_us must be a positive numeric literal", a.Args[1].token())
		}
		used := new(big.Rat).Mul(new(big.Rat).Set(budget), big.NewRat(rate, 1))
		if used.Cmp(big.NewRat(1000000, 1)) > 0 {
			return diag("SAGA-T001", "SAGA-C483", "@control_tick budget_us exceeds the declared period", a.Args[1].token())
		}
	}
	return nil
}

func clearControlScope054(d *FnDecl) {
	d.controlOwner = ""
	d.controlFunctions = nil
	d.controlMethods = nil
}

func validateControlTick047(d *FnDecl) error {
	tick := machineControlTickAnnotated(d)
	safe := machineControlSafeAnnotated(d)
	if !tick {
		if !safe {
			return nil
		}
		if d.Async {
			return diag("SAGA-T001", "SAGA-C484", "@control_safe function cannot be async", d.Tok)
		}
		clone := *d
		clone.Annotations = append([]Annotation{}, d.Annotations...)
		clone.Annotations = append(clone.Annotations, Annotation{Name: "control_tick", Tok: d.Tok})
		clearControlScope054(&clone)
		if err := validateControlTick047(&clone); err != nil {
			return err
		}
		return validateControlLexicalTransitive054(d)
	}
	if err := validateControlTickContract047(d); err != nil {
		return err
	}
	if d.Async {
		return diag("SAGA-T001", "SAGA-C470", "@control_tick function cannot be async", d.Tok)
	}
	var walkExpr func(Expr) error
	var walkStmt func(Stmt) error
	walkExpr = func(e Expr) error {
		if e == nil {
			return nil
		}
		switch x := e.(type) {
		case *ListExpr:
			return diag("SAGA-T001", "SAGA-C471", "@control_tick cannot construct a list", x.Tok)
		case *ClosureExpr:
			return diag("SAGA-T001", "SAGA-C472", "@control_tick cannot construct a closure", x.Tok)
		case *AwaitExpr:
			return diag("SAGA-T001", "SAGA-C473", "@control_tick cannot await", x.Tok)
		case *MoveExpr:
			return diag("SAGA-T001", "SAGA-C474", "@control_tick cannot move resources", x.Tok)
		case *Unary:
			return walkExpr(x.Right)
		case *Binary:
			if e := walkExpr(x.Left); e != nil {
				return e
			}
			return walkExpr(x.Right)
		case *RangeExpr:
			if e := walkExpr(x.Start); e != nil {
				return e
			}
			return walkExpr(x.End)
		case *Call:
			if name := controlExprPath(x.Callee); name != "" && controlCallForbidden(name) {
				return diag("SAGA-T001", "SAGA-C479", "@control_tick cannot call "+name, x.Tok)
			}
			if e := walkExpr(x.Callee); e != nil {
				return e
			}
			for _, a := range x.Args {
				if e := walkExpr(a); e != nil {
					return e
				}
			}
		case *Index:
			if e := walkExpr(x.Target); e != nil {
				return e
			}
			return walkExpr(x.Index)
		case *Member:
			return walkExpr(x.Target)
		case *PropagateExpr:
			return walkExpr(x.Value)
		case *InterpolatedString:
			for _, q := range x.Exprs {
				if e := walkExpr(q); e != nil {
					return e
				}
			}
		}
		return nil
	}
	walkStmt = func(s Stmt) error {
		if s == nil {
			return nil
		}
		switch x := s.(type) {
		case *Block:
			for _, q := range x.Stmts {
				if e := walkStmt(q); e != nil {
					return e
				}
			}
		case *VarDecl:
			return walkExpr(x.Init)
		case *Assign:
			if e := walkExpr(x.Target); e != nil {
				return e
			}
			return walkExpr(x.Value)
		case *ExprStmt:
			return walkExpr(x.Expr)
		case *IfStmt:
			if e := walkExpr(x.Cond); e != nil {
				return e
			}
			if e := walkStmt(x.Then); e != nil {
				return e
			}
			return walkStmt(x.Else)
		case *WhileStmt:
			return diag("SAGA-T001", "SAGA-C477", "@control_tick cannot use while; use a literal-bounded range for", x.Tok)
		case *ForStmt:
			r, ok := x.Iterable.(*RangeExpr)
			if !ok {
				return diag("SAGA-T001", "SAGA-C478", "@control_tick for must use a literal-bounded integer range", x.Tok)
			}
			if _, ok := r.Start.(*Literal); !ok {
				return diag("SAGA-T001", "SAGA-C478", "@control_tick range start must be a literal", x.Tok)
			}
			if _, ok := r.End.(*Literal); !ok {
				return diag("SAGA-T001", "SAGA-C478", "@control_tick range end must be a literal", x.Tok)
			}
			return walkStmt(x.Body)
		case *ReturnStmt:
			return walkExpr(x.Value)
		case *ThrowStmt:
			return diag("SAGA-T001", "SAGA-C476", "@control_tick cannot use exceptions", x.Tok)
		case *DeferStmt, *UsingStmt, *TaskGroupStmt:
			return diag("SAGA-T001", "SAGA-C475", "@control_tick cannot create dynamic lifetime/task structures", s.token())
		case *TryStmt:
			return diag("SAGA-T001", "SAGA-C476", "@control_tick cannot use exceptions", x.Tok)
		case *FnDecl:
			return diag("SAGA-T001", "SAGA-C472", "@control_tick cannot declare nested functions", x.Tok)
		}
		return nil
	}
	var err error
	if d.ExprBody != nil {
		err = walkExpr(d.ExprBody)
	} else {
		err = walkStmt(d.Body)
	}
	if err != nil {
		return err
	}
	return validateControlLexicalTransitive054(d)
}

// Saga 0.50 Production-GA whole-call-graph hardening.
func machineControlSafeAnnotated(d *FnDecl) bool {
	for _, a := range d.Annotations {
		if a.Name == "control_safe" {
			return true
		}
	}
	return false
}

var controlSafeBuiltins050 = map[string]bool{
	"abs": true, "min": true, "max": true, "floor": true, "ceil": true, "round": true, "int": true, "decimal": true,
	"is_ok": true, "is_err": true, "unwrap_result_or": true, "is_some": true, "is_none": true, "unwrap_or": true,
}
var controlSafeMachine050 = map[string]bool{
	"machine.clarke": true, "machine.park": true, "machine.inverse_park": true, "machine.svpwm": true,
	"machine.pid_step": true, "machine.pid_reset": true, "machine.pid_integral_limits": true,
	"machine.pid2_step": true, "machine.pid2_reset": true, "machine.filter_step": true, "machine.filter_reset": true,
	"machine.alpha_beta_step": true, "machine.alpha_beta_reset": true, "machine.foc_step": true, "machine.foc_reset": true,
	"machine.foc_duty": true, "machine.foc_id": true, "machine.foc_iq": true, "machine.foc_vd": true, "machine.foc_vq": true,
	"machine.fast_state_predict": true, "machine.fast_state_command": true, "machine.state_space_predict": true, "machine.state_space_command": true,
	"machine.kalman_predict": true, "machine.kalman_update": true, "machine.rls2_update": true, "machine.rls2_error": true,
	"machine.rls2_theta0": true, "machine.rls2_theta1": true, "machine.mpc2_step": true, "machine.mpc2_reset": true,
	"machine.disturbance_step": true, "machine.disturbance_reset": true, "machine.friction_compensation": true,
	"machine.axis_step": true, "machine.axis_done": true, "machine.axis_planned_position": true, "machine.axis_sync_correction": true,
	"machine.axis_sync_error": true, "machine.axis_sync_ok": true, "machine.profile_step": true, "machine.profile_done": true,
	"machine.profile_velocity": true, "machine.s_curve_step": true, "machine.s_curve_done": true, "machine.s_curve_velocity": true,
	"machine.s_curve_acceleration": true, "machine.actuator_step": true, "machine.actuator_set": true, "machine.actuator_set_all": true,
	"machine.actuator_zero": true, "machine.control_guard_begin": true, "machine.control_guard_end": true, "machine.control_guard_ok": true,
	"machine.budget_begin": true, "machine.budget_end": true,
	"machine.slew": true, "machine.low_pass": true, "machine.deadband": true, "machine.integrate_clamped": true,
	"machine.q31_from_ratio": true, "machine.q31_add_sat": true, "machine.q31_sub_sat": true,
	"machine.q31_mul_sat": true, "machine.q31_mac_sat": true,
}

func controlLiteralInt050(e Expr) (int64, bool) { return controlAnnotationInt(e) }

func validateControlRestrictedHelper050(d *FnDecl) error {
	if !machineControlSafeAnnotated(d) || machineControlTickAnnotated(d) {
		return nil
	}
	clone := *d
	clone.Annotations = append([]Annotation{}, d.Annotations...)
	clone.Annotations = append(clone.Annotations, Annotation{Name: "control_tick", Tok: d.Tok})
	clearControlScope054(&clone)
	return validateControlTick047(&clone)
}

func controlNodeKey054(d *FnDecl) string {
	if d.controlOwner != "" {
		return "method:" + d.controlOwner + "." + d.Name
	}
	return "fn:" + d.Name
}

func validateControlReferencedLocal054(d *FnDecl) error {
	clone := *d
	clearControlScope054(&clone)
	return validateControlTick047(&clone)
}

// validateControlLexicalTransitive054 is the implementation-neutral lexical
// call-graph pass used by both top-level functions and class methods. The
// parser binds same-source functions and same-receiver methods directly on the
// AST, so this pass does not depend on Checker internals and therefore applies
// to method bodies before type-checking can hide an unverified helper call.
func validateControlLexicalTransitive054(root *FnDecl) error {
	if !machineControlTickAnnotated(root) && !machineControlSafeAnnotated(root) {
		return nil
	}
	if root.controlFunctions == nil && root.controlMethods == nil {
		return nil
	}
	visiting := map[string]bool{}
	visited := map[string]bool{}
	var visit func(*FnDecl) error
	visit = func(d *FnDecl) error {
		key := controlNodeKey054(d)
		if visiting[key] {
			return diag("SAGA-T001", "SAGA-C485", "Production GA control call graph cannot be recursive: "+key, d.Tok)
		}
		if visited[key] {
			return nil
		}
		visiting[key] = true
		defer func() { visiting[key] = false; visited[key] = true }()

		if d != root {
			if e := validateControlReferencedLocal054(d); e != nil {
				return e
			}
		}
		locals := map[string]bool{}
		for _, p := range d.Params {
			locals[p.Name] = true
		}
		var walkExpr func(Expr) error
		var walkStmt func(Stmt) error
		walkExpr = func(e Expr) error {
			if e == nil {
				return nil
			}
			switch x := e.(type) {
			case *Unary:
				return walkExpr(x.Right)
			case *Binary:
				if q := walkExpr(x.Left); q != nil {
					return q
				}
				return walkExpr(x.Right)
			case *RangeExpr:
				if q := walkExpr(x.Start); q != nil {
					return q
				}
				return walkExpr(x.End)
			case *Call:
				name := controlExprPath(x.Callee)
				if name == "" {
					return diag("SAGA-T001", "SAGA-C489", "Production GA control code cannot use indirect/dynamic calls", x.Tok)
				}
				var target *FnDecl
				if strings.HasPrefix(name, "self.") && d.controlOwner != "" {
					methodName := strings.TrimPrefix(name, "self.")
					if !strings.Contains(methodName, ".") {
						target = d.controlMethods[methodName]
					}
				} else if !strings.Contains(name, ".") {
					target = d.controlFunctions[name]
				}
				if target != nil {
					if !machineControlSafeAnnotated(target) && !machineControlTickAnnotated(target) {
						return diag("SAGA-T001", "SAGA-C490", "control function cannot call unverified user function "+name, x.Tok)
					}
					if q := visit(target); q != nil {
						return q
					}
				} else if !strings.Contains(name, ".") {
					if !controlSafeBuiltins050[name] {
						return diag("SAGA-T001", "SAGA-C491", "Production GA control code cannot call builtin "+name, x.Tok)
					}
				} else if strings.HasPrefix(name, "machine.") {
					if !controlSafeMachine050[name] {
						return diag("SAGA-T001", "SAGA-C492", "Production GA control code cannot call "+name, x.Tok)
					}
				} else {
					return diag("SAGA-T001", "SAGA-C493", "Production GA control code cannot call external module "+name, x.Tok)
				}
				for _, a := range x.Args {
					if q := walkExpr(a); q != nil {
						return q
					}
				}
				return nil
			case *Index:
				if q := walkExpr(x.Target); q != nil {
					return q
				}
				return walkExpr(x.Index)
			case *Member:
				return walkExpr(x.Target)
			case *PropagateExpr:
				return walkExpr(x.Value)
			case *InterpolatedString:
				for _, q := range x.Exprs {
					if z := walkExpr(q); z != nil {
						return z
					}
				}
			}
			return nil
		}
		walkStmt = func(s Stmt) error {
			if s == nil {
				return nil
			}
			switch x := s.(type) {
			case *Block:
				for _, q := range x.Stmts {
					if z := walkStmt(q); z != nil {
						return z
					}
				}
			case *VarDecl:
				locals[x.Name] = true
				return walkExpr(x.Init)
			case *Assign:
				switch t := x.Target.(type) {
				case *Variable:
					if !locals[t.Name] {
						return diag("SAGA-T001", "SAGA-C487", "control function cannot mutate shared/global variable "+t.Name, t.Tok)
					}
				case *Member:
					return diag("SAGA-T001", "SAGA-C488", "control function cannot directly mutate arbitrary object fields", t.Tok)
				}
				return walkExpr(x.Value)
			case *ExprStmt:
				return walkExpr(x.Expr)
			case *IfStmt:
				if z := walkExpr(x.Cond); z != nil {
					return z
				}
				if z := walkStmt(x.Then); z != nil {
					return z
				}
				return walkStmt(x.Else)
			case *ForStmt:
				locals[x.Name] = true
				if r, ok := x.Iterable.(*RangeExpr); ok {
					a, aok := controlLiteralInt050(r.Start)
					b, bok := controlLiteralInt050(r.End)
					if aok && bok {
						delta := b - a
						if delta < 0 {
							delta = -delta
						}
						if delta > 4096 {
							return diag("SAGA-T001", "SAGA-C486", "control loop static bound exceeds 4096 iterations", x.Tok)
						}
					}
				}
				return walkStmt(x.Body)
			case *ReturnStmt:
				return walkExpr(x.Value)
			}
			return nil
		}
		if d.ExprBody != nil {
			if e := walkExpr(d.ExprBody); e != nil {
				return e
			}
		} else if e := walkStmt(d.Body); e != nil {
			return e
		}
		return nil
	}
	return visit(root)
}

func (c *Checker) validateControlTransitive050(root *FnDecl) error {
	if !machineControlTickAnnotated(root) {
		return nil
	}
	visiting := map[string]bool{}
	visited := map[string]bool{}
	var visit func(*FnDecl) error
	visit = func(d *FnDecl) error {
		if visiting[d.Name] {
			return diag("SAGA-T001", "SAGA-C485", "Production GA control call graph cannot be recursive: "+d.Name, d.Tok)
		}
		if visited[d.Name] {
			return nil
		}
		visiting[d.Name] = true
		defer func() { visiting[d.Name] = false; visited[d.Name] = true }()
		if e := validateControlRestrictedHelper050(d); e != nil {
			return e
		}
		locals := map[string]bool{}
		for _, p := range d.Params {
			locals[p.Name] = true
		}
		var walkExpr func(Expr) error
		var walkStmt func(Stmt) error
		walkExpr = func(e Expr) error {
			if e == nil {
				return nil
			}
			switch x := e.(type) {
			case *Unary:
				return walkExpr(x.Right)
			case *Binary:
				if q := walkExpr(x.Left); q != nil {
					return q
				}
				return walkExpr(x.Right)
			case *RangeExpr:
				if q := walkExpr(x.Start); q != nil {
					return q
				}
				return walkExpr(x.End)
			case *Call:
				name := controlExprPath(x.Callee)
				if name == "" {
					return diag("SAGA-T001", "SAGA-C489", "Production GA control code cannot use indirect/dynamic calls", x.Tok)
				}
				if fi, ok := c.Functions[name]; ok && fi.Decl != nil {
					if !machineControlSafeAnnotated(fi.Decl) && !machineControlTickAnnotated(fi.Decl) {
						return diag("SAGA-T001", "SAGA-C490", "control function cannot call unverified user function "+name, x.Tok)
					}
					if q := visit(fi.Decl); q != nil {
						return q
					}
				} else if !strings.Contains(name, ".") {
					if !controlSafeBuiltins050[name] {
						return diag("SAGA-T001", "SAGA-C491", "Production GA control code cannot call builtin "+name, x.Tok)
					}
				} else if strings.HasPrefix(name, "machine.") {
					if !controlSafeMachine050[name] {
						return diag("SAGA-T001", "SAGA-C492", "Production GA control code cannot call "+name, x.Tok)
					}
				} else {
					return diag("SAGA-T001", "SAGA-C493", "Production GA control code cannot call external module "+name, x.Tok)
				}
				for _, a := range x.Args {
					if q := walkExpr(a); q != nil {
						return q
					}
				}
				return nil
			case *Index:
				if q := walkExpr(x.Target); q != nil {
					return q
				}
				return walkExpr(x.Index)
			case *Member:
				return walkExpr(x.Target)
			case *PropagateExpr:
				return walkExpr(x.Value)
			case *InterpolatedString:
				for _, q := range x.Exprs {
					if z := walkExpr(q); z != nil {
						return z
					}
				}
			}
			return nil
		}
		walkStmt = func(s Stmt) error {
			if s == nil {
				return nil
			}
			switch x := s.(type) {
			case *Block:
				for _, q := range x.Stmts {
					if z := walkStmt(q); z != nil {
						return z
					}
				}
			case *VarDecl:
				locals[x.Name] = true
				return walkExpr(x.Init)
			case *Assign:
				switch t := x.Target.(type) {
				case *Variable:
					if !locals[t.Name] {
						return diag("SAGA-T001", "SAGA-C487", "control function cannot mutate shared/global variable "+t.Name, t.Tok)
					}
				case *Member:
					return diag("SAGA-T001", "SAGA-C488", "control function cannot directly mutate arbitrary object fields", t.Tok)
				}
				return walkExpr(x.Value)
			case *ExprStmt:
				return walkExpr(x.Expr)
			case *IfStmt:
				if z := walkExpr(x.Cond); z != nil {
					return z
				}
				if z := walkStmt(x.Then); z != nil {
					return z
				}
				return walkStmt(x.Else)
			case *ForStmt:
				locals[x.Name] = true
				if r, ok := x.Iterable.(*RangeExpr); ok {
					a, aok := controlLiteralInt050(r.Start)
					b, bok := controlLiteralInt050(r.End)
					if aok && bok {
						delta := b - a
						if delta < 0 {
							delta = -delta
						}
						if delta > 4096 {
							return diag("SAGA-T001", "SAGA-C486", "control loop static bound exceeds 4096 iterations", x.Tok)
						}
					}
				}
				return walkStmt(x.Body)
			case *ReturnStmt:
				return walkExpr(x.Value)
			}
			return nil
		}
		if d.ExprBody != nil {
			if e := walkExpr(d.ExprBody); e != nil {
				return e
			}
		} else if e := walkStmt(d.Body); e != nil {
			return e
		}
		return nil
	}
	return visit(root)
}
