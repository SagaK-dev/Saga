package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

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
