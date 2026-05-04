import os
import tempfile
import unittest
from datetime import datetime

from obd2_logger.calculations import derive_values
from obd2_logger.obd import decode_percent, decode_supported_pids, parse_obd_payload
from obd2_logger.state import RollingState
from obd2_logger.windows_location import gps_fix_from_geoposition


class ObdParsingTests(unittest.TestCase):
    def test_parse_obd_payload(self):
        payload = parse_obd_payload("010C\r41 0C 1A F8\r", 0x0C)
        self.assertEqual(payload, [0x1A, 0xF8])

    def test_decode_supported_pids(self):
        supported = decode_supported_pids(0x00, [0x18, 0x18, 0x00, 0x00])
        self.assertIn(0x04, supported)
        self.assertIn(0x05, supported)
        self.assertIn(0x0C, supported)
        self.assertIn(0x0D, supported)

    def test_decode_percent(self):
        self.assertAlmostEqual(decode_percent([128]), 50.19607843137255)


class CalculationTests(unittest.TestCase):
    def test_derive_values_prefers_pid_015e(self):
        derived = derive_values(
            maf_g_s=20.0,
            map_kpa=150.0,
            baro_kpa=100.0,
            obd_fuel_rate_l_h=5.0,
            allow_maf_fuel_estimate=True,
            assumed_diesel_afr=28.0,
            diesel_density_g_l=832.0,
            diesel_energy_mj_l=35.8,
            engine_efficiency=0.35,
            maf_hp_factor=1.08,
        )
        self.assertAlmostEqual(derived.boost_bar, 0.5)
        self.assertAlmostEqual(derived.fuel_rate_l_h, 5.0)
        self.assertEqual(derived.fuel_rate_source, "015E")


class StateTests(unittest.TestCase):
    def test_rolling_state_accumulates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "state.json")
            state = RollingState.load(path)
            state.update(datetime(2026, 5, 2, 12, 0), 10.0, 0.7)
            state.save()

            loaded = RollingState.load(path)
            self.assertAlmostEqual(loaded.today.distance_km, 10.0)
            self.assertAlmostEqual(loaded.today.fuel_l, 0.7)
            self.assertAlmostEqual(loaded.total.distance_km, 10.0)


class WindowsLocationTests(unittest.TestCase):
    def test_converts_windows_geoposition_to_gps_fix(self):
        class Coordinate:
            latitude = 37.5665
            longitude = 126.978
            speed = 2.5
            timestamp = datetime(2026, 5, 2, 12, 0)

        class Position:
            coordinate = Coordinate()

        fix = gps_fix_from_geoposition(Position())

        self.assertTrue(fix.valid)
        self.assertAlmostEqual(fix.latitude, 37.5665)
        self.assertAlmostEqual(fix.longitude, 126.978)
        self.assertAlmostEqual(fix.speed_kph, 9.0)
        self.assertEqual(fix.timestamp_utc, "2026-05-02T12:00:00")


if __name__ == "__main__":
    unittest.main()
