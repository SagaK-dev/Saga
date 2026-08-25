package main

import (
	"crypto/sha256"
	"crypto/subtle"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"strings"
	"time"
)

type droneAttitudeEstimator struct {
	Gain             float64
	Roll, Pitch, Yaw float64
	Healthy          bool
	Updates          int64
}

type droneAttitudeController struct{ KpRoll, KpPitch, KpYaw, MaxRate float64 }
type droneQuaternionController struct{ KpRoll, KpPitch, KpYaw, MaxRate float64 }
type droneRateController struct{ Roll, Pitch, Yaw *MachinePID }
type dronePositionController struct {
	PositionKP, MaxSpeed, MaxAcceleration float64
	Velocity                              [3]*MachinePID
}
type droneMixer struct{ Idle, Maximum float64 }
type droneGeofence struct{ HomeLat, HomeLon, Radius, MinAlt, MaxAlt float64 }
type droneWaypoint struct{ Lat, Lon, Alt, Radius, Hold float64 }
type droneMission struct {
	Waypoints   []droneWaypoint
	Index       int
	HoldElapsed float64
	Complete    bool
}
type droneFlightManager struct {
	Safety            *MachineSafety
	MinimumArmBattery float64
	State             string
	Mode              string
	HomeSet           bool
	EstimatorHealthy  bool
	PositionHealthy   bool
	BatteryFraction   float64
	RCLink, DataLink  bool
	LastReason        string
}

type droneMavlinkStream struct {
	Buffer       []byte
	DroppedBytes int64
	BadFrames    int64
}

type droneRTLPlanner struct {
	HomeLat, HomeLon, HomeAlt, ReturnAlt, Acceptance float64
}

func droneWrapPi(v float64) float64                   { return math.Atan2(math.Sin(v), math.Cos(v)) }
func droneAngleError(target, current float64) float64 { return droneWrapPi(target - current) }
func validDroneLatitude(v float64) bool               { return finiteFloat(v) && v >= -90 && v <= 90 }
func validDroneLongitude(v float64) bool              { return finiteFloat(v) && v >= -180 && v <= 180 }
func validateDroneCoordinate(lat, lon float64) error {
	if !validDroneLatitude(lat) {
		return fmt.Errorf("latitude must be in -90..90")
	}
	if !validDroneLongitude(lon) {
		return fmt.Errorf("longitude must be in -180..180")
	}
	return nil
}
func wrapDroneLongitude(v float64) float64 {
	return math.Mod(math.Mod(v+180, 360)+360, 360) - 180
}
func droneList3(v Value, name string) ([3]float64, error) {
	var out [3]float64
	list, ok := v.([]Value)
	if !ok || len(list) != 3 {
		return out, fmt.Errorf("%s must contain exactly three values", name)
	}
	for j := 0; j < 3; j++ {
		q, err := machineNumber(list[j], name)
		if err != nil {
			return out, err
		}
		out[j] = q
	}
	return out, nil
}
func droneValues3(v [3]float64) []Value {
	return []Value{machineNumberFromFloat(v[0]), machineNumberFromFloat(v[1]), machineNumberFromFloat(v[2])}
}
func droneValues(v []float64) []Value {
	out := make([]Value, len(v))
	for j, q := range v {
		out[j] = machineNumberFromFloat(q)
	}
	return out
}

func (e *droneAttitudeEstimator) update(gx, gy, gz, ax, ay, az, mx, my, mz, dt float64) ([3]float64, error) {
	var out [3]float64
	if dt <= 0 || !finiteFloat(dt) {
		return out, fmt.Errorf("dt_seconds must be > 0")
	}
	vals := []float64{gx, gy, gz, ax, ay, az, mx, my, mz}
	for _, v := range vals {
		if !finiteFloat(v) {
			return out, fmt.Errorf("sensor input must be finite")
		}
	}
	rg, pg, yg := e.Roll+gx*dt, e.Pitch+gy*dt, e.Yaw+gz*dt
	an := math.Sqrt(ax*ax + ay*ay + az*az)
	mn := math.Sqrt(mx*mx + my*my + mz*mz)
	if an > 1e-9 {
		ra := math.Atan2(ay, az)
		pa := math.Atan2(-ax, math.Sqrt(ay*ay+az*az))
		e.Roll = droneWrapPi((1-e.Gain)*rg + e.Gain*ra)
		e.Pitch = droneWrapPi((1-e.Gain)*pg + e.Gain*pa)
	} else {
		e.Roll, e.Pitch = droneWrapPi(rg), droneWrapPi(pg)
	}
	if mn > 1e-12 {
		r, p := e.Roll, e.Pitch
		xh := mx*math.Cos(p) + mz*math.Sin(p)
		yh := mx*math.Sin(r)*math.Sin(p) + my*math.Cos(r) - mz*math.Sin(r)*math.Cos(p)
		ym := math.Atan2(-yh, xh)
		e.Yaw = droneWrapPi((1-e.Gain)*yg + e.Gain*ym)
	} else {
		e.Yaw = droneWrapPi(yg)
	}
	e.Healthy = an > 1e-9
	e.Updates++
	return [3]float64{e.Roll, e.Pitch, e.Yaw}, nil
}
func (c *droneAttitudeController) step(target, current [3]float64) [3]float64 {
	g := [3]float64{c.KpRoll, c.KpPitch, c.KpYaw}
	var out [3]float64
	for j := 0; j < 3; j++ {
		out[j] = clampFloat(g[j]*droneAngleError(target[j], current[j]), -c.MaxRate, c.MaxRate)
	}
	return out
}
func droneQuaternionNormalize(q [4]float64) ([4]float64, error) {
	n := math.Sqrt(q[0]*q[0] + q[1]*q[1] + q[2]*q[2] + q[3]*q[3])
	if n <= 1e-12 || !finiteFloat(n) {
		return [4]float64{}, fmt.Errorf("quaternion norm must be non-zero")
	}
	for j := range q {
		q[j] /= n
	}
	return q, nil
}
func droneQuaternionFromRPY(roll, pitch, yaw float64) ([4]float64, error) {
	vals := []float64{roll, pitch, yaw}
	for _, v := range vals {
		if !finiteFloat(v) {
			return [4]float64{}, fmt.Errorf("attitude must be finite")
		}
	}
	r, p, y := roll/2, pitch/2, yaw/2
	cr, sr, cp, sp, cy, sy := math.Cos(r), math.Sin(r), math.Cos(p), math.Sin(p), math.Cos(y), math.Sin(y)
	return droneQuaternionNormalize([4]float64{cr*cp*cy + sr*sp*sy, sr*cp*cy - cr*sp*sy, cr*sp*cy + sr*cp*sy, cr*cp*sy - sr*sp*cy})
}
func droneList4(v Value, name string) ([4]float64, error) {
	var out [4]float64
	list, ok := v.([]Value)
	if !ok || len(list) != 4 {
		return out, fmt.Errorf("%s must contain exactly four values", name)
	}
	for j := 0; j < 4; j++ {
		q, e := machineNumber(list[j], name)
		if e != nil {
			return out, e
		}
		out[j] = q
	}
	return out, nil
}
func droneValues4(v [4]float64) []Value {
	return []Value{machineNumberFromFloat(v[0]), machineNumberFromFloat(v[1]), machineNumberFromFloat(v[2]), machineNumberFromFloat(v[3])}
}
func (c *droneQuaternionController) step(target, current [4]float64) ([3]float64, error) {
	t, e := droneQuaternionNormalize(target)
	if e != nil {
		return [3]float64{}, e
	}
	q, e := droneQuaternionNormalize(current)
	if e != nil {
		return [3]float64{}, e
	}
	ew := t[0]*q[0] + t[1]*q[1] + t[2]*q[2] + t[3]*q[3]
	ex := -t[0]*q[1] + t[1]*q[0] - t[2]*q[3] + t[3]*q[2]
	ey := -t[0]*q[2] + t[1]*q[3] + t[2]*q[0] - t[3]*q[1]
	ez := -t[0]*q[3] - t[1]*q[2] + t[2]*q[1] + t[3]*q[0]
	if ew < 0 {
		ex, ey, ez = -ex, -ey, -ez
	}
	g := [3]float64{c.KpRoll, c.KpPitch, c.KpYaw}
	er := [3]float64{ex, ey, ez}
	var out [3]float64
	for j := 0; j < 3; j++ {
		out[j] = clampFloat(2*g[j]*er[j], -c.MaxRate, c.MaxRate)
	}
	return out, nil
}

func newDroneRateController(kp, ki, kd, limit float64) (*droneRateController, error) {
	if limit <= 0 {
		return nil, fmt.Errorf("output_limit must be > 0")
	}
	a, e := newMachinePID(kp, ki, kd, -limit, limit)
	if e != nil {
		return nil, e
	}
	b, _ := newMachinePID(kp, ki, kd, -limit, limit)
	c, _ := newMachinePID(kp, ki, kd, -limit, limit)
	return &droneRateController{a, b, c}, nil
}
func (c *droneRateController) step(target, measured [3]float64, dt float64) ([3]float64, error) {
	var out [3]float64
	ctrls := []*MachinePID{c.Roll, c.Pitch, c.Yaw}
	for j := 0; j < 3; j++ {
		q, e := ctrls[j].step(target[j], measured[j], dt)
		if e != nil {
			return out, e
		}
		out[j] = q
	}
	return out, nil
}
func newDronePositionController(pk, vk, vi, vd, maxSpeed, maxAccel float64) (*dronePositionController, error) {
	if pk <= 0 || maxSpeed <= 0 || maxAccel <= 0 {
		return nil, fmt.Errorf("position gain/speed/acceleration must be > 0")
	}
	c := &dronePositionController{PositionKP: pk, MaxSpeed: maxSpeed, MaxAcceleration: maxAccel}
	for j := 0; j < 3; j++ {
		p, e := newMachinePID(vk, vi, vd, -maxAccel, maxAccel)
		if e != nil {
			return nil, e
		}
		c.Velocity[j] = p
	}
	return c, nil
}
func (c *dronePositionController) step(target, pos, vel, ff [3]float64, dt float64) ([3]float64, error) {
	var out [3]float64
	for j := 0; j < 3; j++ {
		sp := clampFloat(ff[j]+c.PositionKP*(target[j]-pos[j]), -c.MaxSpeed, c.MaxSpeed)
		q, e := c.Velocity[j].step(sp, vel[j], dt)
		if e != nil {
			return out, e
		}
		out[j] = q
	}
	return out, nil
}
func (m *droneMixer) mix(thrust, roll, pitch, yaw float64) ([]float64, error) {
	vals := []float64{thrust, roll, pitch, yaw}
	for _, v := range vals {
		if !finiteFloat(v) {
			return nil, fmt.Errorf("mixer input must be finite")
		}
	}
	raw := []float64{thrust + roll + pitch - yaw, thrust - roll + pitch + yaw, thrust - roll - pitch - yaw, thrust + roll - pitch + yaw}
	hi, lo := raw[0], raw[0]
	for _, v := range raw {
		if v > hi {
			hi = v
		}
		if v < lo {
			lo = v
		}
	}
	if hi > m.Maximum {
		d := hi - m.Maximum
		for j := range raw {
			raw[j] -= d
		}
		lo = raw[0]
		for _, v := range raw {
			if v < lo {
				lo = v
			}
		}
	}
	if lo < m.Idle {
		d := m.Idle - lo
		for j := range raw {
			raw[j] += d
		}
	}
	for j := range raw {
		raw[j] = clampFloat(raw[j], m.Idle, m.Maximum)
	}
	return raw, nil
}
func (g *droneGeofence) distance(lat, lon float64) float64 {
	const r = 6371008.8
	p1, p2 := g.HomeLat*math.Pi/180, lat*math.Pi/180
	dp := p2 - p1
	dl := (lon - g.HomeLon) * math.Pi / 180
	a := math.Sin(dp/2)*math.Sin(dp/2) + math.Cos(p1)*math.Cos(p2)*math.Sin(dl/2)*math.Sin(dl/2)
	return 2 * r * math.Asin(math.Min(1, math.Sqrt(a)))
}
func (g *droneGeofence) contains(lat, lon, alt float64) bool {
	return alt >= g.MinAlt && alt <= g.MaxAlt && g.distance(lat, lon) <= g.Radius
}
func (g *droneGeofence) predict(lat, lon, alt, north, east, up, horizon float64) bool {
	const r = 6371008.8
	lat2 := lat + (north*horizon/r)*180/math.Pi
	if !validDroneLatitude(lat2) {
		return true
	}
	cl := math.Max(1e-9, math.Abs(math.Cos(lat*math.Pi/180)))
	lon2 := wrapDroneLongitude(lon + (east*horizon/r)*180/math.Pi/cl)
	if !finiteFloat(lon2) {
		return true
	}
	return !g.contains(lat2, lon2, alt+up*horizon)
}
func (m *droneMission) update(lat, lon, alt, dt float64) string {
	if m.Complete || m.Index >= len(m.Waypoints) {
		m.Complete = true
		return "complete"
	}
	w := m.Waypoints[m.Index]
	g := droneGeofence{w.Lat, w.Lon, w.Radius, w.Alt - w.Radius, w.Alt + w.Radius}
	if !g.contains(lat, lon, alt) {
		m.HoldElapsed = 0
		return "navigate"
	}
	m.HoldElapsed += dt
	if m.HoldElapsed < w.Hold {
		return "hold"
	}
	m.Index++
	m.HoldElapsed = 0
	if m.Index >= len(m.Waypoints) {
		m.Complete = true
		return "complete"
	}
	return "advance"
}
func (m *droneMission) targetJSON() string {
	if m.Complete || m.Index >= len(m.Waypoints) {
		return `{"complete":true}`
	}
	w := m.Waypoints[m.Index]
	b, _ := json.Marshal(map[string]any{"complete": false, "index": m.Index, "lat": fmt.Sprintf("%.15g", w.Lat), "lon": fmt.Sprintf("%.15g", w.Lon), "alt_m": fmt.Sprintf("%.15g", w.Alt), "acceptance_radius_m": fmt.Sprintf("%.15g", w.Radius), "hold_seconds": fmt.Sprintf("%.15g", w.Hold)})
	return string(b)
}
func newDroneFlightManager(s *MachineSafety, minBattery float64) (*droneFlightManager, error) {
	if minBattery <= 0 || minBattery > 1 {
		return nil, fmt.Errorf("minimum_arm_battery must be in (0,1]")
	}
	m := &droneFlightManager{Safety: s, MinimumArmBattery: minBattery, State: "DISARMED", Mode: "ATTITUDE", BatteryFraction: 1, RCLink: true, DataLink: true}
	if err := s.registerStop(func() error {
		if m.State == "ARMED" {
			m.State = "DISARMED"
			_, reason := m.Safety.snapshot()
			if reason == "" {
				reason = "external safety stop"
			}
			m.LastReason = reason
		}
		return nil
	}); err != nil {
		return nil, err
	}
	return m, nil
}
func (m *droneFlightManager) prearm(requirePosition bool) string {
	tripped, reason := m.Safety.snapshot()
	if tripped {
		return "safety latch: " + reason
	}
	if !m.EstimatorHealthy {
		return "attitude estimator unhealthy"
	}
	if requirePosition && !m.PositionHealthy {
		return "position estimate unhealthy"
	}
	if requirePosition && !m.HomeSet {
		return "home position not set"
	}
	if m.BatteryFraction < m.MinimumArmBattery {
		return "battery below arming threshold"
	}
	if !m.RCLink && !m.DataLink {
		return "no command/control link"
	}
	return ""
}
func (m *droneFlightManager) arm(requirePosition bool) error {
	if m.State != "DISARMED" {
		return fmt.Errorf("cannot arm from %s", m.State)
	}
	if r := m.prearm(requirePosition); r != "" {
		return fmt.Errorf("prearm failed: %s", r)
	}
	m.State = "ARMED"
	m.LastReason = ""
	return nil
}
func (m *droneFlightManager) disarm(reason string) {
	m.State = "DISARMED"
	m.LastReason = strings.TrimSpace(reason)
}
func (m *droneFlightManager) setMode(mode string) error {
	mode = strings.ToUpper(strings.TrimSpace(mode))
	switch mode {
	case "MANUAL", "RATE", "ATTITUDE", "POSITION", "MISSION", "RTL", "LAND":
	default:
		return fmt.Errorf("flight mode must be MANUAL, RATE, ATTITUDE, POSITION, MISSION, RTL, or LAND")
	}
	if m.State != "ARMED" {
		return fmt.Errorf("flight mode can only change while ARMED")
	}
	m.Mode = mode
	return nil
}
func (m *droneFlightManager) allowed() bool {
	tripped, _ := m.Safety.snapshot()
	return m.State == "ARMED" && !tripped
}
func (m *droneFlightManager) controlAllowed() bool { return m.allowed() }

func (r *droneRTLPlanner) targetJSON(lat, lon, alt float64) (string, error) {
	if r.Acceptance <= 0 || r.ReturnAlt < r.HomeAlt {
		return "", fmt.Errorf("invalid RTL planner")
	}
	if err := validateDroneCoordinate(r.HomeLat, r.HomeLon); err != nil {
		return "", fmt.Errorf("invalid RTL home coordinate: %w", err)
	}
	if err := validateDroneCoordinate(lat, lon); err != nil {
		return "", err
	}
	if !finiteFloat(alt) {
		return "", fmt.Errorf("altitude must be finite")
	}
	g := droneGeofence{HomeLat: r.HomeLat, HomeLon: r.HomeLon, Radius: 1, MinAlt: r.HomeAlt - 100000, MaxAlt: r.HomeAlt + 100000}
	d := g.distance(lat, lon)
	phase, tla, tlo, tal := "LAND", r.HomeLat, r.HomeLon, r.HomeAlt
	if d > r.Acceptance && alt < r.ReturnAlt {
		phase, tla, tlo, tal = "CLIMB", lat, lon, r.ReturnAlt
	} else if d > r.Acceptance {
		phase, tla, tlo, tal = "RETURN", r.HomeLat, r.HomeLon, math.Max(alt, r.ReturnAlt)
	} else if alt > r.HomeAlt+0.5 {
		phase = "DESCEND"
	}
	b, _ := json.Marshal(map[string]any{"phase": phase, "lat": fmt.Sprintf("%.15g", tla), "lon": fmt.Sprintf("%.15g", tlo), "alt_m": fmt.Sprintf("%.15g", tal), "distance_m": fmt.Sprintf("%.15g", d)})
	return string(b), nil
}
func droneLandingVelocity(altitude, descent, flareAlt, flareRate float64) (float64, error) {
	if !finiteFloat(altitude) || descent <= 0 || flareAlt <= 0 || flareRate <= 0 {
		return 0, fmt.Errorf("invalid landing profile")
	}
	if altitude <= 0 {
		return 0, nil
	}
	if altitude <= flareAlt {
		return -math.Min(descent, flareRate), nil
	}
	return -descent, nil
}
func droneCANCRC16(data []byte) uint16 {
	crc := uint16(0xffff)
	for _, b := range data {
		crc ^= uint16(b) << 8
		for j := 0; j < 8; j++ {
			if crc&0x8000 != 0 {
				crc = (crc << 1) ^ 0x1021
			} else {
				crc <<= 1
			}
		}
	}
	return crc
}
func droneCANBroadcastID(priority, dataTypeID, sourceNodeID int) (uint32, error) {
	if priority < 0 || priority > 31 {
		return 0, fmt.Errorf("DroneCAN priority must be 0..31")
	}
	if dataTypeID < 0 || dataTypeID > 65535 {
		return 0, fmt.Errorf("DroneCAN message type id must be 0..65535")
	}
	if sourceNodeID < 1 || sourceNodeID > 127 {
		return 0, fmt.Errorf("DroneCAN source node id must be 1..127")
	}
	return uint32(priority)<<24 | uint32(dataTypeID)<<8 | uint32(sourceNodeID), nil
}
func droneCANSingleFrame(priority, dataTypeID, sourceNodeID, transferID int, payload []byte) (map[string]any, error) {
	if transferID < 0 || transferID > 31 {
		return nil, fmt.Errorf("DroneCAN transfer id must be 0..31")
	}
	if len(payload) > 7 {
		return nil, fmt.Errorf("DroneCAN single-frame payload must not exceed 7 bytes")
	}
	id, err := droneCANBroadcastID(priority, dataTypeID, sourceNodeID)
	if err != nil {
		return nil, err
	}
	data := append(append([]byte{}, payload...), byte(0xc0|transferID))
	return map[string]any{"can_id": int(id), "data_hex": hex.EncodeToString(data), "transfer_id": transferID}, nil
}
func droneCANMultiFrame(priority, dataTypeID, sourceNodeID, transferID int, signature, payload []byte) ([]map[string]any, error) {
	if transferID < 0 || transferID > 31 {
		return nil, fmt.Errorf("DroneCAN transfer id must be 0..31")
	}
	if len(signature) != 8 {
		return nil, fmt.Errorf("DroneCAN data type signature must be exactly 8 little-endian bytes")
	}
	if len(payload) <= 7 {
		q, e := droneCANSingleFrame(priority, dataTypeID, sourceNodeID, transferID, payload)
		if e != nil {
			return nil, e
		}
		return []map[string]any{q}, nil
	}
	id, e := droneCANBroadcastID(priority, dataTypeID, sourceNodeID)
	if e != nil {
		return nil, e
	}
	crcInput := append(append([]byte{}, signature...), payload...)
	crc := droneCANCRC16(crcInput)
	stream := []byte{byte(crc), byte(crc >> 8)}
	stream = append(stream, payload...)
	frames := make([]map[string]any, 0, (len(stream)+6)/7)
	toggle := 0
	for off := 0; off < len(stream); off += 7 {
		end := off + 7
		if end > len(stream) {
			end = len(stream)
		}
		chunk := append([]byte{}, stream[off:end]...)
		tail := byte(transferID)
		if off == 0 {
			tail |= 0x80
		}
		if end == len(stream) {
			tail |= 0x40
		}
		if toggle != 0 {
			tail |= 0x20
		}
		data := append(chunk, tail)
		frames = append(frames, map[string]any{"can_id": int(id), "data_hex": hex.EncodeToString(data), "transfer_id": transferID, "index": len(frames)})
		toggle ^= 1
	}
	return frames, nil
}
func droneCANSingleFrameDecode(canID int, data []byte) (map[string]any, error) {
	if canID < 0 || canID >= 1<<29 {
		return nil, fmt.Errorf("DroneCAN CAN id must fit 29 bits")
	}
	if len(data) < 1 || len(data) > 8 {
		return nil, fmt.Errorf("DroneCAN classic CAN data length must be 1..8")
	}
	tail := data[len(data)-1]
	if tail&0xe0 != 0xc0 {
		return nil, fmt.Errorf("DroneCAN frame is not a single-frame transfer")
	}
	return map[string]any{"priority": (canID >> 24) & 0x1f, "data_type_id": (canID >> 8) & 0xffff, "source_node_id": canID & 0x7f, "transfer_id": int(tail & 0x1f), "payload_hex": hex.EncodeToString(data[:len(data)-1])}, nil
}

func mavlinkX25(data []byte) uint16 {
	crc := uint16(0xffff)
	for _, b := range data {
		tmp := uint16(b) ^ uint16(crc&0xff)
		tmp ^= (tmp << 4) & 0xff
		crc = (crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)
	}
	return crc
}
func mavlinkEncode(msgID, crcExtra int, payload []byte, seq, sysid, compid int, incompat, compat byte) ([]byte, error) {
	if msgID < 0 || msgID > 0xffffff || crcExtra < 0 || crcExtra > 255 || len(payload) > 255 || seq < 0 || seq > 255 || sysid < 1 || sysid > 255 || compid < 1 || compid > 255 {
		return nil, fmt.Errorf("invalid MAVLink framing parameter")
	}
	h := []byte{byte(len(payload)), incompat, compat, byte(seq), byte(sysid), byte(compid), byte(msgID), byte(msgID >> 8), byte(msgID >> 16)}
	crc := mavlinkX25(append(append(append([]byte{}, h...), payload...), byte(crcExtra)))
	out := append([]byte{0xfd}, h...)
	out = append(out, payload...)
	var c [2]byte
	binary.LittleEndian.PutUint16(c[:], crc)
	return append(out, c[:]...), nil
}
func mavlinkDecode(frame []byte, crcExtra int) (map[string]any, error) {
	if len(frame) < 12 || frame[0] != 0xfd {
		return nil, fmt.Errorf("invalid MAVLink 2 frame")
	}
	n := int(frame[1])
	signed := frame[2]&1 != 0
	want := 12 + n
	if signed {
		want += 13
	}
	if len(frame) != want {
		return nil, fmt.Errorf("MAVLink 2 frame length mismatch")
	}
	end := 10 + n
	crc := binary.LittleEndian.Uint16(frame[end : end+2])
	actual := mavlinkX25(append(append([]byte{}, frame[1:end]...), byte(crcExtra)))
	if crc != actual {
		return nil, fmt.Errorf("MAVLink 2 checksum mismatch")
	}
	id := int(frame[7]) | int(frame[8])<<8 | int(frame[9])<<16
	info := map[string]any{"payload_len": n, "incompat_flags": int(frame[2]), "compat_flags": int(frame[3]), "sequence": int(frame[4]), "system_id": int(frame[5]), "component_id": int(frame[6]), "message_id": id, "payload_hex": hex.EncodeToString(frame[10:end]), "signed": signed}
	if signed {
		sig := frame[end+2:]
		info["link_id"] = int(sig[0])
		info["timestamp"] = int64(sig[1]) | int64(sig[2])<<8 | int64(sig[3])<<16 | int64(sig[4])<<24 | int64(sig[5])<<32 | int64(sig[6])<<40
		info["signature_hex"] = hex.EncodeToString(sig[7:])
	}
	return info, nil
}
func mavlinkEncodeSigned(msgID, crcExtra int, payload []byte, seq, sysid, compid int, key []byte, linkID int, timestamp int64) ([]byte, error) {
	if len(key) != 32 || linkID < 0 || linkID > 255 || timestamp < 0 || timestamp >= 1<<48 {
		return nil, fmt.Errorf("invalid MAVLink signing metadata")
	}
	base, e := mavlinkEncode(msgID, crcExtra, payload, seq, sysid, compid, 1, 0)
	if e != nil {
		return nil, e
	}
	prefix := make([]byte, 7)
	prefix[0] = byte(linkID)
	for j := 0; j < 6; j++ {
		prefix[j+1] = byte(uint64(timestamp) >> (8 * j))
	}
	h := sha256.New()
	h.Write(key)
	h.Write(base)
	h.Write(prefix)
	sig := h.Sum(nil)[:6]
	out := append(append(base, prefix...), sig...)
	return out, nil
}
func mavlinkVerify(frame []byte, crcExtra int, key []byte, minTimestamp int64) (map[string]any, error) {
	info, e := mavlinkDecode(frame, crcExtra)
	if e != nil {
		return nil, e
	}
	signed, _ := info["signed"].(bool)
	if !signed {
		return nil, fmt.Errorf("MAVLink frame is unsigned")
	}
	if len(key) != 32 {
		return nil, fmt.Errorf("MAVLink signing key must be exactly 32 bytes")
	}
	ts := info["timestamp"].(int64)
	if ts < minTimestamp {
		return nil, fmt.Errorf("MAVLink signing timestamp is older than accepted stream state")
	}
	prefix := frame[len(frame)-13 : len(frame)-6]
	h := sha256.New()
	h.Write(key)
	h.Write(frame[:len(frame)-13])
	h.Write(prefix)
	expected := h.Sum(nil)[:6]
	if subtle.ConstantTimeCompare(expected, frame[len(frame)-6:]) != 1 {
		return nil, fmt.Errorf("MAVLink signature mismatch")
	}
	info["signature_valid"] = true
	return info, nil
}
func mavlinkHeartbeat(seq, sysid, compid, vehicleType, autopilot, baseMode, customMode, status int) ([]byte, error) {
	if vehicleType < 0 || vehicleType > 255 || autopilot < 0 || autopilot > 255 || baseMode < 0 || baseMode > 255 || status < 0 || status > 255 || customMode < 0 || uint64(customMode) > math.MaxUint32 {
		return nil, fmt.Errorf("invalid MAVLink HEARTBEAT field")
	}
	payload := make([]byte, 9)
	binary.LittleEndian.PutUint32(payload[:4], uint32(customMode))
	payload[4] = byte(vehicleType)
	payload[5] = byte(autopilot)
	payload[6] = byte(baseMode)
	payload[7] = byte(status)
	payload[8] = 3
	return mavlinkEncode(0, 50, payload, seq, sysid, compid, 0, 0)
}

var mavlinkCommonCRCExtras = map[int]int{
	0: 50, 31: 246, 32: 185, 33: 104, 76: 152, 77: 143,
	82: 49, 83: 22, 84: 143, 85: 140, 86: 5, 87: 150,
}

func mavlinkPutFloat32(dst []byte, off int, v float64) error {
	if !finiteFloat(v) {
		return fmt.Errorf("MAVLink float field must be finite")
	}
	f := float32(v)
	if math.IsInf(float64(f), 0) || math.IsNaN(float64(f)) {
		return fmt.Errorf("MAVLink float field cannot be represented as float32")
	}
	binary.LittleEndian.PutUint32(dst[off:off+4], math.Float32bits(f))
	return nil
}

func mavlinkSetAttitudeTarget(seq, sysid, compid, targetSystem, targetComponent, typeMask int, quaternion [4]float64, bodyRates [3]float64, thrust float64, timeBootMS int64) ([]byte, error) {
	if timeBootMS < 0 || timeBootMS > math.MaxUint32 || targetSystem < 1 || targetSystem > 255 || targetComponent < 0 || targetComponent > 255 || typeMask < 0 || typeMask > 255 || thrust < -1 || thrust > 1 || !finiteFloat(thrust) {
		return nil, fmt.Errorf("invalid SET_ATTITUDE_TARGET parameter")
	}
	payload := make([]byte, 39)
	binary.LittleEndian.PutUint32(payload[0:4], uint32(timeBootMS))
	off := 4
	for _, v := range quaternion {
		if err := mavlinkPutFloat32(payload, off, v); err != nil {
			return nil, err
		}
		off += 4
	}
	for _, v := range bodyRates {
		if err := mavlinkPutFloat32(payload, off, v); err != nil {
			return nil, err
		}
		off += 4
	}
	if err := mavlinkPutFloat32(payload, off, thrust); err != nil {
		return nil, err
	}
	off += 4
	payload[off], payload[off+1], payload[off+2] = byte(targetSystem), byte(targetComponent), byte(typeMask)
	return mavlinkEncode(82, mavlinkCommonCRCExtras[82], payload, seq, sysid, compid, 0, 0)
}

func mavlinkSetPositionTargetLocalNED(seq, sysid, compid, targetSystem, targetComponent, coordinateFrame, typeMask int, position, velocity, acceleration [3]float64, yaw, yawRate float64, timeBootMS int64) ([]byte, error) {
	if timeBootMS < 0 || timeBootMS > math.MaxUint32 || targetSystem < 1 || targetSystem > 255 || targetComponent < 0 || targetComponent > 255 || typeMask < 0 || typeMask > math.MaxUint16 {
		return nil, fmt.Errorf("invalid SET_POSITION_TARGET_LOCAL_NED parameter")
	}
	if coordinateFrame != 1 && coordinateFrame != 7 && coordinateFrame != 8 && coordinateFrame != 9 {
		return nil, fmt.Errorf("local NED coordinate_frame must be 1, 7, 8, or 9")
	}
	payload := make([]byte, 53)
	binary.LittleEndian.PutUint32(payload[0:4], uint32(timeBootMS))
	off := 4
	for _, vector := range [][3]float64{position, velocity, acceleration} {
		for _, v := range vector {
			if err := mavlinkPutFloat32(payload, off, v); err != nil {
				return nil, err
			}
			off += 4
		}
	}
	for _, v := range []float64{yaw, yawRate} {
		if err := mavlinkPutFloat32(payload, off, v); err != nil {
			return nil, err
		}
		off += 4
	}
	binary.LittleEndian.PutUint16(payload[off:off+2], uint16(typeMask))
	off += 2
	payload[off], payload[off+1], payload[off+2] = byte(targetSystem), byte(targetComponent), byte(coordinateFrame)
	return mavlinkEncode(84, mavlinkCommonCRCExtras[84], payload, seq, sysid, compid, 0, 0)
}

func mavlinkCommandLong(seq, sysid, compid, targetSystem, targetComponent, command, confirmation int, params [7]float64) ([]byte, error) {
	if targetSystem < 1 || targetSystem > 255 || targetComponent < 0 || targetComponent > 255 || command < 0 || command > math.MaxUint16 || confirmation < 0 || confirmation > 255 {
		return nil, fmt.Errorf("invalid COMMAND_LONG parameter")
	}
	payload := make([]byte, 33)
	off := 0
	for _, v := range params {
		if err := mavlinkPutFloat32(payload, off, v); err != nil {
			return nil, err
		}
		off += 4
	}
	binary.LittleEndian.PutUint16(payload[off:off+2], uint16(command))
	off += 2
	payload[off], payload[off+1], payload[off+2] = byte(targetSystem), byte(targetComponent), byte(confirmation)
	return mavlinkEncode(76, mavlinkCommonCRCExtras[76], payload, seq, sysid, compid, 0, 0)
}

func mavlinkFloat32(payload []byte, off int) float64 {
	return float64(math.Float32frombits(binary.LittleEndian.Uint32(payload[off : off+4])))
}

func mavlinkCommonDecode(frame []byte) (map[string]any, error) {
	if len(frame) < 10 || frame[0] != 0xfd {
		return nil, fmt.Errorf("invalid MAVLink 2 frame")
	}
	id := int(frame[7]) | int(frame[8])<<8 | int(frame[9])<<16
	crcExtra, ok := mavlinkCommonCRCExtras[id]
	if !ok {
		return nil, fmt.Errorf("unsupported MAVLink common message id %d", id)
	}
	info, err := mavlinkDecode(frame, crcExtra)
	if err != nil {
		return nil, err
	}
	payloadHex, _ := info["payload_hex"].(string)
	payload, _ := hex.DecodeString(payloadHex)
	fields := map[string]any{}
	switch id {
	case 0:
		if len(payload) >= 9 {
			fields = map[string]any{"custom_mode": int(binary.LittleEndian.Uint32(payload[:4])), "type": int(payload[4]), "autopilot": int(payload[5]), "base_mode": int(payload[6]), "system_status": int(payload[7]), "mavlink_version": int(payload[8])}
		}
	case 31:
		if len(payload) >= 32 {
			q := []float64{mavlinkFloat32(payload, 4), mavlinkFloat32(payload, 8), mavlinkFloat32(payload, 12), mavlinkFloat32(payload, 16)}
			fields = map[string]any{"time_boot_ms": int64(binary.LittleEndian.Uint32(payload[:4])), "q": q, "rollspeed": mavlinkFloat32(payload, 20), "pitchspeed": mavlinkFloat32(payload, 24), "yawspeed": mavlinkFloat32(payload, 28)}
		}
	case 32:
		if len(payload) >= 28 {
			fields = map[string]any{"time_boot_ms": int64(binary.LittleEndian.Uint32(payload[:4])), "x": mavlinkFloat32(payload, 4), "y": mavlinkFloat32(payload, 8), "z": mavlinkFloat32(payload, 12), "vx": mavlinkFloat32(payload, 16), "vy": mavlinkFloat32(payload, 20), "vz": mavlinkFloat32(payload, 24)}
		}
	case 33:
		if len(payload) >= 28 {
			fields = map[string]any{"time_boot_ms": int64(binary.LittleEndian.Uint32(payload[:4])), "lat_e7": int32(binary.LittleEndian.Uint32(payload[4:8])), "lon_e7": int32(binary.LittleEndian.Uint32(payload[8:12])), "alt_mm": int32(binary.LittleEndian.Uint32(payload[12:16])), "relative_alt_mm": int32(binary.LittleEndian.Uint32(payload[16:20])), "vx_cms": int16(binary.LittleEndian.Uint16(payload[20:22])), "vy_cms": int16(binary.LittleEndian.Uint16(payload[22:24])), "vz_cms": int16(binary.LittleEndian.Uint16(payload[24:26])), "heading_cdeg": int(binary.LittleEndian.Uint16(payload[26:28]))}
		}
	case 77:
		if len(payload) >= 3 {
			fields = map[string]any{"command": int(binary.LittleEndian.Uint16(payload[:2])), "result": int(payload[2])}
		}
	case 83:
		if len(payload) >= 37 {
			q := []float64{mavlinkFloat32(payload, 4), mavlinkFloat32(payload, 8), mavlinkFloat32(payload, 12), mavlinkFloat32(payload, 16)}
			fields = map[string]any{"time_boot_ms": int64(binary.LittleEndian.Uint32(payload[:4])), "q": q, "body_roll_rate": mavlinkFloat32(payload, 20), "body_pitch_rate": mavlinkFloat32(payload, 24), "body_yaw_rate": mavlinkFloat32(payload, 28), "thrust": mavlinkFloat32(payload, 32), "type_mask": int(payload[36])}
		}
	case 85:
		if len(payload) >= 51 {
			fields = map[string]any{"time_boot_ms": int64(binary.LittleEndian.Uint32(payload[:4])), "x": mavlinkFloat32(payload, 4), "y": mavlinkFloat32(payload, 8), "z": mavlinkFloat32(payload, 12), "vx": mavlinkFloat32(payload, 16), "vy": mavlinkFloat32(payload, 20), "vz": mavlinkFloat32(payload, 24), "afx": mavlinkFloat32(payload, 28), "afy": mavlinkFloat32(payload, 32), "afz": mavlinkFloat32(payload, 36), "yaw": mavlinkFloat32(payload, 40), "yaw_rate": mavlinkFloat32(payload, 44), "type_mask": int(binary.LittleEndian.Uint16(payload[48:50])), "coordinate_frame": int(payload[50])}
		}
	}
	info["fields"] = fields
	return info, nil
}

func (p *droneMavlinkStream) feed(data []byte) []map[string]any {
	p.Buffer = append(p.Buffer, data...)
	out := []map[string]any{}
	for {
		for len(p.Buffer) > 0 && p.Buffer[0] != 0xfd {
			p.Buffer = p.Buffer[1:]
			p.DroppedBytes++
		}
		if len(p.Buffer) < 12 {
			break
		}
		n := int(p.Buffer[1])
		signed := p.Buffer[2]&1 != 0
		total := 12 + n
		if signed {
			total += 13
		}
		if len(p.Buffer) < total {
			break
		}
		frame := append([]byte{}, p.Buffer[:total]...)
		id := int(frame[7]) | int(frame[8])<<8 | int(frame[9])<<16
		if _, ok := mavlinkCommonCRCExtras[id]; !ok {
			out = append(out, map[string]any{"message_id": id, "frame_hex": hex.EncodeToString(frame), "known": false})
			p.Buffer = p.Buffer[total:]
			continue
		}
		info, err := mavlinkCommonDecode(frame)
		if err != nil {
			p.Buffer = p.Buffer[1:]
			p.BadFrames++
			continue
		}
		info["frame_hex"] = hex.EncodeToString(frame)
		info["known"] = true
		out = append(out, info)
		p.Buffer = p.Buffer[total:]
	}
	return out
}

func droneDShotFrame(throttle float64, telemetry bool) (int, error) {
	if !finiteFloat(throttle) || throttle < 0 || throttle > 1 {
		return 0, fmt.Errorf("DShot throttle must be in 0..1")
	}
	word := 0
	if throttle > 0 {
		word = 48 + int(math.Round(1999*throttle))
		if word > 2047 {
			word = 2047
		}
	}
	packet := word << 1
	if telemetry {
		packet |= 1
	}
	data, checksum := packet, 0
	for j := 0; j < 3; j++ {
		checksum ^= data
		data >>= 4
	}
	return ((packet << 4) | (checksum & 0xf)) & 0xffff, nil
}

func dronePWMESCDuty(throttle, minimumUS, maximumUS, periodUS float64) (float64, error) {
	if !finiteFloat(throttle) || !finiteFloat(minimumUS) || !finiteFloat(maximumUS) || !finiteFloat(periodUS) || throttle < 0 || throttle > 1 || minimumUS <= 0 || maximumUS <= minimumUS || periodUS < maximumUS {
		return 0, fmt.Errorf("ESC PWM timing or throttle is invalid")
	}
	pulse := minimumUS + (maximumUS-minimumUS)*throttle
	return pulse / periodUS, nil
}

func (i *Interpreter) callDroneNative(name string, args []Value, t Token) (Value, error) {
	fail := func(err error) (Value, error) {
		return nil, diag("SAGA-R001", "SAGA-R196", "drone."+name+": "+err.Error(), t)
	}
	num := func(j int, label string) (float64, error) {
		if j >= len(args) {
			return 0, fmt.Errorf("missing %s", label)
		}
		return machineNumber(args[j], label)
	}
	intv := func(j int, label string) (int, error) {
		if j >= len(args) {
			return 0, fmt.Errorf("missing %s", label)
		}
		return machineInt(args[j], label)
	}
	switch name {
	case "profile":
		return "hosted-flight-control-sitl-hil", nil
	case "hard_realtime_available":
		return false, nil
	case "attitude_estimator":
		if len(args) != 1 {
			return fail(fmt.Errorf("requires 1 argument"))
		}
		g, e := num(0, "correction_gain")
		if e != nil || g < 0 || g > 1 {
			if e == nil {
				e = fmt.Errorf("correction_gain must be in 0..1")
			}
			return fail(e)
		}
		return &droneAttitudeEstimator{Gain: g}, nil
	case "attitude_update":
		if len(args) != 11 {
			return fail(fmt.Errorf("requires 11 arguments"))
		}
		est, ok := args[0].(*droneAttitudeEstimator)
		if !ok {
			return fail(fmt.Errorf("invalid attitude estimator"))
		}
		v := make([]float64, 10)
		for j := range v {
			q, e := num(j+1, "sensor")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		out, e := est.update(v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7], v[8], v[9])
		if e != nil {
			return fail(e)
		}
		return droneValues3(out), nil
	case "attitude_rpy":
		est, ok := args[0].(*droneAttitudeEstimator)
		if !ok {
			return fail(fmt.Errorf("invalid attitude estimator"))
		}
		return droneValues3([3]float64{est.Roll, est.Pitch, est.Yaw}), nil
	case "attitude_healthy":
		est, ok := args[0].(*droneAttitudeEstimator)
		if !ok {
			return fail(fmt.Errorf("invalid attitude estimator"))
		}
		return est.Healthy, nil
	case "attitude_controller":
		if len(args) != 4 {
			return fail(fmt.Errorf("requires 4 arguments"))
		}
		v := make([]float64, 4)
		for j := range v {
			q, e := num(j, "gain")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		if v[3] <= 0 {
			return fail(fmt.Errorf("max_rate must be > 0"))
		}
		return &droneAttitudeController{v[0], v[1], v[2], v[3]}, nil
	case "attitude_step":
		c, ok := args[0].(*droneAttitudeController)
		if !ok {
			return fail(fmt.Errorf("invalid attitude controller"))
		}
		a, e := droneList3(args[1], "target attitude")
		if e != nil {
			return fail(e)
		}
		b, e := droneList3(args[2], "current attitude")
		if e != nil {
			return fail(e)
		}
		return droneValues3(c.step(a, b)), nil
	case "quaternion_from_rpy":
		r, _ := num(0, "roll")
		p, _ := num(1, "pitch")
		y, _ := num(2, "yaw")
		q, e := droneQuaternionFromRPY(r, p, y)
		if e != nil {
			return fail(e)
		}
		return droneValues4(q), nil
	case "quaternion_controller":
		v := make([]float64, 4)
		for j := range v {
			q, e := num(j, "gain")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		if v[3] <= 0 {
			return fail(fmt.Errorf("max_rate must be > 0"))
		}
		return &droneQuaternionController{v[0], v[1], v[2], v[3]}, nil
	case "quaternion_step":
		c, ok := args[0].(*droneQuaternionController)
		if !ok {
			return fail(fmt.Errorf("invalid quaternion controller"))
		}
		tq, e := droneList4(args[1], "target quaternion")
		if e != nil {
			return fail(e)
		}
		cq, e := droneList4(args[2], "current quaternion")
		if e != nil {
			return fail(e)
		}
		o, e := c.step(tq, cq)
		if e != nil {
			return fail(e)
		}
		return droneValues3(o), nil
	case "rate_controller":
		if len(args) != 4 {
			return fail(fmt.Errorf("requires 4 arguments"))
		}
		v := make([]float64, 4)
		for j := range v {
			q, e := num(j, "gain")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		c, e := newDroneRateController(v[0], v[1], v[2], v[3])
		if e != nil {
			return fail(e)
		}
		return c, nil
	case "rate_step":
		c, ok := args[0].(*droneRateController)
		if !ok {
			return fail(fmt.Errorf("invalid rate controller"))
		}
		a, e := droneList3(args[1], "target rates")
		if e != nil {
			return fail(e)
		}
		b, e := droneList3(args[2], "measured rates")
		if e != nil {
			return fail(e)
		}
		dt, e := num(3, "dt")
		if e != nil {
			return fail(e)
		}
		o, e := c.step(a, b, dt)
		if e != nil {
			return fail(e)
		}
		return droneValues3(o), nil
	case "rate_reset":
		c, ok := args[0].(*droneRateController)
		if !ok {
			return fail(fmt.Errorf("invalid rate controller"))
		}
		c.Roll.reset()
		c.Pitch.reset()
		c.Yaw.reset()
		return nil, nil
	case "position_controller":
		if len(args) != 6 {
			return fail(fmt.Errorf("requires 6 arguments"))
		}
		v := make([]float64, 6)
		for j := range v {
			q, e := num(j, "parameter")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		c, e := newDronePositionController(v[0], v[1], v[2], v[3], v[4], v[5])
		if e != nil {
			return fail(e)
		}
		return c, nil
	case "position_step":
		c, ok := args[0].(*dronePositionController)
		if !ok {
			return fail(fmt.Errorf("invalid position controller"))
		}
		a, e := droneList3(args[1], "target position")
		if e != nil {
			return fail(e)
		}
		b, e := droneList3(args[2], "position")
		if e != nil {
			return fail(e)
		}
		d, e := droneList3(args[3], "velocity")
		if e != nil {
			return fail(e)
		}
		f, e := droneList3(args[4], "feedforward")
		if e != nil {
			return fail(e)
		}
		dt, e := num(5, "dt")
		if e != nil {
			return fail(e)
		}
		o, e := c.step(a, b, d, f, dt)
		if e != nil {
			return fail(e)
		}
		return droneValues3(o), nil
	case "quad_x_mixer":
		idle, e := num(0, "idle")
		if e != nil {
			return fail(e)
		}
		mx, e := num(1, "maximum")
		if e != nil || idle < 0 || mx <= idle {
			if e == nil {
				e = fmt.Errorf("requires 0 <= idle < maximum")
			}
			return fail(e)
		}
		return &droneMixer{idle, mx}, nil
	case "mix_quad_x":
		m, ok := args[0].(*droneMixer)
		if !ok {
			return fail(fmt.Errorf("invalid mixer"))
		}
		v := make([]float64, 4)
		for j := range v {
			q, e := num(j+1, "mixer input")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		o, e := m.mix(v[0], v[1], v[2], v[3])
		if e != nil {
			return fail(e)
		}
		return droneValues(o), nil
	case "geofence":
		v := make([]float64, 5)
		for j := range v {
			q, e := num(j, "geofence parameter")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		if e := validateDroneCoordinate(v[0], v[1]); e != nil {
			return fail(e)
		}
		if v[2] <= 0 || v[3] >= v[4] {
			return fail(fmt.Errorf("invalid geofence"))
		}
		return &droneGeofence{v[0], v[1], v[2], v[3], v[4]}, nil
	case "geofence_contains":
		g, ok := args[0].(*droneGeofence)
		if !ok {
			return fail(fmt.Errorf("invalid geofence"))
		}
		la, e := num(1, "lat")
		if e != nil {
			return fail(e)
		}
		lo, e := num(2, "lon")
		if e != nil {
			return fail(e)
		}
		al, e := num(3, "alt")
		if e != nil {
			return fail(e)
		}
		if e = validateDroneCoordinate(la, lo); e != nil {
			return fail(e)
		}
		return g.contains(la, lo, al), nil
	case "geofence_distance_m":
		g, ok := args[0].(*droneGeofence)
		if !ok {
			return fail(fmt.Errorf("invalid geofence"))
		}
		la, e := num(1, "lat")
		if e != nil {
			return fail(e)
		}
		lo, e := num(2, "lon")
		if e != nil {
			return fail(e)
		}
		if e = validateDroneCoordinate(la, lo); e != nil {
			return fail(e)
		}
		return machineNumberFromFloat(g.distance(la, lo)), nil
	case "geofence_predict_breach":
		g, ok := args[0].(*droneGeofence)
		if !ok {
			return fail(fmt.Errorf("invalid geofence"))
		}
		v := make([]float64, 7)
		for j := range v {
			q, e := num(j+1, "prediction")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		if e := validateDroneCoordinate(v[0], v[1]); e != nil {
			return fail(e)
		}
		if v[6] <= 0 {
			return fail(fmt.Errorf("horizon must be > 0"))
		}
		return g.predict(v[0], v[1], v[2], v[3], v[4], v[5], v[6]), nil
	case "mission":
		return &droneMission{}, nil
	case "mission_add":
		m, ok := args[0].(*droneMission)
		if !ok {
			return fail(fmt.Errorf("invalid mission"))
		}
		v := make([]float64, 5)
		for j := range v {
			q, e := num(j+1, "waypoint")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		if e := validateDroneCoordinate(v[0], v[1]); e != nil {
			return fail(e)
		}
		if v[3] <= 0 || v[4] < 0 {
			return fail(fmt.Errorf("invalid waypoint radius/hold"))
		}
		m.Waypoints = append(m.Waypoints, droneWaypoint{v[0], v[1], v[2], v[3], v[4]})
		m.Complete = false
		return nil, nil
	case "mission_reset":
		m, ok := args[0].(*droneMission)
		if !ok {
			return fail(fmt.Errorf("invalid mission"))
		}
		m.Index = 0
		m.HoldElapsed = 0
		m.Complete = false
		return nil, nil
	case "mission_update":
		m, ok := args[0].(*droneMission)
		if !ok {
			return fail(fmt.Errorf("invalid mission"))
		}
		v := make([]float64, 4)
		for j := range v {
			q, e := num(j+1, "mission update")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		if e := validateDroneCoordinate(v[0], v[1]); e != nil {
			return fail(e)
		}
		if v[3] <= 0 {
			return fail(fmt.Errorf("dt must be > 0"))
		}
		return m.update(v[0], v[1], v[2], v[3]), nil
	case "mission_target_json":
		m, ok := args[0].(*droneMission)
		if !ok {
			return fail(fmt.Errorf("invalid mission"))
		}
		return m.targetJSON(), nil
	case "mission_complete":
		m, ok := args[0].(*droneMission)
		if !ok {
			return fail(fmt.Errorf("invalid mission"))
		}
		return m.Complete, nil
	case "flight_manager":
		s, ok := args[0].(*MachineSafety)
		if !ok {
			return fail(fmt.Errorf("flight_manager requires machine safety latch"))
		}
		b, e := num(1, "minimum battery")
		if e != nil {
			return fail(e)
		}
		m, e := newDroneFlightManager(s, b)
		if e != nil {
			return fail(e)
		}
		return m, nil
	case "health_update":
		m, ok := args[0].(*droneFlightManager)
		if !ok {
			return fail(fmt.Errorf("invalid flight manager"))
		}
		eh, e := parseMachineBool(args[1], "estimator")
		if e != nil {
			return fail(e)
		}
		ph, e := parseMachineBool(args[2], "position")
		if e != nil {
			return fail(e)
		}
		bf, e := num(3, "battery")
		if e != nil {
			return fail(e)
		}
		if bf < 0 || bf > 1 {
			return fail(fmt.Errorf("battery_fraction must be in 0..1"))
		}
		rc, e := parseMachineBool(args[4], "rc")
		if e != nil {
			return fail(e)
		}
		dl, e := parseMachineBool(args[5], "data")
		if e != nil {
			return fail(e)
		}
		hs, e := parseMachineBool(args[6], "home")
		if e != nil {
			return fail(e)
		}
		m.EstimatorHealthy = eh
		m.PositionHealthy = ph
		m.BatteryFraction = bf
		m.RCLink = rc
		m.DataLink = dl
		m.HomeSet = hs
		return nil, nil
	case "prearm_reason":
		m, ok := args[0].(*droneFlightManager)
		if !ok {
			return fail(fmt.Errorf("invalid flight manager"))
		}
		q, e := parseMachineBool(args[1], "require_position")
		if e != nil {
			return fail(e)
		}
		return m.prearm(q), nil
	case "arm":
		m, ok := args[0].(*droneFlightManager)
		if !ok {
			return fail(fmt.Errorf("invalid flight manager"))
		}
		q, e := parseMachineBool(args[1], "require_position")
		if e != nil {
			return fail(e)
		}
		if e := m.arm(q); e != nil {
			return fail(e)
		}
		return nil, nil
	case "disarm":
		m, ok := args[0].(*droneFlightManager)
		if !ok {
			return fail(fmt.Errorf("invalid flight manager"))
		}
		m.disarm("")
		return nil, nil
	case "set_mode":
		m, ok := args[0].(*droneFlightManager)
		if !ok {
			return fail(fmt.Errorf("invalid flight manager"))
		}
		mode, e := machineText(args[1], "mode")
		if e != nil {
			return fail(e)
		}
		if e = m.setMode(mode); e != nil {
			return fail(e)
		}
		return nil, nil
	case "flight_mode":
		m, ok := args[0].(*droneFlightManager)
		if !ok {
			return fail(fmt.Errorf("invalid flight manager"))
		}
		return m.Mode, nil
	case "flight_allowed":
		m, ok := args[0].(*droneFlightManager)
		if !ok {
			return fail(fmt.Errorf("invalid flight manager"))
		}
		return m.allowed(), nil
	case "control_allowed":
		m, ok := args[0].(*droneFlightManager)
		if !ok {
			return fail(fmt.Errorf("invalid flight manager"))
		}
		return m.controlAllowed(), nil
	case "flight_state":
		m, ok := args[0].(*droneFlightManager)
		if !ok {
			return fail(fmt.Errorf("invalid flight manager"))
		}
		tr, _ := m.Safety.snapshot()
		b, _ := json.Marshal(map[string]any{"state": m.State, "mode": m.Mode, "flight_allowed": m.allowed(), "control_allowed": m.controlAllowed(), "reason": m.LastReason, "safety_tripped": tr, "health": map[string]any{"estimator": m.EstimatorHealthy, "position": m.PositionHealthy, "battery_fraction": m.BatteryFraction, "rc_link": m.RCLink, "data_link": m.DataLink, "home_set": m.HomeSet}})
		return string(b), nil
	case "rtl":
		v := make([]float64, 5)
		for j := range v {
			q, e := num(j, "rtl parameter")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		if e := validateDroneCoordinate(v[0], v[1]); e != nil {
			return fail(e)
		}
		if v[4] <= 0 || v[3] < v[2] {
			return fail(fmt.Errorf("invalid RTL planner"))
		}
		return &droneRTLPlanner{v[0], v[1], v[2], v[3], v[4]}, nil
	case "rtl_target_json":
		r, ok := args[0].(*droneRTLPlanner)
		if !ok {
			return fail(fmt.Errorf("invalid RTL planner"))
		}
		la, e := num(1, "lat")
		if e != nil {
			return fail(e)
		}
		lo, e := num(2, "lon")
		if e != nil {
			return fail(e)
		}
		al, e := num(3, "alt")
		if e != nil {
			return fail(e)
		}
		if e = validateDroneCoordinate(la, lo); e != nil {
			return fail(e)
		}
		q, e := r.targetJSON(la, lo, al)
		if e != nil {
			return fail(e)
		}
		return q, nil
	case "landing_vertical_velocity":
		v := make([]float64, 4)
		for j := range v {
			q, e := num(j, "landing parameter")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		q, e := droneLandingVelocity(v[0], v[1], v[2], v[3])
		if e != nil {
			return fail(e)
		}
		return machineNumberFromFloat(q), nil
	case "dronecan_crc16":
		b, e := machineBytes(args[0], "payload")
		if e != nil {
			return fail(e)
		}
		return numberFromInt64(int64(droneCANCRC16(b))), nil
	case "dronecan_single_frame_json":
		p, _ := intv(0, "priority")
		dt, _ := intv(1, "data type id")
		nid, _ := intv(2, "node id")
		tid, _ := intv(3, "transfer id")
		payload, e := machineBytes(args[4], "payload")
		if e != nil {
			return fail(e)
		}
		q, e := droneCANSingleFrame(p, dt, nid, tid, payload)
		if e != nil {
			return fail(e)
		}
		b, _ := json.Marshal(q)
		return string(b), nil
	case "dronecan_multiframe_json":
		p, _ := intv(0, "priority")
		dt, _ := intv(1, "data type id")
		nid, _ := intv(2, "node id")
		tid, _ := intv(3, "transfer id")
		sig, e := machineBytes(args[4], "signature")
		if e != nil {
			return fail(e)
		}
		payload, e := machineBytes(args[5], "payload")
		if e != nil {
			return fail(e)
		}
		q, e := droneCANMultiFrame(p, dt, nid, tid, sig, payload)
		if e != nil {
			return fail(e)
		}
		b, _ := json.Marshal(q)
		return string(b), nil
	case "dronecan_decode_json":
		id, _ := intv(0, "can id")
		data, e := machineBytes(args[1], "data")
		if e != nil {
			return fail(e)
		}
		q, e := droneCANSingleFrameDecode(id, data)
		if e != nil {
			return fail(e)
		}
		b, _ := json.Marshal(q)
		return string(b), nil
	case "mavlink_encode":
		id, _ := intv(0, "message id")
		ce, _ := intv(1, "crc extra")
		p, e := machineBytes(args[2], "payload")
		if e != nil {
			return fail(e)
		}
		sq, _ := intv(3, "sequence")
		sy, _ := intv(4, "system id")
		co, _ := intv(5, "component id")
		f, e := mavlinkEncode(id, ce, p, sq, sy, co, 0, 0)
		if e != nil {
			return fail(e)
		}
		return f, nil
	case "mavlink_decode_json":
		p, e := machineBytes(args[0], "frame")
		if e != nil {
			return fail(e)
		}
		ce, _ := intv(1, "crc extra")
		info, e := mavlinkDecode(p, ce)
		if e != nil {
			return fail(e)
		}
		b, _ := json.Marshal(info)
		return string(b), nil
	case "mavlink_encode_signed":
		id, _ := intv(0, "message id")
		ce, _ := intv(1, "crc extra")
		p, _ := machineBytes(args[2], "payload")
		sq, _ := intv(3, "sequence")
		sy, _ := intv(4, "system id")
		co, _ := intv(5, "component id")
		key, _ := machineBytes(args[6], "key")
		li, _ := intv(7, "link id")
		tsI, _ := numberToInt(args[8])
		f, e := mavlinkEncodeSigned(id, ce, p, sq, sy, co, key, li, int64(tsI))
		if e != nil {
			return fail(e)
		}
		return f, nil
	case "mavlink_verify_signed_json":
		p, _ := machineBytes(args[0], "frame")
		ce, _ := intv(1, "crc extra")
		key, _ := machineBytes(args[2], "key")
		mt, _ := numberToInt(args[3])
		info, e := mavlinkVerify(p, ce, key, int64(mt))
		if e != nil {
			return fail(e)
		}
		b, _ := json.Marshal(info)
		return string(b), nil
	case "mavlink_signing_timestamp":
		ts := int64(math.Max(0, float64(time.Now().UnixNano())/1e4-1420070400.0*1e5))
		return numberFromInt64(ts), nil
	case "mavlink_heartbeat":
		v := make([]int, 8)
		for j := range v {
			q, e := intv(j, "heartbeat")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		f, e := mavlinkHeartbeat(v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7])
		if e != nil {
			return fail(e)
		}
		return f, nil

	case "mavlink_set_attitude_target":
		if len(args) != 10 {
			return fail(fmt.Errorf("requires 10 arguments"))
		}
		v := make([]int, 6)
		for j := range v {
			q, e := intv(j, "MAVLink integer")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		q, e := droneList4(args[6], "quaternion")
		if e != nil {
			return fail(e)
		}
		rates, e := droneList3(args[7], "body rates")
		if e != nil {
			return fail(e)
		}
		thrust, e := num(8, "thrust")
		if e != nil {
			return fail(e)
		}
		timeBoot, e := numberToInt(args[9])
		if e != nil {
			return fail(e)
		}
		frame, e := mavlinkSetAttitudeTarget(v[0], v[1], v[2], v[3], v[4], v[5], q, rates, thrust, int64(timeBoot))
		if e != nil {
			return fail(e)
		}
		return frame, nil
	case "mavlink_set_position_target_local_ned":
		if len(args) != 13 {
			return fail(fmt.Errorf("requires 13 arguments"))
		}
		v := make([]int, 7)
		for j := range v {
			q, e := intv(j, "MAVLink integer")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		pos, e := droneList3(args[7], "position NED")
		if e != nil {
			return fail(e)
		}
		vel, e := droneList3(args[8], "velocity NED")
		if e != nil {
			return fail(e)
		}
		acc, e := droneList3(args[9], "acceleration NED")
		if e != nil {
			return fail(e)
		}
		yaw, e := num(10, "yaw")
		if e != nil {
			return fail(e)
		}
		yawRate, e := num(11, "yaw rate")
		if e != nil {
			return fail(e)
		}
		timeBoot, e := numberToInt(args[12])
		if e != nil {
			return fail(e)
		}
		frame, e := mavlinkSetPositionTargetLocalNED(v[0], v[1], v[2], v[3], v[4], v[5], v[6], pos, vel, acc, yaw, yawRate, int64(timeBoot))
		if e != nil {
			return fail(e)
		}
		return frame, nil
	case "mavlink_command_long":
		if len(args) != 8 {
			return fail(fmt.Errorf("requires 8 arguments"))
		}
		v := make([]int, 7)
		for j := range v {
			q, e := intv(j, "MAVLink integer")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		list, ok := args[7].([]Value)
		if !ok || len(list) != 7 {
			return fail(fmt.Errorf("COMMAND_LONG params must contain seven values"))
		}
		var params [7]float64
		for j := range params {
			q, e := machineNumber(list[j], "COMMAND_LONG param")
			if e != nil {
				return fail(e)
			}
			params[j] = q
		}
		frame, e := mavlinkCommandLong(v[0], v[1], v[2], v[3], v[4], v[5], v[6], params)
		if e != nil {
			return fail(e)
		}
		return frame, nil
	case "mavlink_common_decode_json":
		frame, e := machineBytes(args[0], "frame")
		if e != nil {
			return fail(e)
		}
		info, e := mavlinkCommonDecode(frame)
		if e != nil {
			return fail(e)
		}
		b, _ := json.Marshal(info)
		return string(b), nil
	case "mavlink_stream":
		return &droneMavlinkStream{}, nil
	case "mavlink_stream_feed_json":
		stream, ok := args[0].(*droneMavlinkStream)
		if !ok {
			return fail(fmt.Errorf("invalid MAVLink stream"))
		}
		data, e := machineBytes(args[1], "data")
		if e != nil {
			return fail(e)
		}
		b, _ := json.Marshal(stream.feed(data))
		return string(b), nil
	case "mavlink_stream_stats_json":
		stream, ok := args[0].(*droneMavlinkStream)
		if !ok {
			return fail(fmt.Errorf("invalid MAVLink stream"))
		}
		b, _ := json.Marshal(map[string]any{"buffered_bytes": len(stream.Buffer), "dropped_bytes": stream.DroppedBytes, "bad_frames": stream.BadFrames})
		return string(b), nil
	case "dshot_frame":
		throttle, e := num(0, "throttle")
		if e != nil {
			return fail(e)
		}
		telemetry, e := parseMachineBool(args[1], "telemetry")
		if e != nil {
			return fail(e)
		}
		word, e := droneDShotFrame(throttle, telemetry)
		if e != nil {
			return fail(e)
		}
		return numberFromInt64(int64(word)), nil
	case "trajectory3d":
		p, e := droneList3(args[0], "position")
		if e != nil {
			return fail(e)
		}
		tg, e := droneList3(args[1], "target")
		if e != nil {
			return fail(e)
		}
		mv, e := num(2, "max velocity")
		if e != nil {
			return fail(e)
		}
		ma, e := num(3, "max acceleration")
		if e != nil {
			return fail(e)
		}
		mj, e := num(4, "max jerk")
		if e != nil {
			return fail(e)
		}
		q, e := newDroneTrajectory3D(p, tg, mv, ma, mj)
		if e != nil {
			return fail(e)
		}
		return q, nil
	case "trajectory_retarget":
		q, ok := args[0].(*droneTrajectory3D)
		if !ok {
			return fail(fmt.Errorf("invalid trajectory"))
		}
		tg, e := droneList3(args[1], "target")
		if e != nil {
			return fail(e)
		}
		if e = q.retarget(tg); e != nil {
			return fail(e)
		}
		return nil, nil
	case "trajectory_step_json":
		q, ok := args[0].(*droneTrajectory3D)
		if !ok {
			return fail(fmt.Errorf("invalid trajectory"))
		}
		dt, e := num(1, "dt")
		if e != nil {
			return fail(e)
		}
		v, e := q.step(dt)
		if e != nil {
			return fail(e)
		}
		b, _ := json.Marshal(v)
		return string(b), nil
	case "trajectory_done":
		q, ok := args[0].(*droneTrajectory3D)
		if !ok {
			return fail(fmt.Errorf("invalid trajectory"))
		}
		return q.done(), nil
	case "quad_x_allocator":
		mn, e := num(0, "minimum")
		if e != nil {
			return fail(e)
		}
		mx, e := num(1, "maximum")
		if e != nil || mx <= mn {
			return fail(fmt.Errorf("invalid allocator range"))
		}
		return &droneAllocator{Matrix: [][4]float64{{1, 1, 1, -1}, {1, -1, 1, 1}, {1, -1, -1, -1}, {1, 1, -1, 1}}, Min: mn, Max: mx, Disabled: map[int]bool{}}, nil
	case "allocator":
		if len(args) != 3 {
			return fail(fmt.Errorf("allocator requires matrix, minimum, maximum"))
		}
		rows, ok := args[0].([]Value)
		if !ok || len(rows) < 4 {
			return fail(fmt.Errorf("allocator matrix requires at least four rows"))
		}
		matrix := make([][4]float64, len(rows))
		for r, raw := range rows {
			row, ok := raw.([]Value)
			if !ok || len(row) != 4 {
				return fail(fmt.Errorf("allocator row %d must contain four coefficients", r))
			}
			for c := 0; c < 4; c++ {
				v, err := machineNumber(row[c], "allocator coefficient")
				if err != nil || !finiteFloat(v) {
					return fail(fmt.Errorf("invalid allocator coefficient"))
				}
				matrix[r][c] = v
			}
		}
		mn, e := num(1, "minimum")
		if e != nil {
			return fail(e)
		}
		mx, e := num(2, "maximum")
		if e != nil || mx <= mn {
			return fail(fmt.Errorf("invalid allocator range"))
		}
		return &droneAllocator{Matrix: matrix, Min: mn, Max: mx, Disabled: map[int]bool{}}, nil
	case "allocator_disable":
		a, ok := args[0].(*droneAllocator)
		if !ok {
			return fail(fmt.Errorf("invalid allocator"))
		}
		list, ok := args[1].([]Value)
		if !ok {
			return fail(fmt.Errorf("disabled list required"))
		}
		a.Disabled = map[int]bool{}
		for _, v := range list {
			n, e := numberToInt(v)
			if e != nil || n < 0 || n >= len(a.Matrix) {
				return fail(fmt.Errorf("invalid actuator index"))
			}
			a.Disabled[n] = true
		}
		return nil, nil
	case "allocate":
		a, ok := args[0].(*droneAllocator)
		if !ok {
			return fail(fmt.Errorf("invalid allocator"))
		}
		list, ok := args[1].([]Value)
		if !ok || len(list) != 4 {
			return fail(fmt.Errorf("four-axis demand required"))
		}
		var d [4]float64
		for j := 0; j < 4; j++ {
			q, e := machineNumber(list[j], "demand")
			if e != nil {
				return fail(e)
			}
			d[j] = q
		}
		out, e := a.allocate(d)
		if e != nil {
			return fail(e)
		}
		return droneValues(out), nil
	case "allocation_report_json":
		a, ok := args[0].(*droneAllocator)
		if !ok {
			return fail(fmt.Errorf("invalid allocator"))
		}
		list, ok := args[1].([]Value)
		if !ok || len(list) != 4 {
			return fail(fmt.Errorf("four-axis demand required"))
		}
		var d [4]float64
		for j := 0; j < 4; j++ {
			q, e := machineNumber(list[j], "demand")
			if e != nil {
				return fail(e)
			}
			d[j] = q
		}
		report, e := a.report(d)
		if e != nil {
			return fail(e)
		}
		return report, nil
	case "link_monitor":
		alpha, e := num(0, "alpha")
		if e != nil || alpha <= 0 || alpha > 1 {
			return fail(fmt.Errorf("alpha must be in (0,1]"))
		}
		return &droneLinkMonitor{Alpha: alpha}, nil
	case "link_observe":
		m, ok := args[0].(*droneLinkMonitor)
		if !ok {
			return fail(fmt.Errorf("invalid link monitor"))
		}
		seq, e := intv(1, "sequence")
		if e != nil {
			return fail(e)
		}
		lat, e := num(2, "latency")
		if e != nil {
			return fail(e)
		}
		if e = m.observe(seq, lat); e != nil {
			return fail(e)
		}
		return nil, nil
	case "link_stats_json":
		m, ok := args[0].(*droneLinkMonitor)
		if !ok {
			return fail(fmt.Errorf("invalid link monitor"))
		}
		return m.stats(), nil
	case "pwm_esc_duty":
		v := make([]float64, 4)
		for j := range v {
			q, e := num(j, "ESC PWM")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		duty, e := dronePWMESCDuty(v[0], v[1], v[2], v[3])
		if e != nil {
			return fail(e)
		}
		return machineNumberFromFloat(duty), nil
	}
	return fail(fmt.Errorf("unknown function"))
}
