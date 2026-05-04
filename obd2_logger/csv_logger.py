import csv
import io
import os
from typing import Dict, Iterable, TextIO


CSV_COLUMNS = [
    "timestamp",
    "평균 연비 (오늘) (L/100km)",
    "평균 연비 (주간) (L/100km)",
    "평균 연비 (합계) (L/100km)",
    "Power from MAF (hp)",
    "Latitude",
    "Longitude",
    "주행 거리 (오늘) (km)",
    "주행 거리 (주간) (km)",
    "주행 거리 (합계) (km)",
    "순간 엔진 출력 (연료 소비 기반) (hp)",
    "계산된 엔진 부하 (%)",
    "스로틀 위치 (%)",
    "엔진 냉각수 온도 (℃)",
    "사용 연료 (L)",
    "사용 연료 (오늘) (L)",
    "사용 연료 (주간) (L)",
    "사용 연료 (합계) (L)",
    "사용 연료 비용 ($)",
    "사용 연료 비용 (오늘) ($)",
    "사용 연료 비용 (주간) ($)",
    "사용 연료 비용 (합계) ($)",
    "공기 질량 유량(MAF) (g/sec)",
    "계산된 부스트 압력 (bar)",
    "계산된 순간 연료 소비율 (L/h)",
    "rpm",
    "obd_speed_kph",
    "gps_speed_kph",
    "gps_valid",
    "map_kpa",
    "baro_kpa",
    "fuel_rate_source",
]


class CsvLogger:
    def __init__(self, path: str, fieldnames: Iterable[str] = CSV_COLUMNS) -> None:
        self.path = path
        self.fieldnames = list(fieldnames)
        self._file: TextIO = None
        self._writer = None

    def __enter__(self) -> "CsvLogger":
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._file = open(self.path, "w", newline="", encoding="utf-8-sig")
        self._writer = csv.DictWriter(self._file, fieldnames=self.fieldnames)
        self._writer.writeheader()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._file:
            self._file.close()

    def write(self, row: Dict[str, object]) -> None:
        self._writer.writerow({name: row.get(name) for name in self.fieldnames})
        self._file.flush()


def format_csv_header(fieldnames: Iterable[str] = CSV_COLUMNS) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="")
    writer.writerow(list(fieldnames))
    return buffer.getvalue()


def format_csv_row(row: Dict[str, object], fieldnames: Iterable[str] = CSV_COLUMNS) -> str:
    names = list(fieldnames)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=names, lineterminator="")
    writer.writerow({name: row.get(name) for name in names})
    return buffer.getvalue()
