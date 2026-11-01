"""TL-14 ``economics`` and ``co2``: CAPEX, OPEX, LCOH/LCOC, CO₂ and cost sensitivity (spec §9.2).

Every cost number is read through ONE accessor, :meth:`CostBook.usd`, which converts the native
value stored in ``config/cost_assumptions.yaml`` to USD: ``eur_to_usd`` for EUR, ×
``dea_eur2020_to_eur2025`` first for DEA 2020 € items, ``aud_to_usd`` for AUD and the CEPCI ratio
(latest index year / price year) for items with ``escalation_index: cepci``; the unit follows the
item name (``M EUR/MW_th`` → ``USD/kW_th``, ``EUR/GJ_GCV`` → ``USD/MWh_LHV``). ``usd_range`` is
already USD and is never converted again. Sensitivity bounds are ±``min_sensitivity_frac`` (30 %)
or the stated range, whichever is wider (DB-35, ECO-04).

Levelised cost (ECO-01, Gippsland Eq. 1)::

    LCOH = Σ_t (C_t + M_t + F_t)/(1+r)^t  ÷  Σ_t H_t/(1+r)^t,   t = 0 … N (H_0 = 0)

with C capex (year 0 plus replacements), M O&M = ``om_frac_of_capex_per_yr`` × capex, F energy
(purchased electricity, grid gas, CO₂ on direct emissions) in real terms, escalating at
``(1 + energy_escalation_frac)/(1 + inflation_general_frac) − 1`` = 2.44 %/yr (ECO-REAL); LCOC
is analogous. Shared costs (wells, pumps, flaring) are split between heat and cooling by the
brine heat each service draws, else by delivered energy (ECO-ALLOC); an allocation-free combined
cost per MWh is reported too. Both a 30-yr and a 20-yr analysis period are reported (ECO-01).
ECO-06 fixed-charge-rate form ``(FCR·TCC + FOC)/AEP + VOC`` is reported alongside.

CO₂ (ECO-05): grid gas 56.6 kg/GJ (ECO-CO2-GAS), associated gas 68.6 kg/GJ_LHV incl. inherent CO₂
(DB-28, derived from the DB-09 composition), grid electricity 0.3 kg/kWh (DB-37). All degassed gas
is burned or flared, never vented (ECO-CO2-AG).

Sources: RC-10 (levelised cost, capex items, sourced values, benchmarks); RC-05 Cost (chiller
plant cost functions, 1986 $, ``VERIFY``); RC-08 (gas boiler, emission factors); RC-07 (dry
cooler ≈ 3× wet tower); spec §9.2 ECO-01…07.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from gemini_agent.schemas.common import (
    HOURS_PER_DAY,
    SUMMARY_MAX_CHARS,
    GasHandling,
    SourceTag,
    StrictModel,
    ToolResult,
)
from gemini_agent.schemas.config import (
    AssumptionsRegister,
    CostAssumptions,
    CostItem,
    DesignBasis,
    load_config,
)
from gemini_agent.schemas.results import AnnualResult, KPISet
from gemini_agent.tools.design_basis import gas_mix_properties

TOOL_ID = "TL-14"
SOURCES = ["RC-10", "RC-05", "RC-08", "RC-07", "spec §9.2"]

# --------------------------------------------------------------------------- constants
GJ_PER_MWH = 3.6
KW_PER_TON = 3.517
"""Refrigeration ton. Source: RC-05 Cost (1 ton = 3.517 kW)."""
CHILLER_TON_OFFSET = 100.0
"""RC-05 cost functions are written in ``(Q_t − 100)`` (Guidebook p.269)."""
CHILLER_MIN_TONS = 100.0
CHILLER_MAX_TONS = 1000.0
"""RC-05 Cost validity 100–1000 ton; larger plants are split into equal units (ECO-CHILLER)."""
WELLS_PER_DOUBLET = 2
MAX_DOUBLETS = 3
"""DB-10 hard limit; costing more only warns (DesignGuard enforces it)."""
SHORT_PERIOD_YR = 20
"""ECO-01: report a 20-yr case besides the base analysis period (Guidebook advises ≤ 20 yr)."""
WELL_COST_STEPS_M: tuple[tuple[float, float], ...] = ((2500.0, 3000.0), (4500.0, 5000.0))
"""RC-10: casing-program steps — interpolating across them is flagged as uncertain."""
SMALL_ELECTRIC_BOILER_MAX_MW = 10.0
"""RC-10 / DEA ch. 41: the small-unit price applies to units ≤ 10 MW_th."""
GAS_LHV_UNIT = "USD/MWh_LHV"

ASSUMPTION_GRID_GAS_CO2 = "ECO-CO2-GAS"
ASSUMPTION_ELECTRICITY_CO2 = "DB-37"
ASSUMPTION_ASSOC_GAS = "ECO-CO2-AG"
ASSUMPTION_CHP_EL_FRAC = "CHP-EL-FRAC"
"""Register id for the CHP electrical fraction of useful output, decided by WP-03 (0.3333)."""
ASSUMPTION_IDS = [
    "ECO-CUR",
    "DB-35",
    "DB-37",
    "ECO-CO2-GAS",
    "ECO-CO2-AG",
    "ECO-CO2-SCOPE",
    "ECO-ELEC-NET",
    "ECO-REAL",
    "ECO-ALLOC",
    "ECO-OM",
    "ECO-PUMP-CAPEX",
    "ECO-CHILLER",
]

CONVERSION_ITEMS = frozenset(
    {
        "eur_to_usd",
        "dea_eur2020_to_eur2025",
        "cepci_index",
        "aud_to_usd",
        "gas_gcv_to_lhv",
        "cepci_escalation_1986_to_2026",
    }
)
"""Conversion factors: used by the accessor, never varied in the tornado."""
NOT_SENSITIVITY_INPUTS = CONVERSION_ITEMS | {
    "analysis_period_yr",
    "pump_replacement_interval_yr",
    "heat_pump_replacement_interval_yr",
    "energy_escalation_frac",
    "inflation_general_frac",
}
NON_MONEY_KEYS = frozenset({"b_per_m", "max_depth_m"})
"""Named parameters of money items that are not money (never converted or scaled)."""
UNIT_RULES: dict[str, tuple[str, float]] = {
    "M EUR/MW_th": ("USD/kW_th", 1000.0),
    "M EUR/MW_e": ("USD/kW_e", 1000.0),
    "AUD/GJ_th": ("USD/MWh_th", GJ_PER_MWH),
}
"""Native unit → (unit returned by the accessor, unit factor). ``EUR/GJ_GCV`` is handled apart."""

Service = Literal["heat", "cool", "shared"]
SERVICES: tuple[Service, ...] = ("heat", "cool", "shared")
_COOL_WORDS = ("chiller", "cool", "tower", "absorption", "dry_cooler", "free", "rejection")
_HEAT_WORDS = ("heat_pump", "heatpump", "hthp", "boiler", "chp", "heating", "hp")
_SHARED_WORDS = ("esp", "pump", "injection", "producer", "injector", "well", "separator")


# --------------------------------------------------------------------------- cost accessor
@dataclass(frozen=True)
class UsdCost:
    """One cost item converted to USD by :meth:`CostBook.usd` (the only conversion point)."""

    name: str
    value: float | dict[str | int, float]
    unit: str
    factor: float
    """Native → returned multiplier (currency × price-year × CEPCI × unit; 1 if dimensionless)."""
    basis: str
    sensitivity_low: float | None
    sensitivity_high: float | None
    source: str
    tag: SourceTag
    verify: bool
    """True when the source is tagged ``VERIFY`` on its card (e.g. the RC-05 chiller functions)."""

    def scalar(self) -> float:
        """Return the value as a float (TypeError for tables and named parameters)."""
        if isinstance(self.value, dict):
            raise TypeError(f"cost item {self.name} is a table, not a scalar")
        return float(self.value)


class CostItemUsed(StrictModel):
    """A cost item as used in a result (for the report and the assumptions trail)."""

    name: str
    value: float | None = Field(description="Converted scalar value; None for tables.")
    unit: str
    basis: str
    sensitivity_low: float | None
    sensitivity_high: float | None
    source: str
    verify: bool


class CostBook:
    """Read-only view of ``CostAssumptions`` that returns USD values (RC-10 conventions).

    ``scale`` multiplies the converted value of named items (the money entries of tables); it is
    how :func:`cost_sensitivity` varies one input at a time.
    """

    def __init__(self, costs: CostAssumptions, scale: Mapping[str, float] | None = None) -> None:
        """Wrap validated cost assumptions; ``scale`` maps item name → multiplier."""
        if costs.reporting_currency != "USD":
            raise ValueError(
                f"reporting_currency {costs.reporting_currency!r}: the accessor converts to USD "
                "only (ECO-CUR)"
            )
        unknown = set(scale or {}) - set(costs.items)
        if unknown:
            raise KeyError(f"unknown cost items in scale: {sorted(unknown)}")
        self._costs = costs
        self._scale = dict(scale or {})
        self._used: set[str] = set()

    @classmethod
    def load(cls, config_dir: Path | str | None = None) -> CostBook:
        """Load ``config/cost_assumptions.yaml``."""
        return cls(load_config("cost_assumptions", config_dir))

    @property
    def currency(self) -> str:
        """Return the reporting currency, ``ECO-CUR``."""
        return self._costs.reporting_currency

    @property
    def min_sensitivity_frac(self) -> float:
        """DB-35 / ECO-04 minimum sensitivity (±30 %)."""
        return self._costs.min_sensitivity_frac

    @property
    def used(self) -> list[str]:
        """Names of the items read so far, sorted."""
        return sorted(self._used)

    def names(self) -> list[str]:
        """All item names."""
        return sorted(self._costs.items)

    def fresh(self) -> CostBook:
        """Return a copy with the same costs and scale and an empty ``used`` record."""
        return CostBook(self._costs, self._scale)

    def scaled(self, name: str, factor: float) -> CostBook:
        """Return a new book with item ``name`` multiplied by ``factor`` (after conversion)."""
        if name in CONVERSION_ITEMS:
            raise ValueError(f"{name} is a conversion factor and cannot be scaled")
        scale = dict(self._scale)
        scale[name] = scale.get(name, 1.0) * factor
        return CostBook(self._costs, scale)

    # ------------------------------------------------------------------ the accessor
    def usd(self, name: str) -> UsdCost:
        """Return item ``name`` converted to USD (THE accessor; never read ``.value`` elsewhere)."""
        item = self._costs.items[name]
        self._used.add(name)
        currency_factor, basis = self._currency_factor(name, item)
        unit, unit_factor = self._unit(item)
        factor = currency_factor * unit_factor
        scale = self._scale.get(name, 1.0)
        value: float | dict[str | int, float]
        if isinstance(item.value, dict):
            value = {
                key: float(raw) * (1.0 if key in NON_MONEY_KEYS else factor * scale)
                for key, raw in item.value.items()
                if isinstance(raw, int | float)
            }
            low = high = None
        elif isinstance(item.value, int | float):
            value = float(item.value) * factor * scale
            low, high = self._bounds(name, item, value, factor * scale)
        else:
            raise TypeError(f"cost item {name} has no numeric value")
        if scale != 1.0:
            basis += f" × sensitivity {scale:.4g}"
        return UsdCost(
            name=name,
            value=value,
            unit=unit,
            factor=factor,
            basis=basis,
            sensitivity_low=low,
            sensitivity_high=high,
            source=item.source,
            tag=item.tag,
            verify="VERIFY" in item.source or "VERIFY" in str(item.range),
        )

    def usd_value(self, name: str, key: str | int | None = None) -> float:
        """Scalar USD value of ``name`` (or entry ``key`` of a table) via :meth:`usd`."""
        cost = self.usd(name)
        if key is None:
            return cost.scalar()
        if not isinstance(cost.value, dict):
            raise TypeError(f"cost item {name} is a scalar; no key {key!r}")
        return cost.value[key]

    def stated_range(self, name: str) -> tuple[float, float] | None:
        """Stated (low, high) of ``name`` in the returned unit (``usd_range`` as is), else None."""
        cost = self.usd(name)
        item = self._costs.items[name]
        factor = cost.factor * self._scale.get(name, 1.0)
        if item.usd_range is not None:
            return item.usd_range  # already USD: never converted again
        if isinstance(item.range, tuple):
            return (item.range[0] * factor, item.range[1] * factor)
        return None

    def used_items(self) -> list[CostItemUsed]:
        """Return the items read so far, converted (for the report)."""
        out = []
        for name in self.used:
            cost = CostBook(self._costs, self._scale).usd(name)
            out.append(
                CostItemUsed(
                    name=name,
                    value=None if isinstance(cost.value, dict) else cost.value,
                    unit=cost.unit,
                    basis=cost.basis,
                    sensitivity_low=cost.sensitivity_low,
                    sensitivity_high=cost.sensitivity_high,
                    source=cost.source,
                    verify=cost.verify,
                )
            )
        return out

    # ------------------------------------------------------------------ internals
    def _raw_scalar(self, name: str) -> float:
        value = self._costs.items[name].value
        if not isinstance(value, int | float):
            raise TypeError(f"conversion item {name} must be a number")
        return float(value)

    def _currency_factor(self, name: str, item: CostItem) -> tuple[float, str]:
        if item.currency is None:
            return 1.0, "dimensionless"
        factor, parts = 1.0, [f"{item.currency}{item.price_year or ''}"]
        if item.currency == "EUR":
            if item.price_year == 2020:
                if "DEA" not in item.source:
                    raise ValueError(f"{name}: EUR2020 conversion is defined for DEA items only")
                dea = self._raw_scalar("dea_eur2020_to_eur2025")
                factor *= dea
                parts.append(f"× {dea} (DEA 2020→2025 €)")
            fx = self._raw_scalar("eur_to_usd")
            factor *= fx
            parts.append(f"× {fx} USD/EUR")
        elif item.currency == "AUD":
            fx = self._raw_scalar("aud_to_usd")
            factor *= fx
            parts.append(f"× {fx} USD/AUD")
        if item.escalation_index == "cepci":
            index = self._costs.items["cepci_index"].value
            assert isinstance(index, dict)  # guaranteed by CostAssumptions validation
            latest = max(int(year) for year in index)
            ratio = float(index[latest]) / float(index[item.price_year])  # type: ignore[index]
            factor *= ratio
            parts.append(f"× CEPCI {latest}/{item.price_year} {ratio:.4f}")
        return factor, " ".join(parts)

    def _unit(self, item: CostItem) -> tuple[str, float]:
        if item.currency is None:
            return item.unit, 1.0
        if item.unit == "EUR/GJ_GCV":
            return GAS_LHV_UNIT, GJ_PER_MWH * self._raw_scalar("gas_gcv_to_lhv")
        if item.unit in UNIT_RULES:
            return UNIT_RULES[item.unit]
        return re.sub(r"\b(EUR|AUD)\b", "USD", item.unit), 1.0

    def _bounds(
        self, name: str, item: CostItem, value: float, factor: float
    ) -> tuple[float, float]:
        frac = self._costs.min_sensitivity_frac
        low, high = value * (1.0 - frac), value * (1.0 + frac)
        if name.endswith("_multiplier") and value > 1.0:
            premium = value - 1.0  # ±30 % on the premium, never below 1 (ECO-SENS-MULT)
            low, high = 1.0 + premium * (1.0 - frac), 1.0 + premium * (1.0 + frac)
        stated: tuple[float, float] | None = None
        if item.usd_range is not None:
            stated = item.usd_range  # already USD: never converted again
        elif isinstance(item.range, tuple):
            stated = (item.range[0] * factor, item.range[1] * factor)
        if stated is not None:
            low, high = min(low, stated[0]), max(high, stated[1])
        return low, high


# --------------------------------------------------------------------------- cost functions
class WellCost(StrictModel):
    """Cost of one well from the NREL table (RC-10, ECO-02)."""

    depth_m: float
    diameter: Literal["large", "small"]
    cost_musd: float
    across_casing_step: bool = Field(description="Interpolated across a casing step: uncertain.")


def well_cost_musd(
    book: CostBook,
    depth_m: float,
    *,
    diameter: Literal["large", "small"] = "large",
    deviated: bool = False,
) -> WellCost:
    """Cost of one well by linear interpolation of the NREL 2025 Fig. 5 table (RC-10, ECO-02).

    Never extrapolates; interpolation across the 2.5–3 km or 4.5–5 km casing step is flagged.
    Source: RC-10 Capex items (NREL 2025 cost curves, ±0.15 M$); deviated × 1.10–1.15.
    """
    name = (
        "well_cost_large_diameter_musd" if diameter == "large" else "well_cost_small_diameter_musd"
    )
    table = book.usd(name).value
    assert isinstance(table, dict)
    nodes = sorted((float(z), cost) for z, cost in table.items())
    if not nodes[0][0] <= depth_m <= nodes[-1][0]:
        raise ValueError(
            f"depth {depth_m} m outside the RC-10 well-cost table {nodes[0][0]}–{nodes[-1][0]} m"
        )
    cost = nodes[-1][1]
    for (z0, c0), (z1, c1) in pairwise(nodes):
        if z0 <= depth_m <= z1:
            cost = c0 + (c1 - c0) * (depth_m - z0) / (z1 - z0)
            break
    if deviated:
        cost *= book.usd_value("well_deviated_multiplier")
    across = any(lo < depth_m < hi for lo, hi in WELL_COST_STEPS_M)
    return WellCost(depth_m=depth_m, diameter=diameter, cost_musd=cost, across_casing_step=across)


def well_cost_smooth_fit_musd(book: CostBook, depth_m: float) -> float:
    """Smooth fit ``1.45 M$ · exp(3.66e-4 z)`` — sensitivity sweeps ≤ 2.5 km only (RC-10)."""
    fit = book.usd("well_cost_smooth_fit").value
    assert isinstance(fit, dict)
    if depth_m > fit["max_depth_m"]:
        raise ValueError(f"smooth fit invalid above {fit['max_depth_m']} m (RC-10)")
    return fit["a_musd"] * math.exp(fit["b_per_m"] * depth_m)


def plate_hex_cost_usd(
    book: CostBook, mass_flow_kg_s: float, delta_t_k: float, *, titanium: bool = True
) -> float:
    """Plate HEX ``(2000 + 48 · ṁ · ΔT) AUD`` × 0.63 USD/AUD, titanium plates +50 % (ECO-03).

    Source: RC-10 Capex items (Gippsland; Guidebook p.220). Small clean-water units: not for the
    free-cooling HEX.
    """
    if mass_flow_kg_s <= 0 or delta_t_k <= 0:
        raise ValueError("mass flow and ΔT must be > 0")
    cost = book.usd_value("plate_hex_base_aud") + book.usd_value("plate_hex_per_kgs_k_aud") * (
        mass_flow_kg_s * delta_t_k
    )
    if titanium:
        cost *= book.usd_value("plate_hex_titanium_multiplier")
    return cost


def pipe_cost_usd(book: CostBook, length_m: float, *, preinsulated: bool = True) -> float:
    """Pipe 500 AUD/m pre-insulated (100 AUD/m bare HDPE) per metre as stated (RC-10, Gippsland)."""
    name = "pipe_preinsulated_aud_per_m" if preinsulated else "pipe_hdpe_uninsulated_aud_per_m"
    return book.usd_value(name) * length_m


def free_cooling_hex_equipment_cost_usd(book: CostBook, tons: float) -> float:
    """Seawater-grade plate HEX + housing ``513 $/ton + 59,600 $`` (2002 $, CEPCI-escalated).

    Source: RC-10 Sourced values (Hawaii SWAC 2002, Eq. 9). Equipment only; the installed per-kW
    value ``free_cooling_hex_capex_usd_per_kw_th`` is what the capex uses.
    """
    return book.usd_value("free_cooling_hex_cost_function_usd", "per_ton") * tons + (
        book.usd_value("free_cooling_hex_cost_function_usd", "fixed")
    )


class ChillerPlantCost(StrictModel):
    """RC-05 chiller plant cost (1986 Means $ × CEPCI), split into ≤ 1000-ton units."""

    kind: Literal["absorption", "electric"]
    duty_mw: float
    units: int
    tons_per_unit: float
    capacity_fraction: float
    cost_usd: float
    warnings: list[str] = Field(default_factory=list)


def chiller_plant_cost_usd(
    book: CostBook,
    kind: Literal["absorption", "electric"],
    duty_mw: float,
    *,
    capacity_fraction: float = 1.0,
    titanium_tubes: bool = False,
) -> ChillerPlantCost:
    """RC-05 Cost functions (Guidebook p.269, 1986 $, ``VERIFY``), escalated by CEPCI.

    Absorption plant ``83,000 + 252 (Q_t − 100)/F + 41,000 + 170 (Q_t − 100)`` (machine + tower,
    pumps, piping), electric centrifugal plant ``107,000 + 267 (Q_t − 100)``; Q_t duty tons, F the
    absorption capacity fraction (nominal size = duty/F). Valid 100–1000 ton: plants above 1000
    nominal tons are split into equal units (ECO-CHILLER); units below 100 duty tons are
    extrapolated with a warning. Titanium
    generator tubes add 10–15 % to the absorption machine terms (RC-05 Materials).
    """
    if duty_mw <= 0:
        raise ValueError("chiller duty must be > 0")
    if not 0.0 < capacity_fraction <= 1.0:
        raise ValueError("capacity fraction must be in (0, 1] (COOL-ABS-CAP)")
    tons = duty_mw * 1000.0 / KW_PER_TON
    nominal = tons / capacity_fraction if kind == "absorption" else tons
    units = max(1, math.ceil(nominal / CHILLER_MAX_TONS - 1e-12))
    q_t = tons / units
    warnings = [f"RC-05 {kind} chiller cost function is VERIFY (decoded, 1986 $ × CEPCI)"]
    if nominal / units < CHILLER_MIN_TONS:
        warnings.append(
            f"{nominal / units:.0f} nominal ton below the RC-05 validity 100–1000 ton: extrapolated"
        )
    if kind == "absorption":
        p = book.usd("absorption_chiller_plant_cost_function_usd").value
        assert isinstance(p, dict)
        machine = p["machine_fixed"] + p["machine_per_ton"] * (q_t - CHILLER_TON_OFFSET) / (
            capacity_fraction
        )
        if titanium_tubes:
            machine *= book.usd_value("absorption_titanium_tubes_multiplier")
        unit_cost = machine + p["plant_fixed"] + p["plant_per_ton"] * (q_t - CHILLER_TON_OFFSET)
    else:
        p = book.usd("electric_chiller_plant_cost_function_usd").value
        assert isinstance(p, dict)
        unit_cost = p["fixed"] + p["per_ton"] * (q_t - CHILLER_TON_OFFSET)
    return ChillerPlantCost(
        kind=kind,
        duty_mw=duty_mw,
        units=units,
        tons_per_unit=q_t,
        capacity_fraction=capacity_fraction,
        cost_usd=units * unit_cost,
        warnings=warnings,
    )


# --------------------------------------------------------------------------- capex
class HexDuty(StrictModel):
    """One plate HEX to cost (ECO-03): brine-side mass flow and temperature change."""

    name: str
    mass_flow_kg_s: float = Field(gt=0)
    delta_t_k: float = Field(gt=0)
    titanium: bool = Field(default=True, description="Ti plates for saline brine (RC-12).")
    service: Service = "heat"


class CapexRequest(StrictModel):
    """Installed equipment of one candidate, in the units each RC-10 cost item uses.

    Sizes are installed capacities (MW). Chiller duties are cooling MW; ``cooling_tower_mw`` is
    heat rejected that is not already inside an RC-05 chiller plant (e.g. free cooling).
    """

    candidate_id: str
    doublets: int = Field(ge=0)
    depth_m: float | None = Field(default=None, gt=0)
    well_diameter: Literal["large", "small"] = Field(
        default="large", description="300 m³/h needs ≥ 13⅜″ production casing (ECO-02)."
    )
    deviated_wells: bool = False
    plate_hexes: list[HexDuty] = Field(default_factory=list)
    hthp_heating_mw: float = Field(default=0.0, ge=0, description="Condenser output.")
    chp_electric_mw: float = Field(default=0.0, ge=0)
    chp_thermal_mw: float = Field(
        default=0.0, ge=0, description="Alternative to chp_electric_mw (needs CHP-EL-FRAC)."
    )
    chp_electrical_fraction: float | None = Field(
        default=None,
        gt=0,
        lt=1,
        description="Electricity / useful output; else register CHP-EL-FRAC (WP-03).",
    )
    gas_boiler_mw: float = Field(default=0.0, ge=0)
    electric_boiler_mw: float = Field(default=0.0, ge=0)
    electric_chiller_mw: float = Field(default=0.0, ge=0)
    absorption_chiller_mw: float = Field(default=0.0, ge=0)
    absorption_capacity_fraction: float | None = Field(
        default=None, gt=0, le=1, description="F at the design drive/chilled temperatures."
    )
    absorption_titanium_tubes: bool = Field(
        default=False, description="Generator on brine directly (no isolation HEX)."
    )
    cooling_tower_mw: float = Field(default=0.0, ge=0)
    dry_cooler_mw: float = Field(default=0.0, ge=0)
    free_cooling_mw: float = Field(default=0.0, ge=0)
    trunk_pipe_length_m: float | None = Field(default=None, ge=0)
    trunk_pipe_service: Service = "heat"
    pump_capex_usd_per_doublet: float | None = Field(
        default=None, ge=0, description="ESP + injection pump set; not on RC-10 (ECO-PUMP-CAPEX)."
    )

    @model_validator(mode="after")
    def _check(self) -> CapexRequest:
        if (self.doublets > 0) != (self.depth_m is not None):
            raise ValueError("depth_m is required if and only if doublets > 0")
        if self.absorption_chiller_mw > 0 and self.absorption_capacity_fraction is None:
            raise ValueError("absorption_chiller_mw needs absorption_capacity_fraction (RC-05 F)")
        if self.chp_electric_mw > 0 and self.chp_thermal_mw > 0:
            raise ValueError("give chp_electric_mw or chp_thermal_mw, not both")
        return self


class CapexLine(StrictModel):
    """One capex line."""

    name: str
    items: list[str] = Field(description="Cost items read (config/cost_assumptions.yaml).")
    service: Service
    cost_usd: float = Field(ge=0)
    replacement_interval_yr: float | None = None
    note: str = ""


class CapexBreakdown(StrictModel):
    """Capex lines and totals (USD)."""

    lines: list[CapexLine]
    total_usd: float
    by_service_usd: dict[str, float]
    warnings: list[str] = Field(default_factory=list)


def chp_electric_capacity_mw(
    thermal_mw: float, fraction: float | None, register: AssumptionsRegister | None
) -> tuple[float, float, list[str]]:
    """CHP electrical capacity from its thermal output: ``P_el = P_th · f/(1 − f)``.

    ``f`` = electricity share of useful output — GEMINI ``chp.py`` 1/3 vs DEA gas engine ≈ 0.50
    (RC-08, RC-10); decided by WP-03 in register ``CHP-EL-FRAC`` (a plain number in (0, 1); it
    wins when present), else the explicit parameter. Returns ``(P_el, f, warnings)``.
    """
    warnings: list[str] = []
    value = None
    if register is not None:
        try:
            value = register.get(ASSUMPTION_CHP_EL_FRAC).value
        except KeyError:
            value = None
    if value is not None:
        if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value < 1:
            raise ValueError(f"register CHP-EL-FRAC must be a number in (0, 1), got {value!r}")
        if fraction is not None and not math.isclose(fraction, float(value)):
            warnings.append(
                f"chp_electrical_fraction {fraction} ignored: register CHP-EL-FRAC = {value}"
            )
        fraction = float(value)
    if fraction is None:
        raise ValueError(
            "CHP electrical fraction unknown: register CHP-EL-FRAC (WP-03) missing and no "
            "chp_electrical_fraction given"
        )
    return thermal_mw * fraction / (1.0 - fraction), fraction, warnings


def capex_breakdown(
    request: CapexRequest, book: CostBook, register: AssumptionsRegister | None = None
) -> CapexBreakdown:
    """Capex of one candidate from the RC-10 / RC-05 / RC-08 cost items (USD)."""
    lines: list[CapexLine] = []
    warnings: list[str] = []
    kw = 1000.0

    def add(line: CapexLine) -> None:
        if line.cost_usd > 0:
            lines.append(line)

    if request.doublets:
        assert request.depth_m is not None
        well = well_cost_musd(
            book, request.depth_m, diameter=request.well_diameter, deviated=request.deviated_wells
        )
        n_wells = WELLS_PER_DOUBLET * request.doublets
        if request.doublets > MAX_DOUBLETS:
            warnings.append(f"{request.doublets} doublets exceed the DB-10 limit of 3")
        if well.across_casing_step:
            warnings.append(
                f"well cost at {request.depth_m:.0f} m interpolated across a casing step "
                "(RC-10): uncertain"
            )
        add(
            CapexLine(
                name="wells",
                items=[f"well_cost_{request.well_diameter}_diameter_musd"]
                + (["well_deviated_multiplier"] if request.deviated_wells else []),
                service="shared",
                cost_usd=n_wells * well.cost_musd * 1e6,
                note=f"{n_wells} wells × {well.cost_musd:.3f} M$ at {request.depth_m:.0f} m",
            )
        )
        if request.pump_capex_usd_per_doublet is None:
            warnings.append(
                "MISSING INPUT: ESP/injection-pump capex not on RC-10; pumps and their "
                "replacement not costed (ECO-PUMP-CAPEX)"
            )
        else:
            add(
                CapexLine(
                    name="pumps",
                    items=["pump_replacement_interval_yr"],
                    service="shared",
                    cost_usd=request.pump_capex_usd_per_doublet * request.doublets,
                    replacement_interval_yr=book.usd_value("pump_replacement_interval_yr"),
                    note="caller-supplied pump capex",
                )
            )
    for hx in request.plate_hexes:
        add(
            CapexLine(
                name=f"plate_hex:{hx.name}",
                items=["plate_hex_base_aud", "plate_hex_per_kgs_k_aud"]
                + (["plate_hex_titanium_multiplier"] if hx.titanium else []),
                service=hx.service,
                cost_usd=plate_hex_cost_usd(
                    book, hx.mass_flow_kg_s, hx.delta_t_k, titanium=hx.titanium
                ),
            )
        )
    if request.hthp_heating_mw:
        add(
            CapexLine(
                name="hthp",
                items=["hthp_capex_usd_per_kw_th", "heat_pump_replacement_interval_yr"],
                service="heat",
                cost_usd=request.hthp_heating_mw * kw * book.usd_value("hthp_capex_usd_per_kw_th"),
                replacement_interval_yr=book.usd_value("heat_pump_replacement_interval_yr"),
            )
        )
    chp_el = request.chp_electric_mw
    if request.chp_thermal_mw:
        chp_el, frac, chp_warnings = chp_electric_capacity_mw(
            request.chp_thermal_mw, request.chp_electrical_fraction, register
        )
        warnings.extend(chp_warnings)
        warnings.append(f"CHP {chp_el:.2f} MW_e from {request.chp_thermal_mw} MW_th at f = {frac}")
    if chp_el:
        add(
            CapexLine(
                name="chp",
                items=["chp_capex_usd_per_kw_e"],
                service="heat",
                cost_usd=chp_el * kw * book.usd_value("chp_capex_usd_per_kw_e"),
            )
        )
    if request.gas_boiler_mw:
        add(
            CapexLine(
                name="gas_boiler",
                items=["gas_boiler_capex_usd_per_kw_th"],
                service="heat",
                cost_usd=request.gas_boiler_mw
                * kw
                * book.usd_value("gas_boiler_capex_usd_per_kw_th"),
            )
        )
    if request.electric_boiler_mw:
        small = request.electric_boiler_mw <= SMALL_ELECTRIC_BOILER_MAX_MW
        item = (
            "electric_boiler_small_capex_usd_per_kw_th"
            if small
            else "electric_boiler_capex_usd_per_kw_th"
        )
        add(
            CapexLine(
                name="electric_boiler",
                items=[item],
                service="heat",
                cost_usd=request.electric_boiler_mw * kw * book.usd_value(item),
            )
        )
    for kind, duty in (
        ("electric", request.electric_chiller_mw),
        ("absorption", request.absorption_chiller_mw),
    ):
        if not duty:
            continue
        plant = chiller_plant_cost_usd(
            book,
            kind,  # type: ignore[arg-type]
            duty,
            capacity_fraction=(
                (request.absorption_capacity_fraction or 1.0) if kind == "absorption" else 1.0
            ),
            titanium_tubes=request.absorption_titanium_tubes and kind == "absorption",
        )
        warnings.extend(plant.warnings)
        add(
            CapexLine(
                name=f"{kind}_chiller",
                items=[f"{kind}_chiller_plant_cost_function_usd"],
                service="cool",
                cost_usd=plant.cost_usd,
                note=f"{plant.units} unit(s) × {plant.tons_per_unit:.0f} ton (RC-05, VERIFY)",
            )
        )
    if request.cooling_tower_mw:
        add(
            CapexLine(
                name="cooling_tower",
                items=["cooling_tower_capex_usd_per_kw_th"],
                service="cool",
                cost_usd=request.cooling_tower_mw
                * kw
                * book.usd_value("cooling_tower_capex_usd_per_kw_th"),
            )
        )
    if request.dry_cooler_mw:
        add(
            CapexLine(
                name="dry_cooler",
                items=["cooling_tower_capex_usd_per_kw_th", "dry_cooler_cost_multiplier"],
                service="cool",
                cost_usd=request.dry_cooler_mw
                * kw
                * book.usd_value("cooling_tower_capex_usd_per_kw_th")
                * book.usd_value("dry_cooler_cost_multiplier"),
            )
        )
    if request.free_cooling_mw:
        add(
            CapexLine(
                name="free_cooling_hex",
                items=["free_cooling_hex_capex_usd_per_kw_th"],
                service="cool",
                cost_usd=request.free_cooling_mw
                * kw
                * book.usd_value("free_cooling_hex_capex_usd_per_kw_th"),
            )
        )
    if request.trunk_pipe_length_m is None:
        warnings.append("network trunk pipe not costed (no length; common to all candidates)")
    elif request.trunk_pipe_length_m > 0:
        add(
            CapexLine(
                name="trunk_pipe",
                items=["pipe_preinsulated_aud_per_m"],
                service=request.trunk_pipe_service,
                cost_usd=pipe_cost_usd(book, request.trunk_pipe_length_m),
            )
        )
    by_service = {s: sum(line.cost_usd for line in lines if line.service == s) for s in SERVICES}
    return CapexBreakdown(
        lines=lines,
        total_usd=sum(by_service.values()),
        by_service_usd=by_service,
        warnings=warnings,
    )


# --------------------------------------------------------------------------- energy carriers
def classify_service(key: str, explicit: Mapping[str, Service] | None = None) -> Service | None:
    """Service of a consumer/burner key: explicit mapping, else keyword rules; None if unknown."""
    if explicit and key in explicit:
        return explicit[key]
    lowered = key.lower()
    for words, service in (
        (_COOL_WORDS, "cool"),
        (_HEAT_WORDS, "heat"),
        (_SHARED_WORDS, "shared"),
    ):
        if any(word in lowered for word in words):
            return service  # type: ignore[return-value]
    return None


class CarrierBalance(StrictModel):
    """Annual energy carriers of one candidate (MWh; fuel on LHV basis)."""

    heat_delivered_mwh: float
    cool_delivered_mwh: float
    electricity_consumed_mwh: float
    electricity_generated_mwh: float
    electricity_purchased_mwh: float = Field(description="Daily max(0, consumed − generated).")
    electricity_surplus_mwh: float = Field(description="Generated beyond own use: not credited.")
    electricity_purchased_by_service_mwh: dict[str, float]
    gas_fuel_mwh: float = Field(description="All fuel burned (LHV).")
    grid_gas_mwh: float
    grid_gas_by_service_mwh: dict[str, float]
    associated_gas_available_mwh: float = Field(description="Degassed gas (LHV), degas_use only.")
    associated_gas_burned_mwh: float
    associated_gas_burned_by_service_mwh: dict[str, float]
    associated_gas_flared_mwh: float
    brine_heat_drawn_by_service_mwh: dict[str, float] = Field(
        description="DayResult.brine_heat_drawn_mw per service: cooling users (absorption drive) "
        "vs heat (everything else); allocates shared costs (ECO-ALLOC)."
    )
    warnings: list[str] = Field(default_factory=list)


def carrier_balance(
    annual: AnnualResult,
    *,
    gas_handling: GasHandling | None,
    associated_gas_lhv_mj_per_nm3: float,
    service_by_key: Mapping[str, Service] | None = None,
) -> CarrierBalance:
    """Annual electricity and gas balance from the daily results (ECO-ELEC-NET, ECO-CO2-AG).

    Own generation offsets purchases day by day; surplus is not credited (no sourced export
    price). With ``degas_use`` the burners take the degassed associated gas first, grid gas makes
    up the rest, and associated gas beyond the burners' need is flared (never vented, DB-36).
    """
    purchased_by: dict[str, float] = defaultdict(float)
    grid_by: dict[str, float] = defaultdict(float)
    assoc_by: dict[str, float] = defaultdict(float)
    brine_by: dict[str, float] = defaultdict(float)
    unknown: set[str] = set()
    totals: dict[str, float] = defaultdict(float)

    def service(key: str) -> Service:
        found = classify_service(key, service_by_key)
        if found is None:
            unknown.add(key)
            return "shared"
        return found

    if gas_handling is None and any(day.associated_gas_produced_nm3 > 0 for day in annual.days):
        raise ValueError(
            "associated gas is produced but gas_handling is not set: say degas_use or "
            "pressurised_reinject (DB-36)"
        )
    for day in annual.days:
        totals["heat"] += day.heat_supplied_mw * HOURS_PER_DAY
        totals["cool"] += day.cool_supplied_mw * HOURS_PER_DAY
        for key, mw in day.brine_heat_drawn_mw.items():
            user = classify_service(key, service_by_key)
            brine_by["cool" if user == "cool" else "heat"] += mw * HOURS_PER_DAY
        consumed = day.electricity_consumed_mw * HOURS_PER_DAY
        generated = day.electricity_generated_mw * HOURS_PER_DAY
        purchased = max(0.0, consumed - generated)
        totals["consumed"] += consumed
        totals["generated"] += generated
        totals["purchased"] += purchased
        totals["surplus"] += max(0.0, generated - consumed)
        if consumed > 0:
            for key, mw in day.electricity_by_consumer_mw.items():
                purchased_by[service(key)] += purchased * mw * HOURS_PER_DAY / consumed
        fuel = sum(day.gas_fuel_by_consumer_mw.values()) * HOURS_PER_DAY
        available = 0.0
        if gas_handling == GasHandling.DEGAS_USE:
            available = day.associated_gas_produced_nm3 * associated_gas_lhv_mj_per_nm3 / 3600.0
        burned_assoc = min(fuel, available)
        grid = fuel - burned_assoc
        totals["fuel"] += fuel
        totals["available"] += available
        totals["burned_assoc"] += burned_assoc
        totals["flared"] += available - burned_assoc
        totals["grid"] += grid
        if fuel > 0:
            for key, mw in day.gas_fuel_by_consumer_mw.items():
                share = mw * HOURS_PER_DAY / fuel
                grid_by[service(key)] += grid * share
                assoc_by[service(key)] += burned_assoc * share
    warnings = []
    if unknown:
        warnings.append(f"keys with unknown service allocated as shared: {sorted(unknown)}")
    if totals["surplus"] > 0:
        warnings.append(
            f"{totals['surplus']:.0f} MWh surplus electricity not credited (no export price)"
        )
    if totals["flared"] > 0:
        warnings.append(f"{totals['flared']:.0f} MWh associated gas flared (not used by burners)")
    return CarrierBalance(
        heat_delivered_mwh=totals["heat"],
        cool_delivered_mwh=totals["cool"],
        electricity_consumed_mwh=totals["consumed"],
        electricity_generated_mwh=totals["generated"],
        electricity_purchased_mwh=totals["purchased"],
        electricity_surplus_mwh=totals["surplus"],
        electricity_purchased_by_service_mwh={s: purchased_by[s] for s in SERVICES},
        gas_fuel_mwh=totals["fuel"],
        grid_gas_mwh=totals["grid"],
        grid_gas_by_service_mwh={s: grid_by[s] for s in SERVICES},
        associated_gas_available_mwh=totals["available"],
        associated_gas_burned_mwh=totals["burned_assoc"],
        associated_gas_burned_by_service_mwh={s: assoc_by[s] for s in SERVICES},
        associated_gas_flared_mwh=totals["flared"],
        brine_heat_drawn_by_service_mwh={"heat": brine_by["heat"], "cool": brine_by["cool"]},
        warnings=warnings,
    )


# --------------------------------------------------------------------------- CO2
class Co2Factors(StrictModel):
    """Emission factors with their register ids (ECO-05)."""

    grid_gas_kg_per_gj: float
    grid_gas_alternative_kg_per_gj: list[float]
    associated_gas_kg_per_gj: float = Field(description="Per GJ LHV incl. inherent CO₂ (DB-28).")
    associated_gas_lhv_mj_per_nm3: float
    electricity_kg_per_kwh: float


class Co2Report(StrictModel):
    """Annual CO₂ of one candidate (t/yr) with the factors used."""

    factors: Co2Factors
    grid_gas_t: float
    associated_gas_burned_t: float
    associated_gas_flared_t: float
    electricity_t: float = Field(description="Purchased electricity × DB-37 (indirect).")
    direct_t: float = Field(description="Gas burned or flared on site (priced, ECO-CO2-SCOPE).")
    total_t: float
    direct_by_service_t: dict[str, float]
    c0_t_per_yr: float | None = None
    reduction_vs_c0_frac: float | None = None
    simulator_direct_t: float | None = Field(
        default=None, description="Σ DayResult.co2_by_source_kg, cross-check only."
    )
    warnings: list[str] = Field(default_factory=list)


def co2_factors(
    register: AssumptionsRegister, basis: DesignBasis, *, grid_gas_kg_per_gj: float | None = None
) -> Co2Factors:
    """Return the ECO-05 factors: grid gas, associated gas (DB-28 from DB-09), power (DB-37).

    ``grid_gas_kg_per_gj`` overrides the register value (sensitivity, e.g. 51.53).
    """
    gas_entry = register.get(ASSUMPTION_GRID_GAS_CO2)
    assert isinstance(gas_entry.value, int | float)
    alternatives = gas_entry.sensitivity if isinstance(gas_entry.sensitivity, list) else []
    electricity = register.get(ASSUMPTION_ELECTRICITY_CO2).value
    assert isinstance(electricity, int | float)
    mix = gas_mix_properties(basis.brine.gas_ch4_mol_frac, basis.brine.gas_co2_mol_frac)
    return Co2Factors(
        grid_gas_kg_per_gj=(
            float(gas_entry.value) if grid_gas_kg_per_gj is None else grid_gas_kg_per_gj
        ),
        grid_gas_alternative_kg_per_gj=[float(x) for x in alternatives],  # type: ignore[arg-type]
        associated_gas_kg_per_gj=mix.co2_when_burned_kg_per_gj,
        associated_gas_lhv_mj_per_nm3=mix.lhv_mj_per_nm3,
        electricity_kg_per_kwh=float(electricity),
    )


def co2_report(
    annual: AnnualResult,
    carriers: CarrierBalance,
    factors: Co2Factors,
    *,
    c0_co2_t_per_yr: float | None = None,
) -> Co2Report:
    """CO₂ from the carriers: gas burned/flared (direct) + purchased electricity (indirect)."""
    t_per_mwh_grid = factors.grid_gas_kg_per_gj * GJ_PER_MWH / 1000.0
    t_per_mwh_assoc = factors.associated_gas_kg_per_gj * GJ_PER_MWH / 1000.0
    grid_t = carriers.grid_gas_mwh * t_per_mwh_grid
    burned_t = carriers.associated_gas_burned_mwh * t_per_mwh_assoc
    flared_t = carriers.associated_gas_flared_mwh * t_per_mwh_assoc
    electricity_t = carriers.electricity_purchased_mwh * factors.electricity_kg_per_kwh
    direct = grid_t + burned_t + flared_t
    by_service = {
        s: carriers.grid_gas_by_service_mwh[s] * t_per_mwh_grid
        + carriers.associated_gas_burned_by_service_mwh[s] * t_per_mwh_assoc
        for s in SERVICES
    }
    by_service["shared"] += flared_t
    total = direct + electricity_t
    reduction = None
    if c0_co2_t_per_yr is not None and c0_co2_t_per_yr > 0:
        reduction = (c0_co2_t_per_yr - total) / c0_co2_t_per_yr
    warnings: list[str] = []
    simulator = None
    if any(day.co2_by_source_kg for day in annual.days):
        simulator = annual.co2_t
        if direct > 0 and abs(simulator - direct) > 0.01 * direct:
            warnings.append(
                f"simulator CO2 {simulator:.0f} t differs > 1 % from the carrier-based direct CO2 "
                f"{direct:.0f} t (check emission factors in the GEMINI JSON)"
            )
    return Co2Report(
        factors=factors,
        grid_gas_t=grid_t,
        associated_gas_burned_t=burned_t,
        associated_gas_flared_t=flared_t,
        electricity_t=electricity_t,
        direct_t=direct,
        total_t=total,
        direct_by_service_t=by_service,
        c0_t_per_yr=c0_co2_t_per_yr,
        reduction_vs_c0_frac=reduction,
        simulator_direct_t=simulator,
        warnings=warnings,
    )


class Co2Request(StrictModel):
    """Input of the ``co2`` tool."""

    gas_handling: GasHandling | None = None
    service_by_key: dict[str, Service] = Field(default_factory=dict)
    c0_co2_t_per_yr: float | None = Field(default=None, gt=0)
    grid_gas_kg_per_gj: float | None = Field(
        default=None, gt=0, description="Override of ECO-CO2-GAS (sensitivity)."
    )


def co2_emissions(
    request: Co2Request, annual: AnnualResult, *, config_dir: Path | str | None = None
) -> ToolResult[Co2Report]:
    """TL-14 ``co2``: annual CO₂ with stated factors and the reduction versus C0 (ECO-05)."""
    register = load_config("assumptions", config_dir)
    basis = load_config("design_basis", config_dir)
    factors = co2_factors(register, basis, grid_gas_kg_per_gj=request.grid_gas_kg_per_gj)
    carriers = carrier_balance(
        annual,
        gas_handling=request.gas_handling,
        associated_gas_lhv_mj_per_nm3=factors.associated_gas_lhv_mj_per_nm3,
        service_by_key=request.service_by_key,
    )
    report = co2_report(annual, carriers, factors, c0_co2_t_per_yr=request.c0_co2_t_per_yr)
    red = report.reduction_vs_c0_frac
    summary = (
        f"{annual.candidate_id}: CO2 {report.total_t:.0f} t/yr (direct {report.direct_t:.0f}, "
        f"electricity {report.electricity_t:.0f})"
        + (f", {100 * red:.0f} % below C0" if red is not None else "")
        + f"; factors gas {factors.grid_gas_kg_per_gj} / assoc "
        f"{factors.associated_gas_kg_per_gj:.1f} kg/GJ, power {factors.electricity_kg_per_kwh} "
        "kg/kWh"
    )
    return ToolResult[Co2Report](
        data=report,
        summary=summary[:SUMMARY_MAX_CHARS],
        warnings=carriers.warnings + report.warnings,
        assumption_ids=["ECO-CO2-GAS", "ECO-CO2-AG", "DB-37", "DB-36", "ECO-ELEC-NET"],
        sources=["RC-08", "spec §9.2 ECO-05", "DB-28"],
    )


# --------------------------------------------------------------------------- levelised cost
class LevelisedCost(StrictModel):
    """LCOH/LCOC for one analysis period (ECO-01)."""

    analysis_period_yr: int
    discount_rate_frac: float
    energy_escalation_real_frac: float
    heat_share_of_shared_frac: float
    allocation_basis: str = Field(description="ECO-ALLOC basis of heat_share_of_shared_frac.")
    lcoe_combined_usd_per_mwh: float | None = Field(
        description="All costs / (heat + cooling) delivered, allocation-free."
    )
    pv_cost_by_service_usd: dict[str, float]
    pv_heat_cost_usd: float
    pv_cool_cost_usd: float
    discounted_heat_mwh: float
    discounted_cool_mwh: float
    lcoh_usd_per_mwh: float | None
    lcoc_usd_per_mwh: float | None


class AnnualOpex(StrictModel):
    """Year-1 operating cost by service and by kind (USD/yr), excluding replacements."""

    by_service_usd: dict[str, float]
    om_usd: float
    electricity_usd: float
    grid_gas_usd: float
    co2_usd: float
    total_usd: float


def annual_opex(
    capex: CapexBreakdown, carriers: CarrierBalance, co2: Co2Report, book: CostBook
) -> AnnualOpex:
    """O&M (ECO-OM) + purchased electricity + grid gas + CO₂ on direct emissions (ECO-CO2-SCOPE)."""
    om_frac = book.usd_value("om_frac_of_capex_per_yr")
    p_el = book.usd_value("electricity_price_usd_per_mwh")
    p_gas = book.usd_value("gas_price_usd_per_mwh")
    p_co2 = book.usd_value("co2_price_usd_per_t")
    by_service = {
        s: om_frac * capex.by_service_usd[s]
        + carriers.electricity_purchased_by_service_mwh[s] * p_el
        + carriers.grid_gas_by_service_mwh[s] * p_gas
        + co2.direct_by_service_t[s] * p_co2
        for s in SERVICES
    }
    om = om_frac * capex.total_usd
    electricity = carriers.electricity_purchased_mwh * p_el
    gas = carriers.grid_gas_mwh * p_gas
    co2_cost = co2.direct_t * p_co2
    return AnnualOpex(
        by_service_usd=by_service,
        om_usd=om,
        electricity_usd=electricity,
        grid_gas_usd=gas,
        co2_usd=co2_cost,
        total_usd=om + electricity + gas + co2_cost,
    )


def heat_share_of_shared(carriers: CarrierBalance) -> tuple[float, str]:
    """ECO-ALLOC: share of shared costs charged to heat, and the basis used.

    By brine heat drawn per service when the results report it (an energy share, RC-10: free
    cooling and electric chillers draw no brine), else by delivered heat vs cooling.
    """
    brine = carriers.brine_heat_drawn_by_service_mwh
    if brine["heat"] + brine["cool"] > 0:
        return brine["heat"] / (brine["heat"] + brine["cool"]), "brine heat drawn"
    heat, cool = carriers.heat_delivered_mwh, carriers.cool_delivered_mwh
    if heat + cool > 0:
        return heat / (heat + cool), "delivered energy"
    return 1.0, "no delivered energy"


def levelised_cost(
    capex: CapexBreakdown,
    opex: AnnualOpex,
    carriers: CarrierBalance,
    book: CostBook,
    *,
    analysis_period_yr: int,
    energy_escalation_real_frac: float = 0.0,
) -> LevelisedCost:
    """Gippsland Eq. 1 for heat and cooling (ECO-01); shared costs split by delivered energy.

    Capex at t = 0, O&M and energy at t = 1…N (energy × (1+e)^(t−1)), replacements at multiples
    of their interval strictly before N. Constant real prices unless ``e`` ≠ 0 (ECO-REAL).
    """
    if analysis_period_yr < 1:
        raise ValueError("analysis period must be ≥ 1 yr")
    r = book.usd_value("discount_rate_frac")
    om_frac = book.usd_value("om_frac_of_capex_per_yr")
    years = range(1, analysis_period_yr + 1)
    disc = {t: (1.0 + r) ** -t for t in years}
    annuity = sum(disc.values())
    pv: dict[str, float] = {}
    for s in SERVICES:
        om = om_frac * capex.by_service_usd[s]
        energy = opex.by_service_usd[s] - om
        pv[s] = capex.by_service_usd[s] + sum(
            (om + energy * (1.0 + energy_escalation_real_frac) ** (t - 1)) * disc[t] for t in years
        )
    for line in capex.lines:
        if line.replacement_interval_yr:
            k = 1
            while (t := math.floor(k * line.replacement_interval_yr + 0.5)) < analysis_period_yr:
                pv[line.service] += line.cost_usd * (1.0 + r) ** -t
                k += 1
    heat, cool = carriers.heat_delivered_mwh, carriers.cool_delivered_mwh
    share, basis = heat_share_of_shared(carriers)
    if cool <= 0:
        share, basis = 1.0, "no cooling delivered"
    pv_heat = pv["heat"] + share * pv["shared"]
    pv_cool = pv["cool"] + (1.0 - share) * pv["shared"]
    total_mwh = (heat + cool) * annuity
    return LevelisedCost(
        analysis_period_yr=analysis_period_yr,
        discount_rate_frac=r,
        energy_escalation_real_frac=energy_escalation_real_frac,
        heat_share_of_shared_frac=share,
        allocation_basis=basis,
        lcoe_combined_usd_per_mwh=sum(pv.values()) / total_mwh if total_mwh > 0 else None,
        pv_cost_by_service_usd=pv,
        pv_heat_cost_usd=pv_heat,
        pv_cool_cost_usd=pv_cool,
        discounted_heat_mwh=heat * annuity,
        discounted_cool_mwh=cool * annuity,
        lcoh_usd_per_mwh=pv_heat / (heat * annuity) if heat > 0 else None,
        lcoc_usd_per_mwh=pv_cool / (cool * annuity) if cool > 0 else None,
    )


def capital_recovery_factor(rate_frac: float, period_yr: int) -> float:
    """CRF = r(1+r)^N / ((1+r)^N − 1), the fixed-charge rate without taxes (ECO-06)."""
    if rate_frac == 0:
        return 1.0 / period_yr
    growth = (1.0 + rate_frac) ** period_yr
    return rate_frac * growth / (growth - 1.0)


def lcoh_fixed_charge_rate(
    capex: CapexBreakdown,
    opex: AnnualOpex,
    carriers: CarrierBalance,
    book: CostBook,
    *,
    analysis_period_yr: int,
) -> float | None:
    """ECO-06 ``LCOH = (FCR·TCC + FOC)/AEP + VOC`` (NREL desalination App. E), no replacements."""
    heat, cool = carriers.heat_delivered_mwh, carriers.cool_delivered_mwh
    if heat <= 0:
        return None
    share, _ = heat_share_of_shared(carriers)
    if cool <= 0:
        share = 1.0
    fcr = capital_recovery_factor(book.usd_value("discount_rate_frac"), analysis_period_yr)
    om_frac = book.usd_value("om_frac_of_capex_per_yr")
    tcc = capex.by_service_usd["heat"] + share * capex.by_service_usd["shared"]
    foc = om_frac * tcc
    variable = (opex.by_service_usd["heat"] - om_frac * capex.by_service_usd["heat"]) + share * (
        opex.by_service_usd["shared"] - om_frac * capex.by_service_usd["shared"]
    )
    return (fcr * tcc + foc) / heat + variable / heat


# --------------------------------------------------------------------------- benchmarks
class BenchmarkCheck(StrictModel):
    """LCOH against an RC-10 benchmark (sanity only, never pass/fail)."""

    name: str
    benchmark_usd_per_mwh: float
    lcoh_usd_per_mwh: float
    ratio: float
    below_benchmark: bool
    note: str


def benchmark_checks(book: CostBook, lcoh_usd_per_mwh: float | None) -> list[BenchmarkCheck]:
    """RC-10 Benchmarks: ACM 2026 heat price cap (≈ 138 $/MWh), Gippsland GRAC, WVU (ECO-07)."""
    if lcoh_usd_per_mwh is None:
        return []
    notes = {
        "heat_price_cap_acm_usd_per_mwh_th": "NL consumer heat price cap 2026: a competitive "
        "LCOH sits below it",
        "benchmark_gippsland_grac_lcoh_usd_per_mwh_th": "Gippsland GRAC LCOH (price year not "
        "stated)",
        "benchmark_wvu_lcoh_usd_per_mwh_th": "WVU heating + absorption cooling, 2012 $, not "
        "escalated",
    }
    out = []
    for name, note in notes.items():
        value = book.usd_value(name)
        out.append(
            BenchmarkCheck(
                name=name,
                benchmark_usd_per_mwh=value,
                lcoh_usd_per_mwh=lcoh_usd_per_mwh,
                ratio=lcoh_usd_per_mwh / value,
                below_benchmark=lcoh_usd_per_mwh < value,
                note=note,
            )
        )
    return out


# --------------------------------------------------------------------------- economics tool
class EconomicsRequest(StrictModel):
    """Input of the ``economics`` tool (the daily results come as an ``AnnualResult``)."""

    capex: CapexRequest
    gas_handling: GasHandling | None = None
    service_by_key: dict[str, Service] = Field(
        default_factory=dict,
        description="Consumer/burner key → service; unknown keys use keyword rules, else shared.",
    )
    c0_co2_t_per_yr: float | None = Field(default=None, gt=0)
    energy_escalation_real_frac: float | None = Field(
        default=None,
        ge=-0.05,
        le=0.1,
        description=(
            "Real escalation of energy and CO2 prices; None ⇒ ECO-REAL "
            "(1 + energy_escalation_frac)/(1 + inflation_general_frac) − 1."
        ),
    )


class EconomicsReport(StrictModel):
    """Output of the ``economics`` tool."""

    candidate_id: str
    currency: str
    capex: CapexBreakdown
    carriers: CarrierBalance
    co2: Co2Report
    opex: AnnualOpex
    levelised: list[LevelisedCost] = Field(description="Base period first, then the 20-yr case.")
    lcoh_fcr_usd_per_mwh: float | None
    benchmarks: list[BenchmarkCheck]
    cost_items: list[CostItemUsed]
    kpis: KPISet


@dataclass(frozen=True)
class _Inputs:
    request: EconomicsRequest
    carriers: CarrierBalance
    co2: Co2Report
    register: AssumptionsRegister


def _periods(book: CostBook) -> list[int]:
    base = round(book.usd_value("analysis_period_yr"))
    return [base] if base == SHORT_PERIOD_YR else [base, SHORT_PERIOD_YR]


def real_energy_escalation(book: CostBook, nominal_frac: float | None = None) -> float:
    """ECO-REAL: ``(1 + e_nominal)/(1 + inflation) − 1`` from the RC-10 defaults in the config."""
    nominal = book.usd_value("energy_escalation_frac") if nominal_frac is None else nominal_frac
    return (1.0 + nominal) / (1.0 + book.usd_value("inflation_general_frac")) - 1.0


def _escalation(inputs: _Inputs, book: CostBook) -> float:
    given = inputs.request.energy_escalation_real_frac
    return real_energy_escalation(book) if given is None else given


def _evaluate(inputs: _Inputs, book: CostBook) -> tuple[CapexBreakdown, AnnualOpex, list]:
    capex = capex_breakdown(inputs.request.capex, book, inputs.register)
    opex = annual_opex(capex, inputs.carriers, inputs.co2, book)
    levelised = [
        levelised_cost(
            capex,
            opex,
            inputs.carriers,
            book,
            analysis_period_yr=period,
            energy_escalation_real_frac=_escalation(inputs, book),
        )
        for period in _periods(book)
    ]
    return capex, opex, levelised


def _prepare(
    request: EconomicsRequest, annual: AnnualResult, config_dir: Path | str | None
) -> _Inputs:
    if annual.candidate_id != request.capex.candidate_id:
        raise ValueError("annual.candidate_id does not match capex.candidate_id")
    register = load_config("assumptions", config_dir)
    basis = load_config("design_basis", config_dir)
    factors = co2_factors(register, basis)
    carriers = carrier_balance(
        annual,
        gas_handling=request.gas_handling,
        associated_gas_lhv_mj_per_nm3=factors.associated_gas_lhv_mj_per_nm3,
        service_by_key=request.service_by_key,
    )
    co2 = co2_report(annual, carriers, factors, c0_co2_t_per_yr=request.c0_co2_t_per_yr)
    return _Inputs(request=request, carriers=carriers, co2=co2, register=register)


def economics_kpis(
    base: KPISet | None, candidate_id: str, report_parts: Mapping[str, object]
) -> KPISet:
    """Fill the economics/CO₂/carrier fields of ``KPISet`` (extend, never rename)."""
    data = base.model_dump() if base is not None else {"candidate_id": candidate_id}
    data.update(report_parts)
    return KPISet.model_validate(data)


def economics(
    request: EconomicsRequest,
    annual: AnnualResult,
    *,
    config_dir: Path | str | None = None,
    book: CostBook | None = None,
) -> ToolResult[EconomicsReport]:
    """TL-14 ``economics``: CAPEX, OPEX, LCOH/LCOC (30 and 20 yr), CO₂ and benchmarks (§9.2)."""
    book = (book or CostBook.load(config_dir)).fresh()
    inputs = _prepare(request, annual, config_dir)
    capex, opex, levelised = _evaluate(inputs, book)
    base_period = levelised[0]
    short = next((lv for lv in levelised if lv.analysis_period_yr == SHORT_PERIOD_YR), None)
    fcr = lcoh_fixed_charge_rate(
        capex, opex, inputs.carriers, book, analysis_period_yr=base_period.analysis_period_yr
    )
    benchmarks = benchmark_checks(book, base_period.lcoh_usd_per_mwh)
    carriers, co2 = inputs.carriers, inputs.co2
    kpis = economics_kpis(
        annual.kpis,
        annual.candidate_id,
        {
            "currency": book.currency,
            "capex_million": capex.total_usd / 1e6,
            "opex_million_per_yr": opex.total_usd / 1e6,
            "lcoh_per_mwh": base_period.lcoh_usd_per_mwh,
            "lcoc_per_mwh": base_period.lcoc_usd_per_mwh,
            "lcoh_20yr_per_mwh": short.lcoh_usd_per_mwh if short else None,
            "lcoc_20yr_per_mwh": short.lcoc_usd_per_mwh if short else None,
            "electricity_consumed_mwh": carriers.electricity_consumed_mwh,
            "electricity_generated_mwh": carriers.electricity_generated_mwh,
            "electricity_purchased_mwh": carriers.electricity_purchased_mwh,
            "gas_fuel_mwh": carriers.gas_fuel_mwh,
            "grid_gas_fuel_mwh": carriers.grid_gas_mwh,
            "co2_t_per_yr": co2.total_t,
            "co2_reduction_vs_c0_frac": co2.reduction_vs_c0_frac,
        },
    )
    report = EconomicsReport(
        candidate_id=annual.candidate_id,
        currency=book.currency,
        capex=capex,
        carriers=carriers,
        co2=co2,
        opex=opex,
        levelised=levelised,
        lcoh_fcr_usd_per_mwh=fcr,
        benchmarks=benchmarks,
        cost_items=book.used_items(),
        kpis=kpis,
    )
    warnings = capex.warnings + carriers.warnings + co2.warnings
    if any(item.verify for item in report.cost_items):
        warnings.append("uses VERIFY-tagged cost functions (RC-05 chillers)")
    return ToolResult[EconomicsReport](
        data=report,
        summary=_summary(report),
        warnings=warnings,
        assumption_ids=list(ASSUMPTION_IDS),
        sources=list(SOURCES),
    )


def _fmt(value: float | None, spec: str = ".1f") -> str:
    return "n/a" if value is None else format(value, spec)


def _summary(report: EconomicsReport) -> str:
    base = report.levelised[0]
    short = next((lv for lv in report.levelised if lv.analysis_period_yr == SHORT_PERIOD_YR), None)
    cap = next((b for b in report.benchmarks if b.name.startswith("heat_price_cap")), None)
    red = report.co2.reduction_vs_c0_frac
    text = (
        f"{report.candidate_id}: CAPEX {report.capex.total_usd / 1e6:.1f} M$, OPEX "
        f"{report.opex.total_usd / 1e6:.2f} M$/yr; LCOH {_fmt(base.lcoh_usd_per_mwh)} $/MWh "
        f"({base.analysis_period_yr} yr; 20 yr "
        f"{_fmt(short.lcoh_usd_per_mwh if short else None)}), LCOC "
        f"{_fmt(base.lcoc_usd_per_mwh)} $/MWh; CO2 {report.co2.total_t:.0f} t/yr"
        + (f" ({100 * red:.0f} % below C0)" if red is not None else "")
        + (
            f"; {'below' if cap.below_benchmark else 'ABOVE'} ACM cap "
            f"{cap.benchmark_usd_per_mwh:.0f} $/MWh"
            if cap
            else ""
        )
    )
    return text[:SUMMARY_MAX_CHARS]


# --------------------------------------------------------------------------- sensitivity
class TornadoBar(StrictModel):
    """One input varied over its sensitivity bounds, the rest at base (ECO-06 tornado)."""

    input_name: str
    unit: str
    base_value: float | None = Field(description="None for tables (varied as a multiplier).")
    low_value: float
    high_value: float
    kpi_base: float
    kpi_at_low: float
    kpi_at_high: float
    swing: float


class SensitivityReport(StrictModel):
    """Tornado-ready sensitivity of LCOH or LCOC (top inputs by swing)."""

    candidate_id: str
    kpi: Literal["lcoh", "lcoc"]
    unit: str
    analysis_period_yr: int
    kpi_base: float
    bars: list[TornadoBar]
    min_sensitivity_frac: float


def _kpi(levelised: LevelisedCost, kpi: Literal["lcoh", "lcoc"]) -> float | None:
    return levelised.lcoh_usd_per_mwh if kpi == "lcoh" else levelised.lcoc_usd_per_mwh


def cost_sensitivity(
    request: EconomicsRequest,
    annual: AnnualResult,
    *,
    kpi: Literal["lcoh", "lcoc"] = "lcoh",
    top_n: int = 8,
    config_dir: Path | str | None = None,
    book: CostBook | None = None,
    extra_inputs: Iterable[str] = (),
) -> ToolResult[SensitivityReport]:
    """TL-14 ``sensitivity``: one-at-a-time LCOH/LCOC swings, sorted for a tornado (ECO-06).

    Each cost input the base case reads is set to its low and high bound — ±30 % or the stated
    range, whichever is wider (DB-35, ECO-04); tables and named parameters move by ±30 %. The
    real energy-price escalation (ECO-REAL) is added as one bar from its base to the RC-10
    range (energy 3–7 % nominal minus general inflation).
    """
    book = (book or CostBook.load(config_dir)).fresh()
    inputs = _prepare(request, annual, config_dir)
    _, _, levelised = _evaluate(inputs, book)
    base = _kpi(levelised[0], kpi)
    if base is None:
        raise ValueError(f"{kpi} undefined for {annual.candidate_id} (no delivered energy)")
    names = [n for n in book.used if n not in NOT_SENSITIVITY_INPUTS and "benchmark" not in n]
    names = [n for n in names if not n.startswith("heat_price_cap")]
    names += [n for n in extra_inputs if n not in names]
    bars: list[TornadoBar] = []
    for name in names:
        cost = book.usd(name)
        if isinstance(cost.value, dict):
            frac = book.min_sensitivity_frac
            factors, base_value, low, high, unit = (
                (1 - frac, 1 + frac),
                None,
                1 - frac,
                1 + frac,
                ("× base"),
            )
        else:
            base_value = cost.scalar()
            if base_value == 0 or cost.sensitivity_low is None or cost.sensitivity_high is None:
                continue
            low, high, unit = cost.sensitivity_low, cost.sensitivity_high, cost.unit
            factors = (low / base_value, high / base_value)
        results = []
        for factor in factors:
            _, _, lv = _evaluate(inputs, book.scaled(name, factor))
            value = _kpi(lv[0], kpi)
            assert value is not None
            results.append(value)
        bars.append(
            TornadoBar(
                input_name=name,
                unit=unit,
                base_value=base_value,
                low_value=low,
                high_value=high,
                kpi_base=base,
                kpi_at_low=results[0],
                kpi_at_high=results[1],
                swing=abs(results[1] - results[0]),
            )
        )
    bars.append(_escalation_bar(inputs, book, kpi, base))
    bars.sort(key=lambda bar: bar.swing, reverse=True)
    report = SensitivityReport(
        candidate_id=annual.candidate_id,
        kpi=kpi,
        unit=f"{book.currency}/MWh",
        analysis_period_yr=levelised[0].analysis_period_yr,
        kpi_base=base,
        bars=bars[:top_n],
        min_sensitivity_frac=book.min_sensitivity_frac,
    )
    top = ", ".join(f"{b.input_name} {b.swing:.1f}" for b in report.bars[:3])
    return ToolResult[SensitivityReport](
        data=report,
        summary=(
            f"{annual.candidate_id} {kpi.upper()} {base:.1f} $/MWh; largest swings ($/MWh): {top}"
        )[:SUMMARY_MAX_CHARS],
        assumption_ids=["DB-35", "ECO-REAL"],
        sources=["RC-10", "spec §9.2 ECO-04, ECO-06"],
    )


def _escalation_bar(
    inputs: _Inputs, book: CostBook, kpi: Literal["lcoh", "lcoc"], base: float
) -> TornadoBar:
    """ECO-REAL: real energy escalation = (1 + e_nominal)/(1 + inflation) − 1 over RC-10's range."""
    nominal = book.stated_range("energy_escalation_frac")
    assert nominal is not None
    real = [real_energy_escalation(book, e) for e in nominal]
    results = []
    for value in real:
        request = inputs.request.model_copy(update={"energy_escalation_real_frac": value})
        _, _, lv = _evaluate(_Inputs(request, inputs.carriers, inputs.co2, inputs.register), book)
        result = _kpi(lv[0], kpi)
        assert result is not None
        results.append(result)
    return TornadoBar(
        input_name="energy_escalation_real_frac",
        unit="1/yr (real)",
        base_value=_escalation(inputs, book),
        low_value=real[0],
        high_value=real[1],
        kpi_base=base,
        kpi_at_low=results[0],
        kpi_at_high=results[1],
        swing=abs(results[1] - results[0]),
    )


__all__ = [
    "TOOL_ID",
    "AnnualOpex",
    "BenchmarkCheck",
    "CapexBreakdown",
    "CapexLine",
    "CapexRequest",
    "CarrierBalance",
    "ChillerPlantCost",
    "Co2Factors",
    "Co2Report",
    "Co2Request",
    "CostBook",
    "CostItemUsed",
    "EconomicsReport",
    "EconomicsRequest",
    "HexDuty",
    "LevelisedCost",
    "SensitivityReport",
    "TornadoBar",
    "UsdCost",
    "WellCost",
    "annual_opex",
    "benchmark_checks",
    "capex_breakdown",
    "capital_recovery_factor",
    "carrier_balance",
    "chiller_plant_cost_usd",
    "chp_electric_capacity_mw",
    "classify_service",
    "co2_emissions",
    "co2_factors",
    "co2_report",
    "cost_sensitivity",
    "economics",
    "economics_kpis",
    "free_cooling_hex_equipment_cost_usd",
    "lcoh_fixed_charge_rate",
    "levelised_cost",
    "pipe_cost_usd",
    "plate_hex_cost_usd",
    "well_cost_musd",
    "well_cost_smooth_fit_musd",
]
