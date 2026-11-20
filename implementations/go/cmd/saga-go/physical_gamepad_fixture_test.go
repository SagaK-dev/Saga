//go:build !sagaruntime

package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Verify the exact file consumed by the operator-controlled physical lab.
// This deliberately checks only syntax and types, not hardware presence.
func TestPhysicalGamepadFixtureTypeChecks(t *testing.T) {
	path := filepath.Join("..", "..", "..", "..", "validation", "gamepad", "physical_gamepad.saga")
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("physical gamepad fixture is missing: %v", err)
	}
	for _, marker := range []string{"PHYSICAL_GAMEPAD_PASS", "PHYSICAL_GAMEPAD_REQUIRED", "game.gamepad_button", "game.gamepad_close"} {
		if !strings.Contains(string(data), marker) {
			t.Fatalf("physical gamepad fixture omits %q", marker)
		}
	}
	program, err := loadProgram(path)
	if err != nil {
		t.Fatalf("cannot parse physical gamepad fixture: %v", err)
	}
	checker := NewChecker()
	if err := checker.Check(program); err != nil {
		t.Fatalf("physical gamepad fixture failed Saga type check: %v", err)
	}
}
