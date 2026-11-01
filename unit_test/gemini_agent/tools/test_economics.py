"""Tests for TL-14 economics / co2 / sensitivity (RC-10, RC-05, RC-08 validation tests)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from gemini_agent.schemas.common import GasHandling
from gemini_agent.schemas.config import load_config
from gemini_agent.schemas.results import KPISet
from gemini_agent.tools import economics as eco
from gemini_agent.tools.economics import (
    CapexRequest,
    Co2Request,
    CostBook,
    EconomicsRequest,
    HexDuty,
)
from unit_test.gemini_agent.tools.annual_factory import make_annual

CEPCI_1986_TO_2023 = 797.9 / 318.4
BOILER_USD_PER_KW = 270.0 * 797.9 / 576.1  # RC-08 2014 $ × CEPCI (gas_boiler price_year note)


@pytest.fixture(scope="module")
def book() -> CostBook:
    """Fixture: book."""
    return CostBook.load()


# --------------------------------------------------------------------------- accessor (RC-10)
def test_accessor_reproduces_rc10_sourced_conversions(book: CostBook) -> None:
    """RC-10 validation: every sourced value converted through the one accessor."""
    assert book.usd_value("hthp_capex_usd_per_kw_th") == pytest.approx(1356.0, abs=1.0)
    assert book.usd_value("chp_capex_usd_per_kw_e") == pytest.approx(1333.0, abs=2.0)
    assert book.usd_value("gas_price_usd_per_mwh") == pytest.approx(71.4, abs=0.1)
    assert book.usd("gas_price_usd_per_mwh").unit == "USD/MWh_LHV"
    assert book.usd_value("co2_price_usd_per_t") == pytest.approx(89.74, abs=0.01)
    assert book.usd_value("cooling_tower_capex_usd_per_kw_th") == pytest.approx(87.2, abs=0.1)
    assert book.usd_value("free_cooling_hex_capex_usd_per_kw_th") == pytest.approx(296.0, abs=1.0)
    assert book.usd_value("electricity_price_usd_per_mwh") == pytest.approx(133.6, abs=0.1)
    assert book.usd_value("electric_boiler_capex_usd_per_kw_th") == pytest.approx(79.1, abs=0.1)
    assert book.usd_value("electric_boiler_small_capex_usd_per_kw_th") == pytest.approx(
        174.7, abs=0.1
    )
    assert book.usd_value("plate_hex_base_aud") == pytest.approx(2000 * 0.63)
    assert book.usd("hthp_capex_usd_per_kw_th").unit == "USD/kW_th"


def test_hawaii_eq9_and_vendor_total(book: CostBook) -> None:
    """RC-10: Eq. 9 at 8,310 ton = 4,322,600 $ (2002 $) ±100, within 1 % of 4,299,000 $."""
    factor = book.usd("free_cooling_hex_cost_function_usd").factor
    assert factor == pytest.approx(797.9 / 395.6)
    native = eco.free_cooling_hex_equipment_cost_usd(book, 8310.0) / factor
    assert native == pytest.approx(4_322_600.0, abs=100.0)
    assert abs(native - 4_299_000.0) / 4_299_000.0 < 0.01


def test_sensitivity_bounds_are_max_of_30pct_and_stated_range(book: CostBook) -> None:
    """DB-35 / ECO-04; usd_range is used as is (never converted again)."""
    gas = book.usd("gas_price_usd_per_mwh")
    assert (gas.sensitivity_low, gas.sensitivity_high) == (40.0, 105.0)
    tower = book.usd("cooling_tower_capex_usd_per_kw_th")
    assert (tower.sensitivity_low, tower.sensitivity_high) == (40.0, 170.0)
    hthp = book.usd("hthp_capex_usd_per_kw_th")
    assert hthp.sensitivity_low == pytest.approx(1356.0 * 0.7)
    assert hthp.sensitivity_high == pytest.approx(1356.0 * 1.3)
    rate = book.usd("discount_rate_frac")
    assert rate.sensitivity_low == pytest.approx(0.056)
    assert rate.sensitivity_high == pytest.approx(0.112)


def test_every_item_reads_through_the_accessor(book: CostBook) -> None:
    """Check every item reads through the accessor."""
    for name in book.names():
        cost = book.usd(name)
        assert cost.factor > 0
    fit = book.usd("well_cost_smooth_fit").value
    assert isinstance(fit, dict) and fit["b_per_m"] == 0.000366  # non-money key untouched
    chiller = book.usd("absorption_chiller_plant_cost_function_usd")
    assert chiller.verify and chiller.factor == pytest.approx(CEPCI_1986_TO_2023)


def test_accessor_refuses_non_usd_reporting(config_copy: Path, edit_yaml) -> None:
    """Check accessor refuses non usd reporting."""
    edit_yaml(config_copy / "cost_assumptions.yaml", lambda d: d.update(reporting_currency="EUR"))
    with pytest.raises(ValueError, match="USD"):
        CostBook.load(config_copy)


def test_scaled_book_multiplies_converted_value(book: CostBook) -> None:
    """Check scaled book multiplies converted value."""
    scaled = book.scaled("gas_price_usd_per_mwh", 1.5)
    assert scaled.usd_value("gas_price_usd_per_mwh") == pytest.approx(
        1.5 * book.usd_value("gas_price_usd_per_mwh")
    )
    with pytest.raises(ValueError):
        book.scaled("eur_to_usd", 1.1)


# --------------------------------------------------------------------------- cost functions
@pytest.mark.parametrize(
    ("depth", "expected", "tol", "across"),
    [
        (2500, 3.45, 0.05, False),
        (2750, 4.7, 0.1, True),
        (3000, 6.0, 1e-9, False),
        (3200, 6.3, 0.1, False),
    ],
)
def test_well_cost_table(book, depth, expected, tol, across) -> None:
    """RC-10 validation: table nodes, interpolation across the casing step is flagged."""
    well = eco.well_cost_musd(book, depth)
    assert well.cost_musd == pytest.approx(expected, abs=tol)
    assert well.across_casing_step is across


def test_well_cost_smooth_fit_and_limits(book: CostBook) -> None:
    """Check well cost smooth fit and limits."""
    assert eco.well_cost_smooth_fit_musd(book, 2500.0) == pytest.approx(3.6, abs=0.1)
    with pytest.raises(ValueError):
        eco.well_cost_smooth_fit_musd(book, 2600.0)
    with pytest.raises(ValueError):
        eco.well_cost_musd(book, 900.0)
    deviated = eco.well_cost_musd(book, 2500.0, deviated=True).cost_musd
    assert deviated == pytest.approx(3.45 * 1.125)
    assert eco.well_cost_musd(book, 2500.0, diameter="small").cost_musd == pytest.approx(2.9)


def test_plate_hex_cost(book: CostBook) -> None:
    """RC-10 validation: HEX cost(25 kg/s, 10 K) = 14,000 AUD; titanium +50 %."""
    plain = eco.plate_hex_cost_usd(book, 25.0, 10.0, titanium=False)
    assert plain / 0.63 == pytest.approx(14_000.0)
    assert eco.plate_hex_cost_usd(book, 25.0, 10.0) == pytest.approx(1.5 * plain)


def test_chiller_cost_functions(book: CostBook) -> None:
    """RC-05 Cost: 1986 $ functions × CEPCI; units of ≤ 1000 nominal tons."""
    duty_500_ton = 500 * eco.KW_PER_TON / 1000
    electric = eco.chiller_plant_cost_usd(book, "electric", duty_500_ton)
    assert electric.cost_usd == pytest.approx((107_000 + 267 * 400) * CEPCI_1986_TO_2023)
    absorption = eco.chiller_plant_cost_usd(book, "absorption", duty_500_ton, capacity_fraction=0.8)
    native = 83_000 + 252 * 400 / 0.8 + 41_000 + 170 * 400
    assert absorption.cost_usd == pytest.approx(native * CEPCI_1986_TO_2023)
    assert any("VERIFY" in w for w in absorption.warnings)
    ti = eco.chiller_plant_cost_usd(
        book, "absorption", duty_500_ton, capacity_fraction=0.8, titanium_tubes=True
    )
    machine = (83_000 + 252 * 400 / 0.8) * CEPCI_1986_TO_2023
    assert ti.cost_usd - absorption.cost_usd == pytest.approx(0.125 * machine)
    # 10 MW cooling peak (DB-19): 2843 ton electric -> 3 units; absorption F 0.65 -> 5 units
    assert eco.chiller_plant_cost_usd(book, "electric", 10.0).units == 3
    assert eco.chiller_plant_cost_usd(book, "absorption", 10.0, capacity_fraction=0.65).units == 5
    small = eco.chiller_plant_cost_usd(book, "electric", 0.2)
    assert any("extrapolated" in w for w in small.warnings)


def test_chiller_costs_within_rc10_benchmark_bands(book: CostBook) -> None:
    """Sanity (RC-10 Benchmarks): RC-05 × CEPCI vs EIA 2023 / DOE 2017 installed $/ton (×0.5–2)."""
    tons = 500.0
    duty = tons * eco.KW_PER_TON / 1000
    electric = eco.chiller_plant_cost_usd(book, "electric", duty).cost_usd / tons
    absorption = eco.chiller_plant_cost_usd(book, "absorption", duty).cost_usd / tons
    assert 0.5 < electric / 750.0 < 2.0  # EIA centrifugal 740–760 $2022/ton
    assert 0.5 < absorption / 2050.0 < 2.0  # DOE single-stage LiBr 1,800–2,300 $/ton


# --------------------------------------------------------------------------- capex
def _capex(**overrides) -> CapexRequest:
    """Fixture:  capex."""
    data = {"candidate_id": "C2-test", "doublets": 1, "depth_m": 2500.0, "trunk_pipe_length_m": 0.0}
    data.update(overrides)
    return CapexRequest.model_validate(data)


def test_capex_lines_and_services(book: CostBook) -> None:
    """Check capex lines and services."""
    request = _capex(
        doublets=2,
        hthp_heating_mw=12.0,
        gas_boiler_mw=10.0,
        electric_chiller_mw=4.0,
        free_cooling_mw=6.0,
        cooling_tower_mw=6.0,
        plate_hexes=[HexDuty(name="hex1", mass_flow_kg_s=85.8, delta_t_k=28.0)],
    )
    capex = eco.capex_breakdown(request, book)
    lines = {line.name: line for line in capex.lines}
    assert lines["wells"].cost_usd == pytest.approx(4 * 3.45e6)
    assert lines["wells"].service == "shared"
    assert lines["hthp"].cost_usd == pytest.approx(12_000 * 1356.0, rel=1e-6)
    assert lines["hthp"].replacement_interval_yr == 15.0
    assert lines["gas_boiler"].cost_usd == pytest.approx(10_000 * BOILER_USD_PER_KW)
    assert lines["free_cooling_hex"].service == "cool"
    assert capex.total_usd == pytest.approx(sum(capex.by_service_usd.values()))
    assert any("MISSING INPUT" in w and "pump" in w for w in capex.warnings)


def test_capex_validation(book: CostBook) -> None:
    """Check that CapexRequest refuses incomplete absorption, well and CHP inputs."""
    with pytest.raises(ValueError):
        _capex(absorption_chiller_mw=2.0)  # needs the capacity fraction
    with pytest.raises(ValueError):
        _capex(doublets=0)  # depth given without doublets
    with pytest.raises(ValueError):
        _capex(chp_electric_mw=1.0, chp_thermal_mw=2.0)


def test_chp_fraction_from_real_register(book: CostBook) -> None:
    """CHP-EL-FRAC (WP-03: 0.3333 = GEMINI chp.py 1/3) from config/assumptions.yaml is used."""
    register = load_config("assumptions")
    entry = register.get("CHP-EL-FRAC")
    assert entry.value == 0.3333 and entry.added_by == "WP-03"
    p_el, frac, warnings = eco.chp_electric_capacity_mw(2.0, None, register)
    assert frac == 0.3333
    assert p_el == pytest.approx(2.0 * 0.3333 / 0.6667)
    assert warnings == []
    # an explicit DEA 0.50 is overridden by the register and warned about
    _, frac, warnings = eco.chp_electric_capacity_mw(2.0, 0.5, register)
    assert frac == 0.3333 and "ignored" in warnings[0]
    # end to end: economics() reads the real register for a CHP sized by its heat output
    request = EconomicsRequest(capex=_capex(chp_thermal_mw=2.0), energy_escalation_real_frac=0.0)
    result = eco.economics(request, make_annual(heat_by_source_mw={"chp": 2.0}), book=book)
    chp = next(line for line in result.data.capex.lines if line.name == "chp")
    assert chp.cost_usd == pytest.approx(p_el * 1000 * book.usd_value("chp_capex_usd_per_kw_e"))
    assert any("f = 0.3333" in w for w in result.warnings)


def test_chp_fraction_without_or_with_bad_register_entry(
    book: CostBook, config_copy: Path, edit_yaml
) -> None:
    """No entry: the parameter is used, else ValueError; a non-numeric entry raises."""

    def drop_entry(data: dict) -> None:
        data["assumptions"] = [a for a in data["assumptions"] if a["id"] != "CHP-EL-FRAC"]

    edit_yaml(config_copy / "assumptions.yaml", drop_entry)
    register = load_config("assumptions", config_copy)
    with pytest.raises(ValueError, match="CHP-EL-FRAC"):
        eco.capex_breakdown(_capex(chp_thermal_mw=2.0), book, register)
    capex = eco.capex_breakdown(
        _capex(chp_thermal_mw=2.0, chp_electrical_fraction=0.5), book, register
    )
    chp = next(line for line in capex.lines if line.name == "chp")
    assert chp.cost_usd == pytest.approx(2.0 * 1000 * book.usd_value("chp_capex_usd_per_kw_e"))

    def bad_entry(data: dict) -> None:
        data["assumptions"].append(
            {
                "id": "CHP-EL-FRAC",
                "title": "t",
                "value": "half",
                "justification": "j",
                "source": "s",
                "sensitivity": None,
                "added_by": "WP-03",
            }
        )

    edit_yaml(config_copy / "assumptions.yaml", bad_entry)
    with pytest.raises(ValueError, match="CHP-EL-FRAC"):
        eco.chp_electric_capacity_mw(2.0, None, load_config("assumptions", config_copy))


def test_small_and_large_electric_boiler(book: CostBook) -> None:
    """Check small and large electric boiler."""
    small = eco.capex_breakdown(_capex(electric_boiler_mw=5.0), book)
    large = eco.capex_breakdown(_capex(electric_boiler_mw=20.0), book)
    get = {
        c: next(x for x in b.lines if x.name == "electric_boiler")
        for c, b in (("s", small), ("l", large))
    }
    assert get["s"].items == ["electric_boiler_small_capex_usd_per_kw_th"]
    assert get["l"].cost_usd == pytest.approx(20_000 * 79.1, rel=1e-3)


# --------------------------------------------------------------------------- carriers and CO2
def test_carrier_balance_netting_and_associated_gas() -> None:
    """Check carrier balance netting and associated gas."""
    lhv = 28.64  # MJ/Nm³ (DB-28)
    annual = make_annual(
        days=10,
        heat_by_source_mw={"hex": 10.0},
        electricity_by_consumer_mw={"esp": 1.0, "hthp": 1.0},
        electricity_generated_mw=0.5,
        gas_fuel_by_consumer_mw={"chp": 2.0},
        associated_gas_produced_nm3=4000.0,
    )
    bal = eco.carrier_balance(
        annual, gas_handling=GasHandling.DEGAS_USE, associated_gas_lhv_mj_per_nm3=lhv
    )
    assert bal.electricity_purchased_mwh == pytest.approx(10 * 1.5 * 24)
    assert bal.electricity_purchased_by_service_mwh["shared"] == pytest.approx(10 * 0.75 * 24)
    available = 10 * 4000.0 * lhv / 3600.0
    assert bal.associated_gas_available_mwh == pytest.approx(available)
    assert bal.associated_gas_burned_mwh == pytest.approx(min(available, 10 * 48.0))
    assert bal.grid_gas_mwh == pytest.approx(10 * 48.0 - bal.associated_gas_burned_mwh)
    reinject = eco.carrier_balance(
        annual, gas_handling=GasHandling.PRESSURISED_REINJECT, associated_gas_lhv_mj_per_nm3=lhv
    )
    assert reinject.associated_gas_available_mwh == 0.0
    assert reinject.grid_gas_mwh == pytest.approx(10 * 48.0)
    surplus = make_annual(
        days=2, electricity_by_consumer_mw={"esp": 1.0}, electricity_generated_mw=3.0
    )
    bal = eco.carrier_balance(surplus, gas_handling=None, associated_gas_lhv_mj_per_nm3=lhv)
    assert bal.electricity_purchased_mwh == 0.0
    assert bal.electricity_surplus_mwh == pytest.approx(2 * 2 * 24)
    assert any("not credited" in w for w in bal.warnings)


def test_co2_factors_and_report() -> None:
    """ECO-05 / RC-08: 56.6 kg/GJ grid gas, 68.6 ±0.5 kg/GJ associated gas, 0.3 kg/kWh power."""
    annual = make_annual(
        "C1-test",
        days=365,
        heat_by_source_mw={"boiler": 10.0},
        electricity_by_consumer_mw={"esp": 1.0},
        gas_fuel_by_consumer_mw={"boiler": 2.0},
        associated_gas_produced_nm3=2000.0,
    )
    result = eco.co2_emissions(
        Co2Request(gas_handling=GasHandling.DEGAS_USE, c0_co2_t_per_yr=50_000.0), annual
    )
    report = result.data
    assert report.factors.grid_gas_kg_per_gj == 56.6
    assert report.factors.grid_gas_alternative_kg_per_gj == [51.53]
    assert report.factors.associated_gas_kg_per_gj == pytest.approx(68.6, abs=0.5)
    assert report.factors.electricity_kg_per_kwh == 0.3
    assoc_mwh = 365 * 2000.0 * report.factors.associated_gas_lhv_mj_per_nm3 / 3600.0
    fuel_mwh = 365 * 48.0
    expected_direct = (fuel_mwh - assoc_mwh) * 3.6 * 56.6 / 1000 + assoc_mwh * 3.6 * (
        report.factors.associated_gas_kg_per_gj
    ) / 1000
    assert report.direct_t == pytest.approx(expected_direct)
    assert report.electricity_t == pytest.approx(365 * 24 * 0.3)
    assert report.reduction_vs_c0_frac == pytest.approx(1 - report.total_t / 50_000.0)
    low = eco.co2_emissions(
        Co2Request(gas_handling=GasHandling.DEGAS_USE, grid_gas_kg_per_gj=51.53), annual
    ).data
    assert low.grid_gas_t == pytest.approx(report.grid_gas_t * 51.53 / 56.6)
    assert len(result.summary) <= 300


def test_flared_surplus_counts_as_co2() -> None:
    """Check flared surplus counts as co2."""
    annual = make_annual(days=1, associated_gas_produced_nm3=24_000.0)
    report = eco.co2_emissions(Co2Request(gas_handling=GasHandling.DEGAS_USE), annual).data
    assert report.associated_gas_flared_t > 0
    assert report.direct_by_service_t["shared"] == pytest.approx(report.associated_gas_flared_t)


# --------------------------------------------------------------------------- levelised cost
def _constant_case(**capex_overrides) -> tuple[EconomicsRequest, object]:
    """Fixture:  constant case."""
    request = EconomicsRequest(
        capex=_capex(gas_boiler_mw=10.0, **capex_overrides),
        gas_handling=GasHandling.PRESSURISED_REINJECT,
        energy_escalation_real_frac=0.0,
    )
    annual = make_annual(
        heat_by_source_mw={"hex": 10.0},
        electricity_by_consumer_mw={"esp": 1.0},
        gas_fuel_by_consumer_mw={"boiler": 2.0},
    )
    return request, annual


def test_lcoh_constant_annuity_matches_hand_calculation(book: CostBook) -> None:
    """RC-10 validation: LCOH of a constant annuity case = hand calculation (±0.1 %)."""
    request, annual = _constant_case()
    report = eco.economics(request, annual, book=book).data
    capex = 2 * 3.45e6 + 10_000 * BOILER_USD_PER_KW
    heat = 365 * 240.0
    fuel = 365 * 48.0
    co2_t = fuel * 3.6 * 56.6 / 1000
    energy = 365 * 24 * 133.566 + fuel * 71.4136 + co2_t * 89.7446
    for period in (30, 20):
        r = 0.08
        crf = r * (1 + r) ** period / ((1 + r) ** period - 1)
        hand = (capex * crf + 0.015 * capex + energy) / heat
        lv = next(x for x in report.levelised if x.analysis_period_yr == period)
        assert lv.lcoh_usd_per_mwh == pytest.approx(hand, rel=1e-3)
    assert report.lcoh_fcr_usd_per_mwh == pytest.approx(report.levelised[0].lcoh_usd_per_mwh)
    assert report.levelised[0].analysis_period_yr == 30
    assert report.levelised[0].lcoc_usd_per_mwh is None


def test_replacements_raise_lcoh(book: CostBook) -> None:
    """Check replacements raise lcoh."""
    request, annual = _constant_case(hthp_heating_mw=5.0, pump_capex_usd_per_doublet=0.5e6)
    report = eco.economics(request, annual, book=book).data
    lv30 = report.levelised[0]
    capex = report.capex
    r = 0.08
    pv_repl = 5_000 * 1356.0 * (1 + r) ** -15 + sum(0.5e6 * (1 + r) ** -t for t in (7, 14, 21, 28))
    no_repl = capex.total_usd + sum(
        (0.015 * capex.total_usd + report.opex.total_usd - report.opex.om_usd) * (1 + r) ** -t
        for t in range(1, 31)
    )
    assert lv30.pv_heat_cost_usd == pytest.approx(no_repl + pv_repl, rel=1e-6)
    assert report.lcoh_fcr_usd_per_mwh < lv30.lcoh_usd_per_mwh  # FCR form has no replacements


def test_shared_costs_split_by_energy(book: CostBook) -> None:
    """Check shared costs split by energy."""
    request = EconomicsRequest(
        capex=_capex(electric_chiller_mw=4.0), gas_handling=GasHandling.PRESSURISED_REINJECT
    )
    annual = make_annual(
        heat_by_source_mw={"hex": 9.0},
        cool_by_source_mw={"chiller": 3.0},
        electricity_by_consumer_mw={"esp": 1.0, "electric_chiller": 0.5},
    )
    report = eco.economics(request, annual, book=book).data
    lv = report.levelised[0]
    assert lv.heat_share_of_shared_frac == pytest.approx(0.75)
    assert lv.pv_heat_cost_usd + lv.pv_cool_cost_usd == pytest.approx(
        sum(lv.pv_cost_by_service_usd.values())
    )
    assert lv.lcoc_usd_per_mwh is not None and lv.lcoc_usd_per_mwh > 0
    assert report.carriers.electricity_purchased_by_service_mwh["cool"] == pytest.approx(365 * 12.0)


def test_economics_fills_kpis_and_benchmarks(book: CostBook) -> None:
    """Check economics fills kpis and benchmarks."""
    request, annual = _constant_case()
    annual = annual.model_copy(update={"kpis": KPISet(candidate_id="C2-test", unmet_heating_mwh=0)})
    result = eco.economics(request, annual, book=book)
    kpis = result.data.kpis
    assert kpis.unmet_heating_mwh == 0  # existing fields kept
    assert kpis.currency == "USD"
    assert kpis.capex_million == pytest.approx(result.data.capex.total_usd / 1e6)
    assert kpis.lcoh_per_mwh == result.data.levelised[0].lcoh_usd_per_mwh
    assert kpis.lcoh_20yr_per_mwh == result.data.levelised[1].lcoh_usd_per_mwh
    assert kpis.co2_t_per_yr == pytest.approx(result.data.co2.total_t)
    assert kpis.grid_gas_fuel_mwh == pytest.approx(365 * 48.0)
    cap = next(b for b in result.data.benchmarks if b.name.startswith("heat_price_cap"))
    assert cap.benchmark_usd_per_mwh == pytest.approx(138.0, abs=0.5)  # RC-10 ACM 2026 cap
    assert cap.below_benchmark is (kpis.lcoh_per_mwh < cap.benchmark_usd_per_mwh)
    assert len(result.summary) <= 300 and "ACM" in result.summary
    with pytest.raises(ValueError):
        eco.economics(request, make_annual("other"), book=book)


def test_classify_service() -> None:
    """Check classify service."""
    assert eco.classify_service("electric_chiller") == "cool"
    assert eco.classify_service("hthp_compressor") == "heat"
    assert eco.classify_service("heat_pump") == "heat"
    assert eco.classify_service("esp_1") == "shared"
    assert eco.classify_service("injection_pump") == "shared"
    assert eco.classify_service("mystery") is None
    assert eco.classify_service("mystery", {"mystery": "cool"}) == "cool"


# --------------------------------------------------------------------------- sensitivity
def test_tornado_is_sorted_and_uses_stated_bounds(book: CostBook) -> None:
    """Check tornado is sorted and uses stated bounds."""
    request, annual = _constant_case(hthp_heating_mw=5.0)
    result = eco.cost_sensitivity(request, annual, book=book)
    bars = result.data.bars
    assert 1 <= len(bars) <= 8
    assert [b.swing for b in bars] == sorted((b.swing for b in bars), reverse=True)
    by_name = {b.input_name: b for b in bars}
    gas = by_name["gas_price_usd_per_mwh"]
    assert (gas.low_value, gas.high_value) == (40.0, 105.0)
    assert gas.kpi_at_low < gas.kpi_base < gas.kpi_at_high
    base_lcoh = eco.economics(request, annual, book=book).data.levelised[0].lcoh_usd_per_mwh
    assert result.data.kpi_base == pytest.approx(base_lcoh)
    names = {b.input_name for b in bars}
    assert not names & eco.CONVERSION_ITEMS
    assert len(result.summary) <= 300


def test_tornado_escalation_bar_and_lcoc(book: CostBook) -> None:
    """Check tornado escalation bar and lcoc."""
    request = EconomicsRequest(capex=_capex(electric_chiller_mw=4.0))
    annual = make_annual(
        heat_by_source_mw={"hex": 9.0},
        cool_by_source_mw={"chiller": 3.0},
        electricity_by_consumer_mw={"esp": 1.0, "electric_chiller": 0.5},
    )
    report = eco.cost_sensitivity(request, annual, kpi="lcoc", top_n=50, book=book).data
    esc = next(b for b in report.bars if b.input_name == "energy_escalation_real_frac")
    assert esc.low_value == pytest.approx(1.03 / 1.025 - 1)
    assert esc.high_value == pytest.approx(1.07 / 1.025 - 1)
    assert esc.kpi_at_high > esc.kpi_at_low
    assert math.isfinite(report.kpi_base)


# --------------------------------------------------------------------------- review regressions
def test_reused_book_does_not_leak_items_between_candidates(book: CostBook) -> None:
    """A shared CostBook must not carry 'used' items from one candidate to the next."""
    shared = CostBook.load()
    chiller = EconomicsRequest(
        capex=_capex(absorption_chiller_mw=2.0, absorption_capacity_fraction=0.7)
    )
    eco.economics(chiller, make_annual(heat_by_source_mw={"hex": 5.0}), book=shared)
    request, annual = _constant_case()
    result = eco.economics(request, annual, book=shared)
    names = {item.name for item in result.data.cost_items}
    assert "absorption_chiller_plant_cost_function_usd" not in names
    assert not any("VERIFY" in w for w in result.warnings)
    bars = eco.cost_sensitivity(request, annual, book=shared, top_n=50).data.bars
    assert "absorption_chiller_plant_cost_function_usd" not in {b.input_name for b in bars}


def test_associated_gas_needs_gas_handling() -> None:
    """DB-36: degassed gas must not vanish silently when gas_handling is unset."""
    annual = make_annual(days=2, associated_gas_produced_nm3=1000.0)
    with pytest.raises(ValueError, match="gas_handling"):
        eco.co2_emissions(Co2Request(), annual)


def test_default_escalation_is_real_rate_from_config(book: CostBook) -> None:
    """ECO-REAL: base = (1.05/1.025) − 1; the tornado bar brackets the base."""
    assert eco.real_energy_escalation(book) == pytest.approx(1.05 / 1.025 - 1)
    request, annual = _constant_case()
    flat = eco.economics(request, annual, book=book).data.levelised[0]
    default = request.model_copy(update={"energy_escalation_real_frac": None})
    escalated = eco.economics(default, annual, book=book).data.levelised[0]
    assert escalated.energy_escalation_real_frac == pytest.approx(0.02439, abs=1e-5)
    assert escalated.lcoh_usd_per_mwh > flat.lcoh_usd_per_mwh
    bars = eco.cost_sensitivity(default, annual, book=book, top_n=50).data.bars
    esc = next(b for b in bars if b.input_name == "energy_escalation_real_frac")
    assert esc.low_value < esc.base_value < esc.high_value
    assert esc.kpi_at_low < esc.kpi_base < esc.kpi_at_high


def test_shared_costs_follow_brine_heat_drawn(book: CostBook) -> None:
    """ECO-ALLOC: free cooling / electric chillers draw no brine, so wells go to heat."""
    request = EconomicsRequest(
        capex=_capex(electric_chiller_mw=4.0), energy_escalation_real_frac=0.0
    )
    days = {
        "heat_by_source_mw": {"hex": 9.0},
        "cool_by_source_mw": {"chiller": 3.0},
        "electricity_by_consumer_mw": {"esp": 1.0, "electric_chiller": 0.5},
    }
    no_brine = eco.economics(request, make_annual(**days), book=book).data.levelised[0]
    assert no_brine.allocation_basis == "delivered energy"
    brine = eco.economics(
        request, make_annual(brine_heat_drawn_mw={"hex1": 9.0}, **days), book=book
    ).data.levelised[0]
    assert brine.allocation_basis == "brine heat drawn"
    assert brine.heat_share_of_shared_frac == 1.0
    assert brine.lcoc_usd_per_mwh < no_brine.lcoc_usd_per_mwh
    absorption = eco.economics(
        request,
        make_annual(brine_heat_drawn_mw={"hex1": 9.0, "absorption_drive": 3.0}, **days),
        book=book,
    ).data.levelised[0]
    assert absorption.heat_share_of_shared_frac == pytest.approx(0.75)
    total = sum(brine.pv_cost_by_service_usd.values())
    assert brine.lcoe_combined_usd_per_mwh == pytest.approx(
        total / ((9.0 + 3.0) * 24 * 365 * sum(1.08**-t for t in range(1, 31)))
    )


def test_multiplier_sensitivity_on_premium(book: CostBook) -> None:
    """ECO-SENS-MULT: ±30 % on the premium; the stated range still widens the bounds."""
    deviated = book.usd("well_deviated_multiplier")
    assert deviated.sensitivity_low == pytest.approx(1.0875)
    assert deviated.sensitivity_high == pytest.approx(1.1625)
    titanium = book.usd("plate_hex_titanium_multiplier")
    assert (titanium.sensitivity_low, titanium.sensitivity_high) == (
        pytest.approx(1.35),
        pytest.approx(1.65),
    )


def test_replacement_years_round_half_up(book: CostBook) -> None:
    """Non-integer intervals: 7.5 yr ⇒ years 8, 15, 23 (30 excluded)."""
    capex = eco.CapexBreakdown(
        lines=[
            eco.CapexLine(
                name="x", items=[], service="heat", cost_usd=1.0, replacement_interval_yr=7.5
            )
        ],
        total_usd=1.0,
        by_service_usd={"heat": 1.0, "cool": 0.0, "shared": 0.0},
    )
    carriers = eco.carrier_balance(
        make_annual(days=1, heat_by_source_mw={"hex": 1.0}),
        gas_handling=None,
        associated_gas_lhv_mj_per_nm3=28.64,
    )
    opex = eco.AnnualOpex(
        by_service_usd={"heat": 0.015, "cool": 0.0, "shared": 0.0},
        om_usd=0.015,
        electricity_usd=0.0,
        grid_gas_usd=0.0,
        co2_usd=0.0,
        total_usd=0.015,
    )
    lv = eco.levelised_cost(capex, opex, carriers, book, analysis_period_yr=30)
    om = sum(0.015 * 1.08**-t for t in range(1, 31))
    replacements = sum(1.08**-t for t in (8, 15, 23))
    assert lv.pv_heat_cost_usd == pytest.approx(1.0 + om + replacements)
