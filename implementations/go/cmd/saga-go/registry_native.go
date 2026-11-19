//go:build !sagaruntime

package main

import (
	"archive/zip"
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"crypto/x509"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	pathpkg "path"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

type packageSignature struct {
	Algorithm   string `json:"algorithm"`
	SHA256      string `json:"sha256"`
	Signature   string `json:"signature"`
	PublicKey   string `json:"public_key"`
	Fingerprint string `json:"fingerprint"`
}
type registryTrustStore struct {
	Fingerprints []string `json:"fingerprints"`
}

const (
	registryMaxPackageBytes   = int64(96 << 20)
	registryMaxMetadataBytes  = int64(8 << 20)
	registryMaxExtractedBytes = uint64(256 << 20)
	registryMaxExtractedFiles = 10_000
)

var registryHTTPClient = newExplicitHTTPClient(30 * time.Second)

func validateRegistryBaseURL(raw string) error {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return fmt.Errorf("invalid registry URL")
	}
	if u.Scheme == "https" {
		return nil
	}
	if u.Scheme == "http" {
		host := strings.TrimSuffix(strings.ToLower(u.Hostname()), ".")
		if host == "localhost" || strings.HasSuffix(host, ".localhost") {
			return nil
		}
		if ip := net.ParseIP(host); ip != nil && ip.IsLoopback() {
			return nil
		}
	}
	return fmt.Errorf("registry URL must use HTTPS; plain HTTP is allowed only for loopback development")
}

func readRegistryBody(r io.Reader, max int64) ([]byte, error) {
	b, err := io.ReadAll(io.LimitReader(r, max+1))
	if err != nil {
		return nil, err
	}
	if int64(len(b)) > max {
		return nil, fmt.Errorf("registry response exceeds %d bytes", max)
	}
	return b, nil
}

func readRegistryFile(path string, max int64) ([]byte, error) {
	st, err := os.Stat(path)
	if err != nil {
		return nil, err
	}
	if st.Size() < 0 || st.Size() > max {
		return nil, fmt.Errorf("registry stored file exceeds %d bytes", max)
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	b, err := readRegistryBody(f, max)
	if err != nil {
		return nil, err
	}
	if int64(len(b)) != st.Size() {
		return nil, fmt.Errorf("registry stored file size mismatch")
	}
	return b, nil
}

func validRegistryPackageIdentity(name, version string) bool {
	return validProjectName(name) && semverRE.MatchString(version)
}

func normalizeFingerprint(v string) string { return strings.ToLower(strings.TrimSpace(v)) }
func validFingerprint(v string) bool {
	v = normalizeFingerprint(v)
	if len(v) != 64 {
		return false
	}
	_, err := hex.DecodeString(v)
	return err == nil
}
func trustStorePath(projectRoot string) (string, error) {
	root, err := filepath.Abs(projectRoot)
	if err != nil {
		return "", err
	}
	return filepath.Join(root, "saga.trust.json"), nil
}
func loadTrustStore(projectRoot string) (registryTrustStore, error) {
	var out registryTrustStore
	p, err := trustStorePath(projectRoot)
	if err != nil {
		return out, err
	}
	b, err := os.ReadFile(p)
	if os.IsNotExist(err) {
		return out, nil
	}
	if err != nil {
		return out, err
	}
	if _, err = decodeJSONSaga(string(b)); err != nil {
		return out, fmt.Errorf("invalid trust store: %w", err)
	}
	if err = json.Unmarshal(b, &out); err != nil {
		return out, fmt.Errorf("invalid trust store: %w", err)
	}
	for i := range out.Fingerprints {
		out.Fingerprints[i] = normalizeFingerprint(out.Fingerprints[i])
	}
	sort.Strings(out.Fingerprints)
	return out, nil
}
func saveTrustStore(projectRoot string, store registryTrustStore) error {
	p, err := trustStorePath(projectRoot)
	if err != nil {
		return err
	}
	seen := map[string]bool{}
	clean := []string{}
	for _, fp := range store.Fingerprints {
		fp = normalizeFingerprint(fp)
		if validFingerprint(fp) && !seen[fp] {
			seen[fp] = true
			clean = append(clean, fp)
		}
	}
	sort.Strings(clean)
	store.Fingerprints = clean
	b, _ := json.MarshalIndent(store, "", "  ")
	return writeFileAtomic(p, append(b, '\n'), 0644)
}
func trustFingerprint(projectRoot, fingerprint string) error {
	fingerprint = normalizeFingerprint(fingerprint)
	if !validFingerprint(fingerprint) {
		return fmt.Errorf("publisher fingerprint must be 64 hexadecimal characters")
	}
	p, err := trustStorePath(projectRoot)
	if err != nil {
		return err
	}
	return withKVFileLock(p, true, func() error {
		store, er := loadTrustStore(projectRoot)
		if er != nil {
			return er
		}
		for _, fp := range store.Fingerprints {
			if fp == fingerprint {
				return nil
			}
		}
		store.Fingerprints = append(store.Fingerprints, fingerprint)
		return saveTrustStore(projectRoot, store)
	})
}
func isFingerprintTrusted(projectRoot, fingerprint string) (bool, error) {
	store, err := loadTrustStore(projectRoot)
	if err != nil {
		return false, err
	}
	fingerprint = normalizeFingerprint(fingerprint)
	for _, fp := range store.Fingerprints {
		if fp == fingerprint {
			return true, nil
		}
	}
	return false, nil
}

func shaBytes(b []byte) string { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }
func generateSigningKey(privPath, pubPath string) error {
	pub, priv, e := ed25519.GenerateKey(rand.Reader)
	if e != nil {
		return e
	}
	pkcs, e := x509.MarshalPKCS8PrivateKey(priv)
	if e != nil {
		return e
	}
	pubder, e := x509.MarshalPKIXPublicKey(pub)
	if e != nil {
		return e
	}
	privFile, e := os.OpenFile(privPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if e != nil {
		return fmt.Errorf("refusing to overwrite private key path: %w", e)
	}
	privBytes := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: pkcs})
	if _, e = privFile.Write(privBytes); e == nil {
		e = privFile.Sync()
	}
	closeErr := privFile.Close()
	if e == nil {
		e = closeErr
	}
	if e != nil {
		_ = os.Remove(privPath)
		return e
	}
	pubFile, e := os.OpenFile(pubPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0644)
	if e != nil {
		_ = os.Remove(privPath)
		return fmt.Errorf("refusing to overwrite public key path: %w", e)
	}
	pubBytes := pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: pubder})
	if _, e = pubFile.Write(pubBytes); e == nil {
		e = pubFile.Sync()
	}
	closeErr = pubFile.Close()
	if e == nil {
		e = closeErr
	}
	if e != nil {
		_ = os.Remove(privPath)
		_ = os.Remove(pubPath)
		return e
	}
	return nil
}
func readPrivateKey(path string) (ed25519.PrivateKey, error) {
	b, e := os.ReadFile(path)
	if e != nil {
		return nil, e
	}
	blk, _ := pem.Decode(b)
	if blk == nil {
		return nil, fmt.Errorf("invalid PEM private key")
	}
	q, e := x509.ParsePKCS8PrivateKey(blk.Bytes)
	if e != nil {
		return nil, e
	}
	k, ok := q.(ed25519.PrivateKey)
	if !ok {
		return nil, fmt.Errorf("not an Ed25519 private key")
	}
	return k, nil
}
func fingerprintPub(pub ed25519.PublicKey) string {
	h := sha256.Sum256(pub)
	return hex.EncodeToString(h[:])
}
func signBytes(data []byte, key ed25519.PrivateKey) packageSignature {
	pub := key.Public().(ed25519.PublicKey)
	return packageSignature{"Ed25519", shaBytes(data), base64.StdEncoding.EncodeToString(ed25519.Sign(key, data)), base64.StdEncoding.EncodeToString(pub), fingerprintPub(pub)}
}
func verifyPackageSignature(data []byte, s packageSignature) error {
	if s.Algorithm != "Ed25519" {
		return fmt.Errorf("unsupported signature algorithm")
	}
	if shaBytes(data) != s.SHA256 {
		return fmt.Errorf("package SHA-256 mismatch")
	}
	pub, e := base64.StdEncoding.DecodeString(s.PublicKey)
	if e != nil || len(pub) != ed25519.PublicKeySize {
		return fmt.Errorf("invalid public key")
	}
	if fingerprintPub(ed25519.PublicKey(pub)) != s.Fingerprint {
		return fmt.Errorf("publisher fingerprint mismatch")
	}
	sig, e := base64.StdEncoding.DecodeString(s.Signature)
	if e != nil || !ed25519.Verify(ed25519.PublicKey(pub), data, sig) {
		return fmt.Errorf("invalid package signature")
	}
	return nil
}
func signPackageFile(pkg, keyPath, out string) (string, error) {
	b, e := os.ReadFile(pkg)
	if e != nil {
		return "", e
	}
	k, e := readPrivateKey(keyPath)
	if e != nil {
		return "", e
	}
	s := signBytes(b, k)
	raw, _ := json.MarshalIndent(s, "", "  ")
	raw = append(raw, '\n')
	if out == "" {
		out = pkg + ".sig"
	}
	return out, os.WriteFile(out, raw, 0644)
}
func readSignature(path string) (packageSignature, error) {
	var s packageSignature
	b, e := readRegistryFile(path, registryMaxMetadataBytes)
	if e != nil {
		return s, e
	}
	e = json.Unmarshal(b, &s)
	return s, e
}
func packageManifestIdentity(raw []byte) (string, string, error) {
	vals := map[string]string{}
	seenIdentity := map[string]bool{}
	inProject := false
	for _, line := range strings.Split(strings.ReplaceAll(string(raw), "\r\n", "\n"), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if strings.HasPrefix(line, "[") {
			inProject = line == "[project]"
			continue
		}
		if !inProject {
			continue
		}
		eq := strings.Index(line, "=")
		if eq < 1 {
			continue
		}
		key := strings.TrimSpace(line[:eq])
		if (key == "name" || key == "version") && seenIdentity[key] {
			return "", "", fmt.Errorf("saga.toml contains duplicate project.%s", key)
		}
		if key == "name" || key == "version" {
			seenIdentity[key] = true
		}
		if key != "name" && key != "version" {
			continue
		}
		value := trimTomlComment(line[eq+1:])
		q, err := parseTomlString(value)
		if err != nil {
			return "", "", fmt.Errorf("saga.toml %s: %w", key, err)
		}
		vals[key] = q
	}
	name, version := vals["name"], vals["version"]
	if !validRegistryPackageIdentity(name, version) {
		return "", "", fmt.Errorf("invalid saga.toml package identity")
	}
	return name, version, nil
}

func readZipEntryLimited(f *zip.File, max uint64) ([]byte, error) {
	if f.UncompressedSize64 > max {
		return nil, fmt.Errorf("package entry exceeds safety limit: %s", f.Name)
	}
	r, err := f.Open()
	if err != nil {
		return nil, err
	}
	defer r.Close()
	b, err := io.ReadAll(io.LimitReader(r, int64(max)+1))
	if err != nil {
		return nil, err
	}
	if uint64(len(b)) != f.UncompressedSize64 || uint64(len(b)) > max {
		return nil, fmt.Errorf("package entry size mismatch: %s", f.Name)
	}
	return b, nil
}

func packageIdentity(data []byte) (string, string, error) {
	if int64(len(data)) > registryMaxPackageBytes {
		return "", "", fmt.Errorf("package exceeds %d bytes", registryMaxPackageBytes)
	}
	zr, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		return "", "", err
	}
	if len(zr.File) > registryMaxExtractedFiles {
		return "", "", fmt.Errorf("package contains too many files")
	}
	seen := map[string]bool{}
	fileByName := map[string]*zip.File{}
	var total uint64
	var manifestRaw, lockRaw []byte
	for _, f := range zr.File {
		if strings.ContainsRune(f.Name, '\\') || strings.ContainsRune(f.Name, '\x00') {
			return "", "", fmt.Errorf("unsafe package path")
		}
		rel := pathpkg.Clean(f.Name)
		canonicalName := strings.TrimSuffix(f.Name, "/")
		if rel == "." || rel == ".." || strings.HasPrefix(rel, "/") || strings.HasPrefix(rel, "../") {
			return "", "", fmt.Errorf("unsafe package path")
		}
		if rel != canonicalName {
			return "", "", fmt.Errorf("non-canonical package path: %s", f.Name)
		}
		if seen[rel] {
			return "", "", fmt.Errorf("duplicate package path: %s", rel)
		}
		seen[rel] = true
		if !f.FileInfo().IsDir() {
			fileByName[rel] = f
		}
		mode := f.Mode()
		if mode&os.ModeSymlink != 0 || (!mode.IsRegular() && !f.FileInfo().IsDir()) {
			return "", "", fmt.Errorf("unsupported package file type: %s", rel)
		}
		total += f.UncompressedSize64
		if f.UncompressedSize64 > uint64(registryMaxPackageBytes) || total > registryMaxExtractedBytes {
			return "", "", fmt.Errorf("package expanded content exceeds safety limit")
		}
		if rel == "saga.toml" {
			manifestRaw, err = readZipEntryLimited(f, uint64(registryMaxMetadataBytes))
			if err != nil {
				return "", "", err
			}
		}
		if rel == "saga.lock" {
			lockRaw, err = readZipEntryLimited(f, uint64(registryMaxMetadataBytes))
			if err != nil {
				return "", "", err
			}
		}
	}
	if manifestRaw == nil || lockRaw == nil {
		return "", "", fmt.Errorf("package must contain saga.toml and saga.lock")
	}
	manifestName, manifestVersion, err := packageManifestIdentity(manifestRaw)
	if err != nil {
		return "", "", err
	}
	var l LockData
	if _, err = decodeJSONSaga(string(lockRaw)); err != nil {
		return "", "", fmt.Errorf("invalid saga.lock: %w", err)
	}
	if err = json.Unmarshal(lockRaw, &l); err != nil {
		return "", "", fmt.Errorf("invalid saga.lock: %w", err)
	}
	if !validRegistryPackageIdentity(l.Project.Name, l.Project.Version) || l.Project.Name != manifestName || l.Project.Version != manifestVersion {
		return "", "", fmt.Errorf("package manifest/lock identity mismatch")
	}
	expectedFiles := map[string]bool{"saga.lock": true}
	tracked := map[string]bool{}
	for _, rec := range l.Files {
		rel := pathpkg.Clean(strings.ReplaceAll(rec.Path, "\\", "/"))
		if rel == "." || rel == ".." || strings.HasPrefix(rel, "/") || strings.HasPrefix(rel, "../") || strings.ContainsRune(rec.Path, '\x00') || strings.ContainsRune(rec.Path, '\\') {
			return "", "", fmt.Errorf("unsafe package lock path: %s", rec.Path)
		}
		if rel != rec.Path {
			return "", "", fmt.Errorf("non-canonical package lock path: %s", rec.Path)
		}
		if tracked[rel] {
			return "", "", fmt.Errorf("package lock contains duplicate file path: %s", rel)
		}
		tracked[rel] = true
		expectedFiles[rel] = true
		zf, ok := fileByName[rel]
		if !ok {
			return "", "", fmt.Errorf("package lock tracked file is missing: %s", rel)
		}
		content, er := readZipEntryLimited(zf, uint64(registryMaxPackageBytes))
		if er != nil {
			return "", "", er
		}
		h := sha256.Sum256(content)
		if rec.Size != int64(len(content)) || !strings.EqualFold(rec.SHA256, hex.EncodeToString(h[:])) {
			return "", "", fmt.Errorf("package content does not match saga.lock: %s", rel)
		}
	}
	if !tracked["saga.toml"] {
		return "", "", fmt.Errorf("package lock does not track saga.toml")
	}
	for rel := range fileByName {
		if !expectedFiles[rel] {
			return "", "", fmt.Errorf("package contains file not tracked by saga.lock: %s", rel)
		}
	}
	return manifestName, manifestVersion, nil
}

func registryPath(root, name, version string) (string, error) {
	if !validRegistryPackageIdentity(name, version) {
		return "", fmt.Errorf("invalid package identity")
	}
	return filepath.Join(root, name, version), nil
}

func signatureFromHeaders(h http.Header, data []byte) (packageSignature, error) {
	sig := strings.TrimSpace(h.Get("X-Saga-Signature"))
	pub := strings.TrimSpace(h.Get("X-Saga-Publisher-Key"))
	fp := normalizeFingerprint(h.Get("X-Saga-Publisher-Fingerprint"))
	digest := strings.ToLower(strings.TrimSpace(h.Get("X-Saga-Sha256")))
	if sig == "" || pub == "" || fp == "" || digest == "" {
		return packageSignature{}, fmt.Errorf("complete signed publisher headers are required")
	}
	ps := packageSignature{Algorithm: "Ed25519", SHA256: digest, Signature: sig, PublicKey: pub, Fingerprint: fp}
	if err := verifyPackageSignature(data, ps); err != nil {
		return packageSignature{}, err
	}
	return ps, nil
}

func setSignatureHeaders(h http.Header, s packageSignature) {
	h.Set("X-Saga-Sha256", s.SHA256)
	h.Set("X-Saga-Signature", s.Signature)
	h.Set("X-Saga-Publisher-Key", s.PublicKey)
	h.Set("X-Saga-Publisher-Fingerprint", s.Fingerprint)
}

func persistRegistryPackage(root, name, version string, data []byte, sig packageSignature) (bool, error) {
	dir, err := registryPath(root, name, version)
	if err != nil {
		return false, err
	}
	parent := filepath.Dir(dir)
	if err = os.MkdirAll(parent, 0755); err != nil {
		return false, err
	}
	if existing, er := readRegistryFile(filepath.Join(dir, "package.sagapkg"), registryMaxPackageBytes); er == nil {
		if shaBytes(existing) != sig.SHA256 {
			return false, fmt.Errorf("version already exists with different content")
		}
		existingSig, er2 := readSignature(filepath.Join(dir, "package.sig"))
		if er2 != nil || verifyPackageSignature(existing, existingSig) != nil || existingSig.Fingerprint != sig.Fingerprint {
			return false, fmt.Errorf("version already exists with invalid or different publisher evidence")
		}
		return true, nil
	} else if !os.IsNotExist(er) {
		return false, er
	}
	stage, err := os.MkdirTemp(parent, "."+version+".publish-")
	if err != nil {
		return false, err
	}
	keep := false
	defer func() {
		if !keep {
			_ = os.RemoveAll(stage)
		}
	}()
	if err = os.WriteFile(filepath.Join(stage, "package.sagapkg"), data, 0644); err != nil {
		return false, err
	}
	raw, _ := json.MarshalIndent(sig, "", "  ")
	if err = os.WriteFile(filepath.Join(stage, "package.sig"), append(raw, '\n'), 0644); err != nil {
		return false, err
	}
	if err = os.Rename(stage, dir); err != nil {
		// A second writer may have won the immutable version race.
		if existing, er := readRegistryFile(filepath.Join(dir, "package.sagapkg"), registryMaxPackageBytes); er == nil && shaBytes(existing) == sig.SHA256 {
			existingSig, er2 := readSignature(filepath.Join(dir, "package.sig"))
			if er2 == nil && verifyPackageSignature(existing, existingSig) == nil && existingSig.Fingerprint == sig.Fingerprint {
				return true, nil
			}
		}
		return false, err
	}
	keep = true
	return false, nil
}

func registryLoopbackBind(addr string) bool {
	host, _, err := net.SplitHostPort(addr)
	if err != nil {
		return false
	}
	host = strings.ToLower(strings.TrimSuffix(host, "."))
	if host == "localhost" || strings.HasSuffix(host, ".localhost") {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

func runRegistryServer(root, addr, tlsCert, tlsKey string) error {
	if tlsCert == "" && tlsKey == "" && !registryLoopbackBind(addr) {
		return fmt.Errorf("registry publishing requires TLS on non-loopback addresses")
	}
	if e := os.MkdirAll(root, 0755); e != nil {
		return e
	}
	searchIndex, err := newRegistrySearchIndex(root)
	if err != nil {
		return err
	}
	secureHeaders := func(w http.ResponseWriter) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		secureHeaders(w)
		json.NewEncoder(w).Encode(map[string]any{"status": "ok", "schema": 1})
	})

	// Registry Protocol v1: raw signed package transfer. These endpoints are
	// shared with the Python reference implementation. Legacy 0.25 JSON/base64
	// endpoints remain below for migration only.
	var publishMu sync.Mutex
	mux.HandleFunc("/v1/packages/", func(w http.ResponseWriter, r *http.Request) {
		secureHeaders(w)
		parts := strings.Split(strings.TrimPrefix(r.URL.Path, "/v1/packages/"), "/")
		if len(parts) != 2 || !validRegistryPackageIdentity(parts[0], parts[1]) {
			http.Error(w, "invalid package identity", 400)
			return
		}
		name, version := parts[0], parts[1]
		switch r.Method {
		case http.MethodPut:
			token := os.Getenv("SAGA_REGISTRY_TOKEN")
			if token == "" {
				http.Error(w, "publishing is disabled until SAGA_REGISTRY_TOKEN is configured", 503)
				return
			}
			wantAuth := sha256.Sum256([]byte("Bearer " + token))
			gotAuth := sha256.Sum256([]byte(r.Header.Get("Authorization")))
			if subtle.ConstantTimeCompare(gotAuth[:], wantAuth[:]) != 1 {
				http.Error(w, "unauthorized", 401)
				return
			}
			body, err := readRegistryBody(http.MaxBytesReader(w, r.Body, registryMaxPackageBytes), registryMaxPackageBytes)
			if err != nil {
				http.Error(w, "package too large or unreadable", 413)
				return
			}
			if n, v, er := packageIdentity(body); er != nil || n != name || v != version {
				http.Error(w, "identity mismatch", 400)
				return
			}
			sig, err := signatureFromHeaders(r.Header, body)
			if err != nil {
				http.Error(w, "invalid signed package: "+err.Error(), 400)
				return
			}
			publishMu.Lock()
			idem, err := persistRegistryPackage(root, name, version, body, sig)
			if err == nil {
				searchIndex.recordPublished(name, version, body, sig)
			}
			publishMu.Unlock()
			if err != nil {
				if strings.Contains(err.Error(), "already exists") {
					http.Error(w, err.Error(), 409)
				} else {
					http.Error(w, err.Error(), 500)
				}
				return
			}
			w.Header().Set("Content-Type", "application/json; charset=utf-8")
			status := http.StatusCreated
			if idem {
				status = http.StatusOK
			}
			w.WriteHeader(status)
			_ = json.NewEncoder(w).Encode(map[string]any{"name": name, "version": version, "sha256": sig.SHA256, "size": len(body), "publisher_fingerprint": sig.Fingerprint, "idempotent": idem})
		case http.MethodGet:
			dir, err := registryPath(root, name, version)
			if err != nil {
				http.Error(w, err.Error(), 400)
				return
			}
			data, err := readRegistryFile(filepath.Join(dir, "package.sagapkg"), registryMaxPackageBytes)
			if err != nil {
				http.NotFound(w, r)
				return
			}
			sig, err := readSignature(filepath.Join(dir, "package.sig"))
			if err != nil || verifyPackageSignature(data, sig) != nil {
				http.Error(w, "stored package integrity failure", 500)
				return
			}
			w.Header().Set("Content-Type", "application/vnd.saga.package")
			setSignatureHeaders(w.Header(), sig)
			w.Header().Set("Content-Length", fmt.Sprint(len(data)))
			w.WriteHeader(200)
			_, _ = w.Write(data)
		default:
			http.Error(w, "method", 405)
		}
	})
	mux.HandleFunc("/v1/search", func(w http.ResponseWriter, r *http.Request) {
		secureHeaders(w)
		if r.Method != http.MethodGet {
			http.Error(w, "method", 405)
			return
		}
		q := r.URL.Query().Get("q")
		_ = json.NewEncoder(w).Encode(map[string]any{"packages": searchIndex.search(q)})
	})
	// Registry Protocol v1 is intentionally singular.  The 0.25 JSON/base64
	// endpoints are hard-disabled so reviewers and clients cannot accidentally
	// exercise a second, weaker publication protocol.
	legacyGone := func(w http.ResponseWriter, r *http.Request) {
		secureHeaders(w)
		w.WriteHeader(http.StatusGone)
		_ = json.NewEncoder(w).Encode(map[string]any{
			"error":       "legacy registry endpoint removed",
			"protocol":    "Registry Protocol v1",
			"replacement": "/v1/packages/{name}/{version} and /v1/search",
		})
	}
	mux.HandleFunc("/v1/publish", legacyGone)
	mux.HandleFunc("/v1/index", legacyGone)
	mux.HandleFunc("/v1/package/", legacyGone)
	srv := &http.Server{Addr: addr, Handler: mux, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 30 * time.Second, WriteTimeout: 30 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 1 << 20}
	fmt.Println("Saga registry listening on", addr)
	if tlsCert != "" || tlsKey != "" {
		if tlsCert == "" || tlsKey == "" {
			return fmt.Errorf("both --tls-cert and --tls-key are required")
		}
		return srv.ListenAndServeTLS(tlsCert, tlsKey)
	}
	return srv.ListenAndServe()
}
func registryPublish(project, registry, key string) error {
	if e := validateRegistryBaseURL(registry); e != nil {
		return e
	}
	pkg, e := packProject(project, "")
	if e != nil {
		return e
	}
	data, e := os.ReadFile(pkg)
	if e != nil {
		return e
	}
	if int64(len(data)) > registryMaxPackageBytes {
		return fmt.Errorf("package exceeds %d bytes", registryMaxPackageBytes)
	}
	k, e := readPrivateKey(key)
	if e != nil {
		return e
	}
	sig := signBytes(data, k)
	n, v, e := packageIdentity(data)
	if e != nil {
		return e
	}
	req, e := http.NewRequest(http.MethodPut, strings.TrimRight(registry, "/")+"/v1/packages/"+url.PathEscape(n)+"/"+url.PathEscape(v), bytes.NewReader(data))
	if e != nil {
		return e
	}
	req.Header.Set("Content-Type", "application/vnd.saga.package")
	setSignatureHeaders(req.Header, sig)
	if t := os.Getenv("SAGA_REGISTRY_TOKEN"); t != "" {
		req.Header.Set("Authorization", "Bearer "+t)
	}
	resp, e := registryHTTPClient.Do(req)
	if e != nil {
		return e
	}
	defer resp.Body.Close()
	b, e := readRegistryBody(resp.Body, registryMaxMetadataBytes)
	if e != nil {
		return e
	}
	if resp.StatusCode/100 != 2 {
		return fmt.Errorf("registry publish failed: %s", strings.TrimSpace(string(b)))
	}
	fmt.Print(string(b))
	return nil
}
func registrySearch(registry, q string) error {
	if e := validateRegistryBaseURL(registry); e != nil {
		return e
	}
	resp, e := registryHTTPClient.Get(strings.TrimRight(registry, "/") + "/v1/search?q=" + url.QueryEscape(q))
	if e != nil {
		return e
	}
	defer resp.Body.Close()
	if resp.StatusCode/100 != 2 {
		return fmt.Errorf("registry search failed: %s", resp.Status)
	}
	b, e := readRegistryBody(resp.Body, registryMaxMetadataBytes)
	if e != nil {
		return e
	}
	_, e = os.Stdout.Write(b)
	return e
}
func safeExtractSagaPackage(data []byte, dest string) error {
	if int64(len(data)) > registryMaxPackageBytes {
		return fmt.Errorf("package exceeds %d bytes", registryMaxPackageBytes)
	}
	zr, e := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if e != nil {
		return e
	}
	if len(zr.File) > registryMaxExtractedFiles {
		return fmt.Errorf("package contains too many files")
	}
	var total uint64
	seen := map[string]bool{}
	for _, f := range zr.File {
		if strings.ContainsRune(f.Name, '\\') || strings.ContainsRune(f.Name, '\x00') {
			return fmt.Errorf("unsafe package path")
		}
		posixName := strings.TrimSuffix(f.Name, "/")
		cleanPosix := pathpkg.Clean(f.Name)
		if cleanPosix != posixName {
			return fmt.Errorf("non-canonical package path: %s", f.Name)
		}
		rel := filepath.Clean(filepath.FromSlash(cleanPosix))
		if rel == "." || rel == ".." || filepath.IsAbs(rel) || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
			return fmt.Errorf("unsafe package path")
		}
		if seen[rel] {
			return fmt.Errorf("duplicate package path: %s", rel)
		}
		seen[rel] = true
		mode := f.Mode()
		if mode&os.ModeSymlink != 0 || (!mode.IsRegular() && !f.FileInfo().IsDir()) {
			return fmt.Errorf("unsupported package file type: %s", rel)
		}
		total += f.UncompressedSize64
		if f.UncompressedSize64 > uint64(registryMaxPackageBytes) || total > registryMaxExtractedBytes {
			return fmt.Errorf("package expanded content exceeds safety limit")
		}
		target := filepath.Join(dest, rel)
		if f.FileInfo().IsDir() {
			if e = os.MkdirAll(target, 0755); e != nil {
				return e
			}
			continue
		}
		if e = os.MkdirAll(filepath.Dir(target), 0755); e != nil {
			return e
		}
		r, e := f.Open()
		if e != nil {
			return e
		}
		b, e := io.ReadAll(io.LimitReader(r, int64(f.UncompressedSize64)+1))
		r.Close()
		if e != nil {
			return e
		}
		if uint64(len(b)) != f.UncompressedSize64 {
			return fmt.Errorf("package file size mismatch: %s", rel)
		}
		if e = os.WriteFile(target, b, 0644); e != nil {
			return e
		}
	}
	return nil
}


 // rejectPackagePathSymlinks checks the package destination before creating
 // staging directories. An existing link in .saga/packages must not redirect
 // an installation outside the current project.
 func rejectPackagePathSymlinks(root, destination string) error {
 	relative, err := filepath.Rel(root, destination)
 	if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
 		return fmt.Errorf("package destination escapes project")
 	}
 	current := root
 	for _, part := range strings.Split(relative, string(filepath.Separator)) {
 		current = filepath.Join(current, part)
 		info, err := os.Lstat(current)
 		if os.IsNotExist(err) {
 			continue
 		}
 		if err != nil {
 			return err
 		}
 		if info.Mode()&os.ModeSymlink != 0 {
 			return fmt.Errorf("package destination contains a symlink: %s", current)
 		}
 	}
 	return nil
 }
 
func registryAdd(projectRoot, spec, registry, trustOnce string) error {
	if e := validateRegistryBaseURL(registry); e != nil {
		return e
	}
	parts := strings.SplitN(spec, "@", 2)
	if len(parts) != 2 || !validRegistryPackageIdentity(parts[0], parts[1]) {
		return fmt.Errorf("package must be valid name@semver")
	}
	packageURL := strings.TrimRight(registry, "/") + "/v1/packages/" + url.PathEscape(parts[0]) + "/" + url.PathEscape(parts[1])
	resp, e := registryHTTPClient.Get(packageURL)
	if e != nil {
		return e
	}
	defer resp.Body.Close()
	if resp.StatusCode/100 != 2 {
		return fmt.Errorf("package fetch failed: %s", resp.Status)
	}
	data, e := readRegistryBody(resp.Body, registryMaxPackageBytes)
	if e != nil {
		return e
	}
	sig, e := signatureFromHeaders(resp.Header, data)
	if e != nil {
		return e
	}
	n, v, e := packageIdentity(data)
	if e != nil {
		return fmt.Errorf("registry package validation failed: %w", e)
	}
	if n != parts[0] || v != parts[1] {
		return fmt.Errorf("registry returned mismatched package identity")
	}
	fp := normalizeFingerprint(sig.Fingerprint)
	if trustOnce != "" {
		if normalizeFingerprint(trustOnce) != fp {
			return fmt.Errorf("publisher fingerprint does not match --trust value: got %s", fp)
		}
		if e = trustFingerprint(projectRoot, fp); e != nil {
			return e
		}
	} else {
		trusted, er := isFingerprintTrusted(projectRoot, fp)
		if er != nil {
			return er
		}
		if !trusted {
			return fmt.Errorf("untrusted publisher %s; review the publisher key, then run `saga registry trust %s --project %s` or retry add with `--trust %s`", fp, fp, projectRoot, fp)
		}
	}
	root, e := filepath.Abs(projectRoot)
	if e != nil {
		return e
	}
	dest := filepath.Join(root, ".saga", "packages", n, v)
	if err := rejectPackagePathSymlinks(root, dest); err != nil {
		return err
	}
	parent := filepath.Dir(dest)
	if e = os.MkdirAll(parent, 0755); e != nil {
		return e
	}
	stage, e := os.MkdirTemp(parent, "."+v+".install-")
	if e != nil {
		return e
	}
	keep := false
	defer func() {
		if !keep {
			_ = os.RemoveAll(stage)
		}
	}()
	if e = safeExtractSagaPackage(data, stage); e != nil {
		return e
	}
	// Re-check the project and full lock snapshot after extraction. The signed
	// archive must be internally self-consistent, not merely name/version-consistent.
	proj, e := loadProject(stage)
	if e != nil {
		return fmt.Errorf("installed package manifest invalid: %w", e)
	}
	if proj.Name != n || proj.Version != v {
		return fmt.Errorf("installed package identity mismatch")
	}
	lockOK, lockErrors, er := verifyLock(stage)
	if er != nil {
		return fmt.Errorf("installed package lock verification failed: %w", er)
	}
	if !lockOK {
		return fmt.Errorf("installed package lock verification failed: %s", strings.Join(lockErrors, "; "))
	}
	depPath := filepath.Join(root, "saga.dependencies.json")
	e = withKVFileLock(depPath, true, func() error {
		var dep dependencyLock
		dep.Packages = map[string]dependencyRecord{}
		if b, er := os.ReadFile(depPath); er == nil {
			if _, er = decodeJSONSaga(string(b)); er != nil {
				return fmt.Errorf("existing dependency lock is not strict JSON: %w", er)
			}
			if er = json.Unmarshal(b, &dep); er != nil {
				return fmt.Errorf("existing dependency lock is malformed: %w", er)
			}
			if dep.Packages == nil {
				dep.Packages = map[string]dependencyRecord{}
			}
		} else if !os.IsNotExist(er) {
			return er
		}
		if _, er := os.Stat(dest); er == nil {
			return fmt.Errorf("package target already exists")
		} else if !os.IsNotExist(er) {
			return er
		}
		if er := os.Rename(stage, dest); er != nil {
			return er
		}
		keep = true
		rel, er := filepath.Rel(root, dest)
		if er != nil {
			_ = os.RemoveAll(dest)
			keep = false
			return er
		}
		dep.Packages[n] = dependencyRecord{Version: v, SHA256: sig.SHA256, Path: filepath.ToSlash(rel)}
		raw, er := json.MarshalIndent(dep, "", "  ")
		if er != nil {
			_ = os.RemoveAll(dest)
			keep = false
			return er
		}
		tmpf, er := os.CreateTemp(filepath.Dir(depPath), ".saga.dependencies-*.tmp")
		if er != nil {
			_ = os.RemoveAll(dest)
			keep = false
			return er
		}
		tmp := tmpf.Name()
		cleanup := true
		defer func() {
			_ = tmpf.Close()
			if cleanup {
				_ = os.Remove(tmp)
			}
		}()
		if er = tmpf.Chmod(0644); er == nil {
			_, er = tmpf.Write(append(raw, '\n'))
		}
		if er == nil {
			er = tmpf.Sync()
		}
		if closeErr := tmpf.Close(); er == nil {
			er = closeErr
		}
		if er == nil {
			er = atomicReplacePath(tmp, depPath)
		}
		if er != nil {
			_ = os.RemoveAll(dest)
			keep = false
			return er
		}
		cleanup = false
		return nil
	})
	if e != nil {
		return e
	}
	return nil
}
func runRegistryCLI(args []string) int {
	if len(args) == 0 {
		fmt.Fprintln(os.Stderr, "registry subcommand required")
		return 64
	}
	switch args[0] {
	case "keygen":
		if len(args) != 3 {
			fmt.Fprintln(os.Stderr, "usage: saga registry keygen private.pem public.pem")
			return 64
		}
		if e := generateSigningKey(args[1], args[2]); e != nil {
			fmt.Fprintln(os.Stderr, e)
			return 74
		}
		return 0
	case "sign":
		if len(args) < 3 {
			fmt.Fprintln(os.Stderr, "usage: saga registry sign pkg --key private.pem")
			return 64
		}
		key := ""
		out := ""
		for j := 2; j < len(args); j++ {
			if args[j] == "--key" && j+1 < len(args) {
				key = args[j+1]
				j++
			} else if (args[j] == "-o" || args[j] == "--output") && j+1 < len(args) {
				out = args[j+1]
				j++
			}
		}
		p, e := signPackageFile(args[1], key, out)
		if e != nil {
			fmt.Fprintln(os.Stderr, e)
			return 74
		}
		fmt.Println("Signed:", p)
		return 0
	case "verify":
		if len(args) != 3 {
			return 64
		}
		data, e := os.ReadFile(args[1])
		if e == nil {
			var sig packageSignature
			sig, e = readSignature(args[2])
			if e == nil {
				e = verifyPackageSignature(data, sig)
			}
		}
		if e != nil {
			fmt.Fprintln(os.Stderr, e)
			return 7
		}
		fmt.Println("Signature verified")
		return 0
	case "serve":
		root := "registry-data"
		addr := "127.0.0.1:7331"
		tlsCert, tlsKey := "", ""
		for j := 1; j < len(args); j++ {
			if args[j] == "--root" && j+1 < len(args) {
				root = args[j+1]
				j++
			} else if args[j] == "--addr" && j+1 < len(args) {
				addr = args[j+1]
				j++
			} else if args[j] == "--tls-cert" && j+1 < len(args) {
				tlsCert = args[j+1]
				j++
			} else if args[j] == "--tls-key" && j+1 < len(args) {
				tlsKey = args[j+1]
				j++
			}
		}
		if e := runRegistryServer(root, addr, tlsCert, tlsKey); e != nil {
			fmt.Fprintln(os.Stderr, e)
			return 70
		}
		return 0
	case "publish":
		if len(args) < 2 {
			return 64
		}
		reg, key := "", ""
		for j := 2; j < len(args); j++ {
			if args[j] == "--registry" && j+1 < len(args) {
				reg = args[j+1]
				j++
			} else if args[j] == "--key" && j+1 < len(args) {
				key = args[j+1]
				j++
			}
		}
		if reg == "" || key == "" {
			return 64
		}
		if e := registryPublish(args[1], reg, key); e != nil {
			fmt.Fprintln(os.Stderr, e)
			return 74
		}
		return 0
	case "search":
		q := ""
		reg := ""
		if len(args) > 1 {
			q = args[1]
		}
		for j := 2; j < len(args); j++ {
			if args[j] == "--registry" && j+1 < len(args) {
				reg = args[j+1]
				j++
			}
		}
		if reg == "" {
			return 64
		}
		if e := registrySearch(reg, q); e != nil {
			fmt.Fprintln(os.Stderr, e)
			return 74
		}
		return 0
	case "trust":
		if len(args) < 2 {
			fmt.Fprintln(os.Stderr, "usage: saga registry trust <publisher-fingerprint> [--project path]")
			return 64
		}
		project := "."
		for j := 2; j < len(args); j++ {
			if args[j] == "--project" && j+1 < len(args) {
				project = args[j+1]
				j++
				continue
			}
			fmt.Fprintln(os.Stderr, "unknown trust option:", args[j])
			return 64
		}
		if e := trustFingerprint(project, args[1]); e != nil {
			fmt.Fprintln(os.Stderr, e)
			return 74
		}
		fmt.Println("Trusted publisher", normalizeFingerprint(args[1]))
		return 0
	case "add":
		if len(args) < 2 {
			return 64
		}
		project := "."
		reg := ""
		trustOnce := ""
		for j := 2; j < len(args); j++ {
			if args[j] == "--project" && j+1 < len(args) {
				project = args[j+1]
				j++
			} else if args[j] == "--registry" && j+1 < len(args) {
				reg = args[j+1]
				j++
			} else if args[j] == "--trust" && j+1 < len(args) {
				trustOnce = args[j+1]
				j++
			} else {
				fmt.Fprintln(os.Stderr, "unknown add option:", args[j])
				return 64
			}
		}
		if reg == "" {
			return 64
		}
		if e := registryAdd(project, args[1], reg, trustOnce); e != nil {
			fmt.Fprintln(os.Stderr, e)
			return 74
		}
		fmt.Println("Added", args[1])
		return 0
	}
	fmt.Fprintln(os.Stderr, "unknown registry subcommand")
	return 64
}
