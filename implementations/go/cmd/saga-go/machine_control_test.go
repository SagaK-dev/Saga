package main

import (
	"encoding/binary"
	"fmt"
	"io"
	"math"
	"net"
	"strings"
	"testing"
	"time"
)

func machineDecimal(t *testing.T, s string) Number {
	t.Helper()
	n, err := newNumber(s, "decimal")
	if err != nil {
		t.Fatal(err)
	}
	return n
}

func TestMachinePortableControlSurface(t *testing.T) {
	it := NewInterpreter(NewChecker(), func(string) {})
	tok := Token{File: "<machine-test>", Line: 1, Col: 1}
	pid, err := it.callNativeModule("machine", "pid", []Value{machineDecimal(t, "1"), machineDecimal(t, "0.1"), machineDecimal(t, "0"), machineDecimal(t, "-1"), machineDecimal(t, "1")}, tok)
	if err != nil {
		t.Fatal(err)
	}
	out, err := it.callNativeModule("machine", "pid_step", []Value{pid, machineDecimal(t, "10"), machineDecimal(t, "8"), machineDecimal(t, "0.1")}, tok)
	if err != nil {
		t.Fatal(err)
	}
	if out.(Number).String() != "1" {
		t.Fatalf("PID output=%v", out)
	}

	profile, err := it.callNativeModule("machine", "profile", []Value{machineDecimal(t, "0"), machineDecimal(t, "0"), machineDecimal(t, "1"), machineDecimal(t, "2"), machineDecimal(t, "4")}, tok)
	if err != nil {
		t.Fatal(err)
	}
	pos, err := it.callNativeModule("machine", "profile_step", []Value{profile, machineDecimal(t, "0.1")}, tok)
	if err != nil {
		t.Fatal(err)
	}
	if pos.(Number).String() != "0.02" {
		t.Fatalf("profile step=%v", pos)
	}
}

func TestMachineHardwareIsFailClosed(t *testing.T) {
	it := NewInterpreter(NewChecker(), func(string) {})
	tok := Token{File: "<machine-test>", Line: 1, Col: 1}
	_, err := it.callNativeModule("machine", "i2c_open", []Value{"/definitely/not/a/device", numberFromInt64(0x40)}, tok)
	if err == nil || !strings.Contains(err.Error(), "physical device access denied") {
		t.Fatalf("expected device capability error, got %v", err)
	}
	it.AllowDevice = true
	_, err = it.callNativeModule("machine", "i2c_open", []Value{"/definitely/not/a/device", numberFromInt64(0x40)}, tok)
	if err == nil || strings.Contains(err.Error(), "physical device access denied") {
		t.Fatalf("expected host I/O error after explicit grant, got %v", err)
	}
}

func TestMachineModuleChecksAndRuns(t *testing.T) {
	src := `use machine
let pid = machine.pid(1.0, 0.1, 0.0, -1.0, 1.0)
print(machine.pid_step(pid, 10.0, 8.0, 0.1))
print(machine.slew(0.0, 10.0, 2.0, 0.5))
let p = machine.profile(0.0, 0.0, 1.0, 2.0, 4.0)
print(machine.profile_step(p, 0.1))`
	out, err := runSagaForTest(t, src)
	if err != nil {
		t.Fatal(err)
	}
	if out != "1\n1\n0.02" {
		t.Fatalf("output=%s", out)
	}
}

func TestMachineIIOReadRejectsFilesystemEscape(t *testing.T) {
	it := NewInterpreter(NewChecker(), func(string) {})
	it.AllowDevice = true
	tok := Token{File: "<machine-test>", Line: 1, Col: 1}
	_, err := it.callNativeModule("machine", "iio_read", []Value{"/etc/hosts", machineDecimal(t, "1")}, tok)
	if err == nil || !strings.Contains(err.Error(), "restricted to /sys/bus/iio/devices") {
		t.Fatalf("expected IIO path restriction, got %v", err)
	}
}

func TestMachineMonotonicClockIncreases(t *testing.T) {
	it := NewInterpreter(NewChecker(), func(string) {})
	tok := Token{File: "<machine-test>", Line: 1, Col: 1}
	first, err := it.callNativeModule("machine", "monotonic_ns", nil, tok)
	if err != nil {
		t.Fatal(err)
	}
	time.Sleep(2 * time.Millisecond)
	second, err := it.callNativeModule("machine", "monotonic_ns", nil, tok)
	if err != nil {
		t.Fatal(err)
	}
	if second.(Number).R.Cmp(first.(Number).R) <= 0 {
		t.Fatalf("monotonic clock did not increase: %v -> %v", first, second)
	}
}

func TestMachineEncoderWraparound(t *testing.T) {
	enc, err := newMachineEncoder(4096, 1)
	if err != nil {
		t.Fatal(err)
	}
	if err := enc.setWrapModulus(65536); err != nil {
		t.Fatal(err)
	}
	if err := enc.update(65530, 1_000_000_000); err != nil {
		t.Fatal(err)
	}
	if err := enc.update(4, 2_000_000_000); err != nil {
		t.Fatal(err)
	}
	if enc.UnwrappedCount != 65540 {
		t.Fatalf("unwrapped=%d", enc.UnwrappedCount)
	}
	if enc.VelocityRPM <= 0 {
		t.Fatalf("velocity=%f", enc.VelocityRPM)
	}
}

func TestMachineLowPassAndSafetyHelpers(t *testing.T) {
	got, err := machineLowPass(0, 10, 0.25)
	if err != nil || got != 2.5 {
		t.Fatalf("low pass=%v err=%v", got, err)
	}
	latch := &MachineSafety{}
	if err := latch.trip("limit"); err != nil {
		t.Fatal(err)
	}
	tripped, reason := latch.snapshot()
	if !tripped || reason != "limit" {
		t.Fatalf("safety=%v %q", tripped, reason)
	}
}

func TestMachineSafetyClearRejectedDuringTrip(t *testing.T) {
	latch := &MachineSafety{}
	entered := make(chan struct{})
	release := make(chan struct{})
	if err := latch.registerStop(func() error {
		close(entered)
		<-release
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- latch.trip("concurrent stop") }()
	select {
	case <-entered:
	case <-time.After(time.Second):
		t.Fatal("stopper was not entered")
	}
	if err := latch.clear(); err == nil {
		t.Fatal("expected clear to be rejected during trip")
	}
	close(release)
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	tripped, _ := latch.snapshot()
	if !tripped {
		t.Fatal("trip was lost after concurrent clear attempt")
	}
}

func TestMachineSCurveAndAxisControl036(t *testing.T) {
	curve, err := newMachineSCurve(0, 0, 0, 1, 2, 4, 20)
	if err != nil {
		t.Fatal(err)
	}
	for j := 0; j < 2000 && !curve.done(); j++ {
		pos, err := curve.step(0.005)
		if err != nil {
			t.Fatal(err)
		}
		if pos < 0 || pos > 1 {
			t.Fatalf("S-curve overshoot: %v", pos)
		}
	}
	if !curve.done() || curve.Position != 1 {
		t.Fatalf("curve did not settle: %+v", curve)
	}

	latch := &MachineSafety{}
	axis, err := newMachineAxis(0, -10, 10, 2, 4, 1, 0, 0, 0.01, latch)
	if err != nil {
		t.Fatal(err)
	}
	if err := axis.setTarget(1); err != nil {
		t.Fatal(err)
	}
	if _, err := axis.step(-1, 0.1); err == nil {
		t.Fatal("expected following-error trip")
	}
	tripped, reason := latch.snapshot()
	if !tripped || reason != "axis following error exceeded" || axis.Command != 0 {
		t.Fatalf("unexpected axis safety state: tripped=%v reason=%q command=%v", tripped, reason, axis.Command)
	}
}

func TestMachineModbusCRCAndTCPFraming036(t *testing.T) {
	request := []byte{0x01, 0x03, 0x00, 0x00, 0x00, 0x0a}
	if got := machineModbusCRC16(request); got != 0xcdc5 {
		t.Fatalf("CRC=%#x", got)
	}

	client, server := net.Pipe()
	defer client.Close()
	defer server.Close()
	master := &machineModbusTCP{conn: client, unit: 7, timeout: time.Second}
	done := make(chan error, 1)
	go func() {
		header := make([]byte, 7)
		if _, err := io.ReadFull(server, header); err != nil {
			done <- err
			return
		}
		length := int(binary.BigEndian.Uint16(header[4:6]))
		pdu := make([]byte, length-1)
		if _, err := io.ReadFull(server, pdu); err != nil {
			done <- err
			return
		}
		if header[6] != 7 || pdu[0] != 3 {
			done <- fmt.Errorf("bad request frame")
			return
		}
		responsePDU := []byte{3, 4, 0, 42, 0x12, 0x34}
		response := make([]byte, 7+len(responsePDU))
		copy(response[:2], header[:2])
		binary.BigEndian.PutUint16(response[4:6], uint16(len(responsePDU)+1))
		response[6] = 7
		copy(response[7:], responsePDU)
		_, err := server.Write(response)
		done <- err
	}()
	values, err := master.readHolding(0, 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(values) != 2 || values[0].(Number).String() != "42" || values[1].(Number).String() != "4660" {
		t.Fatalf("registers=%v", values)
	}
	if err := <-done; err != nil {
		t.Fatal(err)
	}
}

func TestMachineSurface036ChecksAndRuns(t *testing.T) {
	src := `use machine
let curve = machine.s_curve(0.0, 0.0, 0.0, 1.0, 2.0, 4.0, 20.0)
print(machine.s_curve_step(curve, 0.01) >= 0.0)
let safety = machine.safety_latch()
let axis = machine.axis(0.0, -10.0, 10.0, 2.0, 4.0, 1.0, 0.0, 0.0, 2.0, safety)
machine.axis_target(axis, 1.0)
print(machine.axis_step(axis, 0.0, 0.01) >= 0.0)
print(machine.modbus_crc16(machine.bytes_from_hex("01030000000a")))`
	out, err := runSagaForTest(t, src)
	if err != nil {
		t.Fatal(err)
	}
	if out != "true\ntrue\n52677" {
		t.Fatalf("output=%s", out)
	}
}

func TestMachineModbusTCPRequiresExplicitNetworkGrant036(t *testing.T) {
	it := NewInterpreter(NewChecker(), func(string) {})
	if err := it.requireNet("127.0.0.1", 502); err == nil {
		t.Fatal("expected network denial")
	}
	it.NetHosts = []string{"127.0.0.1:502"}
	if err := it.requireNet("127.0.0.1", 502); err != nil {
		t.Fatal(err)
	}
	if err := it.requireNet("127.0.0.1", 503); err == nil {
		t.Fatal("port-scoped grant must not allow a different port")
	}
}

func TestMachine4kHzLogicalCycle044(t *testing.T) {
	it := NewInterpreter(NewChecker(), func(string) {})
	tok := Token{File: "<machine-4khz-test>", Line: 1, Col: 1}
	v, err := it.callNativeModule("machine", "cyclic_clock", []Value{numberFromInt64(4000)}, tok)
	if err != nil {
		t.Fatal(err)
	}
	cycle, ok := v.(*MachineCycle)
	if !ok {
		t.Fatalf("cycle type=%T", v)
	}
	start := time.Now()
	var total int64
	for total < 400 {
		due, err := it.callNativeModule("machine", "cycle_wait_due", []Value{cycle}, tok)
		if err != nil {
			t.Fatal(err)
		}
		n, ok := due.(Number)
		if !ok {
			t.Fatalf("due type=%T", due)
		}
		parsed, ok := n.Int()
		if !ok || !parsed.IsInt64() {
			t.Fatalf("due is not an int64: %v", n)
		}
		total += parsed.Int64()
	}
	elapsed := time.Since(start)
	if elapsed < 80*time.Millisecond || elapsed > 180*time.Millisecond {
		t.Fatalf("400 logical 4kHz ticks elapsed=%s", elapsed)
	}
	stats, err := it.callNativeModule("machine", "cycle_stats_json", []Value{cycle}, tok)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(stats.(string), `"period_us":250`) {
		t.Fatalf("stats=%s", stats)
	}
}

func TestPrecisionMachine046PIDObserverAndFOC(t *testing.T) {
	pid, err := newMachinePID2(1, 0, 0, 1, 0, 0, -10, 10)
	if err != nil {
		t.Fatal(err)
	}
	out, err := pid.step(2, .5, 0, .01)
	if err != nil || math.Abs(out-1.5) > 1e-12 {
		t.Fatalf("pid2=%v err=%v", out, err)
	}

	observer, err := newMachineAlphaBeta(.5, .1, 0, 0)
	if err != nil {
		t.Fatal(err)
	}
	position, velocity, err := observer.step(2, .1)
	if err != nil || math.Abs(position-1) > 1e-12 || math.Abs(velocity-2) > 1e-12 {
		t.Fatalf("observer=(%v,%v) err=%v", position, velocity, err)
	}

	ab0, err := machineClarke(1, -.5, -.5)
	if err != nil || math.Abs(ab0[0]-1) > 1e-12 || math.Abs(ab0[1]) > 1e-12 || math.Abs(ab0[2]) > 1e-12 {
		t.Fatalf("clarke=%v err=%v", ab0, err)
	}
	dq, err := machinePark(1, 0, 0)
	if err != nil || math.Abs(dq[0]-1) > 1e-12 || math.Abs(dq[1]) > 1e-12 {
		t.Fatalf("park=%v err=%v", dq, err)
	}
	duty, err := machineSVPWM(1, 0, 4)
	if err != nil || math.Abs(duty[0]-.6875) > 1e-12 || math.Abs(duty[1]-.3125) > 1e-12 || math.Abs(duty[2]-.3125) > 1e-12 {
		t.Fatalf("svpwm=%v err=%v", duty, err)
	}
}

func TestPrecisionMachine046NotchAndBudget(t *testing.T) {
	filter, err := newMachineNotch(1000, 120, 5)
	if err != nil {
		t.Fatal(err)
	}
	first := make([]float64, 8)
	for j := range first {
		first[j], err = filter.step(1)
		if err != nil || !finiteFloat(first[j]) {
			t.Fatalf("notch step=%v err=%v", first[j], err)
		}
	}
	filter.reset()
	for j := range first {
		got, e := filter.step(1)
		if e != nil || math.Abs(got-first[j]) > 1e-15 {
			t.Fatalf("notch reset step %d=%v want=%v err=%v", j, got, first[j], e)
		}
	}

	budget, err := newMachineDeadlineBudget(1_000_000, 1_000_000)
	if err != nil {
		t.Fatal(err)
	}
	if err := budget.begin(); err != nil {
		t.Fatal(err)
	}
	over, err := budget.end()
	if err != nil || over {
		t.Fatalf("budget over=%v err=%v", over, err)
	}
	if budget.Samples != 1 {
		t.Fatalf("budget samples=%d", budget.Samples)
	}
}

func TestPrecisionMachine046SagaSurface(t *testing.T) {
	src := `use machine
let controller = machine.pid2(1.0, 0.0, 0.0, 1.0, 0.0, 0.0, -10.0, 10.0)
print(machine.pid2_step(controller, 2.0, 0.5, 0.0, 0.01))
print(machine.motor_feedforward(0.2, 1.5, 0.1, 2.0, 3.0))
let observer = machine.alpha_beta(0.5, 0.1, 0.0, 0.0)
let estimate = machine.alpha_beta_step(observer, 2.0, 0.1)
print(estimate[0])
print(estimate[1])
let dq = machine.park(1.0, 0.0, 0.0)
print(dq[0])
let duty = machine.svpwm(1.0, 0.0, 4.0)
print(duty[0])`
	out, err := runSagaForTest(t, src)
	if err != nil {
		t.Fatal(err)
	}
	if out != "1.5\n3.5\n1\n2\n1\n0.6875" {
		t.Fatalf("output=%s", out)
	}
}

func TestAdvancedMotion047PortableSurface(t *testing.T) {
	foc, err := newMachineFOCCurrent([]float64{2, 20, 2, 20, .1, .001, .001, .02, 20, 24, 10})
	if err != nil {
		t.Fatal(err)
	}
	if err := foc.step(0, 0, 0, 0, 0, 0, 0, 48, .0001); err != nil {
		t.Fatal(err)
	}
	if math.Abs(foc.DutyA-.5) > 1e-12 || math.Abs(foc.DutyB-.5) > 1e-12 || math.Abs(foc.DutyC-.5) > 1e-12 {
		t.Fatalf("unexpected centered FOC duty: %v %v %v", foc.DutyA, foc.DutyB, foc.DutyC)
	}
	enc, err := newMachineUnifiedEncoder(4096, 1, 4096, 1, 1)
	if err != nil {
		t.Fatal(err)
	}
	if err := enc.sample(4090, 1_000_000_000); err != nil {
		t.Fatal(err)
	}
	if err := enc.sample(2, 1_010_000_000); err != nil {
		t.Fatal(err)
	}
	if enc.Unwrapped != 4098 || enc.Velocity <= 0 {
		t.Fatalf("encoder=%+v", enc)
	}
	rls, err := newMachineRLS2(.995, 1000)
	if err != nil {
		t.Fatal(err)
	}
	for i := 1; i < 80; i++ {
		x0 := float64(i) / 10
		x1 := float64((i*7)%13) / 10
		rls.update(x0, x1, 2*x0-.5*x1)
	}
	if math.Abs(rls.Theta0-2) > 1e-3 || math.Abs(rls.Theta1+.5) > 1e-3 {
		t.Fatalf("rls=%+v", rls)
	}
}

func TestAdvancedMotion047SagaAndControlTick(t *testing.T) {
	src := `use machine
let enc = machine.encoder_integrated(4096, 1.0, 4096, 1, 1.0)
machine.encoder_sample(enc, 4090, 1000000000)
machine.encoder_sample(enc, 2, 1010000000)
print(machine.encoder_position_deg(enc))
let ec = machine.ethercat_lrw(1, 305419896, machine.bytes_from_hex("1122"))
print(machine.ethercat_first_datagram_json(ec))`
	out, err := runSagaForTest(t, src)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(out, "360.17578125\n") || !strings.Contains(out, `"command":"LRW"`) {
		t.Fatalf("output=%s", out)
	}

	bad := `use machine
@control_tick
fn tick() -> int {
  let packet = machine.canfd_recv(0, 1)
  return 0
}`
	_, err = runSagaForTest(t, bad)
	if err == nil || !strings.Contains(err.Error(), "machine.canfd_recv") {
		t.Fatalf("control profile error=%v", err)
	}
}

func TestProductionIndustrial049ControlGuardAndContract(t *testing.T) {
	g, err := newMachineControlGuard(20000, 35, 100, 5)
	if err != nil {
		t.Fatal(err)
	}
	ok, err := g.begin(999950000, 1000000000)
	if err != nil || !ok {
		t.Fatalf("begin ok=%v err=%v", ok, err)
	}
	ok, err = g.end(1000020000)
	if err != nil || !ok {
		t.Fatalf("end ok=%v err=%v", ok, err)
	}
	ok, err = g.begin(1000000000, 1000050000)
	if err != nil || !ok {
		t.Fatalf("second begin ok=%v err=%v", ok, err)
	}
	ok, err = g.end(1000090000)
	if err != nil || ok {
		t.Fatalf("budget miss expected ok=%v err=%v", ok, err)
	}
	if g.BudgetMisses != 1 || g.Samples != 2 {
		t.Fatalf("guard=%+v", g)
	}

	good := `@control_tick(20000, 35)
fn tick(x: int) -> int { return x }`
	if _, err := runSagaForTest(t, good); err != nil {
		t.Fatal(err)
	}
	bad := `@control_tick(20000, 60)
fn tick(x: int) -> int { return x }`
	if _, err := runSagaForTest(t, bad); err == nil || !strings.Contains(err.Error(), "budget_us exceeds") {
		t.Fatalf("expected control budget diagnostic, got %v", err)
	}
}

func TestControlGA050TransitiveControlProfile(t *testing.T) {
	good := `@control_tick(1000, 500)
fn tick(x: int) -> int { return helper(x) }
@control_safe
fn helper(x: int) -> int { return x + 1 }`
	if _, err := runSagaForTest(t, good); err != nil {
		t.Fatal(err)
	}

	unverified := `@control_tick(1000, 500)
fn tick(x: int) -> int { return helper(x) }
fn helper(x: int) -> int { return x + 1 }`
	if _, err := runSagaForTest(t, unverified); err == nil || !strings.Contains(err.Error(), "unverified user function helper") {
		t.Fatalf("expected SAGA-C490 for unverified helper, got %v", err)
	}

	recursive := `@control_tick(1000, 500)
fn tick(x: int) -> int { return helper(x) }
@control_safe
fn helper(x: int) -> int { return tick(x) }`
	if _, err := runSagaForTest(t, recursive); err == nil || !strings.Contains(err.Error(), "call graph cannot be recursive") {
		t.Fatalf("expected SAGA-C485 for recursive control call graph, got %v", err)
	}
}

func TestControlGA050MoveRemainsContextualIdentifier(t *testing.T) {
	src := `let move = 1
if move < 2 { print(move) }`
	out, err := runSagaForTest(t, src)
	if err != nil {
		t.Fatal(err)
	}
	if out != "1" {
		t.Fatalf("output=%q", out)
	}
}


func TestControl60kHz054RationalPhaseAndFractionalBudget(t *testing.T) {
	clock, err := newMachineCycleHz(60000)
	if err != nil {
		t.Fatal(err)
	}
	if got := clock.deadlineForCycle(60000).Sub(clock.Anchor); got != time.Second {
		t.Fatalf("60000-cycle phase=%s", got)
	}
	if got := clock.deadlineForCycle(120000).Sub(clock.Anchor); got != 2*time.Second {
		t.Fatalf("120000-cycle phase=%s", got)
	}

	good := `use machine
@control_tick(60000, 12.5)
fn tick(previous: decimal, sample: decimal) -> decimal {
  let limited = machine.slew(previous, sample, 1000.0, 0.0000166666666666667)
  let filtered = machine.low_pass(previous, limited, 0.25)
  let centered = machine.deadband(filtered, 0.001)
  return machine.integrate_clamped(previous, centered, 0.0000166666666666667, -1.0, 1.0)
}`
	if _, err := runSagaForTest(t, good); err != nil {
		t.Fatal(err)
	}

	bad := `@control_tick(60000, 16.7)
fn tick(value: decimal) -> decimal { return value }`
	if _, err := runSagaForTest(t, bad); err == nil || !strings.Contains(err.Error(), "exceeds the declared period") {
		t.Fatalf("expected 60 kHz overbudget rejection, got %v", err)
	}
}

func TestControlSignalPrimitives054(t *testing.T) {
	src := `use machine
print(machine.slew(0.0, 1.0, 10.0, 0.02))
print(machine.low_pass(0.0, 1.0, 0.25))
print(machine.deadband(0.5, 0.1))
print(machine.integrate_clamped(0.9, 1.0, 0.2, -1.0, 1.0))`
	out, err := runSagaForTest(t, src)
	if err != nil {
		t.Fatal(err)
	}
	if out != "0.2\n0.25\n0.4\n1" {
		t.Fatalf("output=%q", out)
	}
}
