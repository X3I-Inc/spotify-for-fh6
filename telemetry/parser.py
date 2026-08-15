"""Parses raw Forza Horizon 6 UDP telemetry packets into structured data.

FH6 broadcasts a 324-byte "Horizon" packet: the same 232-byte Sled struct as
Forza Motorsport, followed by a 12-byte Horizon-only block (car_group,
smashable_vel_diff, smashable_mass) not present in FM7, then the same 79-byte
Dash tail (position/speed/power/torque/tire temps/lap info/inputs) shifted 12
bytes later, plus a 1-byte trailing field. Confirmed against a real FH6 capture
and the official Data Out documentation — see docs/DECISIONS.md.
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 20127

# (field_name, struct_format_char), in wire order. '<' (little-endian, no padding)
# is prepended when building the final struct format string.
FIELD_SPEC: list[tuple[str, str]] = [
    # --- Sled (base physics) ---
    ("is_race_on", "i"),
    ("timestamp_ms", "I"),
    ("engine_max_rpm", "f"),
    ("engine_idle_rpm", "f"),
    ("current_engine_rpm", "f"),
    ("acceleration_x", "f"),
    ("acceleration_y", "f"),
    ("acceleration_z", "f"),
    ("velocity_x", "f"),
    ("velocity_y", "f"),
    ("velocity_z", "f"),
    ("angular_velocity_x", "f"),
    ("angular_velocity_y", "f"),
    ("angular_velocity_z", "f"),
    ("yaw", "f"),
    ("pitch", "f"),
    ("roll", "f"),
    ("norm_suspension_travel_fl", "f"),
    ("norm_suspension_travel_fr", "f"),
    ("norm_suspension_travel_rl", "f"),
    ("norm_suspension_travel_rr", "f"),
    ("tire_slip_ratio_fl", "f"),
    ("tire_slip_ratio_fr", "f"),
    ("tire_slip_ratio_rl", "f"),
    ("tire_slip_ratio_rr", "f"),
    ("wheel_rotation_speed_fl", "f"),
    ("wheel_rotation_speed_fr", "f"),
    ("wheel_rotation_speed_rl", "f"),
    ("wheel_rotation_speed_rr", "f"),
    ("wheel_on_rumble_strip_fl", "i"),
    ("wheel_on_rumble_strip_fr", "i"),
    ("wheel_on_rumble_strip_rl", "i"),
    ("wheel_on_rumble_strip_rr", "i"),
    ("wheel_in_puddle_depth_fl", "f"),
    ("wheel_in_puddle_depth_fr", "f"),
    ("wheel_in_puddle_depth_rl", "f"),
    ("wheel_in_puddle_depth_rr", "f"),
    ("surface_rumble_fl", "f"),
    ("surface_rumble_fr", "f"),
    ("surface_rumble_rl", "f"),
    ("surface_rumble_rr", "f"),
    ("tire_slip_angle_fl", "f"),
    ("tire_slip_angle_fr", "f"),
    ("tire_slip_angle_rl", "f"),
    ("tire_slip_angle_rr", "f"),
    ("tire_combined_slip_fl", "f"),
    ("tire_combined_slip_fr", "f"),
    ("tire_combined_slip_rl", "f"),
    ("tire_combined_slip_rr", "f"),
    ("suspension_travel_meters_fl", "f"),
    ("suspension_travel_meters_fr", "f"),
    ("suspension_travel_meters_rl", "f"),
    ("suspension_travel_meters_rr", "f"),
    ("car_ordinal", "i"),
    ("car_class", "i"),
    ("car_performance_index", "i"),
    ("drivetrain_type", "i"),
    ("num_cylinders", "i"),
    # --- Horizon block (FH6/FH5/FH4 only, not present in FM7's Dash) ---
    ("car_group", "i"),
    ("smashable_vel_diff", "f"),
    ("smashable_mass", "f"),
    # --- Dash tail ---
    ("position_x", "f"),
    ("position_y", "f"),
    ("position_z", "f"),
    ("speed", "f"),
    ("power", "f"),
    ("torque", "f"),
    ("tire_temp_fl", "f"),
    ("tire_temp_fr", "f"),
    ("tire_temp_rl", "f"),
    ("tire_temp_rr", "f"),
    ("boost", "f"),
    ("fuel", "f"),
    ("distance_traveled", "f"),
    ("best_lap", "f"),
    ("last_lap", "f"),
    ("current_lap", "f"),
    ("current_race_time", "f"),
    ("lap_number", "H"),
    ("race_position", "B"),
    ("accel", "B"),
    ("brake", "B"),
    ("clutch", "B"),
    ("hand_brake", "B"),
    ("gear", "B"),
    ("steer", "b"),
    ("normalized_driving_line", "b"),
    ("normalized_ai_brake_difference", "b"),
    ("_trailing", "B"),
]

FIELD_NAMES = tuple(name for name, _ in FIELD_SPEC)
STRUCT_FORMAT = "<" + "".join(fmt for _, fmt in FIELD_SPEC)
SLED_DASH_STRUCT = struct.Struct(STRUCT_FORMAT)
PACKET_SIZE = SLED_DASH_STRUCT.size  # 324 bytes for FH6's Sled+Horizon+Dash layout


class TelemetryParseError(ValueError):
    """Raised when a UDP payload can't be parsed as a Sled+Dash telemetry packet."""


@dataclass
class TelemetryPacket:
    is_race_on: int
    timestamp_ms: int
    engine_max_rpm: float
    engine_idle_rpm: float
    current_engine_rpm: float
    acceleration_x: float
    acceleration_y: float
    acceleration_z: float
    velocity_x: float
    velocity_y: float
    velocity_z: float
    angular_velocity_x: float
    angular_velocity_y: float
    angular_velocity_z: float
    yaw: float
    pitch: float
    roll: float
    norm_suspension_travel_fl: float
    norm_suspension_travel_fr: float
    norm_suspension_travel_rl: float
    norm_suspension_travel_rr: float
    tire_slip_ratio_fl: float
    tire_slip_ratio_fr: float
    tire_slip_ratio_rl: float
    tire_slip_ratio_rr: float
    wheel_rotation_speed_fl: float
    wheel_rotation_speed_fr: float
    wheel_rotation_speed_rl: float
    wheel_rotation_speed_rr: float
    wheel_on_rumble_strip_fl: int
    wheel_on_rumble_strip_fr: int
    wheel_on_rumble_strip_rl: int
    wheel_on_rumble_strip_rr: int
    wheel_in_puddle_depth_fl: float
    wheel_in_puddle_depth_fr: float
    wheel_in_puddle_depth_rl: float
    wheel_in_puddle_depth_rr: float
    surface_rumble_fl: float
    surface_rumble_fr: float
    surface_rumble_rl: float
    surface_rumble_rr: float
    tire_slip_angle_fl: float
    tire_slip_angle_fr: float
    tire_slip_angle_rl: float
    tire_slip_angle_rr: float
    tire_combined_slip_fl: float
    tire_combined_slip_fr: float
    tire_combined_slip_rl: float
    tire_combined_slip_rr: float
    suspension_travel_meters_fl: float
    suspension_travel_meters_fr: float
    suspension_travel_meters_rl: float
    suspension_travel_meters_rr: float
    car_ordinal: int
    car_class: int
    car_performance_index: int
    drivetrain_type: int
    num_cylinders: int
    car_group: int
    smashable_vel_diff: float
    smashable_mass: float
    position_x: float
    position_y: float
    position_z: float
    speed: float
    power: float
    torque: float
    tire_temp_fl: float
    tire_temp_fr: float
    tire_temp_rl: float
    tire_temp_rr: float
    boost: float
    fuel: float
    distance_traveled: float
    best_lap: float
    last_lap: float
    current_lap: float
    current_race_time: float
    lap_number: int
    race_position: int
    accel: int
    brake: int
    clutch: int
    hand_brake: int
    gear: int
    steer: int
    normalized_driving_line: int
    normalized_ai_brake_difference: int
    _trailing: int

    @property
    def state(self) -> str:
        return "driving" if self.is_race_on == 1 else "paused_or_menu"


def parse_packet(data: bytes) -> TelemetryPacket:
    """Parse a raw UDP payload into a TelemetryPacket.

    Raises TelemetryParseError on payloads shorter than the expected packet size.
    Extra trailing bytes (e.g. from a future/larger FH6 packet variant) are ignored.
    """
    if len(data) < PACKET_SIZE:
        raise TelemetryParseError(
            f"packet too short: got {len(data)} bytes, need at least {PACKET_SIZE}"
        )
    values = SLED_DASH_STRUCT.unpack_from(data, 0)
    return TelemetryPacket(**dict(zip(FIELD_NAMES, values)))


class TelemetryListener:
    """Listens for FH6 UDP telemetry and calls `callback` with each parsed packet.

    Usage:
        with TelemetryListener(callback=on_packet) as listener:
            ...  # runs until stop() / context exit

    Malformed or short packets are logged and skipped rather than raised, so a
    single bad packet can't take down the listener loop.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        callback: Optional[Callable[[TelemetryPacket], None]] = None,
        socket_timeout: float = 0.5,
    ) -> None:
        self.host = host
        self.port = port
        self.callback = callback
        self._socket_timeout = socket_timeout
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("TelemetryListener already started")
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(self._socket_timeout)
        self._sock.bind((self.host, self.port))
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        logger.info("TelemetryListener started on %s:%d", self.host, self.port)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._socket_timeout + 1.0)
            self._thread = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        logger.info("TelemetryListener stopped")

    def _listen_loop(self) -> None:
        assert self._sock is not None
        while not self._stop_event.is_set():
            try:
                data, _addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                # Socket was closed out from under us during stop().
                break

            try:
                packet = parse_packet(data)
            except TelemetryParseError as exc:
                logger.warning("Skipping malformed telemetry packet: %s", exc)
                continue

            if self.callback is not None:
                try:
                    self.callback(packet)
                except Exception:
                    logger.exception("Telemetry callback raised an exception")

    def __enter__(self) -> "TelemetryListener":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()
