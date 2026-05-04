# obd2_test

ELM327 Bluetooth OBD-II adapter and optional USB GPS logger for a diesel car.

The logger writes a CSV on the laptop and keeps a small `state.json` file so
today, weekly, and total fuel/distance values can continue across runs.

## Hardware

- ELM327 v1.5 compatible Bluetooth OBD-II adapter
- Windows laptop
- Optional USB GPS/GNSS receiver or Windows Location Services for latitude and longitude

The OBD adapter does not provide GPS. If you want latitude/longitude, use a USB
GPS receiver, another NMEA serial GPS source, or Windows Location Services on
the laptop.

## Setup

1. Pair the ELM327 adapter in Windows Bluetooth settings.
2. Check the assigned COM port in Device Manager.
3. Optional: connect a USB GPS receiver and check its COM port.
4. Install Python dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

For Windows Location Services, use Python 3.9 or newer and make sure Windows
Settings allows location access for desktop apps.

List detected serial ports:

```powershell
python -m obd2_logger --list-ports
```

Open the GUI logger:

```powershell
python -m obd2_logger.gui
```

In the GUI, click `Refresh ports`, select the OBD COM port, choose the GPS
source, then click `Start logging`. Use `Stop` before unplugging the OBD
adapter so the COM port is closed cleanly.

Run the logger:

```powershell
python -m obd2_logger --obd-port COM5 --gps-port COM7 --fuel-price 1.50
```

Use the laptop's Windows Location Services instead of a GPS COM port:

```powershell
python -m obd2_logger --obd-port COM5 --use-windows-location --fuel-price 1.50
```

Do not combine `--gps-port` and `--use-windows-location`; pick one location
source.

Show the exact CSV rows in the terminal as they are saved:

```powershell
python -m obd2_logger --obd-port COM5 --gps-port COM7 --fuel-price 1.50 --print-csv-row
```

You can also watch the latest CSV file from a second PowerShell window:

```powershell
$file = Get-ChildItem .\logs\*.csv | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-Content $file.FullName -Wait -Tail 20 -Encoding UTF8
```

If GPS is not connected:

```powershell
python -m obd2_logger --obd-port COM5 --fuel-price 1.50
```

If your diesel car does not support standard PID `015E` for fuel rate, the
logger will leave fuel-consumption-derived values blank by default. You can
enable a rough MAF-based diesel estimate, but use it only for comparison:

```powershell
python -m obd2_logger --obd-port COM5 --allow-maf-fuel-estimate
```

Run a local fake-data check without hardware:

```powershell
python -m obd2_logger --simulate --samples 5
```

Test Windows location while simulating OBD data:

```powershell
python -m obd2_logger --simulate --use-windows-location --samples 5
```

Run unit tests:

```powershell
python -m unittest discover
```

## Important PIDs

| Value | PID | Notes |
| --- | --- | --- |
| Calculated engine load | `0104` | Direct OBD value |
| RPM | `010C` | Used for diagnostics/context |
| Vehicle speed | `010D` | Used for distance integration by default |
| Coolant temperature | `0105` | Direct OBD value |
| MAF | `0110` | Used for MAF power estimate |
| MAP | `010B` | Used with BARO for boost |
| Throttle position | `0111` | Direct OBD value |
| Barometric pressure | `0133` | Used for boost |
| Fuel rate | `015E` | Best standard source for fuel use, if supported |

## CSV columns

The CSV contains the requested Car Scanner-style columns:

- `평균 연비 (오늘) (L/100km)`
- `평균 연비 (주간) (L/100km)`
- `평균 연비 (합계) (L/100km)`
- `Power from MAF (hp)`
- `Latitude`
- `Longitude`
- `주행 거리 (오늘) (km)`
- `주행 거리 (주간) (km)`
- `주행 거리 (합계) (km)`
- `순간 엔진 출력 (연료 소비 기반) (hp)`
- `계산된 엔진 부하 (%)`
- `스로틀 위치 (%)`
- `엔진 냉각수 온도 (℃)`
- `사용 연료 (L)`
- `사용 연료 (오늘) (L)`
- `사용 연료 (주간) (L)`
- `사용 연료 (합계) (L)`
- `사용 연료 비용 ($)`
- `사용 연료 비용 (오늘) ($)`
- `사용 연료 비용 (주간) ($)`
- `사용 연료 비용 (합계) ($)`
- `공기 질량 유량(MAF) (g/sec)`
- `계산된 부스트 압력 (bar)`
- `계산된 순간 연료 소비율 (L/h)`

Extra diagnostic columns such as timestamp, RPM, OBD speed, MAP, BARO, and GPS
validity are also included to make validation easier.

## Notes for diesel cars

For diesel vehicles, fuel consumption from MAF is only an approximation because
diesel air-fuel ratio changes widely with load, turbo pressure, EGR, and DPF
regeneration. Prefer standard PID `015E` or a manufacturer-specific fuel PID
when available.
