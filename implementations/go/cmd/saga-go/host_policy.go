package main

import (
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"
)

// canonicalHostPath resolves existing symlinks and keeps non-existent leaf
// components anchored to the nearest existing directory.
func canonicalHostPath(path string) (string, error) {
	if strings.TrimSpace(path) == "" {
		return "", fmt.Errorf("path must not be empty")
	}
	absolute, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	prefix := absolute
	suffix := []string{}
	for {
		resolved, err := filepath.EvalSymlinks(prefix)
		if err == nil {
			for _, component := range suffix {
				resolved = filepath.Join(resolved, component)
			}
			return filepath.Clean(resolved), nil
		}
		if !os.IsNotExist(err) {
			return "", err
		}
		parent := filepath.Dir(prefix)
		if parent == prefix {
			return "", fmt.Errorf("cannot resolve path: %s", path)
		}
		suffix = append([]string{filepath.Base(prefix)}, suffix...)
		prefix = parent
	}
}

func (i *Interpreter) requireHostPath(path string, roots []string, option string) error {
	if i.TrustedHost {
		return nil
	}
	target, err := canonicalHostPath(path)
	if err != nil {
		return err
	}
	for _, allowed := range roots {
		root, err := canonicalHostPath(allowed)
		if err != nil {
			return err
		}
		relative, err := filepath.Rel(root, target)
		if err == nil && relative != ".." &&
			!strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
			return nil
		}
	}
	return fmt.Errorf("filesystem access denied: %s; use %s PATH", target, option)
}

func (i *Interpreter) requireHostProcess() error {
	if i.TrustedHost || i.AllowProcess {
		return nil
	}
	return fmt.Errorf("process execution denied; use --allow-process")
}

func hostTextArg(args []Value, index int) (string, bool) {
	if index >= len(args) {
		return "", false
	}
	value, ok := args[index].(string)
	return value, ok
}

func (i *Interpreter) requireHostEndpoint(args []Value, hostIndex, portIndex int) error {
	host, ok := hostTextArg(args, hostIndex)
	if !ok || portIndex >= len(args) {
		return nil // The interpreter will report an argument error.
	}
	port, err := numberToInt(args[portIndex])
	if err != nil {
		return nil
	}
	return i.requireNet(host, port)
}

func (i *Interpreter) requireHostURL(args []Value) error {
	raw, ok := hostTextArg(args, 0)
	if !ok {
		return nil
	}
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Hostname() == "" {
		return nil // Leave URL validation to the HTTP client.
	}
	port := 80
	if parsed.Scheme == "https" {
		port = 443
	}
	if parsed.Port() != "" {
		// Invalid ports are diagnosed by the underlying HTTP client.
		fmt.Sscanf(parsed.Port(), "%d", &port)
	}
	return i.requireNet(parsed.Hostname(), port)
}

// checkHostAccess is the shared boundary for hosted I/O in the Go runtime.
// Module imports alone do not grant authority to use the host.
func (i *Interpreter) checkHostAccess(module, name string, args []Value) error {
	switch module {
	case "io":
		path, ok := hostTextArg(args, 0)
		if !ok {
			return nil
		}
		switch name {
		case "read_text", "exists", "list":
			return i.requireHostPath(path, i.ReadRoots, "--allow-read")
		case "write_text", "remove":
			return i.requireHostPath(path, i.WriteRoots, "--allow-write")
		}
	case "db":
		if name == "open" {
			if path, ok := hostTextArg(args, 0); ok {
				return i.requireHostPath(path, i.DBRoots, "--allow-db")
			}
		}
	case "process":
		if name == "run" {
			return i.requireHostProcess()
		}
	case "net":
		switch name {
		case "connect", "listen":
			return i.requireHostEndpoint(args, 0, 1)
		case "udp_bind":
			return i.requireHostEndpoint(args, 1, 2)
		case "udp_send":
			return i.requireHostEndpoint(args, 2, 3)
		case "udp":
			// udp() has no address argument and binds an unspecified local
			// interface. A host-scoped grant must not authorize that bind.
			if !i.TrustedHost {
				for _, grant := range i.NetHosts {
					if strings.TrimSpace(grant) == "*" {
						return nil
					}
				}
				return fmt.Errorf("unrestricted UDP bind denied; use --allow-net *")
			}
		}
	case "http":
		switch name {
		case "get", "post", "status":
			return i.requireHostURL(args)
		case "listen":
			return i.requireHostEndpoint(args, 0, 1)
		}
	case "app":
		if name != "invoke" || len(args) != 2 {
			break
		}
		operation, ok := args[0].(string)
		if !ok {
			break
		}
		payload, ok := args[1].(string)
		if !ok {
			break
		}
		values, err := appJSON(payload)
		if err != nil {
			break // The app adapter diagnoses malformed input.
		}
		switch operation {
		case "filesystem.read_text", "filesystem.exists", "filesystem.list", "filesystem.stat":
			return i.requireHostPath(appString(values, "path"), i.ReadRoots, "--allow-read")
		case "filesystem.write_text", "filesystem.remove", "filesystem.mkdir":
			return i.requireHostPath(appString(values, "path"), i.WriteRoots, "--allow-write")
		case "filesystem.rename":
			if err := i.requireHostPath(appString(values, "from"), i.WriteRoots, "--allow-write"); err != nil {
				return err
			}
			return i.requireHostPath(appString(values, "to"), i.WriteRoots, "--allow-write")
		case "process.run":
			return i.requireHostProcess()
		case "network.http_get":
			return i.requireHostURL([]Value{appString(values, "url")})
		}
	case "security":
		switch name {
		case "file_sha256":
			if path, ok := hostTextArg(args, 0); ok {
				return i.requireHostPath(path, i.ReadRoots, "--allow-read")
			}
		case "tls_probe":
			return i.requireHostEndpoint(args, 0, 1)
		}
	}
	return nil
}
