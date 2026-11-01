"""FreeCooling model (GM-07): RC-07 active conditions, partial pre-cooling and power."""

import pytest

from gemini_model.free_cooling.free_cooling import FreeCooling, free_cooling_mode

LOAD = 4.0e6  # W
# RC-07 monthly wet-bulb (Stull) for De Bilt normals, °C.
MONTHLY_WET_BULB = {
    "Jan": 2.4, "Feb": 2.4, "Mar": 4.6, "Apr": 7.3, "May": 10.6, "Jun": 13.3,
    "Jul": 15.4, "Aug": 15.2, "Sep": 12.7, "Oct": 9.3, "Nov": 5.8, "Dec": 3.0,
}  # fmt: skip


def run(parameters=None, **inputs):
    """Run a free-cooling unit once and return its output dict."""
    unit = FreeCooling()
    unit.update_parameters(parameters or {})
    unit.calculate_output(inputs)
    return unit.get_output()


@pytest.mark.parametrize(
    "t_wb, mode", [(13.5, "full"), (13.6, "partial"), (19.4, "partial"), (19.5, "off")]
)
def test_active_condition_thresholds(t_wb, mode):
    """Full when T_wb + 4.5 + 2 <= 20 °C (T_wb <= 13.5); partial while < 26 °C."""
    out = run(ambient_wet_bulb_degc=t_wb, cooling_load_w=LOAD)
    assert out["mode"] == mode
    assert out["economiser_temperature_degc"] == pytest.approx(t_wb + 6.5)


def test_partial_free_cooling_pre_cools_the_chiller_return():
    """July (T_wb 15.4 °C): return 26 -> 21.9 °C, fraction (26 - 21.9)/6 of the load."""
    out = run(ambient_wet_bulb_degc=15.4, cooling_load_w=LOAD)
    fraction = (26.0 - 21.9) / 6.0
    assert out["free_cooling_fraction"] == pytest.approx(fraction)
    assert out["free_cooling_w"] == pytest.approx(fraction * LOAD)
    assert out["residual_cooling_w"] == pytest.approx((1.0 - fraction) * LOAD)
    assert out["chilled_water_temperature_out_degc"] == pytest.approx(21.9)


def test_rc07_monthly_screening_months():
    """RC-07 [D]: monthly means make Apr, May, Jun (marginal), Sep, Oct eligible; not Jul/Aug."""
    modes = {
        month: run(ambient_wet_bulb_degc=t_wb, cooling_load_w=LOAD)["mode"]
        for month, t_wb in MONTHLY_WET_BULB.items()
    }
    for month in ("Apr", "May", "Jun", "Sep", "Oct"):
        assert modes[month] == "full", month
    assert modes["Jul"] == modes["Aug"] == "partial"
    jun = run(ambient_wet_bulb_degc=MONTHLY_WET_BULB["Jun"], cooling_load_w=LOAD)
    assert jun["economiser_temperature_degc"] == pytest.approx(19.8)  # marginal: 0.2 K margin


def test_power_is_two_percent_of_heat_moved():
    """Fan + pumps ≈ 2 % of heat moved: performance factor 50, inside the 24-53 range."""
    out = run(ambient_wet_bulb_degc=8.0, cooling_load_w=LOAD)
    assert out["power_el"] == pytest.approx(0.02 * LOAD)
    assert out["heat_rejected_w"] == pytest.approx(LOAD + out["power_el"], rel=1e-14)
    assert out["power_th"] == LOAD
    assert out["emission"] == 0.0
    assert 24.0 <= out["performance_factor"] <= 53.0


def test_capacity_limit_and_chilled_flow_balance():
    """A capacity limit leaves a residual load; the chilled-water balance closes."""
    out = run(
        {"nominal_capacity_w": 1.0e6}, ambient_wet_bulb_degc=8.0, chilled_water_flow_rate_m3s=0.1
    )
    rho_cp = 1000.0 * 4217.4
    assert out["cooling_load_w"] == pytest.approx(rho_cp * 0.1 * 6.0)
    assert out["free_cooling_w"] == 1.0e6
    leaving = out["chilled_water_temperature_out_degc"]
    assert rho_cp * 0.1 * (26.0 - leaving) == pytest.approx(1.0e6, rel=1e-12)
    assert out["residual_cooling_w"] == pytest.approx(out["cooling_load_w"] - 1.0e6)


def test_off_and_zero_load():
    """Off: nothing free cooled; zero load: zero power."""
    off = run(ambient_wet_bulb_degc=21.0, cooling_load_w=LOAD)
    assert off["free_cooling_w"] == 0.0
    assert off["residual_cooling_w"] == LOAD
    assert off["power_el"] == 0.0
    assert off["chilled_water_temperature_out_degc"] == 26.0
    idle = run(ambient_wet_bulb_degc=5.0, cooling_load_w=0.0)
    assert idle["free_cooling_w"] == idle["power_el"] == idle["free_cooling_fraction"] == 0.0
    assert idle["validity_warnings"] == []


def test_freezing_tower_water_is_flagged():
    """At T_wb -5 °C the tower water (T_wb + 4.5 K) is below 0 °C: freeze warning."""
    out = run(ambient_wet_bulb_degc=-5.0, cooling_load_w=LOAD)
    assert out["mode"] == "full"
    assert "freeze" in out["validity_warnings"][0]


def test_mode_function():
    """free_cooling_mode classifies the economiser temperature against supply/return."""
    assert free_cooling_mode(19.0, 20.0, 26.0) == "full"
    assert free_cooling_mode(23.0, 20.0, 26.0) == "partial"
    assert free_cooling_mode(26.0, 20.0, 26.0) == "off"


@pytest.mark.parametrize(
    "inputs, message",
    [
        ({"cooling_load_w": 1.0, "chilled_water_flow_rate_m3s": 0.1}, "exactly one"),
        ({"cooling_load_w": 1.0, "chilled_water_temperature_in_degc": 20.0}, "return"),
        ({"cooling_load_w": -1.0}, ">= 0"),
    ],
)
def test_invalid_inputs_raise(inputs, message):
    """Inconsistent or invalid inputs raise ValueError."""
    with pytest.raises(ValueError, match=message):
        run(ambient_wet_bulb_degc=10.0, **inputs)
