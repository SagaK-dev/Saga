from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from saga import Capabilities, compile_source, run_source
from saga.errors import TypeCheckError


class SagaFullStackTests(unittest.TestCase):
    def run_program(self, source: str, capabilities: Capabilities | None = None) -> list[str]:
        output: list[str] = []
        run_source(source, output=output.append, capabilities=capabilities)
        return output

    def test_oop_inheritance_interface_and_polymorphism(self):
        source = '''
        interface Greeter { fn greet() -> text }
        abstract class Person(private var age: int, let name: text) implements Greeter {
            fn birthday() { self.age = self.age + 1 }
            override fn greet() -> text = "Hello " + self.name
            abstract fn role() -> text
        }
        class Student(let school: text) extends Person {
            override fn role() -> text = "student"
            fn describe() = self.greet() + " / " + self.role() + " / " + self.school
        }
        let student = Student(15, "Aki", "Saga High")
        student.birthday()
        print(student.describe())
        '''
        self.assertEqual(self.run_program(source), ["Hello Aki / student / Saga High"])

    def test_private_field_rejected(self):
        source = 'class Secret(private let value: text) {}\nlet s = Secret("x")\nprint(s.value)'
        with self.assertRaises(TypeCheckError): compile_source(source)

    def test_generic_function_and_class(self):
        source = '''
        fn first[T](items: list[T]) -> T = items[0]
        class Box[T](let value: T) { fn get() -> T = self.value }
        print(first(["a", "b"]), Box(42).get())
        '''
        self.assertEqual(self.run_program(source), ["a 42"])

    def test_try_catch_finally(self):
        source = '''
        try { throw "boom" }
        catch error { print(error.kind, error.message) }
        finally { print("cleanup") }
        '''
        self.assertEqual(self.run_program(source), ["Thrown boom", "cleanup"])

    def test_collections_and_strings(self):
        source = '''
        fn square(x: int) = x * x
        let values = unique(sort(append([3, 1, 2, 2], 4)))
        let squares = transform(square, values)
        let words = split(" Saga,Language ", ",")
        let scores = map_put(map_of("A", 10), "B", 20)
        let tags = set_union(set_of("safe"), set_of("easy"))
        print(values, squares)
        print(upper(trim(words[0])), map_get(scores, "B", 0), set_contains(tags, "easy"))
        '''
        self.assertEqual(self.run_program(source), ["[1, 2, 3, 4] [1, 4, 9, 16]", "SAGA 20 true"])

    def test_file_text_binary_and_permission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            caps = Capabilities(read_roots=(root,), write_roots=(root,))
            source = f'''
            use io
            io.write_text("{root / 'note.txt'}", "hello")
            let data = io.encode(io.read_text("{root / 'note.txt'}"))
            io.write_bytes("{root / 'note.bin'}", data)
            print(io.decode(io.read_bytes("{root / 'note.bin'}")))
            '''
            self.assertEqual(self.run_program(source, caps), ["hello"])
            denied = f'''use io\ntry {{ io.read_text("{root / 'note.txt'}") }} catch error {{ print(error.message) }}'''
            self.assertIn("読み取り権限", self.run_program(denied)[0])

    def test_time_json_and_crypto(self):
        source = '''
        use time
        use json
        use io
        use crypto
        let now = time.parse("2026-08-07T10:00:00+09:00")
        let later = time.add_days(now, 2)
        let payload = map_of("date", time.iso(later), "ok", true)
        let encoded = json.encode(payload)
        print(time.format(later, "%Y-%m-%d"), map_get(json.decode(encoded), "ok", false))
        print(len(crypto.sha256(io.encode("Saga"))))
        '''
        self.assertEqual(self.run_program(source), ["2026-08-09 true", "64"])

    def test_sqlite_and_orm(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); db_path = root / "test.db"
            caps = Capabilities(db_roots=(root,))
            source = f'''
            use db
            use orm
            @table("users")
            class User(let id: int, let name: text) {{}}
            let conn = db.open("{db_path}")
            orm.create_table(conn, User)
            orm.insert(conn, User(1, "Aki"))
            let users = orm.all(conn, User)
            print(users[0].name)
            db.close(conn)
            '''
            self.assertEqual(self.run_program(source, caps), ["Aki"])

    def test_document_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); caps = Capabilities(db_roots=(root,))
            source = f'''
            use docdb
            let store = docdb.open("{root / 'docs.json'}")
            docdb.put(store, "one", map_of("value", 42))
            print(map_get(docdb.get(store, "one", map_of()), "value", 0))
            '''
            self.assertEqual(self.run_program(source, caps), ["42"])

    def test_parallel_map_and_future(self):
        source = '''
        use task
        fn square(x: int) = x * x
        let future = task.spawn(square, 12)
        print(task.await(future))
        print(task.parallel_map(square, [1, 2, 3, 4], 2))
        '''
        self.assertEqual(self.run_program(source), ["144", "[1, 4, 9, 16]"])

    def test_http_server_and_client(self):
        caps = Capabilities(net_hosts=("127.0.0.1",))
        source = '''
        use http
        fn handle(request: any) -> any {
            return http.response(200, "Saga API", "text/plain; charset=utf-8")
        }
        let server = http.serve("127.0.0.1", 0, handle)
        let response = http.get("http://127.0.0.1:" + text(http.port(server)) + "/")
        print(http.status(response), http.text(response))
        http.stop(server)
        '''
        self.assertEqual(self.run_program(source, caps), ["HTTP \"GET / HTTP/1.1\" 200 -", "200 Saga API"])

    def test_http_server_rejects_oversized_requests(self):
        caps = Capabilities(net_hosts=("127.0.0.1",))
        source = """
        use http
        fn handle(request: any) -> any {
            return http.response(200, "unexpected", "text/plain")
        }
        let server = http.serve("127.0.0.1", 0, handle)
        let response = http.post(
            "http://127.0.0.1:" + text(http.port(server)) + "/",
            "a request larger than four bytes",
            "text/plain"
        )
        print(http.status(response))
        http.stop(server)
        """
        with patch.dict("os.environ", {"SAGA_HTTP_SERVER_MAX_BODY_BYTES": "4"}):
            result = self.run_program(source, caps)
        self.assertTrue(any(line == "413" for line in result), result)

    def test_reflection_and_annotations(self):
        source = '''
        use reflect
        @entity("student")
        class Student(let name: text) { fn greet() = "Hi " + self.name }
        let student = Student("Aki")
        print(reflect.type_name(student), reflect.fields(student), reflect.methods(student))
        print(map_get(reflect.annotations(Student), "entity", []))
        '''
        self.assertEqual(self.run_program(source), ["Student [name] [greet]", "[student]"])


    def test_database_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); caps = Capabilities(db_roots=(root,))
            db_path = root / "tx.db"
            source = f"""
            use db
            let conn = db.open("{db_path}")
            db.execute(conn, "CREATE TABLE values_table(value INTEGER)", [])
            fn work(database: any) -> any {{
                db.execute(database, "INSERT INTO values_table(value) VALUES (?)", [7])
                return 7
            }}
            print(db.transaction(conn, work))
            print(map_get(db.query(conn, "SELECT value FROM values_table", [])[0], "value", 0))
            db.close(conn)
            """
            self.assertEqual(self.run_program(source, caps), ["7", "7"])

    def test_plugin_requires_capability_and_runs_when_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); plugin = root / "sample.py"
            plugin.write_text('saga_exports = {"double": lambda value: value * 2}\n', encoding="utf-8")
            source = f'''use plugin\nlet p = plugin.load("{plugin}")\nprint(plugin.call(p, "double", 21))'''
            caps = Capabilities(plugin_roots=(root,))
            self.assertEqual(self.run_program(source, caps), ["42"])


if __name__ == "__main__":
    unittest.main()
