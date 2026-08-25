from __future__ import annotations

from pathlib import Path


def replace_once(path: str, label: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one patch anchor in {path}, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"patched: {label}")


py = "saga/stdlib/drone_control.py"
marker = "\ndef _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:\n"
helpers = (
    "\ndef _latitude(name: str, value: Decimal) -> Decimal:\n"
    "    value = _finite(name, value)\n"
    "    if not Decimal(\"-90\") <= value <= Decimal(\"90\"):\n"
    "        raise DroneControlError(f\"{name} must be in -90..90\")\n"
    "    return value\n\n\n"
    "def _longitude(name: str, value: Decimal) -> Decimal:\n"
    "    value = _finite(name, value)\n"
    "    if not Decimal(\"-180\") <= value <= Decimal(\"180\"):\n"
    "        raise DroneControlError(f\"{name} must be in -180..180\")\n"
    "    return value\n\n\n"
    "def _wrap_longitude(value: Decimal) -> Decimal:\n"
    "    value = _finite(\"longitude\", value)\n"
    "    wrapped = (float(value) + 180.0) % 360.0 - 180.0\n"
    "    if not math.isfinite(wrapped):\n"
    "        raise DroneControlError(\"longitude projection is not finite\")\n"
    "    return Decimal(str(wrapped))\n\n"
)
replace_once(py, "coordinate helpers", marker, helpers + marker)
replace_once(
    py,
    "geofence constructor coordinates",
    '        for name in ("home_lat_deg", "home_lon_deg", "min_alt_m", "max_alt_m"):\n            _finite(name, getattr(self, name))\n',
    '        self.home_lat_deg = _latitude("home_lat_deg", self.home_lat_deg)\n        self.home_lon_deg = _longitude("home_lon_deg", self.home_lon_deg)\n        self.min_alt_m = _finite("min_alt_m", self.min_alt_m)\n        self.max_alt_m = _finite("max_alt_m", self.max_alt_m)\n',
)
replace_once(
    py,
    "remove duplicate geofence coordinate checks",
    '        if not Decimal("-90") <= self.home_lat_deg <= Decimal("90"):\n            raise DroneControlError("home latitude outside -90..90")\n        if not Decimal("-180") <= self.home_lon_deg <= Decimal("180"):\n            raise DroneControlError("home longitude outside -180..180")\n',
    "",
)
replace_once(
    py,
    "geofence runtime coordinate validation",
    '        lat_deg = _finite("latitude", lat_deg); lon_deg = _finite("longitude", lon_deg)\n',
    '        lat_deg = _latitude("latitude", lat_deg); lon_deg = _longitude("longitude", lon_deg)\n',
)
replace_once(
    py,
    "geofence prediction input validation",
    '        horizon = _positive("horizon_seconds", horizon_seconds)\n        north = _finite("north_mps", north_mps) * horizon\n',
    '        horizon = _positive("horizon_seconds", horizon_seconds)\n        lat_deg = _latitude("latitude", lat_deg)\n        lon_deg = _longitude("longitude", lon_deg)\n        alt_m = _finite("altitude", alt_m)\n        north = _finite("north_mps", north_mps) * horizon\n',
)
replace_once(
    py,
    "geofence projected coordinate handling",
    '        return not self.contains(lat_deg + lat_delta, lon_deg + lon_delta, alt_m + up)\n',
    '        projected_lat = lat_deg + lat_delta\n        if not projected_lat.is_finite() or not Decimal("-90") <= projected_lat <= Decimal("90"):\n            return True\n        projected_lon_raw = lon_deg + lon_delta\n        if not projected_lon_raw.is_finite():\n            return True\n        projected_lon = _wrap_longitude(projected_lon_raw)\n        return not self.contains(projected_lat, projected_lon, alt_m + up)\n',
)
replace_once(
    py,
    "mission waypoint coordinates",
    '        self.waypoints.append(Waypoint(_finite("lat", lat_deg), _finite("lon", lon_deg),\n                                       _finite("alt", alt_m), acceptance_radius_m, hold_seconds))\n',
    '        self.waypoints.append(Waypoint(_latitude("lat", lat_deg), _longitude("lon", lon_deg),\n                                       _finite("alt", alt_m), acceptance_radius_m, hold_seconds))\n',
)
replace_once(
    py,
    "battery telemetry fail closed",
    '        battery_fraction = _finite("battery_fraction", battery_fraction)\n        self.estimator_healthy = bool(estimator_healthy)\n        self.position_healthy = bool(position_healthy)\n        self.battery_fraction = _clamp(battery_fraction, D0, D1)\n',
    '        battery_fraction = _finite("battery_fraction", battery_fraction)\n        if not D0 <= battery_fraction <= D1:\n            raise DroneControlError("battery_fraction must be in 0..1")\n        self.estimator_healthy = bool(estimator_healthy)\n        self.position_healthy = bool(position_healthy)\n        self.battery_fraction = battery_fraction\n',
)
replace_once(
    py,
    "RTL home coordinates",
    '        for name in ("home_lat_deg", "home_lon_deg", "home_alt_m", "return_alt_m"):\n            _finite(name, getattr(self, name))\n        self.acceptance_radius_m = _positive("acceptance_radius_m", self.acceptance_radius_m)\n',
    '        self.home_lat_deg = _latitude("home_lat_deg", self.home_lat_deg)\n        self.home_lon_deg = _longitude("home_lon_deg", self.home_lon_deg)\n        self.home_alt_m = _finite("home_alt_m", self.home_alt_m)\n        self.return_alt_m = _finite("return_alt_m", self.return_alt_m)\n        self.acceptance_radius_m = _positive("acceptance_radius_m", self.acceptance_radius_m)\n',
)
replace_once(
    py,
    "RTL current coordinates",
    '    def target(self, lat_deg: Decimal, lon_deg: Decimal, alt_m: Decimal) -> dict[str, object]:\n        distance = Geofence(self.home_lat_deg, self.home_lon_deg, Decimal("1"),\n',
    '    def target(self, lat_deg: Decimal, lon_deg: Decimal, alt_m: Decimal) -> dict[str, object]:\n        lat_deg = _latitude("lat_deg", lat_deg)\n        lon_deg = _longitude("lon_deg", lon_deg)\n        distance = Geofence(self.home_lat_deg, self.home_lon_deg, Decimal("1"),\n',
)
replace_once(
    py,
    "MAVLink float32 range",
    'def _f32(name: str, value: Decimal) -> float:\n    value = _finite(name, value)\n    f = float(value)\n    if not math.isfinite(f):\n        raise DroneControlError(f"{name} cannot be represented as float32")\n    return f\n',
    'def _f32(name: str, value: Decimal) -> float:\n    value = _finite(name, value)\n    f = float(value)\n    if not math.isfinite(f):\n        raise DroneControlError(f"{name} cannot be represented as float32")\n    try:\n        packed = struct.pack("<f", f)\n    except (OverflowError, struct.error) as exc:\n        raise DroneControlError(f"{name} cannot be represented as float32") from exc\n    f32 = struct.unpack("<f", packed)[0]\n    if not math.isfinite(f32):\n        raise DroneControlError(f"{name} cannot be represented as float32")\n    return f32\n',
)
replace_once(
    py,
    "link sequence baseline",
    '        if self.last_sequence is not None:\n            delta = (seq - self.last_sequence) & 0xFF\n            if delta == 0:\n                self.duplicates += 1\n            elif 1 < delta < 128:\n                self.lost += delta - 1\n            elif delta >= 128:\n                self.out_of_order += 1\n        self.last_sequence = seq\n',
    '        if self.last_sequence is None:\n            self.last_sequence = seq\n            return\n        delta = (seq - self.last_sequence) & 0xFF\n        if delta == 0:\n            self.duplicates += 1\n        elif delta < 128:\n            if delta > 1:\n                self.lost += delta - 1\n            self.last_sequence = seq\n        else:\n            self.out_of_order += 1\n',
)

go = "implementations/go/cmd/saga-go/drone_control.go"
go_marker = 'func droneList3(v Value, name string) ([3]float64, error) {\n'
go_helpers = (
    'func validDroneLatitude(v float64) bool  { return finiteFloat(v) && v >= -90 && v <= 90 }\n'
    'func validDroneLongitude(v float64) bool { return finiteFloat(v) && v >= -180 && v <= 180 }\n'
    'func validateDroneCoordinate(lat, lon float64) error {\n'
    '\tif !validDroneLatitude(lat) {\n\t\treturn fmt.Errorf("latitude must be in -90..90")\n\t}\n'
    '\tif !validDroneLongitude(lon) {\n\t\treturn fmt.Errorf("longitude must be in -180..180")\n\t}\n'
    '\treturn nil\n}\n'
    'func wrapDroneLongitude(v float64) float64 {\n\treturn math.Mod(math.Mod(v+180, 360)+360, 360) - 180\n}\n'
)
replace_once(go, "Go coordinate helpers", go_marker, go_helpers + go_marker)
replace_once(
    go,
    "Go projected coordinate handling",
    '\tlat2 := lat + (north*horizon/r)*180/math.Pi\n\tcl := math.Max(1e-9, math.Abs(math.Cos(lat*math.Pi/180)))\n\tlon2 := lon + (east*horizon/r)*180/math.Pi/cl\n\treturn !g.contains(lat2, lon2, alt+up*horizon)\n',
    '\tlat2 := lat + (north*horizon/r)*180/math.Pi\n\tif !validDroneLatitude(lat2) {\n\t\treturn true\n\t}\n\tcl := math.Max(1e-9, math.Abs(math.Cos(lat*math.Pi/180)))\n\tlon2 := wrapDroneLongitude(lon + (east*horizon/r)*180/math.Pi/cl)\n\tif !finiteFloat(lon2) {\n\t\treturn true\n\t}\n\treturn !g.contains(lat2, lon2, alt+up*horizon)\n',
)
replace_once(
    go,
    "Go RTL direct validation",
    '\tif r.Acceptance <= 0 || r.ReturnAlt < r.HomeAlt {\n\t\treturn "", fmt.Errorf("invalid RTL planner")\n\t}\n\tg := droneGeofence{HomeLat: r.HomeLat, HomeLon: r.HomeLon, Radius: 1, MinAlt: r.HomeAlt - 100000, MaxAlt: r.HomeAlt + 100000}\n',
    '\tif r.Acceptance <= 0 || r.ReturnAlt < r.HomeAlt {\n\t\treturn "", fmt.Errorf("invalid RTL planner")\n\t}\n\tif err := validateDroneCoordinate(r.HomeLat, r.HomeLon); err != nil {\n\t\treturn "", fmt.Errorf("invalid RTL home coordinate: %w", err)\n\t}\n\tif err := validateDroneCoordinate(lat, lon); err != nil {\n\t\treturn "", err\n\t}\n\tif !finiteFloat(alt) {\n\t\treturn "", fmt.Errorf("altitude must be finite")\n\t}\n\tg := droneGeofence{HomeLat: r.HomeLat, HomeLon: r.HomeLon, Radius: 1, MinAlt: r.HomeAlt - 100000, MaxAlt: r.HomeAlt + 100000}\n',
)
replace_once(
    go,
    "Go HEARTBEAT field validation",
    'func mavlinkHeartbeat(seq, sysid, compid, vehicleType, autopilot, baseMode, customMode, status int) ([]byte, error) {\n\tpayload := make([]byte, 9)\n',
    'func mavlinkHeartbeat(seq, sysid, compid, vehicleType, autopilot, baseMode, customMode, status int) ([]byte, error) {\n\tif vehicleType < 0 || vehicleType > 255 || autopilot < 0 || autopilot > 255 || baseMode < 0 || baseMode > 255 || status < 0 || status > 255 || customMode < 0 || uint64(customMode) > math.MaxUint32 {\n\t\treturn nil, fmt.Errorf("invalid MAVLink HEARTBEAT field")\n\t}\n\tpayload := make([]byte, 9)\n',
)
replace_once(
    go,
    "Go geofence home validation",
    '\t\tif v[2] <= 0 || v[3] >= v[4] {\n\t\t\treturn fail(fmt.Errorf("invalid geofence"))\n\t\t}\n\t\treturn &droneGeofence{v[0], v[1], v[2], v[3], v[4]}, nil\n',
    '\t\tif e := validateDroneCoordinate(v[0], v[1]); e != nil {\n\t\t\treturn fail(e)\n\t\t}\n\t\tif v[2] <= 0 || v[3] >= v[4] {\n\t\t\treturn fail(fmt.Errorf("invalid geofence"))\n\t\t}\n\t\treturn &droneGeofence{v[0], v[1], v[2], v[3], v[4]}, nil\n',
)
replace_once(
    go,
    "Go geofence contains validation",
    '\t\tlo, _ := num(2, "lon")\n\t\tal, _ := num(3, "alt")\n\t\treturn g.contains(la, lo, al), nil\n',
    '\t\tlo, e := num(2, "lon")\n\t\tif e != nil { return fail(e) }\n\t\tal, e := num(3, "alt")\n\t\tif e != nil { return fail(e) }\n\t\tif e = validateDroneCoordinate(la, lo); e != nil { return fail(e) }\n\t\treturn g.contains(la, lo, al), nil\n',
)
replace_once(
    go,
    "Go geofence distance validation",
    '\t\tla, _ := num(1, "lat")\n\t\tlo, _ := num(2, "lon")\n\t\treturn machineNumberFromFloat(g.distance(la, lo)), nil\n',
    '\t\tla, e := num(1, "lat")\n\t\tif e != nil { return fail(e) }\n\t\tlo, e := num(2, "lon")\n\t\tif e != nil { return fail(e) }\n\t\tif e = validateDroneCoordinate(la, lo); e != nil { return fail(e) }\n\t\treturn machineNumberFromFloat(g.distance(la, lo)), nil\n',
)
replace_once(
    go,
    "Go geofence prediction validation",
    '\t\tif v[6] <= 0 {\n\t\t\treturn fail(fmt.Errorf("horizon must be > 0"))\n\t\t}\n\t\treturn g.predict(v[0], v[1], v[2], v[3], v[4], v[5], v[6]), nil\n',
    '\t\tif e := validateDroneCoordinate(v[0], v[1]); e != nil {\n\t\t\treturn fail(e)\n\t\t}\n\t\tif v[6] <= 0 {\n\t\t\treturn fail(fmt.Errorf("horizon must be > 0"))\n\t\t}\n\t\treturn g.predict(v[0], v[1], v[2], v[3], v[4], v[5], v[6]), nil\n',
)
replace_once(
    go,
    "Go mission waypoint validation",
    '\t\tif v[3] <= 0 || v[4] < 0 {\n\t\t\treturn fail(fmt.Errorf("invalid waypoint radius/hold"))\n\t\t}\n\t\tm.Waypoints = append(m.Waypoints, droneWaypoint{v[0], v[1], v[2], v[3], v[4]})\n',
    '\t\tif e := validateDroneCoordinate(v[0], v[1]); e != nil {\n\t\t\treturn fail(e)\n\t\t}\n\t\tif v[3] <= 0 || v[4] < 0 {\n\t\t\treturn fail(fmt.Errorf("invalid waypoint radius/hold"))\n\t\t}\n\t\tm.Waypoints = append(m.Waypoints, droneWaypoint{v[0], v[1], v[2], v[3], v[4]})\n',
)
replace_once(
    go,
    "Go mission current coordinate validation",
    '\t\tif v[3] <= 0 {\n\t\t\treturn fail(fmt.Errorf("dt must be > 0"))\n\t\t}\n\t\treturn m.update(v[0], v[1], v[2], v[3]), nil\n',
    '\t\tif e := validateDroneCoordinate(v[0], v[1]); e != nil {\n\t\t\treturn fail(e)\n\t\t}\n\t\tif v[3] <= 0 {\n\t\t\treturn fail(fmt.Errorf("dt must be > 0"))\n\t\t}\n\t\treturn m.update(v[0], v[1], v[2], v[3]), nil\n',
)
replace_once(
    go,
    "Go flight health validation",
    '\t\teh, _ := parseMachineBool(args[1], "estimator")\n\t\tph, _ := parseMachineBool(args[2], "position")\n\t\tbf, e := num(3, "battery")\n\t\tif e != nil {\n\t\t\treturn fail(e)\n\t\t}\n\t\trc, _ := parseMachineBool(args[4], "rc")\n\t\tdl, _ := parseMachineBool(args[5], "data")\n\t\ths, _ := parseMachineBool(args[6], "home")\n\t\tm.EstimatorHealthy = eh\n\t\tm.PositionHealthy = ph\n\t\tm.BatteryFraction = clampFloat(bf, 0, 1)\n',
    '\t\teh, e := parseMachineBool(args[1], "estimator")\n\t\tif e != nil { return fail(e) }\n\t\tph, e := parseMachineBool(args[2], "position")\n\t\tif e != nil { return fail(e) }\n\t\tbf, e := num(3, "battery")\n\t\tif e != nil {\n\t\t\treturn fail(e)\n\t\t}\n\t\tif bf < 0 || bf > 1 {\n\t\t\treturn fail(fmt.Errorf("battery_fraction must be in 0..1"))\n\t\t}\n\t\trc, e := parseMachineBool(args[4], "rc")\n\t\tif e != nil { return fail(e) }\n\t\tdl, e := parseMachineBool(args[5], "data")\n\t\tif e != nil { return fail(e) }\n\t\ths, e := parseMachineBool(args[6], "home")\n\t\tif e != nil { return fail(e) }\n\t\tm.EstimatorHealthy = eh\n\t\tm.PositionHealthy = ph\n\t\tm.BatteryFraction = bf\n',
)
replace_once(
    go,
    "Go prearm bool validation",
    '\t\tq, _ := parseMachineBool(args[1], "require_position")\n\t\treturn m.prearm(q), nil\n',
    '\t\tq, e := parseMachineBool(args[1], "require_position")\n\t\tif e != nil { return fail(e) }\n\t\treturn m.prearm(q), nil\n',
)
replace_once(
    go,
    "Go arm bool validation",
    '\t\tq, _ := parseMachineBool(args[1], "require_position")\n\t\tif e := m.arm(q); e != nil {\n',
    '\t\tq, e := parseMachineBool(args[1], "require_position")\n\t\tif e != nil { return fail(e) }\n\t\tif e := m.arm(q); e != nil {\n',
)
replace_once(
    go,
    "Go RTL home validation",
    '\t\tif v[4] <= 0 || v[3] < v[2] {\n\t\t\treturn fail(fmt.Errorf("invalid RTL planner"))\n\t\t}\n\t\treturn &droneRTLPlanner{v[0], v[1], v[2], v[3], v[4]}, nil\n',
    '\t\tif e := validateDroneCoordinate(v[0], v[1]); e != nil {\n\t\t\treturn fail(e)\n\t\t}\n\t\tif v[4] <= 0 || v[3] < v[2] {\n\t\t\treturn fail(fmt.Errorf("invalid RTL planner"))\n\t\t}\n\t\treturn &droneRTLPlanner{v[0], v[1], v[2], v[3], v[4]}, nil\n',
)
replace_once(
    go,
    "Go RTL current validation",
    '\t\tla, _ := num(1, "lat")\n\t\tlo, _ := num(2, "lon")\n\t\tal, _ := num(3, "alt")\n\t\tq, e := r.targetJSON(la, lo, al)\n',
    '\t\tla, e := num(1, "lat")\n\t\tif e != nil { return fail(e) }\n\t\tlo, e := num(2, "lon")\n\t\tif e != nil { return fail(e) }\n\t\tal, e := num(3, "alt")\n\t\tif e != nil { return fail(e) }\n\t\tif e = validateDroneCoordinate(la, lo); e != nil { return fail(e) }\n\t\tq, e := r.targetJSON(la, lo, al)\n',
)
