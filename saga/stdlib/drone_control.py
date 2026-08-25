from __future__ import annotations

import hashlib
import hmac
import json
import math
import struct
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable

from .machine_control import PIDController, SafetyLatch, JerkLimitedProfile


class DroneControlError(RuntimeError):
    pass


D0 = Decimal(0)
D1 = Decimal(1)
EARTH_RADIUS_M = Decimal("6371008.8")


def _finite(name: str, value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise DroneControlError(f"{name} must be a finite decimal")
    return value


def _positive(name: str, value: Decimal) -> Decimal:
    value = _finite(name, value)
    if value <= 0:
        raise DroneControlError(f"{name} must be > 0")
    return value


def _latitude(name: str, value: Decimal) -> Decimal:
    value = _finite(name, value)
    if not Decimal("-90") <= value <= Decimal("90"):
        raise DroneControlError(f"{name} must be in -90..90")
    return value


def _longitude(name: str, value: Decimal) -> Decimal:
    value = _finite(name, value)
    if not Decimal("-180") <= value <= Decimal("180"):
        raise DroneControlError(f"{name} must be in -180..180")
    return value


def _wrap_longitude(value: Decimal) -> Decimal:
    value = _finite("longitude", value)
    wrapped = (float(value) + 180.0) % 360.0 - 180.0
    if not math.isfinite(wrapped):
        raise DroneControlError("longitude projection is not finite")
    return Decimal(str(wrapped))


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return min(max(value, low), high)


def _wrap_pi(value: Decimal) -> Decimal:
    x = float(value)
    return Decimal(str(math.atan2(math.sin(x), math.cos(x))))


def _angle_error(target: Decimal, current: Decimal) -> Decimal:
    return _wrap_pi(target - current)


def _vector3(values: Iterable[Decimal], name: str) -> tuple[Decimal, Decimal, Decimal]:
    seq = tuple(values)
    if len(seq) != 3:
        raise DroneControlError(f"{name} must contain exactly three values")
    return tuple(_finite(f"{name}[{i}]", value) for i, value in enumerate(seq))  # type: ignore[return-value]


@dataclass(slots=True)
class AttitudeEstimator:
    """Complementary attitude estimator for hosted/SITL control loops.

    It integrates body rates and corrects roll/pitch from gravity and yaw from a
    tilt-compensated magnetometer when the respective sensor vectors are valid.
    This is intentionally not represented as an EKF replacement.
    """

    correction_gain: Decimal
    roll: Decimal = D0
    pitch: Decimal = D0
    yaw: Decimal = D0
    healthy: bool = False
    updates: int = 0

    def __post_init__(self) -> None:
        _finite("correction_gain", self.correction_gain)
        if not D0 <= self.correction_gain <= D1:
            raise DroneControlError("correction_gain must be in 0..1")

    def reset(self, roll: Decimal = D0, pitch: Decimal = D0, yaw: Decimal = D0) -> None:
        self.roll = _wrap_pi(_finite("roll", roll))
        self.pitch = _wrap_pi(_finite("pitch", pitch))
        self.yaw = _wrap_pi(_finite("yaw", yaw))
        self.healthy = False
        self.updates = 0

    def update(
        self,
        gx: Decimal, gy: Decimal, gz: Decimal,
        ax: Decimal, ay: Decimal, az: Decimal,
        mx: Decimal, my: Decimal, mz: Decimal,
        dt_seconds: Decimal,
    ) -> tuple[Decimal, Decimal, Decimal]:
        dt = _positive("dt_seconds", dt_seconds)
        gx, gy, gz = _vector3((gx, gy, gz), "gyro")
        ax, ay, az = _vector3((ax, ay, az), "accel")
        mx, my, mz = _vector3((mx, my, mz), "mag")

        roll_gyro = self.roll + gx * dt
        pitch_gyro = self.pitch + gy * dt
        yaw_gyro = self.yaw + gz * dt

        af = math.sqrt(float(ax * ax + ay * ay + az * az))
        accel_valid = af > 1e-9
        mag_norm = math.sqrt(float(mx * mx + my * my + mz * mz))
        mag_valid = mag_norm > 1e-12
        gain = self.correction_gain

        if accel_valid:
            roll_acc = Decimal(str(math.atan2(float(ay), float(az))))
            pitch_acc = Decimal(str(math.atan2(float(-ax), math.sqrt(float(ay * ay + az * az)))))
            self.roll = _wrap_pi((D1 - gain) * roll_gyro + gain * roll_acc)
            self.pitch = _wrap_pi((D1 - gain) * pitch_gyro + gain * pitch_acc)
        else:
            self.roll, self.pitch = _wrap_pi(roll_gyro), _wrap_pi(pitch_gyro)

        if mag_valid:
            r, p = float(self.roll), float(self.pitch)
            mx_f, my_f, mz_f = float(mx), float(my), float(mz)
            xh = mx_f * math.cos(p) + mz_f * math.sin(p)
            yh = mx_f * math.sin(r) * math.sin(p) + my_f * math.cos(r) - mz_f * math.sin(r) * math.cos(p)
            yaw_mag = Decimal(str(math.atan2(-yh, xh)))
            self.yaw = _wrap_pi((D1 - gain) * yaw_gyro + gain * yaw_mag)
        else:
            self.yaw = _wrap_pi(yaw_gyro)

        self.healthy = accel_valid
        self.updates += 1
        return self.roll, self.pitch, self.yaw


@dataclass(slots=True)
class AttitudeController:
    kp_roll: Decimal
    kp_pitch: Decimal
    kp_yaw: Decimal
    max_rate: Decimal

    def __post_init__(self) -> None:
        for name in ("kp_roll", "kp_pitch", "kp_yaw"):
            _finite(name, getattr(self, name))
        self.max_rate = _positive("max_rate", self.max_rate)

    def step(self, target_rpy: Iterable[Decimal], current_rpy: Iterable[Decimal]) -> list[Decimal]:
        target = _vector3(target_rpy, "target attitude")
        current = _vector3(current_rpy, "current attitude")
        gains = (self.kp_roll, self.kp_pitch, self.kp_yaw)
        return [
            _clamp(gain * _angle_error(sp, pv), -self.max_rate, self.max_rate)
            for gain, sp, pv in zip(gains, target, current)
        ]


def _vector4(values: Iterable[Decimal], name: str) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    seq = tuple(values)
    if len(seq) != 4:
        raise DroneControlError(f"{name} must contain exactly four values")
    return tuple(_finite(f"{name}[{i}]", value) for i, value in enumerate(seq))  # type: ignore[return-value]


def quaternion_normalize(values: Iterable[Decimal]) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    q = _vector4(values, "quaternion")
    norm = math.sqrt(sum(float(v * v) for v in q))
    if norm <= 1e-12:
        raise DroneControlError("quaternion norm must be non-zero")
    return tuple(Decimal(str(float(v) / norm)) for v in q)  # type: ignore[return-value]


def quaternion_from_rpy(roll: Decimal, pitch: Decimal, yaw: Decimal) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    r, p, y = float(_finite("roll", roll)) / 2, float(_finite("pitch", pitch)) / 2, float(_finite("yaw", yaw)) / 2
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return quaternion_normalize((
        Decimal(str(cr * cp * cy + sr * sp * sy)),
        Decimal(str(sr * cp * cy - cr * sp * sy)),
        Decimal(str(cr * sp * cy + sr * cp * sy)),
        Decimal(str(cr * cp * sy - sr * sp * cy)),
    ))


@dataclass(slots=True)
class QuaternionAttitudeController:
    kp_roll: Decimal
    kp_pitch: Decimal
    kp_yaw: Decimal
    max_rate: Decimal

    def __post_init__(self) -> None:
        for name in ("kp_roll", "kp_pitch", "kp_yaw"):
            _finite(name, getattr(self, name))
        self.max_rate = _positive("max_rate", self.max_rate)

    def step(self, target_quaternion: Iterable[Decimal], current_quaternion: Iterable[Decimal]) -> list[Decimal]:
        tw, tx, ty, tz = quaternion_normalize(target_quaternion)
        cw, cx, cy, cz = quaternion_normalize(current_quaternion)
        # q_error = target * conjugate(current). Choose the shortest rotation.
        ew = tw*cw + tx*cx + ty*cy + tz*cz
        ex = -tw*cx + tx*cw - ty*cz + tz*cy
        ey = -tw*cy + tx*cz + ty*cw - tz*cx
        ez = -tw*cz - tx*cy + ty*cx + tz*cw
        if ew < 0:
            ex, ey, ez = -ex, -ey, -ez
        gains = (self.kp_roll, self.kp_pitch, self.kp_yaw)
        return [_clamp(Decimal(2) * gain * err, -self.max_rate, self.max_rate) for gain, err in zip(gains, (ex, ey, ez))]


@dataclass(slots=True)
class RateController:
    roll: PIDController
    pitch: PIDController
    yaw: PIDController

    @classmethod
    def create(cls, kp: Decimal, ki: Decimal, kd: Decimal, output_limit: Decimal) -> "RateController":
        output_limit = _positive("output_limit", output_limit)
        return cls(*[
            PIDController.create(kp, ki, kd, -output_limit, output_limit)
            for _ in range(3)
        ])

    def reset(self) -> None:
        self.roll.reset(); self.pitch.reset(); self.yaw.reset()

    def step(self, target_rates: Iterable[Decimal], measured_rates: Iterable[Decimal], dt_seconds: Decimal) -> list[Decimal]:
        target = _vector3(target_rates, "target rates")
        measured = _vector3(measured_rates, "measured rates")
        return [
            ctrl.step(sp, pv, dt_seconds)
            for ctrl, sp, pv in zip((self.roll, self.pitch, self.yaw), target, measured)
        ]


@dataclass(slots=True)
class PositionController:
    position_kp: Decimal
    velocity_kp: Decimal
    velocity_ki: Decimal
    velocity_kd: Decimal
    max_speed: Decimal
    max_acceleration: Decimal
    velocity_pid: tuple[PIDController, PIDController, PIDController] = field(init=False)

    def __post_init__(self) -> None:
        self.position_kp = _positive("position_kp", self.position_kp)
        for name in ("velocity_kp", "velocity_ki", "velocity_kd"):
            _finite(name, getattr(self, name))
        self.max_speed = _positive("max_speed", self.max_speed)
        self.max_acceleration = _positive("max_acceleration", self.max_acceleration)
        self.velocity_pid = tuple(
            PIDController.create(self.velocity_kp, self.velocity_ki, self.velocity_kd,
                                 -self.max_acceleration, self.max_acceleration)
            for _ in range(3)
        )  # type: ignore[assignment]

    def reset(self) -> None:
        for ctrl in self.velocity_pid:
            ctrl.reset()

    def step(
        self,
        target_position: Iterable[Decimal],
        position: Iterable[Decimal],
        velocity: Iterable[Decimal],
        feedforward_velocity: Iterable[Decimal],
        dt_seconds: Decimal,
    ) -> list[Decimal]:
        target = _vector3(target_position, "target position")
        actual = _vector3(position, "position")
        measured_v = _vector3(velocity, "velocity")
        feedforward = _vector3(feedforward_velocity, "feedforward velocity")
        velocity_sp = [
            _clamp(ff + self.position_kp * (sp - pv), -self.max_speed, self.max_speed)
            for sp, pv, ff in zip(target, actual, feedforward)
        ]
        return [
            ctrl.step(sp, pv, dt_seconds)
            for ctrl, sp, pv in zip(self.velocity_pid, velocity_sp, measured_v)
        ]


@dataclass(slots=True)
class QuadXMixer:
    idle: Decimal = Decimal("0.05")
    maximum: Decimal = D1

    def __post_init__(self) -> None:
        _finite("idle", self.idle); _finite("maximum", self.maximum)
        if self.idle < 0 or self.maximum <= self.idle:
            raise DroneControlError("mixer requires 0 <= idle < maximum")

    def mix(self, thrust: Decimal, roll: Decimal, pitch: Decimal, yaw: Decimal) -> list[Decimal]:
        for name, value in (("thrust", thrust), ("roll", roll), ("pitch", pitch), ("yaw", yaw)):
            _finite(name, value)
        # Quad-X normalized allocation. Motor order: front-left, front-right,
        # rear-right, rear-left. Yaw signs assume alternating CW/CCW rotors.
        raw = [
            thrust + roll + pitch - yaw,
            thrust - roll + pitch + yaw,
            thrust - roll - pitch - yaw,
            thrust + roll - pitch + yaw,
        ]
        high, low = max(raw), min(raw)
        if high > self.maximum:
            raw = [v - (high - self.maximum) for v in raw]
            low = min(raw)
        if low < self.idle:
            raw = [v + (self.idle - low) for v in raw]
        return [_clamp(v, self.idle, self.maximum) for v in raw]


@dataclass(slots=True)
class Geofence:
    home_lat_deg: Decimal
    home_lon_deg: Decimal
    radius_m: Decimal
    min_alt_m: Decimal
    max_alt_m: Decimal

    def __post_init__(self) -> None:
        self.home_lat_deg = _latitude("home_lat_deg", self.home_lat_deg)
        self.home_lon_deg = _longitude("home_lon_deg", self.home_lon_deg)
        self.min_alt_m = _finite("min_alt_m", self.min_alt_m)
        self.max_alt_m = _finite("max_alt_m", self.max_alt_m)
        self.radius_m = _positive("radius_m", self.radius_m)
        if self.min_alt_m >= self.max_alt_m:
            raise DroneControlError("min_alt_m must be smaller than max_alt_m")

    def horizontal_distance_m(self, lat_deg: Decimal, lon_deg: Decimal) -> Decimal:
        lat_deg = _latitude("latitude", lat_deg); lon_deg = _longitude("longitude", lon_deg)
        p1, p2 = math.radians(float(self.home_lat_deg)), math.radians(float(lat_deg))
        dp = p2 - p1
        dl = math.radians(float(lon_deg - self.home_lon_deg))
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return Decimal(str(2 * float(EARTH_RADIUS_M) * math.asin(min(1.0, math.sqrt(a)))))

    def contains(self, lat_deg: Decimal, lon_deg: Decimal, alt_m: Decimal) -> bool:
        alt_m = _finite("altitude", alt_m)
        return self.min_alt_m <= alt_m <= self.max_alt_m and self.horizontal_distance_m(lat_deg, lon_deg) <= self.radius_m

    def predict_breach(self, lat_deg: Decimal, lon_deg: Decimal, alt_m: Decimal,
                       north_mps: Decimal, east_mps: Decimal, up_mps: Decimal,
                       horizon_seconds: Decimal) -> bool:
        horizon = _positive("horizon_seconds", horizon_seconds)
        lat_deg = _latitude("latitude", lat_deg)
        lon_deg = _longitude("longitude", lon_deg)
        alt_m = _finite("altitude", alt_m)
        north = _finite("north_mps", north_mps) * horizon
        east = _finite("east_mps", east_mps) * horizon
        up = _finite("up_mps", up_mps) * horizon
        lat_delta = Decimal(str(math.degrees(float(north / EARTH_RADIUS_M))))
        cos_lat = max(1e-9, abs(math.cos(math.radians(float(lat_deg)))))
        lon_delta = Decimal(str(math.degrees(float(east / EARTH_RADIUS_M)) / cos_lat))
        projected_lat = lat_deg + lat_delta
        if not projected_lat.is_finite() or not Decimal("-90") <= projected_lat <= Decimal("90"):
            return True
        projected_lon_raw = lon_deg + lon_delta
        if not projected_lon_raw.is_finite():
            return True
        projected_lon = _wrap_longitude(projected_lon_raw)
        return not self.contains(projected_lat, projected_lon, alt_m + up)


@dataclass(frozen=True, slots=True)
class Waypoint:
    lat_deg: Decimal
    lon_deg: Decimal
    alt_m: Decimal
    acceptance_radius_m: Decimal
    hold_seconds: Decimal


@dataclass(slots=True)
class MissionPlan:
    waypoints: list[Waypoint] = field(default_factory=list)
    index: int = 0
    hold_elapsed: Decimal = D0
    complete: bool = False

    def add(self, lat_deg: Decimal, lon_deg: Decimal, alt_m: Decimal,
            acceptance_radius_m: Decimal, hold_seconds: Decimal) -> None:
        acceptance_radius_m = _positive("acceptance_radius_m", acceptance_radius_m)
        hold_seconds = _finite("hold_seconds", hold_seconds)
        if hold_seconds < 0:
            raise DroneControlError("hold_seconds must be >= 0")
        self.waypoints.append(Waypoint(_latitude("lat", lat_deg), _longitude("lon", lon_deg),
                                       _finite("alt", alt_m), acceptance_radius_m, hold_seconds))
        self.complete = False

    def reset(self) -> None:
        self.index = 0; self.hold_elapsed = D0; self.complete = False

    def current(self) -> Waypoint | None:
        if self.complete or self.index >= len(self.waypoints):
            return None
        return self.waypoints[self.index]

    def update(self, lat_deg: Decimal, lon_deg: Decimal, alt_m: Decimal, dt_seconds: Decimal) -> str:
        dt = _positive("dt_seconds", dt_seconds)
        current = self.current()
        if current is None:
            self.complete = True
            return "complete"
        fence = Geofence(current.lat_deg, current.lon_deg, current.acceptance_radius_m,
                         current.alt_m - current.acceptance_radius_m,
                         current.alt_m + current.acceptance_radius_m)
        reached = fence.contains(lat_deg, lon_deg, alt_m)
        if not reached:
            self.hold_elapsed = D0
            return "navigate"
        self.hold_elapsed += dt
        if self.hold_elapsed < current.hold_seconds:
            return "hold"
        self.index += 1; self.hold_elapsed = D0
        if self.index >= len(self.waypoints):
            self.complete = True
            return "complete"
        return "advance"

    def target_json(self) -> str:
        current = self.current()
        if current is None:
            return json.dumps({"complete": True}, separators=(",", ":"))
        return json.dumps({
            "complete": False,
            "index": self.index,
            "lat": str(current.lat_deg),
            "lon": str(current.lon_deg),
            "alt_m": str(current.alt_m),
            "acceptance_radius_m": str(current.acceptance_radius_m),
            "hold_seconds": str(current.hold_seconds),
        }, separators=(",", ":"))


@dataclass(slots=True)
class FlightManager:
    """Explicit flight state/mode manager.

    Saga 0.40 deliberately performs no automatic in-flight safety transition.
    Health information is advisory/pre-arm input only; the application decides
    when to change mode, return, land, or disarm. An external ``SafetyLatch``
    may still be tripped explicitly by the application or hardware adapter.
    """

    safety: SafetyLatch
    minimum_arm_battery: Decimal
    state: str = "DISARMED"
    mode: str = "ATTITUDE"
    home_set: bool = False
    estimator_healthy: bool = False
    position_healthy: bool = False
    battery_fraction: Decimal = D1
    rc_link: bool = True
    data_link: bool = True
    last_reason: str = ""

    _MODES = frozenset({"MANUAL", "RATE", "ATTITUDE", "POSITION", "MISSION", "RTL", "LAND"})

    def __post_init__(self) -> None:
        _finite("minimum_arm_battery", self.minimum_arm_battery)
        if not D0 < self.minimum_arm_battery <= D1:
            raise DroneControlError("minimum_arm_battery must be in (0,1]")
        self.safety.register_stop(self._external_stop)

    def _external_stop(self) -> None:
        # This is not an automatic drone policy: it only mirrors an explicit
        # external machine-safety trip into the flight state.
        if self.state == "ARMED":
            self.state = "DISARMED"
            self.last_reason = self.safety.reason or "external safety stop"

    def update_health(self, estimator_healthy: bool, position_healthy: bool,
                      battery_fraction: Decimal, rc_link: bool, data_link: bool, home_set: bool) -> None:
        battery_fraction = _finite("battery_fraction", battery_fraction)
        if not D0 <= battery_fraction <= D1:
            raise DroneControlError("battery_fraction must be in 0..1")
        self.estimator_healthy = bool(estimator_healthy)
        self.position_healthy = bool(position_healthy)
        self.battery_fraction = battery_fraction
        self.rc_link = bool(rc_link); self.data_link = bool(data_link); self.home_set = bool(home_set)

    def prearm_reason(self, require_position: bool = True) -> str:
        if self.safety.tripped:
            return f"safety latch: {self.safety.reason}"
        if not self.estimator_healthy:
            return "attitude estimator unhealthy"
        if require_position and not self.position_healthy:
            return "position estimate unhealthy"
        if require_position and not self.home_set:
            return "home position not set"
        if self.battery_fraction < self.minimum_arm_battery:
            return "battery below arming threshold"
        if not self.rc_link and not self.data_link:
            return "no command/control link"
        return ""

    def arm(self, require_position: bool = True) -> None:
        if self.state != "DISARMED":
            raise DroneControlError(f"cannot arm from {self.state}")
        reason = self.prearm_reason(require_position)
        if reason:
            raise DroneControlError(f"prearm failed: {reason}")
        self.state = "ARMED"; self.last_reason = ""

    def disarm(self, reason: str = "") -> None:
        self.state = "DISARMED"
        self.last_reason = reason.strip()

    def set_mode(self, mode: str) -> None:
        mode = mode.strip().upper()
        if mode not in self._MODES:
            raise DroneControlError("flight mode must be MANUAL, RATE, ATTITUDE, POSITION, MISSION, RTL, or LAND")
        if self.state != "ARMED":
            raise DroneControlError("flight mode can only change while ARMED")
        self.mode = mode

    def flight_allowed(self) -> bool:
        return self.state == "ARMED" and not self.safety.tripped

    def control_allowed(self) -> bool:
        return self.flight_allowed()


@dataclass(slots=True)
class RTLPlanner:
    home_lat_deg: Decimal
    home_lon_deg: Decimal
    home_alt_m: Decimal
    return_alt_m: Decimal
    acceptance_radius_m: Decimal = Decimal("2")

    def __post_init__(self) -> None:
        self.home_lat_deg = _latitude("home_lat_deg", self.home_lat_deg)
        self.home_lon_deg = _longitude("home_lon_deg", self.home_lon_deg)
        self.home_alt_m = _finite("home_alt_m", self.home_alt_m)
        self.return_alt_m = _finite("return_alt_m", self.return_alt_m)
        self.acceptance_radius_m = _positive("acceptance_radius_m", self.acceptance_radius_m)
        if self.return_alt_m < self.home_alt_m:
            raise DroneControlError("return_alt_m must be >= home_alt_m")

    def target(self, lat_deg: Decimal, lon_deg: Decimal, alt_m: Decimal) -> dict[str, object]:
        lat_deg = _latitude("lat_deg", lat_deg)
        lon_deg = _longitude("lon_deg", lon_deg)
        distance = Geofence(self.home_lat_deg, self.home_lon_deg, Decimal("1"),
                            self.home_alt_m - Decimal("100000"), self.home_alt_m + Decimal("100000")).horizontal_distance_m(lat_deg, lon_deg)
        alt_m = _finite("alt_m", alt_m)
        if distance > self.acceptance_radius_m and alt_m < self.return_alt_m:
            phase, lat, lon, alt = "CLIMB", lat_deg, lon_deg, self.return_alt_m
        elif distance > self.acceptance_radius_m:
            phase, lat, lon, alt = "RETURN", self.home_lat_deg, self.home_lon_deg, max(alt_m, self.return_alt_m)
        elif alt_m > self.home_alt_m + Decimal("0.5"):
            phase, lat, lon, alt = "DESCEND", self.home_lat_deg, self.home_lon_deg, self.home_alt_m
        else:
            phase, lat, lon, alt = "LAND", self.home_lat_deg, self.home_lon_deg, self.home_alt_m
        return {"phase": phase, "lat": str(lat), "lon": str(lon), "alt_m": str(alt), "distance_m": str(distance)}

    def target_json(self, lat_deg: Decimal, lon_deg: Decimal, alt_m: Decimal) -> str:
        return json.dumps(self.target(lat_deg, lon_deg, alt_m), separators=(",", ":"))


def landing_vertical_velocity(altitude_agl_m: Decimal, descent_rate_mps: Decimal,
                              flare_altitude_m: Decimal, flare_rate_mps: Decimal) -> Decimal:
    altitude = _finite("altitude_agl_m", altitude_agl_m)
    descent = _positive("descent_rate_mps", descent_rate_mps)
    flare_alt = _positive("flare_altitude_m", flare_altitude_m)
    flare = _positive("flare_rate_mps", flare_rate_mps)
    if altitude <= 0:
        return D0
    # Local navigation convention: positive Z/up, therefore descent is negative.
    return -min(descent, flare) if altitude <= flare_alt else -descent


# ---------- DroneCAN classic-CAN transport helpers ----------

def dronecan_crc16_ccitt_false(data: bytes, initial: int = 0xFFFF) -> int:
    crc = initial & 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def dronecan_broadcast_id(priority: int, data_type_id: int, source_node_id: int) -> int:
    if not 0 <= priority <= 31:
        raise DroneControlError("DroneCAN priority must be 0..31")
    if not 0 <= data_type_id <= 65535:
        raise DroneControlError("DroneCAN message type id must be 0..65535")
    if not 1 <= source_node_id <= 127:
        raise DroneControlError("DroneCAN source node id must be 1..127")
    return (priority << 24) | (data_type_id << 8) | source_node_id


def dronecan_single_frame(priority: int, data_type_id: int, source_node_id: int,
                          transfer_id: int, payload: bytes) -> dict[str, object]:
    if not 0 <= transfer_id <= 31:
        raise DroneControlError("DroneCAN transfer id must be 0..31")
    if len(payload) > 7:
        raise DroneControlError("DroneCAN single-frame payload must not exceed 7 bytes")
    tail = 0xC0 | transfer_id  # start=1, end=1, toggle=0, transfer-id=5 bits
    data = payload + bytes([tail])
    return {"can_id": dronecan_broadcast_id(priority, data_type_id, source_node_id),
            "data_hex": data.hex(), "transfer_id": transfer_id}


def dronecan_multi_frame(priority: int, data_type_id: int, source_node_id: int,
                         transfer_id: int, data_type_signature_le: bytes, payload: bytes) -> list[dict[str, object]]:
    if not 0 <= transfer_id <= 31:
        raise DroneControlError("DroneCAN transfer id must be 0..31")
    if len(data_type_signature_le) != 8:
        raise DroneControlError("DroneCAN data type signature must be exactly 8 little-endian bytes")
    if len(payload) <= 7:
        return [dronecan_single_frame(priority, data_type_id, source_node_id, transfer_id, payload)]
    can_id = dronecan_broadcast_id(priority, data_type_id, source_node_id)
    crc = dronecan_crc16_ccitt_false(data_type_signature_le + payload)
    stream = crc.to_bytes(2, "little") + payload
    frames: list[dict[str, object]] = []
    toggle = 0
    for offset in range(0, len(stream), 7):
        chunk = stream[offset:offset + 7]
        first = offset == 0
        last = offset + len(chunk) >= len(stream)
        tail = (0x80 if first else 0) | (0x40 if last else 0) | (0x20 if toggle else 0) | transfer_id
        frames.append({"can_id": can_id, "data_hex": (chunk + bytes([tail])).hex(), "transfer_id": transfer_id, "index": len(frames)})
        toggle ^= 1
    return frames


def dronecan_single_frame_decode(can_id: int, data: bytes) -> dict[str, object]:
    if not 0 <= can_id < (1 << 29):
        raise DroneControlError("DroneCAN CAN id must fit 29 bits")
    if not 1 <= len(data) <= 8:
        raise DroneControlError("DroneCAN classic CAN data length must be 1..8")
    tail = data[-1]
    if tail & 0xE0 != 0xC0:
        raise DroneControlError("DroneCAN frame is not a single-frame transfer")
    return {
        "priority": (can_id >> 24) & 0x1F,
        "data_type_id": (can_id >> 8) & 0xFFFF,
        "source_node_id": can_id & 0x7F,
        "transfer_id": tail & 0x1F,
        "payload_hex": data[:-1].hex(),
    }


# ---------- MAVLink 2 framing/signing ----------

MAVLINK2_MAGIC = 0xFD
MAVLINK2_SIGNED_FLAG = 0x01


def mavlink_x25_crc(data: bytes, initial: int = 0xFFFF) -> int:
    crc = initial & 0xFFFF
    for byte in data:
        tmp = byte ^ (crc & 0xFF)
        tmp ^= (tmp << 4) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc


def mavlink2_encode(msg_id: int, crc_extra: int, payload: bytes, sequence: int,
                    system_id: int, component_id: int, incompat_flags: int = 0,
                    compat_flags: int = 0) -> bytes:
    if not 0 <= msg_id <= 0xFFFFFF:
        raise DroneControlError("MAVLink message id must be 0..16777215")
    if not 0 <= crc_extra <= 255:
        raise DroneControlError("MAVLink CRC_EXTRA must be 0..255")
    if len(payload) > 255:
        raise DroneControlError("MAVLink payload must not exceed 255 bytes")
    for name, value, low, high in (
        ("sequence", sequence, 0, 255), ("system_id", system_id, 1, 255),
        ("component_id", component_id, 1, 255), ("incompat_flags", incompat_flags, 0, 255),
        ("compat_flags", compat_flags, 0, 255),
    ):
        if not low <= int(value) <= high:
            raise DroneControlError(f"MAVLink {name} outside {low}..{high}")
    header = bytes([
        len(payload), incompat_flags, compat_flags, sequence, system_id, component_id,
        msg_id & 0xFF, (msg_id >> 8) & 0xFF, (msg_id >> 16) & 0xFF,
    ])
    crc = mavlink_x25_crc(header + payload + bytes([crc_extra]))
    return bytes([MAVLINK2_MAGIC]) + header + payload + struct.pack("<H", crc)


def mavlink2_decode(frame: bytes, crc_extra: int) -> dict[str, object]:
    if len(frame) < 12 or frame[0] != MAVLINK2_MAGIC:
        raise DroneControlError("invalid MAVLink 2 frame")
    payload_len = frame[1]
    signed = bool(frame[2] & MAVLINK2_SIGNED_FLAG)
    expected_len = 12 + payload_len + (13 if signed else 0)
    if len(frame) != expected_len:
        raise DroneControlError(f"MAVLink 2 frame length mismatch: expected {expected_len}, got {len(frame)}")
    if not 0 <= crc_extra <= 255:
        raise DroneControlError("MAVLink CRC_EXTRA must be 0..255")
    body_end = 10 + payload_len
    expected_crc = struct.unpack("<H", frame[body_end:body_end + 2])[0]
    actual_crc = mavlink_x25_crc(frame[1:body_end] + bytes([crc_extra]))
    if expected_crc != actual_crc:
        raise DroneControlError("MAVLink 2 checksum mismatch")
    msg_id = frame[7] | (frame[8] << 8) | (frame[9] << 16)
    result: dict[str, object] = {
        "payload_len": payload_len,
        "incompat_flags": frame[2], "compat_flags": frame[3], "sequence": frame[4],
        "system_id": frame[5], "component_id": frame[6], "message_id": msg_id,
        "payload_hex": frame[10:body_end].hex(), "signed": signed,
    }
    if signed:
        sig = frame[body_end + 2:]
        result.update({"link_id": sig[0], "timestamp": int.from_bytes(sig[1:7], "little"), "signature_hex": sig[7:].hex()})
    return result


def mavlink2_encode_signed(msg_id: int, crc_extra: int, payload: bytes, sequence: int,
                           system_id: int, component_id: int, secret_key: bytes,
                           link_id: int, timestamp: int, compat_flags: int = 0) -> bytes:
    if len(secret_key) != 32:
        raise DroneControlError("MAVLink signing key must be exactly 32 bytes")
    if not 0 <= link_id <= 255 or not 0 <= timestamp < (1 << 48):
        raise DroneControlError("invalid MAVLink signing metadata")
    base = mavlink2_encode(msg_id, crc_extra, payload, sequence, system_id, component_id,
                           MAVLINK2_SIGNED_FLAG, compat_flags)
    prefix = bytes([link_id]) + timestamp.to_bytes(6, "little")
    digest = hashlib.sha256(secret_key + base + prefix).digest()[:6]
    return base + prefix + digest


def mavlink2_verify_signed(frame: bytes, crc_extra: int, secret_key: bytes,
                           minimum_timestamp: int = 0) -> dict[str, object]:
    info = mavlink2_decode(frame, crc_extra)
    if not info["signed"]:
        raise DroneControlError("MAVLink frame is unsigned")
    if len(secret_key) != 32:
        raise DroneControlError("MAVLink signing key must be exactly 32 bytes")
    timestamp = int(info["timestamp"])
    if timestamp < minimum_timestamp:
        raise DroneControlError("MAVLink signing timestamp is older than accepted stream state")
    unsigned_part = frame[:-13]
    prefix = frame[-13:-6]
    expected = hashlib.sha256(secret_key + unsigned_part + prefix).digest()[:6]
    if not hmac.compare_digest(expected, frame[-6:]):
        raise DroneControlError("MAVLink signature mismatch")
    info["signature_valid"] = True
    return info


def mavlink_signing_timestamp(now_unix: float | None = None) -> int:
    if now_unix is None:
        now_unix = time.time()
    value = int(max(0.0, now_unix - 1420070400.0) * 100000.0)
    return min(value, (1 << 48) - 1)


def mavlink_heartbeat_payload(vehicle_type: int, autopilot: int, base_mode: int,
                              custom_mode: int, system_status: int, mavlink_version: int = 3) -> bytes:
    for name, value, maximum in (
        ("vehicle_type", vehicle_type, 255), ("autopilot", autopilot, 255),
        ("base_mode", base_mode, 255), ("system_status", system_status, 255),
        ("mavlink_version", mavlink_version, 255),
    ):
        if not 0 <= value <= maximum:
            raise DroneControlError(f"{name} outside 0..{maximum}")
    if not 0 <= custom_mode <= 0xFFFFFFFF:
        raise DroneControlError("custom_mode outside uint32")
    return struct.pack("<IBBBBB", custom_mode, vehicle_type, autopilot, base_mode, system_status, mavlink_version)


def mavlink_heartbeat(sequence: int, system_id: int, component_id: int, vehicle_type: int,
                      autopilot: int, base_mode: int, custom_mode: int, system_status: int) -> bytes:
    # HEARTBEAT message id 0, CRC_EXTRA 50 in the MAVLink common/minimal dialect.
    payload = mavlink_heartbeat_payload(vehicle_type, autopilot, base_mode, custom_mode, system_status)
    return mavlink2_encode(0, 50, payload, sequence, system_id, component_id)


MAVLINK_COMMON_CRC_EXTRAS: dict[int, int] = {
    0: 50, 31: 246, 32: 185, 33: 104, 76: 152, 77: 143,
    82: 49, 83: 22, 84: 143, 85: 140, 86: 5, 87: 150,
}


def _u8(name: str, value: int, *, allow_zero: bool = True) -> int:
    low = 0 if allow_zero else 1
    if not low <= int(value) <= 255:
        raise DroneControlError(f"{name} outside {low}..255")
    return int(value)


def _u16(name: str, value: int) -> int:
    if not 0 <= int(value) <= 0xFFFF:
        raise DroneControlError(f"{name} outside uint16")
    return int(value)


def _u32(name: str, value: int) -> int:
    if not 0 <= int(value) <= 0xFFFFFFFF:
        raise DroneControlError(f"{name} outside uint32")
    return int(value)


def _f32(name: str, value: Decimal) -> float:
    value = _finite(name, value)
    f = float(value)
    if not math.isfinite(f):
        raise DroneControlError(f"{name} cannot be represented as float32")
    try:
        packed = struct.pack("<f", f)
    except (OverflowError, struct.error) as exc:
        raise DroneControlError(f"{name} cannot be represented as float32") from exc
    f32 = struct.unpack("<f", packed)[0]
    if not math.isfinite(f32):
        raise DroneControlError(f"{name} cannot be represented as float32")
    return f32


def mavlink_set_attitude_target(sequence: int, system_id: int, component_id: int,
                                target_system: int, target_component: int, type_mask: int,
                                quaternion: Iterable[Decimal], body_rates: Iterable[Decimal],
                                thrust: Decimal, time_boot_ms: int = 0) -> bytes:
    q = _vector4(quaternion, "attitude quaternion")
    rates = _vector3(body_rates, "body rates")
    thrust_f = _f32("thrust", thrust)
    if not -1.0 <= thrust_f <= 1.0:
        raise DroneControlError("thrust must be in -1..1")
    payload = struct.pack(
        "<I4f4fBBB",
        _u32("time_boot_ms", time_boot_ms),
        *(_f32("quaternion", v) for v in q),
        *(_f32("body_rate", v) for v in rates),
        thrust_f,
        _u8("target_system", target_system, allow_zero=False),
        _u8("target_component", target_component),
        _u8("type_mask", type_mask),
    )
    return mavlink2_encode(82, MAVLINK_COMMON_CRC_EXTRAS[82], payload, sequence, system_id, component_id)


def mavlink_set_position_target_local_ned(
    sequence: int, system_id: int, component_id: int, target_system: int, target_component: int,
    coordinate_frame: int, type_mask: int, position_ned: Iterable[Decimal],
    velocity_ned: Iterable[Decimal], acceleration_ned: Iterable[Decimal],
    yaw: Decimal, yaw_rate: Decimal, time_boot_ms: int = 0,
) -> bytes:
    position = _vector3(position_ned, "position_ned")
    velocity = _vector3(velocity_ned, "velocity_ned")
    accel = _vector3(acceleration_ned, "acceleration_ned")
    if int(coordinate_frame) not in {1, 7, 8, 9}:
        raise DroneControlError("local NED coordinate_frame must be 1, 7, 8, or 9")
    payload = struct.pack(
        "<I11fHBBB",
        _u32("time_boot_ms", time_boot_ms),
        *(_f32("position", v) for v in position),
        *(_f32("velocity", v) for v in velocity),
        *(_f32("acceleration", v) for v in accel),
        _f32("yaw", yaw), _f32("yaw_rate", yaw_rate),
        _u16("type_mask", type_mask),
        _u8("target_system", target_system, allow_zero=False),
        _u8("target_component", target_component),
        _u8("coordinate_frame", coordinate_frame),
    )
    return mavlink2_encode(84, MAVLINK_COMMON_CRC_EXTRAS[84], payload, sequence, system_id, component_id)


def mavlink_command_long(sequence: int, system_id: int, component_id: int,
                         target_system: int, target_component: int, command: int, confirmation: int,
                         params: Iterable[Decimal]) -> bytes:
    values = tuple(params)
    if len(values) != 7:
        raise DroneControlError("COMMAND_LONG requires exactly seven parameters")
    payload = struct.pack(
        "<7fHBBB",
        *(_f32(f"param{i+1}", v) for i, v in enumerate(values)),
        _u16("command", command),
        _u8("target_system", target_system, allow_zero=False),
        _u8("target_component", target_component),
        _u8("confirmation", confirmation),
    )
    return mavlink2_encode(76, MAVLINK_COMMON_CRC_EXTRAS[76], payload, sequence, system_id, component_id)


def mavlink_common_decode(frame: bytes) -> dict[str, object]:
    if len(frame) < 10 or frame[0] != MAVLINK2_MAGIC:
        raise DroneControlError("invalid MAVLink 2 frame")
    msg_id = frame[7] | (frame[8] << 8) | (frame[9] << 16)
    crc_extra = MAVLINK_COMMON_CRC_EXTRAS.get(msg_id)
    if crc_extra is None:
        raise DroneControlError(f"unsupported MAVLink common message id {msg_id}")
    info = mavlink2_decode(frame, crc_extra)
    payload = bytes.fromhex(str(info["payload_hex"]))
    fields: dict[str, object] = {}
    if msg_id == 0 and len(payload) >= 9:
        custom, typ, autopilot, base, status, version = struct.unpack_from("<IBBBBB", payload)
        fields = {"custom_mode": custom, "type": typ, "autopilot": autopilot, "base_mode": base, "system_status": status, "mavlink_version": version}
    elif msg_id == 31 and len(payload) >= 32:
        vals = struct.unpack_from("<I7f", payload)
        fields = {"time_boot_ms": vals[0], "q": list(vals[1:5]), "rollspeed": vals[5], "pitchspeed": vals[6], "yawspeed": vals[7]}
    elif msg_id == 32 and len(payload) >= 28:
        vals = struct.unpack_from("<I6f", payload)
        fields = {"time_boot_ms": vals[0], "x": vals[1], "y": vals[2], "z": vals[3], "vx": vals[4], "vy": vals[5], "vz": vals[6]}
    elif msg_id == 33 and len(payload) >= 28:
        vals = struct.unpack_from("<IiiiihhhH", payload)
        fields = {"time_boot_ms": vals[0], "lat_e7": vals[1], "lon_e7": vals[2], "alt_mm": vals[3], "relative_alt_mm": vals[4], "vx_cms": vals[5], "vy_cms": vals[6], "vz_cms": vals[7], "heading_cdeg": vals[8]}
    elif msg_id == 77 and len(payload) >= 3:
        command, result = struct.unpack_from("<HB", payload)
        fields = {"command": command, "result": result}
    elif msg_id == 83 and len(payload) >= 37:
        vals = struct.unpack_from("<I8fB", payload)
        fields = {"time_boot_ms": vals[0], "q": list(vals[1:5]), "body_roll_rate": vals[5], "body_pitch_rate": vals[6], "body_yaw_rate": vals[7], "thrust": vals[8], "type_mask": vals[9]}
    elif msg_id == 85 and len(payload) >= 51:
        vals = struct.unpack_from("<I11fHB", payload)
        fields = {"time_boot_ms": vals[0], "x": vals[1], "y": vals[2], "z": vals[3], "vx": vals[4], "vy": vals[5], "vz": vals[6], "afx": vals[7], "afy": vals[8], "afz": vals[9], "yaw": vals[10], "yaw_rate": vals[11], "type_mask": vals[12], "coordinate_frame": vals[13]}
    info["fields"] = fields
    return info


@dataclass(slots=True)
class MAVLinkStreamParser:
    """Incremental MAVLink 2 stream parser for UART/UDP companion links."""

    buffer: bytearray = field(default_factory=bytearray)
    dropped_bytes: int = 0
    bad_frames: int = 0

    def feed(self, data: bytes) -> list[dict[str, object]]:
        self.buffer.extend(data)
        out: list[dict[str, object]] = []
        while True:
            while self.buffer and self.buffer[0] != MAVLINK2_MAGIC:
                del self.buffer[0]
                self.dropped_bytes += 1
            if len(self.buffer) < 12:
                break
            payload_len = self.buffer[1]
            signed = bool(self.buffer[2] & MAVLINK2_SIGNED_FLAG)
            total = 12 + payload_len + (13 if signed else 0)
            if len(self.buffer) < total:
                break
            frame = bytes(self.buffer[:total])
            msg_id = frame[7] | (frame[8] << 8) | (frame[9] << 16)
            crc_extra = MAVLINK_COMMON_CRC_EXTRAS.get(msg_id)
            if crc_extra is None:
                out.append({"message_id": msg_id, "frame_hex": frame.hex(), "known": False})
                del self.buffer[:total]
                continue
            try:
                info = mavlink_common_decode(frame)
            except DroneControlError:
                del self.buffer[0]
                self.bad_frames += 1
                continue
            info["frame_hex"] = frame.hex()
            info["known"] = True
            out.append(info)
            del self.buffer[:total]
        return out


def dshot_frame(throttle: Decimal, telemetry: bool = False) -> int:
    """Encode a DShot throttle word; waveform generation remains hardware-specific."""
    value = _finite("throttle", throttle)
    if not D0 <= value <= D1:
        raise DroneControlError("DShot throttle must be in 0..1")
    throttle_word = 0 if value == 0 else 48 + int((Decimal(1999) * value).to_integral_value())
    throttle_word = min(2047, throttle_word)
    packet = (throttle_word << 1) | (1 if telemetry else 0)
    csum_data = packet
    checksum = 0
    for _ in range(3):
        checksum ^= csum_data
        csum_data >>= 4
    return ((packet << 4) | (checksum & 0xF)) & 0xFFFF


def pwm_esc_duty(throttle: Decimal, minimum_us: Decimal, maximum_us: Decimal, period_us: Decimal) -> Decimal:
    throttle = _finite("throttle", throttle)
    minimum_us = _positive("minimum_us", minimum_us)
    maximum_us = _positive("maximum_us", maximum_us)
    period_us = _positive("period_us", period_us)
    if not D0 <= throttle <= D1:
        raise DroneControlError("ESC throttle must be in 0..1")
    if maximum_us <= minimum_us or period_us < maximum_us:
        raise DroneControlError("ESC PWM timing is invalid")
    pulse = minimum_us + (maximum_us - minimum_us) * throttle
    return pulse / period_us


def flight_state_json(manager: FlightManager) -> str:
    return json.dumps({
        "state": manager.state,
        "mode": manager.mode,
        "flight_allowed": manager.flight_allowed(),
        "control_allowed": manager.control_allowed(),
        "reason": manager.last_reason,
        "safety_tripped": manager.safety.tripped,
        "health": {
            "estimator": manager.estimator_healthy,
            "position": manager.position_healthy,
            "battery_fraction": str(manager.battery_fraction),
            "rc_link": manager.rc_link,
            "data_link": manager.data_link,
            "home_set": manager.home_set,
        },
    }, separators=(",", ":"))


@dataclass(slots=True)
class Trajectory3D:
    """Jerk-limited NED trajectory generator for companion/offboard control."""
    axes: tuple[JerkLimitedProfile, JerkLimitedProfile, JerkLimitedProfile]

    @classmethod
    def create(cls, position: Iterable[Decimal], target: Iterable[Decimal],
               max_velocity: Decimal, max_acceleration: Decimal, max_jerk: Decimal) -> "Trajectory3D":
        p = _vector3(position, "trajectory position")
        t = _vector3(target, "trajectory target")
        max_velocity = _positive("max_velocity", max_velocity)
        max_acceleration = _positive("max_acceleration", max_acceleration)
        max_jerk = _positive("max_jerk", max_jerk)
        return cls(tuple(JerkLimitedProfile(p[i], D0, D0, t[i], max_velocity, max_acceleration, max_jerk) for i in range(3)))  # type: ignore[arg-type]

    @classmethod
    def create_per_axis(cls, position: Iterable[Decimal], target: Iterable[Decimal],
                        max_velocity: Iterable[Decimal], max_acceleration: Iterable[Decimal],
                        max_jerk: Iterable[Decimal]) -> "Trajectory3D":
        p = _vector3(position, "trajectory position"); t = _vector3(target, "trajectory target")
        mv = tuple(_positive(f"max_velocity[{i}]", v) for i,v in enumerate(_vector3(max_velocity,"max_velocity")))
        ma = tuple(_positive(f"max_acceleration[{i}]", v) for i,v in enumerate(_vector3(max_acceleration,"max_acceleration")))
        mj = tuple(_positive(f"max_jerk[{i}]", v) for i,v in enumerate(_vector3(max_jerk,"max_jerk")))
        return cls(tuple(JerkLimitedProfile(p[i], D0, D0, t[i], mv[i], ma[i], mj[i]) for i in range(3)))  # type: ignore[arg-type]

    def retarget(self, target: Iterable[Decimal]) -> None:
        t = _vector3(target, "trajectory target")
        for axis, value in zip(self.axes, t):
            axis.retarget(value)

    def step(self, dt_seconds: Decimal) -> dict[str, tuple[Decimal, Decimal, Decimal]]:
        dt = _positive("dt_seconds", dt_seconds)
        position = []
        velocity = []
        acceleration = []
        for axis in self.axes:
            position.append(axis.step(dt))
            velocity.append(axis.velocity)
            acceleration.append(axis.acceleration)
        return {"position": tuple(position), "velocity": tuple(velocity), "acceleration": tuple(acceleration)}

    def done(self) -> bool:
        return all(axis.done() for axis in self.axes)


def _solve_linear(matrix: list[list[Decimal]], rhs: list[Decimal]) -> list[Decimal]:
    n = len(rhs)
    a = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) <= Decimal("1e-18"):
            raise DroneControlError("control allocation matrix is singular")
        a[col], a[pivot] = a[pivot], a[col]
        div = a[col][col]
        a[col] = [v / div for v in a[col]]
        for row in range(n):
            if row == col:
                continue
            factor = a[row][col]
            if factor == 0:
                continue
            a[row] = [x - factor*y for x, y in zip(a[row], a[col])]
    return [a[i][-1] for i in range(n)]


@dataclass(slots=True)
class ControlAllocator:
    """Least-squares multirotor allocator with runtime motor disable support.

    Each motor row is [thrust, roll, pitch, yaw] contribution per unit command.
    The allocator solves min ||A^T u - demand|| and clamps commands into the
    configured range. It is intended for companion/HIL work and control-allocation
    research; final airframe validation remains hardware-specific.
    """
    matrix: tuple[tuple[Decimal, Decimal, Decimal, Decimal], ...]
    minimum: Decimal = D0
    maximum: Decimal = D1
    disabled: set[int] = field(default_factory=set)
    _projection_active: tuple[int, ...] = field(default_factory=tuple, init=False, repr=False)
    _projection: tuple[tuple[Decimal, Decimal, Decimal, Decimal], ...] = field(default_factory=tuple, init=False, repr=False)

    def __post_init__(self) -> None:
        if len(self.matrix) < 4:
            raise DroneControlError("control allocator requires at least four actuators")
        self.minimum = _finite("allocator minimum", self.minimum)
        self.maximum = _finite("allocator maximum", self.maximum)
        if self.maximum <= self.minimum:
            raise DroneControlError("allocator maximum must be greater than minimum")
        for i, row in enumerate(self.matrix):
            if len(row) != 4:
                raise DroneControlError(f"allocator row {i} must have four coefficients")
            for j, value in enumerate(row):
                _finite(f"allocator[{i}][{j}]", value)

    @classmethod
    def quad_x(cls, minimum: Decimal = D0, maximum: Decimal = D1) -> "ControlAllocator":
        return cls((
            (D1, D1, D1, Decimal(-1)),
            (D1, Decimal(-1), D1, D1),
            (D1, Decimal(-1), Decimal(-1), Decimal(-1)),
            (D1, D1, Decimal(-1), D1),
        ), minimum, maximum)

    def set_disabled(self, indices: Iterable[int]) -> None:
        values = {int(v) for v in indices}
        if any(v < 0 or v >= len(self.matrix) for v in values):
            raise DroneControlError("disabled actuator index outside allocation matrix")
        self.disabled = values
        self._projection_active = ()
        self._projection = ()

    def _prepare_projection(self) -> tuple[tuple[int, ...], tuple[tuple[Decimal, Decimal, Decimal, Decimal], ...]]:
        active = tuple(i for i in range(len(self.matrix)) if i not in self.disabled)
        if len(active) < 4:
            raise DroneControlError("fewer than four active actuators cannot satisfy four-axis demand")
        if active == self._projection_active and self._projection:
            return active, self._projection
        gram = [[D0 for _ in range(4)] for _ in range(4)]
        for idx in active:
            row = self.matrix[idx]
            for r in range(4):
                rr = row[r]
                for c in range(4):
                    gram[r][c] += rr * row[c]
        # Compute G^-1 once per actuator topology. Each active actuator command is
        # row_i * G^-1 * demand; the hot loop is then only four multiply-adds.
        inv_cols = []
        for col in range(4):
            rhs = [D1 if i == col else D0 for i in range(4)]
            inv_cols.append(_solve_linear(gram, rhs))
        inv = [[inv_cols[c][r] for c in range(4)] for r in range(4)]
        projection = []
        for idx in active:
            row = self.matrix[idx]
            projection.append(tuple(sum((row[j] * inv[j][k] for j in range(4)), D0) for k in range(4)))
        self._projection_active = active
        self._projection = tuple(projection)
        return active, self._projection

    def allocate(self, demand: Iterable[Decimal]) -> list[Decimal]:
        d = tuple(demand)
        if len(d) != 4:
            raise DroneControlError("control demand must contain thrust, roll, pitch, yaw")
        demand4 = [_finite(f"demand[{i}]", v) for i, v in enumerate(d)]
        active, projection = self._prepare_projection()
        out = [D0 for _ in self.matrix]
        for idx, coeff in zip(active, projection):
            cmd = coeff[0]*demand4[0] + coeff[1]*demand4[1] + coeff[2]*demand4[2] + coeff[3]*demand4[3]
            out[idx] = _clamp(cmd, self.minimum, self.maximum)
        return out

    def allocation_report(self, demand: Iterable[Decimal]) -> dict[str, object]:
        demand4 = tuple(demand)
        if len(demand4) != 4:
            raise DroneControlError("control demand must contain thrust, roll, pitch, yaw")
        requested = [_finite(f"demand[{i}]", v) for i, v in enumerate(demand4)]
        commands = self.allocate(requested)
        achieved = [D0, D0, D0, D0]
        saturated: list[int] = []
        for i, command in enumerate(commands):
            if i in self.disabled:
                continue
            if command == self.minimum or command == self.maximum:
                saturated.append(i)
            row = self.matrix[i]
            for axis in range(4):
                achieved[axis] += row[axis] * command
        residual = [requested[i] - achieved[i] for i in range(4)]
        return {"commands": commands, "requested": requested, "achieved": achieved, "residual": residual, "saturated": saturated, "disabled": sorted(self.disabled)}


@dataclass(slots=True)
class LinkMonitor:
    """Track MAVLink sequence quality and transport latency without flight policy."""
    last_sequence: int | None = None
    received: int = 0
    lost: int = 0
    duplicates: int = 0
    out_of_order: int = 0
    latency_ms_ewma: Decimal = D0
    alpha: Decimal = Decimal("0.2")

    def __post_init__(self) -> None:
        if not D0 < self.alpha <= D1:
            raise DroneControlError("link monitor alpha must be in (0,1]")

    def observe(self, sequence: int, latency_ms: Decimal = D0) -> None:
        if not 0 <= int(sequence) <= 255:
            raise DroneControlError("MAVLink sequence must be 0..255")
        latency = _finite("latency_ms", latency_ms)
        if latency < 0:
            raise DroneControlError("latency_ms must be >= 0")
        seq = int(sequence)
        self.received += 1
        if self.received == 1:
            self.latency_ms_ewma = latency
        else:
            self.latency_ms_ewma = self.alpha * latency + (D1-self.alpha) * self.latency_ms_ewma
        if self.last_sequence is None:
            self.last_sequence = seq
            return
        delta = (seq - self.last_sequence) & 0xFF
        if delta == 0:
            self.duplicates += 1
        elif delta < 128:
            if delta > 1:
                self.lost += delta - 1
            self.last_sequence = seq
        else:
            self.out_of_order += 1

    def stats(self) -> dict[str, object]:
        expected = self.received + self.lost
        return {
            "received": self.received,
            "lost": self.lost,
            "duplicates": self.duplicates,
            "out_of_order": self.out_of_order,
            "loss_fraction": str((Decimal(self.lost)/Decimal(expected)) if expected else D0),
            "latency_ms_ewma": str(self.latency_ms_ewma),
        }
