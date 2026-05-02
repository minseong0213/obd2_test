import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

try:
    import pynmea2
    import serial
except ImportError:  # pragma: no cover - handled at runtime
    pynmea2 = None
    serial = None


@dataclass
class GpsFix:
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    speed_kph: Optional[float] = None
    timestamp_utc: Optional[str] = None
    valid: bool = False


class GpsReader:
    def __init__(self, port: str, baudrate: int = 9600) -> None:
        if serial is None or pynmea2 is None:
            raise RuntimeError("GPS support needs pyserial and pynmea2. Run: pip install -r requirements.txt")
        self.port = port
        self.baudrate = baudrate
        self._latest = GpsFix()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="gps-reader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def latest(self) -> GpsFix:
        with self._lock:
            return GpsFix(
                latitude=self._latest.latitude,
                longitude=self._latest.longitude,
                speed_kph=self._latest.speed_kph,
                timestamp_utc=self._latest.timestamp_utc,
                valid=self._latest.valid,
            )

    def _run(self) -> None:
        with serial.Serial(self.port, self.baudrate, timeout=1.0) as gps:
            while not self._stop.is_set():
                line = gps.readline().decode("ascii", errors="ignore").strip()
                if not line.startswith("$"):
                    continue
                try:
                    message = pynmea2.parse(line)
                except pynmea2.ParseError:
                    continue

                fix = self._fix_from_message(message)
                if fix is None:
                    continue
                with self._lock:
                    self._latest = fix

    def _fix_from_message(self, message) -> Optional[GpsFix]:
        sentence_type = getattr(message, "sentence_type", "")

        if sentence_type == "RMC":
            valid = getattr(message, "status", "") == "A"
            if not valid:
                return GpsFix(valid=False)
            speed_knots = float(getattr(message, "spd_over_grnd", 0) or 0)
            timestamp = self._timestamp_from_message(message)
            return GpsFix(
                latitude=float(message.latitude),
                longitude=float(message.longitude),
                speed_kph=speed_knots * 1.852,
                timestamp_utc=timestamp,
                valid=True,
            )

        if sentence_type == "GGA":
            quality = int(getattr(message, "gps_qual", 0) or 0)
            if quality <= 0:
                return GpsFix(valid=False)
            return GpsFix(
                latitude=float(message.latitude),
                longitude=float(message.longitude),
                valid=True,
            )

        return None

    @staticmethod
    def _timestamp_from_message(message) -> Optional[str]:
        datestamp = getattr(message, "datestamp", None)
        timestamp = getattr(message, "timestamp", None)
        if datestamp is None or timestamp is None:
            return None
        return datetime.combine(datestamp, timestamp).isoformat()
