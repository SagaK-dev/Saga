package main

import (
	"encoding/json"
	"math"
	"net"
	"strings"
	"testing"
)

func Test041VisionNMSAndTracking(t *testing.T) {
	ds := []visionDetection{{ClassID: 1, Confidence: .9, Box: []float64{0, 0, 100, 100}}, {ClassID: 1, Confidence: .8, Box: []float64{10, 10, 95, 95}}, {ClassID: 2, Confidence: .7, Box: []float64{10, 10, 95, 95}}}
	kept, e := visionNMS(ds, .5)
	if e != nil || len(kept) != 2 {
		t.Fatalf("nms=%v %v", len(kept), e)
	}
	tr := &visionTracker{MaxDistance: 30, MaxMissed: 2, NextID: 1, Tracks: map[int]visionTrack{}}
	a := tr.update([]visionDetection{{ClassID: 1, Confidence: .9, Box: []float64{0, 0, 100, 100}}})
	b := tr.update([]visionDetection{{ClassID: 1, Confidence: .9, Box: []float64{3, 4, 103, 104}}})
	if a[0]["track_id"] != b[0]["track_id"] {
		t.Fatal("track identity changed")
	}
	cam := &visionCamera{Fx: 500, Fy: 500, Cx: 320, Cy: 240}
	v, e := cam.bearing(320, 240)
	if e != nil || v != [3]float64{0, 0, 1} {
		t.Fatalf("bearing=%v %v", v, e)
	}
}

func Test041TrajectoryAllocatorAndLinkMonitor(t *testing.T) {
	tr, e := newDroneTrajectory3D([3]float64{}, [3]float64{5, -2, 1}, 3, 2, 8)
	if e != nil {
		t.Fatal(e)
	}
	for j := 0; j < 700 && !tr.done(); j++ {
		s, e := tr.step(.02)
		if e != nil {
			t.Fatal(e)
		}
		for _, a := range s["acceleration"].([]float64) {
			if math.Abs(a) > 2.000001 {
				t.Fatal("accel limit")
			}
		}
	}
	if !tr.done() {
		t.Fatal("trajectory did not complete")
	}
	alloc := &droneAllocator{Matrix: [][4]float64{{1, 1, 0, 1}, {1, .5, .8660254, -1}, {1, -.5, .8660254, 1}, {1, -1, 0, -1}, {1, -.5, -.8660254, 1}, {1, .5, -.8660254, -1}}, Min: 0, Max: 1, Disabled: map[int]bool{2: true}}
	out, e := alloc.allocate([4]float64{.5, .02, -.01, .01})
	if e != nil || len(out) != 6 || out[2] != 0 {
		t.Fatalf("alloc %v %v", out, e)
	}
	report, e := alloc.report([4]float64{.5, .02, -.01, .01})
	if e != nil || !strings.Contains(report, `"disabled":[2]`) || !strings.Contains(report, `"residual"`) {
		t.Fatalf("report %s %v", report, e)
	}
	lm := &droneLinkMonitor{Alpha: .5}
	for _, x := range [][2]float64{{10, 10}, {11, 12}, {14, 20}, {14, 30}, {13, 40}} {
		if e := lm.observe(int(x[0]), x[1]); e != nil {
			t.Fatal(e)
		}
	}
	var st map[string]any
	if e := json.Unmarshal([]byte(lm.stats()), &st); e != nil {
		t.Fatal(e)
	}
	if int(st["lost"].(float64)) != 2 {
		t.Fatalf("stats=%v", st)
	}
}

func Test041CustomAllocatorParity(t *testing.T) {
	src := `use drone
let a = drone.allocator([[1.0,1.0,0.0,1.0],[1.0,0.5,0.8660254,-1.0],[1.0,-0.5,0.8660254,1.0],[1.0,-1.0,0.0,-1.0],[1.0,-0.5,-0.8660254,1.0],[1.0,0.5,-0.8660254,-1.0]],0.0,1.0)
drone.allocator_disable(a,[2])
print(drone.allocate(a,[0.5,0.02,-0.01,0.01]))
`
	out, err := runSagaForTest(t, src)
	if err != nil {
		t.Fatal(err)
	}
	if out == "" {
		t.Fatal("expected allocator output")
	}
}

func Test041UDPReceiveFromJSON(t *testing.T) {
	it := NewInterpreter(NewChecker(), func(string) {})
	it.NetHosts = []string{"127.0.0.1"}
	tok := Token{}
	v, err := it.callNativeModule("net", "udp", nil, tok)
	if err != nil {
		t.Fatal(err)
	}
	sock, ok := v.(*UDPConnValue)
	if !ok {
		t.Fatal("expected UDP socket")
	}
	defer sock.Conn.Close()
	addr := sock.Conn.LocalAddr().(*net.UDPAddr)
	sender, err := net.DialUDP("udp", nil, &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: addr.Port})
	if err != nil {
		t.Fatal(err)
	}
	defer sender.Close()
	if _, err = sender.Write([]byte("abc")); err != nil {
		t.Fatal(err)
	}
	if _, err = it.callNativeModule("net", "set_timeout_ms", []Value{sock, numberFromInt64(1000)}, tok); err != nil {
		t.Fatal(err)
	}
	got, err := it.callNativeModule("net", "udp_receive_from_json", []Value{sock, numberFromInt64(16)}, tok)
	if err != nil {
		t.Fatal(err)
	}
	text, ok := got.(string)
	if !ok || !strings.Contains(text, `"data_hex":"616263"`) || !strings.Contains(text, `"host":"127.0.0.1"`) {
		t.Fatalf("unexpected peer JSON: %v", got)
	}
}
