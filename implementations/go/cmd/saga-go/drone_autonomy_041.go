package main

import (
	"encoding/json"
	"fmt"
	"math"
)

type droneTrajectory3D struct{ Axes [3]*MachineSCurve }

func newDroneTrajectory3D(pos, target [3]float64, maxV, maxA, maxJ float64) (*droneTrajectory3D, error) {
	var t droneTrajectory3D
	for j := 0; j < 3; j++ {
		p, e := newMachineSCurve(pos[j], 0, 0, target[j], maxV, maxA, maxJ)
		if e != nil {
			return nil, e
		}
		t.Axes[j] = p
	}
	return &t, nil
}
func (t *droneTrajectory3D) step(dt float64) (map[string]any, error) {
	pos := make([]float64, 3)
	vel := make([]float64, 3)
	acc := make([]float64, 3)
	for j, a := range t.Axes {
		p, e := a.step(dt)
		if e != nil {
			return nil, e
		}
		pos[j] = p
		vel[j] = a.Velocity
		acc[j] = a.Acceleration
	}
	return map[string]any{"position": pos, "velocity": vel, "acceleration": acc}, nil
}
func (t *droneTrajectory3D) retarget(v [3]float64) error {
	for j, a := range t.Axes {
		if e := a.retarget(v[j]); e != nil {
			return e
		}
	}
	return nil
}
func (t *droneTrajectory3D) done() bool {
	for _, a := range t.Axes {
		if !a.done() {
			return false
		}
	}
	return true
}

type droneAllocator struct {
	Matrix   [][4]float64
	Min, Max float64
	Disabled map[int]bool
}

func solve4(a [4][4]float64, b [4]float64) ([4]float64, error) {
	aug := [4][5]float64{}
	for r := 0; r < 4; r++ {
		for c := 0; c < 4; c++ {
			aug[r][c] = a[r][c]
		}
		aug[r][4] = b[r]
	}
	for c := 0; c < 4; c++ {
		p := c
		for r := c + 1; r < 4; r++ {
			if math.Abs(aug[r][c]) > math.Abs(aug[p][c]) {
				p = r
			}
		}
		if math.Abs(aug[p][c]) < 1e-12 {
			return [4]float64{}, fmt.Errorf("control allocation matrix is singular")
		}
		aug[c], aug[p] = aug[p], aug[c]
		d := aug[c][c]
		for k := c; k < 5; k++ {
			aug[c][k] /= d
		}
		for r := 0; r < 4; r++ {
			if r == c {
				continue
			}
			f := aug[r][c]
			for k := c; k < 5; k++ {
				aug[r][k] -= f * aug[c][k]
			}
		}
	}
	return [4]float64{aug[0][4], aug[1][4], aug[2][4], aug[3][4]}, nil
}
func (a *droneAllocator) allocate(d [4]float64) ([]float64, error) {
	active := 0
	var gram [4][4]float64
	for i, row := range a.Matrix {
		if a.Disabled[i] {
			continue
		}
		active++
		for r := 0; r < 4; r++ {
			for c := 0; c < 4; c++ {
				gram[r][c] += row[r] * row[c]
			}
		}
	}
	if active < 4 {
		return nil, fmt.Errorf("fewer than four active actuators")
	}
	dual, e := solve4(gram, d)
	if e != nil {
		return nil, e
	}
	out := make([]float64, len(a.Matrix))
	for i, row := range a.Matrix {
		if a.Disabled[i] {
			continue
		}
		v := 0.0
		for j := 0; j < 4; j++ {
			v += row[j] * dual[j]
		}
		out[i] = clampFloat(v, a.Min, a.Max)
	}
	return out, nil
}

func (a *droneAllocator) report(d [4]float64) (string, error) {
	commands, err := a.allocate(d)
	if err != nil {
		return "", err
	}
	achieved := [4]float64{}
	saturated := []int{}
	disabled := []int{}
	for i, command := range commands {
		if a.Disabled[i] {
			disabled = append(disabled, i)
			continue
		}
		if command == a.Min || command == a.Max {
			saturated = append(saturated, i)
		}
		for axis := 0; axis < 4; axis++ {
			achieved[axis] += a.Matrix[i][axis] * command
		}
	}
	residual := [4]float64{}
	for axis := 0; axis < 4; axis++ {
		residual[axis] = d[axis] - achieved[axis]
	}
	b, _ := json.Marshal(map[string]any{"commands": commands, "requested": d, "achieved": achieved, "residual": residual, "saturated": saturated, "disabled": disabled})
	return string(b), nil
}

type droneLinkMonitor struct {
	Last                                   int
	HasLast                                bool
	Received, Lost, Duplicates, OutOfOrder int
	Latency, Alpha                         float64
}

func (m *droneLinkMonitor) observe(seq int, lat float64) error {
	if seq < 0 || seq > 255 || lat < 0 || !finiteFloat(lat) {
		return fmt.Errorf("invalid link sample")
	}
	m.Received++
	if m.Received == 1 {
		m.Latency = lat
	} else {
		m.Latency = m.Alpha*lat + (1-m.Alpha)*m.Latency
	}
	if !m.HasLast {
		m.Last = seq
		m.HasLast = true
		return nil
	}

	d := (seq - m.Last) & 255
	switch {
	case d == 0:
		m.Duplicates++
		// A duplicate does not advance the accepted stream sequence.
	case d < 128:
		if d > 1 {
			m.Lost += d - 1
		}
		m.Last = seq
	default:
		m.OutOfOrder++
		// Older packets are observations only; retaining Last prevents a later
		// in-order packet from being counted as a fresh loss gap.
	}
	return nil
}
func (m *droneLinkMonitor) stats() string {
	expected := m.Received + m.Lost
	loss := 0.0
	if expected > 0 {
		loss = float64(m.Lost) / float64(expected)
	}
	b, _ := json.Marshal(map[string]any{"received": m.Received, "lost": m.Lost, "duplicates": m.Duplicates, "out_of_order": m.OutOfOrder, "loss_fraction": loss, "latency_ms_ewma": m.Latency})
	return string(b)
}
