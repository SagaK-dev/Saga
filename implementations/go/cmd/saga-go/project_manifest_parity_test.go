package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestProjectManifestRejectsDuplicateIdentity(t *testing.T) {
	root := t.TempDir()
	manifest := "[project]\nname = \"sample\"\nversion = \"1.0.0\"\nname = \"other\"\n"
	if err := os.WriteFile(filepath.Join(root, "saga.toml"), []byte(manifest), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := loadProject(root); err == nil || !strings.Contains(err.Error(), "duplicate") {
		t.Fatalf("duplicate name must be rejected: %v", err)
	}
}

func TestProjectManifestAcceptsLiteralStringsAndUnknownMetadata(t *testing.T) {
	root := t.TempDir()
	manifest := "[project]\nname = 'sample' # package name\nversion = '1.0.0'\nentry = 'file#1.saga'\ncustom = true\n"
	if err := os.WriteFile(filepath.Join(root, "saga.toml"), []byte(manifest), 0600); err != nil {
		t.Fatal(err)
	}
	project, err := loadProject(root)
	if err != nil {
		t.Fatal(err)
	}
	if project.Name != "sample" || project.Version != "1.0.0" || !strings.HasSuffix(project.Entry, "file#1.saga") {
		t.Fatalf("unexpected project: %#v", project)
	}
}

func TestRegistryManifestRejectsRepeatedProjectTable(t *testing.T) {
	raw := []byte("[project]\nname='sample'\n[other]\nvalue=1\n[project]\nversion='1.0.0'\n")
	if _, _, err := packageManifestIdentity(raw); err == nil ||
		!strings.Contains(err.Error(), "duplicate") {
		t.Fatalf("duplicated project table must be rejected: %v", err)
	}
}

func TestRegistryManifestIgnoresUnrelatedTOMLTypes(t *testing.T) {
	name, version, err := packageManifestIdentity([]byte("[project]\nname='sample'\nversion='1.0.0'\nmetadata=true\n"))
	if err != nil || name != "sample" || version != "1.0.0" {
		t.Fatalf("identity = %s %s, error = %v", name, version, err)
	}
}
