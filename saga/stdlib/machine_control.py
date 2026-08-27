from __future__ import annotations

import ctypes
try:
    import fcntl
except ImportError:  # Windows reference runtime: portable control remains available.
    fcntl = None
import json
import math
import os
import select
import socket
import struct
try:
    import termios
except ImportError:  # Windows reference runtime: UART adapter is unavailable.
    termios = None
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path


class MachineControlError(RuntimeError):
    pass


def _finite(name: str, value: Decimal) -> Decimal:
    if not value.is_finite():
        raise MachineControlError(f"{name} must be finite")
    return value


def _positive(name: str, value: Decimal) -> Decimal:
    value = _finite(name, value)
    if value <= 0:
        raise MachineControlError(f"{name} must be > 0")
    return value


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return min(max(value, low), high)


@dataclass(slots=True)
class PIDController:
    kp: Decimal
    ki: Decimal
    kd: Decimal
    output_min: Decimal
    output_max: Decimal
    integral_min: Decimal
    integral_max: Decimal
    integral: Decimal = Decimal(0)
    previous_error: Decimal | None = None

    @classmethod
    def create(
        cls,
        kp: Decimal,
        ki: Decimal,
        kd: Decimal,
        output_min: Decimal,
        output_max: Decimal,
    ) -> "PIDController":
        for name, value in (("kp", kp), ("ki", ki), ("kd", kd), ("output_min", output_min), ("output_max", output_max)):
            _finite(name, value)
        if output_min >= output_max:
            raise MachineControlError("PID output_min must be smaller than output_max")
        return cls(kp, ki, kd, output_min, output_max, output_min, output_max)

    def set_integral_limits(self, low: Decimal, high: Decimal) -> None:
        _finite("integral_min", low)
        _finite("integral_max", high)
        if low > high:
            raise MachineControlError("PID integral_min must not exceed integral_max")
        self.integral_min = low
        self.integral_max = high
        self.integral = _clamp(self.integral, low, high)

    def reset(self) -> None:
        self.integral = Decimal(0)
        self.previous_error = None

    def step(self, setpoint: Decimal, measurement: Decimal, dt_seconds: Decimal) -> Decimal:
        _finite("setpoint", setpoint)
        _finite("measurement", measurement)
        dt_seconds = _positive("dt_seconds", dt_seconds)
        error = setpoint - measurement
        candidate_integral = _clamp(
            self.integral + error * dt_seconds,
            self.integral_min,
            self.integral_max,
        )
        derivative = Decimal(0)
        if self.previous_error is not None:
            derivative = (error - self.previous_error) / dt_seconds
        unclamped = self.kp * error + self.ki * candidate_integral + self.kd * derivative
        output = _clamp(unclamped, self.output_min, self.output_max)

        # Conditional integration: do not wind the integrator further into saturation.
        pushing_high = unclamped > self.output_max and error > 0
        pushing_low = unclamped < self.output_min and error < 0
        if not (pushing_high or pushing_low):
            self.integral = candidate_integral
        self.previous_error = error
        return output


@dataclass(slots=True)
class MotionProfile:
    position: Decimal
    velocity: Decimal
    target: Decimal
    max_velocity: Decimal
    max_acceleration: Decimal
    tolerance: Decimal = Decimal("0.000001")

    def __post_init__(self) -> None:
        for name in ("position", "velocity", "target"):
            _finite(name, getattr(self, name))
        self.max_velocity = _positive("max_velocity", self.max_velocity)
        self.max_acceleration = _positive("max_acceleration", self.max_acceleration)
        self.tolerance = _positive("tolerance", self.tolerance)

    def retarget(self, target: Decimal) -> None:
        self.target = _finite("target", target)

    def done(self) -> bool:
        return abs(self.target - self.position) <= self.tolerance and abs(self.velocity) <= self.tolerance

    def step(self, dt_seconds: Decimal) -> Decimal:
        dt_seconds = _positive("dt_seconds", dt_seconds)
        distance = self.target - self.position
        if self.done():
            self.position = self.target
            self.velocity = Decimal(0)
            return self.position

        direction = Decimal(1) if distance >= 0 else Decimal(-1)
        signed_velocity = self.velocity * direction
        if signed_velocity < 0:
            acceleration = self.max_acceleration * direction
        else:
            braking_distance = (signed_velocity * signed_velocity) / (Decimal(2) * self.max_acceleration)
            acceleration = (-self.max_acceleration if abs(distance) <= braking_distance else self.max_acceleration) * direction

        next_velocity = self.velocity + acceleration * dt_seconds
        next_velocity = _clamp(next_velocity, -self.max_velocity, self.max_velocity)
        next_position = self.position + (self.velocity + next_velocity) * dt_seconds / Decimal(2)

        # Never step through the target. Reaching the target is an explicit stable state.
        if (self.target - self.position) * (self.target - next_position) <= 0:
            self.position = self.target
            self.velocity = Decimal(0)
        else:
            self.position = next_position
            self.velocity = next_velocity
        return self.position


@dataclass(slots=True)
class Watchdog:
    timeout_ms: int
    _deadline_ns: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        if self.timeout_ms <= 0:
            raise MachineControlError("watchdog timeout_ms must be > 0")
        self.feed()

    def feed(self) -> None:
        deadline = time.monotonic_ns() + self.timeout_ms * 1_000_000
        with self._lock:
            self._deadline_ns = deadline

    def expired(self) -> bool:
        now = time.monotonic_ns()
        with self._lock:
            return now >= self._deadline_ns

    def remaining_ms(self) -> int:
        now = time.monotonic_ns()
        with self._lock:
            remaining = self._deadline_ns - now
        return max(0, (remaining + 999_999) // 1_000_000)


@dataclass(slots=True)
class SafetyLatch:
    tripped: bool = False
    reason: str = ""
    _stoppers: list[object] = field(default_factory=list, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _trip_in_progress: bool = field(default=False, repr=False)

    def register_stop(self, stopper: object) -> None:
        if not callable(stopper):
            raise MachineControlError("safety stopper must be callable")
        with self._lock:
            self._stoppers.append(stopper)
            already_tripped = self.tripped
        if already_tripped:
            try:
                stopper()
            except Exception as exc:
                raise MachineControlError(f"failed to place newly guarded actuator in a safe state: {exc}") from exc

    def trip(self, reason: str) -> None:
        with self._lock:
            self.tripped = True
            self.reason = reason.strip() or "unspecified safety trip"
            self._trip_in_progress = True
            stoppers = tuple(self._stoppers)
        failures: list[str] = []
        try:
            for stopper in stoppers:
                try:
                    stopper()
                except Exception as exc:
                    failures.append(str(exc))
        finally:
            with self._lock:
                self._trip_in_progress = False
        if failures:
            raise MachineControlError("safety trip requested but one or more actuator stops failed: " + "; ".join(failures))

    def clear(self) -> None:
        with self._lock:
            if self._trip_in_progress:
                raise MachineControlError("cannot clear a safety latch while a trip is stopping actuators")
            self.tripped = False
            self.reason = ""


@dataclass(slots=True)
class ControlCycle:
    period_us: int
    next_deadline_ns: int = 0
    overruns: int = 0
    last_jitter_us: int = 0

    def __post_init__(self) -> None:
        if self.period_us <= 0:
            raise MachineControlError("control cycle period_us must be > 0")
        self.next_deadline_ns = time.monotonic_ns() + self.period_us * 1_000

    def wait(self) -> None:
        period_ns = self.period_us * 1_000
        now = time.monotonic_ns()
        if now < self.next_deadline_ns:
            time.sleep((self.next_deadline_ns - now) / 1_000_000_000)
            now = time.monotonic_ns()
        self.last_jitter_us = max(0, (now - self.next_deadline_ns) // 1_000)
        if now > self.next_deadline_ns + period_ns:
            missed = (now - self.next_deadline_ns) // period_ns
            self.overruns += int(missed)
            self.next_deadline_ns += (missed + 1) * period_ns
        else:
            self.next_deadline_ns += period_ns


# ---------- Linux bus adapters ----------


class _I2CMsg(ctypes.Structure):
    _fields_ = [
        ("addr", ctypes.c_uint16),
        ("flags", ctypes.c_uint16),
        ("length", ctypes.c_uint16),
        ("buf", ctypes.c_void_p),
    ]


class _I2CRdwr(ctypes.Structure):
    _fields_ = [("msgs", ctypes.POINTER(_I2CMsg)), ("nmsgs", ctypes.c_uint32)]


class _SPITransfer(ctypes.Structure):
    _fields_ = [
        ("tx_buf", ctypes.c_uint64),
        ("rx_buf", ctypes.c_uint64),
        ("length", ctypes.c_uint32),
        ("speed_hz", ctypes.c_uint32),
        ("delay_usecs", ctypes.c_uint16),
        ("bits_per_word", ctypes.c_uint8),
        ("cs_change", ctypes.c_uint8),
        ("tx_nbits", ctypes.c_uint8),
        ("rx_nbits", ctypes.c_uint8),
        ("word_delay_usecs", ctypes.c_uint8),
        ("pad", ctypes.c_uint8),
    ]


class I2CDevice:
    I2C_SLAVE = 0x0703
    I2C_TENBIT = 0x0704
    I2C_RDWR = 0x0707
    I2C_M_RD = 0x0001
    I2C_M_TEN = 0x0010

    def __init__(self, path: str, address: int):
        if os.name != "posix" or not sys_platform_linux() or fcntl is None:
            raise MachineControlError("I2C adapter currently requires Linux i2c-dev")
        if not 0 <= address <= 0x3FF:
            raise MachineControlError("I2C address must be 0..0x3ff")
        self.path = path
        self.address = address
        self.fd = os.open(path, os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
        try:
            if address > 0x7F:
                fcntl.ioctl(self.fd, self.I2C_TENBIT, 1)
            fcntl.ioctl(self.fd, self.I2C_SLAVE, address)
        except Exception:
            os.close(self.fd)
            raise

    def write(self, payload: bytes) -> None:
        written = os.write(self.fd, payload)
        if written != len(payload):
            raise MachineControlError(f"short I2C write: {written}/{len(payload)} bytes")

    def read(self, count: int) -> bytes:
        if count < 0:
            raise MachineControlError("I2C read count must be >= 0")
        data = os.read(self.fd, count)
        if len(data) != count:
            raise MachineControlError(f"short I2C read: {len(data)}/{count} bytes")
        return data

    def write_read(self, payload: bytes, count: int) -> bytes:
        if count < 0:
            raise MachineControlError("I2C read count must be >= 0")
        if len(payload) > 0xFFFF or count > 0xFFFF:
            raise MachineControlError("I2C combined-transfer segments must be <= 65535 bytes")
        libc = ctypes.CDLL(None, use_errno=True)
        write_buf = (ctypes.c_ubyte * max(1, len(payload)))()
        if payload:
            ctypes.memmove(write_buf, payload, len(payload))
        read_buf = (ctypes.c_ubyte * max(1, count))()
        messages = (_I2CMsg * 2)()
        ten_bit = self.I2C_M_TEN if self.address > 0x7F else 0
        messages[0] = _I2CMsg(self.address, ten_bit, len(payload), ctypes.cast(write_buf, ctypes.c_void_p))
        messages[1] = _I2CMsg(self.address, ten_bit | self.I2C_M_RD, count, ctypes.cast(read_buf, ctypes.c_void_p))
        request = _I2CRdwr(messages, 2)
        rc = libc.ioctl(self.fd, self.I2C_RDWR, ctypes.byref(request))
        if rc < 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err), self.path)
        return bytes(read_buf[:count])

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


class SPIDevice:
    _IOC_NRBITS = 8
    _IOC_TYPEBITS = 8
    _IOC_SIZEBITS = 14
    _IOC_DIRBITS = 2
    _IOC_NRSHIFT = 0
    _IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
    _IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
    _IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS
    _IOC_WRITE = 1
    _MAGIC = ord("k")

    @classmethod
    def _iow(cls, nr: int, size: int) -> int:
        return (
            (cls._IOC_WRITE << cls._IOC_DIRSHIFT)
            | (cls._MAGIC << cls._IOC_TYPESHIFT)
            | (nr << cls._IOC_NRSHIFT)
            | (size << cls._IOC_SIZESHIFT)
        )

    def __init__(self, path: str, speed_hz: int, mode: int, bits_per_word: int):
        if os.name != "posix" or not sys_platform_linux() or fcntl is None:
            raise MachineControlError("SPI adapter currently requires Linux spidev")
        if speed_hz <= 0:
            raise MachineControlError("SPI speed_hz must be > 0")
        if not 0 <= mode <= 3:
            raise MachineControlError("SPI mode must be 0..3")
        if not 1 <= bits_per_word <= 32:
            raise MachineControlError("SPI bits_per_word must be 1..32")
        self.path = path
        self.speed_hz = speed_hz
        self.mode = mode
        self.bits_per_word = bits_per_word
        self.fd = os.open(path, os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
        try:
            mode_buf = bytearray([mode])
            bits_buf = bytearray([bits_per_word])
            speed_buf = bytearray(struct.pack("=I", speed_hz))
            fcntl.ioctl(self.fd, self._iow(1, 1), mode_buf)
            fcntl.ioctl(self.fd, self._iow(3, 1), bits_buf)
            fcntl.ioctl(self.fd, self._iow(4, 4), speed_buf)
        except Exception:
            os.close(self.fd)
            raise

    def transfer(self, payload: bytes) -> bytes:
        if not payload:
            return b""
        tx = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
        rx = (ctypes.c_ubyte * len(payload))()
        transfer = _SPITransfer(
            ctypes.addressof(tx),
            ctypes.addressof(rx),
            len(payload),
            self.speed_hz,
            0,
            self.bits_per_word,
            0,
            0,
            0,
            0,
            0,
        )
        libc = ctypes.CDLL(None, use_errno=True)
        rc = libc.ioctl(self.fd, self._iow(0, ctypes.sizeof(transfer)), ctypes.byref(transfer))
        if rc < 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err), self.path)
        return bytes(rx)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


class UARTDevice:
    _BAUD = {} if termios is None else {
        1200: termios.B1200,
        2400: termios.B2400,
        4800: termios.B4800,
        9600: termios.B9600,
        19200: termios.B19200,
        38400: termios.B38400,
        57600: termios.B57600,
        115200: termios.B115200,
        230400: getattr(termios, "B230400", termios.B115200),
    }

    def __init__(self, path: str, baud: int, timeout_ms: int):
        if os.name != "posix" or not sys_platform_linux() or termios is None:
            raise MachineControlError("UART adapter currently requires Linux termios")
        if baud not in self._BAUD:
            raise MachineControlError(f"unsupported UART baud rate: {baud}")
        if timeout_ms < 0:
            raise MachineControlError("UART timeout_ms must be >= 0")
        self.path = path
        self.timeout_ms = timeout_ms
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | getattr(os, "O_CLOEXEC", 0))
        try:
            attrs = termios.tcgetattr(self.fd)
            attrs[0] = 0
            attrs[1] = 0
            attrs[2] = termios.CLOCAL | termios.CREAD | termios.CS8
            attrs[3] = 0
            attrs[4] = self._BAUD[baud]
            attrs[5] = self._BAUD[baud]
            attrs[6][termios.VMIN] = 0
            attrs[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
            termios.tcflush(self.fd, termios.TCIOFLUSH)
        except Exception:
            os.close(self.fd)
            self.fd = -1
            raise

    def write(self, payload: bytes) -> None:
        offset = 0
        while offset < len(payload):
            written = os.write(self.fd, payload[offset:])
            if written <= 0:
                raise MachineControlError("UART write made no progress")
            offset += written

    def read(self, max_bytes: int) -> bytes:
        if max_bytes < 0:
            raise MachineControlError("UART max_bytes must be >= 0")
        if max_bytes == 0:
            return b""
        ready, _, _ = select.select([self.fd], [], [], self.timeout_ms / 1000)
        if not ready:
            return b""
        return os.read(self.fd, max_bytes)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


def _network_timestamp_from_ancillary(ancillary: list[tuple[int, int, bytes]]) -> tuple[int, str]:
    # Linux SCM_TIMESTAMPING returns three timespec values: software, legacy
    # transformed hardware, and raw hardware. On 64-bit targets each timespec
    # is two signed 64-bit fields. If the host/driver does not provide one, the
    # caller receives an explicitly labelled host fallback instead of a false
    # hardware claim.
    for level, ctype, data in ancillary:
        if level != socket.SOL_SOCKET or ctype not in (37, 65) or len(data) < 48:
            continue
        sec0, ns0, _sec1, _ns1, sec2, ns2 = struct.unpack_from("=qqqqqq", data)
        if sec2 or ns2:
            return sec2 * 1_000_000_000 + ns2, "hardware"
        if sec0 or ns0:
            return sec0 * 1_000_000_000 + ns0, "software"
    return time.time_ns(), "host"


class CANDevice:
    CAN_RAW_FD_FRAMES = 5
    CAN_EFF_FLAG = 0x80000000
    CAN_EFF_MASK = 0x1FFFFFFF
    CAN_SFF_MASK = 0x000007FF
    CANFD_BRS = 0x01
    CANFD_ESI = 0x02
    SO_TIMESTAMPING = 37
    SOF_TIMESTAMPING_RX_HARDWARE = 1 << 2
    SOF_TIMESTAMPING_RX_SOFTWARE = 1 << 3
    SOF_TIMESTAMPING_SOFTWARE = 1 << 4
    SOF_TIMESTAMPING_RAW_HARDWARE = 1 << 6

    def __init__(self, interface: str, fd_mode: bool):
        if not hasattr(socket, "AF_CAN"):
            raise MachineControlError("SocketCAN is not available on this host")
        self.fd_mode = fd_mode
        self.timestamping_enabled = False
        self.sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        try:
            if fd_mode:
                self.sock.setsockopt(socket.SOL_CAN_RAW, self.CAN_RAW_FD_FRAMES, 1)
            self.sock.bind((interface,))
        except Exception:
            self.sock.close()
            raise

    def enable_timestamping(self, hardware_preferred: bool) -> None:
        flags = self.SOF_TIMESTAMPING_RX_SOFTWARE | self.SOF_TIMESTAMPING_SOFTWARE
        if hardware_preferred:
            flags |= self.SOF_TIMESTAMPING_RX_HARDWARE | self.SOF_TIMESTAMPING_RAW_HARDWARE
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, self.SO_TIMESTAMPING, flags)
        except OSError as exc:
            raise MachineControlError(f"SocketCAN timestamping is not available: {exc}") from exc
        self.timestamping_enabled = True

    def send(self, can_id: int, data: bytes, fd_flags: int = 0) -> None:
        if not 0 <= can_id <= 0x1FFFFFFF:
            raise MachineControlError("CAN id must be 0..0x1fffffff")
        limit = 64 if self.fd_mode else 8
        if len(data) > limit:
            raise MachineControlError(f"CAN payload exceeds {limit} bytes")
        wire_id = can_id | self.CAN_EFF_FLAG if can_id > self.CAN_SFF_MASK else can_id
        if fd_flags & ~(self.CANFD_BRS | self.CANFD_ESI):
            raise MachineControlError("unsupported CAN FD flags")
        if fd_flags and not self.fd_mode:
            raise MachineControlError("CAN FD flags require fd_mode")
        if self.fd_mode:
            frame = struct.pack("=IBBBB64s", wire_id, len(data), fd_flags, 0, 0, data.ljust(64, b"\0"))
        else:
            frame = struct.pack("=IB3x8s", wire_id, len(data), data.ljust(8, b"\0"))
        self.sock.send(frame)

    def _recv(self, timeout_ms: int, timestamped: bool) -> tuple[int, bytes, int, int, str] | None:
        if timeout_ms < 0:
            raise MachineControlError("CAN timeout_ms must be >= 0")
        self.sock.settimeout(timeout_ms / 1000)
        try:
            if timestamped and self.timestamping_enabled:
                frame, ancillary, _msg_flags, _addr = self.sock.recvmsg(72 if self.fd_mode else 16, 256)
                timestamp_ns, timestamp_source = _network_timestamp_from_ancillary(ancillary)
            else:
                frame = self.sock.recv(72 if self.fd_mode else 16)
                timestamp_ns, timestamp_source = time.time_ns(), "host"
        except socket.timeout:
            return None
        fd_flags = 0
        if len(frame) == 72:
            can_id, length, fd_flags, _, _, data = struct.unpack("=IBBBB64s", frame)
            if length > 64:
                raise MachineControlError("invalid CAN FD frame length")
        elif len(frame) == 16:
            can_id, length, data = struct.unpack("=IB3x8s", frame)
            if length > 8:
                raise MachineControlError("invalid classic CAN frame length")
        else:
            raise MachineControlError(f"unexpected SocketCAN frame size: {len(frame)}")
        mask = self.CAN_EFF_MASK if can_id & self.CAN_EFF_FLAG else self.CAN_SFF_MASK
        return can_id & mask, data[:length], fd_flags, timestamp_ns, timestamp_source

    def recv(self, timeout_ms: int) -> tuple[int, bytes] | None:
        frame = self._recv(timeout_ms, False)
        return None if frame is None else (frame[0], frame[1])

    def recv_fd_timestamped(self, timeout_ms: int) -> tuple[int, bytes, int, int, str] | None:
        if not self.fd_mode:
            raise MachineControlError("CAN FD receive requires fd_mode")
        return self._recv(timeout_ms, True)

    def close(self) -> None:
        self.sock.close()


class EtherCATRawDevice:
    ETHERTYPE = 0x88A4
    SO_TIMESTAMPING = 37
    SOF_TIMESTAMPING_RX_HARDWARE = 1 << 2
    SOF_TIMESTAMPING_RX_SOFTWARE = 1 << 3
    SOF_TIMESTAMPING_SOFTWARE = 1 << 4
    SOF_TIMESTAMPING_RAW_HARDWARE = 1 << 6

    def __init__(self, interface: str, destination_mac: bytes, hardware_timestamps: bool):
        if not hasattr(socket, "AF_PACKET"):
            raise MachineControlError("EtherCAT raw L2 transport is available on Linux only")
        if len(destination_mac) != 6:
            raise MachineControlError("EtherCAT destination MAC must contain 6 bytes")
        self.destination_mac = bytes(destination_mac)
        self.sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(self.ETHERTYPE))
        try:
            self.sock.bind((interface, 0))
            address = self.sock.getsockname()[4]
            self.source_mac = bytes(address[:6])
            flags = self.SOF_TIMESTAMPING_RX_SOFTWARE | self.SOF_TIMESTAMPING_SOFTWARE
            if hardware_timestamps:
                flags |= self.SOF_TIMESTAMPING_RX_HARDWARE | self.SOF_TIMESTAMPING_RAW_HARDWARE
            self.sock.setsockopt(socket.SOL_SOCKET, self.SO_TIMESTAMPING, flags)
        except Exception:
            self.sock.close()
            raise

    def exchange(self, ethercat_frame: bytes, timeout_ms: int) -> tuple[bytes, int, str]:
        if timeout_ms < 0:
            raise MachineControlError("EtherCAT timeout_ms must be >= 0")
        payload = bytes(ethercat_frame)
        header = self.destination_mac + self.source_mac + struct.pack("!H", self.ETHERTYPE)
        self.sock.send(header + payload)
        self.sock.settimeout(timeout_ms / 1000)
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            self.sock.settimeout(remaining)
            try:
                frame, ancillary, _flags, address = self.sock.recvmsg(65535, 256)
            except socket.timeout:
                raise MachineControlError("EtherCAT exchange timed out")
            if len(address) >= 3 and address[2] == getattr(socket, "PACKET_OUTGOING", 4):
                continue
            if len(frame) < 14 or struct.unpack("!H", frame[12:14])[0] != self.ETHERTYPE:
                continue
            timestamp_ns, source = _network_timestamp_from_ancillary(ancillary)
            return frame[14:], timestamp_ns, source

    def close(self) -> None:
        self.sock.close()


class PWMChannel:
    def __init__(self, chip: int, channel: int, period_ns: int):
        if chip < 0 or channel < 0:
            raise MachineControlError("PWM chip/channel must be >= 0")
        if period_ns <= 0:
            raise MachineControlError("PWM period_ns must be > 0")
        self.period_ns = period_ns
        self.closed = False
        root = Path("/sys/class/pwm") / f"pwmchip{chip}"
        if not root.exists():
            raise MachineControlError(f"PWM chip does not exist: {root}")
        self.path = root / f"pwm{channel}"
        if not self.path.exists():
            (root / "export").write_text(str(channel), encoding="ascii")
            deadline = time.monotonic() + 1.0
            while not self.path.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
        if not self.path.exists():
            raise MachineControlError(f"PWM channel did not appear: {self.path}")
        self._write("enable", "0")
        self._write("period", str(period_ns))
        self._write("duty_cycle", "0")

    def _write(self, name: str, value: str) -> None:
        if self.closed:
            raise MachineControlError("PWM channel is closed")
        (self.path / name).write_text(value, encoding="ascii")

    def set_duty(self, duty: Decimal) -> None:
        _finite("PWM duty", duty)
        if duty < 0 or duty > 1:
            raise MachineControlError("PWM duty must be in 0..1")
        ns = int((duty * Decimal(self.period_ns)).to_integral_value())
        ns = min(max(ns, 0), self.period_ns)
        self._write("duty_cycle", str(ns))

    def enable(self) -> None:
        self._write("enable", "1")

    def disable(self) -> None:
        self._write("enable", "0")

    def close(self) -> None:
        if self.closed:
            return
        try:
            self.disable()
        finally:
            self.closed = True


@dataclass(slots=True)
class EncoderTracker:
    counts_per_revolution: int
    gear_ratio: Decimal = Decimal(1)
    count: int = 0
    position_degrees: Decimal = Decimal(0)
    velocity_rpm: Decimal = Decimal(0)
    _last_count: int | None = None
    _last_time_ns: int | None = None
    _unwrapped_count: int = 0
    _wrap_modulus: int | None = None

    def __post_init__(self) -> None:
        if self.counts_per_revolution <= 0:
            raise MachineControlError("encoder counts_per_revolution must be > 0")
        self.gear_ratio = _positive("gear_ratio", self.gear_ratio)

    def set_wrap_modulus(self, modulus: int) -> None:
        if modulus <= 1:
            raise MachineControlError("encoder wrap modulus must be > 1")
        self._wrap_modulus = modulus

    @property
    def unwrapped_count(self) -> int:
        return self._unwrapped_count

    def update(self, count: int, timestamp_ns: int | None = None) -> None:
        if timestamp_ns is None:
            timestamp_ns = time.monotonic_ns()
        if timestamp_ns < 0:
            raise MachineControlError("encoder timestamp_ns must be >= 0")
        effective_cpr = Decimal(self.counts_per_revolution) * self.gear_ratio
        self.count = count
        if self._last_count is None:
            self._unwrapped_count = count
        else:
            delta_count = count - self._last_count
            if self._wrap_modulus is not None:
                half = self._wrap_modulus // 2
                if delta_count > half:
                    delta_count -= self._wrap_modulus
                elif delta_count < -half:
                    delta_count += self._wrap_modulus
            if self._wrap_modulus is None:
                self._unwrapped_count = count
            else:
                self._unwrapped_count += delta_count
        self.position_degrees = Decimal(self._unwrapped_count) * Decimal(360) / effective_cpr
        if self._last_count is not None and self._last_time_ns is not None:
            dt_ns = timestamp_ns - self._last_time_ns
            if dt_ns <= 0:
                raise MachineControlError("encoder timestamps must increase")
            delta = count - self._last_count
            if self._wrap_modulus is not None:
                half = self._wrap_modulus // 2
                if delta > half:
                    delta -= self._wrap_modulus
                elif delta < -half:
                    delta += self._wrap_modulus
            self.velocity_rpm = Decimal(delta) * Decimal(60_000_000_000) / (effective_cpr * Decimal(dt_ns))
        self._last_count = count
        self._last_time_ns = timestamp_ns

    def reset(self, count: int = 0) -> None:
        self.count = count
        self._unwrapped_count = count
        self.position_degrees = Decimal(0)
        self.velocity_rpm = Decimal(0)
        self._last_count = None
        self._last_time_ns = None


@dataclass(slots=True)
class DCMotor:
    forward: PWMChannel
    reverse: PWMChannel
    deadband: Decimal
    safety: SafetyLatch
    command: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        _finite("motor deadband", self.deadband)
        if self.deadband < 0 or self.deadband >= 1:
            raise MachineControlError("motor deadband must be in 0..1")
        self.safety.register_stop(self.stop)

    def write(self, command: Decimal) -> None:
        _finite("motor command", command)
        if self.safety.tripped:
            self.stop()
            raise MachineControlError(f"motor output blocked by safety latch: {self.safety.reason}")
        command = _clamp(command, Decimal(-1), Decimal(1))
        if abs(command) <= self.deadband:
            command = Decimal(0)
        # Break-before-make prevents simultaneous conduction in a two-input H-bridge.
        self.forward.set_duty(Decimal(0))
        self.reverse.set_duty(Decimal(0))
        if command > 0:
            self.forward.set_duty(command)
        elif command < 0:
            self.reverse.set_duty(-command)
        self.command = command

    def stop(self) -> None:
        self.forward.set_duty(Decimal(0))
        self.reverse.set_duty(Decimal(0))
        self.command = Decimal(0)

    def close(self) -> None:
        self.stop()


@dataclass(slots=True)
class Servo:
    pwm: PWMChannel
    min_us: Decimal
    max_us: Decimal
    min_degrees: Decimal
    max_degrees: Decimal
    safety: SafetyLatch | None = None

    def __post_init__(self) -> None:
        if self.min_us >= self.max_us or self.min_degrees >= self.max_degrees:
            raise MachineControlError("servo ranges must be increasing")

    def guard(self, safety: SafetyLatch) -> None:
        self.safety = safety
        safety.register_stop(self.stop)

    def stop(self) -> None:
        self.pwm.set_duty(Decimal(0))

    def write_degrees(self, degrees: Decimal) -> None:
        _finite("servo degrees", degrees)
        if self.safety is not None and self.safety.tripped:
            self.stop()
            raise MachineControlError(f"servo output blocked by safety latch: {self.safety.reason}")
        degrees = _clamp(degrees, self.min_degrees, self.max_degrees)
        ratio = (degrees - self.min_degrees) / (self.max_degrees - self.min_degrees)
        pulse_us = self.min_us + ratio * (self.max_us - self.min_us)
        duty = (pulse_us * Decimal(1000)) / Decimal(self.pwm.period_ns)
        self.pwm.set_duty(duty)



# ---------- Industrial motion and protocol helpers (0.36) ----------


def modbus_crc16(payload: bytes) -> int:
    """Return the Modbus RTU CRC-16 (poly 0xA001, little-endian on the wire)."""
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if (crc & 1) else (crc >> 1)
    return crc & 0xFFFF


def _modbus_u16(name: str, value: int) -> int:
    if not 0 <= value <= 0xFFFF:
        raise MachineControlError(f"{name} must be 0..65535")
    return value


def _modbus_count(name: str, value: int, maximum: int) -> int:
    if not 1 <= value <= maximum:
        raise MachineControlError(f"{name} must be 1..{maximum}")
    return value


def _modbus_exception(function: int, response_function: int, code: int) -> MachineControlError:
    names = {
        1: "illegal function", 2: "illegal data address", 3: "illegal data value",
        4: "server device failure", 5: "acknowledge", 6: "server device busy",
        8: "memory parity error", 10: "gateway path unavailable", 11: "gateway target failed to respond",
    }
    detail = names.get(code, f"exception {code}")
    return MachineControlError(
        f"Modbus function 0x{function:02x} failed with response 0x{response_function:02x}: {detail}"
    )


def _parse_register_response(function: int, pdu: bytes, count: int) -> list[int]:
    if len(pdu) >= 2 and pdu[0] == (function | 0x80):
        raise _modbus_exception(function, pdu[0], pdu[1])
    expected_bytes = count * 2
    if len(pdu) != 2 + expected_bytes or pdu[0] != function or pdu[1] != expected_bytes:
        raise MachineControlError("malformed Modbus register response")
    return [int.from_bytes(pdu[2+i:4+i], "big") for i in range(0, expected_bytes, 2)]


def _parse_coil_response(function: int, pdu: bytes, count: int) -> list[bool]:
    if len(pdu) >= 2 and pdu[0] == (function | 0x80):
        raise _modbus_exception(function, pdu[0], pdu[1])
    expected_bytes = (count + 7) // 8
    if len(pdu) != 2 + expected_bytes or pdu[0] != function or pdu[1] != expected_bytes:
        raise MachineControlError("malformed Modbus coil response")
    return [bool(pdu[2 + (i // 8)] & (1 << (i % 8))) for i in range(count)]


class ModbusRTUMaster:
    """Minimal deterministic Modbus RTU master over Saga's Linux UART adapter."""

    def __init__(self, path: str, baud: int, timeout_ms: int, unit_id: int):
        if not 1 <= unit_id <= 247:
            raise MachineControlError("Modbus RTU unit_id must be 1..247")
        if timeout_ms <= 0:
            raise MachineControlError("Modbus RTU timeout_ms must be > 0")
        self.unit_id = unit_id
        self.uart = UARTDevice(path, baud, timeout_ms)
        self.timeout_ms = timeout_ms
        self.closed = False
        self._lock = threading.Lock()

    def _read_response(self, expected_length: int, function: int) -> bytes:
        deadline = time.monotonic_ns() + max(1, self.timeout_ms) * 1_000_000
        data = bytearray()
        target = expected_length
        while len(data) < target:
            part = self.uart.read(max(1, target - len(data)))
            if part:
                data.extend(part)
                if len(data) >= 2 and data[1] == (function | 0x80):
                    target = 5
            if time.monotonic_ns() >= deadline:
                break
        if len(data) != target:
            raise MachineControlError(f"Modbus RTU timeout/short response: {len(data)}/{target} bytes")
        return bytes(data)

    def _transact(self, function: int, data: bytes, expected_length: int) -> bytes:
        if self.closed:
            raise MachineControlError("Modbus RTU master is closed")
        body = bytes([self.unit_id, function]) + data
        request = body + modbus_crc16(body).to_bytes(2, "little")
        with self._lock:
            self.uart.write(request)
            response = self._read_response(expected_length, function)
        if response[0] != self.unit_id:
            raise MachineControlError("Modbus RTU response unit id mismatch")
        expected_crc = int.from_bytes(response[-2:], "little")
        actual_crc = modbus_crc16(response[:-2])
        if expected_crc != actual_crc:
            raise MachineControlError("Modbus RTU CRC mismatch")
        return response[1:-2]

    def read_holding_registers(self, address: int, count: int) -> list[int]:
        address = _modbus_u16("Modbus address", address)
        count = _modbus_count("Modbus register count", count, 125)
        pdu = self._transact(0x03, struct.pack(">HH", address, count), 5 + count * 2)
        return _parse_register_response(0x03, pdu, count)

    def read_input_registers(self, address: int, count: int) -> list[int]:
        address = _modbus_u16("Modbus address", address)
        count = _modbus_count("Modbus register count", count, 125)
        pdu = self._transact(0x04, struct.pack(">HH", address, count), 5 + count * 2)
        return _parse_register_response(0x04, pdu, count)

    def read_coils(self, address: int, count: int) -> list[bool]:
        address = _modbus_u16("Modbus address", address)
        count = _modbus_count("Modbus coil count", count, 2000)
        pdu = self._transact(0x01, struct.pack(">HH", address, count), 5 + (count + 7) // 8)
        return _parse_coil_response(0x01, pdu, count)

    def write_register(self, address: int, value: int) -> None:
        address = _modbus_u16("Modbus address", address)
        value = _modbus_u16("Modbus register value", value)
        request = struct.pack(">HH", address, value)
        pdu = self._transact(0x06, request, 8)
        if pdu != bytes([0x06]) + request:
            if len(pdu) >= 2 and pdu[0] == 0x86:
                raise _modbus_exception(0x06, pdu[0], pdu[1])
            raise MachineControlError("malformed Modbus write-register response")

    def write_registers(self, address: int, values: list[int]) -> None:
        address = _modbus_u16("Modbus address", address)
        if not values or len(values) > 123:
            raise MachineControlError("Modbus register values must contain 1..123 entries")
        normalized = [_modbus_u16("Modbus register value", int(value)) for value in values]
        payload = b"".join(value.to_bytes(2, "big") for value in normalized)
        request = struct.pack(">HHB", address, len(normalized), len(payload)) + payload
        pdu = self._transact(0x10, request, 8)
        expected = bytes([0x10]) + struct.pack(">HH", address, len(normalized))
        if pdu != expected:
            if len(pdu) >= 2 and pdu[0] == 0x90:
                raise _modbus_exception(0x10, pdu[0], pdu[1])
            raise MachineControlError("malformed Modbus write-multiple response")

    def write_coil(self, address: int, state: bool) -> None:
        address = _modbus_u16("Modbus address", address)
        request = struct.pack(">HH", address, 0xFF00 if state else 0x0000)
        pdu = self._transact(0x05, request, 8)
        if pdu != bytes([0x05]) + request:
            if len(pdu) >= 2 and pdu[0] == 0x85:
                raise _modbus_exception(0x05, pdu[0], pdu[1])
            raise MachineControlError("malformed Modbus write-coil response")

    def close(self) -> None:
        if not self.closed:
            self.uart.close()
            self.closed = True


class ModbusTCPMaster:
    """Modbus TCP master with strict MBAP validation and bounded responses."""

    def __init__(self, host: str, port: int, timeout_ms: int, unit_id: int):
        if not 1 <= unit_id <= 247:
            raise MachineControlError("Modbus TCP unit_id must be 1..247")
        if not 1 <= port <= 65535:
            raise MachineControlError("Modbus TCP port must be 1..65535")
        if timeout_ms <= 0:
            raise MachineControlError("Modbus TCP timeout_ms must be > 0")
        self.host, self.port, self.unit_id = host, port, unit_id
        self.sock = socket.create_connection((host, port), timeout=timeout_ms / 1000)
        self.sock.settimeout(timeout_ms / 1000)
        self._transaction = 0
        self._lock = threading.Lock()
        self.closed = False

    def _recv_exact(self, count: int) -> bytes:
        out = bytearray()
        while len(out) < count:
            chunk = self.sock.recv(count - len(out))
            if not chunk:
                raise MachineControlError("Modbus TCP connection closed during response")
            out.extend(chunk)
        return bytes(out)

    def _transact(self, function: int, data: bytes) -> bytes:
        if self.closed:
            raise MachineControlError("Modbus TCP master is closed")
        with self._lock:
            self._transaction = (self._transaction + 1) & 0xFFFF
            tx = self._transaction
            pdu = bytes([function]) + data
            header = struct.pack(">HHHB", tx, 0, len(pdu) + 1, self.unit_id)
            self.sock.sendall(header + pdu)
            response_header = self._recv_exact(7)
            rx_tx, protocol, length, unit = struct.unpack(">HHHB", response_header)
            if rx_tx != tx or protocol != 0 or unit != self.unit_id:
                raise MachineControlError("Modbus TCP MBAP header mismatch")
            if not 2 <= length <= 254:
                raise MachineControlError("Modbus TCP response length is outside 2..254")
            response_pdu = self._recv_exact(length - 1)
        return response_pdu

    def read_holding_registers(self, address: int, count: int) -> list[int]:
        address = _modbus_u16("Modbus address", address)
        count = _modbus_count("Modbus register count", count, 125)
        return _parse_register_response(0x03, self._transact(0x03, struct.pack(">HH", address, count)), count)

    def read_input_registers(self, address: int, count: int) -> list[int]:
        address = _modbus_u16("Modbus address", address)
        count = _modbus_count("Modbus register count", count, 125)
        return _parse_register_response(0x04, self._transact(0x04, struct.pack(">HH", address, count)), count)

    def read_coils(self, address: int, count: int) -> list[bool]:
        address = _modbus_u16("Modbus address", address)
        count = _modbus_count("Modbus coil count", count, 2000)
        return _parse_coil_response(0x01, self._transact(0x01, struct.pack(">HH", address, count)), count)

    def write_register(self, address: int, value: int) -> None:
        address = _modbus_u16("Modbus address", address)
        value = _modbus_u16("Modbus register value", value)
        req = struct.pack(">HH", address, value)
        pdu = self._transact(0x06, req)
        if pdu != bytes([0x06]) + req:
            if len(pdu) >= 2 and pdu[0] == 0x86:
                raise _modbus_exception(0x06, pdu[0], pdu[1])
            raise MachineControlError("malformed Modbus TCP write-register response")

    def write_registers(self, address: int, values: list[int]) -> None:
        address = _modbus_u16("Modbus address", address)
        if not values or len(values) > 123:
            raise MachineControlError("Modbus register values must contain 1..123 entries")
        values = [_modbus_u16("Modbus register value", int(v)) for v in values]
        payload = b"".join(v.to_bytes(2, "big") for v in values)
        pdu = self._transact(0x10, struct.pack(">HHB", address, len(values), len(payload)) + payload)
        expected = bytes([0x10]) + struct.pack(">HH", address, len(values))
        if pdu != expected:
            if len(pdu) >= 2 and pdu[0] == 0x90:
                raise _modbus_exception(0x10, pdu[0], pdu[1])
            raise MachineControlError("malformed Modbus TCP write-multiple response")

    def write_coil(self, address: int, state: bool) -> None:
        address = _modbus_u16("Modbus address", address)
        req = struct.pack(">HH", address, 0xFF00 if state else 0)
        pdu = self._transact(0x05, req)
        if pdu != bytes([0x05]) + req:
            if len(pdu) >= 2 and pdu[0] == 0x85:
                raise _modbus_exception(0x05, pdu[0], pdu[1])
            raise MachineControlError("malformed Modbus TCP write-coil response")

    def close(self) -> None:
        if not self.closed:
            self.sock.close()
            self.closed = True


@dataclass(slots=True)
class JerkLimitedProfile:
    position: Decimal
    velocity: Decimal
    acceleration: Decimal
    target: Decimal
    max_velocity: Decimal
    max_acceleration: Decimal
    max_jerk: Decimal
    tolerance: Decimal = Decimal("0.000001")

    def __post_init__(self) -> None:
        for name in ("position", "velocity", "acceleration", "target"):
            _finite(name, getattr(self, name))
        self.max_velocity = _positive("max_velocity", self.max_velocity)
        self.max_acceleration = _positive("max_acceleration", self.max_acceleration)
        self.max_jerk = _positive("max_jerk", self.max_jerk)

    def retarget(self, target: Decimal) -> None:
        self.target = _finite("target", target)

    def done(self) -> bool:
        return (abs(self.target - self.position) <= self.tolerance
                and abs(self.velocity) <= self.tolerance
                and abs(self.acceleration) <= self.tolerance)

    def step(self, dt_seconds: Decimal) -> Decimal:
        dt = _positive("dt_seconds", dt_seconds)
        distance = self.target - self.position
        if self.done():
            self.position, self.velocity, self.acceleration = self.target, Decimal(0), Decimal(0)
            return self.position
        direction = Decimal(1) if distance >= 0 else Decimal(-1)
        speed = self.velocity * direction
        braking_distance = (speed * speed) / (Decimal(2) * self.max_acceleration) if speed > 0 else Decimal(0)
        desired_accel = (-self.max_acceleration if abs(distance) <= braking_distance else self.max_acceleration) * direction
        max_da = self.max_jerk * dt
        delta_a = _clamp(desired_accel - self.acceleration, -max_da, max_da)
        next_accel = _clamp(self.acceleration + delta_a, -self.max_acceleration, self.max_acceleration)
        next_velocity = _clamp(self.velocity + (self.acceleration + next_accel) * dt / Decimal(2), -self.max_velocity, self.max_velocity)
        next_position = self.position + (self.velocity + next_velocity) * dt / Decimal(2)
        if (self.target - self.position) * (self.target - next_position) <= 0:
            self.position, self.velocity, self.acceleration = self.target, Decimal(0), Decimal(0)
        else:
            self.position, self.velocity, self.acceleration = next_position, next_velocity, next_accel
        return self.position


@dataclass(slots=True)
class AxisController:
    min_position: Decimal
    max_position: Decimal
    max_following_error: Decimal
    profile: MotionProfile
    pid: PIDController
    safety: SafetyLatch
    command: Decimal = Decimal(0)

    @classmethod
    def create(cls, position: Decimal, min_position: Decimal, max_position: Decimal,
               max_velocity: Decimal, max_acceleration: Decimal,
               kp: Decimal, ki: Decimal, kd: Decimal,
               max_following_error: Decimal, safety: SafetyLatch) -> "AxisController":
        for name, value in (("position", position), ("min_position", min_position), ("max_position", max_position)):
            _finite(name, value)
        if min_position >= max_position:
            raise MachineControlError("axis min_position must be smaller than max_position")
        if not min_position <= position <= max_position:
            raise MachineControlError("axis initial position is outside soft limits")
        max_following_error = _positive("max_following_error", max_following_error)
        profile = MotionProfile(position, Decimal(0), position, max_velocity, max_acceleration)
        pid = PIDController.create(kp, ki, kd, Decimal(-1), Decimal(1))
        axis = cls(min_position, max_position, max_following_error, profile, pid, safety)
        safety.register_stop(axis.stop)
        return axis

    def stop(self) -> None:
        self.command = Decimal(0)
        self.profile.velocity = Decimal(0)

    def set_target(self, target: Decimal) -> None:
        _finite("axis target", target)
        if not self.min_position <= target <= self.max_position:
            raise MachineControlError("axis target is outside soft limits")
        if self.safety.tripped:
            raise MachineControlError(f"axis target blocked by safety latch: {self.safety.reason}")
        self.profile.retarget(target)

    def step(self, measurement: Decimal, dt_seconds: Decimal) -> Decimal:
        _finite("axis measurement", measurement)
        if self.safety.tripped:
            self.stop()
            raise MachineControlError(f"axis output blocked by safety latch: {self.safety.reason}")
        if not self.min_position <= measurement <= self.max_position:
            self.safety.trip("axis soft limit exceeded")
            self.stop()
            raise MachineControlError("axis measurement exceeded soft limits")
        planned = self.profile.step(dt_seconds)
        following_error = planned - measurement
        if abs(following_error) > self.max_following_error:
            self.safety.trip("axis following error exceeded")
            self.stop()
            raise MachineControlError("axis following error exceeded")
        self.command = self.pid.step(planned, measurement, dt_seconds)
        return self.command

    def done(self, measurement: Decimal) -> bool:
        _finite("axis measurement", measurement)
        return self.profile.done() and abs(self.profile.target - measurement) <= self.profile.tolerance

def sys_platform_linux() -> bool:
    import sys
    return sys.platform.startswith("linux")


def slew(current: Decimal, target: Decimal, units_per_second: Decimal, dt_seconds: Decimal) -> Decimal:
    _finite("current", current)
    _finite("target", target)
    units_per_second = _positive("units_per_second", units_per_second)
    dt_seconds = _positive("dt_seconds", dt_seconds)
    delta = target - current
    limit = units_per_second * dt_seconds
    if abs(delta) <= limit:
        return target
    return current + (limit if delta > 0 else -limit)


def low_pass(previous: Decimal, sample: Decimal, alpha: Decimal) -> Decimal:
    _finite("previous", previous)
    _finite("sample", sample)
    _finite("alpha", alpha)
    if alpha < 0 or alpha > 1:
        raise MachineControlError("low-pass alpha must be in 0..1")
    return previous + alpha * (sample - previous)


def deadband(value: Decimal, width: Decimal) -> Decimal:
    value = _finite("value", value)
    width = _finite("width", width)
    if width < 0:
        raise MachineControlError("deadband width must be >= 0")
    if abs(value) <= width:
        return Decimal(0)
    return value - (width if value > 0 else -width)


def integrate_clamped(
    previous: Decimal,
    input_value: Decimal,
    dt_seconds: Decimal,
    low: Decimal,
    high: Decimal,
) -> Decimal:
    previous = _finite("previous", previous)
    input_value = _finite("input_value", input_value)
    dt_seconds = _positive("dt_seconds", dt_seconds)
    low = _finite("low", low)
    high = _finite("high", high)
    if low > high:
        raise MachineControlError("integrator low must not exceed high")
    return _clamp(previous + input_value * dt_seconds, low, high)


Q31_MIN = -(1 << 31)
Q31_MAX = (1 << 31) - 1
Q31_SCALE = 1 << 31


def _q31_operand(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MachineControlError(f"{name} must be an int Q1.31 value")
    if value < Q31_MIN or value > Q31_MAX:
        raise MachineControlError(f"{name} must be in Q1.31 range")
    return value


def _trunc_div(numerator: int, denominator: int) -> int:
    quotient = abs(numerator) // abs(denominator)
    return -quotient if (numerator < 0) != (denominator < 0) else quotient


def q31_from_ratio(numerator: int, denominator: int) -> int:
    numerator = _q31_operand("q31 numerator", numerator)
    if isinstance(denominator, bool) or not isinstance(denominator, int):
        raise MachineControlError("q31 denominator must be an int")
    if denominator <= 0 or denominator > Q31_MAX:
        raise MachineControlError("q31 denominator must be in 1..2147483647")
    if numerator >= denominator:
        return Q31_MAX
    if numerator <= -denominator:
        return Q31_MIN
    return _trunc_div(numerator * Q31_SCALE, denominator)


def q31_add_sat(left: int, right: int) -> int:
    left = _q31_operand("q31 left", left)
    right = _q31_operand("q31 right", right)
    return min(Q31_MAX, max(Q31_MIN, left + right))


def q31_sub_sat(left: int, right: int) -> int:
    left = _q31_operand("q31 left", left)
    right = _q31_operand("q31 right", right)
    return min(Q31_MAX, max(Q31_MIN, left - right))


def q31_mul_sat(left: int, right: int) -> int:
    left = _q31_operand("q31 left", left)
    right = _q31_operand("q31 right", right)
    scaled = _trunc_div(left * right, Q31_SCALE)
    return min(Q31_MAX, max(Q31_MIN, scaled))


def q31_mac_sat(accumulator: int, left: int, right: int) -> int:
    accumulator = _q31_operand("q31 accumulator", accumulator)
    return q31_add_sat(accumulator, q31_mul_sat(left, right))


def servo_duty(
    degrees: Decimal,
    min_degrees: Decimal,
    max_degrees: Decimal,
    min_us: Decimal,
    max_us: Decimal,
    period_us: Decimal,
) -> Decimal:
    for name, value in (("degrees", degrees), ("min_degrees", min_degrees), ("max_degrees", max_degrees), ("min_us", min_us), ("max_us", max_us), ("period_us", period_us)):
        _finite(name, value)
    if min_degrees >= max_degrees or min_us >= max_us or period_us <= 0:
        raise MachineControlError("invalid servo range")
    value = _clamp(degrees, min_degrees, max_degrees)
    ratio = (value - min_degrees) / (max_degrees - min_degrees)
    pulse = min_us + ratio * (max_us - min_us)
    return pulse / period_us


def can_frame_json(frame: tuple[int, bytes] | None) -> str:
    if frame is None:
        return json.dumps({"received": False}, separators=(",", ":"))
    can_id, data = frame
    return json.dumps({"received": True, "id": can_id, "data_hex": data.hex()}, separators=(",", ":"))
