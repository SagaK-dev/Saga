package main

import (
	"io"
	"os"
	"strings"
	"testing"
)

func captureNativeCLIStdout054(t *testing.T, args []string) (string, int) {
	t.Helper()
	old := os.Stdout
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	os.Stdout = w
	defer func() { os.Stdout = old }()

	code := runCLI(args)
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	data, err := io.ReadAll(r)
	if err != nil {
		t.Fatal(err)
	}
	if err := r.Close(); err != nil {
		t.Fatal(err)
	}
	return string(data), code
}

func TestNativeCLIIdentity054(t *testing.T) {
	help, code := captureNativeCLIStdout054(t, []string{"--help"})
	if code != 0 {
		t.Fatalf("help exit code=%d", code)
	}
	if !strings.Contains(help, "machine-control, robotics, and drone") {
		t.Fatalf("native help does not lead with the control identity: %q", help)
	}
	if !strings.Contains(help, "explicit hardware authority") {
		t.Fatalf("native help does not state the hardware-authority boundary: %q", help)
	}

	version, code := captureNativeCLIStdout054(t, []string{"--version"})
	if code != 0 {
		t.Fatalf("version exit code=%d", code)
	}
	if !strings.Contains(version, sagaGoVersion) {
		t.Fatalf("native version %q does not contain %s", version, sagaGoVersion)
	}
}
