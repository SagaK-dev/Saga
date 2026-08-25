package main

import (
	"fmt"
	"strings"
	"unicode/utf8"
)

type Parser struct {
	T                    []Token
	P                    int
	AllowTrailingClosure bool
}

func parse(tokens []Token) ([]Stmt, error) {
	p := &Parser{T: tokens, AllowTrailingClosure: true}
	out := []Stmt{}
	for !p.check(EOF) {
		s, e := p.decl()
		if e != nil {
			return nil, e
		}
		out = append(out, s)
		p.match(SEMICOLON)
	}
	bindControlScopes(out)
	return out, nil
}
func isEdition2027ContextualKind(k Kind) bool {
	return k >= EDITION && k <= COMPTIME
}
func (p *Parser) checkNext(k Kind) bool {
	return p.P+1 < len(p.T) && p.T[p.P+1].Kind == k
}
func (p *Parser) checkNextName() bool {
	return p.P+1 < len(p.T) && (p.T[p.P+1].Kind == IDENT || isEdition2027ContextualKind(p.T[p.P+1].Kind))
}
func (p *Parser) match2027Modifier(k Kind, followedBy Kind) bool {
	if p.check(k) && p.checkNext(followedBy) {
		p.advance()
		return true
	}
	return false
}

func (p *Parser) decl() (Stmt, error) {
	anns, err := p.annotations()
	if err != nil {
		return nil, err
	}
	visibility := "internal"
	if p.match(PUBLIC) {
		visibility = "public"
	} else if p.check(INTERNAL) && p.P+1 < len(p.T) && (p.T[p.P+1].Kind == CLASS || p.T[p.P+1].Kind == INTERFACE || p.T[p.P+1].Kind == RECORD || p.T[p.P+1].Kind == ENUM || p.T[p.P+1].Kind == FN || p.T[p.P+1].Kind == LET || p.T[p.P+1].Kind == VAR || p.T[p.P+1].Kind == ASYNC || p.T[p.P+1].Kind == COMPTIME || p.T[p.P+1].Kind == RESOURCE || p.T[p.P+1].Kind == EXTERN) {
		p.advance()
		visibility = "internal"
	} else if p.match(PRIVATE) {
		visibility = "private"
	}
	if p.check(EDITION) && p.checkNext(INTLIT) {
		p.advance()
		if len(anns) > 0 || visibility != "internal" {
			return nil, p.err(p.prev(), "edition directive cannot have annotations or visibility", "SAGA-P102")
		}
		tok := p.prev()
		v, e := p.need(INTLIT, "edition number")
		if e != nil {
			return nil, e
		}
		return &EditionDecl{Edition: strings.ReplaceAll(v.Lex, "_", ""), Tok: tok}, nil
	}
	if p.check(MODULE) && p.checkNextName() {
		p.advance()
		if len(anns) > 0 || visibility != "internal" {
			return nil, p.err(p.prev(), "module directive cannot have annotations or visibility", "SAGA-P102")
		}
		tok := p.prev()
		n, e := p.needContextualName("module name")
		if e != nil {
			return nil, e
		}
		return &ModuleDecl{Name: n.Lex, Tok: tok}, nil
	}
	abs := p.match(ABSTRACT)
	async := p.match2027Modifier(ASYNC, FN)
	comptime := p.match2027Modifier(COMPTIME, FN)
	resource := p.match2027Modifier(RESOURCE, CLASS)
	if p.check(EXTERN) && p.checkNext(STRING) {
		p.advance()
		if abs || async || comptime || resource {
			return nil, p.err(p.prev(), "extern cannot be combined with abstract/async/comptime/resource", "SAGA-P102")
		}
		abi, e := p.need(STRING, "extern ABI string")
		if e != nil {
			return nil, e
		}
		if _, e = p.need(FN, "fn"); e != nil {
			return nil, e
		}
		f, e := p.fnDecl(false, false, anns)
		if e != nil {
			return nil, e
		}
		f.ExternABI = abi.Lex
		f.Visibility = visibility
		if f.Body != nil || f.ExprBody != nil {
			return nil, p.err(f.Tok, "extern function cannot have a Saga body", "SAGA-P102")
		}
		return f, nil
	}
	if p.match(USE) {
		if visibility != "internal" || async || comptime || resource {
			return nil, p.err(p.prev(), "use cannot have visibility/async/comptime/resource", "SAGA-P102")
		}
		return p.useDecl(anns)
	}
	if p.match(INTERFACE) {
		if async || comptime || resource {
			return nil, p.err(p.prev(), "interface cannot be async/comptime/resource", "SAGA-P102")
		}
		x, e := p.classDecl(true, false, anns)
		if e == nil {
			x.(*ClassDecl).Visibility = visibility
		}
		return x, e
	}
	if p.match(RECORD) {
		if async || comptime || resource {
			return nil, p.err(p.prev(), "record cannot be async/comptime/resource", "SAGA-P102")
		}
		x, e := p.recordDecl(anns)
		if e == nil {
			x.(*ClassDecl).Visibility = visibility
		}
		return x, e
	}
	if p.match(ENUM) {
		if async || comptime || resource {
			return nil, p.err(p.prev(), "enum cannot be async/comptime/resource", "SAGA-P102")
		}
		x, e := p.enumDecl(anns)
		if e == nil {
			x.(*EnumDecl).Visibility = visibility
		}
		return x, e
	}
	if p.match(TEST) {
		if len(anns) > 0 || abs {
			return nil, p.err(p.prev(), "test cannot have annotations or abstract", "SAGA-P102")
		}
		return p.testDecl()
	}
	if p.match(CLASS) {
		if async || comptime {
			return nil, p.err(p.prev(), "class cannot be async/comptime", "SAGA-P102")
		}
		x, e := p.classDecl(false, abs, anns)
		if e == nil {
			c := x.(*ClassDecl)
			c.Visibility = visibility
			c.Resource = resource
		}
		return x, e
	}
	if p.match(FN) {
		if resource {
			return nil, p.err(p.prev(), "resource must modify class", "SAGA-P102")
		}
		f, e := p.fnDecl(abs, false, anns)
		if e == nil {
			f.Visibility = visibility
			f.Async = async
			f.Comptime = comptime
		}
		return f, e
	}
	if p.match(LET, VAR) {
		if abs || async || comptime || resource {
			return nil, p.err(p.prev(), "invalid declaration modifier", "SAGA-P102")
		}
		v, e := p.varDecl(p.prev().Kind == VAR, anns)
		if e == nil {
			v.(*VarDecl).Visibility = visibility
		}
		return v, e
	}
	if len(anns) > 0 || abs || async || comptime || resource || visibility != "internal" {
		return nil, p.err(p.peek(), "annotations/abstract require a declaration", "SAGA-P102")
	}
	return p.statement()
}
func (p *Parser) annotations() ([]Annotation, error) {
	out := []Annotation{}
	for p.match(AT) {
		at := p.prev()
		n, e := p.needContextualName("annotation name")
		if e != nil {
			return nil, e
		}
		a := Annotation{Name: n.Lex, Tok: at}
		if p.match(LPAREN) {
			if !p.check(RPAREN) {
				for {
					v, e := p.annotationValue()
					if e != nil {
						return nil, e
					}
					a.Args = append(a.Args, v)
					if !p.match(COMMA) {
						break
					}
				}
			}
			if _, e = p.need(RPAREN, ")"); e != nil {
				return nil, e
			}
		}
		out = append(out, a)
	}
	return out, nil
}
func (p *Parser) annotationValue() (Expr, error) {
	if p.match(INTLIT, DECLIT, STRING, TRUE, FALSE) {
		return p.literalFrom(p.prev())
	}
	if p.match(LBRACKET) {
		tok := p.prev()
		items := []Expr{}
		if !p.check(RBRACKET) {
			for {
				v, e := p.annotationValue()
				if e != nil {
					return nil, e
				}
				items = append(items, v)
				if !p.match(COMMA) {
					break
				}
			}
		}
		if _, e := p.need(RBRACKET, "]"); e != nil {
			return nil, e
		}
		return &ListExpr{Items: items, Tok: tok}, nil
	}
	return nil, p.err(p.peek(), "annotation arguments must be literals", "SAGA-P102")
}
func (p *Parser) testDecl() (Stmt, error) {
	tok := p.prev()
	n, e := p.need(STRING, "test name")
	if e != nil {
		return nil, e
	}
	b, e := p.block()
	if e != nil {
		return nil, e
	}
	return &TestDecl{Name: n.Lex, Body: b, Tok: tok}, nil
}
func (p *Parser) useDecl(_ []Annotation) (Stmt, error) {
	tok := p.prev()
	alias := ""
	if p.match(IDENT) {
		m := p.prev().Lex
		if p.match(AS) {
			a, e := p.needContextualName("module alias")
			if e != nil {
				return nil, e
			}
			alias = a.Lex
		}
		return &UseStmt{Module: m, Alias: alias, Tok: tok}, nil
	}
	if p.match(STRING) {
		path := p.prev().Lex
		if p.match(AS) {
			a, e := p.needContextualName("module alias")
			if e != nil {
				return nil, e
			}
			alias = a.Lex
		}
		return &UseStmt{SourcePath: path, Alias: alias, Tok: tok}, nil
	}
	return nil, p.err(p.peek(), "use requires a module identifier or source path", "SAGA-P102")
}

func (p *Parser) constraints(typeParams []string) ([]TypeConstraint, error) {
	if !p.match(WHERE) {
		return nil, nil
	}
	valid := map[string]bool{}
	for _, n := range typeParams {
		valid[n] = true
	}
	out := []TypeConstraint{}
	for {
		n, e := p.needContextualName("constrained type parameter")
		if e != nil {
			return nil, e
		}
		if !valid[n.Lex] {
			return nil, p.err(n, "where clause refers to unknown type parameter "+n.Lex, "SAGA-P102")
		}
		if _, e = p.need(COLON, ":"); e != nil {
			return nil, e
		}
		c := TypeConstraint{Param: n.Lex}
		for {
			t, e := p.typeRef()
			if e != nil {
				return nil, e
			}
			c.Types = append(c.Types, t)
			if !p.match(PLUS) {
				break
			}
		}
		out = append(out, c)
		if !p.match(COMMA) {
			break
		}
	}
	return out, nil
}
func (p *Parser) varDecl(mut bool, anns []Annotation) (Stmt, error) {
	tok := p.prev()
	n, e := p.needContextualName("variable name")
	if e != nil {
		return nil, e
	}
	var tr *TypeRef
	if p.match(COLON) {
		t, e := p.typeRef()
		if e != nil {
			return nil, e
		}
		tr = &t
	}
	if _, e = p.need(EQUAL, "="); e != nil {
		return nil, e
	}
	x, e := p.expression()
	if e != nil {
		return nil, e
	}
	return &VarDecl{Name: n.Lex, Mutable: mut, Type: tr, Init: x, Annotations: anns, Tok: tok}, nil
}
func (p *Parser) typeParams() ([]string, error) {
	if !p.match(LBRACKET) {
		return nil, nil
	}
	out := []string{}
	for {
		n, e := p.needContextualName("type parameter")
		if e != nil {
			return nil, e
		}
		out = append(out, n.Lex)
		if !p.match(COMMA) {
			break
		}
	}
	_, e := p.need(RBRACKET, "]")
	return out, e
}
func (p *Parser) needContextualName(what string) (Token, error) {
	if p.check(IDENT) || isEdition2027ContextualKind(p.peek().Kind) {
		return p.advance(), nil
	}
	return Token{}, diag("parse", "SAGA-P001", "expected "+what, p.peek())
}

func (p *Parser) typeRef() (TypeRef, error) {
	var n Token
	var e error
	if p.check(FN) {
		n = p.advance()
	} else {
		n, e = p.needContextualName("type")
	}
	if e != nil {
		return TypeRef{}, e
	}
	t := TypeRef{Name: n.Lex, Tok: n}
	// Associated types use a readable dotted form (for example T.Item). The
	// parser stores the qualified spelling; the checker resolves it against the
	// concrete type's associated-type map during substitution.
	if p.match(DOT) {
		a, ae := p.needContextualName("associated type name")
		if ae != nil {
			return TypeRef{}, ae
		}
		t.Name += "." + a.Lex
	}
	if p.match(LBRACKET) {
		if !p.check(RBRACKET) {
			for {
				a, e := p.typeRef()
				if e != nil {
					return TypeRef{}, e
				}
				t.Args = append(t.Args, a)
				if !p.match(COMMA) {
					break
				}
			}
		}
		if _, e = p.need(RBRACKET, "]"); e != nil {
			return TypeRef{}, e
		}
	}
	return t, nil
}
func (p *Parser) fnDecl(abs, override bool, anns []Annotation) (*FnDecl, error) {
	name, e := p.needContextualName("function name")
	if e != nil {
		return nil, e
	}
	return p.fnTail(name, abs, override, anns)
}
func (p *Parser) fnTail(name Token, abs, override bool, anns []Annotation) (*FnDecl, error) {
	tps, e := p.typeParams()
	if e != nil {
		return nil, e
	}
	if _, e = p.need(LPAREN, "("); e != nil {
		return nil, e
	}
	params := []Param{}
	if !p.check(RPAREN) {
		for {
			n, e := p.needContextualName("parameter name")
			if e != nil {
				return nil, e
			}
			if _, e = p.need(COLON, ":"); e != nil {
				return nil, e
			}
			tr, e := p.typeRef()
			if e != nil {
				return nil, e
			}
			params = append(params, Param{Name: n.Lex, Type: tr, Tok: n})
			if !p.match(COMMA) {
				break
			}
		}
	}
	if _, e = p.need(RPAREN, ")"); e != nil {
		return nil, e
	}
	var ret *TypeRef
	if p.match(ARROW) {
		r, e := p.typeRef()
		if e != nil {
			return nil, e
		}
		ret = &r
	}
	constraints, e := p.constraints(tps)
	if e != nil {
		return nil, e
	}
	fn := &FnDecl{Name: name.Lex, TypeParams: tps, Params: params, Return: ret, Annotations: anns, Constraints: constraints, Abstract: abs, Override: override, Tok: name}
	if p.match(EQUAL) {
		x, e := p.expression()
		fn.ExprBody = x
		return fn, e
	}
	if p.check(LBRACE) {
		b, e := p.block()
		fn.Body = b
		return fn, e
	}
	if abs || p.check(SEMICOLON) || p.check(RBRACE) {
		p.match(SEMICOLON)
		return fn, nil
	}
	return nil, p.err(p.peek(), "function requires a body or expression", "SAGA-P102")
}
func (p *Parser) recordDecl(anns []Annotation) (Stmt, error) {
	name, e := p.needContextualName("record name")
	if e != nil {
		return nil, e
	}
	tps, e := p.typeParams()
	if e != nil {
		return nil, e
	}
	c := &ClassDecl{Name: name.Lex, TypeParams: tps, Annotations: anns, Record: true, Tok: name}
	if _, e = p.need(LPAREN, "("); e != nil {
		return nil, e
	}
	if !p.check(RPAREN) {
		for {
			n, e := p.needContextualName("field name")
			if e != nil {
				return nil, e
			}
			if _, e = p.need(COLON, ":"); e != nil {
				return nil, e
			}
			tr, e := p.typeRef()
			if e != nil {
				return nil, e
			}
			c.Fields = append(c.Fields, FieldDecl{Name: n.Lex, Type: tr, Mutable: false, Private: false, Tok: n})
			if !p.match(COMMA) {
				break
			}
		}
	}
	if _, e = p.need(RPAREN, ")"); e != nil {
		return nil, e
	}
	return c, nil
}
func (p *Parser) enumDecl(anns []Annotation) (Stmt, error) {
	if len(anns) > 0 {
		return nil, p.err(p.peek(), "enum annotations are not yet supported", "SAGA-P102")
	}
	name, e := p.needContextualName("enum name")
	if e != nil {
		return nil, e
	}
	typeParams, e := p.typeParams()
	if e != nil {
		return nil, e
	}
	d := &EnumDecl{Name: name.Lex, TypeParams: typeParams, Tok: name}
	if _, e = p.need(LBRACE, "{"); e != nil {
		return nil, e
	}
	for !p.check(RBRACE) {
		v, e := p.needContextualName("enum variant")
		if e != nil {
			return nil, e
		}
		variant := EnumVariant{Name: v.Lex, Tok: v}
		if p.match(LPAREN) {
			if !p.check(RPAREN) {
				for {
					t, te := p.typeRef()
					if te != nil {
						return nil, te
					}
					variant.Payload = append(variant.Payload, t)
					if !p.match(COMMA) {
						break
					}
				}
			}
			if _, e = p.need(RPAREN, ")"); e != nil {
				return nil, e
			}
		}
		d.Variants = append(d.Variants, variant)
		if p.match(COMMA) {
			continue
		}
		if !p.check(RBRACE) {
			return nil, p.err(p.peek(), "expected ',' or '}' in enum", "SAGA-P102")
		}
	}
	p.advance()
	if len(d.Variants) == 0 {
		return nil, p.err(name, "enum requires at least one variant", "SAGA-P102")
	}
	return d, nil
}
func (p *Parser) classDecl(iface, abs bool, anns []Annotation) (Stmt, error) {
	name, e := p.needContextualName("class/interface name")
	if e != nil {
		return nil, e
	}
	tps, e := p.typeParams()
	if e != nil {
		return nil, e
	}
	c := &ClassDecl{Name: name.Lex, TypeParams: tps, Annotations: anns, Abstract: abs || iface, Interface: iface, Tok: name, AssociatedTypes: map[string]*TypeRef{}}
	if iface {
		constraints, ce := p.constraints(tps)
		if ce != nil {
			return nil, ce
		}
		c.Constraints = constraints
		if _, e = p.need(LBRACE, "{"); e != nil {
			return nil, e
		}
		for !p.check(RBRACE) {
			if p.check(TYPE) && p.checkNextName() {
				p.advance()
				tn, te := p.needContextualName("associated type name")
				if te != nil {
					return nil, te
				}
				if _, exists := c.AssociatedTypes[tn.Lex]; exists {
					return nil, p.err(tn, "duplicate associated type "+tn.Lex, "SAGA-P102")
				}
				c.RequiredAssocTypes = append(c.RequiredAssocTypes, tn.Lex)
				c.AssociatedTypes[tn.Lex] = nil
				p.match(SEMICOLON)
				continue
			}
			ma, e := p.annotations()
			if e != nil {
				return nil, e
			}
			masync := p.match2027Modifier(ASYNC, FN)
			mcomp := p.match2027Modifier(COMPTIME, FN)
			if _, e = p.need(FN, "fn"); e != nil {
				return nil, e
			}
			mn, e := p.needContextualName("method name")
			if e != nil {
				return nil, e
			}
			m, e := p.fnTail(mn, true, false, ma)
			if e != nil {
				return nil, e
			}
			m.Async, m.Comptime = masync, mcomp
			c.Methods = append(c.Methods, m)
		}
		p.advance()
		return c, nil
	}
	if p.match(LPAREN) {
		if !p.check(RPAREN) {
			for {
				priv := p.match(PRIVATE)
				if !priv {
					p.match(PUBLIC)
				}
				mut := false
				if p.match(LET, VAR) {
					mut = p.prev().Kind == VAR
				}
				n, e := p.needContextualName("field name")
				if e != nil {
					return nil, e
				}
				if _, e = p.need(COLON, ":"); e != nil {
					return nil, e
				}
				tr, e := p.typeRef()
				if e != nil {
					return nil, e
				}
				c.Fields = append(c.Fields, FieldDecl{Name: n.Lex, Type: tr, Mutable: mut, Private: priv, Tok: n})
				if !p.match(COMMA) {
					break
				}
			}
		}
		if _, e = p.need(RPAREN, ")"); e != nil {
			return nil, e
		}
	}
	if p.match(EXTENDS) {
		b, e := p.typeRef()
		if e != nil {
			return nil, e
		}
		c.Base = &b
	}
	if p.match(IMPLEMENTS) {
		for {
			q, e := p.typeRef()
			if e != nil {
				return nil, e
			}
			c.Interfaces = append(c.Interfaces, q)
			if !p.match(COMMA) {
				break
			}
		}
	}
	constraints, ce := p.constraints(tps)
	if ce != nil {
		return nil, ce
	}
	c.Constraints = constraints
	if _, e = p.need(LBRACE, "{"); e != nil {
		return nil, e
	}
	for !p.check(RBRACE) {
		if p.check(TYPE) && p.checkNextName() {
			p.advance()
			tn, te := p.needContextualName("associated type name")
			if te != nil {
				return nil, te
			}
			if _, te = p.need(EQUAL, "="); te != nil {
				return nil, te
			}
			tr, te := p.typeRef()
			if te != nil {
				return nil, te
			}
			if _, exists := c.AssociatedTypes[tn.Lex]; exists {
				return nil, p.err(tn, "duplicate associated type "+tn.Lex, "SAGA-P102")
			}
			copy := tr
			c.AssociatedTypes[tn.Lex] = &copy
			p.match(SEMICOLON)
			continue
		}
		ma, e := p.annotations()
		if e != nil {
			return nil, e
		}
		mabs := p.match(ABSTRACT)
		over := false
		if !mabs {
			over = p.match(OVERRIDE)
		}
		masync := p.match2027Modifier(ASYNC, FN)
		mcomp := p.match2027Modifier(COMPTIME, FN)
		if _, e = p.need(FN, "fn"); e != nil {
			return nil, e
		}
		mn, e := p.needContextualName("method name")
		if e != nil {
			return nil, e
		}
		m, e := p.fnTail(mn, mabs, over, ma)
		if e != nil {
			return nil, e
		}
		m.Async, m.Comptime = masync, mcomp
		c.Methods = append(c.Methods, m)
	}
	p.advance()
	return c, nil
}
func (p *Parser) statement() (Stmt, error) {
	if p.check(DEFER) && p.contextualPrefixActive() {
		p.advance()
		tok := p.prev()
		x, e := p.expression()
		return &DeferStmt{Value: x, Tok: tok}, e
	}
	if p.check(USING) && p.checkNextName() {
		p.advance()
		tok := p.prev()
		n, e := p.needContextualName("resource binding name")
		if e != nil {
			return nil, e
		}
		if _, e = p.need(EQUAL, "="); e != nil {
			return nil, e
		}
		// The following `{` belongs to the `using` scope, not to the
		// initializer as a Natural trailing closure. Keep this in lockstep
		// with Python's control-header expression parsing.
		init, e := p.controlExpression()
		if e != nil {
			return nil, e
		}
		body, e := p.block()
		if e != nil {
			return nil, e
		}
		return &UsingStmt{Name: n.Lex, Init: init, Body: body, Tok: tok}, nil
	}
	if p.check(UNSAFE) && p.checkNext(LBRACE) {
		p.advance()
		tok := p.prev()
		body, e := p.block()
		return &UnsafeStmt{Body: body, Tok: tok}, e
	}
	if p.check(TASKGROUP) && p.checkNext(LBRACE) {
		p.advance()
		tok := p.prev()
		body, e := p.block()
		return &TaskGroupStmt{Body: body, Tok: tok}, e
	}
	if p.match(MATCH) {
		return p.matchStmt()
	}
	if p.match(UNLESS) {
		tok := p.prev()
		c, e := p.controlExpression()
		if e != nil {
			return nil, e
		}
		b, e := p.block()
		if e != nil {
			return nil, e
		}
		var alt Stmt
		if p.match(ELSE) {
			alt, e = p.block()
			if e != nil {
				return nil, e
			}
		}
		notTok := Token{Kind: NOT, Lex: "not", Line: tok.Line, Col: tok.Col, File: tok.File}
		return &IfStmt{Cond: &Unary{Op: notTok, Right: c}, Then: b, Else: alt, Tok: tok}, nil
	}
	if p.match(IF) {
		return p.ifStmt()
	}
	if p.match(WHILE) {
		return p.whileStmt()
	}
	if p.match(FOR) {
		return p.forStmt()
	}
	if p.match(TRY) {
		return p.tryStmt()
	}
	if p.match(THROW) {
		tok := p.prev()
		x, e := p.expression()
		return &ThrowStmt{Value: x, Tok: tok}, e
	}
	if p.match(RETURN) {
		tok := p.prev()
		if p.check(RBRACE) || p.check(SEMICOLON) {
			p.match(SEMICOLON)
			return &ReturnStmt{Tok: tok}, nil
		}
		x, e := p.expression()
		return &ReturnStmt{Value: x, Tok: tok}, e
	}
	if p.match(BREAK) {
		return &BreakStmt{Tok: p.prev()}, nil
	}
	if p.match(CONTINUE) {
		return &ContinueStmt{Tok: p.prev()}, nil
	}
	if p.check(LBRACE) {
		return p.block()
	}
	x, e := p.expression()
	if e != nil {
		return nil, e
	}
	if p.match(EQUAL) {
		eq := p.prev()
		switch x.(type) {
		case *Variable, *Member:
		default:
			return nil, p.err(eq, "invalid assignment target", "SAGA-P102")
		}
		v, e := p.expression()
		return &Assign{Target: x, Value: v, Tok: eq}, e
	}
	return &ExprStmt{Expr: x, Tok: x.token()}, nil
}
func (p *Parser) matchStmt() (Stmt, error) {
	tok := p.prev()
	value, e := p.controlExpression()
	if e != nil {
		return nil, e
	}
	if _, e = p.need(LBRACE, "{"); e != nil {
		return nil, e
	}
	m := &MatchStmt{Value: value, Tok: tok}
	for !p.check(RBRACE) && !p.check(EOF) {
		if p.match(CASE) {
			ct := p.prev()
			pat, e := p.controlExpression()
			if e != nil {
				return nil, e
			}
			b, e := p.block()
			if e != nil {
				return nil, e
			}
			m.Cases = append(m.Cases, MatchCase{Pattern: pat, Body: b, Tok: ct})
			continue
		}
		if p.match(DEFAULT) {
			if m.Default != nil {
				return nil, p.err(p.prev(), "duplicate default case", "SAGA-P102")
			}
			b, e := p.block()
			if e != nil {
				return nil, e
			}
			m.Default = b
			continue
		}
		return nil, p.err(p.peek(), "match requires case or default", "SAGA-P102")
	}
	if _, e = p.need(RBRACE, "}"); e != nil {
		return nil, e
	}
	if len(m.Cases) == 0 && m.Default == nil {
		return nil, p.err(tok, "match requires at least one case", "SAGA-P102")
	}
	return m, nil
}
func (p *Parser) ifStmt() (Stmt, error) {
	tok := p.prev()
	c, e := p.controlExpression()
	if e != nil {
		return nil, e
	}
	b, e := p.block()
	if e != nil {
		return nil, e
	}
	var other Stmt
	if p.match(ELSE) {
		if p.match(IF) {
			other, e = p.ifStmt()
		} else {
			other, e = p.block()
		}
	}
	return &IfStmt{Cond: c, Then: b, Else: other, Tok: tok}, e
}
func (p *Parser) whileStmt() (Stmt, error) {
	tok := p.prev()
	c, e := p.controlExpression()
	if e != nil {
		return nil, e
	}
	b, e := p.block()
	return &WhileStmt{Cond: c, Body: b, Tok: tok}, e
}
func (p *Parser) forStmt() (Stmt, error) {
	tok := p.prev()
	n, e := p.needContextualName("loop variable")
	if e != nil {
		return nil, e
	}
	if _, e = p.need(IN, "in"); e != nil {
		return nil, e
	}
	it, e := p.controlExpression()
	if e != nil {
		return nil, e
	}
	b, e := p.block()
	return &ForStmt{Name: n.Lex, Iterable: it, Body: b, Tok: tok}, e
}

func (p *Parser) controlExpression() (Expr, error) {
	old := p.AllowTrailingClosure
	p.AllowTrailingClosure = false
	defer func() { p.AllowTrailingClosure = old }()
	return p.expression()
}
func (p *Parser) tryStmt() (Stmt, error) {
	tok := p.prev()
	body, e := p.block()
	if e != nil {
		return nil, e
	}
	t := &TryStmt{Try: body, Tok: tok}
	if p.match(CATCH) {
		n, e := p.needContextualName("catch variable")
		if e != nil {
			return nil, e
		}
		t.CatchName = n.Lex
		t.Catch, e = p.block()
		if e != nil {
			return nil, e
		}
	}
	if p.match(FINALLY) {
		t.Finally, e = p.block()
		if e != nil {
			return nil, e
		}
	}
	if t.Catch == nil && t.Finally == nil {
		return nil, p.err(tok, "try requires catch or finally", "SAGA-P102")
	}
	return t, nil
}
func (p *Parser) block() (*Block, error) {
	open, e := p.need(LBRACE, "{")
	if e != nil {
		return nil, e
	}
	b := &Block{Tok: open}
	for !p.check(RBRACE) && !p.check(EOF) {
		s, e := p.decl()
		if e != nil {
			return nil, e
		}
		b.Stmts = append(b.Stmts, s)
		p.match(SEMICOLON)
	}
	if _, e = p.need(RBRACE, "}"); e != nil {
		return nil, e
	}
	return b, nil
}
func (p *Parser) expression() (Expr, error) { return p.pipeline() }

func (p *Parser) pipeline() (Expr, error) {
	x, e := p.logicalOr()
	for e == nil && p.match(PIPE) {
		op := p.prev()
		stage, er := p.logicalOr()
		if er != nil {
			return nil, er
		}
		// In a pipeline, a bare stage name may take a trailing closure even
		// though a bare identifier does not normally consume `{ ... }`.
		if q, ok := stage.(*Variable); ok && p.AllowTrailingClosure && p.check(LBRACE) {
			cl, ce := p.closure()
			if ce != nil {
				return nil, ce
			}
			stage = &Call{Callee: q, Args: []Expr{cl}, Tok: cl.token()}
		}

		switch s := stage.(type) {
		case *Variable:
			if naturalPipelineStage(s.Name) {
				x = &Call{Callee: &Member{Target: x, Name: s.Name, Tok: op}, Tok: op}
			} else {
				x = &Call{Callee: s, Args: []Expr{x}, Tok: op}
			}
		case *Call:
			if q, ok := s.Callee.(*Variable); ok {
				if naturalPipelineStage(q.Name) {
					x = &Call{Callee: &Member{Target: x, Name: q.Name, Tok: op}, Args: s.Args, Tok: s.Tok}
					continue
				}
				// Transitional callback-first HOFs preserve their historical
				// argument order while participating in `|>`.
				switch q.Name {
				case "reduce", "find":
					args := append([]Expr{}, s.Args...)
					if len(args) > 0 {
						args = append(args[:1], append([]Expr{x}, args[1:]...)...)
					} else {
						args = []Expr{x}
					}
					s.Args = args
					x = s
					continue
				case "transform", "filter", "any", "all":
					s.Args = append(s.Args, x)
					x = s
					continue
				}
			}
			s.Args = append([]Expr{x}, s.Args...)
			x = s
		default:
			x = &Call{Callee: stage, Args: []Expr{x}, Tok: op}
		}
	}
	return x, e
}

func naturalPipelineStage(name string) bool {
	// Keep this list in lockstep with the reference parser. Predicate-style
	// filter/any/all intentionally remain on the transitional functional path
	// so historical callback-first pipeline code keeps its argument order.
	switch name {
	case "map", "each", "fold", "none", "sorted", "sortedBy", "distinct",
		"take", "skip", "zip", "flatten", "flatMap", "chunk", "window",
		"group", "groupBy", "sum", "contains":
		return true
	}
	return false
}
func (p *Parser) logicalOr() (Expr, error) {
	x, e := p.logicalAnd()
	for e == nil && p.match(OR) {
		op := p.prev()
		r, er := p.logicalAnd()
		if er != nil {
			return nil, er
		}
		x = &Binary{Left: x, Op: op, Right: r}
	}
	return x, e
}
func (p *Parser) logicalAnd() (Expr, error) {
	x, e := p.equality()
	for e == nil && p.match(AND) {
		op := p.prev()
		r, er := p.equality()
		if er != nil {
			return nil, er
		}
		x = &Binary{Left: x, Op: op, Right: r}
	}
	return x, e
}
func (p *Parser) equality() (Expr, error) {
	x, e := p.comparison()
	for e == nil && p.match(EQEQ, BANGEQ) {
		op := p.prev()
		r, er := p.comparison()
		if er != nil {
			return nil, er
		}
		x = &Binary{Left: x, Op: op, Right: r}
	}
	return x, e
}
func (p *Parser) comparison() (Expr, error) {
	x, e := p.rangeExpr()
	for e == nil && p.match(LESS, LESSEQ, GREATER, GREATEREQ) {
		op := p.prev()
		r, er := p.rangeExpr()
		if er != nil {
			return nil, er
		}
		x = &Binary{Left: x, Op: op, Right: r}
	}
	return x, e
}
func (p *Parser) rangeExpr() (Expr, error) {
	x, e := p.additive()
	if e == nil && p.match(RANGE) {
		op := p.prev()
		r, er := p.additive()
		if er != nil {
			return nil, er
		}
		x = &RangeExpr{Start: x, Op: op, End: r}
	}
	return x, e
}
func (p *Parser) additive() (Expr, error) {
	x, e := p.multiplicative()
	for e == nil && p.match(PLUS, MINUS) {
		op := p.prev()
		r, er := p.multiplicative()
		if er != nil {
			return nil, er
		}
		x = &Binary{Left: x, Op: op, Right: r}
	}
	return x, e
}
func (p *Parser) multiplicative() (Expr, error) {
	x, e := p.unary()
	for e == nil && p.match(STAR, SLASH, PERCENT) {
		op := p.prev()
		r, er := p.unary()
		if er != nil {
			return nil, er
		}
		x = &Binary{Left: x, Op: op, Right: r}
	}
	return x, e
}
func (p *Parser) contextualPrefixActive() bool {
	if p.P+1 >= len(p.T) {
		return false
	}
	if p.T[p.P+1].Line != p.peek().Line {
		return false
	}
	switch p.T[p.P+1].Kind {
	case LPAREN, RPAREN, LBRACKET, RBRACKET, RBRACE, COMMA, DOT, QUESTION, EQUAL, EQEQ, BANGEQ, LESS, LESSEQ, GREATER, GREATEREQ, AND, OR, RANGE, PLUS, MINUS, STAR, SLASH, PERCENT, SEMICOLON, EOF:
		return false
	}
	return true
}

func (p *Parser) unary() (Expr, error) {
	if p.check(AWAIT) && p.contextualPrefixActive() {
		p.advance()
		tok := p.prev()
		r, e := p.unary()
		return &AwaitExpr{Value: r, Tok: tok}, e
	}
	if p.check(MOVE) && p.contextualPrefixActive() {
		p.advance()
		tok := p.prev()
		r, e := p.unary()
		return &MoveExpr{Value: r, Tok: tok}, e
	}
	if p.match(BANG, NOT, MINUS) {
		op := p.prev()
		r, e := p.unary()
		return &Unary{Op: op, Right: r}, e
	}
	return p.power()
}
func (p *Parser) power() (Expr, error) {
	x, e := p.postfix()
	if e == nil && p.match(POWER) {
		op := p.prev()
		r, er := p.unary()
		if er != nil {
			return nil, er
		}
		x = &Binary{Left: x, Op: op, Right: r}
	}
	return x, e
}
func (p *Parser) postfix() (Expr, error) {
	x, e := p.primary()
	if e != nil {
		return nil, e
	}
	for {
		if p.match(LPAREN) {
			tok := p.prev()
			args := []Expr{}
			if !p.check(RPAREN) {
				for {
					a, e := p.expression()
					if e != nil {
						return nil, e
					}
					args = append(args, a)
					if !p.match(COMMA) {
						break
					}
				}
			}
			if _, e = p.need(RPAREN, ")"); e != nil {
				return nil, e
			}
			x = &Call{Callee: x, Args: args, Tok: tok}
			continue
		}
		if p.match(LBRACKET) {
			tok := p.prev()
			i, e := p.expression()
			if e != nil {
				return nil, e
			}
			if _, e = p.need(RBRACKET, "]"); e != nil {
				return nil, e
			}
			x = &Index{Target: x, Index: i, Tok: tok}
			continue
		}
		if p.match(DOT) {
			n, e := p.needContextualName("member name")
			if e != nil {
				return nil, e
			}
			x = &Member{Target: x, Name: n.Lex, Tok: n}
			continue
		}
		if p.match(QUESTION) {
			x = &PropagateExpr{Value: x, Tok: p.prev()}
			continue
		}
		if p.canStartBareArgument(x) {
			tok := p.peek()
			first, be := p.logicalOr()
			if be != nil {
				return nil, be
			}
			args := []Expr{first}
			for p.match(COMMA) {
				a, ae := p.logicalOr()
				if ae != nil {
					return nil, ae
				}
				args = append(args, a)
			}
			x = &Call{Callee: x, Args: args, Tok: tok}
			continue
		}
		if p.AllowTrailingClosure && p.check(LBRACE) {
			switch q := x.(type) {
			case *Member:
				cl, ce := p.closure()
				if ce != nil {
					return nil, ce
				}
				x = &Call{Callee: q, Args: []Expr{cl}, Tok: cl.token()}
				continue
			case *Call:
				cl, ce := p.closure()
				if ce != nil {
					return nil, ce
				}
				q.Args = append(q.Args, cl)
				x = q
				continue
			}
		}
		break
	}
	return x, nil
}

func (p *Parser) canStartBareArgument(x Expr) bool {
	// Bare arguments are a deliberately small, same-line convenience syntax.
	// Keeping unary operators out of the start set is what prevents `n - 1`
	// from being reinterpreted as a call to `n` with argument `-1`.
	var callee Token
	switch q := x.(type) {
	case *Variable:
		callee = q.Tok
	case *Member:
		callee = q.Tok
	default:
		return false
	}
	if p.peek().Line != callee.Line {
		return false
	}
	switch p.peek().Kind {
	case FALSE, TRUE, INTLIT, DECLIT, STRING, IDENT, LBRACKET, LPAREN:
		return true
	default:
		return false
	}
}

func (p *Parser) closure() (Expr, error) {
	open, e := p.need(LBRACE, "{")
	if e != nil {
		return nil, e
	}
	params := []Token{}
	implicit := true
	mark := p.P
	if p.check(IDENT) {
		candidate := []Token{p.advance()}
		for p.match(COMMA) {
			if !p.check(IDENT) {
				candidate = nil
				break
			}
			candidate = append(candidate, p.advance())
		}
		if len(candidate) > 0 && p.match(ARROW) {
			seen := map[string]bool{}
			for _, param := range candidate {
				if seen[param.Lex] {
					return nil, p.err(param, "duplicate closure parameter "+param.Lex, "SAGA-P001")
				}
				seen[param.Lex] = true
			}
			params = candidate
			implicit = false
		} else {
			p.P = mark
		}
	}
	body := &Block{Tok: open}
	for !p.check(RBRACE) && !p.check(EOF) {
		s, er := p.decl()
		if er != nil {
			return nil, er
		}
		body.Stmts = append(body.Stmts, s)
		p.match(SEMICOLON)
	}
	if _, e = p.need(RBRACE, "}"); e != nil {
		return nil, e
	}
	return &ClosureExpr{Params: params, Body: body, Implicit: implicit, Tok: open}, nil
}
func (p *Parser) primary() (Expr, error) {
	if p.match(INTLIT, DECLIT, FLOAT32LIT, FLOAT64LIT, STRING, INTERPSTRING, TRUE, FALSE) {
		return p.literalFrom(p.prev())
	}
	if p.match(IDENT) || isEdition2027ContextualKind(p.peek().Kind) && func() bool { p.advance(); return true }() {
		t := p.prev()
		return &Variable{Name: t.Lex, Tok: t}, nil
	}
	if p.check(LBRACE) {
		return p.closure()
	}
	if p.match(LBRACKET) {
		tok := p.prev()
		items := []Expr{}
		if !p.check(RBRACKET) {
			for {
				a, e := p.expression()
				if e != nil {
					return nil, e
				}
				items = append(items, a)
				if !p.match(COMMA) {
					break
				}
			}
		}
		if _, e := p.need(RBRACKET, "]"); e != nil {
			return nil, e
		}
		return &ListExpr{Items: items, Tok: tok}, nil
	}
	if p.match(LPAREN) {
		x, e := p.expression()
		if e != nil {
			return nil, e
		}
		_, e = p.need(RPAREN, ")")
		return x, e
	}
	return nil, p.err(p.peek(), "expected expression", "SAGA-P102")
}
func (p *Parser) literalFrom(t Token) (Expr, error) {
	switch t.Kind {
	case STRING:
		return &Literal{Value: t.Lex, Tok: t}, nil
	case INTERPSTRING:
		return parseInterpolatedString(t)
	case TRUE:
		return &Literal{Value: true, Tok: t}, nil
	case FALSE:
		return &Literal{Value: false, Tok: t}, nil
	case INTLIT, DECLIT:
		n, e := newNumber(t.Lex, map[bool]string{true: "decimal", false: "int"}[t.Kind == DECLIT])
		if e != nil {
			return nil, p.err(t, e.Error(), "SAGA-L103")
		}
		return &Literal{Value: n, Tok: t}, nil
	case FLOAT32LIT, FLOAT64LIT:
		return parseFloatLiteral(t)
	}
	return nil, fmt.Errorf("not literal")
}

func parseFloatLiteral(t Token) (Expr, error) {
	bits := 64
	if t.Kind == FLOAT32LIT {
		bits = 32
	}
	f, err := newFloatValue(t.Lex, bits)
	if err != nil {
		return nil, diag("SAGA-L001", "SAGA-L103", "invalid floating-point literal", t)
	}
	return &Literal{Value: f, Tok: t}, nil
}
func (p *Parser) match(k ...Kind) bool {
	for _, x := range k {
		if p.check(x) {
			p.advance()
			return true
		}
	}
	return false
}
func (p *Parser) check(k Kind) bool { return p.peek().Kind == k }
func (p *Parser) advance() Token {
	t := p.peek()
	if t.Kind != EOF {
		p.P++
	}
	return t
}
func (p *Parser) peek() Token { return p.T[p.P] }
func (p *Parser) prev() Token { return p.T[p.P-1] }
func (p *Parser) need(k Kind, label string) (Token, error) {
	if p.check(k) {
		return p.advance(), nil
	}
	id := "SAGA-P001"
	if k == RPAREN || k == RBRACKET || k == RBRACE {
		id = "SAGA-P101"
	}
	return Token{}, p.err(p.peek(), "expected "+label, id)
}
func (p *Parser) err(t Token, msg, id string) error { return diag("SAGA-P001", id, msg, t) }

func parseInterpolatedString(t Token) (Expr, error) {
	body := t.Lex
	texts := []string{}
	exprs := []Expr{}
	start := 0
	for {
		j := strings.Index(body[start:], "${")
		if j < 0 {
			texts = append(texts, body[start:])
			break
		}
		j += start
		texts = append(texts, body[start:j])
		k := j + 2
		depth := 1
		quote := rune(0)
		escaped := false
		for k < len(body) && depth > 0 {
			r, size := utf8.DecodeRuneInString(body[k:])
			if quote != 0 {
				if escaped {
					escaped = false
				} else if r == '\\' {
					escaped = true
				} else if r == quote {
					quote = 0
				}
				k += size
				continue
			}
			if r == '"' || r == '\'' {
				quote = r
				k += size
				continue
			}
			if r == '{' {
				depth++
			}
			if r == '}' {
				depth--
				if depth == 0 {
					break
				}
			}
			k += size
		}
		if depth != 0 {
			return nil, &SagaError{"SAGA-P001", "SAGA-P103", "unterminated ${...} in interpolated string", t.File, t.Line, t.Col}
		}
		frag := strings.TrimSpace(body[j+2 : k])
		if frag == "" {
			return nil, &SagaError{"SAGA-P001", "SAGA-P102", "empty interpolation expression", t.File, t.Line, t.Col}
		}
		toks, err := lex(frag, t.File)
		if err != nil {
			return nil, err
		}
		pp := &Parser{T: toks}
		ex, err := pp.expression()
		if err != nil {
			return nil, err
		}
		if !pp.check(EOF) {
			return nil, pp.err(pp.peek(), "unexpected token in interpolation", "SAGA-P101")
		}
		exprs = append(exprs, ex)
		start = k + 1
	}
	return &InterpolatedString{Texts: texts, Exprs: exprs, Tok: t}, nil
}
