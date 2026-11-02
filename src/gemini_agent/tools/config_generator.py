"""TL-09 ``generate_gemini_config``: GEMINI project JSON for candidates C0–C8 (spec §3.4, §3.5).

Two layers:

* :class:`PlantSpec` is an explicit intermediate representation: every component's GEMINI
  parameters, the boundary values and the layout switches. :func:`build_plant_project` turns it
  into a GEMINI project (``components``, ``boundary_inputs``, ``connections``) with unique names
  and connections in topological order (the solver executes them in declared order, so every
  component's inputs are written before it is read). It invents no numbers; the GEMINI doublet
  example is rebuilt from a ``PlantSpec`` holding the example's own parameters (test).
* :func:`plan_plant` fills a ``PlantSpec`` for a :class:`Candidate`: doublets from TL-05
  (``resolve_doublet`` + ``operating_point``: brine ``pvt``, SUB-WELL roughness, injector friction
  as a loss, the flow-scaled ESP at the frequency that delivers the target flow), the plate HEX
  sized so GEMINI's ``HeatExchanger`` hits its design outlet (RC-03 eq 3 via eq 1 inverted),
  associated-gas burners at the separator LHV and 68.56 kg CO₂/GJ (RC-08), and the cooling
  chain of the concept (RC-05/06/07 models).

Concept layouts (spec §3.4, §3.5): per doublet ``producer → [separator] → filter A → [brine split
→ absorption drive] → [plate HEX] → [brine mix] → [heat-pump evaporator] → filter B → injector``;
district water ``return → plate HEXs → [CHP on associated gas] → [heat pumps, 100 °C target] →
[peak boiler on grid gas]``; cooling ``[free cooling] → [absorption] → electric chiller`` with one
cooling tower. Choices are in the register (CFG-LAYOUT, CFG-SURFACE, CFG-HEX, CFG-HP, CFG-CHP,
CFG-BOILER, CFG-GRID-GAS, CFG-ASSOC-GAS, CFG-SIZING). Units: GEMINI's own at the JSON boundary
(surface flows m³/s, pressures bar(g) — WP-05 runs GEMINI on gauge pressure — well flows m³/h,
temperatures °C); unit-suffixed fields everywhere else.

The peak boiler's grid-gas flow is a control input: the generator writes 0 and
:mod:`gemini_agent.tools.simulate_day` sets it to hold the supply temperature (DB-16).
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, JsonValue, model_validator

from gemini_agent.memory.design_cache import config_hash
from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.common import ConceptId, GasHandling, StrictModel, ToolResult
from gemini_agent.schemas.config import (
    AssumptionsRegister,
    ClimateConfig,
    DesignBasis,
    HardLimits,
    load_config,
)
from gemini_agent.tools.design_basis import SECONDS_PER_HOUR
from gemini_agent.tools.doublet import (
    DoubletDesign,
    OperatingPoint,
    ResolvedDoublet,
    injector_well_parameters,
    operating_point,
    producer_well_parameters,
    resolve_doublet,
)
from gemini_agent.tools.resource import SubsurfaceBasis, load_subsurface_basis
from gemini_agent.verify.design_verifier import HEX_RATING, PA_PER_PSI, HexPoint
from gemini_model.absorption_chiller.absorption_chiller import AbsorptionChiller
from gemini_model.chp.chp import CHP
from gemini_model.fluid.pvt_config import heat_exchanger_fluid_parameters
from gemini_model.free_cooling.free_cooling import FreeCooling
from gemini_model.heat_rejection.heat_rejection import HeatRejection
from gemini_model.separator.separator import associated_gas_properties
from gemini_simulator.registry import COMPONENT_REGISTRY

TOOL_ID = "TL-09"
GENERATOR_VERSION = 1
"""Bumped when the generated topology changes (part of every design-cache namespace)."""

JOULE_PER_GJ = 1.0e9
CHP_FRACTION_ID = "CHP-EL-FRAC"
"""Register id of the CHP electrical share (owned by WP-03; WP-08 reads the same entry)."""
HP_EFFICIENCY_ID = "HP-ETA-L"
"""Register id of the heat-pump Lorenz efficiency η_L (owned by WP-03)."""
HP_APPROACH_ID = "HP-APPROACH"
"""Register id of the heat-pump condenser/evaporator approaches (owned by WP-03; sets the cap)."""
CHP_FRACTION_TOL = 5e-5
"""CHP-EL-FRAC is stored to 4 decimals (0.3333) while chp.py uses exactly 1/3."""
CR_BALANCED_TOL = 1e-9
"""|1 − C_r| below which a counter-flow HEX is treated as balanced (C_r = 1 limit)."""
DISTRICT_FLUID = heat_exchanger_fluid_parameters()
"""District water = GEMINI water PVT (1000 kg/m³, 4217.4 J/(kg K)); boilers, CHP, HEX secondary."""

GENERATOR_ASSUMPTION_IDS: tuple[str, ...] = (
    "CFG-LAYOUT",
    "CFG-SURFACE",
    "CFG-HEX",
    "CFG-HP",
    "CFG-CHP",
    "CFG-BOILER",
    "CFG-GRID-GAS",
    "CFG-ASSOC-GAS",
    "CFG-SIZING",
    "DB-31",
    "DB-32",
    "DB-33",
    "DB-34b",
    "DB-36",
    "DB-40",
    "VER-HP-LIFT",
    "CHP-EL-FRAC",
    "ECO-CO2-GAS",
    "HP-ETA-L",
    "HP-APPROACH",
    "VER-HP-ETA",
)

ComponentRole = Literal[
    "producer",
    "injector",
    "separator",
    "filter",
    "brine_splitter",
    "brine_mixer",
    "direct_hex",
    "heat_pump",
    "chp",
    "boiler",
    "district_splitter",
    "district_mixer",
    "gas_adder",
    "gas_splitter",
    "free_cooling",
    "absorption_chiller",
    "electric_chiller",
    "heat_rejection",
    "heat_rejection_adder",
]
Fuel = Literal["associated_gas", "grid_gas"]
Source = tuple[str, str] | float
"""Where an input comes from: ``(component, output_key)`` or a boundary value."""


class ConfigError(ValueError):
    """The candidate cannot be turned into a GEMINI project (reason in the message)."""


# --------------------------------------------------------------------------- project builder
class ProjectBuilder:
    """Accumulates components, boundary inputs and connections; emits a GEMINI project.

    Names must be unique and types registered (:data:`COMPONENT_REGISTRY`). :meth:`project`
    orders the connections topologically (Kahn, ties broken by insertion order), so every
    connection into a component precedes every connection out of it and all connections into one
    component are consecutive (the solver evaluates it once per group, ``Solver.connect``).
    """

    def __init__(self, description: str = "") -> None:
        """Start an empty project."""
        self.description = description
        self.components: dict[str, dict[str, Any]] = {}
        self.roles: dict[str, ComponentRole] = {}
        self.doublet_of: dict[str, int] = {}
        self.boundary: list[dict[str, Any]] = []
        self._edges: list[tuple[str, str, list[tuple[str, str]]]] = []

    def add(
        self,
        name: str,
        component_type: str,
        parameters: dict[str, Any] | None,
        role: ComponentRole,
        doublet: int | None = None,
    ) -> str:
        """Add one component; raises :class:`ConfigError` on a duplicate name or unknown type."""
        if name in self.components:
            raise ConfigError(f"duplicate component name '{name}'")
        if component_type not in COMPONENT_REGISTRY:
            raise ConfigError(f"component type '{component_type}' is not in COMPONENT_REGISTRY")
        spec: dict[str, Any] = {"type": component_type}
        if parameters:
            spec["parameters"] = parameters
        self.components[name] = spec
        self.roles[name] = role
        if doublet is not None:
            self.doublet_of[name] = doublet
        return name

    def connect(self, predecessor: str, successor: str, ports: list[tuple[str, str]]) -> None:
        """Wire ``predecessor`` outputs to ``successor`` inputs (``[(output, input), ...]``)."""
        for name in (predecessor, successor):
            if name not in self.components:
                raise ConfigError(f"connection references unknown component '{name}'")
        for edge in self._edges:
            if edge[0] == predecessor and edge[1] == successor:
                edge[2].extend(ports)
                return
        self._edges.append((predecessor, successor, list(ports)))

    def boundary_input(self, component: str, key: str, value: float) -> None:
        """Set a fixed input value (``boundary_inputs`` entry)."""
        if component not in self.components:
            raise ConfigError(f"boundary input references unknown component '{component}'")
        if not math.isfinite(value):
            raise ConfigError(f"boundary input {component}.{key} must be finite, got {value}")
        self.boundary.append({"component": component, "key": key, "value": float(value)})

    def feed(self, source: Source, successor: str, input_key: str) -> None:
        """Connect ``source`` to ``successor.input_key`` (a boundary value or an output)."""
        if isinstance(source, tuple):
            self.connect(source[0], successor, [(source[1], input_key)])
        else:
            self.boundary_input(successor, input_key, float(source))

    def ordered_edges(self) -> list[tuple[str, str, list[tuple[str, str]]]]:
        """Edges in topological order of their successors (stable); raises on a cycle."""
        order = list(self.components)
        index = {name: i for i, name in enumerate(order)}
        incoming: dict[str, set[str]] = {name: set() for name in order}
        outgoing: dict[str, set[str]] = {name: set() for name in order}
        for pred, succ, _ in self._edges:
            incoming[succ].add(pred)
            outgoing[pred].add(succ)
        ready = sorted((n for n in order if not incoming[n]), key=index.__getitem__)
        rank: dict[str, int] = {}
        while ready:
            node = ready.pop(0)
            rank[node] = len(rank)
            for nxt in sorted(outgoing[node], key=index.__getitem__):
                incoming[nxt].discard(node)
                if not incoming[nxt] and nxt not in rank and nxt not in ready:
                    ready.append(nxt)
                    ready.sort(key=index.__getitem__)
        if len(rank) != len(order):
            cyclic = sorted(set(order) - set(rank))
            raise ConfigError(f"connections form a cycle through {cyclic}")
        positions = {id(edge): i for i, edge in enumerate(self._edges)}
        return sorted(self._edges, key=lambda e: (rank[e[1]], positions[id(e)]))

    def project(self) -> dict[str, Any]:
        """Return the GEMINI project dict (``components``, ``boundary_inputs``, ``connections``)."""
        connections = []
        for pred, succ, ports in self.ordered_edges():
            if len(ports) == 1:
                connections.append(
                    {
                        "predecessor": pred,
                        "successor": succ,
                        "predecessor_output": ports[0][0],
                        "successor_input": ports[0][1],
                    }
                )
            else:
                connections.append(
                    {
                        "predecessor": pred,
                        "successor": succ,
                        "ports": [[out, inp] for out, inp in ports],
                    }
                )
        project: dict[str, Any] = {}
        if self.description:
            project["_description"] = self.description
        project["components"] = self.components
        project["boundary_inputs"] = self.boundary
        project["connections"] = connections
        return project


# --------------------------------------------------------------------------- explicit spec (IR)
class DoubletTrainSpec(StrictModel):
    """GEMINI parameters of one active doublet's brine train (spec §3.5)."""

    index: int = Field(ge=1, description="1-based doublet number (component name suffix).")
    producer: dict[str, JsonValue] = Field(description="``producer_well`` parameters.")
    injector: dict[str, JsonValue] = Field(description="``injector_well`` parameters.")
    wellhead_pressure_barg: float = Field(description="Producer WHP boundary input, bar(g).")
    wellhead_temperature_degc: float = Field(description="Producer T_wh boundary input (TL-05).")
    separator: dict[str, JsonValue] | None = Field(
        default=None, description="Degasser; None = pressurised loop, gas stays dissolved (C7)."
    )
    filter_a: dict[str, JsonValue]
    filter_b: dict[str, JsonValue]
    hex: dict[str, JsonValue] | None = Field(default=None, description="Direct plate HEX-1.")
    heat_pump: dict[str, JsonValue] | None = Field(
        default=None, description="HTHP with its evaporator on this doublet's brine."
    )


class DistrictSpec(StrictModel):
    """District-heating water side (series or GEMINI-example parallel CHP layout)."""

    flow_m3s: float = Field(ge=0)
    return_temperature_degc: float
    supply_temperature_degc: float = Field(description="DB-16 set point (heat-pump target).")
    branch_fracs: list[float] | None = Field(
        default=None, description="Share of the main flow per doublet branch; None: equal."
    )
    chp: dict[str, JsonValue] | None = None
    chp_fuel: Fuel = "associated_gas"
    chp_grid_gas_kg_s: float = Field(default=0.0, ge=0)
    chp_layout: Literal["series", "parallel"] = Field(
        default="series",
        description="series: in line after the plate HEXs; parallel: on a side branch (GEMINI).",
    )
    chp_water_frac: float = Field(default=0.0, ge=0, le=1, description="Parallel layout only.")
    hex_flow_frac: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Share of the district flow through the plate HEXs (rest bypasses); None: no "
        "bypass components.",
    )
    hp_flow_frac: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Share of the district flow through the heat pumps (rest bypasses); None: no "
        "bypass components.",
    )
    boiler: dict[str, JsonValue] | None = None
    boiler_fuel: Fuel = "grid_gas"
    boiler_gas_kg_s: float = Field(default=0.0, ge=0, description="Grid-gas flow (control).")
    associated_gas_to_boiler_frac: float = Field(
        default=0.0, ge=0, le=1, description="Share of associated gas to the boiler (GEMINI)."
    )

    @model_validator(mode="after")
    def _check(self) -> DistrictSpec:
        if self.chp_layout == "parallel" and self.chp is None:
            raise ValueError("parallel CHP layout needs a CHP")
        if self.branch_fracs is not None and abs(sum(self.branch_fracs) - 1.0) > 1e-9:
            raise ValueError(f"branch_fracs must sum to 1, got {self.branch_fracs}")
        return self


class CoolingSpec(StrictModel):
    """Cooling chain: [free cooling] → [absorption] → [electric chiller]; one cooling tower."""

    load_w: float = Field(ge=0)
    ambient_dry_bulb_degc: float
    ambient_wet_bulb_degc: float
    free_cooling: dict[str, JsonValue] | None = None
    absorption: dict[str, JsonValue] | None = None
    absorption_doublet: int = Field(default=1, ge=1, description="Brine train driving it.")
    absorption_drive_frac: float = Field(
        default=0.0, ge=0, le=1, description="Share of that doublet's brine to the drive."
    )
    absorption_cooling_water_in_degc: float | None = None
    electric_chiller: dict[str, JsonValue] | None = None
    heat_rejection: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def _check(self) -> CoolingSpec:
        if self.absorption is not None and self.absorption_cooling_water_in_degc is None:
            raise ValueError("absorption chiller needs absorption_cooling_water_in_degc")
        return self


class PlantSpec(StrictModel):
    """Everything :func:`build_plant_project` needs; no defaults are filled in there."""

    name: str
    doublets: list[DoubletTrainSpec] = Field(default_factory=list)
    district: DistrictSpec | None = None
    cooling: CoolingSpec | None = None

    @model_validator(mode="after")
    def _check(self) -> PlantSpec:
        indices = [d.index for d in self.doublets]
        if len(set(indices)) != len(indices):
            raise ValueError(f"duplicate doublet indices {indices}")
        district = self.district
        if district is not None and district.branch_fracs is not None:
            expected = len(self.doublets) or 1
            if len(district.branch_fracs) != expected:
                raise ValueError(f"branch_fracs needs {expected} entries")
        cooling = self.cooling
        drive = cooling.absorption_doublet if cooling and cooling.absorption else None
        if drive is not None and drive not in indices:
            raise ValueError("absorption_doublet is not an active doublet")
        return self


class BuiltProject(StrictModel):
    """GEMINI project plus the component index used by simulate_day and DesignGuard."""

    project: dict[str, JsonValue]
    roles: dict[str, ComponentRole]
    doublet_of: dict[str, int]
    burner_fuels: dict[str, Fuel] = Field(default_factory=dict)
    supply_component: tuple[str, str] | None = Field(
        default=None, description="(component, temperature output) of the district supply."
    )
    preheat_component: tuple[str, str] | None = Field(
        default=None,
        description="District water after plate HEXs + CHP (before heat pumps and peak boiler).",
    )


def _brine_name(kind: str, index: int) -> str:
    return f"{kind}_{index}"


def _stage(
    b: ProjectBuilder,
    flow: Source,
    temperature: Source,
    branches: list[tuple[str, str, str, str, str]],
    fracs: list[float],
    tag: str,
) -> tuple[Source, Source]:
    """Feed a water stream through parallel branches; return the mixed outlet stream.

    ``branches``: (component, flow_in, temperature_in, flow_out, temperature_out) per branch.
    """
    if len(branches) == 1:
        name, f_in, t_in, f_out, t_out = branches[0]
        b.feed(flow, name, f_in)
        b.feed(temperature, name, t_in)
        return (name, f_out), (name, t_out)
    splitter = b.add(
        f"district_split_{tag}",
        "splitter",
        {"split_fractions": fracs},
        "district_splitter",
    )
    b.feed(flow, splitter, "input")
    mixer = b.add(f"district_mix_{tag}", "mixer", {"num_inputs": len(branches)}, "district_mixer")
    for k, (name, f_in, t_in, f_out, t_out) in enumerate(branches):
        b.connect(splitter, name, [(f"output_{k}", f_in)])
        b.feed(temperature, name, t_in)
        b.connect(name, mixer, [(f_out, f"input_{k}_flow"), (t_out, f"input_{k}_temperature")])
    return (mixer, "flow"), (mixer, "temperature")


def _bypassed_stage(
    b: ProjectBuilder,
    flow: Source,
    temperature: Source,
    branches: list[tuple[str, str, str, str, str]],
    fracs: list[float],
    tag: str,
    share: float | None,
) -> tuple[Source, Source]:
    """:func:`_stage` behind a district bypass: ``share`` goes through, the rest around it.

    ``share=None`` builds no bypass. simulate_day sets the share (DB-12, DB-16 control).
    """
    if share is None:
        return _stage(b, flow, temperature, branches, fracs, tag)
    split = b.add(
        f"district_split_{tag}_bypass",
        "splitter",
        {"split_fractions": [share, 1.0 - share]},
        "district_splitter",
    )
    b.feed(flow, split, "input")
    out_flow, out_temp = _stage(b, (split, "output_0"), temperature, branches, fracs, tag)
    mixer = b.add(f"district_mix_{tag}_bypass", "mixer", {"num_inputs": 2}, "district_mixer")
    b.feed(out_flow, mixer, "input_0_flow")
    b.feed(out_temp, mixer, "input_0_temperature")
    b.connect(split, mixer, [("output_1", "input_1_flow")])
    b.feed(temperature, mixer, "input_1_temperature")
    return (mixer, "flow"), (mixer, "temperature")


def build_plant_project(spec: PlantSpec) -> BuiltProject:
    """Build the GEMINI project of a :class:`PlantSpec` (topologically ordered, unique names)."""
    b = ProjectBuilder(
        f"GeoAgent {TOOL_ID} generator v{GENERATOR_VERSION}: {spec.name}. Units: surface flows "
        "m3/s, pressures bar(g), well flows m3/h, temperatures degC."
    )
    burner_fuels: dict[str, Fuel] = {}
    cooling = spec.cooling
    gas_sources: list[tuple[str, str]] = []
    brine_after_hex: dict[int, tuple[Source, Source, Source]] = {}

    # ---- brine trains (up to the heat-pump evaporator)
    for d in spec.doublets:
        i = d.index
        producer = b.add(
            _brine_name("producer_well", i), "producer_well", d.producer, "producer", i
        )
        b.boundary_input(producer, "wellhead_pressure", d.wellhead_pressure_barg)
        b.boundary_input(producer, "wellhead_temperature", d.wellhead_temperature_degc)
        upstream: tuple[str, str] = (producer, "flow_rate_m3s")
        pressure: tuple[str, str] = (producer, "pressure_out")
        temperature: tuple[str, str] = (producer, "temperature_out")
        if d.separator is not None:
            separator = b.add(_brine_name("separator", i), "separator", d.separator, "separator", i)
            b.connect(
                producer,
                separator,
                [("flow_rate_m3s", "flow_rate"), ("pressure_out", "pressure")],
            )
            b.connect(producer, separator, [("temperature_out", "temperature")])
            gas_sources.append((separator, "gas_mass_flow_rate"))
            upstream = (separator, "flow_rate")
            pressure = (separator, "pressure_out")
            temperature = (separator, "temperature_out")
        filter_a = b.add(_brine_name("filter_a", i), "filter", d.filter_a, "filter", i)
        b.connect(pressure[0], filter_a, [(pressure[1], "pressure")])
        b.connect(temperature[0], filter_a, [(temperature[1], "temperature")])
        b.connect(upstream[0], filter_a, [(upstream[1], "flow_rate")])
        flow_src: Source = (filter_a, "flow_rate")
        temp_src: Source = (filter_a, "temperature_out")
        pres_src: Source = (filter_a, "pressure_out")

        drives_absorption = (
            cooling is not None
            and cooling.absorption is not None
            and cooling.absorption_doublet == i
        )
        if drives_absorption:
            assert cooling is not None
            frac = cooling.absorption_drive_frac
            split = b.add(
                _brine_name("brine_split", i),
                "splitter",
                {"split_fractions": [1.0 - frac, frac]},
                "brine_splitter",
                i,
            )
            b.connect(filter_a, split, [("flow_rate", "input")])
            flow_src = (split, "output_0")

        if d.hex is not None:
            hex_name = b.add(_brine_name("hex", i), "heat_exchanger", d.hex, "direct_hex", i)
            b.feed(pres_src, hex_name, "pressure_in")
            b.feed(temp_src, hex_name, "primary_temperature_in")
            b.feed(flow_src, hex_name, "primary_flow_rate")
            flow_src = (hex_name, "primary_flow_rate")
            temp_src = (hex_name, "primary_temperature_out")
            pres_src = (hex_name, "pressure_out")

        if drives_absorption:
            assert cooling is not None and cooling.absorption is not None
            absorber = b.add(
                "absorption_chiller", "absorption_chiller", cooling.absorption, "absorption_chiller"
            )
            b.connect(filter_a, absorber, [("temperature_out", "drive_temperature_in_degc")])
            b.connect(
                _brine_name("brine_split", i), absorber, [("output_1", "drive_flow_rate_m3s")]
            )
            mix = b.add(_brine_name("brine_mix", i), "mixer", {"num_inputs": 2}, "brine_mixer", i)
            b.feed(flow_src, mix, "input_0_flow")
            b.feed(temp_src, mix, "input_0_temperature")
            b.connect(absorber, mix, [("drive_flow_rate_m3s", "input_1_flow")])
            b.connect(absorber, mix, [("drive_temperature_out_degc", "input_1_temperature")])
            flow_src = (mix, "flow")
            temp_src = (mix, "temperature")
        brine_after_hex[i] = (flow_src, temp_src, pres_src)

    # ---- associated gas: separators → (adder) → (split) → burners
    gas_total: Source | None = None
    if len(gas_sources) == 1:
        gas_total = gas_sources[0]
    elif gas_sources:
        adder = b.add("gas_adder", "adder", {"num_inputs": len(gas_sources)}, "gas_adder")
        for k, (name, key) in enumerate(gas_sources):
            b.connect(name, adder, [(key, f"input_{k}")])
        gas_total = (adder, "output")
    district = spec.district
    chp_gas: Source | None = None
    boiler_gas: Source | None = None
    if district is not None and gas_total is not None:
        chp_assoc = district.chp is not None and district.chp_fuel == "associated_gas"
        boiler_assoc = district.boiler is not None and district.boiler_fuel == "associated_gas"
        if chp_assoc and boiler_assoc:
            frac = district.associated_gas_to_boiler_frac
            split = b.add(
                "gas_split", "splitter", {"split_fractions": [frac, 1.0 - frac]}, "gas_splitter"
            )
            b.feed(gas_total, split, "input")
            boiler_gas, chp_gas = (split, "output_0"), (split, "output_1")
        elif chp_assoc:
            chp_gas = gas_total
        elif boiler_assoc:
            boiler_gas = gas_total

    # ---- district heating chain
    supply: tuple[str, str] | None = None
    preheat: tuple[str, str] | None = None
    if district is not None:
        n = len(spec.doublets)
        fracs = district.branch_fracs or ([1.0 / n] * n if n else [1.0])
        flow: Source = district.flow_m3s
        temp: Source = district.return_temperature_degc
        chp_branch: tuple[Source, Source] | None = None
        if district.chp is not None and district.chp_layout == "parallel":
            split = b.add(
                "district_split_chp",
                "splitter",
                {"split_fractions": [1.0 - district.chp_water_frac, district.chp_water_frac]},
                "district_splitter",
            )
            b.boundary_input(split, "input", district.flow_m3s)
            flow = (split, "output_0")
            chp_branch = ((split, "output_1"), district.return_temperature_degc)

        hex_branches = [
            (
                _brine_name("hex", d.index),
                "secondary_flow_rate",
                "secondary_temperature_in",
                "secondary_flow_rate",
                "secondary_temperature_out",
            )
            for d in spec.doublets
            if d.hex is not None
        ]
        hex_fracs = [fracs[k] for k, d in enumerate(spec.doublets) if d.hex is not None]
        if hex_branches:
            total = sum(hex_fracs)
            flow, temp = _bypassed_stage(
                b,
                flow,
                temp,
                hex_branches,
                [f / total for f in hex_fracs],
                "hex",
                district.hex_flow_frac,
            )

        if district.chp is not None:
            chp = b.add("chp", "chp", district.chp, "chp")
            burner_fuels[chp] = district.chp_fuel
            if chp_gas is not None:
                b.feed(chp_gas, chp, "gas_flow_rate")
            else:
                b.boundary_input(chp, "gas_flow_rate", district.chp_grid_gas_kg_s)
            if chp_branch is None:
                b.feed(flow, chp, "water_flow_rate")
                b.feed(temp, chp, "temperature_in")
                flow, temp = (chp, "water_flow_rate"), (chp, "temperature_out")
            else:
                b.feed(chp_branch[0], chp, "water_flow_rate")
                b.feed(chp_branch[1], chp, "temperature_in")

        if isinstance(temp, tuple):
            preheat = temp
        hp_doublets = [d for d in spec.doublets if d.heat_pump is not None]
        if hp_doublets:
            hp_branches = []
            hp_fracs = []
            for k, d in enumerate(spec.doublets):
                if d.heat_pump is None:
                    continue
                name = b.add(
                    _brine_name("hthp", d.index), "heatpump", d.heat_pump, "heat_pump", d.index
                )
                b_flow, b_temp, _ = brine_after_hex[d.index]
                b.feed(b_flow, name, "source_flow_rate")
                b.feed(b_temp, name, "Ts_in")
                hp_branches.append((name, "hot_flow_rate", "Th_in", "hot_flow_rate", "Th_out"))
                hp_fracs.append(fracs[k])
                brine_after_hex[d.index] = (
                    (name, "source_flow_rate"),
                    (name, "Ts_out"),
                    brine_after_hex[d.index][2],
                )
            total = sum(hp_fracs)
            flow, temp = _bypassed_stage(
                b,
                flow,
                temp,
                hp_branches,
                [f / total for f in hp_fracs],
                "hp",
                district.hp_flow_frac,
            )

        if district.boiler is not None:
            boiler = b.add("peak_boiler", "boiler", district.boiler, "boiler")
            burner_fuels[boiler] = district.boiler_fuel
            if boiler_gas is not None:
                b.feed(boiler_gas, boiler, "gas_flow_rate")
            else:
                b.boundary_input(boiler, "gas_flow_rate", district.boiler_gas_kg_s)
            b.feed(flow, boiler, "water_flow_rate")
            b.feed(temp, boiler, "temperature_in")
            flow, temp = (boiler, "water_flow_rate"), (boiler, "temperature_out")

        if chp_branch is not None:
            mixer = b.add("district_mix_out", "mixer", {"num_inputs": 2}, "district_mixer")
            b.feed(flow, mixer, "input_0_flow")
            b.feed(temp, mixer, "input_0_temperature")
            b.connect("chp", mixer, [("water_flow_rate", "input_1_flow")])
            b.connect("chp", mixer, [("temperature_out", "input_1_temperature")])
            temp = (mixer, "temperature")
        if isinstance(temp, tuple):
            supply = temp

    # ---- brine trains (filter B → injector)
    for d in spec.doublets:
        i = d.index
        b_flow, b_temp, b_pres = brine_after_hex[i]
        filter_b = b.add(_brine_name("filter_b", i), "filter", d.filter_b, "filter", i)
        b.feed(b_pres, filter_b, "pressure")
        b.feed(b_temp, filter_b, "temperature")
        b.feed(b_flow, filter_b, "flow_rate")
        injector = b.add(
            _brine_name("injector_well", i), "injector_well", d.injector, "injector", i
        )
        b.connect(
            filter_b,
            injector,
            [
                ("flow_rate_m3h", "flow_rate"),
                ("temperature_out", "wellhead_temperature"),
                ("pressure_out_pa", "pressure_in"),
            ],
        )

    # ---- cooling chain
    if cooling is not None:
        _cooling_chain(b, cooling)

    return BuiltProject(
        project=b.project(),
        roles=b.roles,
        doublet_of=b.doublet_of,
        burner_fuels=burner_fuels,
        supply_component=supply,
        preheat_component=preheat,
    )


def _cooling_chain(b: ProjectBuilder, cooling: CoolingSpec) -> None:
    load: Source = cooling.load_w
    chilled_in: Source | None = None
    rejecting: list[str] = []
    if cooling.free_cooling is not None:
        fc = b.add("free_cooling", "free_cooling", cooling.free_cooling, "free_cooling")
        b.boundary_input(fc, "ambient_wet_bulb_degc", cooling.ambient_wet_bulb_degc)
        b.feed(load, fc, "cooling_load_w")
        load = (fc, "residual_cooling_w")
        chilled_in = (fc, "chilled_water_temperature_out_degc")
        rejecting.append(fc)
    if cooling.absorption is not None:
        absorber = "absorption_chiller"
        assert cooling.absorption_cooling_water_in_degc is not None
        b.boundary_input(
            absorber, "cooling_water_temperature_in_degc", cooling.absorption_cooling_water_in_degc
        )
        b.feed(load, absorber, "cooling_load_w")
        if chilled_in is not None:
            b.feed(chilled_in, absorber, "chilled_water_temperature_in_degc")
        load = (absorber, "unmet_cooling_w")
        rejecting.append(absorber)
    if cooling.electric_chiller is not None:
        chiller = b.add(
            "electric_chiller", "electric_chiller", cooling.electric_chiller, "electric_chiller"
        )
        b.boundary_input(chiller, "ambient_dry_bulb_degc", cooling.ambient_dry_bulb_degc)
        b.boundary_input(chiller, "ambient_wet_bulb_degc", cooling.ambient_wet_bulb_degc)
        b.feed(load, chiller, "cooling_load_w")
        if chilled_in is not None:
            b.feed(chilled_in, chiller, "chilled_water_temperature_in_degc")
        rejecting.append(chiller)
    if cooling.heat_rejection is not None and rejecting:
        tower = b.add("heat_rejection", "heat_rejection", cooling.heat_rejection, "heat_rejection")
        b.boundary_input(tower, "ambient_dry_bulb_degc", cooling.ambient_dry_bulb_degc)
        b.boundary_input(tower, "ambient_wet_bulb_degc", cooling.ambient_wet_bulb_degc)
        if len(rejecting) == 1:
            b.connect(rejecting[0], tower, [("heat_rejected_w", "heat_load_w")])
        else:
            adder = b.add(
                "heat_rejection_adder",
                "adder",
                {"num_inputs": len(rejecting)},
                "heat_rejection_adder",
            )
            for k, name in enumerate(rejecting):
                b.connect(name, adder, [("heat_rejected_w", f"input_{k}")])
            b.connect(adder, tower, [("output", "heat_load_w")])


# --------------------------------------------------------------------------- basis (register)
class GeneratorBasis(StrictModel):
    """Register and design-basis values the generator uses (CFG-*, DB-*)."""

    heating_supply_degc: float
    cooling_supply_degc: float
    brine_cooling_max_k: float
    heating_peak_mw: float
    cooling_peak_mw: float
    design_dry_bulb_degc: float
    design_wet_bulb_degc: float
    gas_liquid_ratio_nm3_per_m3: float
    ch4_mol_frac: float
    co2_mol_frac: float
    hex_approach_k: float
    hex_u_w_per_m2k: float
    hex_ntu_per_pass_max: float
    hex_dp_per_ntu_pa: float
    hex_construction: Literal["gasketed", "welded"]
    hex_plate_material: str
    hp_solver: Literal["closed_form", "nelder_mead"] = Field(description="CFG-HP (WP-03 GM-03).")
    hp_mode: Literal["carnot", "lorenz"]
    hp_eta: float = Field(description="HP-ETA-L (Lorenz) — WP-03's register entry.")
    hp_condenser_approach_k: float = Field(description="HP-APPROACH (WP-03).")
    hp_evaporator_approach_k: float = Field(description="HP-APPROACH (WP-03).")
    hp_max_lift_k: float = Field(description="VER-HP-LIFT: sink outlet − source outlet.")
    chp_total_efficiency_frac: float
    chp_electrical_fraction: float = Field(
        description="CHP-EL-FRAC (WP-03); chp.py default 1/3, else its electrical_fraction."
    )
    boiler_efficiency_frac: float
    grid_gas_lhv_j_kg: float
    grid_gas_emission_kg_per_gj: float
    associated_gas_lhv_j_kg: float
    associated_gas_emission_kg_per_gj: float
    associated_gas_density_kg_nm3: float


def _number(register: AssumptionsRegister, assumption_id: str) -> float:
    value = register.get(assumption_id).value
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ConfigError(f"{assumption_id} must hold a number, got {value!r}")
    return float(value)


def _entry(register: AssumptionsRegister, assumption_id: str) -> dict[str, Any]:
    value = register.get(assumption_id).value
    if not isinstance(value, dict):
        raise ConfigError(f"{assumption_id} must hold a mapping, got {value!r}")
    return value


def gas_burner_values(ch4_frac: float, co2_frac: float) -> tuple[float, float, float]:
    """LHV (J/kg), CO₂ factor incl. inherent CO₂ (kg/GJ_LHV) and density (kg/Nm³) of a CH₄/CO₂ gas.

    Source: RC-08 via ``separator.associated_gas_properties`` (80/20: 29.67 MJ/kg, 68.56 kg/GJ,
    0.965 kg/Nm³; pure CH₄: 50.03 MJ/kg).
    """
    gas = associated_gas_properties(ch4_frac, co2_frac)
    factor = gas["co2_if_burned_kg_per_nm3"] / gas["lhv_j_nm3"] * JOULE_PER_GJ
    return float(gas["lhv_j_kg"]), float(factor), float(gas["density_kg_nm3"])


def load_generator_basis(config_dir: Path | str | None = None) -> GeneratorBasis:
    """Collect the generator's values from ``config/`` (design basis, limits, register)."""
    basis: DesignBasis = load_config("design_basis", config_dir)
    limits: HardLimits = load_config("limits", config_dir)
    register: AssumptionsRegister = load_config("assumptions", config_dir)
    climate: ClimateConfig = load_config("climate_nl", config_dir)
    hex_cfg = _entry(register, "CFG-HEX")
    hp_cfg = _entry(register, "CFG-HP")
    chp_cfg = _entry(register, "CFG-CHP")
    boiler_cfg = _entry(register, "CFG-BOILER")
    grid_cfg = _entry(register, "CFG-GRID-GAS")
    # The CHP electrical share is WP-03's register entry; GEMINI chp.py defaults to 1/3 and takes
    # an "electrical_fraction" parameter (WP-11) that the generator writes only when they differ.
    electrical_fraction = _number(register, CHP_FRACTION_ID)
    if not 0.0 < electrical_fraction < 1.0:
        raise ConfigError(f"{CHP_FRACTION_ID} must be in (0, 1), got {electrical_fraction}")
    brine = basis.brine
    assoc_lhv, assoc_factor, assoc_density = gas_burner_values(
        brine.gas_ch4_mol_frac, brine.gas_co2_mol_frac
    )
    grid_lhv, _, _ = gas_burner_values(
        float(grid_cfg["ch4_mol_frac"]), 1.0 - grid_cfg["ch4_mol_frac"]
    )
    return GeneratorBasis(
        heating_supply_degc=limits.heating_supply_degc,
        cooling_supply_degc=limits.cooling_supply_degc,
        brine_cooling_max_k=limits.brine_cooling_max_k,
        heating_peak_mw=basis.heating.peak_mw,
        cooling_peak_mw=basis.cooling.peak_mw,
        design_dry_bulb_degc=climate.design_point.dry_bulb_degc,
        design_wet_bulb_degc=climate.design_point.wet_bulb_degc,
        gas_liquid_ratio_nm3_per_m3=brine.gas_liquid_ratio_nm3_per_m3,
        ch4_mol_frac=brine.gas_ch4_mol_frac,
        co2_mol_frac=brine.gas_co2_mol_frac,
        hex_approach_k=float(hex_cfg["approach_k"]),
        hex_u_w_per_m2k=float(hex_cfg["u_w_per_m2k"]),
        hex_ntu_per_pass_max=float(hex_cfg["ntu_per_pass_max"]),
        hex_dp_per_ntu_pa=float(hex_cfg["dp_per_ntu_psi"]) * PA_PER_PSI,
        hex_construction=hex_cfg["construction"],
        hex_plate_material=str(hex_cfg["plate_material"]),
        hp_solver=hp_cfg["solver"],
        hp_mode=hp_cfg["mode"],
        hp_eta=_number(register, HP_EFFICIENCY_ID),
        hp_condenser_approach_k=float(_entry(register, HP_APPROACH_ID)["condenser_approach_k"]),
        hp_evaporator_approach_k=float(_entry(register, HP_APPROACH_ID)["evaporator_approach_k"]),
        hp_max_lift_k=float(register.get("VER-HP-LIFT").value),  # type: ignore[arg-type]
        chp_total_efficiency_frac=float(chp_cfg["total_efficiency_frac"]),
        chp_electrical_fraction=electrical_fraction,
        boiler_efficiency_frac=float(boiler_cfg["efficiency_frac"]),
        grid_gas_lhv_j_kg=grid_lhv,
        # One CO₂ factor for grid gas: WP-08's ECO-CO2-GAS (ECO-05).
        grid_gas_emission_kg_per_gj=_number(register, "ECO-CO2-GAS"),
        associated_gas_lhv_j_kg=assoc_lhv,
        associated_gas_emission_kg_per_gj=assoc_factor,
        associated_gas_density_kg_nm3=assoc_density,
    )


# --------------------------------------------------------------------------- concept layouts
class ConceptLayout(StrictModel):
    """Equipment of a concept family (spec §3.4)."""

    direct_hex: bool = False
    heat_pump: bool = False
    degas: bool = False
    free_cooling: bool = False
    absorption: bool = False
    storage: bool = False


CONCEPT_LAYOUTS: dict[str, ConceptLayout] = {
    "C0": ConceptLayout(),
    "C1": ConceptLayout(direct_hex=True, degas=True),
    "C2": ConceptLayout(direct_hex=True, heat_pump=True, degas=True, free_cooling=True),
    "C3": ConceptLayout(
        direct_hex=True, heat_pump=True, degas=True, free_cooling=True, absorption=True
    ),
    "C4": ConceptLayout(direct_hex=True, degas=True, free_cooling=True, absorption=True),
    "C5": ConceptLayout(heat_pump=True, degas=True, free_cooling=True),
}
"""C6 = parent + seasonal storage; C7 = parent without degassing; C8 = parent (spare doublet)."""

DERIVED_CONCEPTS = ("C6", "C7", "C8")
_PARENT_ID = re.compile(r"^(C[0-8])")


def concept_layout(
    candidate: Candidate, parent_concept: str | None = None
) -> tuple[str, ConceptLayout, list[str]]:
    """Return (layout concept, layout, notes) of a candidate; C6–C8 take their parent's plant."""
    notes: list[str] = []
    concept = candidate.concept
    if concept not in DERIVED_CONCEPTS:
        return concept, CONCEPT_LAYOUTS[concept], notes
    parent = parent_concept
    if parent is None and candidate.parent_id:
        match = _PARENT_ID.match(candidate.parent_id)
        parent = match.group(1) if match else None
    if parent not in CONCEPT_LAYOUTS or parent == "C0":
        notes.append(
            f"{concept}: parent concept of '{candidate.parent_id}' unknown; C2 plant used "
            "(spec §3.4 'best of C2/C3')"
        )
        parent = "C2"
    layout = CONCEPT_LAYOUTS[parent].model_copy()
    if concept == "C6":
        layout.storage = True
        notes.append(
            "C6: seasonal HT-ATES has no GEMINI component in COMPONENT_REGISTRY; the plant is the "
            f"parent {parent} plant and storage is left to dispatch (WP-03/WP-07)"
        )
    if concept == "C7":
        layout.degas = False
    return parent, layout, notes


# --------------------------------------------------------------------------- loads and dispatch
class PlantLoads(StrictModel):
    """Day-average loads and ambient conditions the plant is configured for."""

    heating_demand_mw: float = Field(ge=0)
    cooling_demand_mw: float = Field(ge=0)
    ambient_dry_bulb_degc: float
    ambient_wet_bulb_degc: float


class PlantDispatch(StrictModel):
    """Operating set-points of one day (WP-07 decides them; defaults are the design point)."""

    doublet_flows_m3h: list[float] | None = Field(
        default=None, description="Per doublet; 0 = off; None: candidate design flow on all."
    )
    absorption_drive_frac: float | None = Field(
        default=None, ge=0, le=1, description="None: the model's required drive flow, capped."
    )
    boiler_gas_kg_s: float = Field(default=0.0, ge=0, description="Peak-boiler grid gas.")
    hex_flow_frac: float = Field(
        default=1.0, ge=0, le=1, description="District share through the plate HEXs."
    )
    hp_flow_frac: float = Field(
        default=1.0, ge=0, le=1, description="District share through the heat pumps."
    )
    heat_pumps_on: bool = Field(
        default=True, description="False (or hp_flow_frac 0): the day's plant has no heat pumps."
    )


class HexDesign(StrictModel):
    """Plate HEX-1 sized at the design flow (RC-03 eq 3, bisection on UA)."""

    ua_w_per_k: float
    duty_mw: float
    brine_flow_m3h: float
    district_flow_m3h: float
    brine_in_degc: float
    brine_out_degc: float
    district_in_degc: float
    district_out_degc: float
    ntu: float
    passes: int
    ntu_per_pass: float
    pressure_drop_per_pass_pa: float = Field(description="24 kPa/NTU (RC-03 eq 4).")
    area_m2: float = Field(description="UA / U, U from CFG-HEX.")
    construction: Literal["gasketed", "welded"]
    plate_material: str
    design_pressure_barg: float
    min_brine_pressure_barg: float


class DoubletPlan(StrictModel):
    """One doublet of the candidate (active or off) with its TL-05 operating point."""

    index: int
    active: bool
    flow_m3h: float
    esp_frequency_hz: float | None = None
    wellhead_temperature_degc: float | None = None
    producer_wellhead_pressure_barg: float | None = None
    injector_wellhead_pressure_barg: float | None = None
    esp_intake_setpoint_barg: float | None = None
    npsh_available_m: float | None = None
    min_brine_outlet_degc: float | None = Field(
        default=None, description="T_wh − DB-12: the coldest brine the plant may return."
    )


class GeneratedConfig(StrictModel):
    """TL-09 output: the GEMINI project of one candidate for one set of loads and set-points."""

    candidate_id: str
    concept: ConceptId
    layout_concept: str = Field(description="Concept whose plant was built (parent for C6–C8).")
    project: dict[str, JsonValue]
    config_hash: str = Field(description="SHA-256 of the canonical project JSON (DesignGuard).")
    roles: dict[str, ComponentRole]
    doublet_of: dict[str, int]
    burner_fuels: dict[str, Fuel]
    supply_component: tuple[str, str] | None
    preheat_component: tuple[str, str] | None = None
    doublets: list[DoubletPlan]
    hex_design: HexDesign | None = None
    reservoir_temperature_degc: float | None = None
    heating_return_degc: float = Field(description="District return (candidate, DB-31).")
    cooling_return_degc: float = Field(description="Cooling return (candidate, DB-32).")
    loads: PlantLoads
    district_flow_m3s: float
    equipment_mw: dict[str, float] = Field(
        description="Nameplates used: peak_boiler_mw, electric_chiller_mw, absorption_chiller_mw."
    )
    gas_handling: GasHandling | None
    absorption_drive_frac: float | None = None
    release_pressure_barg: float | None = Field(
        default=None, description="Bare brine bubble point p_b, bar(g) (WP-05; verifier)."
    )
    flags: list[str] = Field(default_factory=list, description="Design flags (C7 HEX rating …).")
    notes: list[str] = Field(default_factory=list)
    assumption_ids: list[str] = Field(default_factory=list)

    def hex_points(self) -> list[HexPoint]:
        """Design-point ``HexPoint`` evidence of the plate HEXs (RC-03)."""
        h = self.hex_design
        if h is None:
            return []
        return [
            HexPoint(
                name=name,
                hot_in_degc=h.brine_in_degc,
                hot_out_degc=h.brine_out_degc,
                cold_in_degc=h.district_in_degc,
                cold_out_degc=h.district_out_degc,
                construction=h.construction,
                passes=h.passes,
                design_pressure_barg=h.design_pressure_barg,
                min_brine_pressure_barg=h.min_brine_pressure_barg,
                ntu=h.ntu_per_pass,
                pressure_drop_pa=h.pressure_drop_per_pass_pa,
                flow_m3h=max(h.brine_flow_m3h, h.district_flow_m3h),
                area_m2=h.area_m2,
                plate_material=h.plate_material,
                brine_side=True,
            )
            for name, role in self.roles.items()
            if role == "direct_hex"
        ]


class ConfigRequest(StrictModel):
    """TL-09 input."""

    candidate: Candidate
    loads: PlantLoads | None = Field(
        default=None, description="None: design capacity (DB-18/DB-19 peaks at DB-34b ambient)."
    )
    dispatch: PlantDispatch = Field(default_factory=PlantDispatch)
    design: DoubletDesign | None = Field(default=None, description="TL-05 design overrides.")
    parent_concept: ConceptId | None = Field(default=None, description="For C6–C8.")


# --------------------------------------------------------------------------- sizing helpers
def counterflow_ntu(effectiveness: float, capacity_ratio: float) -> float:
    """NTU of a counter-flow exchanger for ε and C_r (RC-03 eq 1 inverted).

    ``NTU = ln((1 − ε C_r)/(1 − ε)) / (1 − C_r)``, and ``ε/(1 − ε)`` at C_r = 1 (the limit).
    """
    if not 0.0 <= effectiveness < 1.0:
        raise ValueError(f"effectiveness must be in [0, 1), got {effectiveness}")
    if not 0.0 <= capacity_ratio <= 1.0:
        raise ValueError(f"capacity ratio must be in [0, 1], got {capacity_ratio}")
    if abs(1.0 - capacity_ratio) < CR_BALANCED_TOL:
        return effectiveness / (1.0 - effectiveness)
    return math.log((1.0 - effectiveness * capacity_ratio) / (1.0 - effectiveness)) / (
        1.0 - capacity_ratio
    )


def hex_ua_for_outlet(
    *,
    primary_flow_m3s: float,
    primary_in_degc: float,
    secondary_flow_m3s: float,
    secondary_in_degc: float,
    secondary_out_degc: float,
    primary_fluid: dict[str, float],
    secondary_fluid: dict[str, float],
) -> float:
    """UA (W/K) at which a counter-flow HEX heats the secondary side to ``secondary_out_degc``.

    RC-03 eq 3 asks for the UA at which GEMINI's ``HeatExchanger`` hits the target; that model is
    RC-03 eq 1, so the UA is taken from eq 1 inverted (:func:`counterflow_ntu`). This is exact
    also at C_r = 1, where GEMINI's C_r < 1 branch loses precision (a bisection on the model
    returns a UA ≈ 5 % high at exactly balanced flows). Raises ``ValueError`` if unreachable.
    """
    c_p = primary_flow_m3s * primary_fluid["fluid_density"] * primary_fluid["specific_heat"]
    c_s = secondary_flow_m3s * secondary_fluid["fluid_density"] * secondary_fluid["specific_heat"]
    c_min, c_max = min(c_p, c_s), max(c_p, c_s)
    if c_min <= 0.0 or primary_in_degc <= secondary_in_degc:
        raise ValueError("no heat transfer possible (zero flow or primary not hotter)")
    duty = c_s * (secondary_out_degc - secondary_in_degc)
    effectiveness = duty / (c_min * (primary_in_degc - secondary_in_degc))
    return counterflow_ntu(effectiveness, c_min / c_max) * c_min


def size_plate_hex(
    *,
    brine_flow_m3s: float,
    brine_in_degc: float,
    district_in_degc: float,
    district_supply_degc: float,
    brine_cooling_max_k: float,
    brine_fluid: dict[str, float],
    gb: GeneratorBasis,
    pressure_barg: float,
    min_pressure_barg: float,
) -> HexDesign | None:
    """Size the counter-flow plate HEX-1 (RC-03 eq 3; UA from eq 1 inverted).

    Design outlets: district ``min(T_brine,in − approach, T_supply)``, brine
    ``max(T_brine,in − DB-12, T_return + approach)``; the district flow closes the energy balance.
    Returns None when no useful duty is possible (e.g. 57 °C brine against a 55 °C return, C5).
    """
    approach = gb.hex_approach_k
    district_out = min(brine_in_degc - approach, district_supply_degc)
    brine_out = max(brine_in_degc - brine_cooling_max_k, district_in_degc + approach)
    if district_out <= district_in_degc or brine_out >= brine_in_degc:
        return None
    rho_b, cp_b = brine_fluid["fluid_density"], brine_fluid["specific_heat"]
    rho_w, cp_w = DISTRICT_FLUID["fluid_density"], DISTRICT_FLUID["specific_heat"]
    c_brine = rho_b * cp_b * brine_flow_m3s
    duty = c_brine * (brine_in_degc - brine_out)
    district_flow = duty / (rho_w * cp_w * (district_out - district_in_degc))
    ua = hex_ua_for_outlet(
        primary_flow_m3s=brine_flow_m3s,
        primary_in_degc=brine_in_degc,
        secondary_flow_m3s=district_flow,
        secondary_in_degc=district_in_degc,
        secondary_out_degc=district_out,
        primary_fluid=brine_fluid,
        secondary_fluid=DISTRICT_FLUID,
    )
    ntu = ua / min(c_brine, rho_w * cp_w * district_flow)
    passes = max(1, math.ceil(ntu / gb.hex_ntu_per_pass_max - 1e-9))
    return HexDesign(
        ua_w_per_k=ua,
        duty_mw=duty / 1e6,
        brine_flow_m3h=brine_flow_m3s * SECONDS_PER_HOUR,
        district_flow_m3h=district_flow * SECONDS_PER_HOUR,
        brine_in_degc=brine_in_degc,
        brine_out_degc=brine_out,
        district_in_degc=district_in_degc,
        district_out_degc=district_out,
        ntu=ntu,
        passes=passes,
        ntu_per_pass=ntu / passes,
        pressure_drop_per_pass_pa=gb.hex_dp_per_ntu_pa * ntu / passes,
        area_m2=ua / gb.hex_u_w_per_m2k,
        construction=gb.hex_construction,
        plate_material=gb.hex_plate_material,
        design_pressure_barg=pressure_barg,
        min_brine_pressure_barg=min_pressure_barg,
    )


def district_flow_m3s(heating_mw: float, supply_degc: float, return_degc: float) -> float:
    """District water flow that carries ``heating_mw`` between return and supply (ρ c_p V ΔT)."""
    rho, cp = DISTRICT_FLUID["fluid_density"], DISTRICT_FLUID["specific_heat"]
    return heating_mw * 1e6 / (rho * cp * (supply_degc - return_degc))


def tower_water_degc(
    dry_bulb_degc: float, wet_bulb_degc: float, parameters: dict[str, Any] | None = None
) -> float:
    """Cooling-tower leaving water from GEMINI ``HeatRejection`` (T_wb + approach, RC-07)."""
    tower = HeatRejection()
    if parameters:
        tower.update_parameters(parameters)
    tower.calculate_output(
        {
            "ambient_dry_bulb_degc": dry_bulb_degc,
            "ambient_wet_bulb_degc": wet_bulb_degc,
            "heat_load_w": 1.0,
        }
    )
    return float(tower.output["water_temperature_out_degc"])


def required_absorption_drive_m3s(
    cooling: CoolingSpec, drive_temperature_degc: float
) -> tuple[float, float]:
    """Return (required, minimum) absorption-chiller drive flow in m³/s for today's load.

    Free cooling first (GEMINI ``FreeCooling``), then the absorption model in its fixed-ΔT
    drive mode reports the drive flow for the residual load up to its nameplate (RC-05, RC-07).
    """
    assert cooling.absorption is not None
    load = cooling.load_w
    chilled_in: float | None = None
    if cooling.free_cooling is not None:
        fc = FreeCooling()
        fc.update_parameters(cooling.free_cooling)
        fc.calculate_output(
            {"ambient_wet_bulb_degc": cooling.ambient_wet_bulb_degc, "cooling_load_w": load}
        )
        load = float(fc.output["residual_cooling_w"])
        chilled_in = float(fc.output["chilled_water_temperature_out_degc"])
    absorber = AbsorptionChiller()
    absorber.update_parameters(cooling.absorption)
    nominal = float(cooling.absorption.get("nominal_capacity_w") or 0.0)  # type: ignore[arg-type]
    u: dict[str, Any] = {
        "drive_temperature_in_degc": drive_temperature_degc,
        "cooling_water_temperature_in_degc": cooling.absorption_cooling_water_in_degc,
        "cooling_load_w": min(load, nominal),
    }
    if chilled_in is not None:
        u["chilled_water_temperature_in_degc"] = chilled_in
    absorber.calculate_output(u)
    need = float(absorber.output["drive_flow_rate_m3s"]) if load > 0 else 0.0
    return need, float(absorber.output.get("minimum_drive_flow_rate_m3s", 0.0))


# --------------------------------------------------------------------------- planning
def _equipment_mw(candidate: Candidate, key: str, default_mw: float) -> float:
    value = candidate.equipment_sizes.get(key)
    return float(value) if value is not None else default_mw


def design_loads(gb: GeneratorBasis) -> PlantLoads:
    """Design-capacity loads: DB-18/DB-19 peaks at the DB-34b summer design ambient."""
    return PlantLoads(
        heating_demand_mw=gb.heating_peak_mw,
        cooling_demand_mw=gb.cooling_peak_mw,
        ambient_dry_bulb_degc=gb.design_dry_bulb_degc,
        ambient_wet_bulb_degc=gb.design_wet_bulb_degc,
    )


def plan_plant(
    candidate: Candidate,
    loads: PlantLoads,
    dispatch: PlantDispatch | None = None,
    *,
    design: DoubletDesign | None = None,
    parent_concept: str | None = None,
    gb: GeneratorBasis,
    sub: SubsurfaceBasis,
) -> tuple[PlantSpec, GeneratedConfig]:
    """Fill a :class:`PlantSpec` for a candidate and return it with the (unbuilt) metadata.

    Raises :class:`ConfigError` if a doublet's TL-05 point is infeasible or the dispatch does not
    match the candidate. The returned ``GeneratedConfig`` has an empty ``project``; use
    :func:`generate_plant`.
    """
    dispatch = dispatch or PlantDispatch()
    layout_concept, layout, notes = concept_layout(candidate, parent_concept)
    flags: list[str] = []
    if candidate.doublets and candidate.gas_handling is not None:
        degas = candidate.gas_handling == GasHandling.DEGAS_USE
        if degas != layout.degas:
            layout = layout.model_copy(update={"degas": degas})
            notes.append(
                f"gas handling '{candidate.gas_handling.value}' overrides the {layout_concept} "
                "default (DB-36, DR-07)"
            )
    if layout.heat_pump and (not dispatch.heat_pumps_on or dispatch.hp_flow_frac <= 0.0):
        layout = layout.model_copy(update={"heat_pump": False})
        notes.append("heat pumps off today (dispatch): brine goes from HEX-1 to filter B")
    n = candidate.doublets
    flows = dispatch.doublet_flows_m3h
    if flows is None:
        flows = [float(candidate.design_flow_m3h or 0.0)] * n
    if len(flows) != n:
        raise ConfigError(f"dispatch gives {len(flows)} doublet flows for {n} doublet(s)")
    if any(q < 0 for q in flows):
        raise ConfigError(f"doublet flows must be >= 0, got {flows}")

    # Return temperatures are the candidate's (filled from DB-31/DB-32 or the user's answer).
    supply_t, return_t = gb.heating_supply_degc, candidate.heating_return_degc
    trains: list[DoubletTrainSpec] = []
    plans: list[DoubletPlan] = []
    hex_design: HexDesign | None = None
    release: float | None = None
    brine_fluid: dict[str, float] = {}
    doublet: ResolvedDoublet | None = None
    if n:
        base = design or DoubletDesign()
        base = base.model_copy(update={"depth_m": candidate.depth_m})
        doublet = resolve_doublet(base, sub)
        if not layout.degas:
            setpoint = doublet.bubble_point.intake_setpoint_barg
            given = base.producer_wellhead_pressure_barg
            if given is None or given < setpoint:
                base = base.model_copy(update={"producer_wellhead_pressure_barg": setpoint})
                doublet = resolve_doublet(base, sub)
                notes.append(
                    f"pressurised loop: producer WHP {setpoint:.2f} bar(g) = SUB-PBUB setpoint "
                    f"(1.2 × p_b(a)), p_b {doublet.bubble_point.release_pressure_barg:.2f} bar(g)"
                )
        release = doublet.bubble_point.release_pressure_barg
        brine_fluid = heat_exchanger_fluid_parameters(doublet.pvt)
        design_flow = float(candidate.design_flow_m3h or 0.0)
        design_point = _feasible_point(doublet, design_flow, "design")
        assert design_point.wellhead_temperature_degc is not None
        if layout.direct_hex:
            hex_pressure = (
                sub.injection_pump_suction_barg
                if layout.degas
                else float(design_point.producer_wellhead_pressure_barg or 0.0)
            )
            hex_design = size_plate_hex(
                brine_flow_m3s=design_flow / SECONDS_PER_HOUR,
                brine_in_degc=design_point.wellhead_temperature_degc,
                district_in_degc=return_t,
                district_supply_degc=supply_t,
                brine_cooling_max_k=gb.brine_cooling_max_k,
                brine_fluid=brine_fluid,
                gb=gb,
                pressure_barg=hex_pressure,
                min_pressure_barg=hex_pressure,
            )
            if hex_design is None:
                notes.append("direct HEX dropped: brine too cold for the district return")
            else:
                _, rating_bar = HEX_RATING[hex_design.construction]
                if hex_design.design_pressure_barg > rating_bar:
                    flags.append(
                        f"pressurised loop: brine plate HEX (and HP evaporator) at "
                        f"{hex_design.design_pressure_barg:.1f} bar(g) exceeds the "
                        f"{hex_design.construction} plate-HEX rating {rating_bar:.2f} bar(g) "
                        "(RC-03; GNS p.40, Guidebook p.218) and the loop "
                        f"must stay above p_b {release:.1f} bar(g): use shell-and-tube or "
                        "plate-and-shell (no card cost) or degas with inhibitor (DR-07); the "
                        "verifier fails PH-09 and PD-10 holds with zero margin"
                    )
        t_design = design_point.wellhead_temperature_degc
        if layout.degas and t_design > doublet.vapour_pressure_bound_valid_to_degc:
            flags.append(
                f"degassed brine at {t_design:.1f} °C is let down to "
                f"{sub.injection_pump_suction_barg:g} bar(g) (CFG-SURFACE): above "
                f"{doublet.vapour_pressure_bound_valid_to_degc:g} °C the saturation pressure "
                "exceeds 1 atm and flashing is not checked (no saturation data, RC-01); hold the "
                "degasser and HEX above saturation"
            )
        for k, q in enumerate(flows):
            index = k + 1
            if q <= 0:
                plans.append(DoubletPlan(index=index, active=False, flow_m3h=0.0))
                continue
            point = _feasible_point(doublet, q, f"doublet {index}")
            assert point.wellhead_temperature_degc is not None
            assert point.esp_frequency_hz is not None
            t_wh = point.wellhead_temperature_degc
            whp = float(
                point.producer_wellhead_pressure_barg or doublet.producer_wellhead_pressure_barg
            )
            plans.append(
                DoubletPlan(
                    index=index,
                    active=True,
                    flow_m3h=q,
                    esp_frequency_hz=point.esp_frequency_hz,
                    wellhead_temperature_degc=t_wh,
                    producer_wellhead_pressure_barg=whp,
                    injector_wellhead_pressure_barg=point.injector_wellhead_pressure_barg,
                    esp_intake_setpoint_barg=point.intake_setpoint_barg,
                    npsh_available_m=point.npsh_available_m,
                    min_brine_outlet_degc=t_wh - gb.brine_cooling_max_k,
                )
            )
            trains.append(
                _train(index, doublet, point, whp, layout, hex_design, brine_fluid, gb, sub)
            )

    active = [p for p in plans if p.active]
    district_flow = district_flow_m3s(loads.heating_demand_mw, supply_t, return_t)
    fracs = None
    if active:
        total = sum(p.flow_m3h for p in active)
        fracs = [p.flow_m3h / total for p in active]
    degassed = bool(trains) and layout.degas
    chp_params = None
    if degassed:
        chp_params = {
            "caloric_value": gb.associated_gas_lhv_j_kg,
            "efficiency_factor": gb.chp_total_efficiency_frac,
            "gas_emission_factor": gb.associated_gas_emission_kg_per_gj,
            **DISTRICT_FLUID,
        }
        if not math.isclose(
            gb.chp_electrical_fraction, CHP.ELECTRICAL_POWER_FRACTION, abs_tol=CHP_FRACTION_TOL
        ):
            # CHP-EL-FRAC sensitivity (e.g. DEA 0.50): chp.py's default 1/3 is kept otherwise, so
            # the base-case project (and its hash) is unchanged.
            chp_params["electrical_fraction"] = gb.chp_electrical_fraction
    boiler_mw = _equipment_mw(candidate, "peak_boiler_mw", gb.heating_peak_mw)
    district = DistrictSpec(
        flow_m3s=district_flow,
        return_temperature_degc=return_t,
        supply_temperature_degc=supply_t,
        branch_fracs=fracs,
        chp=chp_params,
        chp_fuel="associated_gas",
        boiler={
            "caloric_value": gb.grid_gas_lhv_j_kg,
            "efficiency_factor": gb.boiler_efficiency_frac,
            "gas_emission_factor": gb.grid_gas_emission_kg_per_gj,
            **DISTRICT_FLUID,
        },
        boiler_fuel="grid_gas",
        boiler_gas_kg_s=dispatch.boiler_gas_kg_s,
        hex_flow_frac=dispatch.hex_flow_frac if hex_design is not None and trains else None,
        hp_flow_frac=dispatch.hp_flow_frac if layout.heat_pump and trains else None,
    )

    cooling, equipment, drive_frac = _cooling_spec(
        candidate, layout, loads, dispatch, gb, active, trains, brine_fluid, notes
    )
    equipment["peak_boiler_mw"] = boiler_mw
    spec = PlantSpec(
        name=f"{candidate.id} ({candidate.concept}, layout {layout_concept})",
        doublets=trains,
        district=district,
        cooling=cooling,
    )
    meta = GeneratedConfig(
        candidate_id=candidate.id,
        concept=candidate.concept,
        layout_concept=layout_concept,
        project={},
        config_hash="",
        roles={},
        doublet_of={},
        burner_fuels={},
        supply_component=None,
        doublets=plans,
        hex_design=hex_design,
        reservoir_temperature_degc=doublet.reservoir_temperature_degc if doublet else None,
        heating_return_degc=return_t,
        cooling_return_degc=candidate.cooling_return_degc,
        loads=loads,
        district_flow_m3s=district_flow,
        equipment_mw=equipment,
        gas_handling=candidate.gas_handling,
        absorption_drive_frac=drive_frac,
        release_pressure_barg=release,
        flags=flags,
        notes=notes,
        assumption_ids=list(GENERATOR_ASSUMPTION_IDS),
    )
    return spec, meta


def _feasible_point(doublet: ResolvedDoublet, flow_m3h: float, label: str) -> OperatingPoint:
    point = operating_point(doublet, flow_m3h)
    if not point.feasible:
        raise ConfigError(f"{label}: TL-05 point at {flow_m3h:g} m³/h infeasible: {point.reasons}")
    return point


def _train(
    index: int,
    doublet: ResolvedDoublet,
    point: OperatingPoint,
    whp_barg: float,
    layout: ConceptLayout,
    hex_design: HexDesign | None,
    brine_fluid: dict[str, float],
    gb: GeneratorBasis,
    sub: SubsurfaceBasis,
) -> DoubletTrainSpec:
    """Brine train of one active doublet (CFG-SURFACE, CFG-HEX, CFG-HP)."""
    assert point.esp_frequency_hz is not None and point.wellhead_temperature_degc is not None
    flow_m3s = point.flow_m3h / SECONDS_PER_HOUR
    adiabatic_filter = {
        "base_resistance": 0.0,
        "fouling_coeff_a": 0.0,
        "fouling_coeff_b": 0.0,
        "temperature_drop": 0.0,
    }
    separator = None
    if layout.degas:
        # Surface losses lumped in the degasser letdown: WHP → SUB-PSUC at this flow (CFG-SURFACE).
        letdown = max(whp_barg - sub.injection_pump_suction_barg, 0.0) / flow_m3s
        separator = {
            "flow_resistance": letdown,
            "temperature_drop": 0.0,
            "gas_water_ratio": gb.gas_liquid_ratio_nm3_per_m3,
            "ch4_fraction": gb.ch4_mol_frac,
            "co2_fraction": gb.co2_mol_frac,
        }
    hex_params = None
    if hex_design is not None:
        hex_params = {
            "flow_resistance": 0.0,
            "flow_configuration": "counter",
            "heat_transfer_coefficient": hex_design.ua_w_per_k,
            **brine_fluid,
            "secondary_fluid_density": DISTRICT_FLUID["fluid_density"],
            "secondary_specific_heat": DISTRICT_FLUID["specific_heat"],
        }
    hp_params = None
    if layout.heat_pump:
        if gb.hp_mode != "lorenz":
            raise ConfigError(f"CFG-HP mode {gb.hp_mode!r}: {HP_EFFICIENCY_ID} is a Lorenz η")
        hp_params = {
            # WP-03 closed form (RC-04 eq 1–3, bracketed root; COP ≤ VER-HP-ETA × Carnot at the
            # refrigerant temperatures with HP-APPROACH) — GEMINI's default Nelder–Mead untouched.
            "solver": gb.hp_solver,
            "mode": gb.hp_mode,
            "eta_lorenz": gb.hp_eta,
            "condenser_approach_k": gb.hp_condenser_approach_k,
            "evaporator_approach_k": gb.hp_evaporator_approach_k,
            "Th_out_target": gb.heating_supply_degc,
            # DB-12 brine floor, and VER-HP-LIFT: sink outlet − source outlet ≤ 60 K.
            "Ts_in_minimum": max(
                point.wellhead_temperature_degc - gb.brine_cooling_max_k,
                gb.heating_supply_degc - gb.hp_max_lift_k,
            ),
            "rho_s": brine_fluid["fluid_density"],
            "Cp_s": brine_fluid["specific_heat"],
            "rho_h": DISTRICT_FLUID["fluid_density"],
            "Cp_h": DISTRICT_FLUID["specific_heat"],
        }
    producer = producer_well_parameters(doublet, point.esp_frequency_hz, point.flow_m3h)
    producer["flow_bracket_m3h"] = list(producer["flow_bracket_m3h"])
    injector = injector_well_parameters(doublet)
    injector["pressure_bracket_bar"] = list(injector["pressure_bracket_bar"])
    return DoubletTrainSpec(
        index=index,
        producer=producer,
        injector=injector,
        wellhead_pressure_barg=whp_barg,
        wellhead_temperature_degc=point.wellhead_temperature_degc,
        separator=separator,
        filter_a=dict(adiabatic_filter),
        filter_b=dict(adiabatic_filter),
        hex=hex_params,
        heat_pump=hp_params,
    )


def _cooling_spec(
    candidate: Candidate,
    layout: ConceptLayout,
    loads: PlantLoads,
    dispatch: PlantDispatch,
    gb: GeneratorBasis,
    active: list[DoubletPlan],
    trains: list[DoubletTrainSpec],
    brine_fluid: dict[str, float],
    notes: list[str],
) -> tuple[CoolingSpec, dict[str, float], float | None]:
    """Cooling chain of the concept (RC-05/06/07; CFG-SIZING)."""
    equipment = {
        "electric_chiller_mw": _equipment_mw(candidate, "electric_chiller_mw", gb.cooling_peak_mw)
    }
    tower = {"technology": "cooling_tower"}
    cooling = CoolingSpec(
        load_w=loads.cooling_demand_mw * 1e6,
        ambient_dry_bulb_degc=loads.ambient_dry_bulb_degc,
        ambient_wet_bulb_degc=loads.ambient_wet_bulb_degc,
        electric_chiller={
            "nominal_capacity_w": equipment["electric_chiller_mw"] * 1e6,
            "heat_rejection": "cooling_tower",
            "chilled_water_temperature_out_degc": gb.cooling_supply_degc,
            "chilled_water_temperature_in_degc": candidate.cooling_return_degc,
        },
        heat_rejection=tower,
    )
    if layout.free_cooling:
        # The tower is a separate heat_rejection unit: count its fan once (RC-07).
        cooling.free_cooling = {
            "aux_power_fraction": 0.0,
            "chilled_water_temperature_out_degc": gb.cooling_supply_degc,
            "chilled_water_temperature_in_degc": candidate.cooling_return_degc,
        }
    drive_frac: float | None = None
    if layout.absorption:
        if not trains:
            notes.append("absorption chiller omitted: no active doublet to drive it today")
            return cooling, equipment, None
        equipment["absorption_chiller_mw"] = _equipment_mw(
            candidate, "absorption_chiller_mw", gb.cooling_peak_mw
        )
        drive = trains[0]
        cooling.absorption = {
            "nominal_capacity_w": equipment["absorption_chiller_mw"] * 1e6,
            "include_heat_rejection_auxiliaries": False,
            "chilled_water_temperature_out_degc": gb.cooling_supply_degc,
            "chilled_water_temperature_in_degc": candidate.cooling_return_degc,
            # Brine through titanium generator tubes (RC-05 p.266–267): drive side = brine.
            "drive_density_kg_m3": brine_fluid["fluid_density"],
            "drive_specific_heat_j_kgk": brine_fluid["specific_heat"],
        }
        cooling.absorption_doublet = drive.index
        cooling.absorption_cooling_water_in_degc = tower_water_degc(
            loads.ambient_dry_bulb_degc, loads.ambient_wet_bulb_degc, tower
        )
        drive_frac = dispatch.absorption_drive_frac
        if drive_frac is None:
            need, _ = required_absorption_drive_m3s(cooling, drive.wellhead_temperature_degc)
            available = active[0].flow_m3h / SECONDS_PER_HOUR
            drive_frac = min(1.0, need / available) if available > 0 else 0.0
        cooling.absorption_drive_frac = drive_frac
    return cooling, equipment, drive_frac


def generate_plant(
    candidate: Candidate,
    loads: PlantLoads | None = None,
    dispatch: PlantDispatch | None = None,
    *,
    design: DoubletDesign | None = None,
    parent_concept: str | None = None,
    gb: GeneratorBasis | None = None,
    sub: SubsurfaceBasis | None = None,
    config_dir: Path | str | None = None,
) -> GeneratedConfig:
    """Plan and build the GEMINI project of one candidate (see module doc)."""
    gb = gb or load_generator_basis(config_dir)
    sub = sub or load_subsurface_basis(config_dir)
    loads = loads or design_loads(gb)
    spec, meta = plan_plant(
        candidate,
        loads,
        dispatch,
        design=design,
        parent_concept=parent_concept,
        gb=gb,
        sub=sub,
    )
    built = build_plant_project(spec)
    return meta.model_copy(
        update={
            "project": built.project,
            "config_hash": project_hash(built.project),
            "roles": built.roles,
            "doublet_of": built.doublet_of,
            "burner_fuels": built.burner_fuels,
            "supply_component": built.supply_component,
            "preheat_component": built.preheat_component,
        }
    )


def project_hash(project: dict[str, Any]) -> str:
    """SHA-256 of the canonical project JSON; ``_``-prefixed comment keys are ignored."""
    return config_hash(_strip_comments(project), namespace=f"gemini_project:v{GENERATOR_VERSION}")


def _strip_comments(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_comments(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, list):
        return [_strip_comments(v) for v in value]
    return value


def _summary(config: GeneratedConfig) -> str:
    active = [p for p in config.doublets if p.active]
    flows = "/".join(f"{p.flow_m3h:g}" for p in active) or "-"
    n_components = len(config.project["components"])  # type: ignore[arg-type]
    n_connections = len(config.project["connections"])  # type: ignore[arg-type]
    text = (
        f"{TOOL_ID} {config.candidate_id}: {n_components} components, "
        f"{n_connections} connections; "
        f"{len(active)}/{len(config.doublets)} doublets at {flows} m³/h"
    )
    if config.hex_design is not None:
        text += f"; HEX UA {config.hex_design.ua_w_per_k / 1e6:.2f} MW/K"
    if config.flags:
        text += f"; FLAG: {config.flags[0]}"
    return text[:300]


def generate_gemini_config(
    request: ConfigRequest,
    *,
    config_dir: Path | str | None = None,
) -> ToolResult[GeneratedConfig]:
    """TL-09: GEMINI project JSON for one candidate (spec §3.4, §3.5; RC-03, RC-08)."""
    config = generate_plant(
        request.candidate,
        request.loads,
        request.dispatch,
        design=request.design,
        parent_concept=request.parent_concept,
        config_dir=config_dir,
    )
    return ToolResult[GeneratedConfig](
        data=config,
        summary=_summary(config),
        warnings=config.flags + config.notes,
        assumption_ids=config.assumption_ids,
        sources=["spec §3.4", "spec §3.5", "RC-03", "RC-08", "RC-01", "GEMINI project_loader"],
    )


__all__ = [
    "CONCEPT_LAYOUTS",
    "DISTRICT_FLUID",
    "GENERATOR_VERSION",
    "TOOL_ID",
    "BuiltProject",
    "ConceptLayout",
    "ConfigError",
    "ConfigRequest",
    "CoolingSpec",
    "DistrictSpec",
    "DoubletPlan",
    "DoubletTrainSpec",
    "GeneratedConfig",
    "GeneratorBasis",
    "HexDesign",
    "PlantDispatch",
    "PlantLoads",
    "PlantSpec",
    "ProjectBuilder",
    "build_plant_project",
    "concept_layout",
    "counterflow_ntu",
    "design_loads",
    "district_flow_m3s",
    "gas_burner_values",
    "generate_gemini_config",
    "generate_plant",
    "hex_ua_for_outlet",
    "load_generator_basis",
    "plan_plant",
    "project_hash",
    "required_absorption_drive_m3s",
    "size_plate_hex",
    "tower_water_degc",
]
