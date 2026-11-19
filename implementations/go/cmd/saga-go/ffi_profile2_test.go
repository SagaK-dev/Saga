//go:build sagaffi && linux && amd64 && cgo

package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestFFIProfile2StructByValueCallbackAndOwnership(t *testing.T) {
	dir := t.TempDir()
	csrc := `
#include <stdint.h>
#include <stdlib.h>
typedef struct { int32_t x; double y; } Pair;
typedef struct { int32_t v[3]; Pair p; } Complex;
Pair pair_twice(Pair a){ Pair r={a.x*2,a.y*2.0}; return r; }
int64_t apply_cb(int64_t (*cb)(int64_t)){ return cb(32)+1; }
int64_t read_pair_ptr(Pair *p){ return (int64_t)p->x + (int64_t)p->y; }
int64_t complex_sum(Complex c){ return c.v[0]+c.v[1]+c.v[2]+c.p.x+(int64_t)c.p.y; }
void *make_owned8(void){ return malloc(8); }
`
	cp := filepath.Join(dir, "probe.c")
	so := filepath.Join(dir, "libprobe.so")
	if err := os.WriteFile(cp, []byte(csrc), 0644); err != nil {
		t.Fatal(err)
	}
	cc, err := exec.LookPath("cc")
	if err != nil {
		t.Skip("C compiler unavailable for FFI integration test")
	}
	if out, err := exec.Command(cc, "-shared", "-fPIC", "-O2", cp, "-o", so).CombinedOutput(); err != nil {
		t.Fatalf("build C probe: %v\n%s", err, out)
	}
	source := `edition 2027
use ffi
unsafe {
  print(ffi.profile())
  let layout = ffi.layout(["x:i32", "y:f64"])
  print(ffi.layout_size(layout))
  let p = ffi.struct_alloc(layout)
  ffi.struct_set(layout,p,"x",21)
  ffi.struct_set(layout,p,"y",1.5f64)
  let doubled = ffi.call("` + so + `","pair_twice","struct{x:i32,y:f64}",["struct{x:i32,y:f64}"],[p])
  print(ffi.load(doubled,0,"i32"))
  print(ffi.load(doubled,8,"f64"))
  print(ffi.call("` + so + `","read_pair_ptr","i64",["ptr"],[p]))
  let complex_layout = ffi.layout(["v:array[3:i32]", "p:struct{x:i32,y:f64}"])
  let complex = ffi.struct_alloc(complex_layout)
  let arr = ffi.struct_get(complex_layout,complex,"v")
  ffi.store(arr,0,"i32",1)
  ffi.store(arr,4,"i32",2)
  ffi.store(arr,8,"i32",3)
  let nested = ffi.struct_get(complex_layout,complex,"p")
  ffi.store(nested,0,"i32",4)
  ffi.store(nested,8,"f64",5.0f64)
  print(ffi.call("` + so + `","complex_sum","i64",["struct{v:array[3:i32],p:struct{x:i32,y:f64}}"],[complex]))
  ffi.free(complex)
  fn plus10(x:int)->int=x+10
  let cb = ffi.callback(plus10,"i64",["i64"])
  let cbp = ffi.callback_ptr(cb)
  print(ffi.call("` + so + `","apply_cb","i64",["ptr"],[cbp]))
  ffi.callback_close(cb)
  let foreign = ffi.call("` + so + `","make_owned8","ptr",[],[])
  let owned = ffi.adopt(foreign,8)
  ffi.store(owned,0,"i64",99)
  print(ffi.load(owned,0,"i64"))
  ffi.free(owned)
  ffi.free(doubled)
  ffi.free(p)
}`
	toks, e := lex(source, "<ffi-profile2>")
	if e != nil {
		t.Fatal(e)
	}
	stmts, e := parse(toks)
	if e != nil {
		t.Fatal(e)
	}
	ch := NewChecker()
	if e = ch.Check(stmts); e != nil {
		t.Fatal(e)
	}
	var out []string
	it := NewInterpreter(ch, func(s string) { out = append(out, s) })
	it.TrustedHost = true // The test compiles and loads its own C probe.
	if e = it.Interpret(stmts); e != nil {
		t.Fatal(e)
	}
	got := strings.Join(out, "\n")
	want := "C-ABI-2\n16\n42\n3\n22\n15\n43\n99"
	if got != want {
		t.Fatalf("unexpected output\nwant:\n%s\ngot:\n%s", want, got)
	}
}

func TestFFIProfile2RejectsUseAfterFreeAndBorrowedFree(t *testing.T) {
	p := &FFIPointer{Addr: ffiAlloc(32), Size: 32, Owner: true}
	if p.Addr == 0 {
		t.Fatal("allocation failed")
	}
	child := &FFIPointer{Addr: p.Addr + 8, Size: 24, Parent: p}
	if err := ffiWriteValue(child, 0, "i32", numberFromInt64(7)); err != nil {
		t.Fatal(err)
	}
	if err := ffiFreePointer(child); err == nil || !strings.Contains(err.Error(), "borrowed") {
		t.Fatalf("borrowed free must fail: %v", err)
	}
	if err := ffiFreePointer(p); err != nil {
		t.Fatal(err)
	}
	if _, err := ffiReadValue(child, 0, "i32"); err == nil {
		t.Fatal("derived pointer stayed usable after owner free")
	}
	if err := ffiFreePointer(p); err == nil || !strings.Contains(err.Error(), "double free") {
		t.Fatalf("double free must fail: %v", err)
	}
}

func TestFFIProfile2NestedStructAndAdopt(t *testing.T) {
	outer, err := parseFFIStructDesc("struct{tag:i32,inner:struct{x:i16,y:i16}}")
	if err != nil {
		t.Fatal(err)
	}
	p := &FFIPointer{Addr: ffiAlloc(outer.Size), Size: outer.Size, Owner: true}
	if p.Addr == 0 {
		t.Fatal("allocation failed")
	}
	defer func() {
		if !p.Freed {
			_ = ffiFreePointer(p)
		}
	}()
	innerField, ok := outer.field("inner")
	if !ok {
		t.Fatal("inner field missing")
	}
	inner := &FFIPointer{Addr: p.Addr + uintptr(innerField.Offset), Size: innerField.Size, Parent: p}
	if err := ffiWriteValue(inner, 0, "i16", numberFromInt64(12)); err != nil {
		t.Fatal(err)
	}
	if err := ffiWriteValue(inner, 2, "i16", numberFromInt64(34)); err != nil {
		t.Fatal(err)
	}
	v, err := ffiReadValue(inner, 2, "i16")
	if err != nil {
		t.Fatal(err)
	}
	n := v.(Number)
	x, _ := n.Int()
	if x.Int64() != 34 {
		t.Fatalf("nested value=%s", x.String())
	}

	raw := &FFIPointer{Addr: ffiAlloc(8), Owner: false}
	if raw.Addr == 0 {
		t.Fatal("raw allocation failed")
	}
	owned := &FFIPointer{Addr: raw.Addr, Size: 8, Owner: true}
	raw.Freed = true
	raw.Addr = 0 // mirrors ffi.adopt transfer contract
	if err := ffiFreePointer(owned); err != nil {
		t.Fatal(err)
	}
}
