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
