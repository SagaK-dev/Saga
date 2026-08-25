package main

import "testing"

func TestMachineDroneFocus053CommonControlSurface(t *testing.T) {
	src := `
use machine
use drone

let safety = machine.safety_latch()
let pid = machine.pid(1.0, 0.1, 0.0, -1.0, 1.0)
print(machine.pid_step(pid, 10.0, 8.0, 0.1))

let flight = drone.flight_manager(safety, 0.2)
drone.health_update(flight, true, true, 0.8, true, true, true)
drone.arm(flight, true)
print(drone.flight_allowed(flight))

let mixer = drone.quad_x_mixer(0.05, 1.0)
print(len(drone.mix_quad_x(mixer, 0.5, 0.0, 0.0, 0.0)))

let fence = drone.geofence(35.0, 139.0, 100.0, 0.0, 120.0)
print(drone.geofence_contains(fence, 35.0, 139.0, 10.0))

let heartbeat = drone.mavlink_heartbeat(1, 1, 1, 2, 3, 0, 0, 4)
print(len(heartbeat) > 12)
`
	out, err := runSagaForTest(t, src)
	if err != nil {
		t.Fatal(err)
	}
	if got, want := out, "1\ntrue\n4\ntrue\ntrue"; got != want {
		t.Fatalf("got %q want %q", got, want)
	}
}

func TestMachineDroneFocus053RejectsInvalidBatteryTelemetry(t *testing.T) {
	for _, battery := range []string{"-0.01", "1.01"} {
		src := `
use machine
use drone
let safety = machine.safety_latch()
let flight = drone.flight_manager(safety, 0.2)
drone.health_update(flight, true, true, ` + battery + `, true, true, true)
`
		if _, err := runSagaForTest(t, src); err == nil {
			t.Fatalf("battery fraction %s should be rejected", battery)
		}
	}
}

func TestMachineDroneFocus053RejectsImpossibleCoordinates(t *testing.T) {
	cases := []string{
		`use drone\nlet f = drone.geofence(91.0, 139.0, 100.0, 0.0, 120.0)`,
		`use drone\nlet f = drone.geofence(35.0, 139.0, 100.0, 0.0, 120.0)\nprint(drone.geofence_contains(f, 35.0, 181.0, 10.0))`,
		`use drone\nlet m = drone.mission()\ndrone.mission_add(m, -91.0, 139.0, 10.0, 2.0, 0.0)`,
		`use drone\nlet r = drone.rtl(35.0, 181.0, 5.0, 30.0, 2.0)`,
		`use drone\nlet r = drone.rtl(35.0, 139.0, 5.0, 30.0, 2.0)\nprint(drone.rtl_target_json(r, 91.0, 139.0, 10.0))`,
	}
	for _, src := range cases {
		if _, err := runSagaForTest(t, src); err == nil {
			t.Fatalf("impossible coordinate should be rejected: %q", src)
		}
	}
}

func TestDroneLinkMonitorKeepsForwardSequenceBaseline(t *testing.T) {
	m := &droneLinkMonitor{Alpha: 0.5}
	for _, sample := range [][2]int{{10, 10}, {11, 12}, {14, 20}, {14, 30}, {13, 40}, {15, 50}} {
		if err := m.observe(sample[0], float64(sample[1])); err != nil {
			t.Fatal(err)
		}
	}
	if m.Lost != 2 || m.Duplicates != 1 || m.OutOfOrder != 1 || m.Last != 15 {
		t.Fatalf("unexpected link state: %#v", m)
	}
}

func TestDroneMAVLinkHeartbeatRejectsOutOfRangeFields(t *testing.T) {
	cases := [][5]int{
		{-1, 3, 0, 0, 4},
		{2, 256, 0, 0, 4},
		{2, 3, -1, 0, 4},
		{2, 3, 0, -1, 4},
		{2, 3, 0, 0, 256},
	}
	for _, c := range cases {
		if _, err := mavlinkHeartbeat(1, 1, 1, c[0], c[1], c[2], c[3], c[4]); err == nil {
			t.Fatalf("invalid HEARTBEAT fields should be rejected: %v", c)
		}
	}
}
