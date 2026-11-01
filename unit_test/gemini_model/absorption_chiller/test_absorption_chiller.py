"""AbsorptionChiller model (GM-04): RC-05 checks, energy balances and validity warnings."""

import pytest

from gemini_model.absorption_chiller import characteristic, ghc1989
from gemini_model.absorption_chiller.absorption_chiller import AbsorptionChiller

Q_NOM = 1.0e6  # W
RATING_CHILLED_DEGC = ghc1989.degf_to_degc(44.0)
RATING_COOLING_DEGC = ghc1989.degf_to_degc(85.0)
BRINE = {"drive_density_kg_m3": 1030.0, "drive_specific_heat_j_kgk": 4100.0}


def make_chiller(**parameters):
    """Return an AbsorptionChiller with a 1 MW nominal capacity and the given parameters."""
    chiller = AbsorptionChiller()
    chiller.update_parameters({"nominal_capacity_w": Q_NOM, **parameters})
    return chiller


def run(chiller, **inputs):
    """Run the chiller once and return its output dict."""
    chiller.calculate_output(inputs)
    return chiller.get_output()


def rating_inputs(t_hw_degf, load_w=10 * Q_NOM):
    """Build inputs at a Method A rating point (44 °F leaving chilled, 85 °F cooling water)."""
    return {
        "drive_temperature_in_degc": ghc1989.degf_to_degc(t_hw_degf),
        "cooling_water_temperature_in_degc": RATING_COOLING_DEGC,
        "chilled_water_temperature_out_degc": RATING_CHILLED_DEGC,
        "chilled_water_temperature_in_degc": RATING_CHILLED_DEGC + 6.0,
        "cooling_load_w": load_w,
    }


@pytest.mark.parametrize("t_hw_degf", [180.0, 190.0, 200.0, 210.0, 220.0, 230.0, 240.0])
def test_characteristic_model_matches_method_a_on_rating_points(t_hw_degf):
    """RC-05: Method B (calibrated) reproduces Method A within ± 5 % at its rating points."""
    out_b = run(make_chiller(), **rating_inputs(t_hw_degf))
    out_a = run(make_chiller(method="ghc1989"), **rating_inputs(t_hw_degf))
    expected = ghc1989.capacity_fraction(t_hw_degf, 44.0, 85.0)
    assert out_a["capacity_fraction"] == pytest.approx(expected, rel=1e-12)
    assert out_b["capacity_fraction"] == pytest.approx(expected, rel=0.05)
    assert out_b["cop"] == pytest.approx(ghc1989.cop(t_hw_degf), rel=0.05)
    assert out_a["cop"] == pytest.approx(ghc1989.cop(t_hw_degf), rel=1e-12)
    assert out_b["validity_warnings"] == []
    assert out_a["validity_warnings"] == []


def test_screening_estimate_85c_drive_at_20c_chilled_water():
    """RC-05 [D]: 85 °C drive at 20/26 °C chilled ≈ capacity fraction 0.6-0.7, COP 0.63-0.70.

    Rating cooling water (29.4 °C). Extrapolated from the calibration: reported as uncertain.
    """
    out = run(
        make_chiller(),
        drive_temperature_in_degc=85.0,
        cooling_water_temperature_in_degc=RATING_COOLING_DEGC,
        cooling_load_w=10 * Q_NOM,
    )
    assert 0.60 <= out["capacity_fraction"] <= 0.70
    assert 0.63 <= out["cop"] <= 0.70
    assert any("uncertain" in w for w in out["validity_warnings"])


def test_twenty_degree_chilled_water_is_worth_about_fifteen_k_of_drive():
    """RC-05 [D]: raising t_E by ≈ 14 K is worth B × 14 ≈ 16 K of drive temperature."""
    b = characteristic.DEFAULT_DUHRING_SLOPE
    warm = run(
        make_chiller(),
        drive_temperature_in_degc=85.0,
        cooling_water_temperature_in_degc=RATING_COOLING_DEGC,
        chilled_water_temperature_out_degc=RATING_CHILLED_DEGC + 14.0,
        chilled_water_temperature_in_degc=RATING_CHILLED_DEGC + 20.0,
        cooling_load_w=10 * Q_NOM,
    )
    hot_drive_degf = ghc1989.degc_to_degf(85.0 + 14.0 * b)
    hot = run(make_chiller(), **rating_inputs(hot_drive_degf))
    assert warm["capacity_fraction"] == pytest.approx(hot["capacity_fraction"], rel=1e-12)
    assert 15.0 <= 14 * b <= 17.0


@pytest.mark.parametrize("method", ["characteristic", "ghc1989"])
@pytest.mark.parametrize("load_fraction", [0.2, 0.5, 5.0])
def test_energy_balance_is_exact(method, load_fraction):
    """Q_reject = Q_cool + Q_drive + P_aux exactly; P_aux = 0.077 kW/ton of Q_cool."""
    out = run(
        make_chiller(method=method),
        drive_temperature_in_degc=95.0,
        cooling_water_temperature_in_degc=27.0,
        cooling_load_w=load_fraction * Q_NOM,
    )
    q_cool, q_drive, p_aux = out["cooling_power_w"], out["drive_heat_w"], out["power_el"]
    assert q_cool > 0.0
    assert out["heat_rejected_w"] == pytest.approx(q_cool + q_drive + p_aux, rel=1e-14)
    assert p_aux == pytest.approx(0.077 / 3.517 * q_cool, rel=1e-14)
    assert out["power_th"] == q_cool
    assert out["emission"] == 0.0
    assert q_cool + out["unmet_cooling_w"] == pytest.approx(load_fraction * Q_NOM, rel=1e-14)
    assert out["cop"] == pytest.approx(q_cool / q_drive, rel=1e-14)


@pytest.mark.parametrize("method", ["characteristic", "ghc1989"])
def test_circuit_energy_balances_with_given_flows(method):
    """With given flows each external circuit closes: ρ c_p V ΔT = its heat flow."""
    flows = {"drive": 0.03, "cooling_water": 0.12, "chilled_water": 0.05}
    chiller = make_chiller(method=method, **BRINE)
    out = run(
        chiller,
        drive_temperature_in_degc=95.0,
        drive_flow_rate_m3s=flows["drive"],
        cooling_water_temperature_in_degc=27.0,
        cooling_water_flow_rate_m3s=flows["cooling_water"],
        chilled_water_flow_rate_m3s=flows["chilled_water"],
    )
    p = chiller.parameters

    def rate(prefix):
        density = p[f"{prefix}_density_kg_m3"]
        return density * p[f"{prefix}_specific_heat_j_kgk"] * flows[prefix]

    drive = rate("drive") * (out["drive_temperature_in_degc"] - out["drive_temperature_out_degc"])
    cooling = rate("cooling_water") * (
        out["cooling_water_temperature_out_degc"] - out["cooling_water_temperature_in_degc"]
    )
    chilled = rate("chilled_water") * (
        out["chilled_water_temperature_in_degc"] - out["chilled_water_temperature_out_degc"]
    )
    assert drive == pytest.approx(out["drive_heat_w"], rel=1e-12)
    assert cooling == pytest.approx(out["drive_heat_w"] + out["cooling_power_w"], rel=1e-12)
    assert chilled == pytest.approx(out["cooling_power_w"], rel=1e-12)
    assert out["cooling_load_w"] == pytest.approx(rate("chilled_water") * 6.0, rel=1e-12)
    assert out["drive_flow_rate_m3s"] == flows["drive"]


def test_closed_form_solution_satisfies_the_characteristic_equation():
    """With all flows given and the load not met, the outlet temperatures satisfy ΔΔt."""
    chiller = make_chiller()
    out = run(
        chiller,
        drive_temperature_in_degc=88.0,
        drive_flow_rate_m3s=0.02,
        cooling_water_temperature_in_degc=28.0,
        cooling_water_flow_rate_m3s=0.08,
        chilled_water_flow_rate_m3s=0.2,
    )
    assert out["unmet_cooling_w"] > 0.0  # full-load operating point
    mean = {
        key: 0.5 * (out[f"{key}_temperature_in_degc"] + out[f"{key}_temperature_out_degc"])
        for key in ("drive", "cooling_water", "chilled_water")
    }
    ddt = characteristic.delta_delta_t_k(
        mean["drive"], mean["cooling_water"], mean["chilled_water"], 1.15
    )
    c = chiller.characteristic_coefficients()
    assert out["delta_delta_t_k"] == pytest.approx(ddt, rel=1e-12)
    assert out["cooling_power_w"] == pytest.approx(
        c["s_e_per_k"] * Q_NOM * (ddt - c["ddt_min_k"]), rel=1e-12
    )
    assert out["drive_heat_w"] == pytest.approx(
        c["s_g_per_k"] * Q_NOM * (ddt - c["ddt_min_g_k"]), rel=1e-12
    )


def test_fixed_delta_t_mode_agrees_with_flow_mode_at_the_reported_flows():
    """Feeding the reported required flows back as given flows gives the same duty."""
    base = {
        "drive_temperature_in_degc": 90.0,
        "cooling_water_temperature_in_degc": 26.0,
        "cooling_load_w": 3 * Q_NOM,
    }
    fixed = run(make_chiller(), **base)
    flow = run(
        make_chiller(),
        **base,
        drive_flow_rate_m3s=fixed["drive_flow_rate_m3s"],
        cooling_water_flow_rate_m3s=fixed["cooling_water_flow_rate_m3s"],
    )
    assert fixed["drive_temperature_in_degc"] - fixed["drive_temperature_out_degc"] == (
        pytest.approx(7.2)
    )
    assert flow["cooling_power_w"] == pytest.approx(fixed["cooling_power_w"], rel=1e-9)
    assert flow["drive_heat_w"] == pytest.approx(fixed["drive_heat_w"], rel=1e-9)
    assert flow["drive_temperature_out_degc"] == pytest.approx(
        fixed["drive_temperature_out_degc"], rel=1e-9
    )


@pytest.mark.parametrize("method", ["characteristic", "ghc1989"])
def test_heat_rejection_auxiliaries_switch(method):
    """include_heat_rejection_auxiliaries: True adds 0.077 kW/ton; False (tower connected) 0."""
    inputs = {
        "drive_temperature_in_degc": 95.0,
        "cooling_water_temperature_in_degc": 27.0,
        "cooling_load_w": 0.5 * Q_NOM,
    }
    on = run(make_chiller(method=method), **inputs)
    off = run(make_chiller(method=method, include_heat_rejection_auxiliaries=False), **inputs)
    assert on["power_el"] == pytest.approx(0.077 / 3.517 * on["cooling_power_w"], rel=1e-14)
    assert off["power_el"] == off["aux_power_w"] == 0.0
    assert off["heat_rejected_w"] == off["absorber_condenser_heat_w"]
    assert off["heat_rejected_w"] == pytest.approx(on["heat_rejected_w"] - on["power_el"])
    assert (off["cooling_power_w"], off["drive_heat_w"]) == (
        on["cooling_power_w"],
        on["drive_heat_w"],
    )
    with pytest.raises(TypeError, match="include_heat_rejection_auxiliaries"):
        make_chiller(include_heat_rejection_auxiliaries="no").calculate_output(inputs)


def test_part_load_cop_falls_slightly_with_characteristic_method():
    """Method B part load reduces ΔΔt: Q_E = load and the COP is below the full-load COP."""
    inputs = {"drive_temperature_in_degc": 95.0, "cooling_water_temperature_in_degc": 27.0}
    full = run(make_chiller(), **inputs, cooling_load_w=5 * Q_NOM)
    part = run(make_chiller(), **inputs, cooling_load_w=0.3 * Q_NOM)
    assert part["cooling_power_w"] == pytest.approx(0.3 * Q_NOM)
    assert part["unmet_cooling_w"] == 0.0
    assert part["available_cooling_w"] == pytest.approx(full["available_cooling_w"])
    assert 0.9 * full["cop"] < part["cop"] < full["cop"]
    part_a = run(make_chiller(method="ghc1989"), **inputs, cooling_load_w=0.3 * Q_NOM)
    full_a = run(make_chiller(method="ghc1989"), **inputs, cooling_load_w=5 * Q_NOM)
    assert part_a["cop"] == pytest.approx(full_a["cop"], rel=1e-14)


def test_capacity_is_capped_at_nameplate():
    """Cold cooling water would exceed nameplate: available capacity is capped [A]."""
    out = run(
        make_chiller(),
        drive_temperature_in_degc=95.0,
        cooling_water_temperature_in_degc=22.9,
        cooling_load_w=5 * Q_NOM,
    )
    assert out["capacity_fraction"] == pytest.approx(1.0)
    assert out["cooling_power_w"] == pytest.approx(Q_NOM)
    capped = run(
        make_chiller(max_capacity_fraction=0.5),
        drive_temperature_in_degc=95.0,
        cooling_water_temperature_in_degc=22.9,
        cooling_load_w=5 * Q_NOM,
    )
    assert capped["cooling_power_w"] == pytest.approx(0.5 * Q_NOM)


@pytest.mark.parametrize("method", ["characteristic", "ghc1989"])
def test_cold_drive_switches_the_machine_off(method):
    """Below the zero-capacity drive temperature there is no cooling and no drive heat.

    With 20 °C chilled water the ΔΔt form lowers the zero-capacity drive temperature
    (≈ 55 °C at 29.4 °C cooling water), so 50 °C is used; Method A keeps it at 71.4 °C.
    """
    out = run(
        make_chiller(method=method),
        drive_temperature_in_degc=50.0,
        cooling_water_temperature_in_degc=RATING_COOLING_DEGC,
        cooling_load_w=Q_NOM,
    )
    assert out["cooling_power_w"] == 0.0
    assert out["drive_heat_w"] == 0.0
    assert out["heat_rejected_w"] == 0.0
    assert out["unmet_cooling_w"] == Q_NOM
    assert out["drive_temperature_out_degc"] == 50.0
    assert out["drive_flow_rate_m3s"] == 0.0
    if method == "ghc1989":
        assert any("zero-capacity" in w for w in out["validity_warnings"])


def test_ghc1989_outside_validity_is_a_conservative_lower_bound():
    """Method A at 20 °C chilled / 22 °C cooling water is evaluated at 50 °F / 75 °F."""
    inputs = {
        "drive_temperature_in_degc": 90.0,
        "cooling_water_temperature_in_degc": 22.0,
        "cooling_load_w": 5 * Q_NOM,
    }
    out = run(make_chiller(method="ghc1989"), **inputs)
    t_hw = ghc1989.degc_to_degf(90.0)
    assert out["capacity_fraction"] == pytest.approx(
        ghc1989.capacity_fraction(t_hw, 50.0, 75.0), rel=1e-12
    )
    assert len(out["validity_warnings"]) == 2
    assert all("lower bound" in w for w in out["validity_warnings"])
    raw = run(make_chiller(method="ghc1989", ghc1989_conservative_clamp=False), **inputs)
    assert raw["capacity_fraction"] > out["capacity_fraction"]
    # The calibrated characteristic method is above the Method A lower bound here.
    assert run(make_chiller(), **inputs)["capacity_fraction"] > out["capacity_fraction"]


def test_characteristic_warns_when_extrapolating_beyond_calibration():
    """20 °C chilled and 22.9 °C cooling water are outside the Method A calibration."""
    out = run(
        make_chiller(),
        drive_temperature_in_degc=85.0,
        cooling_water_temperature_in_degc=22.9,
        cooling_load_w=Q_NOM,
    )
    text = " ".join(out["validity_warnings"])
    assert "chilled water 20.0 °C extrapolated" in text
    assert "cooling water 22.9 °C extrapolated" in text


def test_datasheet_coefficients_are_used_without_method_a_warnings():
    """User coefficients (e.g. from a datasheet) replace the Method A fit."""
    coefficients = {"s_e_per_k": 0.03, "ddt_min_k": 8.0, "s_g_per_k": 0.045, "ddt_min_g_k": 6.0}
    chiller = make_chiller(characteristic_coefficients=coefficients)
    out = run(
        chiller,
        drive_temperature_in_degc=85.0,
        cooling_water_temperature_in_degc=25.0,
        cooling_load_w=5 * Q_NOM,
    )
    ddt = (85.0 - 3.6) - 2.15 * (25.0 + 2.5) + 1.15 * 23.0
    assert out["delta_delta_t_k"] == pytest.approx(ddt)
    assert out["capacity_fraction"] == pytest.approx(min(0.03 * (ddt - 8.0), 1.0))
    assert out["validity_warnings"] == []


def test_brine_drive_needs_less_flow_than_water_for_the_same_heat():
    """Per-circuit ρ c_p: required drive flow scales with 1/(ρ c_p) (brine 1030 × 4100)."""
    inputs = {
        "drive_temperature_in_degc": 90.0,
        "cooling_water_temperature_in_degc": 26.0,
        "cooling_load_w": 0.5 * Q_NOM,
    }
    water = run(make_chiller(), **inputs)
    brine = run(make_chiller(**BRINE), **inputs)
    assert brine["drive_heat_w"] == pytest.approx(water["drive_heat_w"])
    ratio = (1000.0 * 4217.4) / (1030.0 * 4100.0)
    assert brine["drive_flow_rate_m3s"] == pytest.approx(water["drive_flow_rate_m3s"] * ratio)


def test_method_a_drive_heat_is_limited_by_a_small_drive_flow():
    """Method A: the drive outlet never falls below the zero-capacity temperature (71.4 °C)."""
    out = run(
        make_chiller(method="ghc1989"),
        drive_temperature_in_degc=95.0,
        drive_flow_rate_m3s=0.001,
        cooling_water_temperature_in_degc=27.0,
        cooling_load_w=Q_NOM,
    )
    t_zero = ghc1989.degf_to_degc(ghc1989.zero_capacity_temperature_degf())
    assert out["drive_temperature_out_degc"] == pytest.approx(t_zero, rel=1e-12)
    rho_cp = 1000.0 * 4217.4
    assert out["drive_heat_w"] == pytest.approx(rho_cp * 0.001 * (95.0 - t_zero), rel=1e-12)
    assert out["cooling_power_w"] == pytest.approx(out["drive_heat_w"] * out["cop"], rel=1e-12)
    assert out["cop"] == pytest.approx(ghc1989.cop(ghc1989.degc_to_degf(95.0)), rel=1e-12)
    assert any("limited by the drive flow" in w for w in out["validity_warnings"])


def drive_case(flow_m3h):
    """Run the physics-check case: 5 MW machine, 85 °C drive, 29.4 °C cooling water flow."""
    chiller = AbsorptionChiller()
    chiller.update_parameters({"nominal_capacity_w": 5.0e6})
    out = run(
        chiller,
        drive_temperature_in_degc=85.0,
        drive_flow_rate_m3s=flow_m3h / 3600.0,
        cooling_water_temperature_in_degc=RATING_COOLING_DEGC,
        cooling_water_flow_rate_m3s=0.30,
        cooling_load_w=5.0e6,
    )
    return chiller, out


def test_method_b_drive_flow_below_the_minimum_is_infeasible():
    """C_G < s_G/2: the outlet would be below the minimum generator temperature -> off.

    Physics-check case: 50 m³/h on a 5 MW machine gave 1.51 MW cooling with a 43 K drive
    drop to 41.6 °C, below t_G,min ≈ 47.7 °C. Minimum = s_G Q_nom / (2 ρ c_p) ≈ 69 m³/h.
    """
    chiller, out = drive_case(50.0)
    s_g = chiller.characteristic_coefficients()["s_g_per_k"] * 5.0e6
    minimum = 0.5 * s_g / (1000.0 * 4217.4)
    assert out["minimum_drive_flow_rate_m3s"] == pytest.approx(minimum, rel=1e-12)
    assert minimum * 3600.0 == pytest.approx(69.3, abs=0.1)
    assert out["cooling_power_w"] == out["drive_heat_w"] == out["available_cooling_w"] == 0.0
    assert out["drive_temperature_out_degc"] == 85.0
    assert any("below the minimum" in w for w in out["validity_warnings"])


@pytest.mark.parametrize("flow_m3h", [70.0, 100.0, 400.0])
def test_method_b_drive_outlet_stays_above_the_minimum_generator_temperature(flow_m3h):
    """C_G >= s_G/2: drive outlet >= t_G,min = ΔΔt_min,G + (1+B) t̄_AC - B t̄_E."""
    chiller, out = drive_case(flow_m3h)
    c = chiller.characteristic_coefficients()
    t_ac = 0.5 * (
        out["cooling_water_temperature_in_degc"] + out["cooling_water_temperature_out_degc"]
    )
    t_e = 0.5 * (
        out["chilled_water_temperature_in_degc"] + out["chilled_water_temperature_out_degc"]
    )
    t_g_min = c["ddt_min_g_k"] + 2.15 * t_ac - 1.15 * t_e
    assert out["cooling_power_w"] > 0.0
    assert out["drive_temperature_out_degc"] >= t_g_min
    assert out["drive_delta_t_k"] == pytest.approx(
        out["drive_heat_w"] / (1000.0 * 4217.4 * flow_m3h / 3600.0), rel=1e-12
    )


def test_method_a_warns_below_the_rating_hot_water_range():
    """Method A between the zero-capacity temperature and 180 °F is flagged as extrapolated."""
    out = run(
        make_chiller(method="ghc1989"),
        drive_temperature_in_degc=78.0,
        cooling_water_temperature_in_degc=RATING_COOLING_DEGC,
        chilled_water_temperature_out_degc=RATING_CHILLED_DEGC,
        chilled_water_temperature_in_degc=RATING_CHILLED_DEGC + 6.0,
        cooling_load_w=Q_NOM,
    )
    assert out["cooling_power_w"] > 0.0
    expected = (
        "ghc1989: hot water 172.4 °F below the rating range 180-240 °F "
        "(extrapolated towards zero capacity)"
    )
    assert out["validity_warnings"] == [expected]


def test_zero_load_downstream_of_full_free_cooling_is_allowed():
    """Zero load with return = supply (free cooling covers all) gives a zero output."""
    out = run(
        make_chiller(),
        drive_temperature_in_degc=90.0,
        cooling_water_temperature_in_degc=26.0,
        chilled_water_temperature_in_degc=20.0,
        cooling_load_w=0.0,
    )
    assert out["cooling_power_w"] == 0.0
    assert out["chilled_water_flow_rate_m3s"] == 0.0


@pytest.mark.parametrize(
    "parameters, inputs, message",
    [
        ({}, {"cooling_load_w": 1.0, "chilled_water_flow_rate_m3s": 0.1}, "exactly one"),
        ({}, {}, "exactly one"),
        ({"nominal_capacity_w": None}, {"cooling_load_w": 1.0}, "nominal_capacity_w"),
        ({"method": "magic"}, {"cooling_load_w": 1.0}, "method"),
        ({}, {"cooling_load_w": 1.0, "chilled_water_temperature_in_degc": 19.0}, "return"),
        ({}, {"cooling_load_w": -1.0}, ">= 0"),
        ({"drive_density_kg_m3": 0.0}, {"cooling_load_w": 1.0, "drive_flow_rate_m3s": 0.1}, "> 0"),
    ],
)
def test_invalid_inputs_raise(parameters, inputs, message):
    """Inconsistent or invalid inputs and parameters raise ValueError."""
    chiller = make_chiller(**parameters)
    base = {"drive_temperature_in_degc": 90.0, "cooling_water_temperature_in_degc": 26.0}
    with pytest.raises(ValueError, match=message):
        chiller.calculate_output({**base, **inputs})
