import asyncio
import sys
import threading
from datetime import timedelta
from typing import Optional

from .gps import GpsFix


PYWINRT_INSTALL_HINT = (
    'Windows location support needs PyWinRT. Run: pip install '
    '"winrt-Windows.Devices.Geolocation[all]>=3.2.1"'
)


class WindowsLocationError(RuntimeError):
    """Raised when Windows Location Services cannot provide a fix."""


class WindowsLocationReader:
    def __init__(
        self,
        poll_interval_s: float = 1.0,
        maximum_age_s: float = 10.0,
        timeout_s: float = 5.0,
        desired_accuracy_m: Optional[int] = 50,
        startup_timeout_s: float = 30.0,
    ) -> None:
        if sys.platform != "win32":
            raise WindowsLocationError("Windows location is only available on Windows.")
        if sys.version_info < (3, 9):
            raise WindowsLocationError(
                "Windows location support needs Python 3.9 or newer."
            )

        (
            self._Geolocator,
            self._GeolocationAccessStatus,
            self._PositionAccuracy,
        ) = load_geolocation_api()

        self.poll_interval_s = max(poll_interval_s, 0.2)
        self.maximum_age_s = max(maximum_age_s, 0.0)
        self.timeout_s = max(timeout_s, 1.0)
        self.desired_accuracy_m = desired_accuracy_m
        self.startup_timeout_s = max(startup_timeout_s, self.timeout_s)
        self._latest = GpsFix()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error: Optional[WindowsLocationError] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return

        self._thread = threading.Thread(
            target=self._run,
            name="windows-location-reader",
            daemon=True,
        )
        self._thread.start()

        if not self._ready.wait(timeout=self.startup_timeout_s):
            raise WindowsLocationError(
                "Timed out waiting for Windows location. Check Windows location "
                "permissions and try again."
            )

        with self._lock:
            error = self._error
        if error is not None:
            raise error

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.timeout_s + 2.0)

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
        try:
            asyncio.run(self._run_async())
        except WindowsLocationError as exc:
            self._set_startup_error(exc)
        except Exception as exc:  # pragma: no cover - depends on Windows runtime
            self._set_startup_error(
                WindowsLocationError(f"Could not read Windows location: {exc}")
            )

    async def _run_async(self) -> None:
        locator = await self._create_locator()

        while not self._stop.is_set():
            try:
                self._set_latest(await self._read_once(locator))
                self._ready.set()
            except Exception as exc:
                if not self._ready.is_set():
                    raise WindowsLocationError(
                        f"Could not read Windows location: {exc}"
                    ) from exc
            await asyncio.sleep(self.poll_interval_s)

    async def _create_locator(self):
        status = await self._Geolocator.request_access_async()
        if status != self._GeolocationAccessStatus.ALLOWED:
            raise WindowsLocationError(
                "Windows location access was denied. Enable Location services and "
                "Let desktop apps access your location in Windows Settings."
            )

        locator = self._Geolocator()
        locator.desired_accuracy = self._PositionAccuracy.HIGH
        locator.report_interval = int(self.poll_interval_s * 1000)
        if self.desired_accuracy_m is not None:
            locator.desired_accuracy_in_meters = int(self.desired_accuracy_m)
        return locator

    async def _read_once(self, locator) -> GpsFix:
        position = await locator.get_geoposition_async_with_age_and_timeout(
            timedelta(seconds=self.maximum_age_s),
            timedelta(seconds=self.timeout_s),
        )
        return gps_fix_from_geoposition(position)

    def _set_latest(self, fix: GpsFix) -> None:
        with self._lock:
            self._latest = fix
            self._error = None

    def _set_startup_error(self, error: WindowsLocationError) -> None:
        with self._lock:
            self._error = error
        self._ready.set()


def load_geolocation_api():
    try:
        from winrt.windows.devices.geolocation import (  # type: ignore
            GeolocationAccessStatus,
            Geolocator,
            PositionAccuracy,
        )
    except ImportError as exc:  # pragma: no cover - exercised without dependency
        raise WindowsLocationError(PYWINRT_INSTALL_HINT) from exc

    return Geolocator, GeolocationAccessStatus, PositionAccuracy


def gps_fix_from_geoposition(geoposition) -> GpsFix:
    coordinate = getattr(geoposition, "coordinate", None)
    if coordinate is None:
        return GpsFix(valid=False)

    latitude = _optional_float(getattr(coordinate, "latitude", None))
    longitude = _optional_float(getattr(coordinate, "longitude", None))
    if latitude is None or longitude is None:
        point = getattr(coordinate, "point", None)
        position = getattr(point, "position", None)
        latitude = _optional_float(getattr(position, "latitude", None))
        longitude = _optional_float(getattr(position, "longitude", None))

    speed_m_s = _optional_float(getattr(coordinate, "speed", None))
    timestamp = getattr(coordinate, "timestamp", None)
    timestamp_text = timestamp.isoformat() if hasattr(timestamp, "isoformat") else None

    return GpsFix(
        latitude=latitude,
        longitude=longitude,
        speed_kph=speed_m_s * 3.6 if speed_m_s is not None else None,
        timestamp_utc=timestamp_text,
        valid=latitude is not None and longitude is not None,
    )


def _optional_float(value) -> Optional[float]:
    if value is None:
        return None
    return float(value)
