package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"net/http/httptest"
	"strconv"
)

func numI(v int64) Number  { return numberFromInt64(v) }
func numD(s string) Number { n, _ := newNumber(s, "decimal"); return n }

func TestWebHelpersEscapeRouteAndDocument(t *testing.T) {
	it := NewInterpreter(NewChecker(), func(string) {})
	it.TrustedHost = true
	tok := Token{File: "<web>", Line: 1, Col: 1}
	call := func(name string, args ...Value) Value {
		v, e := it.callNativeModule("web", name, args, tok)
		if e != nil {
			t.Fatalf("web.%s: %v", name, e)
		}
		return v
	}
	if got := call("escape", "<b>&\"").(string); got != "&lt;b&gt;&amp;&quot;" {
		t.Fatalf("escape=%q", got)
	}
	attrs := MapValue{Entries: []MapEntry{{Key: "class", Value: "a&b"}}}
	if got := call("element", "div", attrs, "ok").(string); got != "<div class=\"a&amp;b\">ok</div>" {
		t.Fatalf("element=%q", got)
	}
	r := call("route", "/users/:id/files/*rest", "/users/42/files/a/b").(OptionValue)
	if !r.Present {
		t.Fatal("route missing")
	}
	m := r.Value.(MapValue)
	id, _ := mapLookup(m, "id")
	rest, _ := mapLookup(m, "rest")
	if id != "42" || rest != "a/b" {
		t.Fatalf("route values %#v", m)
	}
	q := call("query", "a=1&b=hello+world").(MapValue)
	b, _ := mapLookup(q, "b")
	if b != "hello world" {
		t.Fatalf("query %#v", q)
	}
	doc := call("document", "<Saga>", "<main>ok</main>").(string)
	if !strings.Contains(doc, "<title>&lt;Saga&gt;</title>") {
		t.Fatalf("document=%q", doc)
	}
}

func TestSagaHTTPClientPolicyIsExplicitAndOptional(t *testing.T) {
	if sagaHTTPClient.Timeout != 0 {
		t.Fatal("standard HTTP client must not impose a language-level timeout")
	}
	tr, ok := sagaHTTPClient.Transport.(*http.Transport)
	if !ok || tr.Proxy != nil {
		t.Fatal("standard HTTP client must not inherit environment proxies")
	}
	req, _ := http.NewRequest("GET", "http://example.invalid/next", nil)
	if err := sagaHTTPClient.CheckRedirect(req, nil); err != http.ErrUseLastResponse {
		t.Fatalf("redirect policy=%v", err)
	}
	t.Setenv("SAGA_HTTP_TIMEOUT_MS", "50")
	client, err := standardHTTPClientForPolicy()
	if err != nil || client.Timeout != 50*time.Millisecond {
		t.Fatalf("host timeout policy not applied: %v %#v", err, client)
	}
}

func TestStandardHTTPBodyLimitIsOptionalHostPolicy(t *testing.T) {
	body := strings.Repeat("x", 17<<20)
	t.Setenv("SAGA_HTTP_MAX_BODY_BYTES", "")
	got, err := readStandardHTTPText(strings.NewReader(body))
	if err != nil || len(got) != len(body) {
		t.Fatalf("legacy fixed body ceiling returned: len=%d err=%v", len(got), err)
	}
	t.Setenv("SAGA_HTTP_MAX_BODY_BYTES", fmt.Sprint(8<<20))
	if _, err := readStandardHTTPText(strings.NewReader(body)); err == nil {
		t.Fatal("administrator body limit was not enforced")
	}
}

func TestHTTPServerRejectsInvalidUTF8AndHasReadBounds(t *testing.T) {
	srv, err := newSagaHTTPServer("127.0.0.1", 0)
	if err != nil {
		t.Fatal(err)
	}
	defer srv.Server.Close()
	if srv.Server.ReadHeaderTimeout <= 0 || srv.Server.ReadTimeout <= 0 || srv.Server.IdleTimeout <= 0 {
		t.Fatalf("server timeouts are not bounded: %#v", srv.Server)
	}
	port := sagaServerPort(srv)
	client := &http.Client{Timeout: 2 * time.Second}
	req, _ := http.NewRequest("POST", "http://127.0.0.1:"+strconv.Itoa(port)+"/bad", bytes.NewReader([]byte{0xff, 0xfe}))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("invalid UTF-8 status=%d", resp.StatusCode)
	}
}

func TestHTTPRespondRejectsHeaderInjection(t *testing.T) {
	it := NewInterpreter(NewChecker(), func(string) {})
	it.TrustedHost = true
	tok := Token{File: "<http>", Line: 1, Col: 1}
	done := make(chan struct{})
	req := &HTTPRequestValue{response: make(chan sagaHTTPResponse, 1), ctxDone: done}
	_, err := it.callNativeModule("http", "respond", []Value{req, numI(200), "text/plain\r\nX-Evil: yes", "body"}, tok)
	if err == nil || !strings.Contains(err.Error(), "CR/LF") {
		t.Fatalf("content-type header injection was not rejected: %v", err)
	}
}

func TestHTTPResponseAckWinsConcurrentContextClose(t *testing.T) {
	for i := 0; i < 1000; i++ {
		ack := make(chan error, 1)
		ctxDone := make(chan struct{})
		ack <- nil
		close(ctxDone)
		if err, ok := sagaAwaitHTTPResponseAck(ack, ctxDone); !ok || err != nil {
			t.Fatalf("iteration %d: ack was not preferred: ok=%v err=%v", i, ok, err)
		}
	}
	ack := make(chan error, 1)
	ctxDone := make(chan struct{})
	close(ctxDone)
	if err, ok := sagaAwaitHTTPResponseAck(ack, ctxDone); ok || err != nil {
		t.Fatalf("closed request without ack should fail: ok=%v err=%v", ok, err)
	}
}

func TestHTTPServerRoundTrip(t *testing.T) {
	it := NewInterpreter(NewChecker(), func(string) {})
	it.TrustedHost = true
	tok := Token{File: "<http>", Line: 1, Col: 1}
	call := func(name string, args ...Value) Value {
		v, e := it.callNativeModule("http", name, args, tok)
		if e != nil {
			t.Fatalf("http.%s: %v", name, e)
		}
		return v
	}
	lr := call("listen", "127.0.0.1", numI(0)).(ResultValue)
	if !lr.OK {
		t.Fatal(lr.Value)
	}
	srv := lr.Value.(*HTTPServerValue)
	defer call("server_close", srv)
	port := call("server_port", srv).(Number)
	pi, _ := port.Int()
	type clientResult struct {
		status int
		body   string
		err    error
	}
	ch := make(chan clientResult, 1)
	go func() {
		req, _ := http.NewRequest("POST", "http://127.0.0.1:"+pi.String()+"/hello?q=42", strings.NewReader("payload"))
		req.Header.Set("X-Test", "yes")
		resp, e := http.DefaultClient.Do(req)
		if e != nil {
			ch <- clientResult{err: e}
			return
		}
		defer resp.Body.Close()
		b, _ := io.ReadAll(resp.Body)
		ch <- clientResult{status: resp.StatusCode, body: string(b)}
	}()
	ar := call("accept", srv).(ResultValue)
	if !ar.OK {
		t.Fatal(ar.Value)
	}
	req := ar.Value.(*HTTPRequestValue)
	if call("request_method", req) != "POST" || call("request_path", req) != "/hello" || call("request_body", req) != "payload" {
		t.Fatal("request accessors")
	}
	h := call("request_header", req, "X-Test").(OptionValue)
	q := call("request_query", req, "q").(OptionValue)
	if !h.Present || h.Value != "yes" || !q.Present || q.Value != "42" {
		t.Fatalf("header/query %#v %#v", h, q)
	}
	rr := call("respond", req, numI(201), "text/plain", "created").(ResultValue)
	if !rr.OK {
		t.Fatal(rr.Value)
	}
	cr := <-ch
	if cr.err != nil || cr.status != 201 || cr.body != "created" {
		t.Fatalf("client %#v", cr)
	}
	rr2 := call("respond", req, numI(200), "text/plain", "again").(ResultValue)
	if rr2.OK {
		t.Fatal("double response accepted")
	}
}

func TestDBTransactionsCommitRollbackAndConflict(t *testing.T) {
	it := NewInterpreter(NewChecker(), func(string) {})
	it.TrustedHost = true
	tok := Token{File: "<db>", Line: 1, Col: 1}
	call := func(name string, args ...Value) Value {
		v, e := it.callNativeModule("db", name, args, tok)
		if e != nil {
			t.Fatalf("db.%s: %v", name, e)
		}
		return v
	}
	path := filepath.Join(t.TempDir(), "db.json")
	or := call("open", path).(ResultValue)
	if !or.OK {
		t.Fatal(or.Value)
	}
	db := or.Value.(*KVDBValue)
	t1 := call("begin", db).(ResultValue).Value.(*KVTxValue)
	t2 := call("begin", db).(ResultValue).Value.(*KVTxValue)
	call("tx_put", t1, "a", numI(1))
	if r := call("commit", t1).(ResultValue); !r.OK {
		t.Fatal(r.Value)
	}
	call("tx_put", t2, "b", numI(2))
	if r := call("commit", t2).(ResultValue); r.OK || r.Value != "transaction conflict" {
		t.Fatalf("expected conflict %#v", r)
	}
	t3 := call("begin", db).(ResultValue).Value.(*KVTxValue)
	call("tx_put", t3, "c", numI(3))
	call("rollback", t3)
	if v := call("get", db, "c").(OptionValue); v.Present {
		t.Fatal("rollback leaked value")
	}
	if v := call("get", db, "a").(OptionValue); !v.Present || formatValue(v.Value, false) != "1" {
		t.Fatalf("commit missing %#v", v)
	}
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var obj map[string]any
	if json.Unmarshal(b, &obj) != nil || len(obj) != 1 {
		t.Fatalf("database file=%s", b)
	}
}

func TestCPU3DCubeRasterizationAndDepth(t *testing.T) {
	it := NewInterpreter(NewChecker(), func(string) {})
	it.TrustedHost = true
	tok := Token{File: "<3d>", Line: 1, Col: 1}
	call := func(name string, args ...Value) Value {
		v, e := it.callNativeModule("game", name, args, tok)
		if e != nil {
			t.Fatalf("game.%s: %v", name, e)
		}
		return v
	}
	fb := call("framebuffer", numI(96), numI(96)).(*PixelBuffer)
	call("fb_clear", fb, numI(0), numI(0), numI(0), numI(255))
	mesh := call("mesh3d_cube", numD("2")).(*Mesh3D)
	call("mesh3d_translate", mesh, numD("0"), numD("0"), numD("5"))
	call("mesh3d_rotate", mesh, numD("0.25"), numD("0.4"), numD("0"))
	cam := call("camera3d", numD("0"), numD("0"), numD("0"), numD("0"), numD("0"), numD("5"), numD("60"), numD("0.1"), numD("100")).(*Camera3D)
	n := call("draw_mesh3d", fb, mesh, cam, numI(240), numI(80), numI(40), numI(255)).(Number)
	ni, _ := n.Int()
	if ni.Sign() == 0 {
		t.Fatal("no triangles rasterized")
	}
	colored := 0
	finiteDepth := 0
	for p := 0; p < len(fb.Pix); p += 4 {
		if fb.Pix[p] != 0 || fb.Pix[p+1] != 0 || fb.Pix[p+2] != 0 {
			colored++
		}
	}
	for _, d := range fb.Depth {
		if !mathIsInf(d) {
			finiteDepth++
		}
	}
	if colored < 100 || finiteDepth < 100 {
		t.Fatalf("weak rasterization colored=%d depth=%d", colored, finiteDepth)
	}
}
func mathIsInf(v float64) bool { return v > 1e300 }

func TestWebBundleRunsSagaSH3InJavaScript(t *testing.T) {
	node, err := exec.LookPath("node")
	if err != nil {
		t.Skip("node not installed")
	}
	dir := t.TempDir()
	entry := filepath.Join(dir, "main.saga")
	if err = os.WriteFile(entry, []byte("print(40+2)\n"), 0644); err != nil {
		t.Fatal(err)
	}
	out := filepath.Join(dir, "web")
	if _, err = writeWebBundle(entry, out, true); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"index.html", "app.js", "saga-sh3-browser.js", "kernel.sbc", "sources.json", "manifest.webmanifest", "service-worker.js", "saga-web.json"} {
		if _, err = os.Stat(filepath.Join(out, name)); err != nil {
			t.Fatalf("missing %s: %v", name, err)
		}
	}
	runner := filepath.Join(dir, "runner.js")
	script := `const fs=require('fs');const rt=require(process.argv[2]);const k=fs.readFileSync(process.argv[3],'utf8');const files=JSON.parse(fs.readFileSync(process.argv[4],'utf8'));let out='';const r=rt.runSagaSH3(k,['run','/app/main.saga'],files,s=>out+=s);process.stdout.write(String(r.code)+'|'+out);`
	if err = os.WriteFile(runner, []byte(script), 0644); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(node, runner, filepath.Join(out, "saga-sh3-browser.js"), filepath.Join(out, "kernel.sbc"), filepath.Join(out, "sources.json"))
	b, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("node: %v\n%s", err, b)
	}
	if string(b) != "0|42\n" {
		t.Fatalf("browser VM got %q", b)
	}
}

func TestHTTPAcceptUnblocksOnClose(t *testing.T) {
	it := NewInterpreter(NewChecker(), func(string) {})
	it.TrustedHost = true
	tok := Token{File: "<http-close>", Line: 1, Col: 1}
	call := func(name string, args ...Value) Value {
		v, e := it.callNativeModule("http", name, args, tok)
		if e != nil {
			t.Fatalf("http.%s: %v", name, e)
		}
		return v
	}
	lr := call("listen", "127.0.0.1", numI(0)).(ResultValue)
	if !lr.OK {
		t.Fatal(lr.Value)
	}
	srv := lr.Value.(*HTTPServerValue)
	ch := make(chan ResultValue, 1)
	go func() { ch <- call("accept", srv).(ResultValue) }()
	time.Sleep(10 * time.Millisecond)
	call("server_close", srv)
	select {
	case r := <-ch:
		if r.OK {
			t.Fatal("accept unexpectedly succeeded after close")
		}
	case <-time.After(time.Second):
		t.Fatal("accept did not unblock when server closed")
	}
}

func TestOBJMeshLoadAndRender(t *testing.T) {
	it := NewInterpreter(NewChecker(), func(string) {})
	it.TrustedHost = true
	tok := Token{File: "<obj>", Line: 1, Col: 1}
	call := func(name string, args ...Value) Value {
		v, e := it.callNativeModule("game", name, args, tok)
		if e != nil {
			t.Fatalf("game.%s: %v", name, e)
		}
		return v
	}
	path := filepath.Join(t.TempDir(), "quad.obj")
	obj := "v -1 -1 0\nv 1 -1 0\nv 1 1 0\nv -1 1 0\nf 1 2 3 4\n"
	if err := os.WriteFile(path, []byte(obj), 0644); err != nil {
		t.Fatal(err)
	}
	r := call("mesh3d_obj", path).(ResultValue)
	if !r.OK {
		t.Fatal(r.Value)
	}
	m := r.Value.(*Mesh3D)
	if len(m.Vertices) != 4 || len(m.Indices) != 6 {
		t.Fatalf("obj mesh v=%d i=%d", len(m.Vertices), len(m.Indices))
	}
	call("mesh3d_translate", m, numD("0"), numD("0"), numD("4"))
	fb := call("framebuffer", numI(64), numI(64)).(*PixelBuffer)
	call("fb_clear", fb, numI(0), numI(0), numI(0), numI(255))
	cam := call("camera3d", numD("0"), numD("0"), numD("0"), numD("0"), numD("0"), numD("4"), numD("60"), numD("0.1"), numD("50")).(*Camera3D)
	n := call("draw_mesh3d", fb, m, cam, numI(20), numI(200), numI(80), numI(255)).(Number)
	ni, _ := n.Int()
	if ni.Sign() == 0 {
		t.Fatal("OBJ triangles not rendered")
	}
}

func TestBrowserSH3DOMStorageAndClickHost(t *testing.T) {
	node, err := exec.LookPath("node")
	if err != nil {
		t.Skip("node not installed")
	}
	dir := t.TempDir()
	runner := filepath.Join(dir, "browser-host.js")
	script := `const fs=require('fs');const elements={root:{textContent:'',innerHTML:'',value:'',attrs:{},setAttribute(k,v){this.attrs[k]=v}},btn:{textContent:'',innerHTML:'',value:'',attrs:{},setAttribute(k,v){this.attrs[k]=v}}};global.document={getElementById:id=>elements[id]||null};const store=new Map();global.localStorage={getItem:k=>store.has(k)?store.get(k):null,setItem:(k,v)=>store.set(k,String(v)),removeItem:k=>store.delete(k)};global.__sagaDispatch=x=>global.ev=x;const rt=require(process.argv[2]);const kernel=fs.readFileSync(process.argv[3],'utf8');const src='use web\nprint(web.browser_available())\nweb.set_text("root","Hello")\nweb.set_value("root","42")\nweb.set_attr("root","role","main")\nweb.storage_set("answer","42")\nweb.on_click("btn","go")\n';let out='';const r=rt.runSagaSH3(kernel,['run','/app/main.saga'],{'/app/main.saga':src},s=>out+=s);elements.btn.onclick();process.stdout.write(JSON.stringify({code:r.code,out,text:elements.root.textContent,value:elements.root.value,role:elements.root.attrs.role,stored:store.get('answer'),event:global.ev}));`
	if err := os.WriteFile(runner, []byte(script), 0644); err != nil {
		t.Fatal(err)
	}
	js, _ := filepath.Abs("web_runtime/sh3vm-browser.js")
	kernel, _ := filepath.Abs("web_runtime/kernel.sbc")
	b, err := exec.Command(node, runner, js, kernel).CombinedOutput()
	if err != nil {
		t.Fatalf("node browser host: %v\n%s", err, b)
	}
	var got struct {
		Code                           int `json:"code"`
		Out, Text, Value, Role, Stored string
		Event                          []string `json:"event"`
	}
	if err := json.Unmarshal(b, &got); err != nil {
		t.Fatalf("json %s: %v", b, err)
	}
	if got.Code != 0 || got.Out != "true\n" || got.Text != "Hello" || got.Value != "42" || got.Role != "main" || got.Stored != "42" || len(got.Event) != 3 || got.Event[1] != "go" {
		t.Fatalf("browser host result %#v", got)
	}
}

func TestNativeAppHTTPGetDoesNotFollowRedirectAndCapsBody(t *testing.T) {
	final := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, "final-secret")
	}))
	defer final.Close()
	redirect := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Location", final.URL)
		w.WriteHeader(http.StatusFound)
	}))
	defer redirect.Close()

	got, err := nativeAppInvoke("network.http_get", `{"url":`+strconv.Quote(redirect.URL)+`}`)
	if err != nil {
		t.Fatal(err)
	}
	var response map[string]any
	if err := json.Unmarshal([]byte(got), &response); err != nil {
		t.Fatal(err)
	}
	if response["status"] != float64(http.StatusFound) || response["redirect_location"] != final.URL {
		t.Fatalf("redirect semantics changed: %#v", response)
	}
	if response["body"] == "final-secret" {
		t.Fatal("native app HTTP followed redirect")
	}

	oversize := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, strings.Repeat("x", sagaHostedMaxTextBytes+1))
	}))
	defer oversize.Close()
	if _, err := nativeAppInvoke("network.http_get", `{"url":`+strconv.Quote(oversize.URL)+`}`); err == nil || !strings.Contains(err.Error(), "exceeds") {
		t.Fatalf("oversized response was not rejected: %v", err)
	}
}
