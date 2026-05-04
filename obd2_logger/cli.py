import argparse
import math
import os
import sys
import time
from datetime import datetime
from typing import Callable, Optional

from .calculations import derive_values, safe_round
from .csv_logger import CsvLogger, format_csv_header, format_csv_row
from .gps import GpsFix, GpsReader
from .obd import ObdError, ObdSerial, ObdSnapshot, available_ports, format_supported_pids
from .state import RollingState
from .windows_location import WindowsLocationError, WindowsLocationReader


SnapshotProvider = Callable[[], ObdSnapshot]
GpsProvider = Callable[[], GpsFix]
MessageCallback = Callable[[str], None]
RowCallback = Callable[[dict], None]
StopPredicate = Callable[[], bool]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Log ELM327 OBD-II and optional USB GPS data to CSV.",
    )
    parser.add_argument("--obd-port", help="ELM327 serial port, for example COM5")
    parser.add_argument("--baud", type=int, default=38400, help="ELM327 baud rate")
    parser.add_argument("--gps-port", help="Optional GPS NMEA serial port, for example COM7")
    parser.add_argument("--gps-baud", type=int, default=9600, help="GPS baud rate")
    parser.add_argument(
        "--use-windows-location",
        action="store_true",
        help="Use Windows Location Services instead of a serial GPS port",
    )
    parser.add_argument(
        "--windows-location-timeout",
        type=float,
        default=5.0,
        help="Windows location read timeout in seconds",
    )
    parser.add_argument(
        "--windows-location-maximum-age",
        type=float,
        default=10.0,
        help="Maximum age in seconds for cached Windows location fixes",
    )
    parser.add_argument(
        "--windows-location-accuracy-m",
        type=int,
        default=50,
        help="Requested Windows location accuracy in meters",
    )
    parser.add_argument("--interval", type=float, default=1.0, help="Log interval in seconds")
    parser.add_argument("--out-dir", default="logs", help="CSV output directory")
    parser.add_argument("--state-file", default="state.json", help="Rolling totals JSON path")
    parser.add_argument("--fuel-price", type=float, default=0.0, help="Fuel price per liter")
    parser.add_argument("--samples", type=int, help="Stop after this many rows")
    parser.add_argument("--once", action="store_true", help="Capture one row and exit")
    parser.add_argument("--list-ports", action="store_true", help="List serial ports and exit")
    parser.add_argument("--simulate", action="store_true", help="Generate fake data without OBD hardware")
    parser.add_argument(
        "--print-csv-row",
        action="store_true",
        help="Print the exact CSV header and each saved CSV row to the terminal",
    )
    parser.add_argument(
        "--allow-maf-fuel-estimate",
        action="store_true",
        help="Use rough diesel MAF fuel estimate when PID 015E is unavailable",
    )
    parser.add_argument(
        "--assumed-diesel-afr",
        type=float,
        default=28.0,
        help="Assumed diesel air-fuel ratio for MAF fuel estimate",
    )
    parser.add_argument(
        "--diesel-density-g-l",
        type=float,
        default=832.0,
        help="Diesel density used for MAF fuel estimate",
    )
    parser.add_argument(
        "--diesel-energy-mj-l",
        type=float,
        default=35.8,
        help="Diesel lower heating value used for fuel-power estimate",
    )
    parser.add_argument(
        "--engine-efficiency",
        type=float,
        default=0.35,
        help="Assumed engine efficiency for fuel-power estimate",
    )
    parser.add_argument(
        "--maf-hp-factor",
        type=float,
        default=1.08,
        help="Rough MAF to horsepower factor",
    )
    parser.add_argument(
        "--fallback-baro-kpa",
        type=float,
        default=101.325,
        help="Barometric pressure fallback when PID 0133 is unsupported",
    )
    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_ports:
        try:
            for port in available_ports():
                print(port)
        except ObdError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        return 0

    if args.once:
        args.samples = 1

    if args.gps_port and args.use_windows_location:
        parser.error("--gps-port and --use-windows-location cannot be used together")

    if not args.simulate and not args.obd_port:
        parser.error("--obd-port is required unless --simulate or --list-ports is used")

    gps_reader = None
    windows_location_reader = None
    gps_provider = simulated_gps_provider() if args.simulate else empty_gps_provider()

    try:
        if args.use_windows_location:
            print("Using Windows Location Services...")
            windows_location_reader = WindowsLocationReader(
                poll_interval_s=args.interval,
                maximum_age_s=args.windows_location_maximum_age,
                timeout_s=args.windows_location_timeout,
                desired_accuracy_m=args.windows_location_accuracy_m,
            )
            windows_location_reader.start()
            gps_provider = windows_location_reader.latest
        elif args.gps_port:
            gps_reader = GpsReader(args.gps_port, args.gps_baud)
            gps_reader.start()
            gps_provider = gps_reader.latest

        if args.simulate:
            return run_logger(args, simulated_snapshot_provider(), gps_provider)

        with ObdSerial(args.obd_port, baudrate=args.baud) as obd:
            print("Initializing ELM327...")
            init_responses = obd.initialize()
            for command, response in init_responses.items():
                print(f"{command}: {one_line(response)}")

            supported = obd.supported_pids()
            print(f"Supported PIDs: {format_supported_pids(supported)}")
            if supported is not None and 0x5E not in supported:
                print("Warning: PID 015E fuel rate is not supported. Fuel totals may stay blank.")

            provider = lambda: obd.read_snapshot(supported)
            return run_logger(args, provider, gps_provider)
    except WindowsLocationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130
    finally:
        if gps_reader is not None:
            gps_reader.stop()
        if windows_location_reader is not None:
            windows_location_reader.stop()


def run_logger(
    args,
    snapshot_provider: SnapshotProvider,
    gps_provider: GpsProvider,
    should_stop: Optional[StopPredicate] = None,
    on_message: Optional[MessageCallback] = None,
    on_row: Optional[RowCallback] = None,
) -> int:
    state = RollingState.load(args.state_file)
    csv_path = make_csv_path(args.out_dir)
    emit(f"Writing CSV: {csv_path}", on_message)
    emit(f"State file: {os.path.abspath(args.state_file)}", on_message)

    count = 0
    last_time = time.monotonic()
    stop_requested = should_stop or (lambda: False)

    with CsvLogger(csv_path) as logger:
        if args.print_csv_row:
            emit("CSV live view:", on_message)
            emit(format_csv_header(), on_message)

        while not stop_requested():
            loop_started = time.monotonic()
            now = datetime.now()
            elapsed_s = max(loop_started - last_time, 0.0)
            last_time = loop_started

            snapshot = snapshot_provider()
            gps_fix = gps_provider()

            baro_kpa = snapshot.baro_kpa
            if baro_kpa is None:
                baro_kpa = args.fallback_baro_kpa

            derived = derive_values(
                maf_g_s=snapshot.maf_g_s,
                map_kpa=snapshot.map_kpa,
                baro_kpa=baro_kpa,
                obd_fuel_rate_l_h=snapshot.fuel_rate_l_h,
                allow_maf_fuel_estimate=args.allow_maf_fuel_estimate,
                assumed_diesel_afr=args.assumed_diesel_afr,
                diesel_density_g_l=args.diesel_density_g_l,
                diesel_energy_mj_l=args.diesel_energy_mj_l,
                engine_efficiency=args.engine_efficiency,
                maf_hp_factor=args.maf_hp_factor,
            )

            dt_h = elapsed_s / 3600.0
            distance_delta_km = (snapshot.speed_kph or 0.0) * dt_h
            fuel_delta_l = None
            if derived.fuel_rate_l_h is not None:
                fuel_delta_l = derived.fuel_rate_l_h * dt_h

            state.update(now, distance_delta_km, fuel_delta_l)
            state.save()

            row = build_row(
                now=now,
                snapshot=snapshot,
                gps_fix=gps_fix,
                derived=derived,
                state=state,
                fuel_price=args.fuel_price,
                baro_kpa=baro_kpa,
            )
            logger.write(row)
            if on_row is not None:
                on_row(row)
            if args.print_csv_row:
                emit(format_csv_row(row), on_message)
            else:
                emit(format_status(row), on_message)

            count += 1
            if args.samples and count >= args.samples:
                break

            sleep_s = max(args.interval - (time.monotonic() - loop_started), 0.0)
            sleep_until = time.monotonic() + sleep_s
            while not stop_requested() and time.monotonic() < sleep_until:
                time.sleep(min(0.1, max(sleep_until - time.monotonic(), 0.0)))

    return 0


def build_row(
    now: datetime,
    snapshot: ObdSnapshot,
    gps_fix: GpsFix,
    derived,
    state: RollingState,
    fuel_price: float,
    baro_kpa: Optional[float],
) -> dict:
    averages = state.averages()
    session_cost = state.session.fuel_l * fuel_price
    today_cost = state.today.fuel_l * fuel_price
    week_cost = state.week.fuel_l * fuel_price
    total_cost = state.total.fuel_l * fuel_price

    return {
        "timestamp": now.isoformat(timespec="seconds"),
        "평균 연비 (오늘) (L/100km)": safe_round(averages["today_l_100km"], 3),
        "평균 연비 (주간) (L/100km)": safe_round(averages["week_l_100km"], 3),
        "평균 연비 (합계) (L/100km)": safe_round(averages["total_l_100km"], 3),
        "Power from MAF (hp)": safe_round(derived.power_from_maf_hp, 2),
        "Latitude": safe_round(gps_fix.latitude, 7),
        "Longitude": safe_round(gps_fix.longitude, 7),
        "주행 거리 (오늘) (km)": safe_round(state.today.distance_km, 4),
        "주행 거리 (주간) (km)": safe_round(state.week.distance_km, 4),
        "주행 거리 (합계) (km)": safe_round(state.total.distance_km, 4),
        "순간 엔진 출력 (연료 소비 기반) (hp)": safe_round(derived.fuel_power_hp, 2),
        "계산된 엔진 부하 (%)": safe_round(snapshot.engine_load_pct, 1),
        "스로틀 위치 (%)": safe_round(snapshot.throttle_pct, 1),
        "엔진 냉각수 온도 (℃)": safe_round(snapshot.coolant_c, 1),
        "사용 연료 (L)": safe_round(state.session.fuel_l, 5),
        "사용 연료 (오늘) (L)": safe_round(state.today.fuel_l, 5),
        "사용 연료 (주간) (L)": safe_round(state.week.fuel_l, 5),
        "사용 연료 (합계) (L)": safe_round(state.total.fuel_l, 5),
        "사용 연료 비용 ($)": safe_round(session_cost, 4),
        "사용 연료 비용 (오늘) ($)": safe_round(today_cost, 4),
        "사용 연료 비용 (주간) ($)": safe_round(week_cost, 4),
        "사용 연료 비용 (합계) ($)": safe_round(total_cost, 4),
        "공기 질량 유량(MAF) (g/sec)": safe_round(snapshot.maf_g_s, 2),
        "계산된 부스트 압력 (bar)": safe_round(derived.boost_bar, 3),
        "계산된 순간 연료 소비율 (L/h)": safe_round(derived.fuel_rate_l_h, 3),
        "rpm": safe_round(snapshot.rpm, 0),
        "obd_speed_kph": safe_round(snapshot.speed_kph, 2),
        "gps_speed_kph": safe_round(gps_fix.speed_kph, 2),
        "gps_valid": gps_fix.valid,
        "map_kpa": safe_round(snapshot.map_kpa, 2),
        "baro_kpa": safe_round(baro_kpa, 2),
        "fuel_rate_source": derived.fuel_rate_source,
    }


def print_status(row: dict) -> None:
    print(format_status(row))


def format_status(row: dict) -> str:
    return " | ".join(
        [
            row["timestamp"],
            f"speed={row['obd_speed_kph']}km/h",
            f"fuel={row['계산된 순간 연료 소비율 (L/h)']}L/h",
            f"avg_today={row['평균 연비 (오늘) (L/100km)']}L/100km",
            f"lat={row['Latitude']}",
            f"lon={row['Longitude']}",
        ]
    )


def emit(message: str, callback: Optional[MessageCallback] = None) -> None:
    if callback is None:
        print(message)
    else:
        callback(message)


def make_csv_path(out_dir: str) -> str:
    filename = f"obd_log_{datetime.now():%Y%m%d_%H%M%S}.csv"
    return os.path.abspath(os.path.join(out_dir, filename))


def one_line(value: str) -> str:
    return " ".join(value.split())


def empty_gps_provider() -> GpsProvider:
    return lambda: GpsFix()


def simulated_gps_provider() -> GpsProvider:
    start = time.monotonic()

    def provider() -> GpsFix:
        elapsed = time.monotonic() - start
        return GpsFix(
            latitude=37.5665 + elapsed * 0.00001,
            longitude=126.9780 + elapsed * 0.00001,
            speed_kph=42.0,
            timestamp_utc=datetime.utcnow().isoformat(),
            valid=True,
        )

    return provider


def simulated_snapshot_provider() -> SnapshotProvider:
    start = time.monotonic()

    def provider() -> ObdSnapshot:
        elapsed = time.monotonic() - start
        wave = math.sin(elapsed / 5.0)
        speed = 45.0 + wave * 10.0
        rpm = 1500.0 + wave * 300.0
        maf = 22.0 + wave * 8.0
        map_kpa = 120.0 + max(wave, 0.0) * 60.0
        fuel_rate = 4.8 + max(wave, 0.0) * 3.0
        return ObdSnapshot(
            engine_load_pct=36.0 + max(wave, 0.0) * 28.0,
            rpm=rpm,
            speed_kph=speed,
            coolant_c=86.0,
            maf_g_s=maf,
            map_kpa=map_kpa,
            baro_kpa=101.0,
            throttle_pct=18.0 + max(wave, 0.0) * 22.0,
            fuel_rate_l_h=fuel_rate,
        )

    return provider
