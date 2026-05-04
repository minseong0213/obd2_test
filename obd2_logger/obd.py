import re
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Set

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - handled at runtime for nicer CLI errors
    serial = None
    list_ports = None


HEX_RE = re.compile(r"[0-9A-Fa-f]{2}")


class ObdError(RuntimeError):
    """Raised when the OBD serial connection cannot be used."""


@dataclass
class ObdSnapshot:
    engine_load_pct: Optional[float] = None
    rpm: Optional[float] = None
    speed_kph: Optional[float] = None
    coolant_c: Optional[float] = None
    maf_g_s: Optional[float] = None
    map_kpa: Optional[float] = None
    baro_kpa: Optional[float] = None
    throttle_pct: Optional[float] = None
    fuel_rate_l_h: Optional[float] = None


def require_serial() -> None:
    if serial is None:
        raise ObdError("pyserial is not installed. Run: pip install -r requirements.txt")


def available_ports() -> List[str]:
    require_serial()
    return [
        f"{port.device} - {port.description}"
        for port in list_ports.comports()
    ]


def _bytes_from_response(response: str) -> List[int]:
    return [int(value, 16) for value in HEX_RE.findall(response)]


def parse_obd_payload(response: str, pid: int) -> Optional[List[int]]:
    """Return data bytes after a standard 0x41 PID response."""
    values = _bytes_from_response(response)
    for index in range(len(values) - 2):
        if values[index] == 0x41 and values[index + 1] == pid:
            return values[index + 2:]
    return None


def decode_supported_pids(base_pid: int, payload: List[int]) -> Set[int]:
    if len(payload) < 4:
        return set()

    mask = (
        (payload[0] << 24)
        | (payload[1] << 16)
        | (payload[2] << 8)
        | payload[3]
    )
    supported: Set[int] = set()
    for bit_index in range(32):
        if mask & (1 << (31 - bit_index)):
            supported.add(base_pid + bit_index + 1)
    return supported


def _u16(payload: List[int]) -> int:
    return (payload[0] << 8) + payload[1]


def decode_rpm(payload: List[int]) -> Optional[float]:
    if len(payload) < 2:
        return None
    return _u16(payload) / 4.0


def decode_speed(payload: List[int]) -> Optional[float]:
    return float(payload[0]) if payload else None


def decode_temp(payload: List[int]) -> Optional[float]:
    return float(payload[0] - 40) if payload else None


def decode_percent(payload: List[int]) -> Optional[float]:
    return payload[0] * 100.0 / 255.0 if payload else None


def decode_maf(payload: List[int]) -> Optional[float]:
    if len(payload) < 2:
        return None
    return _u16(payload) / 100.0


def decode_pressure(payload: List[int]) -> Optional[float]:
    return float(payload[0]) if payload else None


def decode_fuel_rate(payload: List[int]) -> Optional[float]:
    if len(payload) < 2:
        return None
    return _u16(payload) * 0.05


PID_DECODERS: Dict[int, Callable[[List[int]], Optional[float]]] = {
    0x04: decode_percent,
    0x05: decode_temp,
    0x0B: decode_pressure,
    0x0C: decode_rpm,
    0x0D: decode_speed,
    0x10: decode_maf,
    0x11: decode_percent,
    0x33: decode_pressure,
    0x5E: decode_fuel_rate,
}


class ObdSerial:
    def __init__(
        self,
        port: str,
        baudrate: int = 38400,
        timeout: float = 0.2,
        command_timeout: float = 2.0,
    ) -> None:
        require_serial()
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.command_timeout = command_timeout
        self._serial = None

    def __enter__(self) -> "ObdSerial":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        if self._serial is not None:
            return
        self._serial = serial.Serial(
            self.port,
            self.baudrate,
            timeout=self.timeout,
            write_timeout=2.0,
        )

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def send(self, command: str, timeout: Optional[float] = None) -> str:
        if self._serial is None:
            raise ObdError("OBD port is not open")

        deadline = time.monotonic() + (timeout or self.command_timeout)
        self._serial.reset_input_buffer()
        self._serial.write((command.strip() + "\r").encode("ascii"))

        data = b""
        while time.monotonic() < deadline:
            chunk = self._serial.read(self._serial.in_waiting or 1)
            if chunk:
                data += chunk
                if b">" in data:
                    break
        return data.decode("ascii", errors="ignore").replace(">", "").strip()

    def initialize(self) -> Dict[str, str]:
        responses: Dict[str, str] = {}
        for command in ("ATZ", "ATE0", "ATL0", "ATS0", "ATH0", "ATSP0"):
            responses[command] = self.send(command, timeout=3.0)
            time.sleep(0.2)
        return responses

    def query_pid(self, pid: int) -> Optional[List[int]]:
        response = self.send(f"01{pid:02X}")
        return parse_obd_payload(response, pid)

    def supported_pids(self) -> Optional[Set[int]]:
        supported: Set[int] = set()
        for base_pid in (0x00, 0x20, 0x40, 0x60):
            payload = self.query_pid(base_pid)
            if not payload:
                continue
            supported.update(decode_supported_pids(base_pid, payload))
        return supported or None

    def read_snapshot(self, supported: Optional[Set[int]] = None) -> ObdSnapshot:
        def read_value(pid: int) -> Optional[float]:
            if supported is not None and pid not in supported:
                return None
            payload = self.query_pid(pid)
            if not payload:
                return None
            return PID_DECODERS[pid](payload)

        return ObdSnapshot(
            engine_load_pct=read_value(0x04),
            rpm=read_value(0x0C),
            speed_kph=read_value(0x0D),
            coolant_c=read_value(0x05),
            maf_g_s=read_value(0x10),
            map_kpa=read_value(0x0B),
            baro_kpa=read_value(0x33),
            throttle_pct=read_value(0x11),
            fuel_rate_l_h=read_value(0x5E),
        )


def format_supported_pids(supported: Optional[Iterable[int]]) -> str:
    if supported is None:
        return "unknown"
    return ", ".join(f"01{pid:02X}" for pid in sorted(supported))
