package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestUnsafeNativeRequiresHostApproval(t *testing.T) {
	i := NewInterpreter(NewChecker(), func(string) {})
	if err := i.requireHostUnsafe(); err == nil {
		t.Fatal("unsafe native operations must be denied by default")
	}
	i.AllowUnsafeNative = true
	if err := i.requireHostUnsafe(); err != nil {
		t.Fatal(err)
	}
}

func TestUnsafeNativeEntrypointsEnforceHostApproval(t *testing.T) {
	i := NewInterpreter(NewChecker(), func(string) {})
	i.UnsafeDepth = 1
	token := Token{Lex: "unsafe-native"}
	for _, call := range []struct {
		name string
		run  func() error
	}{
		{"ffi", func() error { _, err := i.callFFI("profile", nil, token); return err }},
		{"jit", func() error { _, err := i.callJIT("compile_i64", nil, token); return err }},
		{"extern", func() error { _, err := i.callExtern(nil, nil, token); return err }},
	} {
		err := call.run()
		sagaErr, ok := err.(*SagaError)
		if !ok || sagaErr.ID != "SAGA-R103" {
			t.Errorf("%s should reject ungranted native execution: %v", call.name, err)
		}
	}
}

func TestHostPolicyIsDeniedByDefault(t *testing.T) {
	i := NewInterpreter(NewChecker(), func(string) {})
	root := t.TempDir()
	source := filepath.Join(root, "source.saga")
	if err := os.WriteFile(source, []byte("hello"), 0600); err != nil {
		t.Fatal(err)
	}
	for _, probe := range []struct {
		module string
		name   string
		args   []Value
	}{
		{"io", "read_text", []Value{source}},
		{"io", "write_text", []Value{source, "data"}},
		{"db", "open", []Value{filepath.Join(root, "data.json")}},
		{"net", "connect", []Value{"127.0.0.1", numberFromInt64(80)}},
		{"http", "get", []Value{"http://127.0.0.1/test"}},
		{"http", "listen", []Value{"127.0.0.1", numberFromInt64(8080)}},
		{"process", "run", []Value{"echo", []Value{"hello"}}},
	} {
		err := i.checkHostAccess(probe.module, probe.name, probe.args)
		if err == nil || !strings.Contains(err.Error(), "denied") {
			t.Errorf("%s.%s should be denied by default: %v", probe.module, probe.name, err)
		}
	}
}

func TestUDPWildcardBindRequiresWildcardGrant(t *testing.T) {
	i := NewInterpreter(NewChecker(), func(string) {})
	i.NetHosts = []string{"127.0.0.1:5353"}
	if err := i.checkHostAccess("net", "udp", nil); err == nil {
		t.Fatal("a host-scoped grant must not authorize a wildcard UDP bind")
	}
	i.NetHosts = []string{"*"}
	if err := i.checkHostAccess("net", "udp", nil); err != nil {
		t.Fatal(err)
	}
}

func TestNetworkHostPatternsRequireExactSuffixAndPort(t *testing.T) {
	i := NewInterpreter(NewChecker(), func(string) {})
	i.NetHosts = []string{"*.example.com:443", "[::1]:8080"}
	for _, allowed := range []struct {
		host string
		port int
	}{
		{"api.example.com", 443},
		{"sub.api.example.com", 443},
		{"::1", 8080},
		{"[::1]", 8080},
	} {
		if err := i.requireNet(allowed.host, allowed.port); err != nil {
			t.Errorf("%s:%d denied: %v", allowed.host, allowed.port, err)
		}
	}
	for _, blocked := range []struct {
		host string
		port int
	}{
		{"example.com", 443},
		{"evil-example.com", 443},
		{"api.example.com.evil", 443},
		{"api.example.com", 80},
		{"::1", 8081},
	} {
		if err := i.requireNet(blocked.host, blocked.port); err == nil {
			t.Errorf("%s:%d unexpectedly permitted", blocked.host, blocked.port)
		}
	}
}

func TestHostPolicyRespectsSpecificGrants(t *testing.T) {
	i := NewInterpreter(NewChecker(), func(string) {})
	root := t.TempDir()
	i.ReadRoots = []string{root}
	i.WriteRoots = []string{root}
	i.DBRoots = []string{root}
	i.NetHosts = []string{"127.0.0.1:8080"}
	i.AllowProcess = true

	file := filepath.Join(root, "file.txt")
	if err := i.checkHostAccess("io", "write_text", []Value{file, "data"}); err != nil {
		t.Fatal(err)
	}
	if err := i.checkHostAccess("db", "open", []Value{filepath.Join(root, "data.json")}); err != nil {
		t.Fatal(err)
	}
	if err := i.checkHostAccess("http", "get", []Value{"http://127.0.0.1:8080/"}); err != nil {
		t.Fatal(err)
	}
	if err := i.checkHostAccess("process", "run", []Value{"echo", []Value{}}); err != nil {
		t.Fatal(err)
	}
	if err := i.checkHostAccess("http", "get", []Value{"http://127.0.0.1:8081/"}); err == nil {
		t.Fatal("granted host must not authorize unrelated port")
	}
	if err := i.checkHostAccess("io", "read_text", []Value{filepath.Join(root, "..", "outside.txt")}); err == nil {
		t.Fatal("read grant must not authorize parent directory")
	}
}

func TestAppInvokeCannotBypassHostPolicy(t *testing.T) {
	i := NewInterpreter(NewChecker(), func(string) {})
	for _, probe := range []struct {
		operation string
		payload   string
	}{
		{"filesystem.read_text", `{"path":"/etc/hosts"}`},
		{"filesystem.write_text", `{"path":"file.txt","text":"hello"}`},
		{"filesystem.rename", `{"from":"old.txt","to":"new.txt"}`},
		{"process.run", `{"argv":["echo","hello"]}`},
		{"network.http_get", `{"url":"http://127.0.0.1/"}`},
	} {
		arguments := []Value{probe.operation, probe.payload}
		if err := i.checkHostAccess("app", "invoke", arguments); err == nil {
			t.Errorf("app.invoke(%s) bypassed its host capability", probe.operation)
		}
	}
}

func TestHostPolicyResolvesSymlinkDestinations(t *testing.T) {
	root := t.TempDir()
	other := t.TempDir()
	link := filepath.Join(root, "linked")
	if err := os.Symlink(other, link); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	i := NewInterpreter(NewChecker(), func(string) {})
	i.WriteRoots = []string{root}
	if err := i.checkHostAccess("io", "write_text", []Value{filepath.Join(link, "file.txt"), "data"}); err == nil {
		t.Fatal("symlink must not broaden the permitted root")
	}
}
