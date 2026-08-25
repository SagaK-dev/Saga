from __future__ import annotations

import unittest
from decimal import Decimal as D

from saga import run_source
from saga.stdlib.drone_control import (
    DroneControlError,
    FlightManager,
    Geofence,
    LinkMonitor,
    MissionPlan,
    RTLPlanner,
    mavlink_set_attitude_target,
)
from saga.stdlib.machine_control import SafetyLatch


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

    def test_flight_health_rejects_out_of_range_battery_fraction(self) -> None:
        manager = FlightManager(SafetyLatch(), D("0.2"))
        for battery in (D("-0.01"), D("1.01")):
            with self.subTest(battery=battery):
                with self.assertRaises(DroneControlError):
                    manager.update_health(True, True, battery, True, True, True)

    def test_link_monitor_does_not_rebase_on_duplicate_or_old_packet(self) -> None:
        monitor = LinkMonitor(alpha=D("0.5"))
        for sequence, latency in ((10, 10), (11, 12), (14, 20), (14, 30), (13, 40), (15, 50)):
            monitor.observe(sequence, D(latency))
        stats = monitor.stats()
        self.assertEqual((stats["lost"], stats["duplicates"], stats["out_of_order"]), (2, 1, 1))
        self.assertEqual(monitor.last_sequence, 15)

    def test_navigation_coordinates_reject_impossible_lat_lon(self) -> None:
        with self.assertRaises(DroneControlError):
            Geofence(D("91"), D("139"), D("100"), D("0"), D("120"))

        fence = Geofence(D("35"), D("139"), D("100"), D("0"), D("120"))
        with self.assertRaises(DroneControlError):
            fence.horizontal_distance_m(D("35"), D("181"))

        mission = MissionPlan()
        with self.assertRaises(DroneControlError):
            mission.add(D("-91"), D("139"), D("10"), D("2"), D("0"))

        with self.assertRaises(DroneControlError):
            RTLPlanner(D("35"), D("181"), D("5"), D("30"), D("2"))

        rtl = RTLPlanner(D("35"), D("139"), D("5"), D("30"), D("2"))
        with self.assertRaises(DroneControlError):
            rtl.target(D("91"), D("139"), D("10"))

    def test_mavlink_float32_overflow_is_a_control_error(self) -> None:
        with self.assertRaises(DroneControlError):
            mavlink_set_attitude_target(
                1,
                1,
                1,
                1,
                1,
                0,
                [D("1e100"), D("0"), D("0"), D("0")],
                [D("0"), D("0"), D("0")],
                D("0.5"),
                0,
            )


if __name__ == "__main__":
    unittest.main()
