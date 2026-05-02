import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

from .calculations import average_l_per_100km


@dataclass
class Bucket:
    distance_km: float = 0.0
    fuel_l: float = 0.0

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, float]]) -> "Bucket":
        data = data or {}
        return cls(
            distance_km=float(data.get("distance_km", 0.0)),
            fuel_l=float(data.get("fuel_l", 0.0)),
        )

    def to_dict(self) -> Dict[str, float]:
        return {
            "distance_km": self.distance_km,
            "fuel_l": self.fuel_l,
        }


class RollingState:
    def __init__(self, path: str) -> None:
        self.path = path
        self.today_key = ""
        self.week_key = ""
        self.session = Bucket()
        self.today = Bucket()
        self.week = Bucket()
        self.total = Bucket()

    @classmethod
    def load(cls, path: str) -> "RollingState":
        state = cls(path)
        if not os.path.exists(path):
            return state

        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)

        state.today_key = data.get("today_key", "")
        state.week_key = data.get("week_key", "")
        state.today = Bucket.from_dict(data.get("today"))
        state.week = Bucket.from_dict(data.get("week"))
        state.total = Bucket.from_dict(data.get("total"))
        return state

    def update(
        self,
        now: datetime,
        distance_delta_km: float,
        fuel_delta_l: Optional[float],
    ) -> None:
        self._roll_if_needed(now)

        fuel_delta = fuel_delta_l or 0.0
        for bucket in (self.session, self.today, self.week, self.total):
            bucket.distance_km += max(distance_delta_km, 0.0)
            bucket.fuel_l += max(fuel_delta, 0.0)

    def save(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        data = {
            "version": 1,
            "today_key": self.today_key,
            "week_key": self.week_key,
            "today": self.today.to_dict(),
            "week": self.week.to_dict(),
            "total": self.total.to_dict(),
        }
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.path)

    def averages(self) -> Dict[str, Optional[float]]:
        return {
            "today_l_100km": average_l_per_100km(self.today.fuel_l, self.today.distance_km),
            "week_l_100km": average_l_per_100km(self.week.fuel_l, self.week.distance_km),
            "total_l_100km": average_l_per_100km(self.total.fuel_l, self.total.distance_km),
        }

    def _roll_if_needed(self, now: datetime) -> None:
        today_key = now.date().isoformat()
        iso_year, iso_week, _ = now.isocalendar()
        week_key = f"{iso_year}-W{iso_week:02d}"

        if self.today_key != today_key:
            self.today_key = today_key
            self.today = Bucket()

        if self.week_key != week_key:
            self.week_key = week_key
            self.week = Bucket()
