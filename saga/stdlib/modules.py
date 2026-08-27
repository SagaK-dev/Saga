from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io as pyio
import ipaddress
import json as pyjson
import math
import multiprocessing
import os
import re
import sys
import secrets
import socket
import ssl
import subprocess
import sqlite3
import statistics
import threading
import time as pytime
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from decimal import Decimal, localcontext
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ..native import NativeFailure, NativeFunction, NativeModule, NativeSignature
from ..typesys import (
    ANY, BOOL, BYTES, CLASS_VALUE, DATETIME, DECIMAL, DURATION, ERROR, FUTURE,
    INT, LIST, MAP, NATIVE, OPTION, RESULT, SET, TEXT, UNIT, Type,
)
from ..values import OptionValue, ResultValue
from .drone_control import (
    AttitudeController, AttitudeEstimator, DroneControlError, FlightManager, Geofence, MissionPlan,
    PositionController, QuadXMixer, QuaternionAttitudeController, RTLPlanner, RateController, MAVLinkStreamParser, Trajectory3D, ControlAllocator, LinkMonitor,
    dronecan_crc16_ccitt_false, dronecan_multi_frame, dronecan_single_frame, dronecan_single_frame_decode,
    flight_state_json, landing_vertical_velocity, quaternion_from_rpy, dshot_frame, pwm_esc_duty,
    mavlink2_decode, mavlink2_encode, mavlink2_encode_signed, mavlink2_verify_signed, mavlink_heartbeat,
    mavlink_signing_timestamp, mavlink_set_attitude_target, mavlink_set_position_target_local_ned,
    mavlink_command_long, mavlink_common_decode,
)
from .machine_control import (
    AxisController, CANDevice, EtherCATRawDevice, ControlCycle, DCMotor, EncoderTracker, I2CDevice, JerkLimitedProfile,
    MachineControlError, ModbusRTUMaster, ModbusTCPMaster, MotionProfile, PIDController,
    PWMChannel, SPIDevice, SafetyLatch, Servo, UARTDevice, Watchdog, can_frame_json,
    deadband, integrate_clamped, low_pass, modbus_crc16, servo_duty, slew,
)
from .vision_control import (
    VisionError, Detection, CentroidTracker, PinholeCamera, OpenCVDNNModel, OpenCVYOLOXDetector, OpenCVDirectObjectDetector,
    non_max_suppression, aruco_detect_bgr, aruco_pose_bgr, sparse_optical_flow_velocity_bgr, detections_json,
)
from .autonomy_advanced import (
    VisualServoController, VisualInertialOdometry, PoseGraphSLAM, MultiDroneCoordinator, MAVLinkOffboardSession,
)
from .media_streaming import (
    MediaStreamingError, GStreamerRTPVideo, gstreamer_available, gstreamer_webrtc_available, gstreamer_backend_json, gstreamer_execute_probe, webrtc_browser_recipe_json,
)
from .machine_advanced import (
    StateSpaceController, LinearKalmanFilter, SynchronizedMotionGroup, DHKinematicChain, PLCScanEngine, CANopen, CiA402, ProcessImage, discrete_lqr_gain,
)
from .fine_control import FineActuatorBank, CyclicClock, FastStateSpace
from .machine_precision import (
    AlphaBetaObserver, BiquadFilter, ControlGuard, DeadlineBudget, TwoDOFPID, clarke, inverse_park,
    motor_feedforward, park, svpwm,
)
from .machine_motion import (
    FOCCurrentLoop, UnifiedEncoder, RLS2, MPC2, DisturbanceObserver, MultiAxisSynchronizer,
    friction_compensation, ethercat_datagram, ethercat_frame, ethercat_lrw,
    ethercat_first_datagram_json, canfd_frame_json, allocation_free_profile_json,
)



DB_CONN = NATIVE("db_connection")
DOC_DB = NATIVE("document_database")
HTTP_RESPONSE = NATIVE("http_response")
HTTP_REQUEST = NATIVE("http_request")
HTTP_SERVER = NATIVE("http_server")
SOCKET = NATIVE("socket")
WEBSOCKET = NATIVE("websocket")
TASK_POOL = NATIVE("task_pool")
WINDOW = NATIVE("window")
WIDGET = NATIVE("widget")
IMAGE = NATIVE("image")
VIDEO = NATIVE("video")
MODEL = NATIVE("model")
PLUGIN = NATIVE("plugin")
SPARK = NATIVE("spark_session")
GPIO = NATIVE("gpio_pin")
MACHINE_PID = NATIVE("machine_pid")
MACHINE_PID2 = NATIVE("machine_pid2")
MACHINE_ALPHA_BETA = NATIVE("machine_alpha_beta")
MACHINE_BIQUAD = NATIVE("machine_biquad")
MACHINE_DEADLINE_BUDGET = NATIVE("machine_deadline_budget")
MACHINE_CONTROL_GUARD = NATIVE("machine_control_guard")
MACHINE_FOC_CURRENT = NATIVE("machine_foc_current")
MACHINE_ENCODER_UNIFIED = NATIVE("machine_encoder_unified")
MACHINE_RLS2 = NATIVE("machine_rls2")
MACHINE_MPC2 = NATIVE("machine_mpc2")
MACHINE_DOB = NATIVE("machine_dob")
MACHINE_AXIS_SYNC = NATIVE("machine_axis_sync")
MACHINE_PROFILE = NATIVE("machine_profile")
MACHINE_WATCHDOG = NATIVE("machine_watchdog")
MACHINE_SAFETY = NATIVE("machine_safety")
MACHINE_CYCLE = NATIVE("machine_cycle")
MACHINE_I2C = NATIVE("machine_i2c")
MACHINE_SPI = NATIVE("machine_spi")
MACHINE_UART = NATIVE("machine_uart")
MACHINE_CAN = NATIVE("machine_can")
MACHINE_ETHERCAT = NATIVE("machine_ethercat")
MACHINE_PWM = NATIVE("machine_pwm")
MACHINE_SERVO = NATIVE("machine_servo")
MACHINE_ENCODER = NATIVE("machine_encoder")
MACHINE_MOTOR = NATIVE("machine_motor")
MACHINE_SCURVE = NATIVE("machine_scurve")
MACHINE_AXIS = NATIVE("machine_axis")
MACHINE_MODBUS_RTU = NATIVE("machine_modbus_rtu")
MACHINE_MODBUS_TCP = NATIVE("machine_modbus_tcp")
DRONE_ATTITUDE_ESTIMATOR = NATIVE("drone_attitude_estimator")
DRONE_ATTITUDE_CONTROLLER = NATIVE("drone_attitude_controller")
DRONE_QUATERNION_CONTROLLER = NATIVE("drone_quaternion_controller")
DRONE_RATE_CONTROLLER = NATIVE("drone_rate_controller")
DRONE_POSITION_CONTROLLER = NATIVE("drone_position_controller")
DRONE_MIXER = NATIVE("drone_mixer")
DRONE_GEOFENCE = NATIVE("drone_geofence")
DRONE_MISSION = NATIVE("drone_mission")
DRONE_FLIGHT_MANAGER = NATIVE("drone_flight_manager")
DRONE_RTL = NATIVE("drone_rtl")
DRONE_MAVLINK_STREAM = NATIVE("drone_mavlink_stream")
DRONE_TRAJECTORY = NATIVE("drone_trajectory")
DRONE_ALLOCATOR = NATIVE("drone_allocator")
DRONE_LINK_MONITOR = NATIVE("drone_link_monitor")
VISION_TRACKER = NATIVE("vision_tracker")
VISION_CAMERA = NATIVE("vision_camera")
VISION_DNN = NATIVE("vision_dnn")
VISION_DETECTOR = NATIVE("vision_detector")
VISION_DIRECT_DETECTOR = NATIVE("vision_direct_detector")
DRONE_VISUAL_SERVO = NATIVE("drone_visual_servo")
DRONE_VIO = NATIVE("drone_vio")
DRONE_SLAM = NATIVE("drone_slam")
DRONE_COORDINATOR = NATIVE("drone_coordinator")
DRONE_SITL = NATIVE("drone_sitl")
MEDIA_GSTREAMER = NATIVE("media_gstreamer")
MACHINE_STATE_SPACE = NATIVE("machine_state_space")
MACHINE_KALMAN = NATIVE("machine_kalman")
MACHINE_MOTION_GROUP = NATIVE("machine_motion_group")
MACHINE_DH = NATIVE("machine_dh")
MACHINE_PLC = NATIVE("machine_plc")
MACHINE_PROCESS_IMAGE = NATIVE("machine_process_image")
MACHINE_ACTUATOR_BANK = NATIVE("machine_actuator_bank")
MACHINE_CYCLIC_CLOCK = NATIVE("machine_cyclic_clock")
MACHINE_FAST_STATE_SPACE = NATIVE("machine_fast_state_space")

MODULES: dict[str, NativeModule] = {}

def _host_positive_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise NativeFailure(f"{name} は正の整数で指定してください") from exc
    if value <= 0:
        raise NativeFailure(f"{name} は正の整数で指定してください")
    return value



def native_value_matches(kind: str, value: object) -> bool:
    """Return whether a host value satisfies a Saga native-resource contract.

    This check is intentionally independent from static typing because values
    flowing through ``any`` or trusted adapter boundaries still need to be
    rejected before a host method is invoked on the wrong object.
    """
    internal = {
        "db_connection": sqlite3.Connection,
        "document_database": globals().get("DocumentStore"),
        "http_response": globals().get("HttpResponse"),
        "http_request": globals().get("HttpRequest"),
        "http_server": globals().get("HttpServerHandle"),
        "socket": socket.socket,
        "task_pool": globals().get("TaskPool"),
        "window": globals().get("UiWindow"),
        "widget": globals().get("UiWidget"),
        "model": globals().get("LinearModel"),
    }
    expected = internal.get(kind)
    if isinstance(expected, type):
        return isinstance(value, expected)
    if kind == "plugin":
        try:
            from ..plugin_runtime import IsolatedPluginHandle
            return isinstance(value, IsolatedPluginHandle)
        except Exception:
            return False
    if kind == "websocket":
        # websocket-client may return compatible connection subclasses/wrappers.
        # Require the operational protocol rather than a single concrete class.
        return all(callable(getattr(value, name, None)) for name in ("send", "recv", "close"))
    if kind == "image":
        try:
            from PIL import Image
            return isinstance(value, Image.Image)
        except Exception:
            return False
    if kind == "video":
        try:
            import cv2
            return isinstance(value, cv2.VideoCapture)
        except Exception:
            return False
    if kind == "gpio_pin":
        try:
            from gpiozero import Device
            return isinstance(value, Device)
        except Exception:
            return False
    machine_types = {
        "machine_pid": PIDController,
        "machine_pid2": TwoDOFPID,
        "machine_alpha_beta": AlphaBetaObserver,
        "machine_biquad": BiquadFilter,
        "machine_deadline_budget": DeadlineBudget,
        "machine_control_guard": ControlGuard,
        "machine_foc_current": FOCCurrentLoop,
        "machine_encoder_unified": UnifiedEncoder,
        "machine_rls2": RLS2,
        "machine_mpc2": MPC2,
        "machine_dob": DisturbanceObserver,
        "machine_axis_sync": MultiAxisSynchronizer,
        "machine_profile": MotionProfile,
        "machine_watchdog": Watchdog,
        "machine_safety": SafetyLatch,
        "machine_cycle": ControlCycle,
        "machine_i2c": I2CDevice,
        "machine_spi": SPIDevice,
        "machine_uart": UARTDevice,
        "machine_can": CANDevice,
        "machine_ethercat": EtherCATRawDevice,
        "machine_pwm": PWMChannel,
        "machine_servo": Servo,
        "machine_encoder": EncoderTracker,
        "machine_motor": DCMotor,
        "machine_scurve": JerkLimitedProfile,
        "machine_axis": AxisController,
        "machine_modbus_rtu": ModbusRTUMaster,
        "machine_modbus_tcp": ModbusTCPMaster,
        "drone_attitude_estimator": AttitudeEstimator,
        "drone_attitude_controller": AttitudeController,
        "drone_quaternion_controller": QuaternionAttitudeController,
        "drone_rate_controller": RateController,
        "drone_position_controller": PositionController,
        "drone_mixer": QuadXMixer,
        "drone_geofence": Geofence,
        "drone_mission": MissionPlan,
        "drone_flight_manager": FlightManager,
        "drone_rtl": RTLPlanner,
        "drone_mavlink_stream": MAVLinkStreamParser,
        "drone_trajectory": Trajectory3D,
        "drone_allocator": ControlAllocator,
        "drone_link_monitor": LinkMonitor,
        "vision_tracker": CentroidTracker,
        "vision_camera": PinholeCamera,
        "vision_dnn": OpenCVDNNModel,
        "vision_detector": OpenCVYOLOXDetector,
        "vision_direct_detector": OpenCVDirectObjectDetector,
        "drone_visual_servo": VisualServoController,
        "drone_vio": VisualInertialOdometry,
        "drone_slam": PoseGraphSLAM,
        "drone_coordinator": MultiDroneCoordinator,
        "drone_sitl": MAVLinkOffboardSession,
        "media_gstreamer": GStreamerRTPVideo,
        "machine_state_space": StateSpaceController,
        "machine_kalman": LinearKalmanFilter,
        "machine_motion_group": SynchronizedMotionGroup,
        "machine_dh": DHKinematicChain,
        "machine_plc": PLCScanEngine,
        "machine_process_image": ProcessImage,
        "machine_actuator_bank": FineActuatorBank,
        "machine_cyclic_clock": CyclicClock,
        "machine_fast_state_space": FastStateSpace,
    }
    machine_type = machine_types.get(kind)
    if machine_type is not None:
        return isinstance(value, machine_type)
    if kind == "spark_session":
        try:
            from pyspark.sql import SparkSession
            return isinstance(value, SparkSession)
        except Exception:
            return False
    return False


def _module(name: str) -> NativeModule:
    module = MODULES.get(name)
    if module is None:
        module = NativeModule(name)
        MODULES[name] = module
    return module


def native(module: str, name: str, params: tuple[Type, ...] = (), returns: Type = ANY, *, variadic: bool = False, min_args: int | None = None):
    def decorate(func):
        _module(module).functions[name] = NativeFunction(module, name, NativeSignature(params, returns, variadic, min_args), func)
        return func
    return decorate


def _require_count(args: list[object], count: int, name: str) -> None:
    if len(args) != count:
        raise NativeFailure(f"{name} の引数は {count} 個必要です")


def _require_min(args: list[object], count: int, name: str) -> None:
    if len(args) < count:
        raise NativeFailure(f"{name} の引数は最低 {count} 個必要です")


def _as_text(value: object) -> str:
    if not isinstance(value, str):
        raise NativeFailure("text 型が必要です")
    return value


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NativeFailure("int 型が必要です")
    return value


def _plain(interpreter, value: object) -> object:
    if hasattr(interpreter, "to_plain"):
        return interpreter.to_plain(value)
    if isinstance(value, tuple):
        return [_plain(interpreter, v) for v in value]
    if isinstance(value, frozenset):
        return [_plain(interpreter, v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(interpreter, v) for k, v in value.items()}
    return value


# ---------- Console ----------

@native("console", "input", (TEXT,), TEXT)
def console_input(_interpreter, args):
    try:
        return input(_as_text(args[0]))
    except EOFError as exc:
        raise NativeFailure("入力が終了しました") from exc


@native("console", "write", (TEXT,), UNIT)
def console_write(interpreter, args):
    # The output callback is line-oriented; keeping this deterministic also makes tests easy.
    interpreter.emit_output(_as_text(args[0]))
    return None


# ---------- File and binary I/O ----------

@native("io", "read_text", (TEXT,), TEXT)
def io_read_text(interpreter, args):
    path = interpreter.capabilities.require_read(_as_text(args[0]))
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise NativeFailure(f"UTF-8テキストとして読めません: {path}: {exc}") from exc
    except OSError as exc:
        raise NativeFailure(f"テキストファイルを読めません: {path}: {exc}") from exc


@native("io", "write_text", (TEXT, TEXT), UNIT)
def io_write_text(interpreter, args):
    path = interpreter.capabilities.require_write(_as_text(args[0]))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_as_text(args[1]), encoding="utf-8")
    except OSError as exc:
        raise NativeFailure(f"テキストファイルを書き込めません: {path}: {exc}") from exc
    return None


@native("io", "append_text", (TEXT, TEXT), UNIT)
def io_append_text(interpreter, args):
    path = interpreter.capabilities.require_write(_as_text(args[0]))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(_as_text(args[1]))
    except OSError as exc:
        raise NativeFailure(f"テキストファイルへ追記できません: {path}: {exc}") from exc
    return None


@native("io", "read_bytes", (TEXT,), BYTES)
def io_read_bytes(interpreter, args):
    path = interpreter.capabilities.require_read(_as_text(args[0]))
    try:
        return path.read_bytes()
    except OSError as exc:
        raise NativeFailure(f"バイナリファイルを読めません: {path}: {exc}") from exc


@native("io", "write_bytes", (TEXT, BYTES), UNIT)
def io_write_bytes(interpreter, args):
    path = interpreter.capabilities.require_write(_as_text(args[0]))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes(args[1]))
    except OSError as exc:
        raise NativeFailure(f"バイナリファイルを書き込めません: {path}: {exc}") from exc
    return None


@native("io", "exists", (TEXT,), BOOL)
def io_exists(interpreter, args):
    path = Path(_as_text(args[0])).expanduser().resolve()
    if not interpreter.capabilities.allow_all:
        interpreter.capabilities.require_read(path)
    return path.exists()


@native("io", "list", (TEXT,), LIST(TEXT))
def io_list(interpreter, args):
    path = interpreter.capabilities.require_read(_as_text(args[0]))
    if not path.is_dir():
        raise NativeFailure(f"ディレクトリではありません: {path}")
    try:
        return tuple(sorted(child.name for child in path.iterdir()))
    except OSError as exc:
        raise NativeFailure(f"ディレクトリを一覧できません: {path}: {exc}") from exc


@native("io", "mkdir", (TEXT,), UNIT)
def io_mkdir(interpreter, args):
    path = interpreter.capabilities.require_write(_as_text(args[0]))
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise NativeFailure(f"ディレクトリを作成できません: {path}: {exc}") from exc
    return None


@native("io", "remove", (TEXT,), UNIT)
def io_remove(interpreter, args):
    path = interpreter.capabilities.require_write(_as_text(args[0]))
    try:
        if path.is_dir(): path.rmdir()
        else: path.unlink(missing_ok=True)
    except OSError as exc:
        raise NativeFailure(f"ファイルまたはディレクトリを削除できません: {path}: {exc}") from exc
    return None


@native("io", "copy", (TEXT, TEXT), UNIT)
def io_copy(interpreter, args):
    src = interpreter.capabilities.require_read(_as_text(args[0]))
    dst = interpreter.capabilities.require_write(_as_text(args[1]))
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
    except OSError as exc:
        raise NativeFailure(f"ファイルをコピーできません: {src} -> {dst}: {exc}") from exc
    return None


@native("io", "encode", (TEXT,), BYTES)
def io_encode(_interpreter, args): return _as_text(args[0]).encode("utf-8")


@native("io", "decode", (BYTES,), TEXT)
def io_decode(_interpreter, args):
    try: return bytes(args[0]).decode("utf-8")
    except UnicodeDecodeError as exc: raise NativeFailure(f"UTF-8として読めません: {exc}") from exc


# ---------- Date and time ----------

@native("time", "now", (), DATETIME)
def time_now(_interpreter, _args): return datetime.now().astimezone()


@native("time", "utc_now", (), DATETIME)
def time_utc_now(_interpreter, _args): return datetime.now(timezone.utc)


@native("time", "parse", (TEXT,), DATETIME)
def time_parse(_interpreter, args):
    try: return datetime.fromisoformat(_as_text(args[0]))
    except ValueError as exc: raise NativeFailure(f"ISO日時を解析できません: {exc}") from exc


@native("time", "format", (DATETIME, TEXT), TEXT)
def time_format(_interpreter, args):
    if not isinstance(args[0], datetime): raise NativeFailure("datetime 型が必要です")
    return args[0].strftime(_as_text(args[1]))


@native("time", "iso", (DATETIME,), TEXT)
def time_iso(_interpreter, args):
    if not isinstance(args[0], datetime): raise NativeFailure("datetime 型が必要です")
    return args[0].isoformat()


@native("time", "add_days", (DATETIME, INT), DATETIME)
def time_add_days(_interpreter, args):
    try:
        return args[0] + timedelta(days=_as_int(args[1]))
    except OverflowError as exc:
        raise NativeFailure("datetimeで表現できる日付範囲を超えました") from exc


@native("time", "add_seconds", (DATETIME, INT), DATETIME)
def time_add_seconds(_interpreter, args):
    try:
        return args[0] + timedelta(seconds=_as_int(args[1]))
    except OverflowError as exc:
        raise NativeFailure("datetimeで表現できる時刻範囲を超えました") from exc


@native("time", "diff", (DATETIME, DATETIME), DURATION)
def time_diff(_interpreter, args): return args[0] - args[1]


@native("time", "seconds", (DURATION,), DECIMAL)
def time_seconds(_interpreter, args):
    if not isinstance(args[0], timedelta): raise NativeFailure("duration 型が必要です")
    value = args[0]
    return Decimal(value.days * 86400 + value.seconds) + Decimal(value.microseconds) / Decimal(1_000_000)


@native("time", "sleep", (DECIMAL,), UNIT)
def time_sleep(_interpreter, args):
    seconds = Decimal(args[0])
    if seconds < 0: raise NativeFailure("sleep は0秒以上にしてください")
    if not seconds.is_finite(): raise NativeFailure("sleep には有限の秒数を指定してください")
    # Sleep in finite host-sized chunks.  This is not a Saga language limit;
    # it avoids converting an arbitrarily large Decimal directly to float.
    remaining = seconds
    chunk_size = Decimal(86_400)
    try:
        while remaining > 0:
            chunk = min(remaining, chunk_size)
            pytime.sleep(float(chunk))
            remaining -= chunk
    except (OverflowError, OSError, ValueError) as exc:
        raise NativeFailure(f"sleepを実行できません: {exc}") from exc
    return None


# ---------- JSON / CSV ----------

def _json_number(value: Decimal) -> str:
    if not value.is_finite():
        raise NativeFailure("JSONにはNaNやInfinityを含められません")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"", "-0"}:
        return "0"
    return text


def _json_object_items(interpreter, value: object) -> list[tuple[str, object]]:
    if isinstance(value, dict):
        result: list[tuple[str, object]] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise NativeFailure("JSONオブジェクトのキーはtext型にしてください")
            result.append((key, item))
        return result
    # Avoid importing interpreter runtime classes into the standard-library
    # module. Saga instances expose klass/values with field metadata.
    klass = getattr(value, "klass", None)
    values = getattr(value, "values", None)
    if klass is not None and isinstance(values, dict):
        return [
            (name, item)
            for name, item in values.items()
            if name in klass.fields and not klass.fields[name].private
        ]
    raise NativeFailure(f"{type(value).__name__} はJSONオブジェクトに変換できません")


def _json_encode_exact(interpreter, value: object, *, pretty: bool, level: int = 0, seen: set[int] | None = None) -> str:
    if seen is None:
        seen = set()
    track = isinstance(value, (tuple, dict)) or (getattr(value, "klass", None) is not None and isinstance(getattr(value, "values", None), dict))
    identity = id(value)
    if track and identity in seen:
        raise NativeFailure("JSONへ変換できない循環参照があります")
    if track:
        seen.add(identity)
    try:
        return _json_encode_exact_inner(interpreter, value, pretty=pretty, level=level, seen=seen)
    finally:
        if track:
            seen.remove(identity)


def _json_encode_exact_inner(interpreter, value: object, *, pretty: bool, level: int, seen: set[int]) -> str:
    if isinstance(value, OptionValue):
        return _json_encode_exact(interpreter, value.value, pretty=pretty, level=level, seen=seen) if value.present else "null"
    if value is None:
        raise NativeFailure("unit はJSONへ直接変換できません。JSON nullには none() を使用してください")
    if value is True: return "true"
    if value is False: return "false"
    if isinstance(value, int) and not isinstance(value, bool): return str(value)
    if isinstance(value, Decimal): return _json_number(value)
    from fractions import Fraction
    if isinstance(value, Fraction):
        if value.denominator != 1:
            raise NativeFailure("rational はJSON数値へ正確に変換できません。decimal(value) または text(value) を使用してください")
        return str(value.numerator)
    if isinstance(value, str): return pyjson.dumps(value, ensure_ascii=False)
    if isinstance(value, bytes):
        raise NativeFailure("bytes はJSONへ直接変換できません。Base64等へ明示的に変換してください")
    if isinstance(value, datetime): return pyjson.dumps(value.isoformat(), ensure_ascii=False)
    if isinstance(value, timedelta):
        seconds = Decimal(value.days * 86400 + value.seconds) + Decimal(value.microseconds) / Decimal(1_000_000)
        return _json_number(seconds)
    if isinstance(value, tuple):
        if not value: return "[]"
        encoded = [_json_encode_exact(interpreter, item, pretty=pretty, level=level + 1, seen=seen) for item in value]
        if not pretty: return "[" + ",".join(encoded) + "]"
        pad = "  " * (level + 1); close = "  " * level
        return "[\n" + pad + (",\n" + pad).join(encoded) + "\n" + close + "]"
    if isinstance(value, frozenset):
        raise NativeFailure("set は順序を持たないためJSONへ直接変換できません。listへ変換してください")
    items = _json_object_items(interpreter, value)
    if not items: return "{}"
    encoded_items = [
        pyjson.dumps(key, ensure_ascii=False) + (": " if pretty else ":")
        + _json_encode_exact(interpreter, item, pretty=pretty, level=level + 1, seen=seen)
        for key, item in items
    ]
    if not pretty: return "{" + ",".join(encoded_items) + "}"
    pad = "  " * (level + 1); close = "  " * level
    return "{\n" + pad + (",\n" + pad).join(encoded_items) + "\n" + close + "}"


def _reject_json_constant(value: str):
    raise ValueError(f"JSON標準外の数値です: {value}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSONオブジェクトのキーが重複しています: {key}")
        result[key] = value
    return result


def _freeze_external(value: object) -> object:
    """Convert host/SDK values into the closed set of Saga value types.

    Hosted integrations must never smuggle arbitrary Python objects into Saga:
    doing so would bypass task Send checks, deterministic formatting, and the
    capability boundary.  Unknown SDK objects therefore fail explicitly.
    """
    if value is None: return OptionValue.none()
    if isinstance(value, bool): return value
    if isinstance(value, int): return value
    if isinstance(value, Decimal): return value
    if isinstance(value, float): return Decimal(str(value))
    if isinstance(value, (str, bytes, datetime, timedelta)): return value
    if isinstance(value, (list, tuple)): return tuple(_freeze_external(v) for v in value)
    if isinstance(value, (set, frozenset)): return frozenset(_freeze_external(v) for v in value)
    if isinstance(value, dict):
        result: dict[object, object] = {}
        for key, item in value.items():
            frozen_key = _freeze_external(key)
            try:
                hash(frozen_key)
            except (TypeError, ValueError) as exc:
                raise NativeFailure(f"外部データのmapキーをSaga値へ変換できません: {type(key).__name__}") from exc
            result[frozen_key] = _freeze_external(item)
        return result
    raise NativeFailure(
        f"外部ライブラリがSagaへ安全に渡せない値を返しました: {type(value).__name__}。"
        "text/number/bytes/list/map/set/datetime等へ変換してから返してください"
    )


def _decode_json(text: str) -> object:
    try:
        parsed = pyjson.loads(
            text,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_object,
        )
        return _freeze_external(parsed)
    except (pyjson.JSONDecodeError, ValueError) as exc:
        raise NativeFailure(f"JSONを解析できません: {exc}") from exc


@native("json", "encode", (ANY,), TEXT)
def json_encode(interpreter, args):
    try:
        return _json_encode_exact(interpreter, args[0], pretty=False)
    except RecursionError as exc:
        raise NativeFailure("JSON変換中にホストの再帰スタックを使い切りました。Saga規格上の固定深度上限ではありません") from exc


@native("json", "pretty", (ANY,), TEXT)
def json_pretty(interpreter, args):
    try:
        return _json_encode_exact(interpreter, args[0], pretty=True)
    except RecursionError as exc:
        raise NativeFailure("JSON変換中にホストの再帰スタックを使い切りました。Saga規格上の固定深度上限ではありません") from exc


@native("json", "decode", (TEXT,), ANY)
def json_decode(_interpreter, args):
    try:
        return _decode_json(_as_text(args[0]))
    except RecursionError as exc:
        raise NativeFailure("JSON解析中にホストの再帰スタックを使い切りました。Saga規格上の固定深度上限ではありません") from exc


@native("data", "csv_read", (TEXT,), LIST(LIST(TEXT)))
def data_csv_read(interpreter, args):
    path = interpreter.capabilities.require_read(_as_text(args[0]))
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return tuple(tuple(row) for row in csv.reader(handle))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise NativeFailure(f"CSVを読み込めません: {path}: {exc}") from exc


@native("data", "csv_write", (TEXT, LIST(LIST(TEXT))), UNIT)
def data_csv_write(interpreter, args):
    path = interpreter.capabilities.require_write(_as_text(args[0]))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows(args[1])
    except (OSError, UnicodeError, csv.Error) as exc:
        raise NativeFailure(f"CSVを書き込めません: {path}: {exc}") from exc
    return None


@native("data", "chunks", (LIST(ANY), INT), LIST(LIST(ANY)))
def data_chunks(_interpreter, args):
    size = _as_int(args[1])
    if size < 1: raise NativeFailure("chunk size は1以上にしてください")
    values = tuple(args[0]); return tuple(values[i:i+size] for i in range(0, len(values), size))


@native("data", "group_count", (LIST(ANY),), MAP(ANY, INT))
def data_group_count(_interpreter, args):
    result: dict[object, int] = {}
    for item in args[0]:
        try: result[item] = result.get(item, 0) + 1
        except TypeError as exc: raise NativeFailure("group_count の要素はハッシュ可能である必要があります") from exc
    return result


# ---------- HTTP client/server ----------

@dataclass(slots=True)
class HttpResponse:
    status: int
    body: bytes
    headers: dict[str, str]


@dataclass(slots=True)
class HttpRequest:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes
    query: dict[str, tuple[str, ...]]


@dataclass(slots=True)
class HttpServerHandle:
    server: ThreadingHTTPServer
    thread: threading.Thread

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        if self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout=5)


class _CapabilityRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, capabilities):
        super().__init__()
        self.capabilities = capabilities

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise NativeFailure("リダイレクト先URLが安全ではありません")
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise NativeFailure("リダイレクト先のポート番号が正しくありません") from exc
        try:
            self.capabilities.require_net(parsed.hostname, port)
        except Exception:
            # urllib leaves the redirect response owned by the handler when
            # redirect authorization fails. Close it explicitly so rejected
            # redirects cannot accumulate sockets in long-running controllers.
            fp.close()
            raise
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@native("http", "get", (TEXT,), HTTP_RESPONSE)
def http_get(interpreter, args): return _http_request(interpreter, "GET", _as_text(args[0]), None, {})


@native("http", "post", (TEXT, TEXT, TEXT), HTTP_RESPONSE)
def http_post(interpreter, args):
    return _http_request(interpreter, "POST", _as_text(args[0]), _as_text(args[1]).encode(), {"Content-Type": _as_text(args[2])})


def _http_request(interpreter, method: str, url: str, data: bytes | None, headers: dict[str, str]) -> HttpResponse:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}: raise NativeFailure("HTTP URLは http:// または https:// で始めてください")
    if not parsed.hostname: raise NativeFailure("URLにホスト名がありません")
    if parsed.username or parsed.password: raise NativeFailure("URL内のユーザー名・パスワードは使用できません")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise NativeFailure("URLのポート番号が正しくありません") from exc
    interpreter.capabilities.require_net(parsed.hostname, port)
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _CapabilityRedirectHandler(interpreter.capabilities))

    def read_response(response) -> HttpResponse:
        limit = _host_positive_int("SAGA_HTTP_MAX_BODY_BYTES")
        body = response.read(limit + 1) if limit is not None else response.read()
        if limit is not None and len(body) > limit:
            raise NativeFailure(f"HTTPレスポンスが管理者上限 {limit} バイトを超えました")
        return HttpResponse(response.status, body, dict(response.headers.items()))

    try:
        timeout = _host_positive_int("SAGA_HTTP_TIMEOUT_SECONDS")
        if timeout is None:
            with opener.open(request) as response:
                return read_response(response)
        with opener.open(request, timeout=timeout) as response:
            return read_response(response)
    except urllib.error.HTTPError as exc:
        # HTTPError is also a file-like response object and owns the socket.
        # Always close it after copying the bounded response into Saga memory.
        with exc:
            return read_response(exc)
    except NativeFailure:
        raise
    except (OSError, ValueError) as exc:
        raise NativeFailure(f"HTTP通信に失敗しました: {exc}") from exc


@native("http", "status", (HTTP_RESPONSE,), INT)
def http_status(_interpreter, args): return args[0].status


@native("http", "text", (HTTP_RESPONSE,), TEXT)
def http_text(_interpreter, args):
    response = args[0]
    charset = "utf-8"
    content_type = response.headers.get("Content-Type", "")
    if "charset=" in content_type: charset = content_type.split("charset=", 1)[1].split(";", 1)[0].strip()
    try: return response.body.decode(charset)
    except (LookupError, UnicodeDecodeError): return response.body.decode("utf-8", errors="replace")


@native("http", "bytes", (HTTP_RESPONSE,), BYTES)
def http_bytes(_interpreter, args): return args[0].body


@native("http", "header", (HTTP_RESPONSE, TEXT, TEXT), TEXT)
def http_header(_interpreter, args):
    wanted = _as_text(args[1]).lower()
    for key, value in args[0].headers.items():
        if key.lower() == wanted: return value
    return _as_text(args[2])


@native("http", "response", (INT, TEXT, TEXT), HTTP_RESPONSE)
def http_response(_interpreter, args):
    status = _as_int(args[0]); content_type = _as_text(args[2])
    if status < 100 or status > 599: raise NativeFailure("HTTPステータスは100から599にしてください")
    if "\r" in content_type or "\n" in content_type: raise NativeFailure("Content-Typeに改行を含められません")
    body = _as_text(args[1]).encode("utf-8")
    return HttpResponse(status, body, {"Content-Type": content_type})


@native("http", "serve", (TEXT, INT, ANY), HTTP_SERVER)
def http_serve(interpreter, args):
    host = _as_text(args[0]); port = _as_int(args[1]); handler_callable = args[2]
    interpreter.capabilities.require_net(host, port)

    owner = interpreter
    class Handler(BaseHTTPRequestHandler):
        def _handle(self):
            try: size = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError:
                self.send_error(400, "Invalid Content-Length")
                return
            if size < 0:
                self.send_error(400, "Invalid Content-Length")
                return
            body = self.rfile.read(size) if size else b""
            parsed = urllib.parse.urlsplit(self.path)
            request = HttpRequest(
                self.command, parsed.path, dict(self.headers.items()), body,
                {k: tuple(v) for k, v in urllib.parse.parse_qs(parsed.query).items()},
            )
            try:
                result = owner.invoke_callable_threadsafe(handler_callable, [request])
                if isinstance(result, HttpResponse): response = result
                else: response = HttpResponse(200, owner.format_value(result).encode("utf-8"), {"Content-Type": "text/plain; charset=utf-8"})
            except Exception as exc:  # server boundary
                owner.output(f"HTTP handler error: {exc}")
                response = HttpResponse(500, b"Internal Server Error", {"Content-Type": "text/plain; charset=utf-8"})
            self.send_response(response.status)
            for key, value in response.headers.items(): self.send_header(key, value)
            self.end_headers(); self.wfile.write(response.body)
        do_GET = _handle; do_POST = _handle; do_PUT = _handle; do_DELETE = _handle; do_PATCH = _handle
        def log_message(self, format, *args): owner.output("HTTP " + (format % args))

    try: server = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc: raise NativeFailure(f"HTTPサーバーを開始できません: {exc}") from exc
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name=f"saga-http-{port}", daemon=True)
    handle = HttpServerHandle(server, thread)
    thread.start(); return interpreter.register_resource(handle)


@native("http", "port", (HTTP_SERVER,), INT)
def http_port(_interpreter, args): return int(args[0].server.server_address[1])


@native("http", "stop", (HTTP_SERVER,), UNIT)
def http_stop(_interpreter, args): args[0].server.shutdown(); args[0].server.server_close(); return None


@native("http", "wait", (HTTP_SERVER,), UNIT)
def http_wait(_interpreter, args): args[0].thread.join(); return None


@native("http", "request_method", (HTTP_REQUEST,), TEXT)
def http_request_method(_interpreter, args): return args[0].method


@native("http", "request_path", (HTTP_REQUEST,), TEXT)
def http_request_path(_interpreter, args): return args[0].path


@native("http", "request_text", (HTTP_REQUEST,), TEXT)
def http_request_text(_interpreter, args): return args[0].body.decode("utf-8", errors="replace")


@native("http", "request_header", (HTTP_REQUEST, TEXT, TEXT), TEXT)
def http_request_header(_interpreter, args):
    wanted = _as_text(args[1]).lower()
    for key, value in args[0].headers.items():
        if key.lower() == wanted: return value
    return _as_text(args[2])


@native("http", "query", (HTTP_REQUEST, TEXT, TEXT), TEXT)
def http_query(_interpreter, args):
    values = args[0].query.get(_as_text(args[1])); return values[0] if values else _as_text(args[2])


# ---------- TCP / UDP / WebSocket ----------

@native("net", "tcp_connect", (TEXT, INT), SOCKET)
def net_tcp_connect(interpreter, args):
    host = _as_text(args[0]); port = _as_int(args[1]); interpreter.capabilities.require_net(host, port)
    try: return interpreter.register_resource(socket.create_connection((host, port), timeout=30))
    except OSError as exc: raise NativeFailure(f"TCP接続に失敗しました: {exc}") from exc


@native("net", "tcp_listen", (TEXT, INT), SOCKET)
def net_tcp_listen(interpreter, args):
    host = _as_text(args[0]); port = _as_int(args[1]); interpreter.capabilities.require_net(host, port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM); sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try: sock.bind((host, port)); sock.listen()
    except OSError as exc: sock.close(); raise NativeFailure(f"TCP待受に失敗しました: {exc}") from exc
    return interpreter.register_resource(sock)


@native("net", "accept", (SOCKET,), SOCKET)
def net_accept(interpreter, args):
    try: return interpreter.register_resource(args[0].accept()[0])
    except OSError as exc: raise NativeFailure(f"接続受付に失敗しました: {exc}") from exc


@native("net", "send", (SOCKET, BYTES), INT)
def net_send(_interpreter, args):
    try: args[0].sendall(bytes(args[1])); return len(args[1])
    except OSError as exc: raise NativeFailure(f"送信に失敗しました: {exc}") from exc


@native("net", "receive", (SOCKET, INT), BYTES)
def net_receive(_interpreter, args):
    size = _as_int(args[1])
    if size < 0: raise NativeFailure("受信バイト数は0以上にしてください")
    try: return args[0].recv(size)
    except (OSError, ValueError) as exc: raise NativeFailure(f"受信に失敗しました: {exc}") from exc


@native("net", "udp", (), SOCKET)
def net_udp(interpreter, _args): return interpreter.register_resource(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))


@native("net", "udp_bind", (SOCKET, TEXT, INT), UNIT)
def net_udp_bind(interpreter, args):
    host = _as_text(args[1]); port = _as_int(args[2]); interpreter.capabilities.require_net(host, port)
    try: args[0].bind((host, port))
    except OSError as exc: raise NativeFailure(f"UDP待受に失敗しました: {exc}") from exc
    return None


@native("net", "udp_send", (SOCKET, BYTES, TEXT, INT), INT)
def net_udp_send(interpreter, args):
    host = _as_text(args[2]); port = _as_int(args[3]); interpreter.capabilities.require_net(host, port)
    try: return args[0].sendto(bytes(args[1]), (host, port))
    except OSError as exc: raise NativeFailure(f"UDP送信に失敗しました: {exc}") from exc


@native("net", "udp_receive", (SOCKET, INT), BYTES)
def net_udp_receive(_interpreter, args):
    size = _as_int(args[1])
    if size < 0 or size > 16 << 20: raise NativeFailure("受信バイト数は0以上16MiB以下にしてください")
    try: return args[0].recvfrom(size)[0]
    except (OSError, ValueError) as exc: raise NativeFailure(f"UDP受信に失敗しました: {exc}") from exc

@native("net", "udp_receive_from_json", (SOCKET, INT), TEXT)
def net_udp_receive_from_json(_interpreter, args):
    size = _as_int(args[1])
    if size < 0 or size > 16 << 20: raise NativeFailure("受信バイト数は0以上16MiB以下にしてください")
    try:
        payload, peer = args[0].recvfrom(size)
        host, port = peer[0], int(peer[1])
        return pyjson.dumps({"host":host,"port":port,"data_hex":bytes(payload).hex()},separators=(",",":"),sort_keys=True)
    except (OSError, ValueError) as exc:
        raise NativeFailure(f"UDP受信に失敗しました: {exc}") from exc


@native("net", "close", (SOCKET,), UNIT)
def net_close(_interpreter, args): args[0].close(); return None


@native("websocket", "connect", (TEXT,), WEBSOCKET)
def websocket_connect(interpreter, args):
    url = _as_text(args[0]); parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"ws", "wss"} or not parsed.hostname: raise NativeFailure("ws:// または wss:// URLが必要です")
    try: port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    except ValueError as exc: raise NativeFailure("WebSocketのポート番号が正しくありません") from exc
    interpreter.capabilities.require_net(parsed.hostname, port)
    try:
        import websocket  # type: ignore
    except ImportError as exc:
        raise NativeFailure("WebSocketには websocket-client が必要です: pip install 'saga-language[websocket]'") from exc
    try:
        # Do not inherit HTTP(S)_PROXY from the host: proxy use would create an
        # un-granted network hop. Redirects are disabled because websocket-client
        # otherwise follows them internally without re-running Saga capability
        # authorization for the new destination.
        connection = websocket.create_connection(
            url,
            timeout=30,
            redirect_limit=0,
            http_no_proxy=["*"],
        )
        response = getattr(connection, "handshake_response", None)
        status = getattr(response, "status", None)
        if status in {301, 302, 303, 307, 308}:
            connection.close()
            location = getattr(response, "headers", {}).get("location", "") if response is not None else ""
            raise NativeFailure(
                "WebSocketリダイレクトは自動追従しません。"
                + (f"リダイレクト先 {location} を明示的に接続し、--allow-net で許可してください" if location else "接続先URLを明示的に指定してください")
            )
        return interpreter.register_resource(connection)
    except NativeFailure:
        raise
    except Exception as exc: raise NativeFailure(f"WebSocket接続に失敗しました: {exc}") from exc


@native("websocket", "send", (WEBSOCKET, TEXT), UNIT)
def websocket_send(_interpreter, args): args[0].send(_as_text(args[1])); return None


@native("websocket", "receive", (WEBSOCKET,), TEXT)
def websocket_receive(_interpreter, args): return str(args[0].recv())


@native("websocket", "close", (WEBSOCKET,), UNIT)
def websocket_close(_interpreter, args): args[0].close(); return None


# ---------- SQL database / ORM / document DB ----------

@native("db", "open", (TEXT,), DB_CONN)
def db_open(interpreter, args):
    raw = _as_text(args[0])
    if raw != ":memory:":
        path = interpreter.capabilities.require_db(raw); path.parent.mkdir(parents=True, exist_ok=True); raw = str(path)
    conn = sqlite3.connect(raw, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return interpreter.register_resource(conn)


def _db_param(value: object) -> object:
    if isinstance(value, OptionValue):
        return _db_param(value.value) if value.present else None
    if isinstance(value, bool): return int(value)
    if isinstance(value, (Decimal,)): return str(value)
    from fractions import Fraction
    if isinstance(value, Fraction): return str(value)
    if isinstance(value, (int, str, bytes)) or value is None: return value
    raise NativeFailure(f"SQLパラメータに使用できない型です: {type(value).__name__}")


@native("db", "execute", (DB_CONN, TEXT, LIST(ANY)), INT)
def db_execute(_interpreter, args):
    try:
        cur = args[0].execute(_as_text(args[1]), tuple(_db_param(v) for v in args[2])); return cur.rowcount
    except sqlite3.Error as exc: raise NativeFailure(f"SQL実行に失敗しました: {exc}") from exc


@native("db", "query", (DB_CONN, TEXT, LIST(ANY)), LIST(MAP(TEXT, ANY)))
def db_query(_interpreter, args):
    try:
        cur = args[0].execute(_as_text(args[1]), tuple(_db_param(v) for v in args[2])); return tuple({key: _freeze_external(row[key]) for key in row.keys()} for row in cur.fetchall())
    except sqlite3.Error as exc: raise NativeFailure(f"SQL取得に失敗しました: {exc}") from exc


@native("db", "transaction", (DB_CONN, ANY), ANY)
def db_transaction(interpreter, args):
    conn, action = args
    nested = bool(conn.in_transaction)
    savepoint = f"saga_tx_{threading.get_ident()}_{pytime.monotonic_ns()}"
    try:
        if nested: conn.execute(f'SAVEPOINT "{savepoint}"')
        else: conn.execute("BEGIN")
        result = interpreter.invoke_callable(action, [conn])
        if nested: conn.execute(f'RELEASE SAVEPOINT "{savepoint}"')
        else: conn.commit()
        return result
    except Exception:
        if nested:
            conn.execute(f'ROLLBACK TO SAVEPOINT "{savepoint}"')
            conn.execute(f'RELEASE SAVEPOINT "{savepoint}"')
        else: conn.rollback()
        raise


@native("db", "begin", (DB_CONN,), UNIT)
def db_begin(_interpreter, args): args[0].execute("BEGIN"); return None


@native("db", "commit", (DB_CONN,), UNIT)
def db_commit(_interpreter, args): args[0].commit(); return None


@native("db", "rollback", (DB_CONN,), UNIT)
def db_rollback(_interpreter, args): args[0].rollback(); return None


@native("db", "close", (DB_CONN,), UNIT)
def db_close(_interpreter, args): args[0].close(); return None


@native("orm", "create_table", (DB_CONN, CLASS_VALUE), UNIT)
def orm_create_table(interpreter, args): interpreter.orm_create_table(args[0], args[1]); return None


@native("orm", "insert", (DB_CONN, ANY), INT)
def orm_insert(interpreter, args): return interpreter.orm_insert(args[0], args[1])


@native("orm", "all", (DB_CONN, CLASS_VALUE), LIST(ANY))
def orm_all(interpreter, args): return interpreter.orm_all(args[0], args[1])


@dataclass(slots=True)
class DocumentStore:
    path: Path
    data: dict[str, object] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(_json_encode_exact(None, self.data, pretty=True) + "\n", encoding="utf-8")
        tmp.replace(self.path)


@native("docdb", "open", (TEXT,), DOC_DB)
def docdb_open(interpreter, args):
    path = interpreter.capabilities.require_db(_as_text(args[0])); path.parent.mkdir(parents=True, exist_ok=True)
    store = DocumentStore(path)
    if path.exists():
        try:
            loaded = _decode_json(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict): raise NativeFailure("ドキュメントDBのルートはmapである必要があります")
            store.data = loaded
        except (OSError, pyjson.JSONDecodeError) as exc: raise NativeFailure(f"ドキュメントDBを開けません: {exc}") from exc
    return store


@native("docdb", "put", (DOC_DB, TEXT, ANY), UNIT)
def docdb_put(interpreter, args):
    with args[0].lock:
        # Document storage has JSON value semantics.  Store the decoded
        # canonical representation instead of retaining a live mutable Saga
        # object reference whose later mutation could diverge from disk.
        encoded = _json_encode_exact(interpreter, args[2], pretty=False)
        args[0].data[_as_text(args[1])] = _decode_json(encoded)
        args[0].save()
    return None


@native("docdb", "get", (DOC_DB, TEXT, ANY), ANY)
def docdb_get(_interpreter, args): return args[0].data.get(_as_text(args[1]), args[2])


@native("docdb", "remove", (DOC_DB, TEXT), UNIT)
def docdb_remove(_interpreter, args):
    with args[0].lock: args[0].data.pop(_as_text(args[1]), None); args[0].save()
    return None


@native("docdb", "keys", (DOC_DB,), LIST(TEXT))
def docdb_keys(_interpreter, args): return tuple(sorted(args[0].data))


# ---------- Concurrency and parallelism ----------

@dataclass(slots=True)
class TaskPool:
    executor: ThreadPoolExecutor

    def close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=False)


@native("task", "spawn", (ANY,), FUTURE(ANY), variadic=True, min_args=1)
def task_spawn(interpreter, args): return interpreter.spawn_callable(args[0], list(args[1:]))


@native("task", "await", (FUTURE(ANY),), ANY)
def task_await(_interpreter, args):
    # A task is an isolation boundary, not an exception-erasure boundary.
    # Preserve Saga language failures so try/catch observes the same error kind
    # as a direct call; only unknown host failures are wrapped as NativeFailure.
    from ..interpreter import SagaThrown
    from ..errors import RuntimeLanguageError
    try: return args[0].result()
    except (SagaThrown, RuntimeLanguageError, NativeFailure): raise
    except Exception as exc: raise NativeFailure(f"非同期処理が失敗しました: {exc}") from exc


@native("task", "all", (LIST(FUTURE(ANY)),), LIST(ANY))
def task_all(_interpreter, args):
    from ..interpreter import SagaThrown
    from ..errors import RuntimeLanguageError
    results: list[object] = []
    first_failure: Exception | None = None
    # Structured join semantics: every supplied future reaches completion before
    # task.all returns or raises. Keep the first failure in input order so error
    # selection is deterministic without abandoning later tasks.
    for future in args[0]:
        try:
            results.append(future.result())
        except Exception as exc:
            results.append(None)
            if first_failure is None:
                first_failure = exc
    if first_failure is not None:
        if isinstance(first_failure, (SagaThrown, RuntimeLanguageError, NativeFailure)):
            raise first_failure
        raise NativeFailure(f"並行処理が失敗しました: {first_failure}") from first_failure
    return tuple(results)


@native("task", "parallel_map", (ANY, LIST(ANY), INT), LIST(ANY))
def task_parallel_map(interpreter, args):
    workers = _as_int(args[2])
    if workers < 1: raise NativeFailure("worker数は1以上にしてください")
    for item in args[1]:
        interpreter.validate_task_call(args[0], [item])
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="saga-map") as pool:
        futures = [pool.submit(interpreter.invoke_callable_isolated, args[0], [item]) for item in args[1]]
        return tuple(f.result() for f in futures)


@native("task", "pool", (INT,), TASK_POOL)
def task_pool(interpreter, args):
    workers = _as_int(args[0])
    if workers < 1: raise NativeFailure("worker数は1以上にしてください")
    return interpreter.register_resource(TaskPool(ThreadPoolExecutor(max_workers=workers, thread_name_prefix="saga-pool")))


@native("task", "submit", (TASK_POOL, ANY), FUTURE(ANY), variadic=True, min_args=2)
def task_submit(interpreter, args):
    call_args = list(args[2:])
    interpreter.validate_task_call(args[1], call_args)
    return args[0].executor.submit(interpreter.invoke_callable_isolated, args[1], call_args)


@native("task", "shutdown", (TASK_POOL,), UNIT)
def task_shutdown(_interpreter, args): args[0].executor.shutdown(wait=True); return None


def _cpu_workers(value: object) -> int | None:
    workers = _as_int(value)
    if workers < 0:
        raise NativeFailure("CPU worker数は0（自動）または1以上にしてください")
    return None if workers == 0 else workers


def _run_cpu_jobs(interpreter, callee: object, calls: list[list[object]], workers: int | None) -> tuple[object, ...]:
    from ..parallel_runtime import execute_cpu_job
    jobs = [interpreter.prepare_cpu_job(callee, call) for call in calls]
    if not jobs:
        return ()
    try:
        context = multiprocessing.get_context("spawn")
        effective_workers = workers if workers is not None else min(len(jobs), os.cpu_count() or 1)
        with ProcessPoolExecutor(max_workers=effective_workers, mp_context=context) as pool:
            futures = [pool.submit(execute_cpu_job, job) for job in jobs]
            return tuple(future.result() for future in futures)
    except NativeFailure:
        raise
    except Exception as exc:
        raise NativeFailure(f"CPU並列処理が失敗しました: {type(exc).__name__}: {exc}") from exc


@native("task", "cpu_count", (), INT)
def task_cpu_count(_interpreter, _args):
    return os.cpu_count() or 1


@native("task", "process_id", (), INT)
def task_process_id(_interpreter, _args):
    return os.getpid()


@native("task", "cpu_map", (ANY, LIST(ANY), INT), LIST(ANY))
def task_cpu_map(interpreter, args):
    workers = _cpu_workers(args[2])
    values = tuple(args[1])
    return _run_cpu_jobs(interpreter, args[0], [[item] for item in values], workers)


@native("task", "cpu_filter", (ANY, LIST(ANY), INT), LIST(ANY))
def task_cpu_filter(interpreter, args):
    workers = _cpu_workers(args[2])
    values = tuple(args[1])
    decisions = _run_cpu_jobs(interpreter, args[0], [[item] for item in values], workers)
    for decision in decisions:
        if not isinstance(decision, bool):
            raise NativeFailure("task.cpu_filter の判定関数はboolを返す必要があります")
    return tuple(value for value, keep in zip(values, decisions) if keep)


@native("task", "cpu_reduce", (ANY, LIST(ANY), ANY, INT), ANY)
def task_cpu_reduce(interpreter, args):
    workers = _cpu_workers(args[3])
    values = list(args[1])
    accumulator = args[2]
    if not values:
        return accumulator
    # Tree reduction: each round combines independent pairs in separate
    # processes.  The reducer should be associative for worker-count-independent
    # results; this requirement is documented by the standard profile.
    current = [accumulator, *values]
    while len(current) > 1:
        calls: list[list[object]] = []
        carry = None
        if len(current) % 2:
            carry = current.pop()
        for index in range(0, len(current), 2):
            calls.append([current[index], current[index + 1]])
        next_values = list(_run_cpu_jobs(interpreter, args[0], calls, workers))
        if carry is not None:
            next_values.append(carry)
        current = next_values
    return current[0]


# ---------- Desktop GUI ----------

@dataclass(slots=True)
class UiWindow:
    root: object

    def close(self) -> None:
        destroy = getattr(self.root, "destroy", None)
        if callable(destroy): destroy()


@dataclass(slots=True)
class UiWidget:
    widget: object


@native("ui", "window", (TEXT, INT, INT), WINDOW)
def ui_window(interpreter, args):
    interpreter.capabilities.require_ui()
    try:
        import tkinter as tk
        root = tk.Tk(); root.title(_as_text(args[0])); root.geometry(f"{_as_int(args[1])}x{_as_int(args[2])}")
        return interpreter.register_resource(UiWindow(root))
    except Exception as exc: raise NativeFailure(f"GUIウィンドウを作成できません: {exc}") from exc


@native("ui", "label", (WINDOW, TEXT), WIDGET)
def ui_label(_interpreter, args):
    import tkinter as tk
    widget = tk.Label(args[0].root, text=_as_text(args[1])); widget.pack(padx=8, pady=8); return UiWidget(widget)


@native("ui", "input", (WINDOW, TEXT), WIDGET)
def ui_input(_interpreter, args):
    import tkinter as tk
    var = tk.StringVar(value=_as_text(args[1])); widget = tk.Entry(args[0].root, textvariable=var); widget._saga_var = var; widget.pack(padx=8, pady=8); return UiWidget(widget)


@native("ui", "button", (WINDOW, TEXT, ANY), WIDGET)
def ui_button(interpreter, args):
    import tkinter as tk
    callback = lambda: interpreter.invoke_callable_threadsafe(args[2], [])
    widget = tk.Button(args[0].root, text=_as_text(args[1]), command=callback); widget.pack(padx=8, pady=8); return UiWidget(widget)


@native("ui", "get", (WIDGET,), TEXT)
def ui_get(_interpreter, args):
    widget = args[0].widget
    if hasattr(widget, "_saga_var"): return str(widget._saga_var.get())
    return str(widget.cget("text"))


@native("ui", "set", (WIDGET, TEXT), UNIT)
def ui_set(_interpreter, args):
    widget = args[0].widget
    if hasattr(widget, "_saga_var"): widget._saga_var.set(_as_text(args[1]))
    else: widget.configure(text=_as_text(args[1]))
    return None


@native("ui", "after", (WINDOW, INT, ANY), UNIT)
def ui_after(interpreter, args):
    delay = _as_int(args[1])
    if delay < 0: raise NativeFailure("ui.after の待ち時間は0ミリ秒以上にしてください")
    args[0].root.after(delay, lambda: interpreter.invoke_callable_threadsafe(args[2], []))
    return None


@native("ui", "run", (WINDOW,), UNIT)
def ui_run(_interpreter, args): args[0].root.mainloop(); return None


@native("ui", "close", (WINDOW,), UNIT)
def ui_close(_interpreter, args): args[0].root.destroy(); return None


# ---------- Security / authentication ----------

@native("crypto", "sha256", (BYTES,), TEXT)
def crypto_sha256(_interpreter, args): return hashlib.sha256(bytes(args[0])).hexdigest()


@native("crypto", "hmac_sha256", (BYTES, BYTES), TEXT)
def crypto_hmac(_interpreter, args): return hmac.new(bytes(args[0]), bytes(args[1]), hashlib.sha256).hexdigest()


@native("crypto", "random", (INT,), BYTES)
def crypto_random(_interpreter, args):
    count = _as_int(args[0])
    if count < 1: raise NativeFailure("random bytes は1以上にしてください")
    return secrets.token_bytes(count)


@native("crypto", "base64_encode", (BYTES,), TEXT)
def crypto_b64e(_interpreter, args): return base64.b64encode(bytes(args[0])).decode("ascii")


@native("crypto", "base64_decode", (TEXT,), BYTES)
def crypto_b64d(_interpreter, args):
    try: return base64.b64decode(_as_text(args[0]), validate=True)
    except ValueError as exc: raise NativeFailure("Base64が正しくありません") from exc


@native("crypto", "constant_equal", (BYTES, BYTES), BOOL)
def crypto_constant_equal(_interpreter, args): return hmac.compare_digest(bytes(args[0]), bytes(args[1]))


@native("crypto", "password_hash", (TEXT,), TEXT)
def crypto_password_hash(_interpreter, args):
    salt = secrets.token_bytes(16); derived = hashlib.scrypt(_as_text(args[0]).encode(), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(derived).decode()


@native("crypto", "password_verify", (TEXT, TEXT), BOOL)
def crypto_password_verify(_interpreter, args):
    try:
        scheme, salt64, hash64 = _as_text(args[1]).split("$", 2)
        if scheme != "scrypt": return False
        salt = base64.b64decode(salt64); expected = base64.b64decode(hash64)
        actual = hashlib.scrypt(_as_text(args[0]).encode(), salt=salt, n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError): return False


@native("crypto", "aes_encrypt", (BYTES, BYTES), BYTES)
def crypto_aes_encrypt(_interpreter, args):
    try: from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc: raise NativeFailure("AES-GCMには cryptography が必要です: pip install 'saga-language[crypto]'") from exc
    key = bytes(args[0]); nonce = secrets.token_bytes(12)
    if len(key) not in {16, 24, 32}: raise NativeFailure("AES鍵は16、24、32バイトにしてください")
    return nonce + AESGCM(key).encrypt(nonce, bytes(args[1]), None)


@native("crypto", "aes_decrypt", (BYTES, BYTES), BYTES)
def crypto_aes_decrypt(_interpreter, args):
    try: from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc: raise NativeFailure("AES-GCMには cryptography が必要です") from exc
    key, payload = bytes(args[0]), bytes(args[1])
    if len(key) not in {16, 24, 32}: raise NativeFailure("AES鍵は16、24、32バイトにしてください")
    if len(payload) < 28: raise NativeFailure("AES-GCM暗号文が短すぎます")
    try: return AESGCM(key).decrypt(payload[:12], payload[12:], None)
    except Exception as exc: raise NativeFailure("復号に失敗しました。鍵またはデータが正しくありません") from exc



# ---------- Defensive security / audit ----------

_SECURITY_PBKDF2_ITERATIONS = 210_000

@native("security", "sha512", (TEXT,), TEXT)
def security_sha512(_interpreter, args):
    return hashlib.sha512(_as_text(args[0]).encode("utf-8")).hexdigest()


@native("security", "hmac_sha256", (TEXT, TEXT), TEXT)
def security_hmac_sha256(_interpreter, args):
    return hmac.new(_as_text(args[0]).encode("utf-8"), _as_text(args[1]).encode("utf-8"), hashlib.sha256).hexdigest()


@native("security", "constant_equal", (TEXT, TEXT), BOOL)
def security_constant_equal(_interpreter, args):
    return hmac.compare_digest(_as_text(args[0]).encode("utf-8"), _as_text(args[1]).encode("utf-8"))


@native("security", "random_hex", (INT,), TEXT)
def security_random_hex(_interpreter, args):
    count = _as_int(args[0])
    if count < 1 or count > 4096:
        raise NativeFailure("security.random_hex のバイト数は1..4096にしてください")
    return secrets.token_hex(count)


@native("security", "password_hash", (TEXT,), TEXT)
def security_password_hash(_interpreter, args):
    password = _as_text(args[0]).encode("utf-8")
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password, salt, _SECURITY_PBKDF2_ITERATIONS, dklen=32)
    return f"pbkdf2-sha256${_SECURITY_PBKDF2_ITERATIONS}${salt.hex()}${derived.hex()}"


@native("security", "password_verify", (TEXT, TEXT), BOOL)
def security_password_verify(_interpreter, args):
    try:
        scheme, iterations_s, salt_hex, expected_hex = _as_text(args[1]).split("$", 3)
        if scheme != "pbkdf2-sha256": return False
        iterations = int(iterations_s)
        if not 10_000 <= iterations <= 1_000_000: return False
        salt = bytes.fromhex(salt_hex); expected = bytes.fromhex(expected_hex)
        if not 8 <= len(salt) <= 64 or not 16 <= len(expected) <= 64: return False
        actual = hashlib.pbkdf2_hmac("sha256", _as_text(args[0]).encode("utf-8"), salt, iterations, dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


@native("security", "file_sha256", (TEXT,), RESULT(TEXT, TEXT))
def security_file_sha256(_interpreter, args):
    path = Path(_as_text(args[0]))
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError as exc:
        return ResultValue.failure(str(exc))
    return ResultValue.success(h.hexdigest())


@native("security", "ip_valid", (TEXT,), BOOL)
def security_ip_valid(_interpreter, args):
    try: ipaddress.ip_address(_as_text(args[0])); return True
    except ValueError: return False


@native("security", "cidr_contains", (TEXT, TEXT), RESULT(BOOL, TEXT))
def security_cidr_contains(_interpreter, args):
    try:
        network = ipaddress.ip_network(_as_text(args[0]), strict=False)
        address = ipaddress.ip_address(_as_text(args[1]))
        return ResultValue.success(address in network)
    except ValueError as exc:
        return ResultValue.failure(str(exc))


@native("security", "certificate_info", (TEXT,), RESULT(TEXT, TEXT))
def security_certificate_info(_interpreter, args):
    pem_text = _as_text(args[0])
    try:
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(pem_text.encode("utf-8"))
        try:
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            dns_names = list(san.get_values_for_type(x509.DNSName))
            ip_addresses = [str(v) for v in san.get_values_for_type(x509.IPAddress)]
        except x509.ExtensionNotFound:
            dns_names, ip_addresses = [], []
        return ResultValue.success(pyjson.dumps({
            "subject": cert.subject.rfc4514_string(),
            "issuer": cert.issuer.rfc4514_string(),
            "serial": str(cert.serial_number),
            "not_before_unix_ms": int(cert.not_valid_before_utc.timestamp() * 1000),
            "not_after_unix_ms": int(cert.not_valid_after_utc.timestamp() * 1000),
            "dns_names": dns_names,
            "ip_addresses": ip_addresses,
            "signature_algorithm": cert.signature_hash_algorithm.name if cert.signature_hash_algorithm else "",
            "public_key_algorithm": type(cert.public_key()).__name__,
            "is_ca": bool(cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca) if any(isinstance(ext.value, x509.BasicConstraints) for ext in cert.extensions) else False,
        }, ensure_ascii=False, sort_keys=True))
    except Exception as exc:
        return ResultValue.failure(str(exc))


@native("security", "tls_probe", (TEXT, INT, TEXT, TEXT, INT), RESULT(TEXT, TEXT))
def security_tls_probe(_interpreter, args):
    host = _as_text(args[0]); port = _as_int(args[1]); server_name = _as_text(args[2]) or host
    ca_pem = _as_text(args[3]); timeout_ms = _as_int(args[4])
    if not host: return ResultValue.failure("host required")
    if not 1 <= port <= 65535: return ResultValue.failure("port must be 1..65535")
    if not 1 <= timeout_ms <= 60_000: return ResultValue.failure("timeout_ms must be 1..60000")
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if ca_pem.strip():
        try: context.load_verify_locations(cadata=ca_pem)
        except ssl.SSLError as exc: return ResultValue.failure(str(exc))
    try:
        with socket.create_connection((host, port), timeout=timeout_ms / 1000) as raw:
            with context.wrap_socket(raw, server_hostname=server_name) as conn:
                cert = conn.getpeercert()
                return ResultValue.success(pyjson.dumps({
                    "tls_version": conn.version(),
                    "cipher_suite": conn.cipher()[0] if conn.cipher() else "",
                    "server_name": server_name,
                    "negotiated_protocol": conn.selected_alpn_protocol() or "",
                    "verified_chain_count": 1,
                    "certificate": cert,
                }, ensure_ascii=False, sort_keys=True, default=str))
    except (OSError, ssl.SSLError) as exc:
        return ResultValue.failure(str(exc))


# ---------- Scientific calculation / simple ML ----------

@native("science", "linspace", (DECIMAL, DECIMAL, INT), LIST(DECIMAL))
def science_linspace(interpreter, args):
    start, end, count = Decimal(args[0]), Decimal(args[1]), _as_int(args[2])
    if count < 2: raise NativeFailure("linspace の個数は2以上にしてください")
    with localcontext(interpreter.context):
        step = (end - start) / Decimal(count - 1)
        return tuple(start + step * i for i in range(count))


@native("science", "dot", (LIST(DECIMAL), LIST(DECIMAL)), DECIMAL)
def science_dot(interpreter, args):
    if len(args[0]) != len(args[1]): raise NativeFailure("ベクトルの長さを揃えてください")
    with localcontext(interpreter.context):
        return sum((Decimal(a) * Decimal(b) for a, b in zip(args[0], args[1])), Decimal(0))


@native("science", "mean", (LIST(DECIMAL),), DECIMAL)
def science_mean(interpreter, args):
    if not args[0]: raise NativeFailure("空のデータの平均は計算できません")
    with localcontext(interpreter.context):
        return sum((Decimal(v) for v in args[0]), Decimal(0)) / Decimal(len(args[0]))


@native("science", "matrix_multiply", (LIST(LIST(DECIMAL)), LIST(LIST(DECIMAL))), LIST(LIST(DECIMAL)))
def science_matmul(interpreter, args):
    a, b = args
    if not a or not b: raise NativeFailure("空の行列は掛け算できません")
    a_width = len(a[0]); b_width = len(b[0])
    if a_width == 0 or b_width == 0: raise NativeFailure("列が空の行列は扱えません")
    if any(len(row) != a_width for row in a) or any(len(row) != b_width for row in b):
        raise NativeFailure("すべての行の列数を揃えてください")
    if a_width != len(b): raise NativeFailure("行列の形が掛け算に対応していません")
    bt = tuple(zip(*b))
    with localcontext(interpreter.context):
        return tuple(tuple(sum((Decimal(x)*Decimal(y) for x, y in zip(row, col)), Decimal(0)) for col in bt) for row in a)


@dataclass(slots=True)
class LinearModel:
    slope: Decimal
    intercept: Decimal


@native("ml", "linear_regression", (LIST(DECIMAL), LIST(DECIMAL)), MODEL)
def ml_linear(interpreter, args):
    xs, ys = [Decimal(x) for x in args[0]], [Decimal(y) for y in args[1]]
    if len(xs) != len(ys) or len(xs) < 2: raise NativeFailure("同じ長さで2件以上のデータが必要です")
    with localcontext(interpreter.context):
        mx = sum(xs, Decimal(0)) / Decimal(len(xs)); my = sum(ys, Decimal(0)) / Decimal(len(ys))
        denom = sum(((x-mx) ** 2 for x in xs), Decimal(0))
        if denom == 0: raise NativeFailure("xがすべて同じため学習できません")
        slope = sum(((x-mx)*(y-my) for x, y in zip(xs, ys)), Decimal(0)) / denom
        return LinearModel(slope, my - slope * mx)


@native("ml", "predict", (MODEL, DECIMAL), DECIMAL)
def ml_predict(interpreter, args):
    with localcontext(interpreter.context): return args[0].slope * Decimal(args[1]) + args[0].intercept




# ---------- Regular expressions and host information ----------

def _compile_regex(pattern: str):
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise NativeFailure(f"正規表現が正しくありません: {exc}") from exc

@native("regex", "is_match", (TEXT, TEXT), BOOL)
def regex_is_match(_interpreter, args):
    return _compile_regex(_as_text(args[0])).search(_as_text(args[1])) is not None

@native("regex", "find_all", (TEXT, TEXT), LIST(TEXT))
def regex_find_all(_interpreter, args):
    pattern = _compile_regex(_as_text(args[0])); text = _as_text(args[1])
    result = []
    for match in pattern.finditer(text):
        result.append(match.group(0))
    return tuple(result)

@native("regex", "replace", (TEXT, TEXT, TEXT), TEXT)
def regex_replace(_interpreter, args):
    return _compile_regex(_as_text(args[0])).sub(_as_text(args[2]), _as_text(args[1]))

@native("regex", "split", (TEXT, TEXT), LIST(TEXT))
def regex_split(_interpreter, args):
    return tuple(_compile_regex(_as_text(args[0])).split(_as_text(args[1])))

@native("system", "platform", (), TEXT)
def system_platform(_interpreter, _args): return sys.platform

@native("system", "architecture", (), TEXT)
def system_architecture(_interpreter, _args):
    import platform as _platform
    return _platform.machine()

@native("system", "cpu_count", (), INT)
def system_cpu_count(_interpreter, _args): return os.cpu_count() or 1


# ---------- Image, video and game adapters ----------

@native("image", "open", (TEXT,), IMAGE)
def image_open(interpreter, args):
    path = interpreter.capabilities.require_read(_as_text(args[0]))
    try: from PIL import Image
    except ImportError as exc: raise NativeFailure("画像処理には Pillow が必要です: pip install 'saga-language[media]'") from exc
    try:
        with Image.open(path) as source:
            loaded = source.copy()
        return interpreter.register_resource(loaded)
    except Exception as exc: raise NativeFailure(f"画像を開けません: {exc}") from exc


@native("image", "resize", (IMAGE, INT, INT), IMAGE)
def image_resize(interpreter, args):
    width, height = _as_int(args[1]), _as_int(args[2])
    if width < 1 or height < 1:
        raise NativeFailure("画像サイズは幅・高さとも1以上にしてください")
    try:
        return interpreter.register_resource(args[0].resize((width, height)))
    except Exception as exc:
        raise NativeFailure(f"画像をリサイズできません: {exc}") from exc


@native("image", "save", (IMAGE, TEXT), UNIT)
def image_save(interpreter, args):
    path = interpreter.capabilities.require_write(_as_text(args[1])); path.parent.mkdir(parents=True, exist_ok=True); args[0].save(path); return None


@native("image", "width", (IMAGE,), INT)
def image_width(_interpreter, args): return args[0].width


@native("image", "height", (IMAGE,), INT)
def image_height(_interpreter, args): return args[0].height


@native("video", "open", (TEXT,), VIDEO)
def video_open(interpreter, args):
    path = interpreter.capabilities.require_read(_as_text(args[0]))
    try: import cv2
    except ImportError as exc: raise NativeFailure("動画処理には OpenCV が必要です: pip install 'saga-language[media]'") from exc
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        cap.release()
        raise NativeFailure(f"動画を開けません: {path}")
    return interpreter.register_resource(cap)


@native("video", "open_camera", (INT,), VIDEO)
def video_open_camera(interpreter, args):
    interpreter.capabilities.require_device()
    index = _as_int(args[0])
    if index < 0:
        raise NativeFailure("camera index must be >= 0")
    try: import cv2
    except ImportError as exc: raise NativeFailure("camera input requires OpenCV: pip install 'saga-language[media]'") from exc
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        cap.release(); raise NativeFailure(f"camera {index} could not be opened")
    return interpreter.register_resource(cap)

@native("video", "read_frame", (VIDEO,), IMAGE)
def video_read_frame(interpreter, args):
    try:
        import cv2
        from PIL import Image
        ok, frame = args[0].read()
        if not ok or frame is None:
            raise NativeFailure("video/camera frame could not be read")
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return interpreter.register_resource(Image.fromarray(rgb))
    except NativeFailure:
        raise
    except Exception as exc:
        raise NativeFailure(f"video/camera frame conversion failed: {exc}") from exc

@native("video", "frame_count", (VIDEO,), INT)
def video_frame_count(_interpreter, args):
    import cv2
    return int(args[0].get(cv2.CAP_PROP_FRAME_COUNT))


@native("video", "close", (VIDEO,), UNIT)
def video_close(_interpreter, args): args[0].release(); return None


@native("game", "available", (), BOOL)
def game_available(_interpreter, _args):
    try: import pygame  # noqa: F401
    except ImportError: return False
    return True


@native("game", "run_demo", (TEXT, INT, INT), UNIT)
def game_run_demo(interpreter, args):
    interpreter.capabilities.require_ui()
    try: import pygame
    except ImportError as exc: raise NativeFailure("ゲーム開発には pygame が必要です: pip install 'saga-language[game]'") from exc
    pygame.init(); screen = pygame.display.set_mode((_as_int(args[1]), _as_int(args[2]))); pygame.display.set_caption(_as_text(args[0]))
    clock = pygame.time.Clock(); running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT: running = False
        screen.fill((245, 245, 245)); pygame.display.flip(); clock.tick(60)
    pygame.quit(); return None


@native("game", "run_frames", (TEXT, INT, INT, INT), MAP(TEXT, ANY))
def game_run_frames(interpreter, args):
    """Run a finite pygame loop suitable for CI, kiosks and smoke tests.

    Unlike ``run_demo`` this never waits for a human to close a window.  It is
    therefore the qualification path for the optional pygame adapter.
    """
    interpreter.capabilities.require_ui()
    try:
        import pygame
    except ImportError as exc:
        raise NativeFailure("ゲーム開発には pygame が必要です: pip install 'saga-language[game]'") from exc
    title, width, height, frames = _as_text(args[0]), _as_int(args[1]), _as_int(args[2]), _as_int(args[3])
    if width <= 0 or height <= 0 or width > 8192 or height > 8192:
        raise NativeFailure("game.run_frames の画面サイズは1..8192にしてください")
    if frames <= 0 or frames > 100000:
        raise NativeFailure("game.run_frames のフレーム数は1..100000にしてください")
    pygame.init()
    try:
        screen = pygame.display.set_mode((width, height))
        pygame.display.set_caption(title)
        clock = pygame.time.Clock()
        events = 0
        executed = 0
        for frame in range(frames):
            for event in pygame.event.get():
                events += 1
                if event.type == pygame.QUIT:
                    return {"frames": executed, "events": events, "driver": pygame.display.get_driver(), "quit": True}
            shade = 32 + (frame % 192)
            screen.fill((shade, 96, 160))
            pygame.display.flip()
            clock.tick(60)
            executed += 1
        return {"frames": executed, "events": events, "driver": pygame.display.get_driver(), "quit": False}
    finally:
        pygame.quit()


# ---------- External processes ----------

def _bounded_process_output(program: str, arguments: tuple[str, ...], timeout: int) -> tuple[int, bytes, bytes]:
    try:
        proc = subprocess.Popen(
            [program, *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise NativeFailure(f"実行ファイルが見つかりません: {program}") from exc
    except OSError as exc:
        raise NativeFailure(f"外部プロセスを開始できません: {exc}") from exc

    chunks: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
    limit = _host_positive_int("SAGA_PROCESS_OUTPUT_LIMIT_BYTES")
    total = 0
    total_lock = threading.Lock()
    overflow = threading.Event()

    def drain(name: str, stream) -> None:
        nonlocal total
        try:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    return
                with total_lock:
                    if limit is not None and total + len(chunk) > limit:
                        remaining = max(0, limit - total)
                        if remaining:
                            chunks[name].append(chunk[:remaining])
                            total += remaining
                        overflow.set()
                        try:
                            proc.kill()
                        except OSError:
                            pass
                        return
                    chunks[name].append(chunk)
                    total += len(chunk)
        finally:
            try:
                stream.close()
            except OSError:
                pass

    assert proc.stdout is not None and proc.stderr is not None
    readers = [
        threading.Thread(target=drain, args=("stdout", proc.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", proc.stderr), daemon=True),
    ]
    for thread in readers:
        thread.start()
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.wait()
        for thread in readers:
            thread.join(timeout=1)
        raise NativeFailure(f"外部プロセスが{timeout}秒以内に終了しませんでした") from exc
    for thread in readers:
        thread.join(timeout=1)
    if overflow.is_set():
        raise NativeFailure(f"外部プロセスの出力が管理者上限 {limit} バイトを超えました")
    return code, b"".join(chunks["stdout"]), b"".join(chunks["stderr"])


@native("process", "run", (TEXT, LIST(TEXT), INT), MAP(TEXT, ANY))
def process_run(interpreter, args):
    interpreter.capabilities.require_process()
    program = _as_text(args[0])
    arguments = tuple(_as_text(value) for value in args[1])
    timeout = _as_int(args[2])
    if timeout <= 0:
        raise NativeFailure("process.run のタイムアウトは0秒より大きくしてください")
    code, stdout, stderr = _bounded_process_output(program, arguments, timeout)
    return {
        "code": code,
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
    }


# ---------- Cloud / IoT / big data adapters ----------

@native("cloud", "env", (TEXT, TEXT), TEXT)
def cloud_env(interpreter, args):
    name = _as_text(args[0]); interpreter.capabilities.require_env(name)
    return os.environ.get(name, _as_text(args[1]))


@native("cloud", "aws_client", (TEXT, TEXT), ANY)
def cloud_aws_client(interpreter, args):
    interpreter.capabilities.require_cloud()
    try: import boto3
    except ImportError as exc: raise NativeFailure("AWS連携には boto3 が必要です: pip install 'saga-language[cloud]'") from exc
    try:
        client = boto3.client(_as_text(args[0]), region_name=_as_text(args[1]) or None)
    except Exception as exc:
        raise NativeFailure(f"AWSクライアントを作成できません: {exc}") from exc
    # Botocore clients expose close() in supported current releases. Register
    # when available so interpreter shutdown does not leak pooled connections.
    return interpreter.register_resource(client) if callable(getattr(client, "close", None)) else client


@native("cloud", "call", (ANY, TEXT, MAP(TEXT, ANY)), ANY)
def cloud_call(interpreter, args):
    interpreter.capabilities.require_cloud()
    method_name = _as_text(args[1])
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", method_name) or method_name.startswith("_"):
        raise NativeFailure("クラウドAPI名は公開識別子で指定してください")
    method = getattr(args[0], method_name, None)
    if method is None or not callable(method): raise NativeFailure("クラウドクライアントに指定メソッドがありません")
    try: return _freeze_external(method(**dict(args[2])))
    except Exception as exc: raise NativeFailure(f"クラウドAPI呼び出しに失敗しました: {exc}") from exc


@native("gpio", "output", (INT,), GPIO)
def gpio_output(interpreter, args):
    interpreter.capabilities.require_device()
    try:
        from gpiozero import OutputDevice
        return interpreter.register_resource(OutputDevice(_as_int(args[0])))
    except ImportError as exc: raise NativeFailure("GPIOには gpiozero が必要です: pip install 'saga-language[iot]'") from exc
    except Exception as exc: raise NativeFailure(f"GPIO出力を初期化できません: {exc}") from exc


@native("gpio", "input", (INT, BOOL), GPIO)
def gpio_input(interpreter, args):
    interpreter.capabilities.require_device()
    try:
        from gpiozero import DigitalInputDevice
        return interpreter.register_resource(DigitalInputDevice(_as_int(args[0]), pull_up=bool(args[1])))
    except ImportError as exc:
        raise NativeFailure("GPIOには gpiozero が必要です: pip install 'saga-language[iot]'") from exc
    except Exception as exc:
        raise NativeFailure(f"GPIO入力を初期化できません: {exc}") from exc


@native("gpio", "pwm", (INT, DECIMAL, DECIMAL), GPIO)
def gpio_pwm(interpreter, args):
    interpreter.capabilities.require_device()
    try:
        from gpiozero import PWMOutputDevice
        frequency = float(args[1]); initial = float(args[2])
        if frequency <= 0 or not 0.0 <= initial <= 1.0:
            raise NativeFailure("GPIO PWMは frequency>0、initialは0..1にしてください")
        return interpreter.register_resource(PWMOutputDevice(_as_int(args[0]), frequency=frequency, initial_value=initial))
    except ImportError as exc:
        raise NativeFailure("GPIOには gpiozero が必要です: pip install 'saga-language[iot]'") from exc
    except NativeFailure:
        raise
    except Exception as exc:
        raise NativeFailure(f"GPIO PWMを初期化できません: {exc}") from exc


@native("gpio", "on", (GPIO,), UNIT)
def gpio_on(interpreter, args): interpreter.capabilities.require_device(); args[0].on(); return None


@native("gpio", "off", (GPIO,), UNIT)
def gpio_off(interpreter, args): interpreter.capabilities.require_device(); args[0].off(); return None


@native("gpio", "write", (GPIO, DECIMAL), UNIT)
def gpio_write(interpreter, args):
    interpreter.capabilities.require_device()
    value = float(args[1])
    if not 0.0 <= value <= 1.0:
        raise NativeFailure("gpio.write の値は0..1にしてください")
    try:
        args[0].value = value
    except Exception as exc:
        raise NativeFailure(f"GPIOへ書き込めません: {exc}") from exc
    return None


@native("gpio", "read", (GPIO,), DECIMAL)
def gpio_read(interpreter, args):
    interpreter.capabilities.require_device()
    try:
        return Decimal(str(float(args[0].value)))
    except Exception as exc:
        raise NativeFailure(f"GPIOを読み取れません: {exc}") from exc


@native("gpio", "close", (GPIO,), UNIT)
def gpio_close(interpreter, args): interpreter.capabilities.require_device(); args[0].close(); return None


# ---------- Machine control ----------

def _machine_decimal(value: object, name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise NativeFailure(f"machine.{name} には decimal が必要です")
    return value

def _machine_failure(exc: Exception) -> NativeFailure:
    return NativeFailure(f"機械制御処理に失敗しました: {exc}", "SAGA-R103")

@native("machine", "timing_class", (), TEXT)
def machine_timing_class(_interpreter, _args): return "hosted-soft-realtime"

@native("machine", "hard_realtime_available", (), BOOL)
def machine_hard_realtime_available(_interpreter, _args): return False

@native("machine", "monotonic_ns", (), INT)
def machine_monotonic_ns(_interpreter, _args): return pytime.monotonic_ns()

@native("machine", "bytes_from_hex", (TEXT,), BYTES)
def machine_bytes_from_hex(_interpreter, args):
    try:
        text = _as_text(args[0]).strip().replace(" ", "").replace("_", "")
        if len(text) % 2:
            raise ValueError("hex text must contain an even number of digits")
        return bytes.fromhex(text)
    except ValueError as exc:
        raise _machine_failure(MachineControlError(str(exc))) from exc

@native("machine", "bytes_to_hex", (BYTES,), TEXT)
def machine_bytes_to_hex(_interpreter, args): return bytes(args[0]).hex()

@native("machine", "pid", (DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL), MACHINE_PID)
def machine_pid(interpreter, args):
    try:
        value = PIDController.create(*args)
        return interpreter.register_resource(value)
    except (MachineControlError, ArithmeticError) as exc:
        raise _machine_failure(exc) from exc

@native("machine", "pid_step", (MACHINE_PID, DECIMAL, DECIMAL, DECIMAL), DECIMAL)
def machine_pid_step(_interpreter, args):
    try:
        return args[0].step(args[1], args[2], args[3])
    except (MachineControlError, ArithmeticError) as exc:
        raise _machine_failure(exc) from exc

@native("machine", "pid_reset", (MACHINE_PID,), UNIT)
def machine_pid_reset(_interpreter, args): args[0].reset(); return None

@native("machine", "pid_integral_limits", (MACHINE_PID, DECIMAL, DECIMAL), UNIT)
def machine_pid_integral_limits(_interpreter, args):
    try: args[0].set_integral_limits(args[1], args[2]); return None
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "pid2", (DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL), MACHINE_PID2)
def machine_pid2(interpreter, args):
    try: return interpreter.register_resource(TwoDOFPID(*args))
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "pid2_step", (MACHINE_PID2, DECIMAL, DECIMAL, DECIMAL, DECIMAL), DECIMAL)
def machine_pid2_step(_interpreter, args):
    try: return args[0].step(args[1], args[2], args[3], args[4])
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "pid2_reset", (MACHINE_PID2,), UNIT)
def machine_pid2_reset(_interpreter, args): args[0].reset(); return None

@native("machine", "motor_feedforward", (DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL), DECIMAL)
def machine_motor_feedforward(_interpreter, args):
    try: return motor_feedforward(*args)
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "alpha_beta", (DECIMAL, DECIMAL, DECIMAL, DECIMAL), MACHINE_ALPHA_BETA)
def machine_alpha_beta(interpreter, args):
    try: return interpreter.register_resource(AlphaBetaObserver(*args))
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "alpha_beta_step", (MACHINE_ALPHA_BETA, DECIMAL, DECIMAL), LIST(DECIMAL))
def machine_alpha_beta_step(_interpreter, args):
    try: return tuple(args[0].step(args[1], args[2]))
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "alpha_beta_reset", (MACHINE_ALPHA_BETA, DECIMAL, DECIMAL), UNIT)
def machine_alpha_beta_reset(_interpreter, args):
    try: args[0].reset(args[1], args[2]); return None
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "notch", (DECIMAL, DECIMAL, DECIMAL), MACHINE_BIQUAD)
def machine_notch(interpreter, args):
    try: return interpreter.register_resource(BiquadFilter.notch(*args))
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "filter_step", (MACHINE_BIQUAD, DECIMAL), DECIMAL)
def machine_filter_step(_interpreter, args):
    try: return args[0].step(args[1])
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "filter_reset", (MACHINE_BIQUAD,), UNIT)
def machine_filter_reset(_interpreter, args): args[0].reset(); return None

@native("machine", "control_guard", (INT, INT, INT, INT), MACHINE_CONTROL_GUARD)
def machine_control_guard(interpreter, args):
    try:
        return interpreter.register_resource(ControlGuard(_as_int(args[0]), _as_int(args[1]), _as_int(args[2]), _as_int(args[3])))
    except MachineControlError as exc:
        raise _machine_failure(exc) from exc

@native("machine", "control_guard_begin", (MACHINE_CONTROL_GUARD, INT, INT), BOOL)
def machine_control_guard_begin(_interpreter, args):
    try:
        return args[0].begin(_as_int(args[1]), _as_int(args[2]))
    except MachineControlError as exc:
        raise _machine_failure(exc) from exc

@native("machine", "control_guard_end", (MACHINE_CONTROL_GUARD, INT), BOOL)
def machine_control_guard_end(_interpreter, args):
    try:
        return args[0].end(_as_int(args[1]))
    except MachineControlError as exc:
        raise _machine_failure(exc) from exc

@native("machine", "control_guard_ok", (MACHINE_CONTROL_GUARD,), BOOL)
def machine_control_guard_ok(_interpreter, args):
    return args[0].ok()

@native("machine", "control_guard_stats_json", (MACHINE_CONTROL_GUARD,), TEXT)
def machine_control_guard_stats_json(_interpreter, args):
    return args[0].stats_json()

@native("machine", "control_guard_reset", (MACHINE_CONTROL_GUARD,), UNIT)
def machine_control_guard_reset(_interpreter, args):
    args[0].reset(); return None

@native("machine", "deadline_budget", (INT, INT), MACHINE_DEADLINE_BUDGET)
def machine_deadline_budget(interpreter, args):
    try: return interpreter.register_resource(DeadlineBudget(_as_int(args[0]), _as_int(args[1])))
    except MachineControlError as exc: raise _machine_failure(exc) from exc

@native("machine", "budget_begin", (MACHINE_DEADLINE_BUDGET,), UNIT)
def machine_budget_begin(_interpreter, args):
    try: args[0].begin(); return None
    except MachineControlError as exc: raise _machine_failure(exc) from exc

@native("machine", "budget_end", (MACHINE_DEADLINE_BUDGET,), BOOL)
def machine_budget_end(_interpreter, args):
    try: return args[0].end()
    except MachineControlError as exc: raise _machine_failure(exc) from exc

@native("machine", "budget_stats_json", (MACHINE_DEADLINE_BUDGET,), TEXT)
def machine_budget_stats_json(_interpreter, args): return args[0].stats_json()

@native("machine", "budget_reset", (MACHINE_DEADLINE_BUDGET,), UNIT)
def machine_budget_reset(_interpreter, args): args[0].reset(); return None

@native("machine", "clarke", (DECIMAL, DECIMAL, DECIMAL), LIST(DECIMAL))
def machine_clarke(_interpreter, args):
    try: return tuple(clarke(*args))
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "park", (DECIMAL, DECIMAL, DECIMAL), LIST(DECIMAL))
def machine_park(_interpreter, args):
    try: return tuple(park(*args))
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "inverse_park", (DECIMAL, DECIMAL, DECIMAL), LIST(DECIMAL))
def machine_inverse_park(_interpreter, args):
    try: return tuple(inverse_park(*args))
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "svpwm", (DECIMAL, DECIMAL, DECIMAL), LIST(DECIMAL))
def machine_svpwm(_interpreter, args):
    try: return tuple(svpwm(*args))
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

# ---------- Advanced motion-control kernel (0.47) ----------

@native("machine", "foc_current", (DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL), MACHINE_FOC_CURRENT)
def machine_foc_current(interpreter, args):
    try: return interpreter.register_resource(FOCCurrentLoop(*args))
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "foc_step", (MACHINE_FOC_CURRENT, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL), UNIT)
def machine_foc_step(_interpreter, args):
    try: args[0].step(*args[1:]); return None
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "foc_reset", (MACHINE_FOC_CURRENT,), UNIT)
def machine_foc_reset(_interpreter, args): args[0].reset(); return None

@native("machine", "foc_id", (MACHINE_FOC_CURRENT,), DECIMAL)
def machine_foc_id(_interpreter, args): return args[0].measured_d

@native("machine", "foc_iq", (MACHINE_FOC_CURRENT,), DECIMAL)
def machine_foc_iq(_interpreter, args): return args[0].measured_q

@native("machine", "foc_vd", (MACHINE_FOC_CURRENT,), DECIMAL)
def machine_foc_vd(_interpreter, args): return args[0].voltage_d

@native("machine", "foc_vq", (MACHINE_FOC_CURRENT,), DECIMAL)
def machine_foc_vq(_interpreter, args): return args[0].voltage_q

@native("machine", "foc_duty", (MACHINE_FOC_CURRENT, INT), DECIMAL)
def machine_foc_duty(_interpreter, args):
    phase = _as_int(args[1])
    if phase == 0: return args[0].duty_a
    if phase == 1: return args[0].duty_b
    if phase == 2: return args[0].duty_c
    raise _machine_failure(MachineControlError("FOC phase index must be 0..2"))

@native("machine", "encoder_integrated", (INT, DECIMAL, INT, INT, DECIMAL), MACHINE_ENCODER_UNIFIED)
def machine_encoder_integrated(interpreter, args):
    try: return interpreter.register_resource(UnifiedEncoder(_as_int(args[0]), args[1], _as_int(args[2]), _as_int(args[3]), args[4]))
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "encoder_sample", (MACHINE_ENCODER_UNIFIED, INT, INT), UNIT)
def machine_encoder_sample(_interpreter, args):
    try: args[0].sample(_as_int(args[1]), _as_int(args[2])); return None
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "encoder_align_absolute", (MACHINE_ENCODER_UNIFIED, INT, DECIMAL), UNIT)
def machine_encoder_align_absolute(_interpreter, args):
    try: args[0].align_absolute(_as_int(args[1]), args[2]); return None
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "encoder_position_deg", (MACHINE_ENCODER_UNIFIED,), DECIMAL)
def machine_encoder_position_deg(_interpreter, args): return args[0].position_degrees

@native("machine", "encoder_velocity_deg_s", (MACHINE_ENCODER_UNIFIED,), DECIMAL)
def machine_encoder_velocity_deg_s(_interpreter, args): return args[0].velocity_deg_s

@native("machine", "encoder_integrated_velocity_rpm", (MACHINE_ENCODER_UNIFIED,), DECIMAL)
def machine_encoder_integrated_velocity_rpm(_interpreter, args): return args[0].velocity_rpm

@native("machine", "rls2", (DECIMAL, DECIMAL), MACHINE_RLS2)
def machine_rls2(interpreter, args):
    try: return interpreter.register_resource(RLS2.create(*args))
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "rls2_update", (MACHINE_RLS2, DECIMAL, DECIMAL, DECIMAL), UNIT)
def machine_rls2_update(_interpreter, args):
    try: args[0].update(args[1], args[2], args[3]); return None
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "rls2_theta0", (MACHINE_RLS2,), DECIMAL)
def machine_rls2_theta0(_interpreter, args): return args[0].theta0

@native("machine", "rls2_theta1", (MACHINE_RLS2,), DECIMAL)
def machine_rls2_theta1(_interpreter, args): return args[0].theta1

@native("machine", "rls2_error", (MACHINE_RLS2,), DECIMAL)
def machine_rls2_error(_interpreter, args): return args[0].last_error

@native("machine", "mpc2", (DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, INT, DECIMAL, DECIMAL), MACHINE_MPC2)
def machine_mpc2(interpreter, args):
    try: return interpreter.register_resource(MPC2(*args[:9], _as_int(args[9]), args[10], args[11]))
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "mpc2_step", (MACHINE_MPC2, DECIMAL, DECIMAL, DECIMAL, DECIMAL), DECIMAL)
def machine_mpc2_step(_interpreter, args):
    try: return args[0].step(*args[1:])
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "mpc2_reset", (MACHINE_MPC2,), UNIT)
def machine_mpc2_reset(_interpreter, args): args[0].reset(); return None

@native("machine", "disturbance_observer", (DECIMAL, DECIMAL, DECIMAL), MACHINE_DOB)
def machine_disturbance_observer(interpreter, args):
    try: return interpreter.register_resource(DisturbanceObserver(*args))
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "disturbance_step", (MACHINE_DOB, DECIMAL, DECIMAL, DECIMAL), DECIMAL)
def machine_disturbance_step(_interpreter, args):
    try: return args[0].step(args[1], args[2], args[3])
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "disturbance_reset", (MACHINE_DOB, DECIMAL), UNIT)
def machine_disturbance_reset(_interpreter, args):
    try: args[0].reset(args[1]); return None
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "friction_compensation", (DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL), DECIMAL)
def machine_friction_compensation(_interpreter, args):
    try: return friction_compensation(*args)
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "axis_sync", (INT, DECIMAL, DECIMAL, DECIMAL), MACHINE_AXIS_SYNC)
def machine_axis_sync(interpreter, args):
    try: return interpreter.register_resource(MultiAxisSynchronizer(_as_int(args[0]), args[1], args[2], args[3]))
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "axis_sync_config", (MACHINE_AXIS_SYNC, INT, DECIMAL, DECIMAL), UNIT)
def machine_axis_sync_config(_interpreter, args):
    try: args[0].configure(_as_int(args[1]), args[2], args[3]); return None
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "axis_sync_begin", (MACHINE_AXIS_SYNC, DECIMAL), UNIT)
def machine_axis_sync_begin(_interpreter, args):
    try: args[0].begin(args[1]); return None
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "axis_sync_correction", (MACHINE_AXIS_SYNC, INT, DECIMAL), DECIMAL)
def machine_axis_sync_correction(_interpreter, args):
    try: return args[0].correction(_as_int(args[1]), args[2])
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "axis_sync_error", (MACHINE_AXIS_SYNC, INT), DECIMAL)
def machine_axis_sync_error(_interpreter, args):
    try: return args[0].error(_as_int(args[1]))
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "axis_sync_ok", (MACHINE_AXIS_SYNC,), BOOL)
def machine_axis_sync_ok(_interpreter, args): return args[0].healthy

@native("machine", "ethercat_datagram", (TEXT, INT, INT, INT, BYTES, INT, BOOL), BYTES)
def machine_ethercat_datagram(_interpreter, args):
    try: return ethercat_datagram(_as_text(args[0]), _as_int(args[1]), _as_int(args[2]), _as_int(args[3]), args[4], _as_int(args[5]), bool(args[6]))
    except MachineControlError as exc: raise _machine_failure(exc) from exc

@native("machine", "ethercat_frame", (BYTES,), BYTES)
def machine_ethercat_frame(_interpreter, args):
    try: return ethercat_frame(args[0])
    except MachineControlError as exc: raise _machine_failure(exc) from exc

@native("machine", "ethercat_lrw", (INT, INT, BYTES), BYTES)
def machine_ethercat_lrw(_interpreter, args):
    try: return ethercat_lrw(_as_int(args[0]), _as_int(args[1]), args[2])
    except MachineControlError as exc: raise _machine_failure(exc) from exc

@native("machine", "ethercat_first_datagram_json", (BYTES,), TEXT)
def machine_ethercat_first_datagram_json(_interpreter, args):
    try: return ethercat_first_datagram_json(args[0])
    except MachineControlError as exc: raise _machine_failure(exc) from exc

@native("machine", "allocation_free_profile_json", (), TEXT)
def machine_allocation_free_profile_json(_interpreter, _args): return allocation_free_profile_json()

@native("machine", "slew", (DECIMAL, DECIMAL, DECIMAL, DECIMAL), DECIMAL)
def machine_slew(_interpreter, args):
    try: return slew(*args)
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "low_pass", (DECIMAL, DECIMAL, DECIMAL), DECIMAL)
def machine_low_pass(_interpreter, args):
    try: return low_pass(args[0], args[1], args[2])
    except MachineControlError as exc: raise _machine_failure(exc) from exc


@native("machine", "deadband", (DECIMAL, DECIMAL), DECIMAL)
def machine_deadband(_interpreter, args):
    try: return deadband(args[0], args[1])
    except MachineControlError as exc: raise _machine_failure(exc) from exc

@native("machine", "integrate_clamped", (DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL), DECIMAL)
def machine_integrate_clamped(_interpreter, args):
    try: return integrate_clamped(args[0], args[1], args[2], args[3], args[4])
    except MachineControlError as exc: raise _machine_failure(exc) from exc

@native("machine", "profile", (DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL), MACHINE_PROFILE)
def machine_profile(interpreter, args):
    try: return interpreter.register_resource(MotionProfile(*args))
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "profile_step", (MACHINE_PROFILE, DECIMAL), DECIMAL)
def machine_profile_step(_interpreter, args):
    try: return args[0].step(args[1])
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "profile_velocity", (MACHINE_PROFILE,), DECIMAL)
def machine_profile_velocity(_interpreter, args): return args[0].velocity

@native("machine", "profile_done", (MACHINE_PROFILE,), BOOL)
def machine_profile_done(_interpreter, args): return args[0].done()

@native("machine", "profile_retarget", (MACHINE_PROFILE, DECIMAL), UNIT)
def machine_profile_retarget(_interpreter, args):
    try: args[0].retarget(args[1]); return None
    except MachineControlError as exc: raise _machine_failure(exc) from exc

@native("machine", "watchdog", (INT,), MACHINE_WATCHDOG)
def machine_watchdog(interpreter, args):
    try: return interpreter.register_resource(Watchdog(_as_int(args[0])))
    except MachineControlError as exc: raise _machine_failure(exc) from exc

@native("machine", "watchdog_feed", (MACHINE_WATCHDOG,), UNIT)
def machine_watchdog_feed(_interpreter, args): args[0].feed(); return None

@native("machine", "watchdog_expired", (MACHINE_WATCHDOG,), BOOL)
def machine_watchdog_expired(_interpreter, args): return args[0].expired()

@native("machine", "watchdog_remaining_ms", (MACHINE_WATCHDOG,), INT)
def machine_watchdog_remaining_ms(_interpreter, args): return args[0].remaining_ms()

@native("machine", "watchdog_check", (MACHINE_WATCHDOG, MACHINE_SAFETY, TEXT), BOOL)
def machine_watchdog_check(_interpreter, args):
    if not args[0].expired():
        return False
    try:
        args[1].trip(_as_text(args[2]))
        return True
    except MachineControlError as exc:
        raise _machine_failure(exc) from exc

@native("machine", "safety_latch", (), MACHINE_SAFETY)
def machine_safety_latch(interpreter, _args): return interpreter.register_resource(SafetyLatch())

@native("machine", "safety_trip", (MACHINE_SAFETY, TEXT), UNIT)
def machine_safety_trip(_interpreter, args): args[0].trip(_as_text(args[1])); return None

@native("machine", "safety_clear", (MACHINE_SAFETY,), UNIT)
def machine_safety_clear(_interpreter, args):
    try: args[0].clear(); return None
    except MachineControlError as exc: raise _machine_failure(exc) from exc

@native("machine", "safety_tripped", (MACHINE_SAFETY,), BOOL)
def machine_safety_tripped(_interpreter, args): return args[0].tripped

@native("machine", "safety_reason", (MACHINE_SAFETY,), TEXT)
def machine_safety_reason(_interpreter, args): return args[0].reason

@native("machine", "safety_check", (MACHINE_SAFETY, BOOL, TEXT), BOOL)
def machine_safety_check(_interpreter, args):
    safe = bool(args[1])
    if safe:
        return True
    try:
        args[0].trip(_as_text(args[2]))
        return False
    except MachineControlError as exc:
        raise _machine_failure(exc) from exc

@native("machine", "cycle", (INT,), MACHINE_CYCLE)
def machine_cycle(interpreter, args):
    try: return interpreter.register_resource(ControlCycle(_as_int(args[0])))
    except MachineControlError as exc: raise _machine_failure(exc) from exc

@native("machine", "cycle_wait", (MACHINE_CYCLE,), UNIT)
def machine_cycle_wait(_interpreter, args): args[0].wait(); return None

@native("machine", "cycle_overruns", (MACHINE_CYCLE,), INT)
def machine_cycle_overruns(_interpreter, args): return args[0].overruns

@native("machine", "cycle_jitter_us", (MACHINE_CYCLE,), INT)
def machine_cycle_jitter(_interpreter, args): return args[0].last_jitter_us

@native("machine", "servo_duty", (DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL), DECIMAL)
def machine_servo_duty(_interpreter, args):
    try: return servo_duty(*args)
    except (MachineControlError, ArithmeticError) as exc: raise _machine_failure(exc) from exc

@native("machine", "i2c_open", (TEXT, INT), MACHINE_I2C)
def machine_i2c_open(interpreter, args):
    interpreter.capabilities.require_device()
    try: return interpreter.register_resource(I2CDevice(_as_text(args[0]), _as_int(args[1])))
    except (OSError, MachineControlError) as exc: raise _machine_failure(exc) from exc

@native("machine", "i2c_write", (MACHINE_I2C, BYTES), UNIT)
def machine_i2c_write(interpreter, args):
    interpreter.capabilities.require_device()
    try: args[0].write(args[1]); return None
    except (OSError, MachineControlError) as exc: raise _machine_failure(exc) from exc

@native("machine", "i2c_read", (MACHINE_I2C, INT), BYTES)
def machine_i2c_read(interpreter, args):
    interpreter.capabilities.require_device()
    try: return args[0].read(_as_int(args[1]))
    except (OSError, MachineControlError) as exc: raise _machine_failure(exc) from exc

@native("machine", "i2c_write_read", (MACHINE_I2C, BYTES, INT), BYTES)
def machine_i2c_write_read(interpreter, args):
    interpreter.capabilities.require_device()
    try: return args[0].write_read(args[1], _as_int(args[2]))
    except (OSError, MachineControlError) as exc: raise _machine_failure(exc) from exc

@native("machine", "i2c_close", (MACHINE_I2C,), UNIT)
def machine_i2c_close(interpreter, args): interpreter.capabilities.require_device(); args[0].close(); return None

@native("machine", "spi_open", (TEXT, INT, INT, INT), MACHINE_SPI)
def machine_spi_open(interpreter, args):
    interpreter.capabilities.require_device()
    try: return interpreter.register_resource(SPIDevice(_as_text(args[0]), _as_int(args[1]), _as_int(args[2]), _as_int(args[3])))
    except (OSError, MachineControlError) as exc: raise _machine_failure(exc) from exc

@native("machine", "spi_transfer", (MACHINE_SPI, BYTES), BYTES)
def machine_spi_transfer(interpreter, args):
    interpreter.capabilities.require_device()
    try: return args[0].transfer(args[1])
    except (OSError, MachineControlError) as exc: raise _machine_failure(exc) from exc

@native("machine", "spi_close", (MACHINE_SPI,), UNIT)
def machine_spi_close(interpreter, args): interpreter.capabilities.require_device(); args[0].close(); return None

@native("machine", "uart_open", (TEXT, INT, INT), MACHINE_UART)
def machine_uart_open(interpreter, args):
    interpreter.capabilities.require_device()
    try: return interpreter.register_resource(UARTDevice(_as_text(args[0]), _as_int(args[1]), _as_int(args[2])))
    except (OSError, MachineControlError) as exc: raise _machine_failure(exc) from exc

@native("machine", "uart_write", (MACHINE_UART, BYTES), UNIT)
def machine_uart_write(interpreter, args):
    interpreter.capabilities.require_device()
    try: args[0].write(args[1]); return None
    except (OSError, MachineControlError) as exc: raise _machine_failure(exc) from exc

@native("machine", "uart_read", (MACHINE_UART, INT), BYTES)
def machine_uart_read(interpreter, args):
    interpreter.capabilities.require_device()
    try: return args[0].read(_as_int(args[1]))
    except (OSError, MachineControlError) as exc: raise _machine_failure(exc) from exc

@native("machine", "uart_close", (MACHINE_UART,), UNIT)
def machine_uart_close(interpreter, args): interpreter.capabilities.require_device(); args[0].close(); return None

@native("machine", "can_open", (TEXT, BOOL), MACHINE_CAN)
def machine_can_open(interpreter, args):
    interpreter.capabilities.require_device()
    try: return interpreter.register_resource(CANDevice(_as_text(args[0]), bool(args[1])))
    except (OSError, MachineControlError) as exc: raise _machine_failure(exc) from exc

@native("machine", "can_send", (MACHINE_CAN, INT, BYTES), UNIT)
def machine_can_send(interpreter, args):
    interpreter.capabilities.require_device()
    try: args[0].send(_as_int(args[1]), args[2]); return None
    except (OSError, MachineControlError) as exc: raise _machine_failure(exc) from exc

@native("machine", "can_recv", (MACHINE_CAN, INT), TEXT)
def machine_can_recv(interpreter, args):
    interpreter.capabilities.require_device()
    try: return can_frame_json(args[0].recv(_as_int(args[1])))
    except (OSError, MachineControlError) as exc: raise _machine_failure(exc) from exc

@native("machine", "can_close", (MACHINE_CAN,), UNIT)
def machine_can_close(interpreter, args): interpreter.capabilities.require_device(); args[0].close(); return None

@native("machine", "can_timestamping", (MACHINE_CAN, BOOL), UNIT)
def machine_can_timestamping(interpreter, args):
    interpreter.capabilities.require_device()
    try: args[0].enable_timestamping(bool(args[1])); return None
    except (OSError, MachineControlError) as exc: raise _machine_failure(exc) from exc

@native("machine", "canfd_send", (MACHINE_CAN, INT, BYTES, BOOL), UNIT)
def machine_canfd_send(interpreter, args):
    interpreter.capabilities.require_device()
    try:
        if not args[0].fd_mode: raise MachineControlError("canfd_send requires can_open(..., true)")
        flags = CANDevice.CANFD_BRS if bool(args[3]) else 0
        args[0].send(_as_int(args[1]), args[2], flags); return None
    except (OSError, MachineControlError) as exc: raise _machine_failure(exc) from exc

@native("machine", "canfd_recv", (MACHINE_CAN, INT), TEXT)
def machine_canfd_recv(interpreter, args):
    interpreter.capabilities.require_device()
    try:
        frame = args[0].recv_fd_timestamped(_as_int(args[1]))
        if frame is None: return canfd_frame_json(False)
        return canfd_frame_json(True, frame[0], frame[1], frame[2], frame[3], frame[4])
    except (OSError, MachineControlError) as exc: raise _machine_failure(exc) from exc

@native("machine", "ethercat_open", (TEXT, BYTES, BOOL), MACHINE_ETHERCAT)
def machine_ethercat_open(interpreter, args):
    interpreter.capabilities.require_device()
    try: return interpreter.register_resource(EtherCATRawDevice(_as_text(args[0]), args[1], bool(args[2])))
    except (OSError, MachineControlError) as exc: raise _machine_failure(exc) from exc

@native("machine", "ethercat_exchange", (MACHINE_ETHERCAT, BYTES, INT), TEXT)
def machine_ethercat_exchange(interpreter, args):
    interpreter.capabilities.require_device()
    try:
        frame, timestamp_ns, source = args[0].exchange(args[1], _as_int(args[2]))
        return json.dumps({"frame_hex": frame.hex(), "timestamp_ns": timestamp_ns, "timestamp_source": source}, separators=(",", ":"), sort_keys=True)
    except (OSError, MachineControlError) as exc: raise _machine_failure(exc) from exc

@native("machine", "ethercat_close", (MACHINE_ETHERCAT,), UNIT)
def machine_ethercat_close(interpreter, args): interpreter.capabilities.require_device(); args[0].close(); return None

@native("machine", "pwm_open", (INT, INT, INT), MACHINE_PWM)
def machine_pwm_open(interpreter, args):
    interpreter.capabilities.require_device()
    try: return interpreter.register_resource(PWMChannel(_as_int(args[0]), _as_int(args[1]), _as_int(args[2])))
    except (OSError, MachineControlError) as exc: raise _machine_failure(exc) from exc

@native("machine", "pwm_write", (MACHINE_PWM, DECIMAL), UNIT)
def machine_pwm_write(interpreter, args):
    interpreter.capabilities.require_device()
    try: args[0].set_duty(args[1]); return None
    except (OSError, MachineControlError) as exc: raise _machine_failure(exc) from exc

@native("machine", "pwm_enable", (MACHINE_PWM,), UNIT)
def machine_pwm_enable(interpreter, args): interpreter.capabilities.require_device(); args[0].enable(); return None

@native("machine", "pwm_disable", (MACHINE_PWM,), UNIT)
def machine_pwm_disable(interpreter, args): interpreter.capabilities.require_device(); args[0].disable(); return None

@native("machine", "pwm_close", (MACHINE_PWM,), UNIT)
def machine_pwm_close(interpreter, args): interpreter.capabilities.require_device(); args[0].close(); return None

@native("machine", "servo", (MACHINE_PWM, DECIMAL, DECIMAL, DECIMAL, DECIMAL), MACHINE_SERVO)
def machine_servo(interpreter, args):
    try: return interpreter.register_resource(Servo(args[0], args[1], args[2], args[3], args[4]))
    except MachineControlError as exc: raise _machine_failure(exc) from exc

@native("machine", "servo_write", (MACHINE_SERVO, DECIMAL), UNIT)
def machine_servo_write(interpreter, args):
    interpreter.capabilities.require_device()
    try: args[0].write_degrees(args[1]); return None
    except (OSError, MachineControlError) as exc: raise _machine_failure(exc) from exc

@native("machine", "servo_guard", (MACHINE_SERVO, MACHINE_SAFETY), UNIT)
def machine_servo_guard(interpreter, args): interpreter.capabilities.require_device(); args[0].guard(args[1]); return None

@native("machine", "encoder", (INT, DECIMAL), MACHINE_ENCODER)
def machine_encoder(interpreter, args):
    try: return interpreter.register_resource(EncoderTracker(_as_int(args[0]), args[1]))
    except MachineControlError as exc: raise _machine_failure(exc) from exc

@native("machine", "encoder_wrap", (MACHINE_ENCODER, INT), UNIT)
def machine_encoder_wrap(_interpreter, args):
    try: args[0].set_wrap_modulus(_as_int(args[1])); return None
    except MachineControlError as exc: raise _machine_failure(exc) from exc

@native("machine", "encoder_unwrapped_count", (MACHINE_ENCODER,), INT)
def machine_encoder_unwrapped_count(_interpreter, args): return args[0].unwrapped_count

@native("machine", "encoder_update", (MACHINE_ENCODER, INT, INT), UNIT)
def machine_encoder_update(_interpreter, args):
    try: args[0].update(_as_int(args[1]), _as_int(args[2])); return None
    except MachineControlError as exc: raise _machine_failure(exc) from exc

@native("machine", "encoder_update_now", (MACHINE_ENCODER, INT), UNIT)
def machine_encoder_update_now(_interpreter, args):
    try: args[0].update(_as_int(args[1])); return None
    except MachineControlError as exc: raise _machine_failure(exc) from exc

@native("machine", "encoder_position_degrees", (MACHINE_ENCODER,), DECIMAL)
def machine_encoder_position_degrees(_interpreter, args): return args[0].position_degrees

@native("machine", "encoder_velocity_rpm", (MACHINE_ENCODER,), DECIMAL)
def machine_encoder_velocity_rpm(_interpreter, args): return args[0].velocity_rpm

@native("machine", "encoder_reset", (MACHINE_ENCODER, INT), UNIT)
def machine_encoder_reset(_interpreter, args): args[0].reset(_as_int(args[1])); return None

@native("machine", "motor", (MACHINE_PWM, MACHINE_PWM, DECIMAL, MACHINE_SAFETY), MACHINE_MOTOR)
def machine_motor(interpreter, args):
    interpreter.capabilities.require_device()
    try: return interpreter.register_resource(DCMotor(args[0], args[1], args[2], args[3]))
    except MachineControlError as exc: raise _machine_failure(exc) from exc

@native("machine", "motor_write", (MACHINE_MOTOR, DECIMAL), UNIT)
def machine_motor_write(interpreter, args):
    interpreter.capabilities.require_device()
    try: args[0].write(args[1]); return None
    except (OSError, MachineControlError) as exc: raise _machine_failure(exc) from exc

@native("machine", "motor_stop", (MACHINE_MOTOR,), UNIT)
def machine_motor_stop(interpreter, args):
    interpreter.capabilities.require_device()
    try: args[0].stop(); return None
    except (OSError, MachineControlError) as exc: raise _machine_failure(exc) from exc

@native("machine", "motor_command", (MACHINE_MOTOR,), DECIMAL)
def machine_motor_command(_interpreter, args): return args[0].command


@native("machine", "s_curve", (DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL), MACHINE_SCURVE)
def machine_s_curve(_interpreter, args):
    try: return JerkLimitedProfile(*args)
    except MachineControlError as exc: raise NativeFailure(str(exc)) from exc

@native("machine", "s_curve_step", (MACHINE_SCURVE, DECIMAL), DECIMAL)
def machine_s_curve_step(_interpreter, args):
    try: return args[0].step(args[1])
    except MachineControlError as exc: raise NativeFailure(str(exc)) from exc

@native("machine", "s_curve_velocity", (MACHINE_SCURVE,), DECIMAL)
def machine_s_curve_velocity(_interpreter, args): return args[0].velocity

@native("machine", "s_curve_acceleration", (MACHINE_SCURVE,), DECIMAL)
def machine_s_curve_acceleration(_interpreter, args): return args[0].acceleration

@native("machine", "s_curve_done", (MACHINE_SCURVE,), BOOL)
def machine_s_curve_done(_interpreter, args): return args[0].done()

@native("machine", "s_curve_retarget", (MACHINE_SCURVE, DECIMAL), UNIT)
def machine_s_curve_retarget(_interpreter, args):
    try: args[0].retarget(args[1]); return None
    except MachineControlError as exc: raise NativeFailure(str(exc)) from exc

@native("machine", "axis", (DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, MACHINE_SAFETY), MACHINE_AXIS)
def machine_axis(_interpreter, args):
    try: return AxisController.create(*args)
    except MachineControlError as exc: raise NativeFailure(str(exc)) from exc

@native("machine", "axis_target", (MACHINE_AXIS, DECIMAL), UNIT)
def machine_axis_target(_interpreter, args):
    try: args[0].set_target(args[1]); return None
    except MachineControlError as exc: raise NativeFailure(str(exc)) from exc

@native("machine", "axis_step", (MACHINE_AXIS, DECIMAL, DECIMAL), DECIMAL)
def machine_axis_step(_interpreter, args):
    try: return args[0].step(args[1], args[2])
    except MachineControlError as exc: raise NativeFailure(str(exc)) from exc

@native("machine", "axis_command", (MACHINE_AXIS,), DECIMAL)
def machine_axis_command(_interpreter, args): return args[0].command

@native("machine", "axis_planned_position", (MACHINE_AXIS,), DECIMAL)
def machine_axis_planned_position(_interpreter, args): return args[0].profile.position

@native("machine", "axis_done", (MACHINE_AXIS, DECIMAL), BOOL)
def machine_axis_done(_interpreter, args):
    try: return args[0].done(args[1])
    except MachineControlError as exc: raise NativeFailure(str(exc)) from exc

@native("machine", "modbus_crc16", (BYTES,), INT)
def machine_modbus_crc16(_interpreter, args): return modbus_crc16(bytes(args[0]))

@native("machine", "modbus_rtu_open", (TEXT, INT, INT, INT), MACHINE_MODBUS_RTU)
def machine_modbus_rtu_open(interpreter, args):
    interpreter.capabilities.require_device()
    try: return interpreter.register_resource(ModbusRTUMaster(_as_text(args[0]), _as_int(args[1]), _as_int(args[2]), _as_int(args[3])))
    except MachineControlError as exc: raise NativeFailure(str(exc)) from exc
    except OSError as exc: raise NativeFailure(f"Modbus RTU open failed: {exc}") from exc

@native("machine", "modbus_tcp_open", (TEXT, INT, INT, INT), MACHINE_MODBUS_TCP)
def machine_modbus_tcp_open(interpreter, args):
    interpreter.capabilities.require_device()
    host, port = _as_text(args[0]), _as_int(args[1])
    interpreter.capabilities.require_net(host, port)
    try: return interpreter.register_resource(ModbusTCPMaster(host, port, _as_int(args[2]), _as_int(args[3])))
    except (MachineControlError, OSError) as exc: raise NativeFailure(f"Modbus TCP open failed: {exc}") from exc

def _modbus_master(value):
    if not isinstance(value, (ModbusRTUMaster, ModbusTCPMaster)):
        raise NativeFailure("machine.modbus_* requires a Modbus RTU/TCP master")
    return value

@native("machine", "modbus_read_holding", (ANY, INT, INT), LIST(INT))
def machine_modbus_read_holding(_interpreter, args):
    try: return _modbus_master(args[0]).read_holding_registers(_as_int(args[1]), _as_int(args[2]))
    except (MachineControlError, OSError) as exc: raise NativeFailure(str(exc)) from exc

@native("machine", "modbus_read_input", (ANY, INT, INT), LIST(INT))
def machine_modbus_read_input(_interpreter, args):
    try: return _modbus_master(args[0]).read_input_registers(_as_int(args[1]), _as_int(args[2]))
    except (MachineControlError, OSError) as exc: raise NativeFailure(str(exc)) from exc

@native("machine", "modbus_read_coils", (ANY, INT, INT), LIST(BOOL))
def machine_modbus_read_coils(_interpreter, args):
    try: return _modbus_master(args[0]).read_coils(_as_int(args[1]), _as_int(args[2]))
    except (MachineControlError, OSError) as exc: raise NativeFailure(str(exc)) from exc

@native("machine", "modbus_write_register", (ANY, INT, INT), UNIT)
def machine_modbus_write_register(_interpreter, args):
    try: _modbus_master(args[0]).write_register(_as_int(args[1]), _as_int(args[2])); return None
    except (MachineControlError, OSError) as exc: raise NativeFailure(str(exc)) from exc

@native("machine", "modbus_write_registers", (ANY, INT, LIST(INT)), UNIT)
def machine_modbus_write_registers(_interpreter, args):
    try: _modbus_master(args[0]).write_registers(_as_int(args[1]), [_as_int(v) for v in args[2]]); return None
    except (MachineControlError, OSError) as exc: raise NativeFailure(str(exc)) from exc

@native("machine", "modbus_write_coil", (ANY, INT, BOOL), UNIT)
def machine_modbus_write_coil(_interpreter, args):
    try: _modbus_master(args[0]).write_coil(_as_int(args[1]), bool(args[2])); return None
    except (MachineControlError, OSError) as exc: raise NativeFailure(str(exc)) from exc

@native("machine", "modbus_close", (ANY,), UNIT)
def machine_modbus_close(_interpreter, args):
    try: _modbus_master(args[0]).close(); return None
    except (MachineControlError, OSError) as exc: raise NativeFailure(str(exc)) from exc

@native("machine", "iio_read", (TEXT, DECIMAL), DECIMAL)
def machine_iio_read(interpreter, args):
    interpreter.capabilities.require_device()
    path = Path(_as_text(args[0])).resolve(strict=False)
    root = Path("/sys/bus/iio/devices").resolve(strict=False)
    if path != root and root not in path.parents:
        raise NativeFailure("machine.iio_read は /sys/bus/iio/devices 配下だけを読み取れます", "SAGA-R103")
    try:
        raw = Decimal(path.read_text(encoding="ascii").strip())
        return raw * args[1]
    except (OSError, ArithmeticError) as exc:
        raise _machine_failure(exc) from exc


# ---------- Drone / flight control ----------

def _drone_failure(exc: Exception) -> NativeFailure:
    return NativeFailure(f"ドローン制御処理に失敗しました: {exc}", "SAGA-R196")

@native("drone", "profile", (), TEXT)
def drone_profile(_interpreter, _args): return "hosted-flight-control-sitl-hil"

@native("drone", "hard_realtime_available", (), BOOL)
def drone_hard_realtime_available(_interpreter, _args): return False

@native("drone", "attitude_estimator", (DECIMAL,), DRONE_ATTITUDE_ESTIMATOR)
def drone_attitude_estimator(interpreter, args):
    try: return interpreter.register_resource(AttitudeEstimator(args[0]))
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "attitude_update", (DRONE_ATTITUDE_ESTIMATOR, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL), LIST(DECIMAL))
def drone_attitude_update(_interpreter, args):
    try: return tuple(args[0].update(*args[1:]))
    except (DroneControlError, ArithmeticError) as exc: raise _drone_failure(exc) from exc

@native("drone", "attitude_rpy", (DRONE_ATTITUDE_ESTIMATOR,), LIST(DECIMAL))
def drone_attitude_rpy(_interpreter, args): return (args[0].roll, args[0].pitch, args[0].yaw)

@native("drone", "attitude_healthy", (DRONE_ATTITUDE_ESTIMATOR,), BOOL)
def drone_attitude_healthy(_interpreter, args): return bool(args[0].healthy)

@native("drone", "attitude_controller", (DECIMAL, DECIMAL, DECIMAL, DECIMAL), DRONE_ATTITUDE_CONTROLLER)
def drone_attitude_controller(interpreter, args):
    try: return interpreter.register_resource(AttitudeController(*args))
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "attitude_step", (DRONE_ATTITUDE_CONTROLLER, LIST(DECIMAL), LIST(DECIMAL)), LIST(DECIMAL))
def drone_attitude_step(_interpreter, args):
    try: return tuple(args[0].step(args[1], args[2]))
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "quaternion_from_rpy", (DECIMAL, DECIMAL, DECIMAL), LIST(DECIMAL))
def drone_quaternion_from_rpy(_interpreter, args):
    try: return tuple(quaternion_from_rpy(*args))
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "quaternion_controller", (DECIMAL, DECIMAL, DECIMAL, DECIMAL), DRONE_QUATERNION_CONTROLLER)
def drone_quaternion_controller(interpreter, args):
    try: return interpreter.register_resource(QuaternionAttitudeController(*args))
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "quaternion_step", (DRONE_QUATERNION_CONTROLLER, LIST(DECIMAL), LIST(DECIMAL)), LIST(DECIMAL))
def drone_quaternion_step(_interpreter, args):
    try: return tuple(args[0].step(args[1], args[2]))
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "rate_controller", (DECIMAL, DECIMAL, DECIMAL, DECIMAL), DRONE_RATE_CONTROLLER)
def drone_rate_controller(interpreter, args):
    try: return interpreter.register_resource(RateController.create(*args))
    except (DroneControlError, MachineControlError) as exc: raise _drone_failure(exc) from exc

@native("drone", "rate_step", (DRONE_RATE_CONTROLLER, LIST(DECIMAL), LIST(DECIMAL), DECIMAL), LIST(DECIMAL))
def drone_rate_step(_interpreter, args):
    try: return tuple(args[0].step(args[1], args[2], args[3]))
    except (DroneControlError, MachineControlError) as exc: raise _drone_failure(exc) from exc

@native("drone", "rate_reset", (DRONE_RATE_CONTROLLER,), UNIT)
def drone_rate_reset(_interpreter, args): args[0].reset(); return None

@native("drone", "position_controller", (DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL), DRONE_POSITION_CONTROLLER)
def drone_position_controller(interpreter, args):
    try: return interpreter.register_resource(PositionController(*args))
    except (DroneControlError, MachineControlError) as exc: raise _drone_failure(exc) from exc

@native("drone", "position_step", (DRONE_POSITION_CONTROLLER, LIST(DECIMAL), LIST(DECIMAL), LIST(DECIMAL), LIST(DECIMAL), DECIMAL), LIST(DECIMAL))
def drone_position_step(_interpreter, args):
    try: return tuple(args[0].step(args[1], args[2], args[3], args[4], args[5]))
    except (DroneControlError, MachineControlError) as exc: raise _drone_failure(exc) from exc

@native("drone", "quad_x_mixer", (DECIMAL, DECIMAL), DRONE_MIXER)
def drone_quad_x_mixer(interpreter, args):
    try: return interpreter.register_resource(QuadXMixer(*args))
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "mix_quad_x", (DRONE_MIXER, DECIMAL, DECIMAL, DECIMAL, DECIMAL), LIST(DECIMAL))
def drone_mix_quad_x(_interpreter, args):
    try: return tuple(args[0].mix(*args[1:]))
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "geofence", (DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL), DRONE_GEOFENCE)
def drone_geofence(interpreter, args):
    try: return interpreter.register_resource(Geofence(*args))
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "geofence_contains", (DRONE_GEOFENCE, DECIMAL, DECIMAL, DECIMAL), BOOL)
def drone_geofence_contains(_interpreter, args):
    try: return args[0].contains(args[1], args[2], args[3])
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "geofence_distance_m", (DRONE_GEOFENCE, DECIMAL, DECIMAL), DECIMAL)
def drone_geofence_distance_m(_interpreter, args):
    try: return args[0].horizontal_distance_m(args[1], args[2])
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "geofence_predict_breach", (DRONE_GEOFENCE, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL), BOOL)
def drone_geofence_predict_breach(_interpreter, args):
    try: return args[0].predict_breach(*args[1:])
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "mission", (), DRONE_MISSION)
def drone_mission(interpreter, _args): return interpreter.register_resource(MissionPlan())

@native("drone", "mission_add", (DRONE_MISSION, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL), UNIT)
def drone_mission_add(_interpreter, args):
    try: args[0].add(*args[1:]); return None
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "mission_reset", (DRONE_MISSION,), UNIT)
def drone_mission_reset(_interpreter, args): args[0].reset(); return None

@native("drone", "mission_update", (DRONE_MISSION, DECIMAL, DECIMAL, DECIMAL, DECIMAL), TEXT)
def drone_mission_update(_interpreter, args):
    try: return args[0].update(*args[1:])
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "mission_target_json", (DRONE_MISSION,), TEXT)
def drone_mission_target_json(_interpreter, args): return args[0].target_json()

@native("drone", "mission_complete", (DRONE_MISSION,), BOOL)
def drone_mission_complete(_interpreter, args): return bool(args[0].complete)

@native("drone", "flight_manager", (MACHINE_SAFETY, DECIMAL), DRONE_FLIGHT_MANAGER)
def drone_flight_manager(interpreter, args):
    try: return interpreter.register_resource(FlightManager(args[0], args[1]))
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "health_update", (DRONE_FLIGHT_MANAGER, BOOL, BOOL, DECIMAL, BOOL, BOOL, BOOL), UNIT)
def drone_health_update(_interpreter, args):
    try: args[0].update_health(*args[1:]); return None
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "prearm_reason", (DRONE_FLIGHT_MANAGER, BOOL), TEXT)
def drone_prearm_reason(_interpreter, args): return args[0].prearm_reason(bool(args[1]))

@native("drone", "arm", (DRONE_FLIGHT_MANAGER, BOOL), UNIT)
def drone_arm(_interpreter, args):
    try: args[0].arm(bool(args[1])); return None
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "disarm", (DRONE_FLIGHT_MANAGER,), UNIT)
def drone_disarm(_interpreter, args): args[0].disarm(); return None

@native("drone", "set_mode", (DRONE_FLIGHT_MANAGER, TEXT), UNIT)
def drone_set_mode(_interpreter, args):
    try: args[0].set_mode(_as_text(args[1])); return None
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "flight_mode", (DRONE_FLIGHT_MANAGER,), TEXT)
def drone_flight_mode(_interpreter, args): return args[0].mode

@native("drone", "flight_state", (DRONE_FLIGHT_MANAGER,), TEXT)
def drone_flight_state(_interpreter, args): return flight_state_json(args[0])

@native("drone", "flight_allowed", (DRONE_FLIGHT_MANAGER,), BOOL)
def drone_flight_allowed(_interpreter, args): return args[0].flight_allowed()

@native("drone", "control_allowed", (DRONE_FLIGHT_MANAGER,), BOOL)
def drone_control_allowed(_interpreter, args): return args[0].control_allowed()

@native("drone", "rtl", (DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL), DRONE_RTL)
def drone_rtl(interpreter, args):
    try: return interpreter.register_resource(RTLPlanner(*args))
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "rtl_target_json", (DRONE_RTL, DECIMAL, DECIMAL, DECIMAL), TEXT)
def drone_rtl_target_json(_interpreter, args):
    try: return args[0].target_json(args[1], args[2], args[3])
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "landing_vertical_velocity", (DECIMAL, DECIMAL, DECIMAL, DECIMAL), DECIMAL)
def drone_landing_vertical_velocity(_interpreter, args):
    try: return landing_vertical_velocity(*args)
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "dronecan_crc16", (BYTES,), INT)
def drone_dronecan_crc16(_interpreter, args): return dronecan_crc16_ccitt_false(bytes(args[0]))

@native("drone", "dronecan_single_frame_json", (INT, INT, INT, INT, BYTES), TEXT)
def drone_dronecan_single_frame_json(_interpreter, args):
    try:
        result = dronecan_single_frame(_as_int(args[0]), _as_int(args[1]), _as_int(args[2]), _as_int(args[3]), bytes(args[4]))
        return pyjson.dumps(result, separators=(",", ":"), sort_keys=True)
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "dronecan_multiframe_json", (INT, INT, INT, INT, BYTES, BYTES), TEXT)
def drone_dronecan_multiframe_json(_interpreter, args):
    try:
        result = dronecan_multi_frame(_as_int(args[0]), _as_int(args[1]), _as_int(args[2]), _as_int(args[3]), bytes(args[4]), bytes(args[5]))
        return pyjson.dumps(result, separators=(",", ":"), sort_keys=True)
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "dronecan_decode_json", (INT, BYTES), TEXT)
def drone_dronecan_decode_json(_interpreter, args):
    try:
        result = dronecan_single_frame_decode(_as_int(args[0]), bytes(args[1]))
        return pyjson.dumps(result, separators=(",", ":"), sort_keys=True)
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "mavlink_set_attitude_target", (INT, INT, INT, INT, INT, INT, LIST(DECIMAL), LIST(DECIMAL), DECIMAL, INT), BYTES)
def drone_mavlink_set_attitude_target(_interpreter, args):
    try: return mavlink_set_attitude_target(*args)
    except (DroneControlError, ArithmeticError) as exc: raise _drone_failure(exc) from exc

@native("drone", "mavlink_set_position_target_local_ned", (INT, INT, INT, INT, INT, INT, INT, LIST(DECIMAL), LIST(DECIMAL), LIST(DECIMAL), DECIMAL, DECIMAL, INT), BYTES)
def drone_mavlink_set_position_target_local_ned(_interpreter, args):
    try: return mavlink_set_position_target_local_ned(*args)
    except (DroneControlError, ArithmeticError) as exc: raise _drone_failure(exc) from exc

@native("drone", "mavlink_command_long", (INT, INT, INT, INT, INT, INT, INT, LIST(DECIMAL)), BYTES)
def drone_mavlink_command_long(_interpreter, args):
    try: return mavlink_command_long(*args)
    except (DroneControlError, ArithmeticError) as exc: raise _drone_failure(exc) from exc

@native("drone", "mavlink_common_decode_json", (BYTES,), TEXT)
def drone_mavlink_common_decode_json(_interpreter, args):
    try: return pyjson.dumps(mavlink_common_decode(bytes(args[0])), separators=(",", ":"), sort_keys=True)
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "mavlink_stream", (), DRONE_MAVLINK_STREAM)
def drone_mavlink_stream(interpreter, _args): return interpreter.register_resource(MAVLinkStreamParser())

@native("drone", "mavlink_stream_feed_json", (DRONE_MAVLINK_STREAM, BYTES), TEXT)
def drone_mavlink_stream_feed_json(_interpreter, args):
    try: return pyjson.dumps(args[0].feed(bytes(args[1])), separators=(",", ":"), sort_keys=True)
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "mavlink_stream_stats_json", (DRONE_MAVLINK_STREAM,), TEXT)
def drone_mavlink_stream_stats_json(_interpreter, args):
    return pyjson.dumps({"buffered_bytes": len(args[0].buffer), "dropped_bytes": args[0].dropped_bytes, "bad_frames": args[0].bad_frames}, separators=(",", ":"), sort_keys=True)

@native("drone", "dshot_frame", (DECIMAL, BOOL), INT)
def drone_dshot_frame(_interpreter, args):
    try: return dshot_frame(args[0], bool(args[1]))
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "pwm_esc_duty", (DECIMAL, DECIMAL, DECIMAL, DECIMAL), DECIMAL)
def drone_pwm_esc_duty(_interpreter, args):
    try: return pwm_esc_duty(*args)
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "mavlink_encode", (INT, INT, BYTES, INT, INT, INT), BYTES)
def drone_mavlink_encode(_interpreter, args):
    try: return mavlink2_encode(*[_as_int(args[0]), _as_int(args[1]), bytes(args[2]), _as_int(args[3]), _as_int(args[4]), _as_int(args[5])])
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "mavlink_decode_json", (BYTES, INT), TEXT)
def drone_mavlink_decode_json(_interpreter, args):
    try: return pyjson.dumps(mavlink2_decode(bytes(args[0]), _as_int(args[1])), separators=(",", ":"), sort_keys=True)
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "mavlink_encode_signed", (INT, INT, BYTES, INT, INT, INT, BYTES, INT, INT), BYTES)
def drone_mavlink_encode_signed(_interpreter, args):
    try: return mavlink2_encode_signed(_as_int(args[0]), _as_int(args[1]), bytes(args[2]), _as_int(args[3]), _as_int(args[4]), _as_int(args[5]), bytes(args[6]), _as_int(args[7]), _as_int(args[8]))
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "mavlink_verify_signed_json", (BYTES, INT, BYTES, INT), TEXT)
def drone_mavlink_verify_signed_json(_interpreter, args):
    try: return pyjson.dumps(mavlink2_verify_signed(bytes(args[0]), _as_int(args[1]), bytes(args[2]), _as_int(args[3])), separators=(",", ":"), sort_keys=True)
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "mavlink_signing_timestamp", (), INT)
def drone_mavlink_signing_timestamp(_interpreter, _args): return mavlink_signing_timestamp()

@native("drone", "mavlink_heartbeat", (INT, INT, INT, INT, INT, INT, INT, INT), BYTES)
def drone_mavlink_heartbeat(_interpreter, args):
    try: return mavlink_heartbeat(*[_as_int(v) for v in args])
    except DroneControlError as exc: raise _drone_failure(exc) from exc



def _drone_decimal_json(value: object) -> object:
    if isinstance(value, Decimal): return str(value)
    if isinstance(value, tuple): return [_drone_decimal_json(v) for v in value]
    if isinstance(value, list): return [_drone_decimal_json(v) for v in value]
    if isinstance(value, dict): return {str(k): _drone_decimal_json(v) for k, v in value.items()}
    return value

@native("drone", "trajectory3d", (LIST(DECIMAL), LIST(DECIMAL), DECIMAL, DECIMAL, DECIMAL), DRONE_TRAJECTORY)
def drone_trajectory3d(interpreter, args):
    try: return interpreter.register_resource(Trajectory3D.create(*args))
    except (DroneControlError, MachineControlError) as exc: raise _drone_failure(exc) from exc

@native("drone", "trajectory3d_limits", (LIST(DECIMAL), LIST(DECIMAL), LIST(DECIMAL), LIST(DECIMAL), LIST(DECIMAL)), DRONE_TRAJECTORY)
def drone_trajectory3d_limits(interpreter, args):
    try: return interpreter.register_resource(Trajectory3D.create_per_axis(*args))
    except (DroneControlError, MachineControlError) as exc: raise _drone_failure(exc) from exc

@native("drone", "trajectory_retarget", (DRONE_TRAJECTORY, LIST(DECIMAL)), UNIT)
def drone_trajectory_retarget(_interpreter, args):
    try: args[0].retarget(args[1]); return None
    except (DroneControlError, MachineControlError) as exc: raise _drone_failure(exc) from exc

@native("drone", "trajectory_step_json", (DRONE_TRAJECTORY, DECIMAL), TEXT)
def drone_trajectory_step_json(_interpreter, args):
    try: return pyjson.dumps(_drone_decimal_json(args[0].step(args[1])), separators=(",", ":"), sort_keys=True)
    except (DroneControlError, MachineControlError) as exc: raise _drone_failure(exc) from exc

@native("drone", "trajectory_done", (DRONE_TRAJECTORY,), BOOL)
def drone_trajectory_done(_interpreter, args): return bool(args[0].done())

@native("drone", "quad_x_allocator", (DECIMAL, DECIMAL), DRONE_ALLOCATOR)
def drone_quad_x_allocator(interpreter, args):
    try: return interpreter.register_resource(ControlAllocator.quad_x(*args))
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "allocator", (LIST(LIST(DECIMAL)), DECIMAL, DECIMAL), DRONE_ALLOCATOR)
def drone_allocator(interpreter, args):
    try:
        matrix = tuple(tuple(row) for row in args[0])
        return interpreter.register_resource(ControlAllocator(matrix, args[1], args[2]))
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "allocator_disable", (DRONE_ALLOCATOR, LIST(INT)), UNIT)
def drone_allocator_disable(_interpreter, args):
    try: args[0].set_disabled(args[1]); return None
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "allocate", (DRONE_ALLOCATOR, LIST(DECIMAL)), LIST(DECIMAL))
def drone_allocate(_interpreter, args):
    try: return tuple(args[0].allocate(args[1]))
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "allocation_report_json", (DRONE_ALLOCATOR, LIST(DECIMAL)), TEXT)
def drone_allocation_report_json(_interpreter, args):
    try: return pyjson.dumps(_drone_decimal_json(args[0].allocation_report(args[1])), separators=(",",":"), sort_keys=True)
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "link_monitor", (DECIMAL,), DRONE_LINK_MONITOR)
def drone_link_monitor(interpreter, args):
    try: return interpreter.register_resource(LinkMonitor(alpha=args[0]))
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "link_observe", (DRONE_LINK_MONITOR, INT, DECIMAL), UNIT)
def drone_link_observe(_interpreter, args):
    try: args[0].observe(_as_int(args[1]), args[2]); return None
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "link_stats_json", (DRONE_LINK_MONITOR,), TEXT)
def drone_link_stats_json(_interpreter, args): return pyjson.dumps(args[0].stats(), separators=(",", ":"), sort_keys=True)


def _vision_failure(exc: Exception) -> NativeFailure:
    return NativeFailure(f"vision: {exc}")


def _parse_detections(text: str) -> list[Detection]:
    try: raw = pyjson.loads(text)
    except Exception as exc: raise VisionError(f"invalid detections JSON: {exc}") from exc
    if not isinstance(raw, list): raise VisionError("detections JSON must be an array")
    out=[]
    for item in raw:
        if not isinstance(item, dict): raise VisionError("each detection must be an object")
        box=item.get("box")
        if not isinstance(box, list) or len(box)!=4: raise VisionError("detection box must contain four values")
        out.append(Detection(int(item.get("class_id",0)), Decimal(str(item.get("confidence",0))),
                             *(Decimal(str(v)) for v in box), str(item.get("label",""))))
    return out

@native("vision", "nms_json", (TEXT, DECIMAL), TEXT)
def vision_nms_json(_interpreter, args):
    try: return detections_json(non_max_suppression(_parse_detections(_as_text(args[0])), args[1]))
    except VisionError as exc: raise _vision_failure(exc) from exc

@native("vision", "tracker", (DECIMAL, INT), VISION_TRACKER)
def vision_tracker(interpreter, args):
    try: return interpreter.register_resource(CentroidTracker(args[0], _as_int(args[1])))
    except VisionError as exc: raise _vision_failure(exc) from exc

@native("vision", "track_json", (VISION_TRACKER, TEXT), TEXT)
def vision_track_json(_interpreter, args):
    try: return pyjson.dumps(args[0].update(_parse_detections(_as_text(args[1]))), separators=(",", ":"), sort_keys=True)
    except VisionError as exc: raise _vision_failure(exc) from exc

@native("vision", "camera", (DECIMAL, DECIMAL, DECIMAL, DECIMAL), VISION_CAMERA)
def vision_camera(interpreter, args):
    try: return interpreter.register_resource(PinholeCamera(*args))
    except VisionError as exc: raise _vision_failure(exc) from exc

@native("vision", "pixel_to_bearing", (VISION_CAMERA, DECIMAL, DECIMAL), LIST(DECIMAL))
def vision_pixel_to_bearing(_interpreter, args):
    try: return tuple(args[0].pixel_to_bearing(args[1], args[2]))
    except VisionError as exc: raise _vision_failure(exc) from exc

@native("vision", "aruco_detect_json", (IMAGE, INT), TEXT)
def vision_aruco_detect_json(_interpreter, args):
    try:
        import numpy as np
        rgb = np.asarray(args[0].convert("RGB"))
        bgr = rgb[:, :, ::-1].copy()
        return pyjson.dumps(aruco_detect_bgr(bgr, _as_int(args[1])), separators=(",", ":"), sort_keys=True)
    except (VisionError, Exception) as exc:
        if isinstance(exc, NativeFailure): raise
        raise _vision_failure(exc) from exc


@native("vision", "aruco_pose_json", (IMAGE, INT, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL), TEXT)
def vision_aruco_pose_json(_interpreter, args):
    try:
        import numpy as np
        rgb=np.asarray(args[0].convert("RGB")); bgr=rgb[:,:,::-1].copy()
        return pyjson.dumps(aruco_pose_bgr(bgr,_as_int(args[1]),args[2],args[3],args[4],args[5],args[6]),separators=(",",":"),sort_keys=True)
    except Exception as exc:
        if isinstance(exc, NativeFailure): raise
        raise _vision_failure(exc) from exc

@native("vision", "optical_flow_velocity_json", (IMAGE, IMAGE, DECIMAL, DECIMAL, DECIMAL, DECIMAL), TEXT)
def vision_optical_flow_velocity_json(_interpreter,args):
    try:
        import numpy as np
        a=np.asarray(args[0].convert("RGB"))[:,:,::-1].copy(); b=np.asarray(args[1].convert("RGB"))[:,:,::-1].copy()
        return pyjson.dumps(sparse_optical_flow_velocity_bgr(a,b,args[2],args[3],args[4],args[5]),separators=(",",":"),sort_keys=True)
    except Exception as exc:
        if isinstance(exc, NativeFailure): raise
        raise _vision_failure(exc) from exc

@native("vision", "onnx_load", (TEXT, INT, INT, DECIMAL, BOOL), VISION_DNN)
def vision_onnx_load(interpreter, args):
    path = interpreter.capabilities.require_read(_as_text(args[0]))
    try: return interpreter.register_resource(OpenCVDNNModel(path, _as_int(args[1]), _as_int(args[2]), float(args[3]), bool(args[4])))
    except VisionError as exc: raise _vision_failure(exc) from exc

@native("vision", "onnx_forward_shapes_json", (VISION_DNN, IMAGE), TEXT)
def vision_onnx_forward_shapes_json(_interpreter, args):
    try:
        import numpy as np
        rgb=np.asarray(args[1].convert("RGB")); bgr=rgb[:,:,::-1].copy()
        outputs=args[0].infer(bgr)
        return pyjson.dumps([list(getattr(o, "shape", ())) for o in outputs], separators=(",", ":"))
    except VisionError as exc: raise _vision_failure(exc) from exc

@native("vision", "onnx_forward_json", (VISION_DNN, IMAGE, INT), TEXT)
def vision_onnx_forward_json(_interpreter, args):
    max_values = _as_int(args[2])
    if max_values < 1 or max_values > 100000:
        raise NativeFailure("vision ONNX max_values must be in 1..100000")
    try:
        import numpy as np
        rgb=np.asarray(args[1].convert("RGB")); bgr=rgb[:,:,::-1].copy()
        outputs=args[0].infer(bgr)
        encoded=[]
        remaining=max_values
        for output in outputs:
            arr=np.asarray(output)
            flat=arr.reshape(-1)
            count=min(int(flat.size), remaining)
            encoded.append({"shape":list(arr.shape),"values":[float(v) for v in flat[:count]],"truncated":int(flat.size)>count,"total_values":int(flat.size)})
            remaining -= count
        return pyjson.dumps(encoded,separators=(",",":"),allow_nan=False)
    except (VisionError, ValueError, OverflowError) as exc:
        raise _vision_failure(exc) from exc

@native("net", "set_timeout_ms", (SOCKET, INT), UNIT)
def net_set_timeout_ms(_interpreter, args):
    timeout_ms=_as_int(args[1])
    if timeout_ms < 0: raise NativeFailure("network timeout_ms must be >= 0")
    try: args[0].settimeout(None if timeout_ms == 0 else timeout_ms/1000.0); return None
    except OSError as exc: raise NativeFailure(f"network timeout configuration failed: {exc}") from exc




def _json_decimal(value: object) -> object:
    if isinstance(value, Decimal): return str(value)
    if isinstance(value, (list, tuple)): return [_json_decimal(v) for v in value]
    if isinstance(value, dict): return {str(k): _json_decimal(v) for k,v in value.items()}
    return value

# --- Saga-only advanced machine-control façade ---------------------------------
@native("machine", "lqr_gain_json", (LIST(LIST(DECIMAL)), LIST(LIST(DECIMAL)), LIST(LIST(DECIMAL)), LIST(LIST(DECIMAL)), INT), TEXT)
def machine_lqr_gain_json(_interpreter,args):
    try: return pyjson.dumps(_json_decimal(discrete_lqr_gain(args[0],args[1],args[2],args[3],_as_int(args[4]))),separators=(",",":"))
    except MachineControlError as exc: raise NativeFailure(f"machine LQR: {exc}") from exc

@native("machine", "state_space", (LIST(LIST(DECIMAL)), LIST(LIST(DECIMAL)), LIST(LIST(DECIMAL)), LIST(LIST(DECIMAL)), LIST(DECIMAL), LIST(DECIMAL), LIST(DECIMAL)), MACHINE_STATE_SPACE)
def machine_state_space(interpreter, args):
    try: return interpreter.register_resource(StateSpaceController.create(*args))
    except MachineControlError as exc: raise NativeFailure(f"machine state-space: {exc}") from exc

@native("machine", "state_space_command", (MACHINE_STATE_SPACE, LIST(DECIMAL), LIST(DECIMAL)), LIST(DECIMAL))
def machine_state_space_command(_interpreter,args):
    try: return tuple(args[0].command(args[1], args[2]))
    except MachineControlError as exc: raise NativeFailure(f"machine state-space: {exc}") from exc

@native("machine", "state_space_predict", (MACHINE_STATE_SPACE, LIST(DECIMAL)), LIST(DECIMAL))
def machine_state_space_predict(_interpreter,args):
    try: return tuple(args[0].predict(args[1]))
    except MachineControlError as exc: raise NativeFailure(f"machine state-space: {exc}") from exc

@native("machine", "kalman", (LIST(LIST(DECIMAL)), LIST(LIST(DECIMAL)), LIST(LIST(DECIMAL)), LIST(LIST(DECIMAL)), LIST(DECIMAL), LIST(LIST(DECIMAL))), MACHINE_KALMAN)
def machine_kalman(interpreter,args):
    try: return interpreter.register_resource(LinearKalmanFilter.create(*args))
    except MachineControlError as exc: raise NativeFailure(f"machine kalman: {exc}") from exc

@native("machine", "kalman_predict", (MACHINE_KALMAN,), LIST(DECIMAL))
def machine_kalman_predict(_interpreter,args): return tuple(args[0].predict())

@native("machine", "kalman_update", (MACHINE_KALMAN, LIST(DECIMAL)), LIST(DECIMAL))
def machine_kalman_update(_interpreter,args): return tuple(args[0].update(args[1]))

@native("machine", "motion_group", (LIST(DECIMAL), DECIMAL, DECIMAL, DECIMAL), MACHINE_MOTION_GROUP)
def machine_motion_group(interpreter,args):
    try: return interpreter.register_resource(SynchronizedMotionGroup.create(*args))
    except MachineControlError as exc: raise NativeFailure(f"machine motion group: {exc}") from exc

@native("machine", "motion_group_target", (MACHINE_MOTION_GROUP, LIST(DECIMAL)), UNIT)
def machine_motion_group_target(_interpreter,args): args[0].retarget(args[1]); return None

@native("machine", "motion_group_step_json", (MACHINE_MOTION_GROUP, DECIMAL), TEXT)
def machine_motion_group_step_json(_interpreter,args): return pyjson.dumps(_json_decimal(args[0].step(args[1])),separators=(",",":"),sort_keys=True)

@native("machine", "dh_chain", (LIST(LIST(DECIMAL)),), MACHINE_DH)
def machine_dh_chain(interpreter,args):
    try: return interpreter.register_resource(DHKinematicChain.create(args[0]))
    except MachineControlError as exc: raise NativeFailure(f"machine kinematics: {exc}") from exc

@native("machine", "dh_forward_json", (MACHINE_DH, LIST(DECIMAL)), TEXT)
def machine_dh_forward_json(_interpreter,args): return pyjson.dumps(_json_decimal(args[0].forward(args[1])),separators=(",",":"))

@native("machine", "dh_jacobian_json", (MACHINE_DH, LIST(DECIMAL), DECIMAL), TEXT)
def machine_dh_jacobian_json(_interpreter,args): return pyjson.dumps(_json_decimal(args[0].jacobian(args[1],args[2])),separators=(",",":"))

@native("machine", "dh_resolved_rate", (MACHINE_DH, LIST(DECIMAL), LIST(DECIMAL), DECIMAL, DECIMAL), LIST(DECIMAL))
def machine_dh_resolved_rate(_interpreter,args): return tuple(args[0].resolved_rate(args[1],args[2],args[3],args[4]))

@native("machine", "plc_scan", (DECIMAL,), MACHINE_PLC)
def machine_plc_scan(interpreter,args): return interpreter.register_resource(PLCScanEngine(args[0]))

@native("machine", "plc_sample_json", (MACHINE_PLC, TEXT), UNIT)
def machine_plc_sample_json(_interpreter,args): args[0].sample_json(_as_text(args[1])); return None

@native("machine", "plc_read_json", (MACHINE_PLC, TEXT), TEXT)
def machine_plc_read_json(_interpreter,args): return pyjson.dumps(args[0].read(_as_text(args[1])),separators=(",",":"))

@native("machine", "plc_write_json", (MACHINE_PLC, TEXT, TEXT), UNIT)
def machine_plc_write_json(_interpreter,args): args[0].write(_as_text(args[1]),pyjson.loads(_as_text(args[2]))); return None

@native("machine", "plc_ton", (MACHINE_PLC, TEXT, BOOL, DECIMAL), BOOL)
def machine_plc_ton(_interpreter,args): return bool(args[0].ton(_as_text(args[1]),bool(args[2]),args[3]))

@native("machine", "plc_commit_json", (MACHINE_PLC,), TEXT)
def machine_plc_commit_json(_interpreter,args): return args[0].commit_json()

@native("machine", "canopen_nmt_json", (INT, INT), TEXT)
def machine_canopen_nmt_json(_interpreter,args):
    cob,data=CANopen.nmt(_as_int(args[0]),_as_int(args[1])); return pyjson.dumps({"cob_id":cob,"data_hex":data.hex()},separators=(",",":"))

@native("machine", "canopen_sdo_upload_json", (INT, INT, INT), TEXT)
def machine_canopen_sdo_upload_json(_interpreter,args):
    cob,data=CANopen.sdo_upload(_as_int(args[0]),_as_int(args[1]),_as_int(args[2])); return pyjson.dumps({"cob_id":cob,"data_hex":data.hex()},separators=(",",":"))

@native("machine", "canopen_sdo_download_json", (INT, INT, INT, INT, INT), TEXT)
def machine_canopen_sdo_download_json(_interpreter,args):
    cob,data=CANopen.sdo_download(*[_as_int(v) for v in args]); return pyjson.dumps({"cob_id":cob,"data_hex":data.hex()},separators=(",",":"))

@native("machine", "canopen_pdo_cob_id", (INT, INT, BOOL), INT)
def machine_canopen_pdo_cob_id(_interpreter,args): return CANopen.pdo_cob_id(_as_int(args[0]),_as_int(args[1]),bool(args[2]))

@native("machine", "cia402_controlword", (TEXT,), INT)
def machine_cia402_controlword(_interpreter,args): return CiA402.controlword(_as_text(args[0]))

@native("machine", "cia402_state", (INT,), TEXT)
def machine_cia402_state(_interpreter,args): return CiA402.state(_as_int(args[0]))

@native("machine", "process_image", (INT,), MACHINE_PROCESS_IMAGE)
def machine_process_image(interpreter,args): return interpreter.register_resource(ProcessImage.create(_as_int(args[0])))

@native("machine", "process_read_int", (MACHINE_PROCESS_IMAGE, INT, INT, BOOL), INT)
def machine_process_read_int(_interpreter,args): return args[0].read_int(_as_int(args[1]),_as_int(args[2]),bool(args[3]))

@native("machine", "process_write_int", (MACHINE_PROCESS_IMAGE, INT, INT, BOOL, INT), UNIT)
def machine_process_write_int(_interpreter,args): args[0].write_int(_as_int(args[1]),_as_int(args[2]),bool(args[3]),_as_int(args[4])); return None

@native("machine", "process_read_bit", (MACHINE_PROCESS_IMAGE, INT), BOOL)
def machine_process_read_bit(_interpreter,args): return args[0].read_bit(_as_int(args[1]))

@native("machine", "process_write_bit", (MACHINE_PROCESS_IMAGE, INT, BOOL), UNIT)
def machine_process_write_bit(_interpreter,args): args[0].write_bit(_as_int(args[1]),bool(args[2])); return None

@native("machine", "process_hex", (MACHINE_PROCESS_IMAGE,), TEXT)
def machine_process_hex(_interpreter,args): return args[0].hex()

# --- Advanced drone autonomy ----------------------------------------------------
@native("drone", "visual_servo", (DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL), DRONE_VISUAL_SERVO)
def drone_visual_servo(interpreter,args): return interpreter.register_resource(VisualServoController(*args))

@native("drone", "visual_servo_step_json", (DRONE_VISUAL_SERVO, DECIMAL, DECIMAL, INT, INT, DECIMAL, DECIMAL), TEXT)
def drone_visual_servo_step_json(_interpreter,args):
    try: return pyjson.dumps(_json_decimal(args[0].step(args[1],args[2],_as_int(args[3]),_as_int(args[4]),args[5],args[6])),separators=(",",":"),sort_keys=True)
    except DroneControlError as exc: raise _drone_failure(exc) from exc

@native("drone", "vio", (), DRONE_VIO)
def drone_vio(interpreter,args): return interpreter.register_resource(VisualInertialOdometry())

@native("drone", "vio_imu", (DRONE_VIO, DECIMAL, LIST(DECIMAL), LIST(DECIMAL)), UNIT)
def drone_vio_imu(_interpreter,args): args[0].imu(args[1],args[2],args[3]); return None

@native("drone", "vio_visual_position", (DRONE_VIO, LIST(DECIMAL), DECIMAL), UNIT)
def drone_vio_visual_position(_interpreter,args): args[0].visual_position(args[1],args[2]); return None

@native("drone", "vio_flow_velocity", (DRONE_VIO, LIST(DECIMAL), DECIMAL), UNIT)
def drone_vio_flow_velocity(_interpreter,args): args[0].flow_velocity(args[1],args[2]); return None

@native("drone", "vio_state_json", (DRONE_VIO,), TEXT)
def drone_vio_state_json(_interpreter,args): return pyjson.dumps(args[0].state(),separators=(",",":"),sort_keys=True)

@native("drone", "slam", (INT,), DRONE_SLAM)
def drone_slam(interpreter,args): return interpreter.register_resource(PoseGraphSLAM(_as_int(args[0])))

@native("drone", "slam_add_pose", (DRONE_SLAM, DECIMAL, DECIMAL, DECIMAL), INT)
def drone_slam_add_pose(_interpreter,args): return args[0].add_pose(args[1],args[2],args[3])

@native("drone", "slam_add_constraint", (DRONE_SLAM, INT, INT, DECIMAL, DECIMAL, DECIMAL, DECIMAL), UNIT)
def drone_slam_add_constraint(_interpreter,args): args[0].add_constraint(_as_int(args[1]),_as_int(args[2]),args[3],args[4],args[5],args[6]); return None

@native("drone", "slam_optimize", (DRONE_SLAM, INT, DECIMAL), UNIT)
def drone_slam_optimize(_interpreter,args): args[0].optimize(_as_int(args[1]),args[2]); return None

@native("drone", "slam_json", (DRONE_SLAM,), TEXT)
def drone_slam_json(_interpreter,args): return args[0].json()

@native("drone", "coordinator", (DECIMAL, DECIMAL), DRONE_COORDINATOR)
def drone_coordinator(interpreter,args): return interpreter.register_resource(MultiDroneCoordinator(args[0],args[1]))

@native("drone", "coordinator_update", (DRONE_COORDINATOR, INT, LIST(DECIMAL), LIST(DECIMAL)), UNIT)
def drone_coordinator_update(_interpreter,args): args[0].update(_as_int(args[1]),args[2],args[3]); return None

@native("drone", "coordinator_plan_json", (DRONE_COORDINATOR, TEXT, DECIMAL, DECIMAL), TEXT)
def drone_coordinator_plan_json(_interpreter,args):
    try:
        raw=pyjson.loads(_as_text(args[1])); targets={int(k):[Decimal(str(v)) for v in vals] for k,vals in raw.items()}
        return pyjson.dumps({str(k):[str(v) for v in vals] for k,vals in args[0].plan(targets,args[2],args[3]).items()},separators=(",",":"),sort_keys=True)
    except Exception as exc: raise NativeFailure(f"drone coordinator: {exc}") from exc

@native("drone", "coordinator_conflicts_json", (DRONE_COORDINATOR,), TEXT)
def drone_coordinator_conflicts_json(_interpreter,args): return pyjson.dumps(args[0].conflicts(),separators=(",",":"),sort_keys=True)

@native("drone", "sitl_udp", (TEXT, INT, TEXT, INT, INT, INT, INT, INT, INT), DRONE_SITL)
def drone_sitl_udp(interpreter,args):
    remote_host=_as_text(args[2]); remote_port=_as_int(args[3]); interpreter.capabilities.require_net(remote_host,remote_port)
    try: return interpreter.register_resource(MAVLinkOffboardSession(_as_text(args[0]),_as_int(args[1]),remote_host,remote_port,*[_as_int(v) for v in args[4:8]],timeout_s=max(0.001,_as_int(args[8])/1000.0)))
    except (OSError,DroneControlError) as exc: raise NativeFailure(f"drone SITL: {exc}") from exc

@native("drone", "sitl_close", (DRONE_SITL,), UNIT)
def drone_sitl_close(_interpreter,args): args[0].close(); return None

@native("drone", "sitl_position", (DRONE_SITL, LIST(DECIMAL), LIST(DECIMAL), LIST(DECIMAL), DECIMAL, DECIMAL, INT), UNIT)
def drone_sitl_position(_interpreter,args): args[0].send_position(args[1],args[2],args[3],args[4],args[5],_as_int(args[6])); return None

@native("drone", "sitl_attitude", (DRONE_SITL, LIST(DECIMAL), LIST(DECIMAL), DECIMAL, INT), UNIT)
def drone_sitl_attitude(_interpreter,args): args[0].send_attitude(args[1],args[2],args[3],_as_int(args[4])); return None

@native("drone", "sitl_position_batch_json", (DRONE_SITL, TEXT, INT), INT)
def drone_sitl_position_batch_json(_interpreter,args):
    try:
        points=pyjson.loads(_as_text(args[1])); return args[0].send_position_batch(points,_as_int(args[2]))
    except Exception as exc: raise NativeFailure(f"drone SITL batch: {exc}") from exc

@native("drone", "sitl_timeout_ms", (DRONE_SITL, INT), UNIT)
def drone_sitl_timeout_ms(_interpreter,args): args[0].set_timeout(max(0.001,_as_int(args[1])/1000.0)); return None

@native("drone", "sitl_command", (DRONE_SITL, INT, LIST(DECIMAL), INT), UNIT)
def drone_sitl_command(_interpreter,args): args[0].command_long(_as_int(args[1]),args[2],_as_int(args[3])); return None

@native("drone", "sitl_poll_json", (DRONE_SITL, INT), TEXT)
def drone_sitl_poll_json(_interpreter,args): return pyjson.dumps(args[0].poll(max(0.001,_as_int(args[1])/1000.0)),separators=(",",":"),sort_keys=True)

@native("drone", "sitl_position_json", (DRONE_SITL,), TEXT)
def drone_sitl_position_json(_interpreter,args):
    p=args[0].position(); return "null" if p is None else pyjson.dumps([str(x) for x in p],separators=(",",":"))

# --- Fine-grained lightweight cyclic control ------------------------------------
@native("machine", "actuator_bank", (INT, DECIMAL, DECIMAL, DECIMAL, DECIMAL, DECIMAL), MACHINE_ACTUATOR_BANK)
def machine_actuator_bank(interpreter,args):
    try: return interpreter.register_resource(FineActuatorBank(_as_int(args[0]),args[1],args[2],args[3],args[4],args[5]))
    except MachineControlError as exc: raise NativeFailure(f"machine: {exc}") from exc

@native("machine", "actuator_set", (MACHINE_ACTUATOR_BANK, INT, DECIMAL), UNIT)
def machine_actuator_set(_interpreter,args): args[0].set(_as_int(args[1]),args[2]); return None

@native("machine", "actuator_set_all", (MACHINE_ACTUATOR_BANK, LIST(DECIMAL)), UNIT)
def machine_actuator_set_all(_interpreter,args): args[0].set_all(args[1]); return None

@native("machine", "actuator_zero", (MACHINE_ACTUATOR_BANK,), UNIT)
def machine_actuator_zero(_interpreter,args): args[0].zero(); return None

@native("machine", "actuator_step", (MACHINE_ACTUATOR_BANK, DECIMAL), LIST(DECIMAL))
def machine_actuator_step(_interpreter,args): return tuple(args[0].step(args[1]))

@native("machine", "actuator_state_json", (MACHINE_ACTUATOR_BANK,), TEXT)
def machine_actuator_state_json(_interpreter,args): return args[0].state_json()

@native("machine", "cyclic_clock", (INT,), MACHINE_CYCLIC_CLOCK)
def machine_cyclic_clock(interpreter,args):
    try: return interpreter.register_resource(CyclicClock(_as_int(args[0])))
    except MachineControlError as exc: raise NativeFailure(f"machine: {exc}") from exc

@native("machine", "cycle_wait_us", (MACHINE_CYCLIC_CLOCK,), DECIMAL)
def machine_cycle_wait_us(_interpreter,args): return args[0].wait()

@native("machine", "cycle_wait_due", (MACHINE_CYCLIC_CLOCK,), INT)
def machine_cycle_wait_due(_interpreter,args): return args[0].wait_due()

@native("machine", "cycle_stats_json", (MACHINE_CYCLIC_CLOCK,), TEXT)
def machine_cycle_stats_json(_interpreter,args): return args[0].stats_json()

@native("machine", "cycle_reset", (MACHINE_CYCLIC_CLOCK,), UNIT)
def machine_cycle_reset(_interpreter,args): args[0].reset(); return None

@native("machine", "fast_state_space", (LIST(LIST(DECIMAL)), LIST(LIST(DECIMAL)), LIST(LIST(DECIMAL)), LIST(LIST(DECIMAL)), LIST(DECIMAL), LIST(DECIMAL), LIST(DECIMAL)), MACHINE_FAST_STATE_SPACE)
def machine_fast_state_space(interpreter,args):
    try: return interpreter.register_resource(FastStateSpace.create(*args))
    except MachineControlError as exc: raise NativeFailure(f"machine: {exc}") from exc

@native("machine", "fast_state_command", (MACHINE_FAST_STATE_SPACE, LIST(DECIMAL), LIST(DECIMAL)), LIST(DECIMAL))
def machine_fast_state_command(_interpreter,args): return tuple(args[0].command(args[1],args[2]))

@native("machine", "fast_state_predict", (MACHINE_FAST_STATE_SPACE, LIST(DECIMAL)), LIST(DECIMAL))
def machine_fast_state_predict(_interpreter,args): return tuple(args[0].predict(args[1]))

# --- Object detection -----------------------------------------------------------
@native("vision", "onnx_detector_load", (TEXT, INT, INT, DECIMAL, BOOL), VISION_DIRECT_DETECTOR)
def vision_onnx_detector_load(interpreter,args):
    path=interpreter.capabilities.require_read(_as_text(args[0]))
    try: return interpreter.register_resource(OpenCVDirectObjectDetector(path,_as_int(args[1]),_as_int(args[2]),args[3],bool(args[4])))
    except VisionError as exc: raise _vision_failure(exc) from exc

@native("vision", "onnx_detect_json", (VISION_DIRECT_DETECTOR, IMAGE, TEXT), TEXT)
def vision_onnx_detect_json(_interpreter,args):
    try:
        import numpy as np
        rgb=np.asarray(args[1].convert("RGB")); bgr=rgb[:,:,::-1].copy(); labels=pyjson.loads(_as_text(args[2])) if _as_text(args[2]).strip() else None
        if labels is not None and not isinstance(labels,list): raise VisionError("labels JSON must be an array")
        return detections_json(args[0].detect(bgr,[str(x) for x in labels] if labels else None))
    except (VisionError,ValueError) as exc: raise _vision_failure(exc) from exc

@native("vision", "yolox_load", (TEXT, DECIMAL, DECIMAL, INT, INT), VISION_DETECTOR)
def vision_yolox_load(interpreter,args):
    path=interpreter.capabilities.require_read(_as_text(args[0]))
    try: return interpreter.register_resource(OpenCVYOLOXDetector(path,args[1],args[2],_as_int(args[3]),_as_int(args[4])))
    except VisionError as exc: raise _vision_failure(exc) from exc

@native("vision", "yolox_detect_json", (VISION_DETECTOR, IMAGE, TEXT), TEXT)
def vision_yolox_detect_json(_interpreter,args):
    try:
        import numpy as np
        rgb=np.asarray(args[1].convert("RGB")); bgr=rgb[:,:,::-1].copy(); labels=pyjson.loads(_as_text(args[2])) if _as_text(args[2]).strip() else None
        if labels is not None and not isinstance(labels,list): raise VisionError("labels JSON must be an array")
        return detections_json(args[0].detect(bgr,[str(x) for x in labels] if labels else None))
    except (VisionError,ValueError) as exc: raise _vision_failure(exc) from exc

# --- GStreamer/WebRTC video -----------------------------------------------------
@native("media", "gstreamer_available", (), BOOL)
def media_gstreamer_available(_interpreter,args): return gstreamer_available()

@native("media", "gstreamer_webrtc_available", (), BOOL)
def media_gstreamer_webrtc_available(_interpreter,args): return gstreamer_webrtc_available()

@native("media", "gstreamer_backend_json", (), TEXT)
def media_gstreamer_backend_json(_interpreter,args): return gstreamer_backend_json()

@native("media", "gstreamer_probe_json", (), TEXT)
def media_gstreamer_probe_json(_interpreter,args):
    try: return pyjson.dumps(gstreamer_execute_probe(),separators=(",",":"),sort_keys=True)
    except MediaStreamingError as exc: raise NativeFailure(f"media: {exc}") from exc

@native("media", "gstreamer", (), MEDIA_GSTREAMER)
def media_gstreamer(interpreter,args): return interpreter.register_resource(GStreamerRTPVideo())

@native("media", "gst_test_rtp_start", (MEDIA_GSTREAMER, TEXT, INT, INT), UNIT)
def media_gst_test_rtp_start(interpreter,args):
    interpreter.capabilities.require_net(_as_text(args[1]),_as_int(args[2]))
    try: args[0].start_test_sender(_as_text(args[1]),_as_int(args[2]),_as_int(args[3])); return None
    except MediaStreamingError as exc: raise NativeFailure(f"media: {exc}") from exc

@native("media", "gst_camera_rtp_start", (MEDIA_GSTREAMER, TEXT, TEXT, INT, INT, INT, INT, INT), UNIT)
def media_gst_camera_rtp_start(interpreter,args):
    interpreter.capabilities.require_process(); interpreter.capabilities.require_device(); interpreter.capabilities.require_net(_as_text(args[2]),_as_int(args[3]))
    try: args[0].start_camera_sender(_as_text(args[1]),_as_text(args[2]),_as_int(args[3]),_as_int(args[4]),_as_int(args[5]),_as_int(args[6]),_as_int(args[7])); return None
    except MediaStreamingError as exc: raise NativeFailure(f"media: {exc}") from exc

@native("media", "gst_rtp_receive_start", (MEDIA_GSTREAMER, INT), UNIT)
def media_gst_rtp_receive_start(interpreter,args):
    interpreter.capabilities.require_process()
    try: args[0].start_receiver(_as_int(args[1])); return None
    except MediaStreamingError as exc: raise NativeFailure(f"media: {exc}") from exc

@native("media", "gst_stop", (MEDIA_GSTREAMER,), UNIT)
def media_gst_stop(_interpreter,args): args[0].stop(); return None

@native("media", "gst_status_json", (MEDIA_GSTREAMER,), TEXT)
def media_gst_status_json(_interpreter,args): return args[0].status_json()

@native("media", "webrtc_browser_recipe_json", (), TEXT)
def media_webrtc_browser_recipe_json(_interpreter,args): return webrtc_browser_recipe_json()


@native("spark", "session", (TEXT,), SPARK)
def spark_session(interpreter, args):
    interpreter.capabilities.require_process()
    try: from pyspark.sql import SparkSession
    except ImportError as exc: raise NativeFailure("Spark連携には pyspark が必要です: pip install 'saga-language[bigdata]'") from exc
    try:
        return interpreter.register_resource(SparkSession.builder.appName(_as_text(args[0])).getOrCreate())
    except Exception as exc:
        raise NativeFailure(f"Sparkセッションを開始できません: {exc}") from exc


@native("spark", "local_session", (TEXT, INT), SPARK)
def spark_local_session(interpreter, args):
    interpreter.capabilities.require_process()
    try:
        from pyspark.sql import SparkSession
    except ImportError as exc:
        raise NativeFailure("Spark連携には pyspark が必要です: pip install 'saga-language[bigdata]'") from exc
    workers = _as_int(args[1])
    if workers <= 0 or workers > 256:
        raise NativeFailure("spark.local_session のworker数は1..256にしてください")
    try:
        session = SparkSession.builder.master(f"local[{workers}]").appName(_as_text(args[0])).getOrCreate()
        return interpreter.register_resource(session)
    except Exception as exc:
        raise NativeFailure(f"Sparkローカルセッションを開始できません: {exc}") from exc


@native("spark", "sql", (SPARK, TEXT), LIST(MAP(TEXT, ANY)))
def spark_sql(_interpreter, args):
    try:
        rows = args[0].sql(_as_text(args[1])).collect()
        return tuple({str(key): _freeze_external(value) for key, value in row.asDict(recursive=True).items()} for row in rows)
    except Exception as exc:
        raise NativeFailure(f"Spark SQL実行に失敗しました: {exc}") from exc


@native("spark", "range_count", (SPARK, INT, INT), INT)
def spark_range_count(_interpreter, args):
    start, end = _as_int(args[1]), _as_int(args[2])
    try:
        return int(args[0].range(start, end).count())
    except Exception as exc:
        raise NativeFailure(f"Spark range/count実行に失敗しました: {exc}") from exc


@native("spark", "stop", (SPARK,), UNIT)
def spark_stop(_interpreter, args): args[0].stop(); return None


# ---------- Reflection, annotations and plugins ----------

@native("reflect", "type_name", (ANY,), TEXT)
def reflect_type_name(interpreter, args): return interpreter.runtime_type_name(args[0])


@native("reflect", "fields", (ANY,), LIST(TEXT))
def reflect_fields(interpreter, args): return tuple(interpreter.reflect_fields(args[0]))


@native("reflect", "methods", (ANY,), LIST(TEXT))
def reflect_methods(interpreter, args): return tuple(interpreter.reflect_methods(args[0]))


@native("reflect", "annotations", (ANY,), MAP(TEXT, LIST(ANY)))
def reflect_annotations(interpreter, args): return interpreter.reflect_annotations(args[0])


@native("reflect", "get", (ANY, TEXT), ANY)
def reflect_get(interpreter, args): return interpreter.reflect_get(args[0], _as_text(args[1]))


@native("reflect", "class_of", (ANY,), CLASS_VALUE)
def reflect_class_of(interpreter, args): return interpreter.class_of(args[0])


from ..plugin_runtime import IsolatedPluginHandle, PluginSandboxError, load_plugin, call_plugin


@native("plugin", "load", (TEXT,), PLUGIN)
def plugin_load(interpreter, args):
    path = interpreter.capabilities.require_plugin(_as_text(args[0]))
    try:
        return load_plugin(path)
    except (OSError, UnicodeError, PluginSandboxError) as exc:
        raise NativeFailure(f"隔離プラグインを読み込めません: {exc}", "SAGA-R103") from exc


@native("plugin", "call", (PLUGIN, TEXT), ANY, variadic=True, min_args=2)
def plugin_call(_interpreter, args):
    handle = args[0]
    if not isinstance(handle, IsolatedPluginHandle):
        raise NativeFailure("plugin.call の1つ目は隔離プラグインハンドルである必要があります")
    name = _as_text(args[1])
    try:
        return call_plugin(handle, name, list(args[2:]))
    except PluginSandboxError as exc:
        raise NativeFailure(f"隔離プラグイン関数が失敗しました: {exc}") from exc


# Ensure modules that may only have optional functions are discoverable.
for _name in (
    "console", "io", "time", "json", "data", "http", "net", "websocket", "db", "orm",
    "docdb", "task", "ui", "crypto", "science", "ml", "image", "video",
    "game", "process", "cloud", "gpio", "machine", "spark", "reflect", "plugin", "regex", "system", "security",
):
    _module(_name)
