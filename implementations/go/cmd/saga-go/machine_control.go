package main

import (
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"
)

var machineMonotonicOrigin = time.Now()

type MachinePID struct {
	Kp, Ki, Kd               float64
	OutputMin, OutputMax     float64
	IntegralMin, IntegralMax float64
	Integral                 float64
	PreviousError            float64
	HasPrevious              bool
}

func newMachinePID(kp, ki, kd, outMin, outMax float64) (*MachinePID, error) {
	for name, v := range map[string]float64{"kp": kp, "ki": ki, "kd": kd, "output_min": outMin, "output_max": outMax} {
		if math.IsNaN(v) || math.IsInf(v, 0) {
			return nil, fmt.Errorf("%s must be finite", name)
		}
	}
	if outMin >= outMax {
		return nil, fmt.Errorf("PID output_min must be smaller than output_max")
	}
	return &MachinePID{Kp: kp, Ki: ki, Kd: kd, OutputMin: outMin, OutputMax: outMax, IntegralMin: outMin, IntegralMax: outMax}, nil
}

func (p *MachinePID) reset() { p.Integral, p.PreviousError, p.HasPrevious = 0, 0, false }
func (p *MachinePID) integralLimits(low, high float64) error {
	if math.IsNaN(low) || math.IsInf(low, 0) || math.IsNaN(high) || math.IsInf(high, 0) || low > high {
		return fmt.Errorf("invalid PID integral limits")
	}
	p.IntegralMin, p.IntegralMax = low, high
	p.Integral = clampFloat(p.Integral, low, high)
	return nil
}
func (p *MachinePID) step(setpoint, measurement, dt float64) (float64, error) {
	if dt <= 0 || math.IsNaN(dt) || math.IsInf(dt, 0) {
		return 0, fmt.Errorf("dt_seconds must be > 0")
	}
	if !finiteFloat(setpoint) || !finiteFloat(measurement) {
		return 0, fmt.Errorf("setpoint and measurement must be finite")
	}
	err := setpoint - measurement
	candidate := clampFloat(p.Integral+err*dt, p.IntegralMin, p.IntegralMax)
	derivative := 0.0
	if p.HasPrevious {
		derivative = (err - p.PreviousError) / dt
	}
	unclamped := p.Kp*err + p.Ki*candidate + p.Kd*derivative
	out := clampFloat(unclamped, p.OutputMin, p.OutputMax)
	pushingHigh := unclamped > p.OutputMax && err > 0
	pushingLow := unclamped < p.OutputMin && err < 0
	if !pushingHigh && !pushingLow {
		p.Integral = candidate
	}
	p.PreviousError, p.HasPrevious = err, true
	return out, nil
}

type MachinePID2 struct {
	Kp, Ki, Kd                          float64
	Beta, DerivativeTau, AntiwindupGain float64
	OutputMin, OutputMax                float64
	Integral, PreviousMeasurement       float64
	DerivativeState                     float64
	HasPrevious                         bool
}

func newMachinePID2(kp, ki, kd, beta, tau, kaw, outMin, outMax float64) (*MachinePID2, error) {
	values := []float64{kp, ki, kd, beta, tau, kaw, outMin, outMax}
	for _, v := range values {
		if !finiteFloat(v) {
			return nil, fmt.Errorf("PID2 parameters must be finite")
		}
	}
	if beta < 0 || beta > 1 {
		return nil, fmt.Errorf("PID beta must be in 0..1")
	}
	if tau < 0 {
		return nil, fmt.Errorf("PID derivative_tau must be >= 0")
	}
	if kaw < 0 {
		return nil, fmt.Errorf("PID antiwindup_gain must be >= 0")
	}
	if outMin >= outMax {
		return nil, fmt.Errorf("PID output_min must be smaller than output_max")
	}
	return &MachinePID2{Kp: kp, Ki: ki, Kd: kd, Beta: beta, DerivativeTau: tau, AntiwindupGain: kaw, OutputMin: outMin, OutputMax: outMax}, nil
}

func (p *MachinePID2) reset() {
	p.Integral, p.PreviousMeasurement, p.DerivativeState, p.HasPrevious = 0, 0, 0, false
}

func (p *MachinePID2) step(setpoint, measurement, feedforward, dt float64) (float64, error) {
	if dt <= 0 || !finiteFloat(dt) || !finiteFloat(setpoint) || !finiteFloat(measurement) || !finiteFloat(feedforward) {
		return 0, fmt.Errorf("PID2 inputs must be finite and dt_seconds > 0")
	}
	err := setpoint - measurement
	proportional := p.Kp * (p.Beta*setpoint - measurement)
	derivative := 0.0
	if p.HasPrevious {
		raw := -(measurement - p.PreviousMeasurement) / dt
		if p.DerivativeTau == 0 {
			p.DerivativeState = raw
		} else {
			alpha := dt / (p.DerivativeTau + dt)
			p.DerivativeState += alpha * (raw - p.DerivativeState)
		}
		derivative = p.DerivativeState
	}
	candidate := p.Integral + p.Ki*err*dt
	unclamped := proportional + candidate + p.Kd*derivative + feedforward
	out := clampFloat(unclamped, p.OutputMin, p.OutputMax)
	p.Integral = clampFloat(candidate+p.AntiwindupGain*(out-unclamped)*dt, p.OutputMin, p.OutputMax)
	p.PreviousMeasurement, p.HasPrevious = measurement, true
	return out, nil
}

type MachineAlphaBeta struct {
	Alpha, Beta        float64
	Position, Velocity float64
}

func newMachineAlphaBeta(alpha, beta, position, velocity float64) (*MachineAlphaBeta, error) {
	if !finiteFloat(alpha) || !finiteFloat(beta) || !finiteFloat(position) || !finiteFloat(velocity) {
		return nil, fmt.Errorf("alpha-beta parameters must be finite")
	}
	if alpha <= 0 || alpha > 1 {
		return nil, fmt.Errorf("alpha must be in (0,1]")
	}
	if beta < 0 || beta > 2 {
		return nil, fmt.Errorf("beta must be in 0..2")
	}
	return &MachineAlphaBeta{Alpha: alpha, Beta: beta, Position: position, Velocity: velocity}, nil
}

func (o *MachineAlphaBeta) reset(position, velocity float64) error {
	if !finiteFloat(position) || !finiteFloat(velocity) {
		return fmt.Errorf("observer state must be finite")
	}
	o.Position, o.Velocity = position, velocity
	return nil
}

func (o *MachineAlphaBeta) step(measurement, dt float64) (float64, float64, error) {
	if !finiteFloat(measurement) || dt <= 0 || !finiteFloat(dt) {
		return 0, 0, fmt.Errorf("observer measurement must be finite and dt_seconds > 0")
	}
	predicted := o.Position + o.Velocity*dt
	residual := measurement - predicted
	o.Position = predicted + o.Alpha*residual
	o.Velocity += (o.Beta / dt) * residual
	return o.Position, o.Velocity, nil
}

type MachineBiquad struct {
	B0, B1, B2 float64
	A1, A2     float64
	Z1, Z2     float64
}

func newMachineNotch(sampleHz, centerHz, q float64) (*MachineBiquad, error) {
	if sampleHz <= 0 || centerHz <= 0 || q <= 0 || !finiteFloat(sampleHz) || !finiteFloat(centerHz) || !finiteFloat(q) {
		return nil, fmt.Errorf("notch frequencies and q must be finite and > 0")
	}
	if centerHz >= sampleHz/2 {
		return nil, fmt.Errorf("notch center_hz must be below Nyquist")
	}
	omega := 2 * math.Pi * centerHz / sampleHz
	alpha := math.Sin(omega) / (2 * q)
	c := math.Cos(omega)
	a0 := 1 + alpha
	return &MachineBiquad{B0: 1 / a0, B1: -2 * c / a0, B2: 1 / a0, A1: -2 * c / a0, A2: (1 - alpha) / a0}, nil
}

func (f *MachineBiquad) reset() { f.Z1, f.Z2 = 0, 0 }
func (f *MachineBiquad) step(sample float64) (float64, error) {
	if !finiteFloat(sample) {
		return 0, fmt.Errorf("filter sample must be finite")
	}
	y := f.B0*sample + f.Z1
	f.Z1 = f.B1*sample - f.A1*y + f.Z2
	f.Z2 = f.B2*sample - f.A2*y
	return y, nil
}

type MachineControlGuard struct {
	RateHz, BudgetUS, StaleInputUS, MaxJitterUS                             int64
	Samples, BudgetMisses, StaleInputs, JitterViolations, InvalidTimestamps int64
	MaxExecutionUS, MaxAbsJitterUS, LastExecutionUS, LastAbsJitterUS        int64
	LastCycleOK                                                             bool
	previousStartNS, startedNS                                              int64
	previousValid, startedValid, precheckOK                                 bool
}

func newMachineControlGuard(rateHz, budgetUS, staleInputUS, maxJitterUS int) (*MachineControlGuard, error) {
	if rateHz <= 0 || rateHz > 1000000 {
		return nil, fmt.Errorf("control guard rate_hz must be in 1..1000000")
	}
	if budgetUS <= 0 || int64(budgetUS)*int64(rateHz) > 1000000 {
		return nil, fmt.Errorf("control guard budget_us must fit inside one period")
	}
	if staleInputUS < 0 || maxJitterUS < 0 {
		return nil, fmt.Errorf("control guard stale_input_us/max_jitter_us must be >= 0")
	}
	return &MachineControlGuard{RateHz: int64(rateHz), BudgetUS: int64(budgetUS), StaleInputUS: int64(staleInputUS), MaxJitterUS: int64(maxJitterUS), LastCycleOK: true}, nil
}
func (g *MachineControlGuard) periodNS() int64 { return 1000000000 / g.RateHz }
func (g *MachineControlGuard) begin(inputNS, nowNS int64) (bool, error) {
	if g.startedValid {
		return false, fmt.Errorf("control guard cycle already started")
	}
	if inputNS < 0 || nowNS < 0 {
		return false, fmt.Errorf("control guard timestamps must be >= 0")
	}
	ok := true
	if inputNS > nowNS {
		g.InvalidTimestamps++
		ok = false
	} else if nowNS-inputNS > g.StaleInputUS*1000 {
		g.StaleInputs++
		ok = false
	}
	g.LastAbsJitterUS = 0
	if g.previousValid {
		interval := nowNS - g.previousStartNS
		jitterNS := int64(0)
		if interval < 0 {
			g.InvalidTimestamps++
			ok = false
		} else {
			jitterNS = interval - g.periodNS()
			if jitterNS < 0 {
				jitterNS = -jitterNS
			}
		}
		g.LastAbsJitterUS = (jitterNS + 999) / 1000
		if g.LastAbsJitterUS > g.MaxAbsJitterUS {
			g.MaxAbsJitterUS = g.LastAbsJitterUS
		}
		if jitterNS > g.MaxJitterUS*1000 {
			g.JitterViolations++
			ok = false
		}
	}
	g.previousStartNS = nowNS
	g.previousValid = true
	g.startedNS = nowNS
	g.startedValid = true
	g.precheckOK = ok
	return ok, nil
}
func (g *MachineControlGuard) end(endNS int64) (bool, error) {
	if !g.startedValid {
		return false, fmt.Errorf("control guard cycle was not started")
	}
	elapsed := int64(0)
	ok := g.precheckOK
	if endNS < g.startedNS {
		g.InvalidTimestamps++
		ok = false
	} else {
		elapsed = endNS - g.startedNS
	}
	g.startedValid = false
	g.LastExecutionUS = (elapsed + 999) / 1000
	if g.LastExecutionUS > g.MaxExecutionUS {
		g.MaxExecutionUS = g.LastExecutionUS
	}
	g.Samples++
	if elapsed > g.BudgetUS*1000 {
		g.BudgetMisses++
		ok = false
	}
	g.LastCycleOK = ok
	return ok, nil
}
func (g *MachineControlGuard) ok() bool {
	return g.LastCycleOK && g.BudgetMisses == 0 && g.StaleInputs == 0 && g.JitterViolations == 0 && g.InvalidTimestamps == 0
}
func (g *MachineControlGuard) reset() {
	*g = MachineControlGuard{RateHz: g.RateHz, BudgetUS: g.BudgetUS, StaleInputUS: g.StaleInputUS, MaxJitterUS: g.MaxJitterUS, LastCycleOK: true}
}
func (g *MachineControlGuard) statsJSON() string {
	payload, _ := json.Marshal(map[string]any{"rate_hz": g.RateHz, "period_ns": g.periodNS(), "budget_us": g.BudgetUS, "stale_input_us": g.StaleInputUS, "max_jitter_us": g.MaxJitterUS, "samples": g.Samples, "budget_misses": g.BudgetMisses, "stale_inputs": g.StaleInputs, "jitter_violations": g.JitterViolations, "invalid_timestamps": g.InvalidTimestamps, "last_execution_us": g.LastExecutionUS, "max_execution_us": g.MaxExecutionUS, "last_abs_jitter_us": g.LastAbsJitterUS, "max_abs_jitter_us": g.MaxAbsJitterUS, "last_cycle_ok": g.LastCycleOK, "timing_class": "caller-clock-contract"})
	return string(payload)
}

type MachineDeadlineBudget struct {
	PeriodUS, BudgetUS          int64
	Samples, Violations         int64
	MaxElapsedUS, LastElapsedUS int64
	started                     time.Time
	startedValid                bool
}

func newMachineDeadlineBudget(periodUS, budgetUS int) (*MachineDeadlineBudget, error) {
	if periodUS <= 0 {
		return nil, fmt.Errorf("period_us must be > 0")
	}
	if budgetUS <= 0 || budgetUS > periodUS {
		return nil, fmt.Errorf("budget_us must be in 1..period_us")
	}
	return &MachineDeadlineBudget{PeriodUS: int64(periodUS), BudgetUS: int64(budgetUS)}, nil
}

func (b *MachineDeadlineBudget) begin() error {
	if b.startedValid {
		return fmt.Errorf("deadline budget sample already started")
	}
	b.started = time.Now()
	b.startedValid = true
	return nil
}

func (b *MachineDeadlineBudget) end() (bool, error) {
	if !b.startedValid {
		return false, fmt.Errorf("deadline budget sample was not started")
	}
	elapsedNS := time.Since(b.started).Nanoseconds()
	b.startedValid = false
	if elapsedNS < 0 {
		elapsedNS = 0
	}
	elapsedUS := (elapsedNS + 999) / 1000
	b.LastElapsedUS = elapsedUS
	if elapsedUS > b.MaxElapsedUS {
		b.MaxElapsedUS = elapsedUS
	}
	b.Samples++
	over := elapsedUS > b.BudgetUS
	if over {
		b.Violations++
	}
	return over, nil
}

func (b *MachineDeadlineBudget) reset() {
	b.Samples, b.Violations, b.MaxElapsedUS, b.LastElapsedUS = 0, 0, 0, 0
	b.startedValid = false
}

func (b *MachineDeadlineBudget) statsJSON() string {
	payload, _ := json.Marshal(map[string]any{
		"period_us": b.PeriodUS, "budget_us": b.BudgetUS, "samples": b.Samples,
		"violations": b.Violations, "last_elapsed_us": b.LastElapsedUS,
		"max_elapsed_us": b.MaxElapsedUS, "timing_class": "hosted-soft-realtime",
	})
	return string(payload)
}

func machineMotorFeedforward(ks, kv, ka, velocity, acceleration float64) (float64, error) {
	for _, v := range []float64{ks, kv, ka, velocity, acceleration} {
		if !finiteFloat(v) {
			return 0, fmt.Errorf("motor feedforward values must be finite")
		}
	}
	direction := 0.0
	if velocity > 0 || (velocity == 0 && acceleration > 0) {
		direction = 1
	}
	if velocity < 0 || (velocity == 0 && acceleration < 0) {
		direction = -1
	}
	return ks*direction + kv*velocity + ka*acceleration, nil
}

func machineClarke(ia, ib, ic float64) ([]float64, error) {
	if !finiteFloat(ia) || !finiteFloat(ib) || !finiteFloat(ic) {
		return nil, fmt.Errorf("phase currents must be finite")
	}
	alpha := (2.0 / 3.0) * (ia - ib/2 - ic/2)
	beta := (math.Sqrt(3) / 3.0) * (ib - ic)
	zero := (ia + ib + ic) / 3.0
	return []float64{alpha, beta, zero}, nil
}

func machinePark(alpha, beta, theta float64) ([]float64, error) {
	if !finiteFloat(alpha) || !finiteFloat(beta) || !finiteFloat(theta) {
		return nil, fmt.Errorf("Park inputs must be finite")
	}
	c, s := math.Cos(theta), math.Sin(theta)
	return []float64{alpha*c + beta*s, -alpha*s + beta*c}, nil
}

func machineInversePark(d, q, theta float64) ([]float64, error) {
	if !finiteFloat(d) || !finiteFloat(q) || !finiteFloat(theta) {
		return nil, fmt.Errorf("inverse Park inputs must be finite")
	}
	c, s := math.Cos(theta), math.Sin(theta)
	return []float64{d*c - q*s, d*s + q*c}, nil
}

func machineSVPWM(alpha, beta, bus float64) ([]float64, error) {
	if !finiteFloat(alpha) || !finiteFloat(beta) || bus <= 0 || !finiteFloat(bus) {
		return nil, fmt.Errorf("SVPWM inputs must be finite and bus_voltage > 0")
	}
	va := alpha
	vb := -alpha/2 + math.Sqrt(3)*beta/2
	vc := -alpha/2 - math.Sqrt(3)*beta/2
	maxV, minV := math.Max(va, math.Max(vb, vc)), math.Min(va, math.Min(vb, vc))
	offset := (maxV + minV) / 2
	return []float64{
		clampFloat(0.5+(va-offset)/bus, 0, 1),
		clampFloat(0.5+(vb-offset)/bus, 0, 1),
		clampFloat(0.5+(vc-offset)/bus, 0, 1),
	}, nil
}

type MachineProfile struct {
	Position, Velocity, Target   float64
	MaxVelocity, MaxAcceleration float64
	Tolerance                    float64
}

func newMachineProfile(position, velocity, target, maxVelocity, maxAcceleration float64) (*MachineProfile, error) {
	if !finiteFloat(position) || !finiteFloat(velocity) || !finiteFloat(target) || maxVelocity <= 0 || maxAcceleration <= 0 || !finiteFloat(maxVelocity) || !finiteFloat(maxAcceleration) {
		return nil, fmt.Errorf("invalid motion profile parameters")
	}
	return &MachineProfile{Position: position, Velocity: velocity, Target: target, MaxVelocity: maxVelocity, MaxAcceleration: maxAcceleration, Tolerance: 1e-6}, nil
}
func (p *MachineProfile) done() bool {
	return math.Abs(p.Target-p.Position) <= p.Tolerance && math.Abs(p.Velocity) <= p.Tolerance
}
func (p *MachineProfile) retarget(target float64) error {
	if !finiteFloat(target) {
		return fmt.Errorf("target must be finite")
	}
	p.Target = target
	return nil
}
func (p *MachineProfile) step(dt float64) (float64, error) {
	if dt <= 0 || !finiteFloat(dt) {
		return 0, fmt.Errorf("dt_seconds must be > 0")
	}
	distance := p.Target - p.Position
	if p.done() {
		p.Position, p.Velocity = p.Target, 0
		return p.Position, nil
	}
	dir := 1.0
	if distance < 0 {
		dir = -1
	}
	signedVelocity := p.Velocity * dir
	accel := p.MaxAcceleration * dir
	if signedVelocity >= 0 {
		braking := signedVelocity * signedVelocity / (2 * p.MaxAcceleration)
		if math.Abs(distance) <= braking {
			accel = -p.MaxAcceleration * dir
		}
	}
	nextVelocity := clampFloat(p.Velocity+accel*dt, -p.MaxVelocity, p.MaxVelocity)
	nextPosition := p.Position + (p.Velocity+nextVelocity)*dt/2
	if (p.Target-p.Position)*(p.Target-nextPosition) <= 0 {
		p.Position, p.Velocity = p.Target, 0
	} else {
		p.Position, p.Velocity = nextPosition, nextVelocity
	}
	return p.Position, nil
}

type MachineWatchdog struct {
	mu       sync.Mutex
	Timeout  time.Duration
	Deadline time.Time
}

func newMachineWatchdog(ms int) (*MachineWatchdog, error) {
	if ms <= 0 {
		return nil, fmt.Errorf("watchdog timeout_ms must be > 0")
	}
	w := &MachineWatchdog{Timeout: time.Duration(ms) * time.Millisecond}
	w.feed()
	return w, nil
}
func (w *MachineWatchdog) feed() {
	w.mu.Lock()
	w.Deadline = time.Now().Add(w.Timeout)
	w.mu.Unlock()
}
func (w *MachineWatchdog) expired() bool {
	now := time.Now()
	w.mu.Lock()
	deadline := w.Deadline
	w.mu.Unlock()
	return !now.Before(deadline)
}
func (w *MachineWatchdog) remainingMS() int64 {
	now := time.Now()
	w.mu.Lock()
	deadline := w.Deadline
	w.mu.Unlock()
	d := deadline.Sub(now)
	if d <= 0 {
		return 0
	}
	return (d.Nanoseconds() + 999999) / 1_000_000
}

type machineCANFrame struct {
	received        bool
	id              int
	data            []byte
	flags           byte
	timestampNS     int64
	timestampSource string
}

type MachineEncoder struct {
	CountsPerRevolution int
	GearRatio           float64
	Count               int64
	PositionDegrees     float64
	VelocityRPM         float64
	LastCount           int64
	LastTimeNS          int64
	UnwrappedCount      int64
	WrapModulus         int64
	HasPrevious         bool
}

func newMachineEncoder(cpr int, gear float64) (*MachineEncoder, error) {
	if cpr <= 0 {
		return nil, fmt.Errorf("encoder counts_per_revolution must be > 0")
	}
	if gear <= 0 || !finiteFloat(gear) {
		return nil, fmt.Errorf("encoder gear_ratio must be > 0")
	}
	return &MachineEncoder{CountsPerRevolution: cpr, GearRatio: gear}, nil
}
func (e *MachineEncoder) wrapDelta(delta int64) int64 {
	if e.WrapModulus <= 1 {
		return delta
	}
	half := e.WrapModulus / 2
	if delta > half {
		return delta - e.WrapModulus
	}
	if delta < -half {
		return delta + e.WrapModulus
	}
	return delta
}
func (e *MachineEncoder) setWrapModulus(modulus int64) error {
	if modulus <= 1 {
		return fmt.Errorf("encoder wrap modulus must be > 1")
	}
	e.WrapModulus = modulus
	return nil
}
func (e *MachineEncoder) update(count int64, timestampNS int64) error {
	if timestampNS < 0 {
		return fmt.Errorf("encoder timestamp_ns must be >= 0")
	}
	effective := float64(e.CountsPerRevolution) * e.GearRatio
	e.Count = count
	if !e.HasPrevious {
		e.UnwrappedCount = count
	} else if e.WrapModulus > 1 {
		e.UnwrappedCount += e.wrapDelta(count - e.LastCount)
	} else {
		e.UnwrappedCount = count
	}
	e.PositionDegrees = float64(e.UnwrappedCount) * 360 / effective
	if e.HasPrevious {
		dt := timestampNS - e.LastTimeNS
		if dt <= 0 {
			return fmt.Errorf("encoder timestamps must increase")
		}
		delta := e.wrapDelta(count - e.LastCount)
		e.VelocityRPM = float64(delta) * 60_000_000_000 / (effective * float64(dt))
	}
	e.LastCount = count
	e.LastTimeNS = timestampNS
	e.HasPrevious = true
	return nil
}
func (e *MachineEncoder) reset(count int64) {
	e.Count = count
	e.UnwrappedCount = count
	e.PositionDegrees = 0
	e.VelocityRPM = 0
	e.LastCount = 0
	e.LastTimeNS = 0
	e.HasPrevious = false
}

type MachineSafety struct {
	mu             sync.Mutex
	Tripped        bool
	Reason         string
	stoppers       []func() error
	tripInProgress bool
}

func (s *MachineSafety) registerStop(stopper func() error) error {
	s.mu.Lock()
	s.stoppers = append(s.stoppers, stopper)
	alreadyTripped := s.Tripped
	s.mu.Unlock()
	if alreadyTripped {
		if err := stopper(); err != nil {
			return fmt.Errorf("failed to place newly guarded actuator in a safe state: %w", err)
		}
	}
	return nil
}
func (s *MachineSafety) trip(reason string) error {
	s.mu.Lock()
	s.Tripped = true
	s.Reason = strings.TrimSpace(reason)
	if s.Reason == "" {
		s.Reason = "unspecified safety trip"
	}
	s.tripInProgress = true
	stoppers := append([]func() error(nil), s.stoppers...)
	s.mu.Unlock()
	failures := []string{}
	for _, stop := range stoppers {
		if err := stop(); err != nil {
			failures = append(failures, err.Error())
		}
	}
	s.mu.Lock()
	s.tripInProgress = false
	s.mu.Unlock()
	if len(failures) > 0 {
		return fmt.Errorf("safety trip requested but one or more actuator stops failed: %s", strings.Join(failures, "; "))
	}
	return nil
}
func (s *MachineSafety) clear() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.tripInProgress {
		return fmt.Errorf("cannot clear a safety latch while a trip is stopping actuators")
	}
	s.Tripped = false
	s.Reason = ""
	return nil
}
func (s *MachineSafety) snapshot() (bool, string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.Tripped, s.Reason
}

type MachineCycle struct {
	Period       time.Duration
	Next         time.Time
	Anchor       time.Time
	FrequencyHz  int64
	Overruns     int64
	LastJitterUS int64
	Cycles       int64
	WaitCalls    int64
	LastDue      int64
	MaxDue       int64
}

func newMachineCycle(periodUS int) (*MachineCycle, error) {
	if periodUS <= 0 {
		return nil, fmt.Errorf("control cycle period_us must be > 0")
	}
	return newMachineCycleDuration(time.Duration(periodUS) * time.Microsecond)
}

func newMachineCycleHz(frequencyHz int) (*MachineCycle, error) {
	if frequencyHz < 1 || frequencyHz > 1000000 {
		return nil, fmt.Errorf("cycle frequency must be 1..1000000 Hz")
	}
	rate := int64(frequencyHz)
	p := time.Duration(int64(time.Second) / rate)
	if p <= 0 {
		return nil, fmt.Errorf("cycle period is too small")
	}
	now := time.Now()
	c := &MachineCycle{Period: p, Anchor: now, FrequencyHz: rate}
	c.Next = c.deadlineForCycle(1)
	return c, nil
}

func newMachineCycleDuration(p time.Duration) (*MachineCycle, error) {
	if p <= 0 {
		return nil, fmt.Errorf("control cycle period must be > 0")
	}
	now := time.Now()
	return &MachineCycle{Period: p, Anchor: now, Next: now.Add(p)}, nil
}

func (c *MachineCycle) deadlineForCycle(cycle int64) time.Time {
	if cycle <= 0 {
		return c.Anchor
	}
	if c.FrequencyHz <= 0 {
		return c.Anchor.Add(time.Duration(cycle) * c.Period)
	}
	wholeSeconds := cycle / c.FrequencyHz
	remainderCycles := cycle % c.FrequencyHz
	fractionalNS := (remainderCycles*int64(time.Second) + c.FrequencyHz - 1) / c.FrequencyHz
	return c.Anchor.Add(time.Duration(wholeSeconds)*time.Second + time.Duration(fractionalNS))
}

func (c *MachineCycle) dueCycle(now time.Time) int64 {
	if c.FrequencyHz <= 0 {
		if !now.After(c.Anchor) {
			return 0
		}
		return int64(now.Sub(c.Anchor) / c.Period)
	}
	elapsed := now.Sub(c.Anchor)
	if elapsed <= 0 {
		return 0
	}
	wholeSeconds := int64(elapsed / time.Second)
	remainderNS := int64(elapsed % time.Second)
	return wholeSeconds*c.FrequencyHz + remainderNS*c.FrequencyHz/int64(time.Second)
}

func (c *MachineCycle) waitDue() int64 {
	// Sleep for the coarse portion, then spin over only the final short guard.
	// Frequency-mode deadlines come from the rational phase, never from adding
	// a rounded period repeatedly.
	guard := 80 * time.Microsecond
	if c.Period/3 < guard {
		guard = c.Period / 3
	}
	if guard < 2*time.Microsecond {
		guard = 2 * time.Microsecond
	}
	for {
		remaining := time.Until(c.Next)
		if remaining <= 0 {
			break
		}
		if remaining > guard {
			time.Sleep(remaining - guard)
		}
	}
	now := time.Now()
	due := int64(1)
	if c.FrequencyHz > 0 {
		if candidate := c.dueCycle(now) - c.Cycles; candidate > due {
			due = candidate
		}
	} else {
		late := now.Sub(c.Next)
		if late >= c.Period {
			due += int64(late / c.Period)
		}
	}
	expectedLatest := c.deadlineForCycle(c.Cycles + due)
	late := now.Sub(expectedLatest)
	if late < 0 {
		late = 0
	}
	c.LastJitterUS = late.Microseconds()
	c.LastDue = due
	if due > c.MaxDue {
		c.MaxDue = due
	}
	c.WaitCalls++
	c.Cycles += due
	if due > 1 {
		c.Overruns += due - 1
	}
	c.Next = c.deadlineForCycle(c.Cycles + 1)
	return due
}

func (c *MachineCycle) wait() { _ = c.waitDue() }

func (c *MachineCycle) statsJSON() string {
	periodUS := float64(c.Period) / float64(time.Microsecond)
	frequency := float64(time.Second) / float64(c.Period)
	phaseModel := "fixed-duration"
	if c.FrequencyHz > 0 {
		frequency = float64(c.FrequencyHz)
		periodUS = 1000000.0 / frequency
		phaseModel = "exact-rational-frequency"
	}
	b, _ := json.Marshal(map[string]any{
		"frequency_hz":   frequency,
		"period_us":      periodUS,
		"period_ns_floor": int64(c.Period),
		"cycles":         c.Cycles,
		"wait_calls":     c.WaitCalls,
		"overruns":       c.Overruns,
		"last_due":       c.LastDue,
		"max_due":        c.MaxDue,
		"last_jitter_us": c.LastJitterUS,
		"backend":        "go-absolute-sleep-spin",
		"phase_model":    phaseModel,
		"timing_class":   "hosted-soft-realtime",
	})
	return string(b)
}

func finiteFloat(v float64) bool { return !math.IsNaN(v) && !math.IsInf(v, 0) }
func clampFloat(v, low, high float64) float64 {
	if v < low {
		return low
	}
	if v > high {
		return high
	}
	return v
}
func machineNumber(v Value, name string) (float64, error) {
	f, err := numberToFloat(v)
	if err != nil {
		return 0, fmt.Errorf("%s must be numeric: %w", name, err)
	}
	if !finiteFloat(f) {
		return 0, fmt.Errorf("%s must be finite", name)
	}
	return f, nil
}
func machineInt(v Value, name string) (int, error) {
	n, err := numberToInt(v)
	if err != nil {
		return 0, fmt.Errorf("%s must be int: %w", name, err)
	}
	return n, nil
}
func machineText(v Value, name string) (string, error) {
	s, ok := v.(string)
	if !ok {
		return "", fmt.Errorf("%s must be text", name)
	}
	return s, nil
}
func machineBytes(v Value, name string) ([]byte, error) {
	b, ok := v.([]byte)
	if !ok {
		return nil, fmt.Errorf("%s must be bytes", name)
	}
	return b, nil
}

func machineSlew(current, target, rate, dt float64) (float64, error) {
	if !finiteFloat(current) || !finiteFloat(target) || rate <= 0 || dt <= 0 || !finiteFloat(rate) || !finiteFloat(dt) {
		return 0, fmt.Errorf("invalid slew parameters")
	}
	delta := target - current
	limit := rate * dt
	if math.Abs(delta) <= limit {
		return target, nil
	}
	if delta > 0 {
		return current + limit, nil
	}
	return current - limit, nil
}
func machineLowPass(previous, sample, alpha float64) (float64, error) {
	if !finiteFloat(previous) || !finiteFloat(sample) || !finiteFloat(alpha) {
		return 0, fmt.Errorf("low-pass arguments must be finite")
	}
	if alpha < 0 || alpha > 1 {
		return 0, fmt.Errorf("low-pass alpha must be in 0..1")
	}
	return previous + alpha*(sample-previous), nil
}

func machineDeadband(value, width float64) (float64, error) {
	if !finiteFloat(value) || !finiteFloat(width) || width < 0 {
		return 0, fmt.Errorf("deadband requires finite value and width >= 0")
	}
	if math.Abs(value) <= width {
		return 0, nil
	}
	if value > 0 {
		return value - width, nil
	}
	return value + width, nil
}

func machineIntegrateClamped(previous, input, dt, low, high float64) (float64, error) {
	for _, value := range []float64{previous, input, dt, low, high} {
		if !finiteFloat(value) {
			return 0, fmt.Errorf("integrator arguments must be finite")
		}
	}
	if dt <= 0 {
		return 0, fmt.Errorf("integrator dt must be > 0")
	}
	if low > high {
		return 0, fmt.Errorf("integrator low must not exceed high")
	}
	return clampFloat(previous+input*dt, low, high), nil
}

const (
	machineQ31Min   int64 = -(1 << 31)
	machineQ31Max   int64 = (1 << 31) - 1
	machineQ31Scale int64 = 1 << 31
)

func machineQ31Operand(name string, value int64) (int64, error) {
	if value < machineQ31Min || value > machineQ31Max {
		return 0, fmt.Errorf("%s must be in Q1.31 range", name)
	}
	return value, nil
}

func machineQ31FromRatio(numerator, denominator int64) (int64, error) {
	if _, err := machineQ31Operand("q31 numerator", numerator); err != nil {
		return 0, err
	}
	if denominator <= 0 || denominator > machineQ31Max {
		return 0, fmt.Errorf("q31 denominator must be in 1..2147483647")
	}
	if numerator >= denominator {
		return machineQ31Max, nil
	}
	if numerator <= -denominator {
		return machineQ31Min, nil
	}
	return numerator * machineQ31Scale / denominator, nil
}

func machineQ31AddSat(left, right int64) (int64, error) {
	if _, err := machineQ31Operand("q31 left", left); err != nil {
		return 0, err
	}
	if _, err := machineQ31Operand("q31 right", right); err != nil {
		return 0, err
	}
	sum := left + right
	if sum > machineQ31Max {
		return machineQ31Max, nil
	}
	if sum < machineQ31Min {
		return machineQ31Min, nil
	}
	return sum, nil
}

func machineQ31SubSat(left, right int64) (int64, error) {
	if _, err := machineQ31Operand("q31 left", left); err != nil {
		return 0, err
	}
	if _, err := machineQ31Operand("q31 right", right); err != nil {
		return 0, err
	}
	diff := left - right
	if diff > machineQ31Max {
		return machineQ31Max, nil
	}
	if diff < machineQ31Min {
		return machineQ31Min, nil
	}
	return diff, nil
}

func machineQ31MulSat(left, right int64) (int64, error) {
	if _, err := machineQ31Operand("q31 left", left); err != nil {
		return 0, err
	}
	if _, err := machineQ31Operand("q31 right", right); err != nil {
		return 0, err
	}
	scaled := left * right / machineQ31Scale
	if scaled > machineQ31Max {
		return machineQ31Max, nil
	}
	if scaled < machineQ31Min {
		return machineQ31Min, nil
	}
	return scaled, nil
}

func machineQ31MacSat(accumulator, left, right int64) (int64, error) {
	if _, err := machineQ31Operand("q31 accumulator", accumulator); err != nil {
		return 0, err
	}
	product, err := machineQ31MulSat(left, right)
	if err != nil {
		return 0, err
	}
	return machineQ31AddSat(accumulator, product)
}

func machineServoDuty(deg, minDeg, maxDeg, minUS, maxUS, periodUS float64) (float64, error) {
	for _, v := range []float64{deg, minDeg, maxDeg, minUS, maxUS, periodUS} {
		if !finiteFloat(v) {
			return 0, fmt.Errorf("servo parameters must be finite")
		}
	}
	if minDeg >= maxDeg || minUS >= maxUS || periodUS <= 0 {
		return 0, fmt.Errorf("invalid servo range")
	}
	deg = clampFloat(deg, minDeg, maxDeg)
	ratio := (deg - minDeg) / (maxDeg - minDeg)
	pulse := minUS + ratio*(maxUS-minUS)
	return pulse / periodUS, nil
}

type MachineSCurve struct {
	Position, Velocity, Acceleration, Target float64
	MaxVelocity, MaxAcceleration, MaxJerk    float64
	Tolerance                                float64
}

func newMachineSCurve(position, velocity, acceleration, target, maxVelocity, maxAcceleration, maxJerk float64) (*MachineSCurve, error) {
	for name, value := range map[string]float64{"position": position, "velocity": velocity, "acceleration": acceleration, "target": target, "max_velocity": maxVelocity, "max_acceleration": maxAcceleration, "max_jerk": maxJerk} {
		if !finiteFloat(value) {
			return nil, fmt.Errorf("%s must be finite", name)
		}
	}
	if maxVelocity <= 0 || maxAcceleration <= 0 || maxJerk <= 0 {
		return nil, fmt.Errorf("S-curve limits must be > 0")
	}
	return &MachineSCurve{Position: position, Velocity: velocity, Acceleration: acceleration, Target: target, MaxVelocity: maxVelocity, MaxAcceleration: maxAcceleration, MaxJerk: maxJerk, Tolerance: 1e-6}, nil
}
func (p *MachineSCurve) done() bool {
	return math.Abs(p.Target-p.Position) <= p.Tolerance && math.Abs(p.Velocity) <= p.Tolerance && math.Abs(p.Acceleration) <= p.Tolerance
}
func (p *MachineSCurve) retarget(target float64) error {
	if !finiteFloat(target) {
		return fmt.Errorf("target must be finite")
	}
	p.Target = target
	return nil
}
func (p *MachineSCurve) step(dt float64) (float64, error) {
	if dt <= 0 || !finiteFloat(dt) {
		return 0, fmt.Errorf("dt_seconds must be > 0")
	}
	distance := p.Target - p.Position
	if p.done() {
		p.Position, p.Velocity, p.Acceleration = p.Target, 0, 0
		return p.Position, nil
	}
	direction := 1.0
	if distance < 0 {
		direction = -1
	}
	speed := p.Velocity * direction
	braking := 0.0
	if speed > 0 {
		braking = speed * speed / (2 * p.MaxAcceleration)
	}
	desired := p.MaxAcceleration * direction
	if math.Abs(distance) <= braking {
		desired = -p.MaxAcceleration * direction
	}
	maxDA := p.MaxJerk * dt
	deltaA := clampFloat(desired-p.Acceleration, -maxDA, maxDA)
	nextA := clampFloat(p.Acceleration+deltaA, -p.MaxAcceleration, p.MaxAcceleration)
	nextV := clampFloat(p.Velocity+(p.Acceleration+nextA)*dt/2, -p.MaxVelocity, p.MaxVelocity)
	nextP := p.Position + (p.Velocity+nextV)*dt/2
	if (p.Target-p.Position)*(p.Target-nextP) <= 0 {
		p.Position, p.Velocity, p.Acceleration = p.Target, 0, 0
	} else {
		p.Position, p.Velocity, p.Acceleration = nextP, nextV, nextA
	}
	return p.Position, nil
}

type MachineAxis struct {
	MinPosition, MaxPosition, MaxFollowingError float64
	Profile                                     *MachineProfile
	PID                                         *MachinePID
	Safety                                      *MachineSafety
	Command                                     float64
}

func newMachineAxis(position, minPosition, maxPosition, maxVelocity, maxAcceleration, kp, ki, kd, maxFollowingError float64, safety *MachineSafety) (*MachineAxis, error) {
	if safety == nil {
		return nil, fmt.Errorf("safety latch required")
	}
	if !finiteFloat(position) || !finiteFloat(minPosition) || !finiteFloat(maxPosition) || minPosition >= maxPosition {
		return nil, fmt.Errorf("invalid axis position limits")
	}
	if position < minPosition || position > maxPosition {
		return nil, fmt.Errorf("axis initial position is outside soft limits")
	}
	if maxFollowingError <= 0 || !finiteFloat(maxFollowingError) {
		return nil, fmt.Errorf("max_following_error must be > 0")
	}
	profile, e := newMachineProfile(position, 0, position, maxVelocity, maxAcceleration)
	if e != nil {
		return nil, e
	}
	pid, e := newMachinePID(kp, ki, kd, -1, 1)
	if e != nil {
		return nil, e
	}
	a := &MachineAxis{MinPosition: minPosition, MaxPosition: maxPosition, MaxFollowingError: maxFollowingError, Profile: profile, PID: pid, Safety: safety}
	if e = safety.registerStop(func() error { a.stop(); return nil }); e != nil {
		return nil, e
	}
	return a, nil
}
func (a *MachineAxis) stop() { a.Command = 0; a.Profile.Velocity = 0 }
func (a *MachineAxis) setTarget(target float64) error {
	if !finiteFloat(target) || target < a.MinPosition || target > a.MaxPosition {
		return fmt.Errorf("axis target is outside soft limits")
	}
	tripped, reason := a.Safety.snapshot()
	if tripped {
		return fmt.Errorf("axis target blocked by safety latch: %s", reason)
	}
	return a.Profile.retarget(target)
}
func (a *MachineAxis) step(measurement, dt float64) (float64, error) {
	if !finiteFloat(measurement) {
		return 0, fmt.Errorf("axis measurement must be finite")
	}
	tripped, reason := a.Safety.snapshot()
	if tripped {
		a.stop()
		return 0, fmt.Errorf("axis output blocked by safety latch: %s", reason)
	}
	if measurement < a.MinPosition || measurement > a.MaxPosition {
		_ = a.Safety.trip("axis soft limit exceeded")
		a.stop()
		return 0, fmt.Errorf("axis measurement exceeded soft limits")
	}
	planned, e := a.Profile.step(dt)
	if e != nil {
		return 0, e
	}
	if math.Abs(planned-measurement) > a.MaxFollowingError {
		_ = a.Safety.trip("axis following error exceeded")
		a.stop()
		return 0, fmt.Errorf("axis following error exceeded")
	}
	cmd, e := a.PID.step(planned, measurement, dt)
	if e != nil {
		return 0, e
	}
	a.Command = cmd
	return cmd, nil
}
func (a *MachineAxis) done(measurement float64) bool {
	return a.Profile.done() && math.Abs(a.Profile.Target-measurement) <= a.Profile.Tolerance
}

func machineModbusCRC16(payload []byte) uint16 {
	crc := uint16(0xffff)
	for _, b := range payload {
		crc ^= uint16(b)
		for j := 0; j < 8; j++ {
			if crc&1 != 0 {
				crc = (crc >> 1) ^ 0xa001
			} else {
				crc >>= 1
			}
		}
	}
	return crc
}
func machineModbusU16(name string, v int) (uint16, error) {
	if v < 0 || v > 65535 {
		return 0, fmt.Errorf("%s must be 0..65535", name)
	}
	return uint16(v), nil
}
func machineModbusCount(name string, v, max int) (int, error) {
	if v < 1 || v > max {
		return 0, fmt.Errorf("%s must be 1..%d", name, max)
	}
	return v, nil
}
func machineModbusException(function, response, code byte) error {
	names := map[byte]string{1: "illegal function", 2: "illegal data address", 3: "illegal data value", 4: "server device failure", 5: "acknowledge", 6: "server device busy", 8: "memory parity error", 10: "gateway path unavailable", 11: "gateway target failed to respond"}
	detail := names[code]
	if detail == "" {
		detail = fmt.Sprintf("exception %d", code)
	}
	return fmt.Errorf("Modbus function 0x%02x failed with response 0x%02x: %s", function, response, detail)
}
func machineParseRegisters(function byte, pdu []byte, count int) ([]Value, error) {
	if len(pdu) >= 2 && pdu[0] == function|0x80 {
		return nil, machineModbusException(function, pdu[0], pdu[1])
	}
	expected := count * 2
	if len(pdu) != 2+expected || pdu[0] != function || int(pdu[1]) != expected {
		return nil, fmt.Errorf("malformed Modbus register response")
	}
	out := make([]Value, 0, count)
	for j := 0; j < expected; j += 2 {
		out = append(out, numberFromInt64(int64(binary.BigEndian.Uint16(pdu[2+j:4+j]))))
	}
	return out, nil
}
func machineParseCoils(function byte, pdu []byte, count int) ([]Value, error) {
	if len(pdu) >= 2 && pdu[0] == function|0x80 {
		return nil, machineModbusException(function, pdu[0], pdu[1])
	}
	expected := (count + 7) / 8
	if len(pdu) != 2+expected || pdu[0] != function || int(pdu[1]) != expected {
		return nil, fmt.Errorf("malformed Modbus coil response")
	}
	out := make([]Value, count)
	for j := 0; j < count; j++ {
		out[j] = pdu[2+j/8]&(1<<uint(j%8)) != 0
	}
	return out, nil
}

type machineModbusMaster interface {
	readHolding(int, int) ([]Value, error)
	readInput(int, int) ([]Value, error)
	readCoils(int, int) ([]Value, error)
	writeRegister(int, int) error
	writeRegisters(int, []int) error
	writeCoil(int, bool) error
	sagaMachineClose() error
}

type machineModbusTCP struct {
	conn    net.Conn
	unit    byte
	tx      uint16
	timeout time.Duration
	mu      sync.Mutex
	closed  bool
}

func openMachineModbusTCP(host string, port, timeoutMS, unit int) (*machineModbusTCP, error) {
	if unit < 1 || unit > 247 {
		return nil, fmt.Errorf("Modbus TCP unit_id must be 1..247")
	}
	if port < 1 || port > 65535 {
		return nil, fmt.Errorf("Modbus TCP port must be 1..65535")
	}
	if timeoutMS <= 0 {
		return nil, fmt.Errorf("Modbus TCP timeout_ms must be > 0")
	}
	c, e := net.DialTimeout("tcp", net.JoinHostPort(host, strconv.Itoa(port)), time.Duration(timeoutMS)*time.Millisecond)
	if e != nil {
		return nil, e
	}
	timeout := time.Duration(timeoutMS) * time.Millisecond
	return &machineModbusTCP{conn: c, unit: byte(unit), timeout: timeout}, nil
}
func (d *machineModbusTCP) transact(function byte, data []byte) ([]byte, error) {
	if d.closed {
		return nil, fmt.Errorf("Modbus TCP master is closed")
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	d.tx++
	if err := d.conn.SetDeadline(time.Now().Add(d.timeout)); err != nil {
		return nil, err
	}
	pdu := append([]byte{function}, data...)
	header := make([]byte, 7)
	binary.BigEndian.PutUint16(header[0:2], d.tx)
	binary.BigEndian.PutUint16(header[2:4], 0)
	binary.BigEndian.PutUint16(header[4:6], uint16(len(pdu)+1))
	header[6] = d.unit
	if _, e := d.conn.Write(append(header, pdu...)); e != nil {
		return nil, e
	}
	rh := make([]byte, 7)
	if _, e := io.ReadFull(d.conn, rh); e != nil {
		return nil, e
	}
	if binary.BigEndian.Uint16(rh[0:2]) != d.tx || binary.BigEndian.Uint16(rh[2:4]) != 0 || rh[6] != d.unit {
		return nil, fmt.Errorf("Modbus TCP MBAP header mismatch")
	}
	length := int(binary.BigEndian.Uint16(rh[4:6]))
	if length < 2 || length > 254 {
		return nil, fmt.Errorf("Modbus TCP response length is outside 2..254")
	}
	rpdu := make([]byte, length-1)
	_, e := io.ReadFull(d.conn, rpdu)
	return rpdu, e
}
func (d *machineModbusTCP) readHolding(address, count int) ([]Value, error) {
	a, e := machineModbusU16("Modbus address", address)
	if e != nil {
		return nil, e
	}
	c, e := machineModbusCount("Modbus register count", count, 125)
	if e != nil {
		return nil, e
	}
	buf := make([]byte, 4)
	binary.BigEndian.PutUint16(buf, a)
	binary.BigEndian.PutUint16(buf[2:], uint16(c))
	p, e := d.transact(3, buf)
	if e != nil {
		return nil, e
	}
	return machineParseRegisters(3, p, c)
}
func (d *machineModbusTCP) readInput(address, count int) ([]Value, error) {
	a, e := machineModbusU16("Modbus address", address)
	if e != nil {
		return nil, e
	}
	c, e := machineModbusCount("Modbus register count", count, 125)
	if e != nil {
		return nil, e
	}
	buf := make([]byte, 4)
	binary.BigEndian.PutUint16(buf, a)
	binary.BigEndian.PutUint16(buf[2:], uint16(c))
	p, e := d.transact(4, buf)
	if e != nil {
		return nil, e
	}
	return machineParseRegisters(4, p, c)
}
func (d *machineModbusTCP) readCoils(address, count int) ([]Value, error) {
	a, e := machineModbusU16("Modbus address", address)
	if e != nil {
		return nil, e
	}
	c, e := machineModbusCount("Modbus coil count", count, 2000)
	if e != nil {
		return nil, e
	}
	buf := make([]byte, 4)
	binary.BigEndian.PutUint16(buf, a)
	binary.BigEndian.PutUint16(buf[2:], uint16(c))
	p, e := d.transact(1, buf)
	if e != nil {
		return nil, e
	}
	return machineParseCoils(1, p, c)
}
func (d *machineModbusTCP) writeRegister(address, value int) error {
	a, e := machineModbusU16("Modbus address", address)
	if e != nil {
		return e
	}
	v, e := machineModbusU16("Modbus register value", value)
	if e != nil {
		return e
	}
	req := make([]byte, 4)
	binary.BigEndian.PutUint16(req, a)
	binary.BigEndian.PutUint16(req[2:], v)
	p, e := d.transact(6, req)
	if e != nil {
		return e
	}
	if len(p) >= 2 && p[0] == 0x86 {
		return machineModbusException(6, p[0], p[1])
	}
	if len(p) != 5 || p[0] != 6 || !equalBytes(p[1:], req) {
		return fmt.Errorf("malformed Modbus write-register response")
	}
	return nil
}
func (d *machineModbusTCP) writeRegisters(address int, values []int) error {
	a, e := machineModbusU16("Modbus address", address)
	if e != nil {
		return e
	}
	if len(values) < 1 || len(values) > 123 {
		return fmt.Errorf("Modbus register values must contain 1..123 entries")
	}
	req := make([]byte, 5+2*len(values))
	binary.BigEndian.PutUint16(req, a)
	binary.BigEndian.PutUint16(req[2:], uint16(len(values)))
	req[4] = byte(2 * len(values))
	for j, v := range values {
		q, e := machineModbusU16("Modbus register value", v)
		if e != nil {
			return e
		}
		binary.BigEndian.PutUint16(req[5+2*j:], q)
	}
	p, e := d.transact(0x10, req)
	if e != nil {
		return e
	}
	if len(p) >= 2 && p[0] == 0x90 {
		return machineModbusException(0x10, p[0], p[1])
	}
	if len(p) != 5 || p[0] != 0x10 || binary.BigEndian.Uint16(p[1:3]) != a || binary.BigEndian.Uint16(p[3:5]) != uint16(len(values)) {
		return fmt.Errorf("malformed Modbus write-multiple response")
	}
	return nil
}
func (d *machineModbusTCP) writeCoil(address int, state bool) error {
	a, e := machineModbusU16("Modbus address", address)
	if e != nil {
		return e
	}
	req := make([]byte, 4)
	binary.BigEndian.PutUint16(req, a)
	if state {
		binary.BigEndian.PutUint16(req[2:], 0xff00)
	}
	p, e := d.transact(5, req)
	if e != nil {
		return e
	}
	if len(p) >= 2 && p[0] == 0x85 {
		return machineModbusException(5, p[0], p[1])
	}
	if len(p) != 5 || p[0] != 5 || !equalBytes(p[1:], req) {
		return fmt.Errorf("malformed Modbus write-coil response")
	}
	return nil
}
func (d *machineModbusTCP) sagaMachineClose() error {
	if d.closed {
		return nil
	}
	d.closed = true
	return d.conn.Close()
}
func (i *Interpreter) requireNet(host string, port int) error {
	if len(i.NetHosts) == 0 {
		return &SagaError{Code: "SAGA-R103", ID: "SAGA-R103", Message: fmt.Sprintf("network access denied: %s:%d; use --allow-net host[:port]", host, port)}
	}
	normalized := strings.ToLower(strings.TrimSpace(strings.TrimSuffix(host, ".")))
	for _, raw := range i.NetHosts {
		entry := strings.ToLower(strings.TrimSpace(raw))
		if entry == "*" {
			return nil
		}
		allowedHost, allowedPort := entry, -1
		if h, p, e := net.SplitHostPort(entry); e == nil {
			allowedHost = strings.TrimSuffix(strings.Trim(h, "[]"), ".")
			if q, e := strconv.Atoi(p); e == nil {
				allowedPort = q
			}
		} else if strings.Count(entry, ":") == 1 {
			parts := strings.SplitN(entry, ":", 2)
			if q, e := strconv.Atoi(parts[1]); e == nil {
				allowedHost = strings.TrimSuffix(parts[0], ".")
				allowedPort = q
			}
		}
		if allowedHost == normalized && (allowedPort < 0 || allowedPort == port) {
			return nil
		}
	}
	return &SagaError{Code: "SAGA-R103", ID: "SAGA-R103", Message: fmt.Sprintf("network access denied: %s:%d; use --allow-net host[:port]", host, port)}
}

func (i *Interpreter) requireDevice() error {
	if i.AllowDevice || strings.TrimSpace(os.Getenv("SAGA_ALLOW_DEVICE")) == "1" {
		return nil
	}
	return &SagaError{Code: "SAGA-R103", ID: "SAGA-R103", Message: "physical device access denied; use --allow-device or SAGA_ALLOW_DEVICE=1 for a standalone runtime"}
}

func (i *Interpreter) callMachineNative(name string, args []Value, t Token) (Value, error) {
	fail := func(err error) (Value, error) {
		return nil, diag("SAGA-R001", "SAGA-R103", "machine."+name+": "+err.Error(), t)
	}
	switch name {
	case "timing_class":
		return "hosted-soft-realtime", nil
	case "hard_realtime_available":
		return false, nil
	case "monotonic_ns":
		return numberFromInt64(time.Since(machineMonotonicOrigin).Nanoseconds()), nil
	case "bytes_from_hex":
		if len(args) != 1 {
			return fail(fmt.Errorf("requires 1 argument"))
		}
		text, err := machineText(args[0], "hex text")
		if err != nil {
			return fail(err)
		}
		text = strings.ReplaceAll(strings.ReplaceAll(strings.TrimSpace(text), " ", ""), "_", "")
		if len(text)%2 != 0 {
			return fail(fmt.Errorf("hex text must contain an even number of digits"))
		}
		b, err := hex.DecodeString(text)
		if err != nil {
			return fail(fmt.Errorf("invalid hexadecimal payload: %w", err))
		}
		return b, nil
	case "bytes_to_hex":
		if len(args) != 1 {
			return fail(fmt.Errorf("requires 1 argument"))
		}
		b, err := machineBytes(args[0], "bytes")
		if err != nil {
			return fail(err)
		}
		return hex.EncodeToString(b), nil
	case "pid":
		if len(args) != 5 {
			return fail(fmt.Errorf("requires 5 arguments"))
		}
		v := make([]float64, 5)
		for j := range v {
			q, e := machineNumber(args[j], "argument")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		p, e := newMachinePID(v[0], v[1], v[2], v[3], v[4])
		if e != nil {
			return fail(e)
		}
		return p, nil
	case "pid_step":
		if len(args) != 4 {
			return fail(fmt.Errorf("requires 4 arguments"))
		}
		p, ok := args[0].(*MachinePID)
		if !ok {
			return fail(fmt.Errorf("first argument must be machine PID"))
		}
		a, _ := machineNumber(args[1], "setpoint")
		b, _ := machineNumber(args[2], "measurement")
		dt, e := machineNumber(args[3], "dt_seconds")
		if e != nil {
			return fail(e)
		}
		q, e := p.step(a, b, dt)
		if e != nil {
			return fail(e)
		}
		return machineNumberFromFloat(q), nil
	case "pid_reset":
		p, ok := args[0].(*MachinePID)
		if !ok {
			return fail(fmt.Errorf("PID required"))
		}
		p.reset()
		return nil, nil
	case "pid_integral_limits":
		p, ok := args[0].(*MachinePID)
		if !ok {
			return fail(fmt.Errorf("PID required"))
		}
		low, _ := machineNumber(args[1], "low")
		high, _ := machineNumber(args[2], "high")
		if e := p.integralLimits(low, high); e != nil {
			return fail(e)
		}
		return nil, nil
	case "pid2":
		if len(args) != 8 {
			return fail(fmt.Errorf("requires 8 arguments"))
		}
		v := make([]float64, 8)
		for j := range v {
			q, e := machineNumber(args[j], "argument")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		p, e := newMachinePID2(v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7])
		if e != nil {
			return fail(e)
		}
		return p, nil
	case "pid2_step":
		if len(args) != 5 {
			return fail(fmt.Errorf("requires 5 arguments"))
		}
		p, ok := args[0].(*MachinePID2)
		if !ok {
			return fail(fmt.Errorf("first argument must be machine PID2"))
		}
		v := make([]float64, 4)
		for j := range v {
			q, e := machineNumber(args[j+1], "argument")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		q, e := p.step(v[0], v[1], v[2], v[3])
		if e != nil {
			return fail(e)
		}
		return machineNumberFromFloat(q), nil
	case "pid2_reset":
		p, ok := args[0].(*MachinePID2)
		if !ok {
			return fail(fmt.Errorf("PID2 required"))
		}
		p.reset()
		return nil, nil
	case "motor_feedforward":
		if len(args) != 5 {
			return fail(fmt.Errorf("requires 5 arguments"))
		}
		v := make([]float64, 5)
		for j := range v {
			q, e := machineNumber(args[j], "argument")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		q, e := machineMotorFeedforward(v[0], v[1], v[2], v[3], v[4])
		if e != nil {
			return fail(e)
		}
		return machineNumberFromFloat(q), nil
	case "alpha_beta":
		if len(args) != 4 {
			return fail(fmt.Errorf("requires 4 arguments"))
		}
		v := make([]float64, 4)
		for j := range v {
			q, e := machineNumber(args[j], "argument")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		o, e := newMachineAlphaBeta(v[0], v[1], v[2], v[3])
		if e != nil {
			return fail(e)
		}
		return o, nil
	case "alpha_beta_step":
		o, ok := args[0].(*MachineAlphaBeta)
		if !ok {
			return fail(fmt.Errorf("alpha-beta observer required"))
		}
		measurement, e := machineNumber(args[1], "measurement")
		if e != nil {
			return fail(e)
		}
		dt, e := machineNumber(args[2], "dt_seconds")
		if e != nil {
			return fail(e)
		}
		position, velocity, e := o.step(measurement, dt)
		if e != nil {
			return fail(e)
		}
		return []Value{machineNumberFromFloat(position), machineNumberFromFloat(velocity)}, nil
	case "alpha_beta_reset":
		o, ok := args[0].(*MachineAlphaBeta)
		if !ok {
			return fail(fmt.Errorf("alpha-beta observer required"))
		}
		position, e := machineNumber(args[1], "position")
		if e != nil {
			return fail(e)
		}
		velocity, e := machineNumber(args[2], "velocity")
		if e != nil {
			return fail(e)
		}
		if e := o.reset(position, velocity); e != nil {
			return fail(e)
		}
		return nil, nil
	case "notch":
		if len(args) != 3 {
			return fail(fmt.Errorf("requires 3 arguments"))
		}
		v := make([]float64, 3)
		for j := range v {
			q, e := machineNumber(args[j], "argument")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		f, e := newMachineNotch(v[0], v[1], v[2])
		if e != nil {
			return fail(e)
		}
		return f, nil
	case "filter_step":
		f, ok := args[0].(*MachineBiquad)
		if !ok {
			return fail(fmt.Errorf("machine biquad required"))
		}
		sample, e := machineNumber(args[1], "sample")
		if e != nil {
			return fail(e)
		}
		q, e := f.step(sample)
		if e != nil {
			return fail(e)
		}
		return machineNumberFromFloat(q), nil
	case "filter_reset":
		f, ok := args[0].(*MachineBiquad)
		if !ok {
			return fail(fmt.Errorf("machine biquad required"))
		}
		f.reset()
		return nil, nil
	case "control_guard":
		rate, e := machineInt(args[0], "rate_hz")
		if e != nil {
			return fail(e)
		}
		budget, e := machineInt(args[1], "budget_us")
		if e != nil {
			return fail(e)
		}
		stale, e := machineInt(args[2], "stale_input_us")
		if e != nil {
			return fail(e)
		}
		jitter, e := machineInt(args[3], "max_jitter_us")
		if e != nil {
			return fail(e)
		}
		g, e := newMachineControlGuard(rate, budget, stale, jitter)
		if e != nil {
			return fail(e)
		}
		return g, nil
	case "control_guard_begin":
		g, ok := args[0].(*MachineControlGuard)
		if !ok {
			return fail(fmt.Errorf("control guard required"))
		}
		input, e := machineInt(args[1], "input_timestamp_ns")
		if e != nil {
			return fail(e)
		}
		now, e := machineInt(args[2], "now_ns")
		if e != nil {
			return fail(e)
		}
		v, e := g.begin(int64(input), int64(now))
		if e != nil {
			return fail(e)
		}
		return v, nil
	case "control_guard_end":
		g, ok := args[0].(*MachineControlGuard)
		if !ok {
			return fail(fmt.Errorf("control guard required"))
		}
		end, e := machineInt(args[1], "end_ns")
		if e != nil {
			return fail(e)
		}
		v, e := g.end(int64(end))
		if e != nil {
			return fail(e)
		}
		return v, nil
	case "control_guard_ok":
		g, ok := args[0].(*MachineControlGuard)
		if !ok {
			return fail(fmt.Errorf("control guard required"))
		}
		return g.ok(), nil
	case "control_guard_stats_json":
		g, ok := args[0].(*MachineControlGuard)
		if !ok {
			return fail(fmt.Errorf("control guard required"))
		}
		return g.statsJSON(), nil
	case "control_guard_reset":
		g, ok := args[0].(*MachineControlGuard)
		if !ok {
			return fail(fmt.Errorf("control guard required"))
		}
		g.reset()
		return nil, nil
	case "deadline_budget":
		period, e := machineInt(args[0], "period_us")
		if e != nil {
			return fail(e)
		}
		budget, e := machineInt(args[1], "budget_us")
		if e != nil {
			return fail(e)
		}
		b, e := newMachineDeadlineBudget(period, budget)
		if e != nil {
			return fail(e)
		}
		return b, nil
	case "budget_begin":
		b, ok := args[0].(*MachineDeadlineBudget)
		if !ok {
			return fail(fmt.Errorf("deadline budget required"))
		}
		if e := b.begin(); e != nil {
			return fail(e)
		}
		return nil, nil
	case "budget_end":
		b, ok := args[0].(*MachineDeadlineBudget)
		if !ok {
			return fail(fmt.Errorf("deadline budget required"))
		}
		over, e := b.end()
		if e != nil {
			return fail(e)
		}
		return over, nil
	case "budget_stats_json":
		b, ok := args[0].(*MachineDeadlineBudget)
		if !ok {
			return fail(fmt.Errorf("deadline budget required"))
		}
		return b.statsJSON(), nil
	case "budget_reset":
		b, ok := args[0].(*MachineDeadlineBudget)
		if !ok {
			return fail(fmt.Errorf("deadline budget required"))
		}
		b.reset()
		return nil, nil
	case "clarke", "park", "inverse_park", "svpwm":
		if len(args) != 3 {
			return fail(fmt.Errorf("requires 3 arguments"))
		}
		v := make([]float64, 3)
		for j := range v {
			q, e := machineNumber(args[j], "argument")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		var values []float64
		var e error
		switch name {
		case "clarke":
			values, e = machineClarke(v[0], v[1], v[2])
		case "park":
			values, e = machinePark(v[0], v[1], v[2])
		case "inverse_park":
			values, e = machineInversePark(v[0], v[1], v[2])
		case "svpwm":
			values, e = machineSVPWM(v[0], v[1], v[2])
		}
		if e != nil {
			return fail(e)
		}
		out := make([]Value, len(values))
		for j, q := range values {
			out[j] = machineNumberFromFloat(q)
		}
		return out, nil
	case "foc_current":
		if len(args) != 11 {
			return fail(fmt.Errorf("requires 11 arguments"))
		}
		v := make([]float64, 11)
		for j := range v {
			q, e := machineNumber(args[j], "argument")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		q, e := newMachineFOCCurrent(v)
		if e != nil {
			return fail(e)
		}
		return q, nil
	case "foc_step":
		c, ok := args[0].(*MachineFOCCurrent)
		if !ok {
			return fail(fmt.Errorf("FOC current controller required"))
		}
		if len(args) != 10 {
			return fail(fmt.Errorf("requires 10 arguments"))
		}
		v := make([]float64, 9)
		for j := range v {
			q, e := machineNumber(args[j+1], "argument")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		if e := c.step(v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7], v[8]); e != nil {
			return fail(e)
		}
		return nil, nil
	case "foc_reset":
		c, ok := args[0].(*MachineFOCCurrent)
		if !ok {
			return fail(fmt.Errorf("FOC current controller required"))
		}
		c.reset()
		return nil, nil
	case "foc_id", "foc_iq", "foc_vd", "foc_vq":
		c, ok := args[0].(*MachineFOCCurrent)
		if !ok {
			return fail(fmt.Errorf("FOC current controller required"))
		}
		v := c.MeasuredD
		if name == "foc_iq" {
			v = c.MeasuredQ
		} else if name == "foc_vd" {
			v = c.VoltageD
		} else if name == "foc_vq" {
			v = c.VoltageQ
		}
		return machineNumberFromFloat(v), nil
	case "foc_duty":
		c, ok := args[0].(*MachineFOCCurrent)
		if !ok {
			return fail(fmt.Errorf("FOC current controller required"))
		}
		phase, e := machineInt(args[1], "phase")
		if e != nil {
			return fail(e)
		}
		v := 0.0
		if phase == 0 {
			v = c.DutyA
		} else if phase == 1 {
			v = c.DutyB
		} else if phase == 2 {
			v = c.DutyC
		} else {
			return fail(fmt.Errorf("FOC phase index must be 0..2"))
		}
		return machineNumberFromFloat(v), nil
	case "encoder_integrated":
		cpr, e := machineInt(args[0], "counts_per_revolution")
		if e != nil {
			return fail(e)
		}
		gear, e := machineNumber(args[1], "gear_ratio")
		if e != nil {
			return fail(e)
		}
		mod, e := machineInt(args[2], "modulus")
		if e != nil {
			return fail(e)
		}
		direction, e := machineInt(args[3], "direction")
		if e != nil {
			return fail(e)
		}
		alpha, e := machineNumber(args[4], "velocity_alpha")
		if e != nil {
			return fail(e)
		}
		q, e := newMachineUnifiedEncoder(cpr, gear, mod, direction, alpha)
		if e != nil {
			return fail(e)
		}
		return q, nil
	case "encoder_sample":
		e, ok := args[0].(*MachineUnifiedEncoder)
		if !ok {
			return fail(fmt.Errorf("integrated encoder required"))
		}
		raw, x := machineInt(args[1], "raw_count")
		if x != nil {
			return fail(x)
		}
		ts, x := machineInt(args[2], "timestamp_ns")
		if x != nil {
			return fail(x)
		}
		if x = e.sample(raw, int64(ts)); x != nil {
			return fail(x)
		}
		return nil, nil
	case "encoder_align_absolute":
		e, ok := args[0].(*MachineUnifiedEncoder)
		if !ok {
			return fail(fmt.Errorf("integrated encoder required"))
		}
		raw, x := machineInt(args[1], "raw_count")
		if x != nil {
			return fail(x)
		}
		deg, x := machineNumber(args[2], "mechanical_degrees")
		if x != nil {
			return fail(x)
		}
		if x = e.align(raw, deg); x != nil {
			return fail(x)
		}
		return nil, nil
	case "encoder_position_deg", "encoder_velocity_deg_s", "encoder_integrated_velocity_rpm":
		e, ok := args[0].(*MachineUnifiedEncoder)
		if !ok {
			return fail(fmt.Errorf("integrated encoder required"))
		}
		v := e.Position
		if name == "encoder_velocity_deg_s" {
			v = e.Velocity
		} else if name == "encoder_integrated_velocity_rpm" {
			v = e.Velocity / 6
		}
		return machineNumberFromFloat(v), nil
	case "rls2":
		l, e := machineNumber(args[0], "forgetting_factor")
		if e != nil {
			return fail(e)
		}
		p, e := machineNumber(args[1], "covariance")
		if e != nil {
			return fail(e)
		}
		q, e := newMachineRLS2(l, p)
		if e != nil {
			return fail(e)
		}
		return q, nil
	case "rls2_update":
		r, ok := args[0].(*MachineRLS2)
		if !ok {
			return fail(fmt.Errorf("RLS2 estimator required"))
		}
		v := make([]float64, 3)
		for j := range v {
			q, e := machineNumber(args[j+1], "argument")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		if e := r.update(v[0], v[1], v[2]); e != nil {
			return fail(e)
		}
		return nil, nil
	case "rls2_theta0", "rls2_theta1", "rls2_error":
		r, ok := args[0].(*MachineRLS2)
		if !ok {
			return fail(fmt.Errorf("RLS2 estimator required"))
		}
		v := r.Theta0
		if name == "rls2_theta1" {
			v = r.Theta1
		} else if name == "rls2_error" {
			v = r.LastError
		}
		return machineNumberFromFloat(v), nil
	case "mpc2":
		if len(args) != 12 {
			return fail(fmt.Errorf("requires 12 arguments"))
		}
		v := make([]float64, 11)
		for j := 0; j < 9; j++ {
			q, e := machineNumber(args[j], "argument")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		h, e := machineInt(args[9], "horizon")
		if e != nil {
			return fail(e)
		}
		for j := 10; j < 12; j++ {
			q, e := machineNumber(args[j], "argument")
			if e != nil {
				return fail(e)
			}
			v[j-1] = q
		}
		m, e := newMachineMPC2(v, h)
		if e != nil {
			return fail(e)
		}
		return m, nil
	case "mpc2_step":
		m, ok := args[0].(*MachineMPC2)
		if !ok {
			return fail(fmt.Errorf("MPC2 controller required"))
		}
		v := make([]float64, 4)
		for j := range v {
			q, e := machineNumber(args[j+1], "argument")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		q, e := m.step(v[0], v[1], v[2], v[3])
		if e != nil {
			return fail(e)
		}
		return machineNumberFromFloat(q), nil
	case "mpc2_reset":
		m, ok := args[0].(*MachineMPC2)
		if !ok {
			return fail(fmt.Errorf("MPC2 controller required"))
		}
		m.reset()
		return nil, nil
	case "disturbance_observer":
		v := make([]float64, 3)
		for j := range v {
			q, e := machineNumber(args[j], "argument")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		d, e := newMachineDOB(v[0], v[1], v[2])
		if e != nil {
			return fail(e)
		}
		return d, nil
	case "disturbance_step":
		d, ok := args[0].(*MachineDOB)
		if !ok {
			return fail(fmt.Errorf("disturbance observer required"))
		}
		v := make([]float64, 3)
		for j := range v {
			q, e := machineNumber(args[j+1], "argument")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		q, e := d.step(v[0], v[1], v[2])
		if e != nil {
			return fail(e)
		}
		return machineNumberFromFloat(q), nil
	case "disturbance_reset":
		d, ok := args[0].(*MachineDOB)
		if !ok {
			return fail(fmt.Errorf("disturbance observer required"))
		}
		q, e := machineNumber(args[1], "estimate")
		if e != nil {
			return fail(e)
		}
		if e = d.reset(q); e != nil {
			return fail(e)
		}
		return nil, nil
	case "friction_compensation":
		v := make([]float64, 6)
		for j := range v {
			q, e := machineNumber(args[j], "argument")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		q, e := machineFrictionCompensation(v[0], v[1], v[2], v[3], v[4], v[5])
		if e != nil {
			return fail(e)
		}
		return machineNumberFromFloat(q), nil
	case "axis_sync":
		count, e := machineInt(args[0], "axis_count")
		if e != nil {
			return fail(e)
		}
		kp, e := machineNumber(args[1], "kp")
		if e != nil {
			return fail(e)
		}
		mc, e := machineNumber(args[2], "max_correction")
		if e != nil {
			return fail(e)
		}
		sl, e := machineNumber(args[3], "skew_limit")
		if e != nil {
			return fail(e)
		}
		q, e := newMachineAxisSync(count, kp, mc, sl)
		if e != nil {
			return fail(e)
		}
		return q, nil
	case "axis_sync_config":
		s, ok := args[0].(*MachineAxisSync)
		if !ok {
			return fail(fmt.Errorf("axis synchronizer required"))
		}
		axis, e := machineInt(args[1], "axis")
		if e != nil {
			return fail(e)
		}
		ratio, e := machineNumber(args[2], "ratio")
		if e != nil {
			return fail(e)
		}
		offset, e := machineNumber(args[3], "offset")
		if e != nil {
			return fail(e)
		}
		if e = s.configure(axis, ratio, offset); e != nil {
			return fail(e)
		}
		return nil, nil
	case "axis_sync_begin":
		s, ok := args[0].(*MachineAxisSync)
		if !ok {
			return fail(fmt.Errorf("axis synchronizer required"))
		}
		master, e := machineNumber(args[1], "master_position")
		if e != nil {
			return fail(e)
		}
		if e = s.begin(master); e != nil {
			return fail(e)
		}
		return nil, nil
	case "axis_sync_correction":
		s, ok := args[0].(*MachineAxisSync)
		if !ok {
			return fail(fmt.Errorf("axis synchronizer required"))
		}
		axis, e := machineInt(args[1], "axis")
		if e != nil {
			return fail(e)
		}
		actual, e := machineNumber(args[2], "actual_position")
		if e != nil {
			return fail(e)
		}
		q, e := s.correction(axis, actual)
		if e != nil {
			return fail(e)
		}
		return machineNumberFromFloat(q), nil
	case "axis_sync_error":
		s, ok := args[0].(*MachineAxisSync)
		if !ok {
			return fail(fmt.Errorf("axis synchronizer required"))
		}
		axis, e := machineInt(args[1], "axis")
		if e != nil {
			return fail(e)
		}
		if axis < 0 || axis >= s.AxisCount {
			return fail(fmt.Errorf("axis_sync axis index out of range"))
		}
		return machineNumberFromFloat(s.Errors[axis]), nil
	case "axis_sync_ok":
		s, ok := args[0].(*MachineAxisSync)
		if !ok {
			return fail(fmt.Errorf("axis synchronizer required"))
		}
		return s.Healthy, nil
	case "ethercat_datagram":
		cmd, e := machineText(args[0], "command")
		if e != nil {
			return fail(e)
		}
		idx, e := machineInt(args[1], "index")
		if e != nil {
			return fail(e)
		}
		addr, e := machineInt(args[2], "address")
		if e != nil {
			return fail(e)
		}
		off, e := machineInt(args[3], "offset")
		if e != nil {
			return fail(e)
		}
		data, e := machineBytes(args[4], "data")
		if e != nil {
			return fail(e)
		}
		irq, e := machineInt(args[5], "irq")
		if e != nil {
			return fail(e)
		}
		more, e := parseMachineBool(args[6], "more")
		if e != nil {
			return fail(e)
		}
		q, e := machineEtherCATDatagram(cmd, idx, addr, off, data, irq, more)
		if e != nil {
			return fail(e)
		}
		return q, nil
	case "ethercat_frame":
		b, e := machineBytes(args[0], "datagrams")
		if e != nil {
			return fail(e)
		}
		q, e := machineEtherCATFrame(b)
		if e != nil {
			return fail(e)
		}
		return q, nil
	case "ethercat_lrw":
		idx, e := machineInt(args[0], "index")
		if e != nil {
			return fail(e)
		}
		addr, e := machineInt(args[1], "logical_address")
		if e != nil {
			return fail(e)
		}
		b, e := machineBytes(args[2], "process_data")
		if e != nil {
			return fail(e)
		}
		q, e := machineEtherCATLRW(idx, int64(addr), b)
		if e != nil {
			return fail(e)
		}
		return q, nil
	case "ethercat_first_datagram_json":
		b, e := machineBytes(args[0], "frame")
		if e != nil {
			return fail(e)
		}
		q, e := machineEtherCATFirstDatagramJSON(b)
		if e != nil {
			return fail(e)
		}
		return q, nil
	case "allocation_free_profile_json":
		return machineAllocationFreeProfileJSON(), nil
	case "slew":
		if len(args) != 4 {
			return fail(fmt.Errorf("requires 4 arguments"))
		}
		v := make([]float64, 4)
		for j := range v {
			q, e := machineNumber(args[j], "argument")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		q, e := machineSlew(v[0], v[1], v[2], v[3])
		if e != nil {
			return fail(e)
		}
		return machineNumberFromFloat(q), nil
	case "low_pass":
		if len(args) != 3 {
			return fail(fmt.Errorf("requires 3 arguments"))
		}
		v := make([]float64, 3)
		for j := range v {
			q, e := machineNumber(args[j], "argument")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		q, e := machineLowPass(v[0], v[1], v[2])
		if e != nil {
			return fail(e)
		}
		return machineNumberFromFloat(q), nil
	case "deadband":
		if len(args) != 2 {
			return fail(fmt.Errorf("requires 2 arguments"))
		}
		v := make([]float64, 2)
		for j := range v {
			q, e := machineNumber(args[j], "argument")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		q, e := machineDeadband(v[0], v[1])
		if e != nil {
			return fail(e)
		}
		return machineNumberFromFloat(q), nil
	case "integrate_clamped":
		if len(args) != 5 {
			return fail(fmt.Errorf("requires 5 arguments"))
		}
		v := make([]float64, 5)
		for j := range v {
			q, e := machineNumber(args[j], "argument")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		q, e := machineIntegrateClamped(v[0], v[1], v[2], v[3], v[4])
		if e != nil {
			return fail(e)
		}
		return machineNumberFromFloat(q), nil
	case "q31_from_ratio":
		if len(args) != 2 {
			return fail(fmt.Errorf("requires 2 arguments"))
		}
		n, e := machineInt(args[0], "numerator")
		if e != nil {
			return fail(e)
		}
		d, e := machineInt(args[1], "denominator")
		if e != nil {
			return fail(e)
		}
		q, e := machineQ31FromRatio(int64(n), int64(d))
		if e != nil {
			return fail(e)
		}
		return numberFromInt64(q), nil
	case "q31_add_sat", "q31_sub_sat", "q31_mul_sat":
		if len(args) != 2 {
			return fail(fmt.Errorf("requires 2 arguments"))
		}
		left, e := machineInt(args[0], "left")
		if e != nil {
			return fail(e)
		}
		right, e := machineInt(args[1], "right")
		if e != nil {
			return fail(e)
		}
		var q int64
		switch name {
		case "q31_add_sat":
			q, e = machineQ31AddSat(int64(left), int64(right))
		case "q31_sub_sat":
			q, e = machineQ31SubSat(int64(left), int64(right))
		default:
			q, e = machineQ31MulSat(int64(left), int64(right))
		}
		if e != nil {
			return fail(e)
		}
		return numberFromInt64(q), nil
	case "q31_mac_sat":
		if len(args) != 3 {
			return fail(fmt.Errorf("requires 3 arguments"))
		}
		acc, e := machineInt(args[0], "accumulator")
		if e != nil {
			return fail(e)
		}
		left, e := machineInt(args[1], "left")
		if e != nil {
			return fail(e)
		}
		right, e := machineInt(args[2], "right")
		if e != nil {
			return fail(e)
		}
		q, e := machineQ31MacSat(int64(acc), int64(left), int64(right))
		if e != nil {
			return fail(e)
		}
		return numberFromInt64(q), nil
	case "profile":
		if len(args) != 5 {
			return fail(fmt.Errorf("requires 5 arguments"))
		}
		v := make([]float64, 5)
		for j := range v {
			q, e := machineNumber(args[j], "argument")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		p, e := newMachineProfile(v[0], v[1], v[2], v[3], v[4])
		if e != nil {
			return fail(e)
		}
		return p, nil
	case "profile_step":
		p, ok := args[0].(*MachineProfile)
		if !ok {
			return fail(fmt.Errorf("motion profile required"))
		}
		dt, e := machineNumber(args[1], "dt_seconds")
		if e != nil {
			return fail(e)
		}
		q, e := p.step(dt)
		if e != nil {
			return fail(e)
		}
		return machineNumberFromFloat(q), nil
	case "profile_velocity":
		p, ok := args[0].(*MachineProfile)
		if !ok {
			return fail(fmt.Errorf("motion profile required"))
		}
		return numberFromFloat64(p.Velocity), nil
	case "profile_done":
		p, ok := args[0].(*MachineProfile)
		if !ok {
			return fail(fmt.Errorf("motion profile required"))
		}
		return p.done(), nil
	case "profile_retarget":
		p, ok := args[0].(*MachineProfile)
		if !ok {
			return fail(fmt.Errorf("motion profile required"))
		}
		q, e := machineNumber(args[1], "target")
		if e != nil {
			return fail(e)
		}
		if e = p.retarget(q); e != nil {
			return fail(e)
		}
		return nil, nil
	case "watchdog":
		n, e := machineInt(args[0], "timeout_ms")
		if e != nil {
			return fail(e)
		}
		w, e := newMachineWatchdog(n)
		if e != nil {
			return fail(e)
		}
		return w, nil
	case "watchdog_feed":
		w, ok := args[0].(*MachineWatchdog)
		if !ok {
			return fail(fmt.Errorf("watchdog required"))
		}
		w.feed()
		return nil, nil
	case "watchdog_expired":
		w, ok := args[0].(*MachineWatchdog)
		if !ok {
			return fail(fmt.Errorf("watchdog required"))
		}
		return w.expired(), nil
	case "watchdog_remaining_ms":
		w, ok := args[0].(*MachineWatchdog)
		if !ok {
			return fail(fmt.Errorf("watchdog required"))
		}
		return numberFromInt64(w.remainingMS()), nil
	case "watchdog_check":
		w, ok := args[0].(*MachineWatchdog)
		if !ok {
			return fail(fmt.Errorf("watchdog required"))
		}
		safety, ok := args[1].(*MachineSafety)
		if !ok {
			return fail(fmt.Errorf("safety latch required"))
		}
		reason, e := machineText(args[2], "reason")
		if e != nil {
			return fail(e)
		}
		if !w.expired() {
			return false, nil
		}
		if e := safety.trip(reason); e != nil {
			return fail(e)
		}
		return true, nil
	case "safety_latch":
		return &MachineSafety{}, nil
	case "safety_trip":
		s, ok := args[0].(*MachineSafety)
		if !ok {
			return fail(fmt.Errorf("safety latch required"))
		}
		r, e := machineText(args[1], "reason")
		if e != nil {
			return fail(e)
		}
		if e := s.trip(r); e != nil {
			return fail(e)
		}
		return nil, nil
	case "safety_clear":
		s, ok := args[0].(*MachineSafety)
		if !ok {
			return fail(fmt.Errorf("safety latch required"))
		}
		if e := s.clear(); e != nil {
			return fail(e)
		}
		return nil, nil
	case "safety_tripped":
		s, ok := args[0].(*MachineSafety)
		if !ok {
			return fail(fmt.Errorf("safety latch required"))
		}
		tripped, _ := s.snapshot()
		return tripped, nil
	case "safety_reason":
		s, ok := args[0].(*MachineSafety)
		if !ok {
			return fail(fmt.Errorf("safety latch required"))
		}
		_, reason := s.snapshot()
		return reason, nil
	case "safety_check":
		safety, ok := args[0].(*MachineSafety)
		if !ok {
			return fail(fmt.Errorf("safety latch required"))
		}
		safe, e := parseMachineBool(args[1], "safe")
		if e != nil {
			return fail(e)
		}
		reason, e := machineText(args[2], "reason")
		if e != nil {
			return fail(e)
		}
		if safe {
			return true, nil
		}
		if e := safety.trip(reason); e != nil {
			return fail(e)
		}
		return false, nil
	case "cycle":
		n, e := machineInt(args[0], "period_us")
		if e != nil {
			return fail(e)
		}
		c, e := newMachineCycle(n)
		if e != nil {
			return fail(e)
		}
		return c, nil
	case "cyclic_clock":
		n, e := machineInt(args[0], "frequency_hz")
		if e != nil {
			return fail(e)
		}
		c, e := newMachineCycleHz(n)
		if e != nil {
			return fail(e)
		}
		return c, nil
	case "cycle_wait_due":
		c, ok := args[0].(*MachineCycle)
		if !ok {
			return fail(fmt.Errorf("control cycle required"))
		}
		return numberFromInt64(c.waitDue()), nil
	case "cycle_stats_json":
		c, ok := args[0].(*MachineCycle)
		if !ok {
			return fail(fmt.Errorf("control cycle required"))
		}
		return c.statsJSON(), nil
	case "cycle_wait":
		c, ok := args[0].(*MachineCycle)
		if !ok {
			return fail(fmt.Errorf("control cycle required"))
		}
		c.wait()
		return nil, nil
	case "cycle_overruns":
		c, ok := args[0].(*MachineCycle)
		if !ok {
			return fail(fmt.Errorf("control cycle required"))
		}
		return numberFromInt64(c.Overruns), nil
	case "cycle_jitter_us":
		c, ok := args[0].(*MachineCycle)
		if !ok {
			return fail(fmt.Errorf("control cycle required"))
		}
		return numberFromInt64(c.LastJitterUS), nil
	case "servo_duty":
		if len(args) != 6 {
			return fail(fmt.Errorf("requires 6 arguments"))
		}
		v := make([]float64, 6)
		for j := range v {
			q, e := machineNumber(args[j], "argument")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		q, e := machineServoDuty(v[0], v[1], v[2], v[3], v[4], v[5])
		if e != nil {
			return fail(e)
		}
		return machineNumberFromFloat(q), nil
	case "encoder":
		cpr, e := machineInt(args[0], "counts_per_revolution")
		if e != nil {
			return fail(e)
		}
		gear, e := machineNumber(args[1], "gear_ratio")
		if e != nil {
			return fail(e)
		}
		enc, e := newMachineEncoder(cpr, gear)
		if e != nil {
			return fail(e)
		}
		return enc, nil
	case "encoder_wrap":
		enc, ok := args[0].(*MachineEncoder)
		if !ok {
			return fail(fmt.Errorf("encoder required"))
		}
		modulus, e := machineInt(args[1], "modulus")
		if e != nil {
			return fail(e)
		}
		if e := enc.setWrapModulus(int64(modulus)); e != nil {
			return fail(e)
		}
		return nil, nil
	case "encoder_unwrapped_count":
		enc, ok := args[0].(*MachineEncoder)
		if !ok {
			return fail(fmt.Errorf("encoder required"))
		}
		return numberFromInt64(enc.UnwrappedCount), nil
	case "encoder_update":
		enc, ok := args[0].(*MachineEncoder)
		if !ok {
			return fail(fmt.Errorf("encoder required"))
		}
		count, e := machineInt(args[1], "count")
		if e != nil {
			return fail(e)
		}
		ts, e := machineInt(args[2], "timestamp_ns")
		if e != nil {
			return fail(e)
		}
		if e = enc.update(int64(count), int64(ts)); e != nil {
			return fail(e)
		}
		return nil, nil
	case "encoder_update_now":
		enc, ok := args[0].(*MachineEncoder)
		if !ok {
			return fail(fmt.Errorf("encoder required"))
		}
		count, e := machineInt(args[1], "count")
		if e != nil {
			return fail(e)
		}
		if e = enc.update(int64(count), time.Since(machineMonotonicOrigin).Nanoseconds()); e != nil {
			return fail(e)
		}
		return nil, nil
	case "encoder_position_degrees":
		enc, ok := args[0].(*MachineEncoder)
		if !ok {
			return fail(fmt.Errorf("encoder required"))
		}
		return machineNumberFromFloat(enc.PositionDegrees), nil
	case "encoder_velocity_rpm":
		enc, ok := args[0].(*MachineEncoder)
		if !ok {
			return fail(fmt.Errorf("encoder required"))
		}
		return machineNumberFromFloat(enc.VelocityRPM), nil
	case "encoder_reset":
		enc, ok := args[0].(*MachineEncoder)
		if !ok {
			return fail(fmt.Errorf("encoder required"))
		}
		count, e := machineInt(args[1], "count")
		if e != nil {
			return fail(e)
		}
		enc.reset(int64(count))
		return nil, nil

	case "s_curve":
		if len(args) != 7 {
			return fail(fmt.Errorf("requires 7 arguments"))
		}
		v := make([]float64, 7)
		for j := range v {
			q, e := machineNumber(args[j], "argument")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		p, e := newMachineSCurve(v[0], v[1], v[2], v[3], v[4], v[5], v[6])
		if e != nil {
			return fail(e)
		}
		return p, nil
	case "s_curve_step":
		p, ok := args[0].(*MachineSCurve)
		if !ok {
			return fail(fmt.Errorf("S-curve profile required"))
		}
		dt, e := machineNumber(args[1], "dt_seconds")
		if e != nil {
			return fail(e)
		}
		q, e := p.step(dt)
		if e != nil {
			return fail(e)
		}
		return machineNumberFromFloat(q), nil
	case "s_curve_velocity":
		p, ok := args[0].(*MachineSCurve)
		if !ok {
			return fail(fmt.Errorf("S-curve profile required"))
		}
		return machineNumberFromFloat(p.Velocity), nil
	case "s_curve_acceleration":
		p, ok := args[0].(*MachineSCurve)
		if !ok {
			return fail(fmt.Errorf("S-curve profile required"))
		}
		return machineNumberFromFloat(p.Acceleration), nil
	case "s_curve_done":
		p, ok := args[0].(*MachineSCurve)
		if !ok {
			return fail(fmt.Errorf("S-curve profile required"))
		}
		return p.done(), nil
	case "s_curve_retarget":
		p, ok := args[0].(*MachineSCurve)
		if !ok {
			return fail(fmt.Errorf("S-curve profile required"))
		}
		q, e := machineNumber(args[1], "target")
		if e != nil {
			return fail(e)
		}
		if e = p.retarget(q); e != nil {
			return fail(e)
		}
		return nil, nil
	case "axis":
		if len(args) != 10 {
			return fail(fmt.Errorf("requires 10 arguments"))
		}
		v := make([]float64, 9)
		for j := range v {
			q, e := machineNumber(args[j], "axis argument")
			if e != nil {
				return fail(e)
			}
			v[j] = q
		}
		safety, ok := args[9].(*MachineSafety)
		if !ok {
			return fail(fmt.Errorf("safety latch required"))
		}
		a, e := newMachineAxis(v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7], v[8], safety)
		if e != nil {
			return fail(e)
		}
		return a, nil
	case "axis_target":
		a, ok := args[0].(*MachineAxis)
		if !ok {
			return fail(fmt.Errorf("axis required"))
		}
		q, e := machineNumber(args[1], "target")
		if e != nil {
			return fail(e)
		}
		if e = a.setTarget(q); e != nil {
			return fail(e)
		}
		return nil, nil
	case "axis_step":
		a, ok := args[0].(*MachineAxis)
		if !ok {
			return fail(fmt.Errorf("axis required"))
		}
		m, e := machineNumber(args[1], "measurement")
		if e != nil {
			return fail(e)
		}
		dt, e := machineNumber(args[2], "dt_seconds")
		if e != nil {
			return fail(e)
		}
		q, e := a.step(m, dt)
		if e != nil {
			return fail(e)
		}
		return machineNumberFromFloat(q), nil
	case "axis_command":
		a, ok := args[0].(*MachineAxis)
		if !ok {
			return fail(fmt.Errorf("axis required"))
		}
		return machineNumberFromFloat(a.Command), nil
	case "axis_planned_position":
		a, ok := args[0].(*MachineAxis)
		if !ok {
			return fail(fmt.Errorf("axis required"))
		}
		return machineNumberFromFloat(a.Profile.Position), nil
	case "axis_done":
		a, ok := args[0].(*MachineAxis)
		if !ok {
			return fail(fmt.Errorf("axis required"))
		}
		m, e := machineNumber(args[1], "measurement")
		if e != nil {
			return fail(e)
		}
		return a.done(m), nil
	case "modbus_crc16":
		b, e := machineBytes(args[0], "bytes")
		if e != nil {
			return fail(e)
		}
		return numberFromInt64(int64(machineModbusCRC16(b))), nil
	case "modbus_tcp_open":
		if err := i.requireDevice(); err != nil {
			return nil, err
		}
		host, e := machineText(args[0], "host")
		if e != nil {
			return fail(e)
		}
		port, e := machineInt(args[1], "port")
		if e != nil {
			return fail(e)
		}
		if e := i.requireNet(host, port); e != nil {
			return nil, e
		}
		timeout, e := machineInt(args[2], "timeout_ms")
		if e != nil {
			return fail(e)
		}
		unit, e := machineInt(args[3], "unit_id")
		if e != nil {
			return fail(e)
		}
		m, e := openMachineModbusTCP(host, port, timeout, unit)
		if e != nil {
			return fail(e)
		}
		return m, nil
	case "modbus_read_holding", "modbus_read_input", "modbus_read_coils":
		m, ok := args[0].(machineModbusMaster)
		if !ok {
			return fail(fmt.Errorf("Modbus RTU/TCP master required"))
		}
		address, e := machineInt(args[1], "address")
		if e != nil {
			return fail(e)
		}
		count, e := machineInt(args[2], "count")
		if e != nil {
			return fail(e)
		}
		if name == "modbus_read_holding" {
			return m.readHolding(address, count)
		}
		if name == "modbus_read_input" {
			return m.readInput(address, count)
		}
		return m.readCoils(address, count)
	case "modbus_write_register":
		m, ok := args[0].(machineModbusMaster)
		if !ok {
			return fail(fmt.Errorf("Modbus RTU/TCP master required"))
		}
		address, e := machineInt(args[1], "address")
		if e != nil {
			return fail(e)
		}
		value, e := machineInt(args[2], "value")
		if e != nil {
			return fail(e)
		}
		if e = m.writeRegister(address, value); e != nil {
			return fail(e)
		}
		return nil, nil
	case "modbus_write_registers":
		m, ok := args[0].(machineModbusMaster)
		if !ok {
			return fail(fmt.Errorf("Modbus RTU/TCP master required"))
		}
		address, e := machineInt(args[1], "address")
		if e != nil {
			return fail(e)
		}
		raw, ok := args[2].([]Value)
		if !ok {
			return fail(fmt.Errorf("register values must be list[int]"))
		}
		values := make([]int, len(raw))
		for j, v := range raw {
			q, e := machineInt(v, "register value")
			if e != nil {
				return fail(e)
			}
			values[j] = q
		}
		if e = m.writeRegisters(address, values); e != nil {
			return fail(e)
		}
		return nil, nil
	case "modbus_write_coil":
		m, ok := args[0].(machineModbusMaster)
		if !ok {
			return fail(fmt.Errorf("Modbus RTU/TCP master required"))
		}
		address, e := machineInt(args[1], "address")
		if e != nil {
			return fail(e)
		}
		state, e := parseMachineBool(args[2], "state")
		if e != nil {
			return fail(e)
		}
		if e = m.writeCoil(address, state); e != nil {
			return fail(e)
		}
		return nil, nil
	case "modbus_close":
		m, ok := args[0].(machineModbusMaster)
		if !ok {
			return fail(fmt.Errorf("Modbus RTU/TCP master required"))
		}
		if e := m.sagaMachineClose(); e != nil {
			return fail(e)
		}
		return nil, nil
	}
	if err := i.requireDevice(); err != nil {
		return nil, err
	}
	v, err := machineHardwareCall(name, args)
	if err != nil {
		return fail(err)
	}
	if frame, ok := v.(machineCANFrame); ok {
		b, _ := json.Marshal(map[string]any{"received": frame.received, "id": frame.id, "data_hex": hex.EncodeToString(frame.data)})
		return string(b), nil
	}
	return v, nil
}

func parseMachineBool(v Value, name string) (bool, error) {
	q, ok := v.(bool)
	if !ok {
		return false, fmt.Errorf("%s must be bool", name)
	}
	return q, nil
}
func machineNumberFromFloat(v float64) Number {
	text := strconv.FormatFloat(v, 'g', 15, 64)
	n, err := newNumber(text, "decimal")
	if err != nil {
		return numberFromFloat64(v)
	}
	return n
}

func machineIntText(v int64) string { return strconv.FormatInt(v, 10) }
