//go:build !sagaruntime

package main

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
)

// Registry packages are immutable once published. The search index therefore
// keeps verified metadata in memory and updates only after a successful publish.
const registrySearchLimit = 200

type registrySearchRecord struct {
	Name        string
	Version     string
	SHA256      string
	Size        int
	Fingerprint string
}

type registrySearchIndex struct {
	mu      sync.RWMutex
	records map[string]registrySearchRecord
}

func newRegistrySearchIndex(root string) (*registrySearchIndex, error) {
	index := &registrySearchIndex{records: make(map[string]registrySearchRecord)}
	names, err := os.ReadDir(root)
	if err != nil {
		return nil, err
	}
	for _, name := range names {
		if !name.IsDir() || !validProjectName(name.Name()) {
			continue
		}
		versions, err := os.ReadDir(filepath.Join(root, name.Name()))
		if err != nil {
			return nil, fmt.Errorf("read registry package versions: %w", err)
		}
		for _, version := range versions {
			if !version.IsDir() || !semverRE.MatchString(version.Name()) {
				continue
			}
			dir := filepath.Join(root, name.Name(), version.Name())
			record, err := readVerifiedRegistrySearchRecord(dir, name.Name(), version.Name())
			if err != nil {
				// An incomplete or damaged package must never appear in search.
				continue
			}
			index.records[registryRecordKey(record.Name, record.Version)] = record
		}
	}
	return index, nil
}

func readVerifiedRegistrySearchRecord(dir, name, version string) (registrySearchRecord, error) {
	sig, err := readSignature(filepath.Join(dir, "package.sig"))
	if err != nil {
		return registrySearchRecord{}, err
	}
	data, err := readRegistryFile(filepath.Join(dir, "package.sagapkg"), registryMaxPackageBytes)
	if err != nil {
		return registrySearchRecord{}, err
	}
	if err := verifyPackageSignature(data, sig); err != nil {
		return registrySearchRecord{}, err
	}
	foundName, foundVersion, err := packageIdentity(data)
	if err != nil || foundName != name || foundVersion != version {
		return registrySearchRecord{}, fmt.Errorf("registry package identity mismatch")
	}
	return registrySearchRecord{
		Name: name, Version: version, SHA256: sig.SHA256,
		Size: len(data), Fingerprint: sig.Fingerprint,
	}, nil
}

func registryRecordKey(name, version string) string {
	return name + "@" + version
}

func (index *registrySearchIndex) recordPublished(name, version string, data []byte, sig packageSignature) {
	record := registrySearchRecord{
		Name: name, Version: version, SHA256: sig.SHA256,
		Size: len(data), Fingerprint: sig.Fingerprint,
	}
	index.mu.Lock()
	index.records[registryRecordKey(name, version)] = record
	index.mu.Unlock()
}

func (index *registrySearchIndex) search(query string) []map[string]any {
	folded := strings.ToLower(query)
	index.mu.RLock()
	found := make([]registrySearchRecord, 0)
	for _, record := range index.records {
		if folded == "" || strings.Contains(strings.ToLower(record.Name), folded) {
			found = append(found, record)
		}
	}
	index.mu.RUnlock()

	sort.Slice(found, func(i, j int) bool {
		if found[i].Name == found[j].Name {
			return found[i].Version < found[j].Version
		}
		return found[i].Name < found[j].Name
	})
	if len(found) > registrySearchLimit {
		found = found[:registrySearchLimit]
	}
	out := make([]map[string]any, 0, len(found))
	for _, record := range found {
		out = append(out, map[string]any{
			"name": record.Name, "version": record.Version,
			"sha256": record.SHA256, "size": record.Size,
			"publisher_fingerprint": record.Fingerprint,
		})
	}
	return out
}
