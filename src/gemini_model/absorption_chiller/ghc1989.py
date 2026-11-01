"""Method A: Geo-Heat Center 1989 absorption-chiller correlation (temperatures in °F).

Source: RC-05 Method A -- Geothermal Direct Use Engineering & Design Guidebook
(Geo-Heat Center, 1989), Ch14 p.261-269; the correlations were decoded from the
TI-59 program listing on p.268 (RC-05 "Verification status").

- Hot-water temperature factor ``F_T = (-239 + 1.65 T_hw - 0.001 T_hw^2) / 100``.
- Condition factor ``F_c = (323 - 0.838 T_chw - (3.77 - 0.0358 T_chw) T_cw) / 100``,
  T_chw leaving chilled water (valid 40-50 °F), T_cw entering cooling water (valid 75-90 °F).
- Capacity fraction ``F = F_T F_c`` (fraction of the nominal rating; nominal = Q_required / F).
- ``COP = 0.67 (55.4 + 0.185 T_hw) / 100`` (depends on T_hw only).

Rating basis (p.261-262): 12 psi steam / ~118 °C hot water, 85 °F (29.4 °C) cooling water,
44 °F (6.7 °C) chilled water, COP 0.65-0.70. Outside its validity (e.g. our 20 °C chilled
water) the correlation is a conservative lower bound only (RC-05).
"""

import math

# Validity of the condition factor F_c (RC-05, Guidebook p.267-269), °F.
CHILLED_WATER_VALIDITY_DEGF = (40.0, 50.0)
COOLING_WATER_VALIDITY_DEGF = (75.0, 90.0)
# Hot-water range of the rating points used for checks and calibration (RC-05), °F.
RATING_HOT_WATER_RANGE_DEGF = (180.0, 240.0)
RATING_CHILLED_WATER_DEGF = 44.0
RATING_COOLING_WATER_DEGF = 85.0

KW_PER_TON = 3.517  # kW of cooling per ton of refrigeration (RC-05 cost section)


def degc_to_degf(temperature_degc):
    """Convert °C to °F."""
    return temperature_degc * 1.8 + 32.0


def degf_to_degc(temperature_degf):
    """Convert °F to °C."""
    return (temperature_degf - 32.0) / 1.8


def hot_water_factor(hot_water_temperature_degf):
    """Return the hot-water temperature factor F_T (-); RC-05 Method A (Guidebook p.268).

    Not clipped: values below 0 mean the drive is below the zero-capacity temperature.
    """
    t = hot_water_temperature_degf
    return (-239.0 + 1.65 * t - 0.001 * t * t) / 100.0


def condition_factor(chilled_water_temperature_degf, cooling_water_temperature_degf):
    """Return the condition factor F_c (-) for leaving chilled / entering cooling water (°F).

    RC-05 Method A (Guidebook p.268); F_c ≈ 1.00 at 44/85 °F. Valid for 40-50 °F chilled
    and 75-90 °F cooling water.
    """
    t_chw = chilled_water_temperature_degf
    t_cw = cooling_water_temperature_degf
    return (323.0 - 0.838 * t_chw - (3.77 - 0.0358 * t_chw) * t_cw) / 100.0


def capacity_fraction(
    hot_water_temperature_degf, chilled_water_temperature_degf, cooling_water_temperature_degf
):
    """Return F = F_T F_c, the capacity as a fraction of the nominal rating (RC-05 Method A)."""
    return hot_water_factor(hot_water_temperature_degf) * condition_factor(
        chilled_water_temperature_degf, cooling_water_temperature_degf
    )


def cop(hot_water_temperature_degf):
    """Return the thermal COP = 0.67 (55.4 + 0.185 T_hw)/100 (RC-05 Method A, Guidebook p.268)."""
    return 0.67 * (55.4 + 0.185 * hot_water_temperature_degf) / 100.0


def zero_capacity_temperature_degf():
    """Return the lower root of F_T = 0 (≈ 160.5 °F = 71.4 °C), the zero-capacity drive temperature.

    Method A keeps this temperature fixed whatever the chilled/cooling-water
    temperatures are (a limitation of the product form F_T F_c; see REFERENCE.md).
    """
    a, b, c = -0.001, 1.65, -239.0
    return (-b + math.sqrt(b * b - 4.0 * a * c)) / (2.0 * a)
