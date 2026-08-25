from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace(path: str, old: str, new: str) -> None:
    p = ROOT / path
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected block not found in {path}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(path: str, marker: str, addition: str) -> None:
    p = ROOT / path
    text = p.read_text(encoding="utf-8")
    if marker in text:
        return
    p.write_text(text.rstrip() + "\n\n" + addition.rstrip() + "\n", encoding="utf-8")


# Python HKT inference: function types carry their result separately from Args,
# so treating fn as an ordinary type constructor would erase information.
for fn_name in ("_unify_invariant", "unify"):
    marker = f"def {fn_name}("
    p = ROOT / "saga/typesys.py"
    text = p.read_text(encoding="utf-8")
    start = text.index(marker)
    pos = text.index(
        '        if not is_typevar(constructor) or len(arguments) != len(actual.args):\n            return False\n',
        start,
    )
    old = '        if not is_typevar(constructor) or len(arguments) != len(actual.args):\n            return False\n'
    new = (
        '        if (\n'
        '            not is_typevar(constructor)\n'
        '            or actual.name == "fn"\n'
        '            or len(arguments) != len(actual.args)\n'
        '        ):\n'
        '            return False\n'
    )
    text = text[:pos] + text[pos:].replace(old, new, 1)
    p.write_text(text, encoding="utf-8")

# Enforce one inferred kind arity for each constructor variable across an entire
# function signature.
replace(
    "saga/checker.py",
    '''    def _validate_function_types(self, info: FunctionInfo, token: Token) -> None:\n        for param in info.params:\n            self._validate_type_reference(param, token)\n        if info.return_type is not None:\n            self._validate_type_reference(info.return_type, token)\n''',
    '''    def _validate_hkt_signature(self, info: FunctionInfo, token: Token) -> None:\n        arities: dict[str, int] = {}\n\n        def visit(value: Type) -> None:\n            if value.name == "typeapply" and value.args:\n                constructor, *arguments = value.args\n                if not is_typevar(constructor):\n                    self._error(\n                        token,\n                        "higher-kinded application requires a type-constructor variable",\n                        diagnostic_id="SAGA-T103",\n                    )\n                name = typevar_name(constructor)\n                arity = len(arguments)\n                previous = arities.get(name)\n                if previous is not None and previous != arity:\n                    self._error(\n                        token,\n                        f"higher-kinded型引数 '{name}' のarityが一致しません: {previous} と {arity}",\n                        diagnostic_id="SAGA-T103",\n                    )\n                arities[name] = arity\n                for argument in arguments:\n                    visit(argument)\n                return\n            for argument in value.args:\n                visit(argument)\n            if value.result is not None:\n                visit(value.result)\n\n        for param in info.params:\n            visit(param)\n        if info.return_type is not None:\n            visit(info.return_type)\n\n    def _validate_function_types(self, info: FunctionInfo, token: Token) -> None:\n        for param in info.params:\n            self._validate_type_reference(param, token)\n        if info.return_type is not None:\n            self._validate_type_reference(info.return_type, token)\n        self._validate_hkt_signature(info, token)\n''',
)

# Runtime HKT inference mirrors the static rule: do not capture fn until Saga
# has a representation that preserves function input and result kinds.
replace(
    "saga/interpreter.py",
    '''            if actual is None or len(arguments) != len(actual.args) or not is_typevar(constructor):\n                return\n''',
    '''            if (\n                actual is None\n                or actual.name == "fn"\n                or len(arguments) != len(actual.args)\n                or not is_typevar(constructor)\n            ):\n                return\n''',
)

# Go HKT inference parity.
replace(
    "implementations/go/cmd/saga-go/types.go",
    '''\t\tif !isTypeVar(ctor) || len(applied) != len(actual.Args) {\n\t\t\treturn false\n\t\t}\n''',
    '''\t\tif !isTypeVar(ctor) || actual.Name == "fn" || len(applied) != len(actual.Args) {\n\t\t\treturn false\n\t\t}\n''',
)

# Go checker needs deterministic normalization for generic method constraints.
replace(
    "implementations/go/cmd/saga-go/checker.go",
    '''import (\n\t"fmt"\n\t"strings"\n)\n''',
    '''import (\n\t"fmt"\n\t"sort"\n\t"strings"\n)\n''',
)

replace(
    "implementations/go/cmd/saga-go/checker.go",
    '''\tfor _, ci := range c.Classes {\n\t\tif ci.Decl != nil && len(ci.OwnFields) == 0 && len(ci.OwnMethods) == 0 {\n\t\t\tif e := c.declareMembers(ci); e != nil {\n\t\t\t\treturn e\n\t\t\t}\n\t\t}\n\t}\n\tif e := c.resolveInheritance(); e != nil {\n''',
    '''\tfor _, ci := range c.Classes {\n\t\tif ci.Decl != nil && len(ci.OwnFields) == 0 && len(ci.OwnMethods) == 0 {\n\t\t\tif e := c.declareMembers(ci); e != nil {\n\t\t\t\treturn e\n\t\t\t}\n\t\t}\n\t}\n\tfor _, fi := range c.Functions {\n\t\tif fi.Decl != nil {\n\t\t\tif e := c.validateHKTSignature(fi, fi.Decl.Tok); e != nil {\n\t\t\t\treturn e\n\t\t\t}\n\t\t}\n\t}\n\tfor _, ci := range c.Classes {\n\t\tfor _, fi := range ci.OwnMethods {\n\t\t\tif fi.Decl != nil {\n\t\t\t\tif e := c.validateHKTSignature(fi, fi.Decl.Tok); e != nil {\n\t\t\t\t\treturn e\n\t\t\t\t}\n\t\t\t}\n\t\t}\n\t}\n\tif e := c.resolveInheritance(); e != nil {\n''',
)

old_override = '''func (c *Checker) overrideCompatible(a, b FuncInfo, t Token) error {\n\tif len(a.TypeParams) != len(b.TypeParams) {\n\t\treturn c.err(t, "SAGA-T103", "override generic method type-parameter count differs")\n\t}\n\talpha := map[string]Type{}\n\tfor idx, childName := range b.TypeParams {\n\t\talpha[childName] = typeVar(a.TypeParams[idx])\n\t}\n\tparams := make([]Type, 0, len(b.Params))\n\tfor _, param := range b.Params {\n\t\tparams = append(params, substitute(param, alpha))\n\t}\n\tret := b.Ret\n\tif b.HasRet {\n\t\tret = substitute(b.Ret, alpha)\n\t}\n\tif len(a.Params) != len(params) {\n\t\treturn c.err(t, "SAGA-T103", "override parameter count differs")\n\t}\n\tfor i := range a.Params {\n\t\tif !sameType(a.Params[i], params[i]) {\n\t\t\treturn c.err(t, "SAGA-T103", "override parameter type differs")\n\t\t}\n\t}\n\tif a.HasRet && b.HasRet && !c.assignable(a.Ret, ret) {\n\t\treturn c.err(t, "SAGA-T103", fmt.Sprintf("override return type is incompatible: contract %s, implementation %s", a.Ret, ret))\n\t}\n\treturn nil\n}\n'''
new_override = '''func canonicalConstraintType(r TypeRef, rename map[string]string) string {\n\tname := r.Name\n\tif mapped, ok := rename[name]; ok {\n\t\tname = mapped\n\t}\n\tif len(r.Args) == 0 {\n\t\treturn name\n\t}\n\targs := make([]string, 0, len(r.Args))\n\tfor _, arg := range r.Args {\n\t\targs = append(args, canonicalConstraintType(arg, rename))\n\t}\n\treturn name + "[" + strings.Join(args, ",") + "]"\n}\n\nfunc normalizedMethodConstraints(d *FnDecl, rename map[string]string) []string {\n\tif d == nil {\n\t\treturn nil\n\t}\n\tout := make([]string, 0, len(d.Constraints))\n\tfor _, group := range d.Constraints {\n\t\tparam := group.Param\n\t\tif mapped, ok := rename[param]; ok {\n\t\t\tparam = mapped\n\t\t}\n\t\trequired := make([]string, 0, len(group.Types))\n\t\tfor _, ref := range group.Types {\n\t\t\trequired = append(required, canonicalConstraintType(ref, rename))\n\t\t}\n\t\tsort.Strings(required)\n\t\tout = append(out, param+":"+strings.Join(required, "+"))\n\t}\n\tsort.Strings(out)\n\treturn out\n}\n\nfunc sameStringSlice(a, b []string) bool {\n\tif len(a) != len(b) {\n\t\treturn false\n\t}\n\tfor idx := range a {\n\t\tif a[idx] != b[idx] {\n\t\t\treturn false\n\t\t}\n\t}\n\treturn true\n}\n\nfunc (c *Checker) validateHKTSignature(fi FuncInfo, tok Token) error {\n\tarities := map[string]int{}\n\tvar visit func(Type) error\n\tvisit = func(t Type) error {\n\t\tif t.Name == "typeapply" && len(t.Args) > 0 {\n\t\t\tctor := t.Args[0]\n\t\t\tif !isTypeVar(ctor) {\n\t\t\t\treturn c.err(tok, "SAGA-T103", "higher-kinded application requires a type-constructor variable")\n\t\t\t}\n\t\t\tname := strings.TrimPrefix(ctor.Name, "$")\n\t\t\tarity := len(t.Args) - 1\n\t\t\tif previous, ok := arities[name]; ok && previous != arity {\n\t\t\t\treturn c.err(tok, "SAGA-T103", fmt.Sprintf("higher-kinded type parameter %s is used with both arity %d and %d", name, previous, arity))\n\t\t\t}\n\t\t\tarities[name] = arity\n\t\t\tfor _, arg := range t.Args[1:] {\n\t\t\t\tif err := visit(arg); err != nil {\n\t\t\t\t\treturn err\n\t\t\t\t}\n\t\t\t}\n\t\t\treturn nil\n\t\t}\n\t\tfor _, arg := range t.Args {\n\t\t\tif err := visit(arg); err != nil {\n\t\t\t\treturn err\n\t\t\t}\n\t\t}\n\t\tif t.Result != nil {\n\t\t\treturn visit(*t.Result)\n\t\t}\n\t\treturn nil\n\t}\n\tfor _, param := range fi.Params {\n\t\tif err := visit(param); err != nil {\n\t\t\treturn err\n\t\t}\n\t}\n\tif fi.HasRet {\n\t\treturn visit(fi.Ret)\n\t}\n\treturn nil\n}\n\nfunc (c *Checker) overrideCompatible(a, b FuncInfo, t Token) error {\n\tif len(a.TypeParams) != len(b.TypeParams) {\n\t\treturn c.err(t, "SAGA-T103", "override generic method type-parameter count differs")\n\t}\n\talpha := map[string]Type{}\n\talphaNames := map[string]string{}\n\tfor idx, childName := range b.TypeParams {\n\t\tparentName := a.TypeParams[idx]\n\t\talpha[childName] = typeVar(parentName)\n\t\talphaNames[childName] = parentName\n\t}\n\tif a.Decl != nil && b.Decl != nil {\n\t\tparentConstraints := normalizedMethodConstraints(a.Decl, map[string]string{})\n\t\tchildConstraints := normalizedMethodConstraints(b.Decl, alphaNames)\n\t\tif !sameStringSlice(parentConstraints, childConstraints) {\n\t\t\treturn c.err(t, "SAGA-T103", "override generic method constraints differ from the contract")\n\t\t}\n\t}\n\tparams := make([]Type, 0, len(b.Params))\n\tfor _, param := range b.Params {\n\t\tparams = append(params, substitute(param, alpha))\n\t}\n\tret := b.Ret\n\tif b.HasRet {\n\t\tret = substitute(b.Ret, alpha)\n\t}\n\tif len(a.Params) != len(params) {\n\t\treturn c.err(t, "SAGA-T103", "override parameter count differs")\n\t}\n\tfor i := range a.Params {\n\t\tif !sameType(a.Params[i], params[i]) {\n\t\t\treturn c.err(t, "SAGA-T103", "override parameter type differs")\n\t\t}\n\t}\n\tif a.HasRet && b.HasRet && !c.assignable(a.Ret, ret) {\n\t\treturn c.err(t, "SAGA-T103", fmt.Sprintf("override return type is incompatible: contract %s, implementation %s", a.Ret, ret))\n\t}\n\treturn nil\n}\n'''
replace("implementations/go/cmd/saga-go/checker.go", old_override, new_override)

replace(
    "implementations/go/cmd/saga-go/checker.go",
    '''\tfi := FuncInfo{Params: ps, Ret: ret, HasRet: true, TypeParams: d.TypeParams, Decl: d}\n\tc.LocalFunctions[d] = fi\n''',
    '''\tfi := FuncInfo{Params: ps, Ret: ret, HasRet: true, TypeParams: d.TypeParams, Decl: d}\n\tif e := c.validateHKTSignature(fi, d.Tok); e != nil {\n\t\treturn e\n\t}\n\tc.LocalFunctions[d] = fi\n''',
)

# Regression tests for the review findings.
replace(
    "tests/test_generic_abstractions_052.py",
    'from saga.api import compile_source, run_source\nfrom saga.typesys import INT, TEXT, OPTION, TYPECTOR, parse_type, substitute, unify\n',
    'from saga.api import compile_source, run_source\nfrom saga.errors import TypeCheckError\nfrom saga.typesys import FUNCTION, INT, TEXT, OPTION, TYPECTOR, parse_type, substitute, unify\n',
)
append_once(
    "tests/test_generic_abstractions_052.py",
    "test_hkt_rejects_function_constructor_until_function_kinds_are_modeled",
    '''# Review-hardening regressions are intentionally kept in the 0.52 suite.\n\ndef _install_review_hardening_tests() -> None:\n    def test_hkt_signature_rejects_inconsistent_constructor_arity(self):\n        source = """\n        fn bad[F, A, B](value: F[A, B]) -> F[A] = value\n        """\n        with self.assertRaises(TypeCheckError):\n            compile_source(source)\n\n    def test_hkt_rejects_function_constructor_until_function_kinds_are_modeled(self):\n        pattern = parse_type("F[A]", {"F", "A"})\n        mapping = {}\n        self.assertFalse(unify(pattern, FUNCTION([INT], INT), mapping))\n        source = """\n        fn keep[F, A](value: F[A]) -> F[A] = value\n        fn inc(value: int) -> int = value + 1\n        let kept = keep(inc)\n        """\n        with self.assertRaises(TypeCheckError):\n            compile_source(source)\n\n    GenericAbstractions052Tests.test_hkt_signature_rejects_inconsistent_constructor_arity = test_hkt_signature_rejects_inconsistent_constructor_arity\n    GenericAbstractions052Tests.test_hkt_rejects_function_constructor_until_function_kinds_are_modeled = test_hkt_rejects_function_constructor_until_function_kinds_are_modeled\n\n\n_install_review_hardening_tests()''',
)

append_once(
    "implementations/go/cmd/saga-go/generic_abstractions_052_test.go",
    "TestGenericAbstractions052RejectsInconsistentHigherKindedArity",
    '''func TestGenericAbstractions052RejectsInconsistentHigherKindedArity(t *testing.T) {\n\tsrc := `fn bad[F, A, B](value: F[A, B]) -> F[A] = value`\n\tif _, err := runSagaForTest(t, src); err == nil {\n\t\tt.Fatal("expected inconsistent higher-kinded arity to fail")\n\t}\n}\n\nfunc TestGenericAbstractions052RejectsFunctionAsHigherKindedConstructor(t *testing.T) {\n\tsrc := `fn keep[F, A](value: F[A]) -> F[A] = value\nfn inc(value: int) -> int = value + 1\nlet kept = keep(inc)`\n\tif _, err := runSagaForTest(t, src); err == nil {\n\t\tt.Fatal("expected function-as-HKT inference to fail until function kinds are modeled")\n\t}\n}\n\nfunc TestGenericAbstractions052OverrideCannotNarrowGenericConstraints(t *testing.T) {\n\tsrc := `interface Transformer {\nfn transform[T](value: T) -> T\n}\nclass NumericOnly implements Transformer {\noverride fn transform[U](value: U) -> U where U: Numeric = value\n}`\n\tif _, err := runSagaForTest(t, src); err == nil {\n\t\tt.Fatal("expected narrowed generic override constraint to fail")\n\t}\n}\n''',
)

# Keep the development/release distinction explicit instead of advertising
# unfrozen main as the same maturity as the frozen 0.50 release.
replace(
    "pyproject.toml",
    '  "Development Status :: 5 - Production/Stable",\n',
    '  "Development Status :: 4 - Beta",\n',
)

replace(
    "README.md",
    '''- **Latest frozen release:** Saga 0.50.0 — Production GA Control Hardening\n- **Frozen release branch:** `release/0.50.0-production-ga`\n- **Development branch:** `main`\n''',
    '''- **Latest frozen release:** Saga 0.50.0 — Production GA Control Hardening\n- **Current development version:** Saga 0.52.0 — Generic Abstraction Foundations\n- **Frozen release branch:** `release/0.50.0-production-ga`\n- **Development branch:** `main`\n''',
)
replace(
    "README.md",
    '''`release/source-manifest-0.50.0.json` describes the frozen 0.50.0 source candidate. Maintenance work on `main` may intentionally diverge from that historical manifest; a later release must create new source-bound evidence instead of rewriting old release evidence.\n''',
    '''`release/source-manifest-0.50.0.json` describes the frozen 0.50.0 source candidate. Saga 0.52.0 on `main` is a development line, not a frozen GA release. Maintenance work on `main` may intentionally diverge from the historical manifest; a later frozen release must create new source-bound evidence instead of rewriting old release evidence.\n''',
)

p = ROOT / "CHANGELOG.md"
text = p.read_text(encoding="utf-8")
if not text.startswith("# Saga 0.52.0"):
    prefix = '''# Saga 0.52.0 (development)\n\n- Integrated intrinsic `Option[T]` and `Result[T, E]` with the Generic ADT constructor and exhaustive-match model while preserving legacy helpers.\n- Added alpha-equivalent generic interface method matching and the `F[A]` applied type-constructor foundation.\n- Hardened HKT signatures with consistent constructor arity and explicitly deferred function-kind constructors until their result kind can be represented without information loss.\n- Made Go generic overrides compare normalized `where` constraints instead of allowing an implementation to narrow an interface contract.\n\n# Saga 0.51.0 (development)\n\n- Added generic enums such as `Maybe[T]`, constructor inference, contextual nullary variants, typed match payloads, and module-interface preservation in Python and Go.\n\n'''
    p.write_text(prefix + text, encoding="utf-8")

# Document the newly enforced 0.52 boundary.
p = ROOT / "docs/GENERIC_ABSTRACTIONS_0.52.md"
text = p.read_text(encoding="utf-8")
needle = "Explicit kind annotation syntax such as `F[_]`, higher-rank kinds, type lambdas, and a dedicated trait/type-class declaration syntax are intentionally deferred."
replacement = needle + " A constructor variable must use one consistent arity throughout a signature, and function types are not inferred as higher-kinded constructors until Saga has an explicit function-kind representation that preserves both parameter and result structure."
if needle in text and replacement not in text:
    p.write_text(text.replace(needle, replacement, 1), encoding="utf-8")

print("review hardening patch applied")
