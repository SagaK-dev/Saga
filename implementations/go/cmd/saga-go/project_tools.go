package main

import (
	"archive/zip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"hash/crc32"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

type Project struct {
	Root, Name, Version, Language, Entry, TestDir string
}
type LockFileRecord struct {
	Path   string `json:"path"`
	SHA256 string `json:"sha256"`
	Size   int64  `json:"size"`
}
type LockProject struct {
	Entry   string `json:"entry"`
	Name    string `json:"name"`
	Version string `json:"version"`
}
type LockData struct {
	Files           []LockFileRecord `json:"files"`
	Language        string           `json:"language"`
	LanguageVersion string           `json:"language_version"`
	Project         LockProject      `json:"project"`
	Schema          int              `json:"schema"`
}

var semverRE = regexp.MustCompile(`^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$`)

func validProjectName(s string) bool {
	if s == "" || normalizeNFC(s) != s {
		return false
	}
	for _, part := range strings.Split(s, "-") {
		if part == "" {
			return false
		}
		rs := []rune(part)
		if len(rs) == 0 || !isStart(rs[0]) {
			return false
		}
		for _, r := range rs[1:] {
			if !isContinue(r) {
				return false
			}
		}
	}
	return true
}

// parseTomlString accepts basic and literal strings used in saga.toml.
// Unsupported multi-line values are diagnosed rather than read partially.
func parseTomlString(v string) (string, error) {
	v = strings.TrimSpace(v)
	if len(v) < 2 {
		return "", fmt.Errorf("project values must be quoted TOML strings")
	}
	if v[0] == '\'' && v[len(v)-1] == '\'' {
		if len(v) >= 6 && strings.HasPrefix(v, "'''") {
			return "", fmt.Errorf("multi-line TOML strings are not supported by the native project loader")
		}
		value := v[1 : len(v)-1]
		if strings.ContainsAny(value, "'\n\r") {
			return "", fmt.Errorf("invalid TOML literal string")
		}
		return value, nil
	}
	if v[0] != '"' || v[len(v)-1] != '"' {
		return "", fmt.Errorf("project values must be quoted TOML strings")
	}
	if strings.HasPrefix(v, "\"\"\"") {
		return "", fmt.Errorf("multi-line TOML strings are not supported by the native project loader")
	}
	return strconv.Unquote(v)
}

func trimTomlComment(value string) string {
	quote := byte(0)
	escaped := false
	for i := 0; i < len(value); i++ {
		char := value[i]
		if quote == '"' {
			if escaped {
				escaped = false
			} else if char == '\\' {
				escaped = true
			} else if char == '"' {
				quote = 0
			}
		} else if quote == '\'' {
			if char == '\'' {
				quote = 0
			}
		} else {
			if char == '"' || char == '\'' {
				quote = char
			} else if char == '#' {
				return strings.TrimSpace(value[:i])
			}
		}
	}
	return strings.TrimSpace(value)
}
func loadProject(path string) (*Project, error) {
	abs, err := filepath.Abs(path)
	if err != nil {
		return nil, err
	}
	st, err := os.Stat(abs)
	if err != nil {
		return nil, err
	}
	manifest := abs
	if st.IsDir() {
		manifest = filepath.Join(abs, "saga.toml")
	}
	if resolved, e := filepath.EvalSymlinks(manifest); e == nil {
		manifest = resolved
	}
	raw, err := os.ReadFile(manifest)
	if err != nil {
		return nil, err
	}
	vals := map[string]string{}
	inProject := false
	seenProject := false
	for _, line := range strings.Split(strings.ReplaceAll(string(raw), "\r\n", "\n"), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if strings.HasPrefix(line, "[") {
			inProject = line == "[project]"
			if inProject {
				if seenProject {
					return nil, fmt.Errorf("duplicate [project] table")
				}
				seenProject = true
			}
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
		if key != "name" && key != "version" && key != "language" && key != "entry" && key != "test_dir" {
			continue
		}
		if _, found := vals[key]; found {
			return nil, fmt.Errorf("saga.toml contains duplicate project.%s", key)
		}
		value := trimTomlComment(line[eq+1:])
		q, e := parseTomlString(value)
		if e != nil {
			return nil, fmt.Errorf("saga.toml %s: %w", key, e)
		}
		vals[key] = q
	}
	name := vals["name"]
	version := vals["version"]
	language := vals["language"]
	entry := vals["entry"]
	testDir := vals["test_dir"]
	if language == "" {
		language = "1.0"
	}
	if entry == "" {
		entry = "main.saga"
	}
	if testDir == "" {
		testDir = "tests"
	}
	if !validProjectName(name) {
		return nil, fmt.Errorf("invalid project.name")
	}
	if !semverRE.MatchString(version) {
		return nil, fmt.Errorf("invalid project.version")
	}
	if language != "0.9" && language != "0.10" && language != "1.0" && language != "2027" {
		return nil, fmt.Errorf("unsupported project.language %s", language)
	}
	root := filepath.Dir(manifest)
	entryAbs := filepath.Clean(filepath.Join(root, filepath.FromSlash(entry)))
	testsAbs := filepath.Clean(filepath.Join(root, filepath.FromSlash(testDir)))
	for _, p := range []string{entryAbs, testsAbs} {
		rel, e := filepath.Rel(root, p)
		if e != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
			return nil, fmt.Errorf("project path escapes root")
		}
	}
	if !strings.HasSuffix(entry, ".saga") {
		return nil, fmt.Errorf("project.entry must end in .saga")
	}
	return &Project{root, name, version, language, entryAbs, testsAbs}, nil
}

func collectSourceFiles(entry, root string) ([]string, error) {
	seen := map[string]bool{}
	active := map[string]bool{}
	files := []string{}
	var visit func(string) error
	visit = func(path string) error {
		ap, err := filepath.Abs(path)
		if err != nil {
			return err
		}
		ap = filepath.Clean(ap)
		rel, err := filepath.Rel(root, ap)
		if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
			return fmt.Errorf("source import escapes project root: %s", path)
		}
		if active[ap] {
			return fmt.Errorf("cyclic source import: %s", path)
		}
		if seen[ap] {
			return nil
		}
		if hasSymlinkComponent(ap, root) {
			return fmt.Errorf("symlink source path is not allowed in a locked package: %s", path)
		}
		fi, err := os.Lstat(ap)
		if err != nil {
			return err
		}
		if fi.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("symlink source is not allowed in a locked package: %s", path)
		}
		raw, err := os.ReadFile(ap)
		if err != nil {
			return err
		}
		if !validUTF8String(string(raw)) {
			return fmt.Errorf("source is not valid UTF-8: %s", path)
		}
		toks, err := lex(string(raw), ap)
		if err != nil {
			return err
		}
		stmts, err := parse(toks)
		if err != nil {
			return err
		}
		active[ap] = true
		defer delete(active, ap)
		for _, s := range stmts {
			if u, ok := s.(*UseStmt); ok && u.SourcePath != "" {
				dep := filepath.Join(filepath.Dir(ap), filepath.FromSlash(u.SourcePath))
				if strings.HasPrefix(u.SourcePath, "pkg:") {
					dep, err = resolvePackageImport(root, u.SourcePath)
					if err != nil {
						return err
					}
				}
				if err := visit(dep); err != nil {
					return err
				}
			}
		}
		seen[ap] = true
		files = append(files, ap)
		return nil
	}
	if err := visit(entry); err != nil {
		return nil, err
	}
	sort.Strings(files)
	return files, nil
}
func fileDigest(path string) (string, int64, error) {
	b, e := os.ReadFile(path)
	if e != nil {
		return "", 0, e
	}
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:]), int64(len(b)), nil
}
func relSlash(root, path string) (string, error) {
	if fi, e := os.Lstat(path); e != nil {
		return "", e
	} else if fi.Mode()&os.ModeSymlink != 0 {
		return "", fmt.Errorf("symlink not allowed: %s", path)
	}
	rel, e := filepath.Rel(root, path)
	if e != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("path escapes project root")
	}
	return filepath.ToSlash(rel), nil
}
func lockSnapshot(p *Project) (*LockData, error) {
	files, e := collectSourceFiles(p.Entry, p.Root)
	if e != nil {
		return nil, e
	}
	files = append(files, filepath.Join(p.Root, "saga.toml"))
	sort.Strings(files)
	seen := map[string]bool{}
	recs := []LockFileRecord{}
	for _, f := range files {
		rel, e := relSlash(p.Root, f)
		if e != nil {
			return nil, e
		}
		if seen[rel] {
			continue
		}
		seen[rel] = true
		h, n, e := fileDigest(f)
		if e != nil {
			return nil, e
		}
		recs = append(recs, LockFileRecord{rel, h, n})
	}
	sort.Slice(recs, func(i, j int) bool { return recs[i].Path < recs[j].Path })
	entryRel, _ := filepath.Rel(p.Root, p.Entry)
	return &LockData{recs, "Saga", p.Language, LockProject{filepath.ToSlash(entryRel), p.Name, p.Version}, 1}, nil
}
func writeLock(path string) (string, error) {
	p, e := loadProject(path)
	if e != nil {
		return "", e
	}
	data, e := lockSnapshot(p)
	if e != nil {
		return "", e
	}
	b, e := json.MarshalIndent(data, "", "  ")
	if e != nil {
		return "", e
	}
	b = append(b, '\n')
	out := filepath.Join(p.Root, "saga.lock")
	e = writeFileAtomic(out, b, 0644)
	return out, e
}
func verifyLock(path string) (bool, []string, error) {
	p, e := loadProject(path)
	if e != nil {
		return false, nil, e
	}
	raw, e := os.ReadFile(filepath.Join(p.Root, "saga.lock"))
	if e != nil {
		return false, nil, e
	}
	if _, e = decodeJSONSaga(string(raw)); e != nil {
		return false, nil, fmt.Errorf("saga.lock is not strict JSON: %w", e)
	}
	var actual LockData
	if e = json.Unmarshal(raw, &actual); e != nil {
		return false, nil, e
	}
	expected, e := lockSnapshot(p)
	if e != nil {
		return false, nil, e
	}
	a, _ := json.Marshal(actual)
	b, _ := json.Marshal(expected)
	if string(a) == string(b) {
		return true, nil, nil
	}
	errs := []string{"saga.lock does not match current project inputs"}
	return false, errs, nil
}
func validatePackageMemberSnapshot(rel string, data []byte, records map[string]LockFileRecord) error {
	rec, ok := records[rel]
	if !ok {
		return fmt.Errorf("cannot package file not tracked by saga.lock: %s", rel)
	}
	hash := sha256.Sum256(data)
	if rec.Size != int64(len(data)) || !strings.EqualFold(rec.SHA256, hex.EncodeToString(hash[:])) {
		return fmt.Errorf("project file changed while packing: %s", rel)
	}
	return nil
}

func packProject(path, out string) (string, error) {
	p, e := loadProject(path)
	if e != nil {
		return "", e
	}
	raw, e := os.ReadFile(filepath.Join(p.Root, "saga.lock"))
	if e != nil {
		return "", e
	}
	if _, e = decodeJSONSaga(string(raw)); e != nil {
		return "", fmt.Errorf("saga.lock is not strict JSON: %w", e)
	}
	var lock LockData
	if e = json.Unmarshal(raw, &lock); e != nil {
		return "", e
	}
	expected, e := lockSnapshot(p)
	if e != nil {
		return "", e
	}
	a, _ := json.Marshal(lock)
	b, _ := json.Marshal(expected)
	if string(a) != string(b) {
		return "", fmt.Errorf("lock verification failed: saga.lock does not match current project inputs")
	}
	if out == "" {
		out = filepath.Join(p.Root, "dist", p.Name+"-"+p.Version+".sagapkg")
	}
	if !filepath.IsAbs(out) {
		out = filepath.Join(p.Root, out)
	}
	if e = os.MkdirAll(filepath.Dir(out), 0755); e != nil {
		return "", e
	}
	members := []string{"saga.lock"}
	records := map[string]LockFileRecord{}
	for _, r := range lock.Files {
		members = append(members, r.Path)
		records[r.Path] = r
	}
	sort.Strings(members)
	uniq := []string{}
	last := ""
	for _, m := range members {
		if m != last {
			uniq = append(uniq, m)
			last = m
		}
	}
	outAbs, e := filepath.Abs(out)
	if e != nil {
		return "", e
	}
	for _, m := range uniq {
		inputAbs, er := filepath.Abs(filepath.Join(p.Root, filepath.FromSlash(m)))
		if er != nil {
			return "", er
		}
		if canonicalBuildPathIdentity(inputAbs) == canonicalBuildPathIdentity(outAbs) {
			return "", fmt.Errorf("package output may not overwrite a project input: %s", out)
		}
	}
	f, e := os.CreateTemp(filepath.Dir(out), "."+filepath.Base(out)+"-*.tmp")
	if e != nil {
		return "", e
	}
	tmp := f.Name()
	cleanup := true
	defer func() {
		_ = f.Close()
		if cleanup {
			_ = os.Remove(tmp)
		}
	}()
	if e = f.Chmod(0644); e != nil {
		return "", e
	}
	zw := zip.NewWriter(f)
	for _, m := range uniq {
		var data []byte
		if m == "saga.lock" {
			data = raw
		} else {
			data, e = os.ReadFile(filepath.Join(p.Root, filepath.FromSlash(m)))
			if e != nil {
				zw.Close()
				f.Close()
				return "", e
			}
			if e = validatePackageMemberSnapshot(m, data, records); e != nil {
				zw.Close()
				f.Close()
				return "", e
			}
		}
		h := &zip.FileHeader{Name: m, Method: zip.Store}
		h.CreatorVersion = (3 << 8) | 20
		h.ReaderVersion = 20
		h.Flags = 0
		h.ModifiedDate = 33 // DOS date 1980-01-01
		h.ModifiedTime = 0
		h.CRC32 = crc32.ChecksumIEEE(data)
		h.CompressedSize64 = uint64(len(data))
		h.UncompressedSize64 = uint64(len(data))
		h.ExternalAttrs = uint32(0o100644) << 16
		w, e := zw.CreateRaw(h)
		if e != nil {
			zw.Close()
			f.Close()
			return "", e
		}
		if _, e = w.Write(data); e != nil {
			zw.Close()
			f.Close()
			return "", e
		}
	}
	if e = zw.Close(); e != nil {
		return "", e
	}
	if e = f.Sync(); e != nil {
		return "", e
	}
	if e = f.Close(); e != nil {
		return "", e
	}
	if e = atomicReplacePath(tmp, out); e != nil {
		return "", e
	}
	cleanup = false
	return out, nil
}
