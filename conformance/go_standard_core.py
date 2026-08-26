from __future__ import annotations
import json, os, subprocess, sys, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
GO=ROOT/'implementations/go/saga-go-standard'


def checked_in_standard_core_case(name: str) -> str:
    """Load one Saga source case from the checked-in Standard Core corpus.

    The SH3 corpus is the inspectable source used by external/bootstrap
    conformance work.  Reusing it here prevents a second exhaustive builtin
    fixture from silently drifting or being omitted from source distributions.
    """
    corpus_path=ROOT/'conformance'/'sh3'/'standard-core-cases-1.0.json'
    corpus=json.loads(corpus_path.read_text(encoding='utf-8'))
    success=corpus.get('success')
    if not isinstance(success,list):
        raise RuntimeError(f'invalid Standard Core corpus: {corpus_path}')
    matches=[item for item in success if isinstance(item,dict) and item.get('name')==name]
    if len(matches)!=1:
        raise RuntimeError(f"Standard Core corpus must contain exactly one '{name}' case")
    source=matches[0].get('source')
    if not isinstance(source,str) or not source.strip():
        raise RuntimeError(f"Standard Core corpus case '{name}' has no Saga source")
    return source


CASES={
'control': '''var total: int = 0\nfor n in 1..6 { if n == 3 { continue } total = total + n }\nvar x: int = 0\nwhile x < 3 { x = x + 1 }\nprint(total, x)''',
'function_recursion': '''fn fact(n: int) -> int { if n <= 1 { return 1 } return n * fact(n - 1) }\nprint(fact(8))''',
'exact_numbers': '''print(0.1 + 0.2 == 0.3)\nprint(1 / 3 + 1 / 6)\nprint(2 ** -2)''',
'collections': '''let xs = append([1,2],3)\nlet m = map_of("a",1,"b",2)\nlet s = set_of(1,2,2,3)\nprint(len(xs), map_get(m,"b",0), len(s), contains(xs,3))''',
'option': '''let a: option[int] = some(42)\nlet b: option[int] = none()\nprint(is_some(a), unwrap(a), is_none(b), unwrap_or(b,7))''',
'generics': '''fn first[T](xs: list[T]) -> T { return xs[0] }\nprint(first([7,8]), first(["a","b"]))''',
'higher_order': '''fn square(x: int) -> int = x*x\nfn even(x: int) -> bool = x % 2 == 0\nfn add(a: int,b: int) -> int = a+b\nprint(transform(square,[1,2,3]))\nprint(filter(even,[1,2,3,4]))\nprint(reduce(add,[1,2,3,4],0))''',
'oop': '''interface Named { fn name() -> text }\nclass Person(let value: text) implements Named { override fn name() -> text = self.value }\nclass Student(let student_name: text) extends Person { override fn name() -> text = "Student " + self.student_name }\nfn label(item: Named) -> text = item.name()\nlet person: Person = Student("base","Aki")\nlet named: Named = person\nprint(label(named), text(person))''',
'private': '''class Account(private let secret: text, let name: text) {}\nprint(text(Account("token","Aki")))''',
'exception': '''try { throw "boom" } catch err { print(err.kind, err.message) } finally { print("finally") }''',
'unicode': '''let 合計 = 40 + 2\nprint(合計)''',
'text': '''let s = " Saga "\nprint(trim(s), upper("ab"), lower("AB"), substring("abcdef",1,4), find_text("abcabc","bc"))''',
'task': '''use task\nfn square(x: int) -> int = x*x\nlet f = task.spawn(square,12)\nprint(task.await(f))\nprint(task.parallel_map(square,[1,2,3,4],2))''',
'generic_class': '''class Box[T](let value: T) { fn get() -> T = self.value }\nlet box: Box[int] = Box(42)\nprint(box.get())''',
'abstract_class': '''abstract class Shape() { abstract fn area() -> int }\nclass Square(let side: int) extends Shape { override fn area() -> int = self.side*self.side }\nlet s: Shape = Square(4)\nprint(s.area())''',
'annotations': '''@entity\nclass User(let id: int) {}\n@pure\nfn value() -> int = 7\nprint(value(), text(User(1)))''',
'object_identity': '''class Box(let x: int) {}\nlet a=Box(1)\nlet b=Box(1)\nprint(a==a,a==b)''',
'collection_equality': '''print([1,2]==[1,2], map_of("a",1)==map_of("a",1), set_of(1,2)==set_of(2,1))''',
'lexical_closure': '''fn make_counter(start: int) -> fn[int] { var n = start fn next() -> int { n = n + 1 return n } return next }
let c = make_counter(10)
print(c())
print(c())''',
'generic_interface_binding': '''interface Repository[T] { fn save(value: T) -> T }
class MemoryRepository[T](let seed: T) implements Repository[T] { override fn save(value: T) -> T = value }
let repo: Repository[int] = MemoryRepository(0)
print(repo.save(42))''',
'generic_base_binding': '''class Box[T](let value: T) {}
class IntBox() extends Box[int] {}
let box = IntBox(9)
print(box.value)''',
'generic_transitive_relation': '''interface R[T] { fn value() -> T }
class Base[T](let stored:T) implements R[T] { override fn value() -> T = self.stored }
class IntBase() extends Base[int] {}
let x:R[int]=IntBase(55)
print(x.value())''',
}
# Exercise every Standard Core builtin plus representative OOP/generic/error
# semantics in one observable cross-implementation program.  The source lives
# in the checked-in SH3 corpus so outside labs and this runner use one fixture.
CASES['builtins_complete'] = checked_in_standard_core_case('builtins_complete')
ERROR_CASES={
'immutable': ('let x = 1\nx = 2','SAGA-T101'),
'unknown': ('print(missing)','SAGA-T102'),
'condition': ('if 1 { print(1) }','SAGA-T104'),
'private_access': ('class A(private let x: int) {}\nlet a=A(1)\nprint(a.x)','SAGA-T107'),
'bad_filter': ('fn bad(x: int) -> int = x\nfilter(bad,[1,2])','SAGA-T103'),
'bad_map_key': ('let x = map_of([1],2)','SAGA-T103'),
'duplicate_annotation': ('@x\n@x\nfn value() -> int = 1','SAGA-T108'),
'generic_invariance': ('class Box[T](let value: T) {}\nlet a: Box[int] = Box(1)\nlet b: Box[text] = a','SAGA-T103'),
'generic_interface_invariance': ('interface R[T] { fn save(v: T) -> T }\nclass M[T](let seed: T) implements R[T] { override fn save(v: T) -> T = v }\nlet r: R[text] = M(0)','SAGA-T103'),
'bad_override': ('class A() { fn x() -> int = 1 }\nclass B() extends A { fn x() -> int = 2 }','SAGA-T110'),
'abstract_construct': ('abstract class A() { abstract fn x() -> int }\nlet a=A()','SAGA-T111'),
}

def build_go():
    subprocess.run(['go','build','-o',str(GO),'./cmd/saga-go'],cwd=ROOT/'implementations/go',check=True)

def run(cmd):
    return subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True,env={**os.environ,'PYTHONPATH':str(ROOT)})

def diagnostic_id(stderr:str):
    for line in reversed(stderr.splitlines()):
        try:
            obj=json.loads(line)
            if isinstance(obj,dict):
                if 'diagnostic' in obj:return obj['diagnostic'].get('id')
                return obj.get('id')
        except Exception: pass
    return None

def main()->int:
    build_go(); passed=0; total=0; details=[]
    with tempfile.TemporaryDirectory() as td:
        root=Path(td)
        for name,src in CASES.items():
            total+=1;p=root/f'{name}.saga';p.write_text(src,encoding='utf-8')
            py=run([sys.executable,str(ROOT/'saga.py'),'run',str(p),'--diagnostic-format','json','--language','en'])
            go=run([str(GO),'run',str(p)])
            ok=py.returncode==0 and go.returncode==0 and py.stdout==go.stdout
            details.append((name,ok,py.stdout.strip(),go.stdout.strip(),py.stderr.strip(),go.stderr.strip()))
            passed+=int(ok)
        for name,(src,want) in ERROR_CASES.items():
            total+=1;p=root/f'{name}.saga';p.write_text(src,encoding='utf-8')
            py=run([sys.executable,str(ROOT/'saga.py'),'check',str(p),'--diagnostic-format','json','--language','en'])
            go=run([str(GO),'check',str(p),'--diagnostic-format','json'])
            pi,gi=diagnostic_id(py.stderr),diagnostic_id(go.stderr)
            ok=py.returncode!=0 and go.returncode!=0 and pi==want and gi==want
            details.append((name,ok,pi,gi,py.stderr.strip(),go.stderr.strip()));passed+=int(ok)
        # Source-unit, lock-file, verification and canonical package interoperability.
        total += 1
        proj=root/'project'; proj.mkdir()
        (proj/'saga.toml').write_text('[project]\nname = "国際-test"\nversion = "1.2.3"\nlanguage = "0.9"\nentry = "main.saga"\ntest_dir = "tests"\n',encoding='utf-8')
        (proj/'dep.saga').write_text('fn value() -> int = 42\n',encoding='utf-8')
        (proj/'main.saga').write_text('use "dep.saga"\nprint(value())\n',encoding='utf-8')
        py_run=run([sys.executable,str(ROOT/'saga.py'),'run',str(proj/'main.saga')])
        go_run=run([str(GO),'run',str(proj/'main.saga')])
        py_lock=run([sys.executable,str(ROOT/'saga.py'),'lock',str(proj)])
        py_lock_bytes=(proj/'saga.lock').read_bytes() if (proj/'saga.lock').exists() else b''
        (proj/'saga.lock').unlink(missing_ok=True)
        go_lock=run([str(GO),'lock',str(proj)])
        go_lock_bytes=(proj/'saga.lock').read_bytes() if (proj/'saga.lock').exists() else b''
        py_pkg=proj/'python.sagapkg'; go_pkg=proj/'go.sagapkg'
        py_pack=run([sys.executable,str(ROOT/'saga.py'),'pack',str(proj),'--output',str(py_pkg)])
        go_pack=run([str(GO),'pack',str(proj),str(go_pkg)])
        ok=(py_run.returncode==0 and go_run.returncode==0 and py_run.stdout==go_run.stdout=='42\n' and py_lock.returncode==0 and go_lock.returncode==0 and py_lock_bytes==go_lock_bytes and py_pack.returncode==0 and go_pack.returncode==0 and py_pkg.read_bytes()==go_pkg.read_bytes())
        details.append(('source_lock_package',ok,'','','','')); passed+=int(ok)
    for d in details: print(('PASS' if d[1] else 'FAIL'),d[0],'' if d[1] else d[2:])
    print(f'{passed}/{total} Standard Core cross-implementation cases passed')
    return 0 if passed==total else 1
if __name__=='__main__':raise SystemExit(main())
