"""Unit tests for gemini_agent.schemas.config and the YAML files in config/.

Includes the RC-07 validation tests that concern the climate *data* (Stull wet-bulb column,
annual mean) and the RC-10 table nodes; the climate/economics *tools* are tested in WP-04/WP-08.
"""

import math
import shutil

import pandas as pd
import pytest
import yaml
from pydantic import ValidationError

from gemini_agent.schemas.config import (
    CONFIG_MODELS,
    REPO_ROOT,
    AssumptionsRegister,
    ClimateConfig,
    CostAssumptions,
    CostItem,
    DesignBasis,
    HardLimits,
    LLMConfig,
    ModelBuilderCallSettings,
    load_all_configs,
    load_config,
)


def _stull_wet_bulb_degc(t_degc, rh_pct):
    """Stull (2011) wet-bulb as written on RC-07 (test oracle only)."""
    return (
        t_degc * math.atan(0.151977 * math.sqrt(rh_pct + 8.313659))
        + math.atan(t_degc + rh_pct)
        - math.atan(rh_pct - 1.676331)
        + 0.00391838 * rh_pct**1.5 * math.atan(0.023101 * rh_pct)
        - 4.686035
    )


@pytest.fixture(scope="module")
def configs():
    """All config files, loaded once per module."""
    return load_all_configs(REPO_ROOT / "config")


def test_every_yaml_loads_into_its_schema(configs):
    """Each config/*.yaml validates against its Pydantic model."""
    assert set(configs) == set(CONFIG_MODELS)
    for name, model in configs.items():
        assert isinstance(model, CONFIG_MODELS[name])


def test_every_config_round_trips_through_json(configs):
    """Configs survive dump → validate (run manifests, memory store, tool boundaries)."""
    for name, model in configs.items():
        again = type(model).model_validate_json(model.model_dump_json())
        assert again == model, name
    wells = configs["cost_assumptions"].items["well_cost_large_diameter_musd"]
    assert CostItem.model_validate_json(wells.model_dump_json()).value[2500] == 3.45


def test_typed_loader_returns_schema_instances():
    """load_config returns the schema type for each known file name."""
    assert isinstance(load_config("design_basis"), DesignBasis)
    assert isinstance(load_config("cost_assumptions"), CostAssumptions)


def test_design_basis_values(configs):
    """DB-01…DB-19 as in spec §2.1."""
    db = configs["design_basis"]
    assert isinstance(db, DesignBasis)
    assert db.reservoir.mid_depth_m == 2500.0
    assert db.reservoir.gross_thickness_m == 100.0
    assert db.reservoir.geothermal_gradient_k_per_km == 30.0
    assert db.reservoir.surface_temperature_degc == 12.0
    assert db.reservoir.pressure_barg == 250.0
    assert db.reservoir.permeability_md == 200.0
    assert db.brine.density_kg_m3 == 1030.0
    assert db.brine.viscosity_pa_s == pytest.approx(0.40e-3)
    assert db.brine.gas_liquid_ratio_nm3_per_m3 == 0.8
    assert (db.doublets.count_min, db.doublets.count_max) == (1, 3)
    assert (db.doublets.flow_min_active_m3h, db.doublets.flow_max_m3h) == (50.0, 300.0)
    assert db.doublets.brine_cooling_max_k == 40.0
    assert db.doublets.pump_hydraulic_efficiency_frac == 0.70
    assert db.doublets.operation_days_per_yr == 330
    assert db.heating.supply_temperature_degc == 100.0
    assert db.cooling.supply_temperature_degc == 20.0


@pytest.mark.parametrize("side", ["heating", "cooling"])
def test_design_basis_matches_demand_csv(configs, side):
    """Peak, peak date, annual energy and non-zero days agree with the CSVs (DB-18, DB-19)."""
    basis = getattr(configs["design_basis"], side)
    frame = pd.read_csv(basis.csv_path(), parse_dates=[basis.csv_date_column])
    values = frame[basis.csv_value_column]
    assert len(frame) == 365
    assert values.max() == pytest.approx(basis.peak_mw)
    assert frame.loc[values.idxmax(), basis.csv_date_column].date() == basis.peak_date
    assert values.sum() * 24.0 / 1000.0 == pytest.approx(basis.annual_gwh, abs=0.01)
    assert int((values > 0).sum()) == basis.nonzero_days


def test_limits_agree_with_design_basis(configs):
    """config/limits.yaml repeats DB-10…17 — the two files must never drift apart."""
    lim, dbl = configs["limits"], configs["design_basis"].doublets
    assert isinstance(lim, HardLimits)
    assert (lim.doublet_count_min, lim.doublet_count_max) == (dbl.count_min, dbl.count_max)
    assert lim.flow_min_active_m3h == dbl.flow_min_active_m3h
    assert lim.flow_max_m3h == dbl.flow_max_m3h
    assert lim.brine_cooling_max_k == dbl.brine_cooling_max_k
    assert lim.pump_hydraulic_efficiency_frac == dbl.pump_hydraulic_efficiency_frac
    assert lim.injection_equals_production is dbl.injection_equals_production is True
    assert lim.heating_supply_degc == configs["design_basis"].heating.supply_temperature_degc
    assert lim.cooling_supply_degc == configs["design_basis"].cooling.supply_temperature_degc
    assert lim.ch4_venting_allowed is False


def test_assumptions_register_seeded_with_db30_to_db40(configs):
    """Register holds DB-30…DB-40 (spec defines no DB-39) with all required fields."""
    reg = configs["assumptions"]
    assert isinstance(reg, AssumptionsRegister)
    ids = {a.id for a in reg.assumptions}
    expected = {
        "DB-30",
        "DB-31",
        "DB-32",
        "DB-33",
        "DB-34a",
        "DB-34b",
        "DB-34c",
        "DB-34d",
        "DB-34-RH",
        "DB-35",
        "DB-36",
        "DB-37",
        "DB-38",
        "DB-40",
    }
    assert expected <= ids
    assert reg.get("DB-31").value == 55.0
    assert reg.get("DB-32").value == 26.0
    assert reg.get("DB-33").value == 4100.0
    assert reg.get("DB-37").value == 0.3
    assert reg.get("DB-38").value == {
        "skin": 0.0,
        "wellbore_radius_m": 0.108,
        "drainage_radius_m": 1000.0,
    }
    assert reg.get("DB-36").value["venting_allowed"] is False
    for item in reg.assumptions:
        assert item.justification and item.source and item.added_by
    with pytest.raises(KeyError):
        reg.get("DB-99")


def test_assumptions_register_rejects_duplicates():
    """Duplicate ids are a validation error."""
    entry = {
        "id": "DB-31",
        "title": "t",
        "value": 1,
        "justification": "j",
        "source": "s",
        "sensitivity": None,
        "added_by": "WP-00",
    }
    with pytest.raises(ValidationError, match="duplicate"):
        AssumptionsRegister(assumptions=[entry, entry])


def test_climate_wet_bulb_column_reproduces_stull(configs):
    """RC-07: Stull(20 °C, 50 %) = 13.7 ±0.1; monthly T_wb reproduced ±0.1; annual mean ≈ 10.5."""
    clim = configs["climate_nl"]
    assert isinstance(clim, ClimateConfig)
    assert _stull_wet_bulb_degc(20.0, 50.0) == pytest.approx(13.7, abs=0.1)
    m = clim.monthly
    for t_db, rh, t_wb in zip(
        m.dry_bulb_degc, m.relative_humidity_pct, m.wet_bulb_degc, strict=True
    ):
        assert _stull_wet_bulb_degc(t_db, rh) == pytest.approx(t_wb, abs=0.1)
    assert sum(m.dry_bulb_degc) / 12 == pytest.approx(clim.annual_mean_degc, abs=0.05)
    assert m.relative_humidity_pct == configs["assumptions"].get("DB-34-RH").value
    assert (clim.design_point.dry_bulb_degc, clim.design_point.wet_bulb_degc) == (25.0, 18.4)
    assert (clim.warm_case.dry_bulb_degc, clim.warm_case.wet_bulb_degc) == (30.0, 21.0)
    assert clim.interpolation.anchor_day_of_month == 15 and clim.interpolation.wrap_year


SOURCED_ITEMS = (
    "hthp_capex_usd_per_kw_th",
    "chp_capex_usd_per_kw_e",
    "cooling_tower_capex_usd_per_kw_th",
    "electric_boiler_capex_usd_per_kw_th",
    "free_cooling_hex_capex_usd_per_kw_th",
    "electricity_price_usd_per_mwh",
    "gas_price_usd_per_mwh",
    "co2_price_usd_per_t",
)
"""The eight former ECO-04 '[A] placeholder' items, now from RC-10 'Sourced values'."""


def test_cost_assumptions_rc10_values(configs):
    """RC-10 table nodes and constants; every item carries unit, range, source, tag."""
    cost = configs["cost_assumptions"]
    assert isinstance(cost, CostAssumptions)
    items = cost.items
    wells = items["well_cost_large_diameter_musd"].value
    assert wells[2500] == 3.45 and wells[3000] == 6.0 and wells[5000] == 13.6
    assert items["well_cost_small_diameter_musd"].value[2500] == 2.9
    assert items["discount_rate_frac"].value == 0.08
    hex_cost_aud = (
        items["plate_hex_base_aud"].value + items["plate_hex_per_kgs_k_aud"].value * 25.0 * 10.0
    )
    assert hex_cost_aud == 14000.0  # RC-10 validation: HEX cost(25 kg/s, 10 K) = 14,000 AUD
    assert items["aud_to_usd"].value == 0.63
    assert cost.min_sensitivity_frac == 0.30
    for name, item in items.items():
        assert item.unit and item.source and item.range is not None, name


def test_no_cost_item_is_unset(configs):
    """RC-10 validation / WP-13 check: no null value; unset_placeholders() is empty."""
    cost = configs["cost_assumptions"]
    assert cost.unset_placeholders() == []
    assert all(item.value is not None and not item.placeholder for item in cost.items.values())


def test_sourced_items_carry_native_currency_year_and_source(configs):
    """Each former placeholder: native currency + price year, USD range, RC-10 source, tag L."""
    items = configs["cost_assumptions"].items
    for name in SOURCED_ITEMS:
        item = items[name]
        assert item.currency in {"EUR", "USD"}, name
        assert item.price_year is not None, name
        assert item.usd_range is not None, name
        assert item.source.startswith("RC-10 Sourced values"), name
        assert item.tag == "L", name
    escalated = {name for name in SOURCED_ITEMS if items[name].escalation_index == "cepci"}
    assert escalated == {
        "cooling_tower_capex_usd_per_kw_th",
        "free_cooling_hex_capex_usd_per_kw_th",
    }


def test_rc10_sourced_value_conversions(configs):
    """RC-10 'Sourced-value conversions' reproduced from the YAML native values (data check)."""
    items = configs["cost_assumptions"].items

    def val(name):
        return items[name].value

    fx = val("eur_to_usd")
    cepci = val("cepci_index")
    assert fx == 1.13
    # HTHP: 1.20 M EUR/MW x 1.13 = 1,356 $/kW_th (±1)
    assert val("hthp_capex_usd_per_kw_th") * 1000.0 * fx == pytest.approx(1356.0, abs=1.0)
    # CHP: 1.010 x 1.168 x 1.13 = 1.333 M$/MW_e (±0.002)
    chp_musd_per_mw = val("chp_capex_usd_per_kw_e") * val("dea_eur2020_to_eur2025") * fx
    assert chp_musd_per_mw == pytest.approx(1.333, abs=0.002)
    # Gas: 15.8011 EUR/GJ x 3.6 = 56.88 EUR/MWh_GCV, x 1.111 = 63.19 EUR/MWh_LHV,
    # x 1.13 = 71.4 $/MWh
    gas_gcv = val("gas_price_usd_per_mwh") * 3.6
    assert gas_gcv == pytest.approx(56.88, abs=0.01)
    assert gas_gcv * val("gas_gcv_to_lhv") == pytest.approx(63.19, abs=0.1)
    assert gas_gcv * val("gas_gcv_to_lhv") * fx == pytest.approx(71.4, abs=0.1)
    assert 35.17 / 31.65 == pytest.approx(val("gas_gcv_to_lhv"), abs=0.001)
    # CO2: 79.42 EUR/t x 1.13 = 89.74 $/t
    assert val("co2_price_usd_per_t") * fx == pytest.approx(89.74, abs=0.01)
    # Electricity: 118.2 EUR/MWh x 1.13 = 133.6 $/MWh; e-boiler 0.070 M EUR/MW = 79 $/kW_th
    assert val("electricity_price_usd_per_mwh") * fx == pytest.approx(133.6, abs=0.05)
    assert val("electric_boiler_capex_usd_per_kw_th") * 1000.0 * fx == pytest.approx(79.0, abs=0.5)
    # Cooling tower: 64 $2011/kW x 797.9/585.7 = 87.2 $/kW (±0.1)
    tower = val("cooling_tower_capex_usd_per_kw_th") * cepci[2023] / cepci[2011]
    assert tower == pytest.approx(87.2, abs=0.1)
    # Free-cooling HEX: 147 $2002/kW x 797.9/395.6 = 296 $/kW (±1)
    fc_hex = val("free_cooling_hex_capex_usd_per_kw_th") * cepci[2023] / cepci[2002]
    assert fc_hex == pytest.approx(296.0, abs=1.0)
    # Hawaii Eq. 9 at 8,310 ton = 4,322,600 $ (±100), within 1 % of the vendor total 4,299,000 $
    fn = val("free_cooling_hex_cost_function_usd")
    eq9_usd = fn["per_ton"] * 8310.0 + fn["fixed"]
    assert eq9_usd == pytest.approx(4_322_600.0, abs=100.0)
    assert eq9_usd == pytest.approx(4_299_000.0, rel=0.01)
    # CEPCI 1986 -> 2023 = 2.51, consistent with the [A] escalation factor 2.5
    assert cepci[2023] / cepci[1986] == pytest.approx(
        val("cepci_escalation_1986_to_2026"), abs=0.01
    )


def test_sourced_native_ranges_match_rc10_usd_ranges(configs):
    """Where both are given, native range x conversion = RC-10 USD range (±1 % of the bound)."""
    items = configs["cost_assumptions"].items
    fx = items["eur_to_usd"].value
    to_usd_per_kw = {
        "hthp_capex_usd_per_kw_th": 1000.0 * fx,
        "chp_capex_usd_per_kw_e": 1000.0 * fx * items["dea_eur2020_to_eur2025"].value,
        "electric_boiler_capex_usd_per_kw_th": 1000.0 * fx,
        "co2_price_usd_per_t": fx,
    }
    for name, factor in to_usd_per_kw.items():
        low, high = items[name].range
        usd_low, usd_high = items[name].usd_range
        assert low * factor == pytest.approx(usd_low, rel=0.01), name
        assert high * factor == pytest.approx(usd_high, rel=0.01), name


def test_cost_item_rules():
    """Null values only for placeholders; scalars need a range; low <= high (both ranges)."""
    with pytest.raises(ValidationError, match="placeholder"):
        CostItem(value=None, unit="USD", range=None, source="s", tag="L")
    with pytest.raises(ValidationError, match="range"):
        CostItem(value=1.0, unit="USD", range=None, source="s", tag="L")
    with pytest.raises(ValidationError, match="low"):
        CostItem(value=1.0, unit="USD", range=(2.0, 1.0), source="s", tag="L")
    with pytest.raises(ValidationError, match="usd_range"):
        CostItem(value=1.0, unit="EUR", range="text", usd_range=(2.0, 1.0), source="s", tag="L")
    with pytest.raises(ValidationError):
        CostItem(value=1.0, unit="GBP", range="text", currency="GBP", source="s", tag="L")
    with pytest.raises(ValidationError, match="price_year"):
        CostItem(value=1.0, unit="USD", range="t", escalation_index="cepci", source="s", tag="L")
    placeholder = CostItem(
        value=None, unit="USD", range=None, source="s", tag="A", placeholder=True
    )
    assert placeholder.value is None


def test_escalated_items_need_matching_cepci_year(configs):
    """Every CEPCI-escalated item's price year must be a key of cepci_index."""
    raw = configs["cost_assumptions"].model_dump()
    raw["items"]["cooling_tower_capex_usd_per_kw_th"]["price_year"] = 2012
    with pytest.raises(ValidationError, match="cepci_index"):
        CostAssumptions.model_validate(raw)
    del raw["items"]["cepci_index"]
    with pytest.raises(ValidationError, match="cepci_index"):
        CostAssumptions.model_validate(raw)


def test_llm_config(configs):
    """Spec §4.3: policy engine by default, Ollama endpoint, 64K context, two hardware profiles."""
    llm = configs["llm"]
    assert isinstance(llm, LLMConfig)
    assert llm.engine == "policy"
    assert llm.ollama.base_url == "http://localhost:11434/v1"
    assert llm.ollama.num_ctx == 65536
    assert {"16gb", "36gb"} <= set(llm.profiles)
    assert llm.active().orchestrator
    assert llm.temperatures.extraction == 0.0
    # L-1 follow-up: per-call limits for structured-output calls
    assert llm.calls.thinking is False
    assert llm.calls.max_output_tokens == 2048 and llm.calls.request_timeout_s == 300.0
    # WP-12 follow-up: the Model Builder coder is a long-answer role with its own limits
    builder = llm.calls.model_builder
    assert builder.thinking is False
    assert builder.max_output_tokens == 8192 and builder.request_timeout_s == 1200.0
    assert ModelBuilderCallSettings().model_dump() == builder.model_dump()  # yaml = defaults


def test_loader_errors_and_env_override(tmp_path, monkeypatch):
    """Unknown names, missing files and invalid content fail loudly; env var overrides the dir."""
    with pytest.raises(KeyError):
        load_config("nope")
    with pytest.raises(FileNotFoundError):
        load_config("limits", tmp_path)
    shutil.copy(REPO_ROOT / "config" / "limits.yaml", tmp_path / "limits.yaml")
    monkeypatch.setenv("GEOAGENT_CONFIG_DIR", str(tmp_path))
    assert isinstance(load_config("limits"), HardLimits)
    raw = yaml.safe_load((tmp_path / "limits.yaml").read_text(encoding="utf-8"))
    raw["flow_max_m3h"] = 10.0  # below flow_min_active_m3h
    (tmp_path / "limits.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config("limits")
    raw["ch4_venting_allowed"] = True
    raw["flow_max_m3h"] = 300.0
    (tmp_path / "limits.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config("limits")
