package main

type TypeRef struct {
	Name string
	Args []TypeRef
	Tok  Token
}
type Annotation struct {
	Name string
	Args []Expr
	Tok  Token
}

type TypeConstraint struct {
	Param string
	Types []TypeRef
}
type Param struct {
	Name string
	Type TypeRef
	Tok  Token
}
type FieldDecl struct {
	Name             string
	Type             TypeRef
	Mutable, Private bool
	Tok              Token
}

type Expr interface {
	exprNode()
	token() Token
}
type Literal struct {
	Value Value
	Tok   Token
}

func (*Literal) exprNode()      {}
func (x *Literal) token() Token { return x.Tok }

type InterpolatedString struct {
	Texts []string
	Exprs []Expr
	Tok   Token
}

func (*InterpolatedString) exprNode()      {}
func (x *InterpolatedString) token() Token { return x.Tok }

type Variable struct {
	Name string
	Tok  Token
}

func (*Variable) exprNode()      {}
func (x *Variable) token() Token { return x.Tok }

type ListExpr struct {
	Items []Expr
	Tok   Token
}

func (*ListExpr) exprNode()      {}
func (x *ListExpr) token() Token { return x.Tok }

type Unary struct {
	Op    Token
	Right Expr
}

func (*Unary) exprNode()      {}
func (x *Unary) token() Token { return x.Op }

type AwaitExpr struct {
	Value Expr
	Tok   Token
}

func (*AwaitExpr) exprNode()      {}
func (x *AwaitExpr) token() Token { return x.Tok }

type MoveExpr struct {
	Value Expr
	Tok   Token
}

func (*MoveExpr) exprNode()      {}
func (x *MoveExpr) token() Token { return x.Tok }

type PropagateExpr struct {
	Value Expr
	Tok   Token
}

func (*PropagateExpr) exprNode()      {}
func (x *PropagateExpr) token() Token { return x.Tok }

// ClosureExpr is the Natural Core lexical closure form. Params is empty for
// the implicit form (`{ it * 2 }`); in that form the checker supplies `it`
// only when the surrounding callable contract expects one argument.
type ClosureExpr struct {
	Params   []Token
	Body     *Block
	Implicit bool
	Tok      Token
}

func (*ClosureExpr) exprNode()      {}
func (x *ClosureExpr) token() Token { return x.Tok }

type Binary struct {
	Left  Expr
	Op    Token
	Right Expr
}

func (*Binary) exprNode()      {}
func (x *Binary) token() Token { return x.Op }

type RangeExpr struct {
	Start Expr
	Op    Token
	End   Expr
}

func (*RangeExpr) exprNode()      {}
func (x *RangeExpr) token() Token { return x.Op }

type Call struct {
	Callee Expr
	Args   []Expr
	Tok    Token
}

func (*Call) exprNode()      {}
func (x *Call) token() Token { return x.Tok }

type Index struct {
	Target Expr
	Index  Expr
	Tok    Token
}

func (*Index) exprNode()      {}
func (x *Index) token() Token { return x.Tok }

type Member struct {
	Target Expr
	Name   string
	Tok    Token
}

func (*Member) exprNode()      {}
func (x *Member) token() Token { return x.Tok }

type Stmt interface {
	stmtNode()
	token() Token
}
type UseStmt struct {
	Module     string
	SourcePath string
	Alias      string
	Tok        Token
}

func (*UseStmt) stmtNode()      {}
func (x *UseStmt) token() Token { return x.Tok }

type EditionDecl struct {
	Edition string
	Tok     Token
}

func (*EditionDecl) stmtNode()      {}
func (x *EditionDecl) token() Token { return x.Tok }

type ModuleDecl struct {
	Name string
	Tok  Token
}

func (*ModuleDecl) stmtNode()      {}
func (x *ModuleDecl) token() Token { return x.Tok }

// SourceModuleStmt is produced by the loader for Edition 2027 source modules.
// It is not written directly by users and deliberately preserves the imported
// file's own lexical/type environment instead of flattening declarations.
type SourceModuleStmt struct {
	Name      string
	BindName  string
	Stmts     []Stmt
	Tok       Token
	Interface *ModuleInterface
}

func (*SourceModuleStmt) stmtNode()      {}
func (x *SourceModuleStmt) token() Token { return x.Tok }

type VarDecl struct {
	Name        string
	Mutable     bool
	Type        *TypeRef
	Init        Expr
	Annotations []Annotation
	Visibility  string
	Tok         Token
}

func (*VarDecl) stmtNode()      {}
func (x *VarDecl) token() Token { return x.Tok }

type Assign struct {
	Target Expr
	Value  Expr
	Tok    Token
}

func (*Assign) stmtNode()      {}
func (x *Assign) token() Token { return x.Tok }

type ExprStmt struct {
	Expr Expr
	Tok  Token
}

func (*ExprStmt) stmtNode()      {}
func (x *ExprStmt) token() Token { return x.Tok }

type Block struct {
	Stmts []Stmt
	Tok   Token
}

func (*Block) stmtNode()      {}
func (x *Block) token() Token { return x.Tok }

type IfStmt struct {
	Cond Expr
	Then *Block
	Else Stmt
	Tok  Token
}

func (*IfStmt) stmtNode()      {}
func (x *IfStmt) token() Token { return x.Tok }

type WhileStmt struct {
	Cond Expr
	Body *Block
	Tok  Token
}

func (*WhileStmt) stmtNode()      {}
func (x *WhileStmt) token() Token { return x.Tok }

type ForStmt struct {
	Name     string
	Iterable Expr
	Body     *Block
	Tok      Token
}

func (*ForStmt) stmtNode()      {}
func (x *ForStmt) token() Token { return x.Tok }

type BreakStmt struct{ Tok Token }

func (*BreakStmt) stmtNode()      {}
func (x *BreakStmt) token() Token { return x.Tok }

type ContinueStmt struct{ Tok Token }

func (*ContinueStmt) stmtNode()      {}
func (x *ContinueStmt) token() Token { return x.Tok }

type ReturnStmt struct {
	Value Expr
	Tok   Token
}

func (*ReturnStmt) stmtNode()      {}
func (x *ReturnStmt) token() Token { return x.Tok }

type ThrowStmt struct {
	Value Expr
	Tok   Token
}

func (*ThrowStmt) stmtNode()      {}
func (x *ThrowStmt) token() Token { return x.Tok }

type DeferStmt struct {
	Value Expr
	Tok   Token
}

func (*DeferStmt) stmtNode()      {}
func (x *DeferStmt) token() Token { return x.Tok }

type UsingStmt struct {
	Name string
	Init Expr
	Body *Block
	Tok  Token
}

func (*UsingStmt) stmtNode()      {}
func (x *UsingStmt) token() Token { return x.Tok }

type UnsafeStmt struct {
	Body *Block
	Tok  Token
}

func (*UnsafeStmt) stmtNode()      {}
func (x *UnsafeStmt) token() Token { return x.Tok }

type TaskGroupStmt struct {
	Body *Block
	Tok  Token
}

func (*TaskGroupStmt) stmtNode()      {}
func (x *TaskGroupStmt) token() Token { return x.Tok }

type TryStmt struct {
	Try       *Block
	CatchName string
	Catch     *Block
	Finally   *Block
	Tok       Token
}

func (*TryStmt) stmtNode()      {}
func (x *TryStmt) token() Token { return x.Tok }

type FnDecl struct {
	Name               string
	TypeParams         []string
	Params             []Param
	Return             *TypeRef
	Body               *Block
	ExprBody           Expr
	Annotations        []Annotation
	Constraints        []TypeConstraint
	Visibility         string
	Async, Comptime    bool
	ExternABI          string
	Abstract, Override bool
	Tok                Token

	// Control metadata is populated by the parser after a complete source unit
	// has been parsed. Keeping it on the AST node avoids hidden global registries
	// while letting the control validator resolve same-unit functions and
	// same-receiver methods without depending on checker implementation details.
	controlOwner     string
	controlFunctions map[string]*FnDecl
	controlMethods   map[string]*FnDecl
}

func (*FnDecl) stmtNode()      {}
func (x *FnDecl) token() Token { return x.Tok }

type ClassDecl struct {
	Name                string
	TypeParams          []string
	Fields              []FieldDecl
	Methods             []*FnDecl
	Base                *TypeRef
	Interfaces          []TypeRef
	Annotations         []Annotation
	Constraints         []TypeConstraint
	AssociatedTypes     map[string]*TypeRef
	RequiredAssocTypes  []string
	Visibility          string
	Resource            bool
	Abstract, Interface bool
	Record              bool
	Tok                 Token
}

func (*ClassDecl) stmtNode()      {}
func (x *ClassDecl) token() Token { return x.Tok }

type EnumVariant struct {
	Name    string
	Payload []TypeRef
	Tok     Token
}

type EnumDecl struct {
	Name       string
	TypeParams []string
	Variants   []EnumVariant
	Visibility string
	Tok        Token
}

func (*EnumDecl) stmtNode()      {}
func (x *EnumDecl) token() Token { return x.Tok }

type MatchCase struct {
	Pattern Expr
	Body    *Block
	Tok     Token
}
type MatchStmt struct {
	Value   Expr
	Cases   []MatchCase
	Default *Block
	Tok     Token
}

func (*MatchStmt) stmtNode()      {}
func (x *MatchStmt) token() Token { return x.Tok }

type TestDecl struct {
	Name string
	Body *Block
	Tok  Token
}

func (*TestDecl) stmtNode()      {}
func (x *TestDecl) token() Token { return x.Tok }
