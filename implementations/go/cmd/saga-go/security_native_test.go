package main

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"math/big"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"runtime"
)

func securityCall(t *testing.T, module, name string, args ...Value) Value {
	t.Helper()
	it := NewInterpreter(NewChecker(), func(string) {})
	it.TrustedHost = true
	v, err := it.callNativeModule(module, name, args, Token{File: "<security-test>", Line: 1, Col: 1})
	if err != nil {
		t.Fatalf("%s.%s: %v", module, name, err)
	}
	return v
}

func testCertificateChain(t *testing.T) (tls.Certificate, string, string) {
	t.Helper()
	now := time.Now().UTC()
	caKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	caT := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "Saga Test CA"}, NotBefore: now.Add(-time.Hour), NotAfter: now.Add(24 * time.Hour), IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature}
	caDER, err := x509.CreateCertificate(rand.Reader, caT, caT, &caKey.PublicKey, caKey)
	if err != nil {
		t.Fatal(err)
	}
	caPEM := string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: caDER}))

	srvKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	srvT := &x509.Certificate{SerialNumber: big.NewInt(2), Subject: pkix.Name{CommonName: "localhost"}, NotBefore: now.Add(-time.Hour), NotAfter: now.Add(12 * time.Hour), DNSNames: []string{"localhost"}, IPAddresses: []net.IP{net.ParseIP("127.0.0.1")}, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}, KeyUsage: x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment}
	srvDER, err := x509.CreateCertificate(rand.Reader, srvT, caT, &srvKey.PublicKey, caKey)
	if err != nil {
		t.Fatal(err)
	}
	cert, err := tls.X509KeyPair(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: srvDER}), pem.EncodeToMemory(&pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(srvKey)}))
	if err != nil {
		t.Fatal(err)
	}
	srvPEM := string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: srvDER}))
	return cert, caPEM, srvPEM
}

func TestCybersecurityAPIs(t *testing.T) {
	ni := func(v int64) Number { return numberFromInt64(v) }
	if got := securityCall(t, "crypto", "sha512", "abc").(string); len(got) != 128 {
		t.Fatalf("sha512 length=%d", len(got))
	}
	if got := securityCall(t, "crypto", "hmac_sha256", "key", "The quick brown fox jumps over the lazy dog").(string); got != "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8" {
		t.Fatalf("hmac=%s", got)
	}
	if got := securityCall(t, "crypto", "random_hex", ni(32)).(string); len(got) != 64 {
		t.Fatalf("random hex length=%d", len(got))
	}
	if !securityCall(t, "crypto", "constant_equal", "same", "same").(bool) || securityCall(t, "crypto", "constant_equal", "same", "diff").(bool) {
		t.Fatal("constant_equal")
	}
	hash := securityCall(t, "crypto", "password_hash", "correct horse battery staple").(string)
	if !securityCall(t, "crypto", "password_verify", "correct horse battery staple", hash).(bool) || securityCall(t, "crypto", "password_verify", "wrong", hash).(bool) {
		t.Fatal("password verification")
	}
	key := strings.Repeat("11", 32)
	enc := securityCall(t, "crypto", "aes_gcm_encrypt", key, "secret", "aad").(ResultValue)
	if !enc.OK {
		t.Fatal(enc.Value)
	}
	dec := securityCall(t, "crypto", "aes_gcm_decrypt", key, enc.Value.(string), "aad").(ResultValue)
	if !dec.OK || dec.Value != "secret" {
		t.Fatalf("decrypt=%#v", dec)
	}
	tampered := enc.Value.(string)
	if strings.HasSuffix(tampered, "0") {
		tampered = tampered[:len(tampered)-1] + "1"
	} else {
		tampered = tampered[:len(tampered)-1] + "0"
	}
	if bad := securityCall(t, "crypto", "aes_gcm_decrypt", key, tampered, "aad").(ResultValue); bad.OK {
		t.Fatal("tampered AES-GCM accepted")
	}

	file := filepath.Join(t.TempDir(), "payload.bin")
	if err := os.WriteFile(file, []byte("abc"), 0600); err != nil {
		t.Fatal(err)
	}
	fh := securityCall(t, "security", "file_sha256", file).(ResultValue)
	if !fh.OK || fh.Value != "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad" {
		t.Fatalf("file hash=%#v", fh)
	}
	if !securityCall(t, "security", "ip_valid", "2001:db8::1").(bool) || securityCall(t, "security", "ip_valid", "999.1.1.1").(bool) {
		t.Fatal("ip_valid")
	}
	cc := securityCall(t, "security", "cidr_contains", "10.0.0.0/8", "10.2.3.4").(ResultValue)
	if !cc.OK || cc.Value != true {
		t.Fatalf("cidr=%#v", cc)
	}

	cert, caPEM, srvPEM := testCertificateChain(t)
	ci := securityCall(t, "security", "certificate_info", srvPEM).(ResultValue)
	if !ci.OK {
		t.Fatal(ci.Value)
	}
	var certJSON map[string]any
	if err := json.Unmarshal([]byte(ci.Value.(string)), &certJSON); err != nil || certJSON["subject"] == nil {
		t.Fatalf("cert info: %v %#v", err, certJSON)
	}
	ln, err := tls.Listen("tcp", "127.0.0.1:0", &tls.Config{Certificates: []tls.Certificate{cert}, MinVersion: tls.VersionTLS12})
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	done := make(chan error, 1)
	go func() {
		c, e := ln.Accept()
		if e != nil {
			done <- e
			return
		}
		tc := c.(*tls.Conn)
		e = tc.Handshake()
		_ = tc.Close()
		done <- e
	}()
	port := ln.Addr().(*net.TCPAddr).Port
	probe := securityCall(t, "security", "tls_probe", "127.0.0.1", ni(int64(port)), "localhost", caPEM, ni(5000)).(ResultValue)
	if !probe.OK {
		t.Fatal(probe.Value)
	}
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	var tlsJSON map[string]any
	if err := json.Unmarshal([]byte(probe.Value.(string)), &tlsJSON); err != nil {
		t.Fatal(err)
	}
	if tlsJSON["verified_chain_count"].(float64) < 1 {
		t.Fatalf("unverified TLS: %#v", tlsJSON)
	}
}

func TestKVDBCrossProcessHelper(t *testing.T) {
	if os.Getenv("SAGA_DB_HELPER") != "1" {
		t.Skip("helper only")
	}
	path := os.Getenv("SAGA_DB_PATH")
	key := os.Getenv("SAGA_DB_KEY")
	db, err := openKVDB(path)
	if err != nil {
		t.Fatal(err)
	}
	it := NewInterpreter(NewChecker(), func(string) {})
	it.TrustedHost = true
	v, handled, err := it.callPlatformExpansion("db", "put", []Value{db, key, numberFromInt64(1)}, Token{File: "<db-helper>", Line: 1, Col: 1})
	if err != nil || !handled {
		t.Fatalf("put err=%v handled=%v", err, handled)
	}
	r := v.(ResultValue)
	if !r.OK {
		t.Fatal(r.Value)
	}
}

func runDBChild(t *testing.T, path, key string) {
	t.Helper()
	cmd := exec.Command(os.Args[0], "-test.run=^TestKVDBCrossProcessHelper$", "-test.v=false")
	cmd.Env = append(os.Environ(), "SAGA_DB_HELPER=1", "SAGA_DB_PATH="+path, "SAGA_DB_KEY="+key)
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("db helper: %v\n%s", err, out)
	}
}

func TestKVDBMultiProcessNoLostUpdateAndConflict(t *testing.T) {
	path := filepath.Join(t.TempDir(), "shared.json")
	db, err := openKVDB(path)
	if err != nil {
		t.Fatal(err)
	}
	it := NewInterpreter(NewChecker(), func(string) {})
	it.TrustedHost = true
	tok := Token{File: "<db-parent>", Line: 1, Col: 1}
	call := func(name string, args ...Value) Value {
		v, h, e := it.callPlatformExpansion("db", name, args, tok)
		if e != nil || !h {
			t.Fatalf("%s err=%v handled=%v", name, e, h)
		}
		return v
	}
	if r := call("put", db, "parent", numberFromInt64(1)).(ResultValue); !r.OK {
		t.Fatal(r.Value)
	}
	runDBChild(t, path, "child")
	if r := call("put", db, "after", numberFromInt64(1)).(ResultValue); !r.OK {
		t.Fatal(r.Value)
	}
	fresh, err := openKVDB(path)
	if err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"parent", "child", "after"} {
		got := call("get", fresh, key).(OptionValue)
		if !got.Present {
			t.Fatalf("lost key %s", key)
		}
	}

	txr := call("begin", db).(ResultValue)
	if !txr.OK {
		t.Fatal(txr.Value)
	}
	tx := txr.Value.(*KVTxValue)
	call("tx_put", tx, "tx", numberFromInt64(1))
	runDBChild(t, path, "child2")
	cr := call("commit", tx).(ResultValue)
	if cr.OK || cr.Value != "transaction conflict" {
		t.Fatalf("expected conflict: %#v", cr)
	}
	fresh2, err := openKVDB(path)
	if err != nil {
		t.Fatal(err)
	}
	if got := call("get", fresh2, "child2").(OptionValue); !got.Present {
		t.Fatal("child2 missing after conflict")
	}
	if got := call("get", fresh2, "tx").(OptionValue); got.Present {
		t.Fatal("conflicting tx was committed")
	}
}

func TestPBKDF2KnownVector(t *testing.T) {
	got := pbkdf2SHA256([]byte("password"), []byte("salt"), 1, 32)
	if hex.EncodeToString(got) != "120fb6cffcf8b32c43e7225256c4f837a86548c92ccc35480805987cb70be17b" {
		t.Fatalf("pbkdf2=%x", got)
	}
}

func TestCybersecuritySagaSourceTypecheckAndRuntime(t *testing.T) {
	src := `
use crypto
use security
print(len(security.sha512("abc")))
print(security.constant_equal("token","token"))
print(security.ip_valid("192.0.2.1"))
let inside = security.cidr_contains("192.0.2.0/24", "192.0.2.42")
print(unwrap_ok(inside))
let encrypted = crypto.aes_gcm_encrypt("1111111111111111111111111111111111111111111111111111111111111111", "payload", "aad")
let plain = crypto.aes_gcm_decrypt("1111111111111111111111111111111111111111111111111111111111111111", unwrap_ok(encrypted), "aad")
print(unwrap_ok(plain))
`
	got, err := runSagaForTest(t, src)
	if err != nil {
		t.Fatal(err)
	}
	if got != "128\ntrue\ntrue\ntrue\npayload" {
		t.Fatalf("unexpected output %q", got)
	}
}

func TestKVDBSymlinkAliasesShareLockIdentity(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink creation may require elevated Windows privilege")
	}
	dir := t.TempDir()
	realDir := filepath.Join(dir, "real")
	if err := os.Mkdir(realDir, 0700); err != nil {
		t.Fatal(err)
	}
	aliasDir := filepath.Join(dir, "alias")
	if err := os.Symlink(realDir, aliasDir); err != nil {
		t.Skipf("symlink unavailable: %v", err)
	}
	realPath := filepath.Join(realDir, "shared.json")
	aliasPath := filepath.Join(aliasDir, "shared.json")
	if got, want := canonicalDBLockIdentity(aliasPath), canonicalDBLockIdentity(realPath); got != want {
		t.Fatalf("lock identity bypass via symlink: got %q want %q", got, want)
	}
}

func TestKVDBSymlinkWritePersistsToCanonicalTarget(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink creation may require elevated Windows privilege")
	}
	dir := t.TempDir()
	realPath := filepath.Join(dir, "real.json")
	if err := os.WriteFile(realPath, []byte(`{"before":1}`), 0600); err != nil {
		t.Fatal(err)
	}
	aliasPath := filepath.Join(dir, "alias.json")
	if err := os.Symlink(realPath, aliasPath); err != nil {
		t.Skipf("symlink unavailable: %v", err)
	}
	db, err := openKVDB(aliasPath)
	if err != nil {
		t.Fatal(err)
	}
	if db.Path != canonicalDBLockIdentity(realPath) {
		t.Fatalf("db path was not canonicalized: %q", db.Path)
	}
	it := NewInterpreter(NewChecker(), func(string) {})
	it.TrustedHost = true
	tok := Token{File: "<symlink-db>", Line: 1, Col: 1}
	v, err := it.callNativeModule("db", "put", []Value{db, "after", numberFromInt64(2)}, tok)
	if err != nil {
		t.Fatal(err)
	}
	if r := v.(ResultValue); !r.OK {
		t.Fatal(r.Value)
	}
	st, err := os.Lstat(aliasPath)
	if err != nil {
		t.Fatalf("database alias disappeared: %v", err)
	}
	if st.Mode()&os.ModeSymlink == 0 {
		t.Fatalf("database write replaced symlink alias: mode=%v", st.Mode())
	}
	data, _, err := loadKVDataUnlocked(realPath)
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := mapLookup(data, "after"); !ok {
		t.Fatal("canonical target did not receive write through alias")
	}
}
