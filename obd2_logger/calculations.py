from dataclasses import dataclass
from typing import Optional


@dataclass
class DerivedValues:
    boost_bar: Optional[float]
    fuel_rate_l_h: Optional[float]
    fuel_rate_source: str
    power_from_maf_hp: Optional[float]
    fuel_power_hp: Optional[float]


def safe_round(value: Optional[float], digits: int = 3) -> Optional[float]:
    if value is None:
        return None
    return round(value, digits)


def average_l_per_100km(fuel_l: float, distance_km: float) -> Optional[float]:
    if distance_km <= 0:
        return None
    return fuel_l / distance_km * 100.0


def diesel_fuel_rate_from_maf(
    maf_g_s: Optional[float],
    assumed_afr: float,
    diesel_density_g_l: float,
) -> Optional[float]:
    if maf_g_s is None or assumed_afr <= 0 or diesel_density_g_l <= 0:
        return None
    fuel_g_s = maf_g_s / assumed_afr
    return fuel_g_s * 3600.0 / diesel_density_g_l


def fuel_power_hp(
    fuel_rate_l_h: Optional[float],
    fuel_energy_mj_l: float,
    engine_efficiency: float,
) -> Optional[float]:
    if fuel_rate_l_h is None:
        return None
    kw = fuel_rate_l_h * fuel_energy_mj_l / 3.6 * engine_efficiency
    return kw * 1.34102209


def derive_values(
    maf_g_s: Optional[float],
    map_kpa: Optional[float],
    baro_kpa: Optional[float],
    obd_fuel_rate_l_h: Optional[float],
    allow_maf_fuel_estimate: bool,
    assumed_diesel_afr: float,
    diesel_density_g_l: float,
    diesel_energy_mj_l: float,
    engine_efficiency: float,
    maf_hp_factor: float,
) -> DerivedValues:
    boost_bar = None
    if map_kpa is not None and baro_kpa is not None:
        boost_bar = (map_kpa - baro_kpa) / 100.0

    fuel_rate = obd_fuel_rate_l_h
    fuel_rate_source = "015E" if fuel_rate is not None else ""

    if fuel_rate is None and allow_maf_fuel_estimate:
        fuel_rate = diesel_fuel_rate_from_maf(
            maf_g_s,
            assumed_afr=assumed_diesel_afr,
            diesel_density_g_l=diesel_density_g_l,
        )
        fuel_rate_source = "MAF_ESTIMATE" if fuel_rate is not None else ""

    power_from_maf = maf_g_s * maf_hp_factor if maf_g_s is not None else None
    power_from_fuel = fuel_power_hp(fuel_rate, diesel_energy_mj_l, engine_efficiency)

    return DerivedValues(
        boost_bar=boost_bar,
        fuel_rate_l_h=fuel_rate,
        fuel_rate_source=fuel_rate_source,
        power_from_maf_hp=power_from_maf,
        fuel_power_hp=power_from_fuel,
    )
