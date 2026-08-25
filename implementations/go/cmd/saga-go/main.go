//go:build !sagaruntime

package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
)

func main() {
	// A Saga standalone executable is the native runtime itself with a verified
	// Saga source payload appended. It runs without Python, Go, clang, or any
	// external language runtime.
	if exe, err := os.Executable(); err == nil {
		if payload, e := readEmbeddedBundle(exe); e != nil {
			fmt.Fprintln(os.Stderr, "invalid Saga standalone executable:", e)
			os.Exit(70)
		} else if payload != nil {
			sagaProcessArgs = append([]string{}, os.Args[1:]...)
			if e = executeBundle(payload); e != nil {
				os.Exit(printDiagnostic(e))
			}
			return
		}
	}
	os.Exit(runCLI(os.Args[1:]))
}
func runCLI(args []string) int {
	if len(args) == 0 {
		fmt.Fprintln(os.Stderr, "usage: saga <run|check|debug|profile|build|module|capabilities|codegen|transpile-python|registry|standards|fmt|lint|test|repl|lsp|new|learn|explain|doctor|lock|verify|pack|bootstrap-self|conformance|info> [path]")
		return 64
	}
	switch args[0] {
	case "--help", "-h", "help":
		fmt.Println(`Saga — native machine-control, robotics, and drone programming language
Readable control code with explicit hardware authority; self-hosted native runtime.

Usage: saga <command> [options]

Core:
  run FILE              Check and run Saga source
  check FILE            Type-check without executing
  build INPUT           Build standalone native executable
  build INPUT --target wasm   Build direct WebAssembly module
  build INPUT --target web    Build browser SH-3 web bundle
  build INPUT --target pwa    Build installable offline PWA bundle
  build INPUT --target embedded-wasm  Build no-import embedded WebAssembly library
  build INPUT --target cortex-m0      Build bare-metal ARM Cortex-M0 ELF/BIN firmware
  build INPUT --target stm32f030k6   Build STM32F030K6 BSP firmware
  module compile FILE [OUT]   Compile namespaced module ABI to .smi.json
  module verify IFACE [SOURCE] Verify module ABI hash and optional source freshness
  repl                  Interactive Saga session
  test [PATH]           Run Saga test declarations/files

Development:
  fmt PATH              Format source
  lint PATH             Standard-profile lint
  debug FILE            Trace/breakpoint debugger with watches/JSON recording
  profile FILE          Statement-level elapsed-time and heap profiler
  lsp                   Language Server Protocol over stdio
  codegen json|sql      Generate typed Saga declarations
  capabilities FILE     Show required hosted capabilities

Packages:
  lock [PROJECT]        Write reproducible lock file
  verify [PROJECT]      Verify lock file
  pack [PROJECT]        Build canonical .sagapkg
  registry ...          Signed package-registry operations
  standards ...         Evidence-backed standardization readiness registry

Learning and conformance:
  new NAME --level beginner|standard|advanced
  learn
  explain DIAGNOSTIC_ID
  conformance [--json]
  doctor
  info

Interop exporter:
  transpile-python FILE   Optional source exporter; Python is never required to run Saga.`)
		return 0
	case "--version", "version":
		fmt.Println("Saga Native " + sagaGoVersion + " (Standard Core)")
		return 0
	case "info":
		profiles := []string{"Standard Core", "Native Hosted", "Portable Game Profile"}
		if desktopAvailable() {
			profiles = append(profiles, "Desktop Game Profile")
		}
		nativeHostDeps := []string{}
		if desktopAvailable() {
			nativeHostDeps = []string{"SDL2", "OpenGL-capable graphics driver", "optional native SDL renderer driver (Direct3D/Metal/etc.)"}
		}
		data := map[string]any{
			"implementation": "saga-native", "version": sagaGoVersion,
			// language_version is retained for compatibility with the existing
			// Standard-Core metadata contract. The fields below disambiguate the
			// implementation release from the target specification and preview surface.
			"language_version": "1.0-rc1", "language_spec_target": "1.0",
			"language_edition": "Open-World Runtime 0.37 / Native Runtime ABI 0.35 Preview", "natural_core_version": "0.29", "module_core_version": "0.30", "native_object_core_version": "0.31", "native_codegen_abi_version": "0.32", "native_value_abi_version": "0.33", "native_aggregate_abi_version": "0.35", "gc_preview_version": "0.37",
			"supported_editions": []string{"1.0", "2027-preview"}, "diagnostic_schema": "saga.diagnostic.v2",
			"profiles":                   profiles,
			"unicode_identifier_profile": "15.1.0-vendored", "lock_schema": 1, "host": runtime.GOOS + "/" + runtime.GOARCH,
			"independent": true, "runtime_dependencies": []string{}, "native_host_dependencies": nativeHostDeps, "compiler_toolchain_required": false,
			"standalone_builder": true, "self_hosted_compiler_driver": true, "self_host_source": "selfhost/sagac.saga", "all_runtime_source_self_hosted": false,
			"reference_implementation": true, "official_implementation": "saga-sh3", "official_sh3_all_runtime_source_self_hosted": true,
			"bootstrap_provenance": "reference Go implementation; official SH-3 path uses canonical Saga sources plus language-neutral C11 bootstrap VM", "distribution_profile": "Saga Native reference + official Saga SH-3 0.28.0",
			"native_modules":                   []string{"app", "crypto", "security", "db", "embedded", "ffi", "jit", "game", "http", "web", "io", "json", "machine", "math", "net", "process", "random", "regex", "sys", "task", "time"},
			"build_targets":                    []string{"native", "wasm-scalar", "embedded-wasm", "web", "pwa", "cortex-m0", "stm32f030k6"},
			"diagnostic_locales":               []string{"en", "ja", "fr", "es", "de"},
			"diagnostic_catalog_coverage":      map[string]string{"en": "full-fallback", "ja": "broad", "fr": "partial", "es": "partial", "de": "partial"},
			"game_limits":                      map[string]any{"max_texture_file_bytes": gameMaxTextureBytes, "max_texture_dimension": gameMaxTextureDimension, "max_texture_pixels": gameMaxTexturePixels, "max_wav_file_bytes": gameMaxAudioBytes},
			"game_profile":                     "1.0-rc1 + portable-3d-preview-0.21",
			"graphics_backend_profile":         "1.0-rc1",
			"portable_shader_ir":               map[string]any{"name": "SIR1", "stages": []string{"fragment", "compute"}, "targets": []string{"glsl120-fragment", "glsl450", "hlsl5", "msl2", "wgsl"}},
			"implementation_independence":      "technical-source-runtime; not organizational certification",
			"game_api_manifest":                "compatibility/native-game-api-0.28.0.json",
			"web_host_api_manifest":            "compatibility/web-host-api-0.28.0.json",
			"web_host_api_count":               101,
			"web_module_api_count":             107,
			"universal_app_action_protocol":    "compatibility/app-action-api-0.28.0.json",
			"universal_app_source_api_count":   10,
			"universal_app_browser_operations": 53,
			"chromium_validation":              "validation/chromium-web-0.28.0.json",
			"standards_registry_schema":        standardsSchema,
			"expert_profiles":                  map[string]any{"ffi": "C ABI Profile 2; optional sagaffi reference backend on linux/amd64", "native_scalar_jit": "optional sagajit linux/amd64", "embedded_portable": "embedded-wasm no-import", "bare_metal": "Cortex-M0/STM32F030K6 BSP with LLVM codegen backend"},
			"self_hosting_profiles":            []string{"SH-1", "SH-2", "SH-3 (official saga-sh3 implementation)"},
			"sh3_status":                       "QUALIFIED in official saga-sh3 implementation; this Go binary is retained as a non-official reference implementation",
			"target_toolchain_dependencies":    map[string]any{"native": []string{}, "cortex-m0": []string{"clang/lld", "llvm-objcopy"}, "stm32f030k6": []string{"clang/lld", "llvm-objcopy"}},
		}
		b, _ := json.Marshal(data)
		fmt.Println(string(b))
		return 0
	case "learn":
		printLearningPath()
		return 0
	case "explain":
		if len(args) < 2 {
			fmt.Fprintln(os.Stderr, "diagnostic id required")
			return 64
		}
		return explainDiagnostic(args[1])
	case "new":
		if len(args) < 2 {
			fmt.Fprintln(os.Stderr, "project name required")
			return 64
		}
		level := "beginner"
		for j := 2; j < len(args); j++ {
			if args[j] == "--level" && j+1 < len(args) {
				level = args[j+1]
				j++
				continue
			}
			fmt.Fprintln(os.Stderr, "unknown new option:", args[j])
			return 64
		}
		if level != "beginner" && level != "standard" && level != "advanced" {
			fmt.Fprintln(os.Stderr, "level must be beginner, standard, or advanced")
			return 64
		}
		if err := createProject(args[1], level); err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 74
		}
		fmt.Println("Created Saga project:", args[1], "level:", level)
		return 0
	case "fmt":
		if len(args) < 2 {
			fmt.Fprintln(os.Stderr, "path required")
			return 64
		}
		checkOnly := false
		for _, a := range args[2:] {
			if a == "--check" {
				checkOnly = true
			} else {
				fmt.Fprintln(os.Stderr, "unknown fmt option:", a)
				return 64
			}
		}
		return runFormat(args[1], checkOnly)
	case "lint":
		if len(args) < 2 {
			fmt.Fprintln(os.Stderr, "path required")
			return 64
		}
		deny := false
		for _, a := range args[2:] {
			if a == "--standard" {
				continue
			}
			if a == "--deny-warnings" {
				deny = true
				continue
			}
			fmt.Fprintln(os.Stderr, "unknown lint option:", a)
			return 64
		}
		return runLint(args[1], deny)
	case "test":
		path := "."
		if len(args) >= 2 {
			path = args[1]
		}
		return runNativeTests(path)
	case "repl":
		return runRepl()
	case "lsp":
		return runLSP()
	case "doctor":
		r := runGoSelfConformance()
		fmt.Println("Saga Native", sagaGoVersion)
		fmt.Printf("Standard Core self-conformance: %v/%v passed\n", r["passed"], r["total"])
		fmt.Println("runtime dependencies: none")
		fmt.Println("self-host source: selfhost/sagac.saga")
		if r["pass"] == true {
			return 0
		}
		return 7
	case "debug":
		return runDebugger(args[1:])
	case "profile":
		return runProfiler(args[1:])
	case "module":
		if len(args) < 3 {
			fmt.Fprintln(os.Stderr, "usage: saga module <compile|verify> PATH [OUTPUT_OR_SOURCE]")
			return 64
		}
		switch args[1] {
		case "compile":
			out := ""
			if len(args) > 3 {
				out = args[3]
			}
			mi, err := buildModuleInterface(args[2], out, true, nil)
			if err != nil {
				fmt.Fprintln(os.Stderr, err)
				return 65
			}
			b, _ := json.Marshal(map[string]interface{}{"module": mi.Module, "abi_sha256": mi.ABISHA256, "build_sha256": mi.BuildSHA256})
			fmt.Println(string(b))
			return 0
		case "verify":
			source := ""
			if len(args) > 3 {
				source = args[3]
			}
			mi, err := verifyModuleInterface(args[2], source)
			if err != nil {
				fmt.Fprintln(os.Stderr, err)
				return 65
			}
			b, _ := json.Marshal(map[string]interface{}{"valid": true, "module": mi.Module, "abi_sha256": mi.ABISHA256})
			fmt.Println(string(b))
			return 0
		default:
			fmt.Fprintln(os.Stderr, "unknown module action:", args[1])
			return 64
		}
	case "capabilities":
		return runCapabilities(args[1:])
	case "codegen":
		return runCodegen(args[1:])
	case "transpile-python":
		return runTranspilePython(args[1:])
	case "registry":
		return runRegistryCLI(args[1:])
	case "standards":
		return runStandardsCLI(args[1:])
	case "check", "run":
		if len(args) < 2 {
			fmt.Fprintln(os.Stderr, "source file required")
			return 64
		}
		sagaDiagnosticFormat = "human"
		allowDevice := false
		netHosts := []string{}
		for j := 2; j < len(args); j++ {
			if args[j] == "--diagnostic-format" && j+1 < len(args) {
				sagaDiagnosticFormat = args[j+1]
				j++
				continue
			}
			if args[j] == "--allow-device" {
				allowDevice = true
				continue
			}
			if args[j] == "--allow-net" && j+1 < len(args) {
				netHosts = append(netHosts, args[j+1])
				j++
				continue
			}
			if args[j] == "--language" && j+1 < len(args) {
				sagaLanguage = normalizeSagaLanguage(args[j+1])
				j++
				continue
			}
			fmt.Fprintln(os.Stderr, "unknown option:", args[j])
			return 64
		}
		if sagaDiagnosticFormat != "human" && sagaDiagnosticFormat != "json" {
			fmt.Fprintln(os.Stderr, "diagnostic format must be human or json")
			return 64
		}
		stmts, err := loadProgram(args[1])
		if err != nil {
			return printDiagnostic(err)
		}
		c := NewChecker()
		if err = c.Check(stmts); err != nil {
			return printDiagnostic(err)
		}
		if args[0] == "check" {
			fmt.Println("OK")
			return 0
		}
		it := NewInterpreter(c, nil)
		it.AllowDevice = allowDevice
		it.NetHosts = append([]string(nil), netHosts...)
		if err = it.Interpret(stmts); err != nil {
			return printDiagnostic(err)
		}
		return 0
	case "_bootstrap-compiler":
		if len(args) < 2 {
			fmt.Fprintln(os.Stderr, "compiler source required")
			return 64
		}
		output := ""
		for j := 2; j < len(args); j++ {
			if (args[j] == "--output" || args[j] == "-o") && j+1 < len(args) {
				output = args[j+1]
				j++
				continue
			}
			fmt.Fprintln(os.Stderr, "unknown bootstrap option:", args[j])
			return 64
		}
		out, err := buildStandaloneKind(args[1], output, "compiler")
		if err != nil {
			return printDiagnostic(err)
		}
		fmt.Println("Built Saga self-host compiler:", out)
		return 0
	case "build":
		if len(args) < 2 {
			fmt.Fprintln(os.Stderr, "source file or project directory required")
			return 64
		}
		output := ""
		target := "native"
		for j := 2; j < len(args); j++ {
			if (args[j] == "--output" || args[j] == "-o") && j+1 < len(args) {
				output = args[j+1]
				j++
				continue
			}
			if args[j] == "--target" && j+1 < len(args) {
				target = args[j+1]
				j++
				continue
			}
			fmt.Fprintln(os.Stderr, "unknown build option:", args[j])
			return 64
		}
		if target == "native" {
			if code, delegated := delegateSelfHostedCompiler(args); delegated {
				return code
			}
			out, err := buildStandalone(args[1], output)
			if err != nil {
				return printDiagnostic(err)
			}
			fmt.Println("Built standalone Saga executable:", out)
			return 0
		}
		if target == "wasm" {
			out, err := buildWASM(args[1], output)
			if err != nil {
				fmt.Fprintln(os.Stderr, err)
				return 65
			}
			fmt.Println("Built Saga WebAssembly module:", out)
			return 0
		}
		if target == "web" || target == "pwa" {
			out, err := writeWebBundle(args[1], output, target == "pwa")
			if err != nil {
				return printDiagnostic(err)
			}
			fmt.Println("Built Saga", target, "bundle:", out)
			return 0
		}
		if target == "embedded-wasm" {
			out, err := buildEmbeddedWASM(args[1], output)
			if err != nil {
				fmt.Fprintln(os.Stderr, err)
				return 65
			}
			fmt.Println("Built Saga Embedded Portable WebAssembly library:", out)
			return 0
		}
		if target == "cortex-m0" {
			out, err := buildBareMetalCortexM0(args[1], output)
			if err != nil {
				fmt.Fprintln(os.Stderr, err)
				return 65
			}
			fmt.Println("Built Saga bare-metal Cortex-M0 firmware:", out)
			return 0
		}
		if target == "stm32f030k6" {
			out, err := buildBareMetalSTM32F030K6(args[1], output)
			if err != nil {
				fmt.Fprintln(os.Stderr, err)
				return 65
			}
			fmt.Println("Built Saga STM32F030K6 firmware:", out)
			return 0
		}
		fmt.Fprintln(os.Stderr, "target must be native, wasm, web, pwa, embedded-wasm, cortex-m0, or stm32f030k6")
		return 64
	case "lock":
		path := "."
		if len(args) > 1 {
			path = args[1]
		}
		out, err := writeLock(path)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 74
		}
		fmt.Println("Locked:", out)
		return 0
	case "verify":
		path := "."
		if len(args) > 1 {
			path = args[1]
		}
		ok, errs, err := verifyLock(path)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 74
		}
		if !ok {
			for _, x := range errs {
				fmt.Fprintln(os.Stderr, x)
			}
			return 7
		}
		fmt.Println("Verified")
		return 0
	case "pack":
		path := "."
		out := ""
		if len(args) > 1 {
			path = args[1]
		}
		if len(args) > 2 {
			out = args[2]
		}
		packed, err := packProject(path, out)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 74
		}
		fmt.Println("Packed:", packed)
		return 0
	case "bootstrap-self":
		root := "."
		if len(args) > 1 {
			root = args[1]
		}
		src := filepath.Join(root, "selfhost", "sagac.saga")
		outDir := filepath.Join(root, "build", "selfhost")
		if err := os.MkdirAll(outDir, 0755); err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 74
		}
		stage1 := filepath.Join(outDir, "sagac-stage1")
		stage2 := filepath.Join(outDir, "sagac-stage2")
		stage3 := filepath.Join(outDir, "sagac-stage3")
		if runtime.GOOS == "windows" {
			stage1 += ".exe"
			stage2 += ".exe"
			stage3 += ".exe"
		}
		if _, err := buildStandaloneKind(src, stage1, "compiler"); err != nil {
			return printDiagnostic(err)
		}
		if err := runCompilerStage(stage1, src, stage2); err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 70
		}
		if err := runCompilerStage(stage2, src, stage3); err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 70
		}
		h2, err := fileSHA256(stage2)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 74
		}
		h3, err := fileSHA256(stage3)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 74
		}
		if h2 != h3 {
			fmt.Fprintf(os.Stderr, "self-host fixed point mismatch: stage2=%s stage3=%s\\n", h2, h3)
			return 7
		}
		fmt.Println("Saga self-host fixed point: PASS")
		fmt.Println("stage2/stage3 SHA-256:", h2)
		return 0
	case "conformance":
		edition2027 := false
		jsonOut := false
		for j := 1; j < len(args); j++ {
			if args[j] == "--json" {
				jsonOut = true
			}
			if args[j] == "--edition" && j+1 < len(args) && args[j+1] == "2027" {
				edition2027 = true
				j++
			}
		}
		if edition2027 {
			report := runEdition2027Conformance()
			if jsonOut {
				fmt.Println(encodeEdition2027Conformance())
			} else {
				fmt.Printf("Saga Native Edition 2027 Preview conformance: %v/%v passed\n", report["passed"], report["total"])
			}
			if report["pass"] == true {
				return 0
			}
			return 7
		}
		report := runGoSelfConformance()
		if jsonOut {
			fmt.Println(encodeGoSelfConformance())
		} else {
			fmt.Printf("Saga Native Standard Core self-conformance: %v/%v passed\n", report["passed"], report["total"])
		}
		if report["pass"] == true {
			return 0
		}
		return 7
	default:
		fmt.Fprintln(os.Stderr, "unknown command: "+args[0])
		return 64
	}
}
func delegateSelfHostedCompiler(args []string) (int, bool) {
	if sagaToolchainMode || os.Getenv("SAGA_NATIVE_BUILD") == "1" {
		return 0, false
	}
	exe, err := os.Executable()
	if err != nil {
		return 0, false
	}
	name := "sagac"
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	candidate := filepath.Join(filepath.Dir(exe), name)
	if st, err := os.Stat(candidate); err != nil || st.IsDir() {
		return 0, false
	}
	cmd := exec.Command(candidate, args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	if err := cmd.Run(); err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			return ee.ExitCode(), true
		}
		fmt.Fprintln(os.Stderr, "self-host compiler delegation failed:", err)
		return 70, true
	}
	return 0, true
}

func fileSHA256(path string) (string, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:]), nil
}
func runCompilerStage(exe, source, output string) error {
	p := exec.Command(exe, "self-build", source, "-o", output)
	p.Stdout = os.Stdout
	p.Stderr = os.Stderr
	return p.Run()
}
