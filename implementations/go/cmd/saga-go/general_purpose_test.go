package main

import (
	"archive/zip"
	"bufio"
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"testing"
)

func runSagaForTest(t *testing.T, src string) (string, error) {
	t.Helper()
	toks, err := lex(src, "<test>")
	if err != nil {
		return "", err
	}
	stmts, err := parse(toks)
	if err != nil {
		return "", err
	}
	c := NewChecker()
	if err = c.Check(stmts); err != nil {
		return "", err
	}
	out := []string{}
	it := NewInterpreter(c, func(s string) { out = append(out, s) })
	it.TrustedHost = true // This helper executes only test-owned programs.
	err = it.Interpret(stmts)
	return strings.Join(out, "\n"), err
}

func TestNewStandardCoreFeatures(t *testing.T) {
	src := `
enum Color { Red, Green, Blue }
record Point(x:int,y:int)
let a=Point(2,3)
let b=Point(2,3)
let r: result[int,text]=ok(7)
let name="Saga"
print(a==b)
print(unwrap_ok(r))
print($"Hello ${name}")
match Color.Green {
 case Color.Red { print("red") }
 case Color.Green { print("green") }
 case Color.Blue { print("blue") }
}
`
	got, err := runSagaForTest(t, src)
	if err != nil {
		t.Fatal(err)
	}
	want := "true\n7\nHello Saga\ngreen"
	if got != want {
		t.Fatalf("got %q want %q", got, want)
	}
}

func TestResultSnapshotClonesInstances(t *testing.T) {
	src := `
class Box(var value:int) {}
let box=Box(10)
let r: result[Box,text]=ok(box)
fn change(x:result[Box,text])->int {
  let b=unwrap_ok(x)
  b.value=99
  return b.value
}
use task
let f=task.spawn(change,r)
print(task.await(f))
print(box.value)
`
	got, err := runSagaForTest(t, src)
	if err != nil {
		t.Fatal(err)
	}
	if got != "99\n10" {
		t.Fatalf("task result snapshot leaked shared instance: %q", got)
	}
}

func TestNativeFilesystemJSONDBRegexAndGame(t *testing.T) {
	d := t.TempDir()
	txt := filepath.Join(d, "hello.txt")
	db := filepath.Join(d, "data.json")
	src := fmt.Sprintf(`
use io
use json
use db
use regex
use game
io.write_text(%s,"hello")
print(io.read_text(%s))
let decoded=json.decode('{"n":3}')
print(json.encode(decoded))
let opened=db.open(%s)
let store=unwrap_ok(opened)
print(is_ok(db.put(store,"k",42)))
print(unwrap_or(db.get(store,"k"),0))
print(regex.is_match("[0-9]+","abc123"))
let c=game.canvas(5,3)
game.box(c,0,0,5,3,"#")
game.set(c,2,1,"@")
print(game.render(c))
`, strconv.Quote(txt), strconv.Quote(txt), strconv.Quote(db))
	got, err := runSagaForTest(t, src)
	if err != nil {
		t.Fatal(err)
	}
	for _, needle := range []string{"hello", `{"n":3}`, "true\n42\ntrue", "#####\n# @ #\n#####"} {
		if !strings.Contains(got, needle) {
			t.Fatalf("missing %q in %q", needle, got)
		}
	}
}

func TestNativeGameDrawingPrimitives(t *testing.T) {
	src := `use game
let c=game.canvas(9,7)
game.clear(c,".")
game.fill_rect(c,1,1,3,2,"+")
game.line(c,0,6,8,6,"-")
game.circle(c,6,2,2,"o")
game.sprite(c,1,3,"AB\nCD")
print(game.point_in_rect(2,2,1,1,3,3))
print(game.point_in_rect(8,0,1,1,3,3))
print(game.render(c))`
	got, err := runSagaForTest(t, src)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(got, "true\nfalse\n") {
		t.Fatalf("point collision result mismatch: %q", got)
	}
	for _, needle := range []string{"+++", "AB", "CD", "---------", "o"} {
		if !strings.Contains(got, needle) {
			t.Fatalf("missing %q in %q", needle, got)
		}
	}
}

func TestNativeHTTPNoRedirectFollow(t *testing.T) {
	final := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, "SECRET") }))
	defer final.Close()
	redirect := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, final.URL, http.StatusFound) }))
	defer redirect.Close()
	src := fmt.Sprintf(`use http
let r=http.status(%s)
print(unwrap_ok(r))`, strconv.Quote(redirect.URL))
	got, err := runSagaForTest(t, src)
	if err != nil {
		t.Fatal(err)
	}
	if got != "302" {
		t.Fatalf("native HTTP followed redirect unexpectedly: %q", got)
	}
}

func TestNativeTCPConnectSendReceive(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	done := make(chan error, 1)
	go func() {
		c, e := ln.Accept()
		if e != nil {
			done <- e
			return
		}
		defer c.Close()
		buf := make([]byte, 16)
		n, e := c.Read(buf)
		if e != nil {
			done <- e
			return
		}
		if string(buf[:n]) != "ping" {
			done <- fmt.Errorf("got %q", string(buf[:n]))
			return
		}
		_, e = c.Write([]byte("pong"))
		done <- e
	}()
	port := ln.Addr().(*net.TCPAddr).Port
	src := fmt.Sprintf(`use net
let r=net.connect("127.0.0.1",%d)
let c=unwrap_ok(r)
print(unwrap_ok(net.send(c,"ping")))
print(unwrap_ok(net.recv(c,16)))
net.close(c)`, port)
	got, e := runSagaForTest(t, src)
	if e != nil {
		t.Fatal(e)
	}
	if e = <-done; e != nil {
		t.Fatal(e)
	}
	if got != "4\npong" {
		t.Fatalf("got %q", got)
	}
}

func TestProcessRunHasNoShellExpansion(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("portable command path differs on Windows")
	}
	p, err := exec.LookPath("printf")
	if err != nil {
		t.Skip("printf unavailable")
	}
	src := fmt.Sprintf(`use process
let r=process.run(%s,["%%s","$HOME"])
print(unwrap_ok(r))`, strconv.Quote(p))
	got, e := runSagaForTest(t, src)
	if e != nil {
		t.Fatal(e)
	}
	if got != "$HOME" {
		t.Fatalf("process.run unexpectedly invoked shell expansion: %q", got)
	}
}

func makeRegistryTestPackage(t *testing.T, name, version string) []byte {
	t.Helper()
	root := t.TempDir()
	manifest := fmt.Sprintf("[project]\nname=\"%s\"\nversion=\"%s\"\nlanguage=\"1.0\"\nentry=\"lib.saga\"\ntest_dir=\"tests\"\n", name, version)
	if err := os.WriteFile(filepath.Join(root, "saga.toml"), []byte(manifest), 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "lib.saga"), []byte("fn value()->int=7\n"), 0644); err != nil {
		t.Fatal(err)
	}
	if _, err := writeLock(root); err != nil {
		t.Fatal(err)
	}
	pkg, err := packProject(root, "")
	if err != nil {
		t.Fatal(err)
	}
	b, err := os.ReadFile(pkg)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func writeRegistryRawResponse(w http.ResponseWriter, data []byte, sig packageSignature) {
	setSignatureHeaders(w.Header(), sig)
	w.Header().Set("Content-Type", "application/vnd.saga.package")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(data)
}

func TestRegistryRequiresTrustedPublisher(t *testing.T) {
	project := t.TempDir()
	data := makeRegistryTestPackage(t, "demo", "1.0.0")
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	sig := signBytes(data, priv)
	if sig.Fingerprint != fingerprintPub(pub) {
		t.Fatal("fingerprint mismatch")
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { writeRegistryRawResponse(w, data, sig) }))
	defer srv.Close()
	if err = registryAdd(project, "demo@1.0.0", srv.URL, ""); err == nil || !strings.Contains(err.Error(), "untrusted publisher") {
		t.Fatalf("expected untrusted publisher rejection, got %v", err)
	}
	if err = trustFingerprint(project, sig.Fingerprint); err != nil {
		t.Fatal(err)
	}
	if err = registryAdd(project, "demo@1.0.0", srv.URL, ""); err != nil {
		t.Fatal(err)
	}
	if _, err = os.Stat(filepath.Join(project, ".saga", "packages", "demo", "1.0.0", "lib.saga")); err != nil {
		t.Fatal(err)
	}
}

func TestRegistryRejectsMismatchedIdentityAndAllowsHyphenatedName(t *testing.T) {
	if _, err := registryPath(t.TempDir(), "math-tools", "1.2.3"); err != nil {
		t.Fatalf("valid hyphenated project name rejected: %v", err)
	}
	project := t.TempDir()
	data := makeRegistryTestPackage(t, "other", "1.0.0")
	_, priv, _ := ed25519.GenerateKey(rand.Reader)
	sig := signBytes(data, priv)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { writeRegistryRawResponse(w, data, sig) }))
	defer srv.Close()
	err := registryAdd(project, "demo@1.0.0", srv.URL, sig.Fingerprint)
	if err == nil || !strings.Contains(err.Error(), "mismatched package identity") {
		t.Fatalf("mismatched registry identity was not rejected: %v", err)
	}
}

func TestSafeExtractRejectsExcessiveFileCountAndDuplicates(t *testing.T) {
	makeZip := func(names []string) []byte {
		var buf bytes.Buffer
		zw := zip.NewWriter(&buf)
		for _, name := range names {
			f, err := zw.Create(name)
			if err != nil {
				t.Fatal(err)
			}
			if _, err = f.Write(nil); err != nil {
				t.Fatal(err)
			}
		}
		if err := zw.Close(); err != nil {
			t.Fatal(err)
		}
		return buf.Bytes()
	}
	if err := safeExtractSagaPackage(makeZip([]string{"a", "a"}), t.TempDir()); err == nil || !strings.Contains(err.Error(), "duplicate") {
		t.Fatalf("duplicate package path not rejected: %v", err)
	}
	names := make([]string, registryMaxExtractedFiles+1)
	for i := range names {
		names[i] = fmt.Sprintf("f/%05d", i)
	}
	if err := safeExtractSagaPackage(makeZip(names), t.TempDir()); err == nil || !strings.Contains(err.Error(), "too many files") {
		t.Fatalf("excessive package file count not rejected: %v", err)
	}
}

func TestJSONRejectsDuplicateKeysAndTrailingContent(t *testing.T) {
	for _, src := range []string{
		`use json
json.decode('{"a":1,"a":2}')`,
		`use json
json.decode('{"a":1} true')`,
	} {
		_, err := runSagaForTest(t, src)
		if err == nil {
			t.Fatal("expected JSON rejection")
		}
		se, ok := err.(*SagaError)
		if !ok || se.ID != "SAGA-R161" {
			t.Fatalf("unexpected error: %T %v", err, err)
		}
	}
}

func TestDBFailedPutDoesNotMutateInMemoryState(t *testing.T) {
	db := filepath.Join(t.TempDir(), "data.json")
	src := fmt.Sprintf(`use db
class Box(let x:int) {}
let d=unwrap_ok(db.open(%s))
print(is_ok(db.put(d,"good",1)))
let bad=db.put(d,"bad",Box(2))
print(is_err(bad))
print(unwrap_or(db.get(d,"bad"),99))`, strconv.Quote(db))
	got, err := runSagaForTest(t, src)
	if err != nil {
		t.Fatal(err)
	}
	if got != "true\ntrue\n99" {
		t.Fatalf("failed db transaction mutated state: %q", got)
	}
}

func TestRegistryAddRejectsSignedPackageWithStaleLock(t *testing.T) {
	original := makeRegistryTestPackage(t, "stale-lock", "1.0.0")
	zr, err := zip.NewReader(bytes.NewReader(original), int64(len(original)))
	if err != nil {
		t.Fatal(err)
	}
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	for _, f := range zr.File {
		h := f.FileHeader
		w, er := zw.CreateHeader(&h)
		if er != nil {
			t.Fatal(er)
		}
		r, er := f.Open()
		if er != nil {
			t.Fatal(er)
		}
		b, er := io.ReadAll(r)
		r.Close()
		if er != nil {
			t.Fatal(er)
		}
		if f.Name == "lib.saga" {
			b = []byte("fn value()->int=999\n")
		}
		if _, er = w.Write(b); er != nil {
			t.Fatal(er)
		}
	}
	if err = zw.Close(); err != nil {
		t.Fatal(err)
	}
	data := buf.Bytes()
	_, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	sig := signBytes(data, priv)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { writeRegistryRawResponse(w, data, sig) }))
	defer srv.Close()
	err = registryAdd(t.TempDir(), "stale-lock@1.0.0", srv.URL, sig.Fingerprint)
	if err == nil || !(strings.Contains(err.Error(), "lock verification failed") || strings.Contains(err.Error(), "does not match saga.lock")) {
		t.Fatalf("stale signed lock was not rejected: %v", err)
	}
}

func TestNativeHostedAPIInventory(t *testing.T) {
	expected := strings.Fields(`
io.read_text io.write_text io.exists io.remove io.list
json.encode json.decode
time.unix_ms time.sleep_ms
math.pi math.sin math.cos math.tan
random.int random.decimal
crypto.sha256
net.connect net.listen net.accept net.send net.recv net.close
http.get http.post http.status
db.open db.put db.get db.delete db.keys db.close
process.run
regex.is_match regex.find_all
game.canvas game.clear game.set game.text game.render game.present game.frame game.box game.fill_rect game.line game.circle game.sprite game.point_in_rect game.overlap game.input game.clock_ms game.width game.height`)
	covered := map[string]bool{}
	tok := Token{File: "<api-smoke>", Line: 1, Col: 1}
	it := NewInterpreter(NewChecker(), func(string) {})
	it.TrustedHost = true
	call := func(module, name string, args ...Value) Value {
		t.Helper()
		v, err := it.callNativeModule(module, name, args, tok)
		if err != nil {
			t.Fatalf("%s.%s: %v", module, name, err)
		}
		covered[module+"."+name] = true
		return v
	}
	ni := func(v int64) Number { return numberFromInt64(v) }
	d := t.TempDir()
	file := filepath.Join(d, "a.txt")
	call("io", "write_text", file, "hello")
	if call("io", "read_text", file) != "hello" {
		t.Fatal("io read")
	}
	call("io", "exists", file)
	call("io", "list", d)
	call("io", "remove", file)
	enc := call("json", "encode", MapValue{Entries: []MapEntry{{Key: "a", Value: ni(1)}}}).(string)
	call("json", "decode", enc)
	call("time", "unix_ms")
	call("time", "sleep_ms", ni(0))
	call("math", "pi")
	zero, _ := newNumber("0.0", "decimal")
	call("math", "sin", zero)
	call("math", "cos", zero)
	call("math", "tan", zero)
	call("random", "int", ni(1), ni(2))
	call("random", "decimal")
	call("crypto", "sha256", "abc")

	// Native TCP client/send/recv/close.
	externalLn, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	extDone := make(chan error, 1)
	go func() {
		c, e := externalLn.Accept()
		if e != nil {
			extDone <- e
			return
		}
		defer c.Close()
		b := make([]byte, 8)
		n, e := c.Read(b)
		if e == nil && string(b[:n]) == "ping" {
			_, e = c.Write([]byte("pong"))
		}
		extDone <- e
	}()
	addr := externalLn.Addr().(*net.TCPAddr)
	cr := call("net", "connect", "127.0.0.1", ni(int64(addr.Port))).(ResultValue)
	if !cr.OK {
		t.Fatal(cr.Value)
	}
	conn := cr.Value.(*TCPConnValue)
	call("net", "send", conn, "ping")
	call("net", "recv", conn, ni(8))
	call("net", "close", conn)
	externalLn.Close()
	if e := <-extDone; e != nil {
		t.Fatal(e)
	}
	// Native listener/accept/close.
	lr := call("net", "listen", "127.0.0.1", ni(0)).(ResultValue)
	if !lr.OK {
		t.Fatal(lr.Value)
	}
	listener := lr.Value.(*TCPListenerValue)
	acceptDone := make(chan error, 1)
	go func() {
		c, e := net.Dial("tcp", listener.Listener.Addr().String())
		if e == nil {
			c.Close()
		}
		acceptDone <- e
	}()
	ar := call("net", "accept", listener).(ResultValue)
	if !ar.OK {
		t.Fatal(ar.Value)
	}
	call("net", "close", ar.Value)
	call("net", "close", listener)
	if e := <-acceptDone; e != nil {
		t.Fatal(e)
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == "POST" {
			b, _ := io.ReadAll(r.Body)
			fmt.Fprint(w, "post:"+string(b))
			return
		}
		fmt.Fprint(w, "get")
	}))
	defer srv.Close()
	call("http", "get", srv.URL)
	call("http", "post", srv.URL, "body", "text/plain")
	call("http", "status", srv.URL)

	dbpath := filepath.Join(d, "db.json")
	dr := call("db", "open", dbpath).(ResultValue)
	if !dr.OK {
		t.Fatal(dr.Value)
	}
	dbv := dr.Value.(*KVDBValue)
	call("db", "put", dbv, "k", ni(1))
	call("db", "get", dbv, "k")
	call("db", "keys", dbv)
	call("db", "delete", dbv, "k")
	call("db", "close", dbv)

	if runtime.GOOS != "windows" {
		if p, e := exec.LookPath("printf"); e == nil {
			call("process", "run", p, []Value{"%s", "ok"})
		} else {
			covered["process.run"] = true
		}
	} else {
		covered["process.run"] = true
	}
	call("regex", "is_match", "[0-9]+", "a1")
	call("regex", "find_all", "[0-9]+", "a1b22")

	canvas := call("game", "canvas", ni(6), ni(3)).(*GameCanvas)
	call("game", "clear", canvas, ".")
	call("game", "set", canvas, ni(1), ni(1), "@")
	call("game", "text", canvas, ni(2), ni(1), "hi")
	call("game", "box", canvas, ni(0), ni(0), ni(6), ni(3), "#")
	call("game", "fill_rect", canvas, ni(1), ni(1), ni(2), ni(1), "+")
	call("game", "line", canvas, ni(0), ni(0), ni(5), ni(2), "/")
	call("game", "circle", canvas, ni(3), ni(1), ni(1), "o")
	call("game", "sprite", canvas, ni(1), ni(1), "AB\nCD")
	call("game", "point_in_rect", ni(2), ni(2), ni(1), ni(1), ni(3), ni(3))
	call("game", "render", canvas)
	call("game", "present", canvas)
	call("game", "frame", ni(0))
	call("game", "overlap", ni(0), ni(0), ni(2), ni(2), ni(1), ni(1), ni(2), ni(2))
	call("game", "clock_ms")
	call("game", "width", canvas)
	call("game", "height", canvas)
	sagaGameInputMu.Lock()
	oldReader := sagaGameInputReader
	sagaGameInputReader = bufio.NewReader(strings.NewReader("q\n"))
	sagaGameInputMu.Unlock()
	call("game", "input", ">")
	sagaGameInputMu.Lock()
	sagaGameInputReader = oldReader
	sagaGameInputMu.Unlock()

	missing := []string{}
	for _, name := range expected {
		if !covered[name] {
			missing = append(missing, name)
		}
	}
	if len(missing) > 0 {
		t.Fatalf("native hosted API coverage missing: %v", missing)
	}
	if len(covered) != len(expected) {
		t.Fatalf("coverage count mismatch got=%d expected=%d", len(covered), len(expected))
	}
}

func TestRegistryURLRequiresHTTPSExceptLoopback(t *testing.T) {
	for _, raw := range []string{"http://127.0.0.1:7331", "http://localhost:7331", "https://registry.example.com"} {
		if err := validateRegistryBaseURL(raw); err != nil {
			t.Fatalf("valid registry URL %q rejected: %v", raw, err)
		}
	}
	for _, raw := range []string{"http://registry.example.com", "ftp://registry.example.com", "https://user@example.com"} {
		if err := validateRegistryBaseURL(raw); err == nil {
			t.Fatalf("unsafe registry URL %q accepted", raw)
		}
	}
}

func makeIdentityZipForTest(t *testing.T, manifestName, lockName string, duplicateManifest bool) []byte {
	t.Helper()
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	manifest := fmt.Sprintf("[project]\nname=\"%s\"\nversion=\"1.0.0\"\nlanguage=\"1.0\"\nentry=\"lib.saga\"\ntest_dir=\"tests\"\n", manifestName)
	for _, name := range []string{"saga.toml"} {
		f, err := zw.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = f.Write([]byte(manifest)); err != nil {
			t.Fatal(err)
		}
	}
	if duplicateManifest {
		f, err := zw.Create("./saga.toml")
		if err != nil {
			t.Fatal(err)
		}
		if _, err = f.Write([]byte(manifest)); err != nil {
			t.Fatal(err)
		}
	}
	lock := LockData{Schema: 1, Language: "Saga", LanguageVersion: "1.0", Project: LockProject{Name: lockName, Version: "1.0.0", Entry: "lib.saga"}}
	raw, _ := json.Marshal(lock)
	f, err := zw.Create("saga.lock")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = f.Write(raw); err != nil {
		t.Fatal(err)
	}
	f, err = zw.Create("lib.saga")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = f.Write([]byte("print(1)\n")); err != nil {
		t.Fatal(err)
	}
	if err = zw.Close(); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

func TestPackageIdentityRequiresManifestLockAgreement(t *testing.T) {
	if _, _, err := packageIdentity(makeIdentityZipForTest(t, "manifest-name", "lock-name", false)); err == nil || !strings.Contains(err.Error(), "manifest/lock") {
		t.Fatalf("manifest/lock mismatch was not rejected: %v", err)
	}
	if _, _, err := packageIdentity(makeIdentityZipForTest(t, "same-name", "same-name", true)); err == nil || (!strings.Contains(err.Error(), "duplicate") && !strings.Contains(err.Error(), "non-canonical")) {
		t.Fatalf("normalized duplicate/non-canonical archive path was not rejected: %v", err)
	}
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	manifest := "[project]\nname=\"dup-lock-json\"\nversion=\"1.0.0\"\nlanguage=\"1.0\"\nentry=\"lib.saga\"\ntest_dir=\"tests\"\n"
	for name, body := range map[string]string{
		"saga.toml": manifest,
		"saga.lock": `{"schema":1,"schema":1,"language":"Saga","language_version":"1.0","project":{"name":"dup-lock-json","version":"1.0.0","entry":"lib.saga"},"files":[]}`,
		"lib.saga":  "print(1)\n",
	} {
		w, err := zw.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = w.Write([]byte(body)); err != nil {
			t.Fatal(err)
		}
	}
	if err := zw.Close(); err != nil {
		t.Fatal(err)
	}
	if _, _, err := packageIdentity(buf.Bytes()); err == nil || !strings.Contains(err.Error(), "duplicate JSON key") {
		t.Fatalf("duplicate saga.lock JSON key was not rejected: %v", err)
	}
}
