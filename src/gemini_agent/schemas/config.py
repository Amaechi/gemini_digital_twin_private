"""Schemas for the YAML files in ``config/`` and a loader that validates them.

One model per file:

=====================  ======================  ==========================================
file                   model                   source
=====================  ======================  ==========================================
design_basis.yaml      ``DesignBasis``         spec §2.1 DB-01…DB-19 [P]
limits.yaml            ``HardLimits``          CLAUDE.md golden rule 4 (subset of DB-10…17)
assumptions.yaml       ``AssumptionsRegister`` spec §2.3–2.4 DB-30…DB-40 [A]
climate_nl.yaml        ``ClimateConfig``       RC-07, spec §2.3 DB-34
cost_assumptions.yaml  ``CostAssumptions``     RC-10, spec §9.2 ECO-01…04
llm.yaml               ``LLMConfig``           spec §4.2–4.3
=====================  ======================  ==========================================

The config directory defaults to ``<repo>/config`` and can be overridden with the
``GEOAGENT_CONFIG_DIR`` environment variable or the ``config_dir`` argument.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Literal, overload

import yaml
from pydantic import BaseModel, Field, JsonValue, model_validator

from gemini_agent.schemas.common import SourceTag, StrictModel

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR_ENV = "GEOAGENT_CONFIG_DIR"
MONTHS = 12


# --------------------------------------------------------------------------- design basis
class ReservoirBasis(StrictModel):
    """Reservoir inputs (spec §2.1)."""

    mid_depth_m: float = Field(gt=0, description="DB-01 reservoir mid-depth, TVD.")
    gross_thickness_m: float = Field(gt=0, description="DB-02 gross thickness h.")
    geothermal_gradient_k_per_km: float = Field(gt=0, description="DB-03 geothermal gradient.")
    surface_temperature_degc: float = Field(description="DB-04 surface temperature.")
    pressure_barg: float = Field(gt=0, description="DB-05 reservoir pressure, bar gauge.")
    permeability_md: float = Field(gt=0, description="DB-06 permeability (mD).")
    other_depths_allowed: bool = Field(description="DB-07 other depths allowed.")
    other_depths_pressure_model: Literal["hydrostatic"] = Field(
        description="DB-07 pressure at other depths: hydrostatic + gradient temperature."
    )


class BrineBasis(StrictModel):
    """Brine and associated-gas inputs (spec §2.1)."""

    density_kg_m3: float = Field(gt=0, description="DB-08 brine density.")
    viscosity_pa_s: float = Field(gt=0, description="DB-08 brine viscosity.")
    gas_liquid_ratio_nm3_per_m3: float = Field(ge=0, description="DB-09 gas-liquid ratio.")
    gas_ch4_mol_frac: float = Field(ge=0, le=1, description="DB-09 CH4 mole fraction.")
    gas_co2_mol_frac: float = Field(ge=0, le=1, description="DB-09 CO2 mole fraction.")

    @model_validator(mode="after")
    def _check_gas_fractions(self) -> BrineBasis:
        total = self.gas_ch4_mol_frac + self.gas_co2_mol_frac
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"gas mole fractions must sum to 1, got {total}")
        return self


class DoubletBasis(StrictModel):
    """Doublet operating envelope (spec §2.1)."""

    count_min: int = Field(ge=1, description="DB-10 minimum number of doublets.")
    count_max: int = Field(ge=1, description="DB-10 maximum number of doublets.")
    flow_min_active_m3h: float = Field(gt=0, description="DB-11 min flow per active doublet.")
    flow_max_m3h: float = Field(gt=0, description="DB-11 max flow per doublet.")
    brine_cooling_max_k: float = Field(gt=0, description="DB-12 producer→injector cooling.")
    pump_hydraulic_efficiency_frac: float = Field(gt=0, le=1, description="DB-13 (see DB-40).")
    injection_equals_production: bool = Field(description="DB-14 equal volumes.")
    same_reservoir: bool = Field(description="DB-14 injection into the same reservoir.")
    operation_days_per_yr: int = Field(ge=1, le=366, description="DB-15 (interpretation DB-30).")

    @model_validator(mode="after")
    def _check_ranges(self) -> DoubletBasis:
        if self.count_min > self.count_max:
            raise ValueError("count_min must be <= count_max")
        if self.flow_min_active_m3h > self.flow_max_m3h:
            raise ValueError("flow_min_active_m3h must be <= flow_max_m3h")
        return self


class DemandBasis(StrictModel):
    """One demand side (heating DB-16/18 or cooling DB-17/19)."""

    supply_temperature_degc: float
    demand_csv: str = Field(description="Path relative to the repository root.")
    csv_date_column: str
    csv_value_column: str = Field(description="Column with day-average demand in MW.")
    peak_mw: float = Field(ge=0)
    peak_date: dt.date
    annual_gwh: float = Field(ge=0)
    nonzero_days: int = Field(ge=0, le=366)

    def csv_path(self, root: Path | None = None) -> Path:
        """Absolute path of the demand CSV."""
        return (root or REPO_ROOT) / self.demand_csv


class DesignBasis(StrictModel):
    """Fixed design inputs DB-01…DB-19 (spec §2.1), loaded from ``config/design_basis.yaml``."""

    schema_version: int = 1
    source: str
    year: int
    reservoir: ReservoirBasis
    brine: BrineBasis
    doublets: DoubletBasis
    heating: DemandBasis
    cooling: DemandBasis


# --------------------------------------------------------------------------- hard limits
class HardLimits(StrictModel):
    """Hard limits that must never be violated silently (CLAUDE.md golden rule 4)."""

    schema_version: int = 1
    source: str
    doublet_count_min: int = Field(ge=1)
    doublet_count_max: int = Field(ge=1)
    flow_min_active_m3h: float = Field(gt=0)
    flow_max_m3h: float = Field(gt=0)
    brine_cooling_max_k: float = Field(gt=0)
    pump_hydraulic_efficiency_frac: float = Field(gt=0, le=1)
    injection_equals_production: Literal[True]
    heating_supply_degc: float
    cooling_supply_degc: float
    ch4_venting_allowed: Literal[False]

    @model_validator(mode="after")
    def _check_ranges(self) -> HardLimits:
        if self.doublet_count_min > self.doublet_count_max:
            raise ValueError("doublet_count_min must be <= doublet_count_max")
        if self.flow_min_active_m3h > self.flow_max_m3h:
            raise ValueError("flow_min_active_m3h must be <= flow_max_m3h")
        return self


# --------------------------------------------------------------------------- assumptions
class Assumption(StrictModel):
    """One entry of the assumptions register (CLAUDE.md golden rule 2)."""

    id: str = Field(pattern=r"^[A-Z][A-Z0-9]*(-[A-Za-z0-9]+)+$", description="e.g. 'DB-31'.")
    title: str
    value: JsonValue
    unit: str | None = None
    justification: str
    source: str
    sensitivity: JsonValue
    tag: SourceTag = SourceTag.A
    status: Literal["default", "confirmed", "superseded"] = "default"
    added_by: str = Field(description="Work package that added the entry, e.g. 'WP-00'.")


class AssumptionsRegister(StrictModel):
    """Register of assumptions and defaults; every [A] value used by a tool must be here."""

    schema_version: int = 1
    assumptions: list[Assumption]

    @model_validator(mode="after")
    def _check_unique_ids(self) -> AssumptionsRegister:
        ids = [item.id for item in self.assumptions]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate assumption ids: {duplicates}")
        return self

    def get(self, assumption_id: str) -> Assumption:
        """Return the entry with this id (KeyError if absent)."""
        for item in self.assumptions:
            if item.id == assumption_id:
                return item
        raise KeyError(assumption_id)


# --------------------------------------------------------------------------- climate
class MonthlyClimate(StrictModel):
    """Monthly normals Jan…Dec (RC-07)."""

    dry_bulb_degc: list[float] = Field(min_length=MONTHS, max_length=MONTHS)
    relative_humidity_pct: list[float] = Field(min_length=MONTHS, max_length=MONTHS)
    wet_bulb_degc: list[float] = Field(min_length=MONTHS, max_length=MONTHS)

    @model_validator(mode="after")
    def _check_physical(self) -> MonthlyClimate:
        if any(not 0.0 < rh <= 100.0 for rh in self.relative_humidity_pct):
            raise ValueError("relative humidity must be in (0, 100] %")
        if any(wb > db for wb, db in zip(self.wet_bulb_degc, self.dry_bulb_degc, strict=True)):
            raise ValueError("wet-bulb must not exceed dry-bulb")
        return self


class InterpolationRule(StrictModel):
    """How the daily series is built from monthly normals (DB-34a)."""

    method: Literal["piecewise_linear"]
    anchor_day_of_month: int = Field(ge=1, le=28)
    wrap_year: bool


class ClimatePoint(StrictModel):
    """A dry-bulb / wet-bulb pair, e.g. the summer design point (DB-34b)."""

    dry_bulb_degc: float
    wet_bulb_degc: float
    basis: str

    @model_validator(mode="after")
    def _check_wb(self) -> ClimatePoint:
        if self.wet_bulb_degc > self.dry_bulb_degc:
            raise ValueError("wet-bulb must not exceed dry-bulb")
        return self


class ClimateConfig(StrictModel):
    """Netherlands climate data and rules (RC-07, spec §2.3 DB-34)."""

    schema_version: int = 1
    source: str
    station: str
    normals_period: str
    monthly: MonthlyClimate
    wet_bulb_method: str
    interpolation: InterpolationRule
    design_point: ClimatePoint
    warm_case: ClimatePoint
    annual_mean_degc: float
    shallow_ground_degc: float
    exergy_dead_state: Literal["daily_dry_bulb"]


# --------------------------------------------------------------------------- costs
CostValue = float | dict[int, float] | dict[str, float] | None


class CostItem(StrictModel):
    """One cost assumption (RC-10, ECO-04): value, unit, range, source, tag.

    Values are stored in their **native** currency, unit and price year exactly as RC-10 states
    them; USD figures are derived in code (economics tool, WP-08) from ``eur_to_usd``,
    ``dea_eur2020_to_eur2025`` and ``cepci_index`` (when ``escalation_index == "cepci"``).
    """

    value: CostValue = Field(
        union_mode="left_to_right",
        description=(
            "Scalar, depth table {depth_m: value} or named parameters; null = unset placeholder."
        ),
    )
    unit: str = Field(description="Unit of `value` in its native currency, e.g. 'M EUR/MW_th'.")
    range: tuple[float, float] | str | None = Field(
        description="(low, high) in the unit of `value`, or text; None only for unset placeholders."
    )
    usd_range: tuple[float, float] | None = Field(
        default=None,
        description=(
            "Sensitivity range in USD as stated on RC-10, already converted (and, for "
            "escalation_index=cepci items, already escalated to 2023 USD); do not convert again."
        ),
    )
    currency: Literal["EUR", "USD", "AUD"] | None = Field(
        default=None, description="Currency of `value`; None for dimensionless items."
    )
    price_year: int | None = Field(
        default=None, ge=1900, le=2100, description="Price year of `value` (e.g. 2025 for EUR2025)."
    )
    escalation_index: Literal["cepci"] | None = Field(
        default=None,
        description="Escalate to current prices with this index (RC-10 'escalation A').",
    )
    source: str
    tag: SourceTag
    placeholder: bool = Field(
        default=False, description="ECO-04 '[A] placeholder': unsupported by a labelled source."
    )
    note: str | None = None

    @model_validator(mode="after")
    def _check_value(self) -> CostItem:
        if self.value is None and not self.placeholder:
            raise ValueError("value may be null only for a placeholder item")
        if self.range is None and self.value is not None and not isinstance(self.value, dict):
            raise ValueError("a scalar cost item needs a range")
        for name in ("range", "usd_range"):
            bounds = getattr(self, name)
            if isinstance(bounds, tuple) and bounds[0] > bounds[1]:
                raise ValueError(f"{name} low must be <= high")
        if self.escalation_index is not None and self.price_year is None:
            raise ValueError("an escalated item needs a price_year")
        return self


class CostAssumptions(StrictModel):
    """Cost assumptions (``config/cost_assumptions.yaml``)."""

    schema_version: int = 1
    source: str
    reporting_currency: str
    min_sensitivity_frac: float = Field(
        gt=0,
        lt=1,
        description="DB-35 / ECO-04: sensitivity is ±30 % or the item's range, whichever is wider.",
    )
    items: dict[str, CostItem]

    @model_validator(mode="after")
    def _check_cepci_years(self) -> CostAssumptions:
        escalated = {
            name: item.price_year
            for name, item in self.items.items()
            if item.escalation_index == "cepci"
        }
        if not escalated:
            return self
        index = self.items.get("cepci_index")
        if index is None or not isinstance(index.value, dict):
            raise ValueError("items with escalation_index 'cepci' need a 'cepci_index' table")
        missing = {name: year for name, year in escalated.items() if year not in index.value}
        if missing:
            raise ValueError(f"cepci_index has no entry for the price year of {missing}")
        return self

    def unset_placeholders(self) -> list[str]:
        """Names of placeholder items that still have no value (must be empty at submission)."""
        return sorted(name for name, item in self.items.items() if item.value is None)


# --------------------------------------------------------------------------- LLM
class OllamaSettings(StrictModel):
    """Local Ollama runtime (OpenAI-compatible endpoint)."""

    base_url: str = Field(pattern=r"^https?://")
    num_ctx: int = Field(ge=32768, description="Spec §4.3: >= 32K, 64K preferred.")
    keep_alive: str


class ModelProfile(StrictModel):
    """Model tags for one hardware profile (spec §4.3)."""

    description: str
    orchestrator: str = Field(description="Orchestrator / most agents.")
    coder: str = Field(description="Code writer (Model Builder).")
    alternatives: list[str] = Field(default_factory=list)


class Temperatures(StrictModel):
    """Sampling temperatures per task kind (spec §4.3)."""

    extraction: float = Field(ge=0, le=2)
    formatting: float = Field(ge=0, le=2)
    planning: float = Field(ge=0, le=2)


class CallLimits(StrictModel):
    """Per-call limits of one kind of LLM call; how each value reaches Ollama: ``agents.engine``."""

    thinking: bool = Field(
        default=False,
        description="False sends reasoning_effort 'none' (Ollama: think=false), so the JSON "
        "grammar applies from the first token; True keeps the model's default.",
    )
    max_output_tokens: int = Field(
        default=2048, ge=256, description="Cap per call, sent as max_tokens (Ollama num_predict)."
    )
    request_timeout_s: float = Field(
        default=300.0, gt=0, description="Deadline per model request (pydantic-ai)."
    )


class ModelBuilderCallSettings(CallLimits):
    """Limits of the Model Builder coder call (``calls.model_builder``), a long-answer role.

    One draft is a model file, a test file and REFERENCE.md (1.5–3 k output tokens at laptop
    speed), so it needs a longer deadline and a larger output cap than a ``Decision`` call. Added
    after the local S7 demo: every draft hit the SDK default timeout (WP-12 follow-up).
    """

    max_output_tokens: int = Field(
        default=8192, ge=256, description="Cap per draft, sent as max_tokens (Ollama num_predict)."
    )
    request_timeout_s: float = Field(
        default=1200.0, gt=0, description="Deadline per draft request (pydantic-ai)."
    )


class CallSettings(CallLimits):
    """Per-call limits of every structured-output LLM call (``Decision`` / ``OptionChoice``).

    Added after the L-1 eval (reasoning in the content used up the phase budgets; one call hung
    for 30 min). ``model_builder`` holds the limits of the coder call, which otherwise shares
    the same settings path (``agents.engine.model_settings``).
    """

    model_builder: ModelBuilderCallSettings = Field(
        default_factory=ModelBuilderCallSettings,
        description="Limits of the Model Builder coder call (long answers).",
    )


class LLMConfig(StrictModel):
    """LLM engine selection (``config/llm.yaml``)."""

    schema_version: int = 1
    engine: Literal["policy", "llm"]
    active_profile: str
    ollama: OllamaSettings
    profiles: dict[str, ModelProfile]
    temperatures: Temperatures
    max_output_retries: int = Field(ge=0, le=5)
    seed: int
    calls: CallSettings = Field(default_factory=CallSettings)

    @model_validator(mode="after")
    def _check_profile(self) -> LLMConfig:
        if self.active_profile not in self.profiles:
            raise ValueError(
                f"active_profile '{self.active_profile}' not in {sorted(self.profiles)}"
            )
        return self

    def active(self) -> ModelProfile:
        """Return the selected hardware profile."""
        return self.profiles[self.active_profile]


# --------------------------------------------------------------------------- loader
CONFIG_MODELS: dict[str, type[BaseModel]] = {
    "design_basis": DesignBasis,
    "limits": HardLimits,
    "assumptions": AssumptionsRegister,
    "climate_nl": ClimateConfig,
    "cost_assumptions": CostAssumptions,
    "llm": LLMConfig,
}


def default_config_dir() -> Path:
    """``$GEOAGENT_CONFIG_DIR`` if set, else ``<repo>/config``."""
    override = os.environ.get(CONFIG_DIR_ENV)
    return Path(override) if override else REPO_ROOT / "config"


ConfigName = Literal[
    "design_basis", "limits", "assumptions", "climate_nl", "cost_assumptions", "llm"
]


@overload
def load_config(
    name: Literal["design_basis"], config_dir: Path | str | None = None
) -> DesignBasis: ...


@overload
def load_config(name: Literal["limits"], config_dir: Path | str | None = None) -> HardLimits: ...


@overload
def load_config(
    name: Literal["assumptions"], config_dir: Path | str | None = None
) -> AssumptionsRegister: ...


@overload
def load_config(
    name: Literal["climate_nl"], config_dir: Path | str | None = None
) -> ClimateConfig: ...


@overload
def load_config(
    name: Literal["cost_assumptions"], config_dir: Path | str | None = None
) -> CostAssumptions: ...


@overload
def load_config(name: Literal["llm"], config_dir: Path | str | None = None) -> LLMConfig: ...


@overload
def load_config(name: str, config_dir: Path | str | None = None) -> BaseModel: ...


def load_config(name: str, config_dir: Path | str | None = None) -> BaseModel:
    """Load ``<config_dir>/<name>.yaml`` and validate it against its schema.

    Raises ``KeyError`` for an unknown name, ``FileNotFoundError`` for a missing file and
    ``pydantic.ValidationError`` for invalid content.
    """
    if name not in CONFIG_MODELS:
        raise KeyError(f"unknown config '{name}', expected one of {sorted(CONFIG_MODELS)}")
    directory = Path(config_dir) if config_dir is not None else default_config_dir()
    path = directory / f"{name}.yaml"
    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return CONFIG_MODELS[name].model_validate(raw)


def load_all_configs(config_dir: Path | str | None = None) -> dict[str, BaseModel]:
    """Load and validate every config file; returns ``{name: model}``."""
    return {name: load_config(name, config_dir) for name in CONFIG_MODELS}
