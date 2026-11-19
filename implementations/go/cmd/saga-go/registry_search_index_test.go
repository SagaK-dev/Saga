//go:build !sagaruntime

package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRegistrySearchIndexOnlyIncludesVerifiedPackages(t *testing.T) {
	root := t.TempDir()
	data := makeRegistryTestPackage(t, "sample-one", "1.0.0")
	_, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	signature := signBytes(data, privateKey)
	if _, err := persistRegistryPackage(root, "sample-one", "1.0.0", data, signature); err != nil {
		t.Fatal(err)
	}
	// This package has a valid signature for a *different* name. It must
	// never appear under the directory that contains it.
	wrongDir := filepath.Join(root, "wrong-name", "1.0.0")
	if err := os.MkdirAll(wrongDir, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(wrongDir, "package.sagapkg"), data, 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(wrongDir, "package.sig"), []byte("{}"), 0644); err != nil {
		t.Fatal(err)
	}
	index, err := newRegistrySearchIndex(root)
	if err != nil {
		t.Fatal(err)
	}
	result := index.search("SAMPLE")
	if len(result) != 1 || result[0]["name"] != "sample-one" ||
		result[0]["sha256"] != signature.SHA256 {
		t.Fatalf("search should include only verified packages: %#v", result)
	}
	if got := index.search("wrong"); len(got) != 0 {
		t.Fatalf("invalid package indexed: %#v", got)
	}
}

func TestRegistrySearchIndexUpdatesAfterPublication(t *testing.T) {
	root := t.TempDir()
	index, err := newRegistrySearchIndex(root)
	if err != nil {
		t.Fatal(err)
	}
	if len(index.search("")) != 0 {
		t.Fatal("new registry search index should be empty")
	}
	data := makeRegistryTestPackage(t, "demo", "1.2.3")
	_, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	sig := signBytes(data, privateKey)
	if _, err := persistRegistryPackage(root, "demo", "1.2.3", data, sig); err != nil {
		t.Fatal(err)
	}
	index.recordPublished("demo", "1.2.3", data, sig)
	records := index.search("DEMO")
	if len(records) != 1 || records[0]["version"] != "1.2.3" {
		t.Fatalf("published package missing: %#v", records)
	}
	// Repeated immutable publication must not add a duplicate result.
	index.recordPublished("demo", "1.2.3", data, sig)
	if len(index.search("")) != 1 {
		t.Fatal("repeat publication added a duplicate")
	}
}

func TestRegistrySearchLimitsAndSortsMetadata(t *testing.T) {
	index := &registrySearchIndex{records: make(map[string]registrySearchRecord)}
	for n := 0; n < registrySearchLimit+10; n++ {
		// Names remain valid Saga identifiers.
		name := "lib-" + strings.Repeat("z", n+1)
		index.records[registryRecordKey(name, "1.0.0")] = registrySearchRecord{Name: name, Version: "1.0.0"}
	}
	results := index.search("LIB")
	if len(results) != registrySearchLimit {
		t.Fatalf("search size %d, limit %d", len(results), registrySearchLimit)
	}
	for i := 1; i < len(results); i++ {
		if results[i-1]["name"].(string) > results[i]["name"].(string) {
			t.Fatalf("registry results not sorted at %d", i)
		}
	}
}

func TestRegistryPlainHTTPRequiresLoopback(t *testing.T) {
	for _, addr := range []string{"127.0.0.1:7331", "localhost:0", "[::1]:7000"} {
		if !registryLoopbackBind(addr) {
			t.Fatalf("loopback address %s rejected", addr)
		}
	}
	for _, addr := range []string{"0.0.0.0:7331", ":7331", "[::]:7331", "192.0.2.1:7331"} {
		if registryLoopbackBind(addr) {
			t.Fatalf("non-loopback address %s permitted", addr)
		}
		if err := runRegistryServer(t.TempDir(), addr, "", ""); err == nil ||
			!strings.Contains(err.Error(), "requires TLS") {
			t.Fatalf("registry allowed public plaintext address %s: %v", addr, err)
		}
	}
}
