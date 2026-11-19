package main

import (
	"bufio"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"math/big"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

const defaultHTTPBodyLimit = 8 << 20
const defaultHTTPTimeout = 30 * time.Second

var sagaHTTPClient = &http.Client{
	Timeout: defaultHTTPTimeout,
	Transport: &http.Transport{Proxy: nil},
	CheckRedirect: func(req *http.Request, via []*http.Request) error {
		return http.ErrUseLastResponse
	},
}

func standardHTTPClientForPolicy() (*http.Client, error) {
	client := *sagaHTTPClient
	raw := strings.TrimSpace(os.Getenv("SAGA_HTTP_TIMEOUT_MS"))
	if raw == "" {
		return &client, nil
	}
	ms, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || ms <= 0 {
		return nil, fmt.Errorf("SAGA_HTTP_TIMEOUT_MS must be a positive integer")
	}
	client.Timeout = time.Duration(ms) * time.Millisecond
	return &client, nil
}

func readStandardHTTPText(r io.Reader) (string, error) {
	max := int64(defaultHTTPBodyLimit)
	raw := strings.TrimSpace(os.Getenv("SAGA_HTTP_MAX_BODY_BYTES"))
	if raw != "" {
		parsed, err := strconv.ParseInt(raw, 10, 64)
		if err != nil || parsed <= 0 || parsed == 1<<63-1 {
			return "", fmt.Errorf("SAGA_HTTP_MAX_BODY_BYTES must be a positive integer below MaxInt64")
		}
		max = parsed
	}
	b, err := io.ReadAll(io.LimitReader(r, max+1))
	if err != nil {
		return "", err
	}
	if int64(len(b)) > max {
		return "", fmt.Errorf("HTTP response body exceeds administrator limit %d bytes", max)
	}
	if !validUTF8String(string(b)) {
		return "", fmt.Errorf("HTTP response body is not valid UTF-8")
	}
	return string(b), nil
}

var sagaGameInputMu sync.Mutex
var sagaGameInputReader = bufio.NewReader(os.Stdin)

type GameCanvas struct {
	W, H  int
	Cells [][]rune
}

func newGameCanvas(w, h int) (*GameCanvas, error) {
	if w <= 0 || h <= 0 {
		return nil, fmt.Errorf("game.canvas width/height must be positive")
	}
	// Saga has no language-level fixed size ceiling; allocation failure is a host resource condition.
	c := &GameCanvas{W: w, H: h, Cells: make([][]rune, h)}
	for y := 0; y < h; y++ {
		c.Cells[y] = make([]rune, w)
		for x := range c.Cells[y] {
			c.Cells[y][x] = ' '
		}
	}
	return c, nil
}
func (c *GameCanvas) clear(fill rune) {
	for y := 0; y < c.H; y++ {
		for x := 0; x < c.W; x++ {
			c.Cells[y][x] = fill
		}
	}
}
func (c *GameCanvas) set(x, y int, r rune) {
	if x >= 0 && x < c.W && y >= 0 && y < c.H {
		c.Cells[y][x] = r
	}
}
func (c *GameCanvas) drawText(x, y int, s string) {
	for _, r := range []rune(s) {
		c.set(x, y, r)
		x++
	}
}
func (c *GameCanvas) box(x, y, w, h int, r rune) {
	if w <= 0 || h <= 0 {
		return
	}
	for xx := x; xx < x+w; xx++ {
		c.set(xx, y, r)
		c.set(xx, y+h-1, r)
	}
	for yy := y; yy < y+h; yy++ {
		c.set(x, yy, r)
		c.set(x+w-1, yy, r)
	}
}
func (c *GameCanvas) fillRect(x, y, w, h int, r rune) {
	if w <= 0 || h <= 0 {
		return
	}
	for yy := y; yy < y+h; yy++ {
		for xx := x; xx < x+w; xx++ {
			c.set(xx, yy, r)
		}
	}
}
func (c *GameCanvas) line(x0, y0, x1, y1 int, r rune) {
	dx := int(math.Abs(float64(x1 - x0)))
	sx := -1
	if x0 < x1 {
		sx = 1
	}
	dy := -int(math.Abs(float64(y1 - y0)))
	sy := -1
	if y0 < y1 {
		sy = 1
	}
	err := dx + dy
	for {
		c.set(x0, y0, r)
		if x0 == x1 && y0 == y1 {
			break
		}
		e2 := 2 * err
		if e2 >= dy {
			err += dy
			x0 += sx
		}
		if e2 <= dx {
			err += dx
			y0 += sy
		}
	}
}
func (c *GameCanvas) circle(cx, cy, radius int, r rune) {
	if radius < 0 {
		return
	}
	x, y := radius, 0
	err := 1 - x
	for x >= y {
		points := [][2]int{{cx + x, cy + y}, {cx + y, cy + x}, {cx - y, cy + x}, {cx - x, cy + y}, {cx - x, cy - y}, {cx - y, cy - x}, {cx + y, cy - x}, {cx + x, cy - y}}
		for _, pt := range points {
			c.set(pt[0], pt[1], r)
		}
		y++
		if err < 0 {
			err += 2*y + 1
		} else {
			x--
			err += 2*(y-x) + 1
		}
	}
}
func (c *GameCanvas) sprite(x, y int, art string) {
	for row, line := range strings.Split(strings.ReplaceAll(art, "\r\n", "\n"), "\n") {
		c.drawText(x, y+row, line)
	}
}
func rectOverlap(ax, ay, aw, ah, bx, by, bw, bh int) bool {
	return aw > 0 && ah > 0 && bw > 0 && bh > 0 && ax < bx+bw && bx < ax+aw && ay < by+bh && by < ay+ah
}
func (c *GameCanvas) String() string {
	var b strings.Builder
	for y, row := range c.Cells {
		b.WriteString(string(row))
		if y+1 < c.H {
			b.WriteByte('\n')
		}
	}
	return b.String()
}

func valueToJSON(v Value) (any, error) {
	switch x := v.(type) {
	case nil:
		return nil, nil
	case bool, string:
		return x, nil
	case []byte:
		return hex.EncodeToString(x), nil
	case Number:
		// Preserve exact finite decimal/integer spelling. Rational non-terminating values are encoded as text.
		if x.R.IsInt() {
			return json.Number(x.R.Num().String()), nil
		}
		if decimalPlaces(x.R) < 50 {
			return json.Number(x.String()), nil
		}
		return x.R.RatString(), nil
	case OptionValue:
		if !x.Present {
			return nil, nil
		}
		return valueToJSON(x.Value)
	case ResultValue:
		y, e := valueToJSON(x.Value)
		if e != nil {
			return nil, e
		}
		if x.OK {
			return map[string]any{"ok": y}, nil
		}
		return map[string]any{"err": y}, nil
	case []Value:
		out := make([]any, len(x))
		for j, v := range x {
			q, e := valueToJSON(v)
			if e != nil {
				return nil, e
			}
			out[j] = q
		}
		return out, nil
	case MapValue:
		out := map[string]any{}
		for _, e := range x.Entries {
			k, ok := e.Key.(string)
			if !ok {
				return nil, fmt.Errorf("json object keys must be text")
			}
			if _, dup := out[k]; dup {
				return nil, fmt.Errorf("duplicate json key: %s", k)
			}
			q, er := valueToJSON(e.Value)
			if er != nil {
				return nil, er
			}
			out[k] = q
		}
		return out, nil
	case SetValue:
		vals := append([]Value{}, x.Items...)
		sort.SliceStable(vals, func(i, j int) bool { return formatValue(vals[i], false) < formatValue(vals[j], false) })
		out := make([]any, len(vals))
		for j, v := range vals {
			q, e := valueToJSON(v)
			if e != nil {
				return nil, e
			}
			out[j] = q
		}
		return out, nil
	default:
		return nil, fmt.Errorf("value is not JSON-serializable")
	}
}

func decodeJSONSaga(text string) (Value, error) {
	dec := json.NewDecoder(strings.NewReader(text))
	dec.UseNumber()
	var read func() (Value, error)
	read = func() (Value, error) {
		tok, err := dec.Token()
		if err != nil {
			return nil, err
		}
		switch q := tok.(type) {
		case json.Delim:
			switch q {
			case '{':
				out := MapValue{}
				seen := map[string]bool{}
				for dec.More() {
					kt, er := dec.Token()
					if er != nil {
						return nil, er
					}
					k, ok := kt.(string)
					if !ok {
						return nil, fmt.Errorf("JSON object key must be text")
					}
					if seen[k] {
						return nil, fmt.Errorf("duplicate JSON key: %s", k)
					}
					seen[k] = true
					v, er := read()
					if er != nil {
						return nil, er
					}
					out.Entries = append(out.Entries, MapEntry{Key: k, Value: v})
				}
				if end, er := dec.Token(); er != nil || end != json.Delim('}') {
					if er != nil {
						return nil, er
					}
					return nil, fmt.Errorf("unterminated JSON object")
				}
				sort.SliceStable(out.Entries, func(a, b int) bool { return out.Entries[a].Key.(string) < out.Entries[b].Key.(string) })
				return out, nil
			case '[':
				out := []Value{}
				for dec.More() {
					v, er := read()
					if er != nil {
						return nil, er
					}
					out = append(out, v)
				}
				if end, er := dec.Token(); er != nil || end != json.Delim(']') {
					if er != nil {
						return nil, er
					}
					return nil, fmt.Errorf("unterminated JSON array")
				}
				return out, nil
			default:
				return nil, fmt.Errorf("unexpected JSON delimiter")
			}
		case nil:
			return OptionValue{}, nil
		case bool, string:
			return q, nil
		case json.Number:
			kind := "int"
			if strings.ContainsAny(string(q), ".eE") {
				kind = "decimal"
			}
			return newNumber(string(q), kind)
		default:
			return nil, fmt.Errorf("unsupported JSON token")
		}
	}
	v, err := read()
	if err != nil {
		return nil, err
	}
	if _, err = dec.Token(); err != io.EOF {
		if err == nil {
			return nil, fmt.Errorf("trailing JSON content")
		}
		return nil, err
	}
	return v, nil
}

func jsonToValue(v any) (Value, error) {
	switch x := v.(type) {
	case nil:
		return OptionValue{}, nil
	case bool, string:
		return x, nil
	case json.Number:
		kind := "int"
		if strings.ContainsAny(string(x), ".eE") {
			kind = "decimal"
		}
		return newNumber(string(x), kind)
	case []any:
		out := make([]Value, len(x))
		for j, v := range x {
			q, e := jsonToValue(v)
			if e != nil {
				return nil, e
			}
			out[j] = q
		}
		return out, nil
	case map[string]any:
		keys := make([]string, 0, len(x))
		for k := range x {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		m := MapValue{}
		for _, k := range keys {
			q, e := jsonToValue(x[k])
			if e != nil {
				return nil, e
			}
			m.Entries = append(m.Entries, MapEntry{Key: k, Value: q})
		}
		return m, nil
	default:
		return nil, fmt.Errorf("unsupported JSON value")
	}
}

func numberToInt(v Value) (int, error) {
	n, ok := v.(Number)
	if !ok {
		return 0, fmt.Errorf("int required")
	}
	x, ok := n.Int()
	if !ok || !x.IsInt64() {
		return 0, fmt.Errorf("host-sized int required")
	}
	return int(x.Int64()), nil
}
func numberToFloat(v Value) (float64, error) {
	n, ok := v.(Number)
	if !ok {
		return 0, fmt.Errorf("number required")
	}
	f, _ := new(big.Float).SetRat(n.R).Float64()
	return f, nil
}
func floatToDecimal(v float64, precision int) (Value, error) {
	if math.IsNaN(v) || math.IsInf(v, 0) {
		return nil, fmt.Errorf("non-finite math result")
	}
	s := strconvFloat(v, precision)
	return newNumber(s, "decimal")
}
func strconvFloat(v float64, precision int) string {
	// 17 significant digits round-trip a float64; Saga stores the resulting decimal exactly.
	return fmt.Sprintf("%.17g", v)
}


func (i *Interpreter) callNativeModule(module, name string, args []Value, t Token) (Value, error) {
	if err := i.checkHostAccess(module, name, args); err != nil {
		return nil, i.rerr(t, "SAGA-R103", err.Error())
	}
	if v, handled, err := i.callSecurityModule(module, name, args, t); handled {
		return v, err
	}
	if v, handled, err := i.callPlatformExpansion(module, name, args, t); handled {
		return v, err
	}
	switch module {
	case "embedded":
		return nil, i.rerr(t, "SAGA-R195", "embedded MMIO/interrupt intrinsics require a bare-metal target and cannot execute in the hosted interpreter")
	case "machine":
		return i.callMachineNative(name, args, t)
	case "drone":
		return i.callDroneNative(name, args, t)
	case "vision":
		return i.callVisionNative(name, args, t)
	case "io":
		switch name {
		case "read_text":
			if len(args) != 1 {
				return nil, i.rerr(t, "SAGA-R150", "io.read_text(path)")
			}
			p, ok := args[0].(string)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "path must be text")
			}
			b, e := os.ReadFile(p)
			if e != nil {
				return nil, i.rerr(t, "SAGA-R160", e.Error())
			}
			if !validUTF8String(string(b)) {
				return nil, i.rerr(t, "SAGA-R160", "file is not valid UTF-8")
			}
			return string(b), nil
		case "write_text":
			if len(args) != 2 {
				return nil, i.rerr(t, "SAGA-R150", "io.write_text(path,text)")
			}
			p, pok := args[0].(string)
			s, sok := args[1].(string)
			if !pok || !sok {
				return nil, i.rerr(t, "SAGA-R150", "path/text must be text")
			}
			if e := os.MkdirAll(filepath.Dir(filepath.Clean(p)), 0755); e != nil && filepath.Dir(filepath.Clean(p)) != "." {
				return nil, i.rerr(t, "SAGA-R160", e.Error())
			}
			if e := os.WriteFile(p, []byte(s), 0644); e != nil {
				return nil, i.rerr(t, "SAGA-R160", e.Error())
			}
			return nil, nil
		case "exists":
			if len(args) != 1 {
				return nil, i.rerr(t, "SAGA-R150", "io.exists(path)")
			}
			p, ok := args[0].(string)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "path must be text")
			}
			_, e := os.Stat(p)
			return e == nil, nil
		case "remove":
			if len(args) != 1 {
				return nil, i.rerr(t, "SAGA-R150", "io.remove(path)")
			}
			p, ok := args[0].(string)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "path must be text")
			}
			if e := os.Remove(p); e != nil && !os.IsNotExist(e) {
				return nil, i.rerr(t, "SAGA-R160", e.Error())
			}
			return nil, nil
		case "list":
			if len(args) != 1 {
				return nil, i.rerr(t, "SAGA-R150", "io.list(path)")
			}
			p, ok := args[0].(string)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "path must be text")
			}
			es, e := os.ReadDir(p)
			if e != nil {
				return nil, i.rerr(t, "SAGA-R160", e.Error())
			}
			out := make([]Value, 0, len(es))
			for _, e := range es {
				out = append(out, e.Name())
			}
			return out, nil
		}
	case "json":
		switch name {
		case "encode":
			if len(args) != 1 {
				return nil, i.rerr(t, "SAGA-R150", "json.encode(value)")
			}
			v, e := valueToJSON(args[0])
			if e != nil {
				return nil, i.rerr(t, "SAGA-R161", e.Error())
			}
			b, e := json.Marshal(v)
			if e != nil {
				return nil, i.rerr(t, "SAGA-R161", e.Error())
			}
			return string(b), nil
		case "decode":
			if len(args) != 1 {
				return nil, i.rerr(t, "SAGA-R150", "json.decode(text)")
			}
			s, ok := args[0].(string)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "json text required")
			}
			v, e := decodeJSONSaga(s)
			if e != nil {
				return nil, i.rerr(t, "SAGA-R161", e.Error())
			}
			return v, nil
		}
	case "time":
		switch name {
		case "unix_ms":
			if len(args) != 0 {
				return nil, i.rerr(t, "SAGA-R150", "time.unix_ms()")
			}
			return numberFromBigInt(big.NewInt(time.Now().UnixMilli())), nil
		case "sleep_ms":
			if len(args) != 1 {
				return nil, i.rerr(t, "SAGA-R150", "time.sleep_ms(ms)")
			}
			ms, e := numberToInt(args[0])
			if e != nil || ms < 0 {
				return nil, i.rerr(t, "SAGA-R150", "non-negative int milliseconds required")
			}
			time.Sleep(time.Duration(ms) * time.Millisecond)
			return nil, nil
		}
	case "math":
		switch name {
		case "pi":
			if len(args) != 0 {
				return nil, i.rerr(t, "SAGA-R150", "math.pi()")
			}
			return newNumber("3.14159265358979323846264338327950288419716939937510", "decimal")
		case "sin", "cos", "tan":
			if len(args) != 1 {
				return nil, i.rerr(t, "SAGA-R150", "math function requires one number")
			}
			f, e := numberToFloat(args[0])
			if e != nil {
				return nil, i.rerr(t, "SAGA-R150", e.Error())
			}
			var r float64
			if name == "sin" {
				r = math.Sin(f)
			} else if name == "cos" {
				r = math.Cos(f)
			} else {
				r = math.Tan(f)
			}
			return floatToDecimal(r, i.Precision)
		}
	case "random":
		switch name {
		case "int":
			if len(args) != 2 {
				return nil, i.rerr(t, "SAGA-R150", "random.int(min,max)")
			}
			a, aok := args[0].(Number)
			b, bok := args[1].(Number)
			if !aok || !bok {
				return nil, i.rerr(t, "SAGA-R150", "random.int bounds must be int")
			}
			ai, aok := a.Int()
			bi, bok := b.Int()
			if !aok || !bok || ai.Cmp(bi) > 0 {
				return nil, i.rerr(t, "SAGA-R150", "random.int requires min <= max")
			}
			span := new(big.Int).Sub(bi, ai)
			span.Add(span, big.NewInt(1))
			q, e := rand.Int(rand.Reader, span)
			if e != nil {
				return nil, i.rerr(t, "SAGA-R160", e.Error())
			}
			q.Add(q, ai)
			return numberFromBigInt(q), nil
		case "decimal":
			if len(args) != 0 {
				return nil, i.rerr(t, "SAGA-R150", "random.decimal()")
			}
			buf := make([]byte, 32)
			if _, e := rand.Read(buf); e != nil {
				return nil, i.rerr(t, "SAGA-R160", e.Error())
			}
			n := new(big.Int).SetBytes(buf)
			den := new(big.Int).Lsh(big.NewInt(1), uint(len(buf)*8))
			return Number{R: new(big.Rat).SetFrac(n, den), Kind: "decimal"}, nil
		case "seed":
			return nil, i.rerr(t, "SAGA-R150", "cryptographic random source cannot be seeded")
		}
	case "crypto":
		if name == "sha256" {
			if len(args) != 1 {
				return nil, i.rerr(t, "SAGA-R150", "crypto.sha256(text)")
			}
			s, ok := args[0].(string)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "text required")
			}
			h := sha256.Sum256([]byte(s))
			return hex.EncodeToString(h[:]), nil
		}
	case "net":
		switch name {
		case "connect":
			if len(args) != 2 {
				return nil, i.rerr(t, "SAGA-R150", "net.connect(host,port)")
			}
			host, ok := args[0].(string)
			port, e := numberToInt(args[1])
			if !ok || e != nil {
				return nil, i.rerr(t, "SAGA-R150", "host text and port int required")
			}
			c, e := net.Dial("tcp", net.JoinHostPort(host, strconv.Itoa(port)))
			if e != nil {
				return ResultValue{OK: false, Value: e.Error()}, nil
			}
			return ResultValue{OK: true, Value: &TCPConnValue{Conn: c}}, nil
		case "listen":
			if len(args) != 2 {
				return nil, i.rerr(t, "SAGA-R150", "net.listen(host,port)")
			}
			host, ok := args[0].(string)
			port, e := numberToInt(args[1])
			if !ok || e != nil {
				return nil, i.rerr(t, "SAGA-R150", "host text and port int required")
			}
			l, e := net.Listen("tcp", net.JoinHostPort(host, strconv.Itoa(port)))
			if e != nil {
				return ResultValue{OK: false, Value: e.Error()}, nil
			}
			return ResultValue{OK: true, Value: &TCPListenerValue{Listener: l}}, nil
		case "accept":
			if len(args) != 1 {
				return nil, i.rerr(t, "SAGA-R150", "net.accept(listener)")
			}
			l, ok := args[0].(*TCPListenerValue)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "listener required")
			}
			c, e := l.Listener.Accept()
			if e != nil {
				return ResultValue{OK: false, Value: e.Error()}, nil
			}
			return ResultValue{OK: true, Value: &TCPConnValue{Conn: c}}, nil
		case "send":
			if len(args) != 2 {
				return nil, i.rerr(t, "SAGA-R150", "net.send(conn,text)")
			}
			c, ok := args[0].(*TCPConnValue)
			txt, tok := args[1].(string)
			if !ok || !tok {
				return nil, i.rerr(t, "SAGA-R150", "connection and text required")
			}
			n, e := io.WriteString(c.Conn, txt)
			if e != nil {
				return ResultValue{OK: false, Value: e.Error()}, nil
			}
			return ResultValue{OK: true, Value: numberFromInt64(int64(n))}, nil
		case "recv":
			if len(args) != 2 {
				return nil, i.rerr(t, "SAGA-R150", "net.recv(conn,max_bytes)")
			}
			c, ok := args[0].(*TCPConnValue)
			n, e := numberToInt(args[1])
			if !ok || e != nil || n < 0 || n > 16<<20 {
				return nil, i.rerr(t, "SAGA-R150", "connection and max bytes in 0..16777216 required")
			}
			buf := make([]byte, n)
			got, e := c.Conn.Read(buf)
			if e != nil && e != io.EOF {
				return ResultValue{OK: false, Value: e.Error()}, nil
			}
			return ResultValue{OK: true, Value: string(buf[:got])}, nil
		case "udp":
			if len(args) != 0 {
				return nil, i.rerr(t, "SAGA-R150", "net.udp()")
			}
			c, e := net.ListenUDP("udp", nil)
			if e != nil {
				return nil, i.rerr(t, "SAGA-R160", e.Error())
			}
			return &UDPConnValue{Conn: c}, nil
		case "udp_bind":
			if len(args) != 3 {
				return nil, i.rerr(t, "SAGA-R150", "net.udp_bind(socket,host,port)")
			}
			old, ok := args[0].(*UDPConnValue)
			host, hok := args[1].(string)
			port, e := numberToInt(args[2])
			if !ok || !hok || e != nil {
				return nil, i.rerr(t, "SAGA-R150", "udp socket, host, port required")
			}
			addr, e := net.ResolveUDPAddr("udp", net.JoinHostPort(host, strconv.Itoa(port)))
			if e != nil {
				return nil, i.rerr(t, "SAGA-R160", e.Error())
			}
			c, e := net.ListenUDP("udp", addr)
			if e != nil {
				return nil, i.rerr(t, "SAGA-R160", e.Error())
			}
			_ = old.Conn.Close()
			old.Conn = c
			return nil, nil
		case "udp_send":
			if len(args) != 4 {
				return nil, i.rerr(t, "SAGA-R150", "net.udp_send(socket,bytes,host,port)")
			}
			c, ok := args[0].(*UDPConnValue)
			b, bok := args[1].([]byte)
			host, hok := args[2].(string)
			port, e := numberToInt(args[3])
			if !ok || !bok || !hok || e != nil {
				return nil, i.rerr(t, "SAGA-R150", "udp socket, bytes, host, port required")
			}
			addr, e := net.ResolveUDPAddr("udp", net.JoinHostPort(host, strconv.Itoa(port)))
			if e != nil {
				return nil, i.rerr(t, "SAGA-R160", e.Error())
			}
			n, e := c.Conn.WriteToUDP(b, addr)
			if e != nil {
				return nil, i.rerr(t, "SAGA-R160", e.Error())
			}
			return numberFromInt64(int64(n)), nil
		case "udp_receive":
			if len(args) != 2 {
				return nil, i.rerr(t, "SAGA-R150", "net.udp_receive(socket,max_bytes)")
			}
			c, ok := args[0].(*UDPConnValue)
			n, e := numberToInt(args[1])
			if !ok || e != nil || n < 0 || n > 16<<20 {
				return nil, i.rerr(t, "SAGA-R150", "udp socket and max bytes required")
			}
			buf := make([]byte, n)
			got, _, e := c.Conn.ReadFromUDP(buf)
			if e != nil {
				return nil, i.rerr(t, "SAGA-R160", e.Error())
			}
			return buf[:got], nil
		case "udp_receive_from_json":
			if len(args) != 2 {
				return nil, i.rerr(t, "SAGA-R150", "net.udp_receive_from_json(socket,max_bytes)")
			}
			c, ok := args[0].(*UDPConnValue)
			n, e := numberToInt(args[1])
			if !ok || e != nil || n < 0 || n > 16<<20 {
				return nil, i.rerr(t, "SAGA-R150", "udp socket and max bytes required")
			}
			buf := make([]byte, n)
			got, peer, e := c.Conn.ReadFromUDP(buf)
			if e != nil {
				return nil, i.rerr(t, "SAGA-R160", e.Error())
			}
			encoded, _ := json.Marshal(map[string]any{"host": peer.IP.String(), "port": peer.Port, "data_hex": fmt.Sprintf("%x", buf[:got])})
			return string(encoded), nil
		case "set_timeout_ms":
			if len(args) != 2 {
				return nil, i.rerr(t, "SAGA-R150", "net.set_timeout_ms(resource,ms)")
			}
			ms, e := numberToInt(args[1])
			if e != nil || ms < 0 {
				return nil, i.rerr(t, "SAGA-R150", "non-negative timeout required")
			}
			deadline := time.Time{}
			if ms > 0 {
				deadline = time.Now().Add(time.Duration(ms) * time.Millisecond)
			}
			switch q := args[0].(type) {
			case *TCPConnValue:
				e = q.Conn.SetDeadline(deadline)
			case *UDPConnValue:
				e = q.Conn.SetDeadline(deadline)
			default:
				return nil, i.rerr(t, "SAGA-R150", "network resource required")
			}
			if e != nil {
				return nil, i.rerr(t, "SAGA-R160", e.Error())
			}
			return nil, nil
		case "close":
			if len(args) != 1 {
				return nil, i.rerr(t, "SAGA-R150", "net.close(resource)")
			}
			switch q := args[0].(type) {
			case *TCPConnValue:
				return nil, q.Conn.Close()
			case *TCPListenerValue:
				return nil, q.Listener.Close()
			case *UDPConnValue:
				return nil, q.Conn.Close()
			default:
				return nil, i.rerr(t, "SAGA-R150", "network resource required")
			}
		}
	case "http":
		switch name {
		case "get", "status":
			if len(args) != 1 {
				return nil, i.rerr(t, "SAGA-R150", "http.get/status(url)")
			}
			u, ok := args[0].(string)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "url must be text")
			}
			client, e := standardHTTPClientForPolicy()
			if e != nil {
				return ResultValue{OK: false, Value: e.Error()}, nil
			}
			resp, e := client.Get(u)
			if e != nil {
				return ResultValue{OK: false, Value: e.Error()}, nil
			}
			defer resp.Body.Close()
			if name == "status" {
				return ResultValue{OK: true, Value: numberFromInt64(int64(resp.StatusCode))}, nil
			}
			body, e := readStandardHTTPText(resp.Body)
			if e != nil {
				return ResultValue{OK: false, Value: e.Error()}, nil
			}
			return ResultValue{OK: true, Value: body}, nil
		case "post":
			if len(args) != 3 {
				return nil, i.rerr(t, "SAGA-R150", "http.post(url,body,content_type)")
			}
			u, uok := args[0].(string)
			body, bok := args[1].(string)
			ct, cok := args[2].(string)
			if !uok || !bok || !cok {
				return nil, i.rerr(t, "SAGA-R150", "http.post arguments must be text")
			}
			client, e := standardHTTPClientForPolicy()
			if e != nil {
				return ResultValue{OK: false, Value: e.Error()}, nil
			}
			resp, e := client.Post(u, ct, strings.NewReader(body))
			if e != nil {
				return ResultValue{OK: false, Value: e.Error()}, nil
			}
			defer resp.Body.Close()
			bodyText, e := readStandardHTTPText(resp.Body)
			if e != nil {
				return ResultValue{OK: false, Value: e.Error()}, nil
			}
			return ResultValue{OK: true, Value: bodyText}, nil
		}
	case "db":
		switch name {
		case "open":
			if len(args) != 1 {
				return nil, i.rerr(t, "SAGA-R150", "db.open(path)")
			}
			p, ok := args[0].(string)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "path must be text")
			}
			db, e := openKVDB(p)
			if e != nil {
				return ResultValue{OK: false, Value: e.Error()}, nil
			}
			return ResultValue{OK: true, Value: db}, nil
		case "close":
			if len(args) != 1 {
				return nil, i.rerr(t, "SAGA-R150", "db.close(db)")
			}
			db, ok := args[0].(*KVDBValue)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "db required")
			}
			db.Mu.Lock()
			db.Closed = true
			db.Mu.Unlock()
			return nil, nil
		}

	case "process":
		if name == "run" {
			if len(args) != 2 {
				return nil, i.rerr(t, "SAGA-R150", "process.run(command,args)")
			}
			cmdname, ok := args[0].(string)
			list, lok := args[1].([]Value)
			if !ok || !lok {
				return nil, i.rerr(t, "SAGA-R150", "command text and list[text] required")
			}
			av := make([]string, len(list))
			for j, v := range list {
				q, ok := v.(string)
				if !ok {
					return nil, i.rerr(t, "SAGA-R150", "process args must be text")
				}
				av[j] = q
			}
			argv := append([]string{cmdname}, av...)
			out, e, timedOut, truncated := runBoundedProcess(argv, "", 30*time.Second)
			if e != nil {
				detail := strings.TrimSpace(out)
				if timedOut {
					detail = strings.TrimSpace(detail + " process timed out")
				}
				if truncated {
					detail = strings.TrimSpace(detail + " process output truncated")
				}
				if detail != "" {
					detail += ": "
				}
				return ResultValue{OK: false, Value: detail + e.Error()}, nil
			}
			return ResultValue{OK: true, Value: out}, nil
		}
	case "regex":
		switch name {
		case "is_match":
			if len(args) != 2 {
				return nil, i.rerr(t, "SAGA-R150", "regex.is_match(pattern,text)")
			}
			p, pok := args[0].(string)
			txt, tok := args[1].(string)
			if !pok || !tok {
				return nil, i.rerr(t, "SAGA-R150", "pattern/text required")
			}
			r, e := regexp.Compile(p)
			if e != nil {
				return nil, i.rerr(t, "SAGA-R171", e.Error())
			}
			return r.MatchString(txt), nil
		case "find_all":
			if len(args) != 2 {
				return nil, i.rerr(t, "SAGA-R150", "regex.find_all(pattern,text)")
			}
			p, pok := args[0].(string)
			txt, tok := args[1].(string)
			if !pok || !tok {
				return nil, i.rerr(t, "SAGA-R150", "pattern/text required")
			}
			r, e := regexp.Compile(p)
			if e != nil {
				return nil, i.rerr(t, "SAGA-R171", e.Error())
			}
			ms := r.FindAllString(txt, -1)
			out := make([]Value, len(ms))
			for j, q := range ms {
				out[j] = q
			}
			return out, nil
		}
	case "game":
		switch name {
		case "canvas":
			if len(args) != 2 {
				return nil, i.rerr(t, "SAGA-R150", "game.canvas(width,height)")
			}
			w, e := numberToInt(args[0])
			if e != nil {
				return nil, i.rerr(t, "SAGA-R150", e.Error())
			}
			h, e := numberToInt(args[1])
			if e != nil {
				return nil, i.rerr(t, "SAGA-R150", e.Error())
			}
			c, e := newGameCanvas(w, h)
			if e != nil {
				return nil, i.rerr(t, "SAGA-R170", e.Error())
			}
			return c, nil
		case "clear":
			if len(args) != 2 {
				return nil, i.rerr(t, "SAGA-R150", "game.clear(canvas,fill)")
			}
			c, ok := args[0].(*GameCanvas)
			s, sok := args[1].(string)
			if !ok || !sok || len([]rune(s)) != 1 {
				return nil, i.rerr(t, "SAGA-R150", "game.clear requires canvas and one-character text")
			}
			c.clear([]rune(s)[0])
			return nil, nil
		case "set":
			if len(args) != 4 {
				return nil, i.rerr(t, "SAGA-R150", "game.set(canvas,x,y,char)")
			}
			c, ok := args[0].(*GameCanvas)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "canvas required")
			}
			x, e := numberToInt(args[1])
			if e != nil {
				return nil, i.rerr(t, "SAGA-R150", e.Error())
			}
			y, e := numberToInt(args[2])
			if e != nil {
				return nil, i.rerr(t, "SAGA-R150", e.Error())
			}
			s, ok := args[3].(string)
			if !ok || len([]rune(s)) < 1 {
				return nil, i.rerr(t, "SAGA-R150", "character text required")
			}
			c.set(x, y, []rune(s)[0])
			return nil, nil
		case "text":
			if len(args) != 4 {
				return nil, i.rerr(t, "SAGA-R150", "game.text(canvas,x,y,text)")
			}
			c, ok := args[0].(*GameCanvas)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "canvas required")
			}
			x, e := numberToInt(args[1])
			if e != nil {
				return nil, i.rerr(t, "SAGA-R150", e.Error())
			}
			y, e := numberToInt(args[2])
			if e != nil {
				return nil, i.rerr(t, "SAGA-R150", e.Error())
			}
			s, ok := args[3].(string)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "text required")
			}
			c.drawText(x, y, s)
			return nil, nil
		case "present":
			if len(args) != 1 {
				return nil, i.rerr(t, "SAGA-R150", "game.present(canvas)")
			}
			c, ok := args[0].(*GameCanvas)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "canvas required")
			}
			// ANSI clear/home is supported by modern POSIX terminals and Windows Terminal.
			i.emit("\x1b[2J\x1b[H" + c.String())
			return nil, nil
		case "box":
			if len(args) != 6 {
				return nil, i.rerr(t, "SAGA-R150", "game.box(canvas,x,y,w,h,char)")
			}
			c, ok := args[0].(*GameCanvas)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "canvas required")
			}
			vals := make([]int, 4)
			for j := 0; j < 4; j++ {
				q, e := numberToInt(args[j+1])
				if e != nil {
					return nil, i.rerr(t, "SAGA-R150", e.Error())
				}
				vals[j] = q
			}
			ch, ok := args[5].(string)
			if !ok || len([]rune(ch)) < 1 {
				return nil, i.rerr(t, "SAGA-R150", "character required")
			}
			c.box(vals[0], vals[1], vals[2], vals[3], []rune(ch)[0])
			return nil, nil
		case "fill_rect":
			if len(args) != 6 {
				return nil, i.rerr(t, "SAGA-R150", "game.fill_rect(canvas,x,y,w,h,char)")
			}
			c, ok := args[0].(*GameCanvas)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "canvas required")
			}
			v := make([]int, 4)
			for j := range v {
				q, e := numberToInt(args[j+1])
				if e != nil {
					return nil, i.rerr(t, "SAGA-R150", e.Error())
				}
				v[j] = q
			}
			ch, ok := args[5].(string)
			if !ok || len([]rune(ch)) < 1 {
				return nil, i.rerr(t, "SAGA-R150", "character required")
			}
			c.fillRect(v[0], v[1], v[2], v[3], []rune(ch)[0])
			return nil, nil
		case "line":
			if len(args) != 6 {
				return nil, i.rerr(t, "SAGA-R150", "game.line(canvas,x0,y0,x1,y1,char)")
			}
			c, ok := args[0].(*GameCanvas)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "canvas required")
			}
			v := make([]int, 4)
			for j := range v {
				q, e := numberToInt(args[j+1])
				if e != nil {
					return nil, i.rerr(t, "SAGA-R150", e.Error())
				}
				v[j] = q
			}
			ch, ok := args[5].(string)
			if !ok || len([]rune(ch)) < 1 {
				return nil, i.rerr(t, "SAGA-R150", "character required")
			}
			c.line(v[0], v[1], v[2], v[3], []rune(ch)[0])
			return nil, nil
		case "circle":
			if len(args) != 5 {
				return nil, i.rerr(t, "SAGA-R150", "game.circle(canvas,cx,cy,radius,char)")
			}
			c, ok := args[0].(*GameCanvas)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "canvas required")
			}
			v := make([]int, 3)
			for j := range v {
				q, e := numberToInt(args[j+1])
				if e != nil {
					return nil, i.rerr(t, "SAGA-R150", e.Error())
				}
				v[j] = q
			}
			if v[2] < 0 {
				return nil, i.rerr(t, "SAGA-R150", "radius must be non-negative")
			}
			ch, ok := args[4].(string)
			if !ok || len([]rune(ch)) < 1 {
				return nil, i.rerr(t, "SAGA-R150", "character required")
			}
			c.circle(v[0], v[1], v[2], []rune(ch)[0])
			return nil, nil
		case "sprite":
			if len(args) != 4 {
				return nil, i.rerr(t, "SAGA-R150", "game.sprite(canvas,x,y,art)")
			}
			c, ok := args[0].(*GameCanvas)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "canvas required")
			}
			x, e := numberToInt(args[1])
			if e != nil {
				return nil, i.rerr(t, "SAGA-R150", e.Error())
			}
			y, e := numberToInt(args[2])
			if e != nil {
				return nil, i.rerr(t, "SAGA-R150", e.Error())
			}
			art, ok := args[3].(string)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "sprite art must be text")
			}
			c.sprite(x, y, art)
			return nil, nil
		case "point_in_rect":
			if len(args) != 6 {
				return nil, i.rerr(t, "SAGA-R150", "game.point_in_rect(x,y,rx,ry,rw,rh)")
			}
			v := make([]int, 6)
			for j := range v {
				q, e := numberToInt(args[j])
				if e != nil {
					return nil, i.rerr(t, "SAGA-R150", e.Error())
				}
				v[j] = q
			}
			return v[4] > 0 && v[5] > 0 && v[0] >= v[2] && v[0] < v[2]+v[4] && v[1] >= v[3] && v[1] < v[3]+v[5], nil
		case "overlap":
			if len(args) != 8 {
				return nil, i.rerr(t, "SAGA-R150", "game.overlap(ax,ay,aw,ah,bx,by,bw,bh)")
			}
			v := make([]int, 8)
			for j := range v {
				q, e := numberToInt(args[j])
				if e != nil {
					return nil, i.rerr(t, "SAGA-R150", e.Error())
				}
				v[j] = q
			}
			return rectOverlap(v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7]), nil
		case "input":
			if len(args) != 1 {
				return nil, i.rerr(t, "SAGA-R150", "game.input(prompt)")
			}
			prompt, ok := args[0].(string)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "prompt must be text")
			}
			fmt.Fprint(os.Stdout, prompt)
			sagaGameInputMu.Lock()
			line, e := sagaGameInputReader.ReadString('\n')
			sagaGameInputMu.Unlock()
			if e != nil && len(line) == 0 {
				return nil, i.rerr(t, "SAGA-R160", e.Error())
			}
			return strings.TrimRight(line, "\r\n"), nil
		case "clock_ms":
			if len(args) != 0 {
				return nil, i.rerr(t, "SAGA-R150", "game.clock_ms()")
			}
			return numberFromBigInt(big.NewInt(time.Now().UnixMilli())), nil
		case "render":
			if len(args) != 1 {
				return nil, i.rerr(t, "SAGA-R150", "game.render(canvas)")
			}
			c, ok := args[0].(*GameCanvas)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "canvas required")
			}
			return c.String(), nil
		case "frame":
			if len(args) != 1 {
				return nil, i.rerr(t, "SAGA-R150", "game.frame(ms)")
			}
			ms, e := numberToInt(args[0])
			if e != nil || ms < 0 {
				return nil, i.rerr(t, "SAGA-R150", "non-negative frame delay required")
			}
			time.Sleep(time.Duration(ms) * time.Millisecond)
			return nil, nil
		case "width":
			c, ok := args[0].(*GameCanvas)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "canvas required")
			}
			return numberFromInt64(int64(c.W)), nil
		case "height":
			c, ok := args[0].(*GameCanvas)
			if !ok {
				return nil, i.rerr(t, "SAGA-R150", "canvas required")
			}
			return numberFromInt64(int64(c.H)), nil
		}
		if v, handled, err := i.callGameExtended(name, args, t); handled {
			return v, err
		}
	}
	return nil, i.rerr(t, "SAGA-R123", "unknown "+module+" member: "+name)
}

// --- Independent native general-purpose resources ---
type TCPConnValue struct{ Conn net.Conn }
type TCPListenerValue struct{ Listener net.Listener }
type UDPConnValue struct{ Conn *net.UDPConn }
type KVDBValue struct {
	Path     string
	Data     MapValue
	Mu       sync.Mutex
	Revision string
	Closed   bool
}

func openKVDB(path string) (*KVDBValue, error) {
	if strings.TrimSpace(path) == "" {
		return nil, fmt.Errorf("database path must not be empty")
	}
	// Store and persist through one canonical path. Using only a canonical lock
	// identity is insufficient: os.Rename(tmp, symlinkPath) replaces the symlink
	// itself rather than the symlink target, which can silently fork one logical
	// database into two files after the first write through an alias.
	canonicalPath := canonicalDBLockIdentity(path)
	db := &KVDBValue{Path: canonicalPath, Data: MapValue{}}
	lock := sagaDBPathLock(canonicalPath)
	lock.Lock()
	defer lock.Unlock()
	err := withKVFileLock(canonicalPath, false, func() error {
		data, rev, err := loadKVDataUnlocked(canonicalPath)
		if err != nil {
			return err
		}
		db.Data, db.Revision = data, rev
		return nil
	})
	if err != nil {
		return nil, err
	}
	return db, nil
}
