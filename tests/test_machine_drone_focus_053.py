from __future__ import annotations

import unittest

from saga import run_source


CONTROL_SURFACE_PROGRAM = r'''
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
'''


class MachineDroneFocus053Tests(unittest.TestCase):
    def test_common_machine_drone_control_surface(self) -> None:
        output: list[str] = []
        run_source(CONTROL_SURFACE_PROGRAM, output=output.append)
        self.assertEqual(output, ["1", "true", "4", "true", "true"])


if __name__ == "__main__":
    unittest.main()
