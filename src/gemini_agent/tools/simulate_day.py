"""TL-11 ``simulate_day``: build a candidate's GEMINI project for one day and run it (spec §6).

One day = one GEMINI steady state at the day-average loads (the demand CSVs are day-average MW)
and the day's ambient dry/wet bulb. Procedure:

1. TL-09 :func:`generate_plant` builds the project for the day's loads and set-points (doublet
   flows → TL-05 ESP frequency and wellhead temperature; district flow from the heating demand).
2. GEMINI runs it (``build_project`` + ``Solver.run``).
3. Heat-pump guard (CFG-HP): if a ``HeatpumpBasic`` output is not physical (Th_out outside
   [Th_in, target], COP < 1, source outlet below its DB-12 floor — the source-limited re-solve
   diverges in GEMINI today), the share of district water sent through the heat pumps is
   bisected down until every heat pump is valid; the rest bypasses them to the peak boiler.
4. Supply control: the peak boiler (grid gas) is fired so the district supply reaches DB-16
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
HEAT_TOL_MW = 1e-6
"""Heat-balance noise below which no unmet/surplus heat is reported."""
HP_OFF_FLOW_M3S = 1e-12
"""Sink flow below which a heat pump is treated as off (its Th_out has no weight)."""
HP_BISECTION_STEPS = 12
"""Heat-pump share resolution 1/4096 of the district flow."""
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
    control_heat_pumps: bool = Field(
        default=True, description="Bypass district water around invalid heat pumps (CFG-HP)."
    )


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
    hp_flow_frac: float | None = None
    boiler_gas_kg_s: float = 0.0
    supplied_heat_mw: float = 0.0
    heat_surplus_mw: float = 0.0
    hex_points: list[HexPoint] = Field(default_factory=list)
    pumps: list[PumpPoint] = Field(default_factory=list)
    esps: list[EspPoint] = Field(default_factory=list)
    brine_contact_materials: dict[str, str] = Field(default_factory=dict)
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
    """Return why each ``HeatpumpBasic`` output is not physical (empty if all valid)."""
    problems = []
    for name, role in config.roles.items():
        if role != "heat_pump":
            continue
        params = config.project["components"][name]["parameters"]  # type: ignore[index]
        target = float(params["Th_out_target"])  # type: ignore[index, arg-type]
        floor = float(params["Ts_in_minimum"])  # type: ignore[index, arg-type]
        out = solver.get_output(name)
        values = [float(out[k]) for k in ("Th_out", "Ts_out", "COP", "power_th", "power_el")]
        th_out, ts_out, cop, power_th, power_el = values
        th_in, ts_in = _inp(solver, name, "Th_in"), _inp(solver, name, "Ts_in")
        reasons = []
        if _inp(solver, name, "hot_flow_rate") <= HP_OFF_FLOW_M3S:
            # No sink flow: valid only if the brine passes unchanged (GEMINI cools it anyway).
            if abs(ts_out - ts_in) > TEMPERATURE_TOL_K or abs(power_th) > 1e-6:
                problems.append(f"{name}: no sink flow but Ts_out {ts_out:.2f} ≠ Ts_in {ts_in:.2f}")
            continue
        if not all(math.isfinite(v) for v in values):
            reasons.append("non-finite output")
        else:
            if not th_in - TEMPERATURE_TOL_K <= th_out <= target + TEMPERATURE_TOL_K:
                reasons.append(f"Th_out {th_out:.2f} °C outside [{th_in:.2f}, {target:g}]")
            if not floor - TEMPERATURE_TOL_K <= ts_out <= ts_in + TEMPERATURE_TOL_K:
                reasons.append(f"Ts_out {ts_out:.2f} °C outside [{floor:.2f}, {ts_in:.2f}]")
            if power_th > 0 and cop < 1.0 - 1e-9:
                reasons.append(f"COP {cop:.3f} < 1")
            if power_el < 0 or power_th < 0:
                reasons.append("negative power")
        if reasons:
            problems.append(f"{name}: " + "; ".join(reasons))
    return problems


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
    control_heat_pumps: bool = True,
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
    project = copy.deepcopy(config.project)
    warnings = list(config.flags) + list(config.notes)
    failures: list[str] = []
    solver = _run(project)

    hp_share: float | None = None
    if "district_split_hp_bypass" in config.roles:
        hp_share = dispatch.hp_flow_frac
        problems = heat_pump_failures(solver, config)
        if problems and control_heat_pumps:
            warnings.append(
                "heat-pump model invalid at the full district share "
                f"({problems[0]}); district water bypassed to the peak boiler (CFG-HP, WP-03)"
            )
            low, high = 0.5**HP_BISECTION_STEPS * hp_share, hp_share
            _set_split(project, "district_split_hp_bypass", low)
            if heat_pump_failures(_run(project), config):
                # No positive share is valid (e.g. the water is already above 100 °C): the day's
                # plant runs without heat pumps.
                warnings.append("heat pumps switched off today: no valid operating point")
                dispatch = dispatch.model_copy(update={"heat_pumps_on": False})
                config = generate_plant(
                    candidate,
                    day.loads(),
                    dispatch,
                    design=design,
                    parent_concept=parent_concept,
                    gb=gb,
                    sub=sub,
                )
                project = copy.deepcopy(config.project)
                hp_share = 0.0
            else:
                for _ in range(HP_BISECTION_STEPS):
                    mid = 0.5 * (low + high)
                    _set_split(project, "district_split_hp_bypass", mid)
                    if heat_pump_failures(_run(project), config):
                        high = mid
                    else:
                        low = mid
                hp_share = low
                _set_split(project, "district_split_hp_bypass", hp_share)
            solver = _run(project)
        elif problems:
            failures += problems

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
        if failures == [] and heat_pump_failures(solver, config):
            failures += heat_pump_failures(solver, config)

    final = config.model_copy(update={"project": project, "config_hash": project_hash(project)})
    outputs = _scalar_outputs(solver)
    result = SimulatedDay(
        candidate_id=candidate.id,
        valid=not failures,
        failures=failures,
        config=final,
        outputs=outputs,
        hp_flow_frac=hp_share,
        boiler_gas_kg_s=boiler_gas,
        warnings=warnings,
    )
    if failures:
        return result
    return _assemble(result, solver, day, gb, sub)


def _assemble(
    result: SimulatedDay,
    solver: Solver,
    day: DayConditions,
    gb: GeneratorBasis,
    sub: SubsurfaceBasis,
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
    for name in names.get("heat_pump", []):
        power_th = _out(solver, name, "power_th") / 1e6
        power_el = _out(solver, name, "power_el") / 1e6
        heat[name] = power_th
        drawn[name] = power_th - power_el
        electricity[name] = power_el
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
        heating_return_degc=gb.heating_return_degc,
        cooling_supply_degc=cooling_supply,
        cooling_return_degc=gb.cooling_return_degc,
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
            "brine_contact_materials": materials,
            "warnings": warnings,
        }
    )


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
        control_heat_pumps=request.control_heat_pumps,
        config_dir=config_dir,
    )
    return ToolResult[SimulatedDay](
        data=sim,
        summary=_summary(sim, day),
        warnings=sim.failures + sim.warnings,
        assumption_ids=sorted(set(GENERATOR_ASSUMPTION_IDS) | {"DB-37"}),
        sources=["spec §6 TL-11", "GEMINI project_loader/solver", "RC-01", "RC-03", "RC-08"],
    )


__all__ = [
    "TOOL_ID",
    "DayConditions",
    "SimulateDayRequest",
    "SimulatedDay",
    "day_conditions",
    "heat_pump_failures",
    "simulate_day",
    "simulate_plant_day",
    "verification_evidence",
]
