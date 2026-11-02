"""TL-11 ``simulate_day``: build a candidate's GEMINI project for one day and run it (spec §6).

One day = one GEMINI steady state at the day-average loads (the demand CSVs are day-average MW)
and the day's ambient dry/wet bulb. Procedure:

1. TL-09 :func:`generate_plant` builds the project for the day's loads and set-points (doublet
   flows → TL-05 ESP frequency and wellhead temperature; district flow from the heating demand).
2. GEMINI runs it (``build_project`` + ``Solver.run``).
3. Plate-HEX control: if a plate HEX would cool its brine more than DB-12 (40 K) or the district
   water after the plate HEXs and CHP would exceed DB-16 (100 °C), the share of district water
   through the plate HEXs is bisected down (the rest bypasses them). A doublet still cooled more
   than 40 K at the end makes the day invalid (hard limit, never only a warning).
4. Heat pumps run WP-03's closed-form solver (CFG-HP): a source-limited point holds the brine at
   its floor and delivers less than 100 °C; an idle one passes both streams unchanged. Their
   only bypass is a genuine capacity control: with a heat-pump nameplate
   (``equipment_sizes["hthp_heating_mw"]``) the share of district water through the heat pumps
   is bisected down until their heat fits it (the reason is recorded in ``controls``). Every
   heat-pump point reported outside its RC-04 limits (``within_limits`` False) or with HTHP
   datasheet values MISSING is flagged in ``DayResult.flags`` and ``heat_pump_flags`` (never
   hidden); its ``heat_pump_point`` is the verifier evidence. An unphysical output (non-finite,
   outlets outside their bounds) makes the day invalid.
5. Supply control: the peak boiler (grid gas) is fired so the district supply reaches DB-16
   (100 °C), up to its nameplate; the remainder is unmet heat.

Everything reported comes from GEMINI outputs: heat by source, brine heat drawn per user (the
verifier balances it against ρ·Q·c_p·ΔT, PH-22), electricity by consumer (ESP by the RC-01 rule
``Q·Δp / (0.70·η_motor·η_VFD)`` on GEMINI's own intake/discharge pressures, DB-13/DB-40; the
GEMINI curve power is kept in ``outputs`` for comparison), gas fuel (LHV) and direct CO₂ of the
burners (grid-electricity CO₂ is WP-08's, DB-37). Margins (≥ 0 = satisfied) carry unit suffixes.
A day whose models stay invalid returns ``valid=False`` and no ``DayResult``.
"""

from __future__ import annotations

import copy
import datetime as dt
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, JsonValue, model_validator

from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.common import (
    HOURS_PER_DAY,
    ConceptId,
    StrictModel,
    ToolResult,
)
from gemini_agent.schemas.config import load_config
from gemini_agent.schemas.results import AnnualResult, DayResult
from gemini_agent.tools.climate import build_climate_profile
from gemini_agent.tools.config_generator import (
    DISTRICT_FLUID,
    GENERATOR_ASSUMPTION_IDS,
    GeneratedConfig,
    GeneratorBasis,
    PlantDispatch,
    PlantLoads,
    generate_plant,
    load_generator_basis,
    project_hash,
)
from gemini_agent.tools.demand import read_demand_csv
from gemini_agent.tools.design_basis import PA_PER_BAR, SECONDS_PER_HOUR
from gemini_agent.tools.doublet import DoubletDesign
from gemini_agent.tools.resource import SubsurfaceBasis, load_subsurface_basis
from gemini_agent.verify.design_verifier import (
    EspPoint,
    HeatPumpPoint,
    HexPoint,
    PumpPoint,
    VerificationEvidence,
)
from gemini_simulator.project_loader import build_project
from gemini_simulator.solver import Solver

TOOL_ID = "TL-11"
SECONDS_PER_DAY = HOURS_PER_DAY * SECONDS_PER_HOUR
TEMPERATURE_TOL_K = 0.01
"""Tolerance on heat-pump outlet temperatures (Nelder–Mead residual in HeatpumpBasic)."""
CONTROL_TOL_K = 1e-6
"""Tolerance of the plate-HEX control (closed-form GEMINI models, no solver noise)."""
HEAT_TOL_MW = 1e-6
"""Heat-balance noise below which no unmet/surplus heat is reported."""
HP_OFF_FLOW_M3S = 1e-12
"""Sink flow below which a heat pump is treated as off (its Th_out has no weight)."""
RC04_TYPICAL_COP = (3.5, 4.5)
"""RC-04 typical HTHP COP band (WP-03 decision log); outside it a plausibility warning is given."""
HP_CAPACITY_TOL_MW = 1e-6
"""Heat-pump heat above the hthp_heating_mw nameplate by less than this is not controlled."""
SHARE_BISECTION_STEPS = 12
"""Plate-HEX / heat-pump share resolution: 1/4096 of the district flow."""
BOILER_CONTROL_PASSES = 2
MARGIN_TOL = 1e-6
"""Negative margins smaller than this are solver noise (e.g. 300.00000000007 m³/h)."""


class DayConditions(StrictModel):
    """Day-average demand and ambient of one calendar day (DB-18, DB-19, DB-34a)."""

    date: dt.date
    day_index: int = Field(ge=0, le=365)
    heating_demand_mw: float = Field(ge=0)
    cooling_demand_mw: float = Field(ge=0)
    ambient_dry_bulb_degc: float
    ambient_wet_bulb_degc: float

    @model_validator(mode="after")
    def _check_index(self) -> DayConditions:
        expected = (self.date - dt.date(self.date.year, 1, 1)).days
        if expected != self.day_index:
            raise ValueError(f"day_index {self.day_index} does not match {self.date}")
        return self

    def loads(self) -> PlantLoads:
        """Return the generator's view of this day (``PlantLoads``)."""
        return PlantLoads(
            heating_demand_mw=self.heating_demand_mw,
            cooling_demand_mw=self.cooling_demand_mw,
            ambient_dry_bulb_degc=self.ambient_dry_bulb_degc,
            ambient_wet_bulb_degc=self.ambient_wet_bulb_degc,
        )


def day_conditions(
    date: dt.date | None = None, *, config_dir: Path | str | None = None
) -> DayConditions:
    """Demand (CSV) and climate (TL-02b profile) of one day; default: DB-18 heating peak day."""
    basis = load_config("design_basis", config_dir)
    date = date or basis.heating.peak_date
    profile, _ = build_climate_profile(load_config("climate_nl", config_dir), basis.year)
    index = profile.dates.index(date)
    heating = read_demand_csv(basis.heating, basis.year)
    cooling = read_demand_csv(basis.cooling, basis.year)
    return DayConditions(
        date=date,
        day_index=index,
        heating_demand_mw=float(heating.iloc[index]),
        cooling_demand_mw=float(cooling.iloc[index]),
        ambient_dry_bulb_degc=profile.dry_bulb_degc[index],
        ambient_wet_bulb_degc=profile.wet_bulb_degc[index],
    )


class SimulateDayRequest(StrictModel):
    """TL-11 input."""

    candidate: Candidate
    day: DayConditions | None = Field(default=None, description="None: DB-18 peak day.")
    dispatch: PlantDispatch = Field(default_factory=PlantDispatch)
    design: DoubletDesign | None = None
    parent_concept: ConceptId | None = None
    control_supply_temperature: bool = Field(
        default=True, description="Fire the peak boiler to hold DB-16 (else use dispatch gas)."
    )
    control_heat_pump_capacity: bool = Field(
        default=True, description="Hold heat-pump heat to the hthp_heating_mw nameplate (bypass)."
    )


class HeatPumpFlag(StrictModel):
    """A closed-form heat-pump point a reader must see (RC-04 screening of WP-03's model)."""

    name: str
    running: bool
    within_limits: bool | None = Field(description="None: idle (not a violation).")
    missing_inputs: list[str] = Field(default_factory=list, description="HTHP datasheet values.")
    cop: float
    lift_k: float
    reasons: list[str] = Field(default_factory=list, description="The model's warnings.")


class SimulatedDay(StrictModel):
    """TL-11 output for one candidate and one day."""

    candidate_id: str
    valid: bool
    failures: list[str] = Field(default_factory=list)
    day: DayResult | None = Field(default=None, description="None when ``valid`` is False.")
    config: GeneratedConfig = Field(description="The project actually simulated (final pass).")
    outputs: dict[str, dict[str, JsonValue]] = Field(
        description="Scalar GEMINI outputs per component (final pass)."
    )
    hex_flow_frac: float | None = None
    hp_flow_frac: float | None = None
    controls: list[str] = Field(
        default_factory=list, description="Why a control moved a set-point (bypass shares)."
    )
    boiler_gas_kg_s: float = 0.0
    supplied_heat_mw: float = 0.0
    heat_surplus_mw: float = 0.0
    hex_points: list[HexPoint] = Field(default_factory=list)
    pumps: list[PumpPoint] = Field(default_factory=list)
    esps: list[EspPoint] = Field(default_factory=list)
    heat_pumps: list[HeatPumpPoint] = Field(
        default_factory=list, description="WP-03 ``heat_pump_point`` evidence of running HPs."
    )
    heat_pump_flags: list[HeatPumpFlag] = Field(default_factory=list)
    brine_contact_materials: dict[str, str] = Field(default_factory=dict)
    model_warnings: list[str] = Field(
        default_factory=list,
        description="WP-14: validity warnings of the cooling models and running heat pumps "
        "(DesignVerifier PH-28).",
    )
    warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- helpers
def _scalar_outputs(solver: Solver) -> dict[str, dict[str, JsonValue]]:
    result: dict[str, dict[str, JsonValue]] = {}
    for name in solver.components:
        snapshot = solver.get_output(name)
        values: dict[str, JsonValue] = {}
        for key, value in snapshot.items():
            if isinstance(value, bool | str) or value is None:
                values[key] = value
            elif isinstance(value, int | float):
                number = float(value)
                values[key] = number if math.isfinite(number) else None
            elif hasattr(value, "item"):
                number = float(value.item())
                values[key] = number if math.isfinite(number) else None
        result[name] = values
    return result


def _run(project: dict[str, Any]) -> Solver:
    solver = build_project(project)
    solver.run()
    return solver


def _out(solver: Solver, name: str, key: str) -> float:
    return float(solver.get_output(name, key))


def _inp(solver: Solver, name: str, key: str, default: float = 0.0) -> float:
    return float(solver.inputs[name].get(key, default))


def heat_pump_failures(solver: Solver, config: GeneratedConfig) -> list[str]:
    """Return why each ``HeatpumpBasic`` output is not physical (empty if all valid).

    Physical = finite, Th_in ≤ Th_out ≤ max(target, Th_in), floor ≤ Ts_out ≤ Ts_in, no negative
    power, and
    unchanged at zero sink flow. RC-04 envelope violations are flags, not failures.
    """
    problems = []
    for name, role in config.roles.items():
        if role != "heat_pump":
            continue
        params = config.project["components"][name]["parameters"]  # type: ignore[index]
        target = float(params["Th_out_target"])  # type: ignore[index, arg-type]
        floor = float(params["Ts_in_minimum"])  # type: ignore[index, arg-type]
        out = solver.get_output(name)
        values = [float(out[k]) for k in ("Th_out", "Ts_out", "COP", "power_th", "power_el")]
        th_out, ts_out, _cop, power_th, power_el = values
        th_in, ts_in = _inp(solver, name, "Th_in"), _inp(solver, name, "Ts_in")
        reasons = []
        idle = out.get("running") is False or _inp(solver, name, "hot_flow_rate") <= HP_OFF_FLOW_M3S
        if idle:
            # An idle heat pump (no sink flow, water already at target, or brine already at or
            # below its floor) must pass both streams unchanged at zero power; its floor does not
            # apply (GEMINI's Nelder-Mead cools the brine anyway, the closed form does not).
            unchanged = (
                abs(ts_out - ts_in) <= TEMPERATURE_TOL_K
                and abs(th_out - th_in) <= TEMPERATURE_TOL_K
                and abs(power_th) <= 1e-6
                and abs(power_el) <= 1e-6
            )
            if not unchanged:
                problems.append(
                    f"{name}: idle but changes the streams (Ts_out {ts_out:.2f} vs Ts_in "
                    f"{ts_in:.2f} °C, Th_out {th_out:.2f} vs Th_in {th_in:.2f} °C)"
                )
            continue
        if not all(math.isfinite(v) for v in values):
            reasons.append("non-finite output")
        else:
            # A COP < 1 is physical but outside RC-04: flagged (heat_pump_flags), not failed.
            # An idle heat pump passes water already above its target unchanged.
            upper = max(target, th_in) + TEMPERATURE_TOL_K
            if not th_in - TEMPERATURE_TOL_K <= th_out <= upper:
                reasons.append(f"Th_out {th_out:.2f} °C outside [{th_in:.2f}, {target:g}]")
            if not floor - TEMPERATURE_TOL_K <= ts_out <= ts_in + TEMPERATURE_TOL_K:
                reasons.append(f"Ts_out {ts_out:.2f} °C outside [{floor:.2f}, {ts_in:.2f}]")
            if power_el < 0 or power_th < 0:
                reasons.append("negative power")
        if reasons:
            problems.append(f"{name}: " + "; ".join(reasons))
    return problems


def hex_problems(solver: Solver, config: GeneratedConfig, gb: GeneratorBasis) -> list[str]:
    """Plate-HEX control targets: brine outlet ≥ T_wh − DB-12 and no supply overshoot (DB-16).

    The overshoot is read on the district water after the plate HEXs and the CHP, before the heat
    pumps (fixed 100 °C target) and the peak boiler (fired only up to 100 °C).
    """
    problems = []
    for name, role in config.roles.items():
        if role != "direct_hex":
            continue
        t_wh = _out(solver, f"producer_well_{config.doublet_of[name]}", "temperature_out")
        cooled = t_wh - _out(solver, name, "primary_temperature_out")
        if cooled > gb.brine_cooling_max_k + CONTROL_TOL_K:
            problems.append(f"{name} cools the brine {cooled:.2f} K > {gb.brine_cooling_max_k:g} K")
    if config.preheat_component is not None:
        t = _out(solver, *config.preheat_component)
        if t > gb.heating_supply_degc + CONTROL_TOL_K:
            problems.append(f"district water {t:.2f} °C > {gb.heating_supply_degc:g} °C")
    return problems


def brine_cooling_failures(
    solver: Solver, config: GeneratedConfig, gb: GeneratorBasis
) -> list[str]:
    """DB-12 hard limit on every active doublet: T_wh − T_inj ≤ 40 K (never only a warning)."""
    problems = []
    for plan in config.doublets:
        if not plan.active:
            continue
        i = plan.index
        cooled = _out(solver, f"producer_well_{i}", "temperature_out") - _inp(
            solver, f"injector_well_{i}", "wellhead_temperature"
        )
        if cooled > gb.brine_cooling_max_k + TEMPERATURE_TOL_K:
            problems.append(
                f"DB-12: doublet {i} brine cooled {cooled:.2f} K > {gb.brine_cooling_max_k:g} K"
            )
    return problems


def _heat_pump_heat_mw(solver: Solver, config: GeneratedConfig) -> float:
    return sum(
        _out(solver, name, "power_th") / 1e6
        for name, role in config.roles.items()
        if role == "heat_pump"
    )


def _set_split(project: dict[str, Any], name: str, share: float) -> None:
    project["components"][name]["parameters"]["split_fractions"] = [share, 1.0 - share]


def _set_boundary(project: dict[str, Any], component: str, key: str, value: float) -> None:
    for entry in project["boundary_inputs"]:
        if entry["component"] == component and entry["key"] == key:
            entry["value"] = float(value)
            return
    raise KeyError(f"no boundary input {component}.{key}")


# --------------------------------------------------------------------------- simulation
def simulate_plant_day(
    candidate: Candidate,
    day: DayConditions,
    dispatch: PlantDispatch | None = None,
    *,
    design: DoubletDesign | None = None,
    parent_concept: str | None = None,
    control_supply_temperature: bool = True,
    control_plate_hex: bool = True,
    control_heat_pump_capacity: bool = True,
    gb: GeneratorBasis | None = None,
    sub: SubsurfaceBasis | None = None,
    config_dir: Path | str | None = None,
) -> SimulatedDay:
    """Simulate one candidate on one day (module doc). Raises ``ConfigError`` if not buildable."""
    gb = gb or load_generator_basis(config_dir)
    sub = sub or load_subsurface_basis(config_dir)
    dispatch = dispatch or PlantDispatch()
    config = generate_plant(
        candidate,
        day.loads(),
        dispatch,
        design=design,
        parent_concept=parent_concept,
        gb=gb,
        sub=sub,
    )
    return simulate_config_day(
        candidate,
        day,
        config,
        dispatch,
        control_supply_temperature=control_supply_temperature,
        control_plate_hex=control_plate_hex,
        control_heat_pump_capacity=control_heat_pump_capacity,
        gb=gb,
        sub=sub,
    )


def simulate_config_day(
    candidate: Candidate,
    day: DayConditions,
    config: GeneratedConfig,
    dispatch: PlantDispatch | None = None,
    *,
    control_supply_temperature: bool = True,
    control_plate_hex: bool = True,
    control_heat_pump_capacity: bool = True,
    gb: GeneratorBasis | None = None,
    sub: SubsurfaceBasis | None = None,
    config_dir: Path | str | None = None,
) -> SimulatedDay:
    """Run a TL-09 config for one day with the module's controls (no regeneration).

    ``config`` must be :func:`generate_plant`'s output for this candidate and day (it may carry
    edited component parameters, e.g. a cited HTHP datasheet).
    """
    gb = gb or load_generator_basis(config_dir)
    sub = sub or load_subsurface_basis(config_dir)
    dispatch = dispatch or PlantDispatch()
    project = copy.deepcopy(config.project)
    controls: list[str] = []
    warnings = list(config.flags) + list(config.notes)
    failures: list[str] = []
    solver = _run(project)

    hex_share: float | None = None
    if "district_split_hex_bypass" in config.roles:
        hex_share = dispatch.hex_flow_frac
        problems = hex_problems(solver, config, gb)
        if problems and control_plate_hex:
            controls.append(
                f"plate-HEX share reduced from {hex_share:g} ({problems[0]}); district water "
                "bypasses the plate HEXs (DB-12/DB-16 control)"
            )
            _set_split(project, "district_split_hex_bypass", 0.0)
            if hex_problems(_run(project), config, gb):
                # Even without plate-HEX heat the water is too hot (the CHP burns all the
                # associated gas, DB-36): no geothermal direct heat; WP-07 lowers the flows.
                hex_share = 0.0
            else:
                low, high = 0.0, hex_share
                for _ in range(SHARE_BISECTION_STEPS):
                    mid = 0.5 * (low + high)
                    _set_split(project, "district_split_hex_bypass", mid)
                    if hex_problems(_run(project), config, gb):
                        high = mid
                    else:
                        low = mid
                hex_share = low
            _set_split(project, "district_split_hex_bypass", hex_share)
            solver = _run(project)

    hp_share: float | None = None
    if "district_split_hp_bypass" in config.roles:
        hp_share = dispatch.hp_flow_frac
        nameplate = candidate.equipment_sizes.get("hthp_heating_mw")
        heat = _heat_pump_heat_mw(solver, config)
        if (
            control_heat_pump_capacity
            and nameplate is not None
            and heat > nameplate + HP_CAPACITY_TOL_MW
        ):
            # Genuine capacity control: the heat pumps cannot deliver more than their nameplate;
            # the rest of the district water bypasses them to the peak boiler. Heat is not
            # monotone in the share once source-limited (Q_h = Q_src·COP/(COP−1) falls with the
            # lift), so the bisection lands on the rising branch: correct but conservative (a
            # smaller share at a higher lift than compressor turndown would give; WP-07/WP-11).
            low, high = 0.0, hp_share
            for _ in range(SHARE_BISECTION_STEPS):
                mid = 0.5 * (low + high)
                _set_split(project, "district_split_hp_bypass", mid)
                if _heat_pump_heat_mw(_run(project), config) > nameplate + HP_CAPACITY_TOL_MW:
                    high = mid
                else:
                    low = mid
            controls.append(
                f"heat-pump share reduced from {hp_share:g} to {low:.4f}: heat {heat:.2f} MW > "
                f"nameplate hthp_heating_mw {nameplate:g} MW (capacity control)"
            )
            hp_share = low
            _set_split(project, "district_split_hp_bypass", hp_share)
            solver = _run(project)

    boiler_gas = dispatch.boiler_gas_kg_s
    boiler = next((n for n, r in config.roles.items() if r == "boiler"), None)
    if (
        boiler is not None
        and control_supply_temperature
        and config.burner_fuels.get(boiler) == "grid_gas"
        and not failures
    ):
        params = project["components"][boiler]["parameters"]
        capacity_w = config.equipment_mw.get("peak_boiler_mw", 0.0) * 1e6
        for _ in range(BOILER_CONTROL_PASSES):
            t_in = _inp(solver, boiler, "temperature_in")
            water = _inp(solver, boiler, "water_flow_rate")
            need_w = (
                DISTRICT_FLUID["fluid_density"]
                * DISTRICT_FLUID["specific_heat"]
                * water
                * (gb.heating_supply_degc - t_in)
            )
            heat_w = min(max(need_w, 0.0), capacity_w)
            gas = heat_w / (params["efficiency_factor"] * params["caloric_value"])
            if math.isclose(gas, boiler_gas, rel_tol=1e-12, abs_tol=1e-15):
                break
            boiler_gas = gas
            _set_boundary(project, boiler, "gas_flow_rate", boiler_gas)
            solver = _run(project)
    failures += heat_pump_failures(solver, config)
    if not failures:
        failures += brine_cooling_failures(solver, config, gb)

    final = config.model_copy(update={"project": project, "config_hash": project_hash(project)})
    outputs = _scalar_outputs(solver)
    result = SimulatedDay(
        candidate_id=candidate.id,
        valid=not failures,
        failures=failures,
        config=final,
        outputs=outputs,
        hex_flow_frac=hex_share,
        hp_flow_frac=hp_share,
        controls=controls,
        boiler_gas_kg_s=boiler_gas,
        warnings=warnings,
    )
    if failures:
        return result
    return _assemble(result, solver, day, gb, sub, candidate)


def _assemble(
    result: SimulatedDay,
    solver: Solver,
    day: DayConditions,
    gb: GeneratorBasis,
    sub: SubsurfaceBasis,
    candidate: Candidate,
) -> SimulatedDay:
    """Fill the ``DayResult`` and the verifier evidence from the final GEMINI pass."""
    config = result.config
    roles = config.roles
    names = {role: [n for n, r in roles.items() if r == role] for role in set(roles.values())}
    wire_to_water = (
        sub.pump_hydraulic_efficiency_frac * sub.motor_efficiency_frac * sub.vfd_efficiency_frac
    )
    motor_vfd = sub.motor_efficiency_frac * sub.vfd_efficiency_frac
    margins: dict[str, float] = {}
    warnings = list(result.warnings)

    # Doublets (all N; off doublets have zero flow at reservoir temperature).
    available, flows, inj_flows, t_prod, t_inj = [], [], [], [], []
    electricity: dict[str, float] = {}
    pumps: list[PumpPoint] = []
    esps: list[EspPoint] = []
    t_res = config.reservoir_temperature_degc or 0.0
    for plan in config.doublets:
        i = plan.index
        available.append(plan.active)
        if not plan.active:
            flows.append(0.0)
            inj_flows.append(0.0)
            t_prod.append(t_res)
            t_inj.append(t_res)
            continue
        producer, injector = f"producer_well_{i}", f"injector_well_{i}"
        q = _out(solver, producer, "flow_rate")
        q_inj = _inp(solver, injector, "flow_rate")
        tp = _out(solver, producer, "temperature_out")
        ti = _inp(solver, injector, "wellhead_temperature")
        flows.append(q)
        inj_flows.append(q_inj)
        t_prod.append(tp)
        t_inj.append(ti)
        intake = _out(solver, producer, "esp_intake_pressure")
        discharge = _out(solver, producer, "esp_discharge_pressure")
        dp_pa = max(discharge - intake, 0.0) * PA_PER_BAR
        q_m3s = q / SECONDS_PER_HOUR
        esp_shaft = q_m3s * dp_pa / sub.pump_hydraulic_efficiency_frac
        electricity[f"esp_{i}"] = q_m3s * dp_pa / wire_to_water / 1e6
        pumps.append(
            PumpPoint(name=f"esp_{i}", flow_m3h=q, pressure_rise_pa=dp_pa, shaft_power_w=esp_shaft)
        )
        whp = _out(solver, injector, "wellhead_pressure")
        suction_pa = _inp(solver, injector, "pressure_in")
        inj_el = _out(solver, injector, "power_el")
        electricity[f"injection_pump_{i}"] = inj_el / 1e6
        pumps.append(
            PumpPoint(
                name=f"injection_pump_{i}",
                flow_m3h=q_inj,
                pressure_rise_pa=max(whp * PA_PER_BAR - suction_pa, 0.0),
                shaft_power_w=inj_el * motor_vfd,
            )
        )
        esps.append(
            EspPoint(
                name=f"esp_{i}",
                intake_pressure_barg=intake,
                release_pressure_barg=config.release_pressure_barg,
                npsh_available_m=plan.npsh_available_m,
            )
        )
        if plan.esp_intake_setpoint_barg is not None:
            margins[f"esp_{i}_intake_margin_bar"] = intake - plan.esp_intake_setpoint_barg
        margins[f"doublet_{i}_brine_cooling_margin_k"] = gb.brine_cooling_max_k - (tp - ti)
        margins[f"doublet_{i}_flow_margin_m3h"] = min(q - sub.flow_min_m3h, sub.flow_max_m3h - q)
        margins[f"doublet_{i}_injection_balance_margin_m3h"] = -abs(q_inj - q)

    # Heat sources (district network only) and brine heat drawn per user.
    heat: dict[str, float] = {}
    drawn: dict[str, float] = {}
    for name in names.get("direct_hex", []):
        duty = _out(solver, name, "heat_duty") / 1e6
        heat[name] = duty
        drawn[name] = duty
    hp_flags: list[HeatPumpFlag] = []
    hp_points: list[HeatPumpPoint] = []
    for name in names.get("heat_pump", []):
        out = solver.get_output(name)
        heat[name] = float(out["power_th"]) / 1e6
        electricity[name] = float(out["power_el"]) / 1e6
        # WP-03: source_heat_w is the heat taken from the brine (PH-22); NM: Q_h − W.
        drawn[name] = float(out.get("source_heat_w", out["power_th"] - out["power_el"])) / 1e6
        flag, point = _heat_pump_screening(name, out)
        if flag is not None:
            hp_flags.append(flag)
        if out.get("running"):
            # RC-04 equipment notes of the model (multi-pass ΔT > 11 K, COP capped …) and a
            # plausibility note outside RC-04's typical HTHP band (cap-limited low-lift points).
            warnings += [f"{name}: {w}" for w in out.get("validity_warnings") or []]
            cop = float(out["COP"])
            low, high = RC04_TYPICAL_COP
            if not low <= cop <= high:
                warnings.append(
                    f"{name}: COP {cop:.2f} outside RC-04's typical HTHP band {low:g}–{high:g} "
                    f"(lift {float(out.get('lift_k') or 0.0):.1f} K, "
                    f"{float(out['power_th']) / 1e6:.3f} MW; cap-limited, plausibility only)"
                )
        if point is not None:
            hp_points.append(point)
        _heat_pump_margins(name, out, params=config.project["components"][name], margins=margins)
    gas_fuel: dict[str, float] = {}
    co2: dict[str, float] = {}
    generated = 0.0
    for role in ("chp", "boiler"):
        for name in names.get(role, []):
            heat[name] = _out(solver, name, "power_th") / 1e6
            params = config.project["components"][name]["parameters"]  # type: ignore[index]
            gas_kg_s = _inp(solver, name, "gas_flow_rate")
            gas_fuel[name] = gas_kg_s * float(params["caloric_value"]) / 1e6  # type: ignore[index]
            co2[name] = _out(solver, name, "emission") * SECONDS_PER_DAY
            if role == "chp":
                generated += _out(solver, name, "power_el") / 1e6
    produced_nm3 = sum(
        _out(solver, name, "gas_flow_rate") * SECONDS_PER_DAY for name in names.get("separator", [])
    )

    # Cooling.
    cool: dict[str, float] = {}
    cooling_supply = None
    if "free_cooling" in roles:
        cool["free_cooling"] = _out(solver, "free_cooling", "free_cooling_w") / 1e6
        electricity["free_cooling"] = _out(solver, "free_cooling", "power_el") / 1e6
    if "absorption_chiller" in roles:
        absorber = "absorption_chiller"
        cool[absorber] = _out(solver, absorber, "cooling_power_w") / 1e6
        drawn[absorber] = _out(solver, absorber, "drive_heat_w") / 1e6
        electricity[absorber] = _out(solver, absorber, "power_el") / 1e6
        drive_m3s = _out(solver, absorber, "drive_flow_rate_m3s")
        if drive_m3s > 0:
            margins["absorption_drive_cooling_margin_k"] = gb.brine_cooling_max_k - _out(
                solver, absorber, "drive_delta_t_k"
            )
            minimum = float(solver.get_output(absorber).get("minimum_drive_flow_rate_m3s", 0.0))
            margins["absorption_drive_flow_margin_m3h"] = (drive_m3s - minimum) * SECONDS_PER_HOUR
            if drive_m3s < minimum:
                warnings.append(
                    f"absorption drive {drive_m3s * SECONDS_PER_HOUR:.0f} m³/h below its minimum "
                    f"{minimum * SECONDS_PER_HOUR:.0f} m³/h: machine off (COOL-ABS-DRV)"
                )
    if "electric_chiller" in roles:
        chiller = "electric_chiller"
        cool[chiller] = _out(solver, chiller, "cooling_power_w") / 1e6
        electricity[chiller] = _out(solver, chiller, "power_el") / 1e6
        cooling_supply = _out(solver, chiller, "chilled_water_temperature_out_degc")
    if "heat_rejection" in roles:
        electricity["heat_rejection"] = _out(solver, "heat_rejection", "power_el") / 1e6
    supplied_cool = sum(cool.values())
    unmet_cooling_mw = max(day.cooling_demand_mw - supplied_cool, 0.0)
    if unmet_cooling_mw <= HEAT_TOL_MW:
        unmet_cooling_mw = 0.0
    if day.cooling_demand_mw > 0 and cooling_supply is not None:
        margins["cooling_supply_margin_k"] = gb.cooling_supply_degc - cooling_supply
    if "electric_chiller" in roles:
        chiller_warnings = solver.get_output("electric_chiller").get("validity_warnings") or []
        warnings += [f"electric_chiller: {w}" for w in chiller_warnings][:2]
    # WP-14: every cooling model's validity warnings reach the verifier (PH-28).
    model_warnings = [
        f"{name}: {w}"
        for name in ("free_cooling", "absorption_chiller", "electric_chiller", "heat_rejection")
        if name in roles
        for w in solver.get_output(name).get("validity_warnings") or []
    ]
    model_warnings += [
        f"{name}: {w}"
        for name in names.get("heat_pump", [])
        if solver.get_output(name).get("running", True)
        for w in solver.get_output(name).get("validity_warnings") or []
    ]

    # Heating balance and supply temperature.
    supplied = sum(heat.values())
    supply_t = _out(solver, *config.supply_component) if config.supply_component else None
    gap = day.heating_demand_mw - supplied
    unmet_heating_mw = gap if gap > HEAT_TOL_MW else 0.0
    surplus = -gap if -gap > HEAT_TOL_MW else 0.0
    if day.heating_demand_mw > 0 and supply_t is not None:
        margins["heating_supply_margin_k"] = supply_t - gb.heating_supply_degc
        margins["heat_surplus_margin_mw"] = -surplus
        if surplus:
            warnings.append(
                f"heat surplus {surplus:.3f} MW (supply {supply_t:.2f} °C > "
                f"{gb.heating_supply_degc:g} °C): lower the doublet flows (WP-07 dispatch)"
            )
    hp_size = candidate.equipment_sizes.get("hthp_heating_mw")
    if hp_size is not None and names.get("heat_pump"):
        margins["hthp_capacity_margin_mw"] = hp_size - sum(heat[n] for n in names["heat_pump"])
    if boiler := next((n for n, r in roles.items() if r == "boiler"), None):
        margins["peak_boiler_capacity_margin_mw"] = config.equipment_mw.get(
            "peak_boiler_mw", 0.0
        ) - heat.get(boiler, 0.0)

    day_result = DayResult(
        day_index=day.day_index,
        date=day.date,
        ambient_dry_bulb_degc=day.ambient_dry_bulb_degc,
        ambient_wet_bulb_degc=day.ambient_wet_bulb_degc,
        heating_demand_mw=day.heating_demand_mw,
        cooling_demand_mw=day.cooling_demand_mw,
        doublet_available=available,
        doublet_flow_m3h=flows,
        injection_flow_m3h=inj_flows,
        production_temperature_degc=t_prod,
        injection_temperature_degc=t_inj,
        heating_supply_degc=supply_t,
        heating_return_degc=config.heating_return_degc,
        cooling_supply_degc=cooling_supply,
        cooling_return_degc=config.cooling_return_degc,
        heat_by_source_mw=heat,
        cool_by_source_mw=cool,
        electricity_by_consumer_mw=electricity,
        brine_heat_drawn_mw=drawn,
        electricity_generated_mw=generated,
        gas_fuel_by_consumer_mw=gas_fuel,
        associated_gas_produced_nm3=produced_nm3,
        co2_by_source_kg=co2,
        unmet_heating_mwh=unmet_heating_mw * HOURS_PER_DAY,
        unmet_cooling_mwh=unmet_cooling_mw * HOURS_PER_DAY,
        margins=margins,
        flags=[_flag_text(f) for f in hp_flags],
    )
    warnings += [
        f"margin {key} = {value:.3g} < 0"
        for key, value in sorted(margins.items())
        if value < -MARGIN_TOL
    ]
    hex_points = _hex_points(solver, config)
    materials = {p.name: p.plate_material for p in hex_points if p.plate_material}
    if config.hex_design is not None:
        for name in names.get("heat_pump", []):
            materials[f"{name}_evaporator"] = config.hex_design.plate_material
    if "absorption_chiller" in roles and config.doublets:
        materials["absorption_chiller_generator"] = "titanium tubes"
    return result.model_copy(
        update={
            "day": day_result,
            "supplied_heat_mw": supplied,
            "heat_surplus_mw": surplus,
            "hex_points": hex_points,
            "pumps": pumps,
            "esps": esps,
            "heat_pumps": hp_points,
            "heat_pump_flags": hp_flags,
            "brine_contact_materials": materials,
            "model_warnings": model_warnings,
            "warnings": warnings,
        }
    )


def _heat_pump_screening(
    name: str, out: dict[str, Any]
) -> tuple[HeatPumpFlag | None, HeatPumpPoint | None]:
    """Flag and evidence of one heat pump from WP-03's RC-04 screening outputs.

    Flagged (never hidden): a running point with ``within_limits`` False, and any heat pump whose
    HTHP datasheet values are MISSING. ``heat_pump_point`` (None when idle or unconverged) is the
    DesignVerifier's ``HeatPumpPoint`` (PH-13/PH-14).
    """
    running = bool(out.get("running", True))
    within = out.get("within_limits")
    missing = list(out.get("missing_inputs") or [])
    flag = None
    if within is False or missing:
        flag = HeatPumpFlag(
            name=name,
            running=running,
            within_limits=within,
            missing_inputs=missing,
            cop=float(out["COP"]),
            lift_k=float(out.get("lift_k", 0.0)),
            reasons=[str(w) for w in out.get("validity_warnings") or []],
        )
    raw = out.get("heat_pump_point")
    point = HeatPumpPoint(name=name, **raw) if isinstance(raw, dict) else None
    return flag, point


def _heat_pump_margins(
    name: str, out: dict[str, Any], *, params: Any, margins: dict[str, float]
) -> None:
    """Margins of a running heat pump, so the verifier's PH-21 sees every limit (not only flags).

    ``{name}_rc04_limits_margin_count`` is −1 for a point reported outside its limits (also when
    it emits no ``heat_pump_point``, e.g. COP < 1); lift and datasheet sink temperature get their
    own margins. The COP-cap margin is reported only when the model does not enforce the cap
    itself (``apply_cop_cap`` False) — otherwise it is 0 by construction whenever the cap binds.
    """
    if not out.get("running", True):
        return
    margins[f"{name}_rc04_limits_margin_count"] = -1.0 if out.get("within_limits") is False else 0.0
    if out.get("max_lift_k") is not None and out.get("lift_k") is not None:
        margins[f"{name}_lift_margin_k"] = float(out["max_lift_k"]) - float(out["lift_k"])
    sink_max = out.get("max_sink_temperature_degc")
    if sink_max is not None and out.get("sink_temperature_out_degc") is not None:
        margins[f"{name}_sink_temperature_margin_k"] = float(sink_max) - float(
            out["sink_temperature_out_degc"]
        )
    enforced = (params.get("parameters") or {}).get("apply_cop_cap", True)
    if not enforced and out.get("cop_cap"):
        margins[f"{name}_cop_cap_margin_frac"] = 1.0 - float(out["COP"]) / float(out["cop_cap"])


def _flag_text(flag: HeatPumpFlag) -> str:
    parts = []
    if flag.within_limits is False:
        detail = "; ".join(flag.reasons[:3]) or "see the model warnings"
        parts.append(
            f"OUTSIDE RC-04 limits (COP {flag.cop:.2f}, lift {flag.lift_k:.1f} K): {detail}"
        )
    if flag.missing_inputs:
        parts.append(
            f"HTHP datasheet MISSING {flag.missing_inputs}: RC-04 defaults VER-HP-ETA/VER-HP-LIFT"
        )
    state = "running" if flag.running else "idle"
    return f"{flag.name} ({state}): " + " | ".join(parts)


def _hex_points(solver: Solver, config: GeneratedConfig) -> list[HexPoint]:
    """Day operating points of the plate HEXs (RC-03), skipped when one side has no flow."""
    design = config.hex_design
    points = []
    for name, role in config.roles.items():
        if role != "direct_hex" or design is None:
            continue
        params = config.project["components"][name]["parameters"]  # type: ignore[index]
        prim = _inp(solver, name, "primary_flow_rate")
        sec = _inp(solver, name, "secondary_flow_rate")
        density = float(params["fluid_density"])  # type: ignore[index]
        c_prim = prim * density * float(params["specific_heat"])  # type: ignore[index]
        c_sec = (
            sec
            * float(params["secondary_fluid_density"])  # type: ignore[index]
            * float(params["secondary_specific_heat"])  # type: ignore[index]
        )
        if min(c_prim, c_sec) <= 0 or _out(solver, name, "heat_duty") <= 0:
            continue
        ntu = float(params["heat_transfer_coefficient"]) / min(c_prim, c_sec)  # type: ignore[index]
        ntu_pass = ntu / design.passes
        points.append(
            HexPoint(
                name=name,
                hot_in_degc=_inp(solver, name, "primary_temperature_in"),
                hot_out_degc=_out(solver, name, "primary_temperature_out"),
                cold_in_degc=_inp(solver, name, "secondary_temperature_in"),
                cold_out_degc=_out(solver, name, "secondary_temperature_out"),
                construction=design.construction,
                passes=design.passes,
                design_pressure_barg=_inp(solver, name, "pressure_in"),
                min_brine_pressure_barg=_out(solver, name, "pressure_out"),
                ntu=ntu_pass,
                pressure_drop_pa=design.pressure_drop_per_pass_pa / design.ntu_per_pass * ntu_pass,
                flow_m3h=max(prim, sec) * SECONDS_PER_HOUR,
                area_m2=design.area_m2,
                plate_material=design.plate_material,
                brine_side=True,
            )
        )
    return points


# --------------------------------------------------------------------------- evidence
def verification_evidence(
    candidate: Candidate,
    days: Sequence[SimulatedDay],
    *,
    interpretation: Literal["A", "B"] = "A",
    project: dict[str, Any] | None = None,
) -> VerificationEvidence:
    """``VerificationEvidence`` (WP-09) from simulated days: full-mode results and evidence.

    ``project`` defaults to the first day's GEMINI project. Plate-HEX evidence is the design
    point (``GeneratedConfig.hex_points``): the RC-03 checks (approach ≥ 1.1 K, NTU per pass
    0.6–4, Δp per NTU, ratings, unit size) are sizing criteria, and a fixed-UA plate at part load
    legitimately runs at a smaller approach and a larger NTU (the day points stay in
    ``SimulatedDay.hex_points``). Raises ``ValueError`` on an invalid day or a day of another
    candidate.
    """
    if not days:
        raise ValueError("no simulated days")
    for sim in days:
        if sim.candidate_id != candidate.id:
            raise ValueError(f"day of '{sim.candidate_id}' given for '{candidate.id}'")
        if not sim.valid or sim.day is None:
            raise ValueError(f"invalid simulated day for '{candidate.id}': {sim.failures}")
    ordered = sorted(days, key=lambda s: s.day.day_index)  # type: ignore[union-attr]
    annual = AnnualResult(
        candidate_id=candidate.id,
        year=ordered[0].day.date.year,  # type: ignore[union-attr]
        interpretation=interpretation,
        days=[s.day for s in ordered],  # type: ignore[misc]
    )
    materials: dict[str, str] = {}
    for sim in ordered:
        materials.update(sim.brine_contact_materials)
    return VerificationEvidence(
        candidate=candidate,
        project=project if project is not None else ordered[0].config.project,
        annual=annual,
        hex_points=ordered[0].config.hex_points(),
        heat_pumps=[p for s in ordered for p in s.heat_pumps],
        pumps=[p for s in ordered for p in s.pumps],
        esps=[p for s in ordered for p in s.esps],
        co2_release_pressure_barg=ordered[0].config.release_pressure_barg,
        brine_contact_materials=materials,
        stub_evidence=False,
    )


# --------------------------------------------------------------------------- tool
def _summary(sim: SimulatedDay, day: DayConditions) -> str:
    head = f"{TOOL_ID} {sim.candidate_id} {day.date}"
    if not sim.valid:
        return f"{head}: INVALID — {sim.failures[0]}"[:300]
    d = sim.day
    assert d is not None
    flows = "/".join(f"{q:.0f}" for q in d.doublet_flow_m3h) or "-"
    text = (
        f"{head}: heat {sim.supplied_heat_mw:.2f}/{day.heating_demand_mw:.2f} MW "
        f"(unmet {d.unmet_heating_mwh:.1f} MWh), cool {d.cool_supplied_mw:.2f}/"
        f"{day.cooling_demand_mw:.2f} MW, elec {d.electricity_consumed_mw:.2f} MW, "
        f"gas {sum(d.gas_fuel_by_consumer_mw.values()):.2f} MW, flows {flows} m³/h"
    )
    if d.heating_supply_degc is not None:
        text += f", supply {d.heating_supply_degc:.1f} °C"
    return text[:300]


def simulate_day(
    request: SimulateDayRequest,
    *,
    config_dir: Path | str | None = None,
) -> ToolResult[SimulatedDay]:
    """TL-11: GEMINI build + run of one candidate for one day (spec §6)."""
    day = request.day or day_conditions(config_dir=config_dir)
    sim = simulate_plant_day(
        request.candidate,
        day,
        request.dispatch,
        design=request.design,
        parent_concept=request.parent_concept,
        control_supply_temperature=request.control_supply_temperature,
        control_heat_pump_capacity=request.control_heat_pump_capacity,
        config_dir=config_dir,
    )
    return ToolResult[SimulatedDay](
        data=sim,
        summary=_summary(sim, day),
        warnings=sim.failures + sim.controls + (sim.day.flags if sim.day else []) + sim.warnings,
        assumption_ids=sorted(set(GENERATOR_ASSUMPTION_IDS) | {"DB-37"}),
        sources=["spec §6 TL-11", "GEMINI project_loader/solver", "RC-01", "RC-03", "RC-08"],
    )


__all__ = [
    "TOOL_ID",
    "DayConditions",
    "HeatPumpFlag",
    "SimulateDayRequest",
    "SimulatedDay",
    "brine_cooling_failures",
    "day_conditions",
    "heat_pump_failures",
    "hex_problems",
    "simulate_config_day",
    "simulate_day",
    "simulate_plant_day",
    "verification_evidence",
]
