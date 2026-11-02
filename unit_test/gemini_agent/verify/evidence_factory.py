"""Evidence bundles for DesignVerifier tests: one physically consistent design that passes.

Day energy balance (spec §3.5 structure): the brine heat ρ·Q·c_p·ΔT (DB-08 1030 kg/m³, DB-33
4100 J/(kg K), 86 → 46 °C) is drawn by the direct HEX, the HTHP evaporator (Q_cond − W) and the
absorption drive (cooling / COP); the boiler tops the network up to the 20 MW heating demand.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.config import load_config
from gemini_agent.schemas.results import AnnualResult, DayResult, KPISet
from gemini_agent.tools.design_basis import brine_heat_mw
from gemini_agent.verify.design_verifier import (
    AbsorptionPoint,
    DoubletPointEvidence,
    EspPoint,
    HeatPumpPoint,
    HexPoint,
    Mitigation,
    PumpPoint,
    ScalingEvidence,
    SubsurfaceEvidence,
    VerificationEvidence,
)

REPO = Path(__file__).resolve().parents[3]
PROJECT_PATH = REPO / "simulator_examples" / "projects" / "doublet_plant.json"
CANDIDATE_ID = "C2-2d"
RHO = load_config("design_basis").brine.density_kg_m3
CP = float(load_config("assumptions").get("DB-33").value)
HP_COP = 3.5
ABS_COP = 0.65
HEATING_MW = 20.0
COOLING_MW = 2.0
HTHP_HEAT_MW = 6.0
BOILER_EFF = 0.9  # fixture value only (the verifier checks that fuel exists, not its amount)


def project() -> dict[str, Any]:
    """Return the GEMINI doublet example plant: one well pair, HEX, boiler and CHP."""
    return json.loads(PROJECT_PATH.read_text(encoding="utf-8"))


def candidate(candidate_id: str = CANDIDATE_ID, **overrides: Any) -> Candidate:
    """Two-doublet candidate at the design-basis depth, degassed loop (RC-01 item 9 base route)."""
    data: dict[str, Any] = {
        "id": candidate_id,
        "concept": "C2",
        "doublets": 2,
        "depth_m": 2500.0,
        "design_flow_m3h": 300.0,
        "gas_handling": "degas_use",
        "heating_return_degc": 55.0,
        "cooling_return_degc": 26.0,
        "equipment_sizes": {"hthp_heating_mw": 6.0, "absorption_cooling_mw": 3.0},
    }
    data.update(overrides)
    return Candidate.model_validate(data)


def balanced_sources(flows_m3h: list[float]) -> dict[str, dict[str, float]]:
    """Heat, cooling, electricity and brine-heat dicts that close every balance for one day."""
    brine = sum(brine_heat_mw(q, RHO, CP, 40.0) for q in flows_m3h)
    hthp_el = HTHP_HEAT_MW / HP_COP
    drive = COOLING_MW / ABS_COP
    direct = brine - (HTHP_HEAT_MW - hthp_el) - drive
    return {
        "heat_by_source_mw": {
            "geothermal_hex": direct,
            "hthp": HTHP_HEAT_MW,
            "boiler": HEATING_MW - direct - HTHP_HEAT_MW,
        },
        "cool_by_source_mw": {"absorption": COOLING_MW},
        "electricity_by_consumer_mw": {"esp": 0.4, "injection_pump": 0.3, "hthp": hthp_el},
        "gas_fuel_by_consumer_mw": {"boiler": (HEATING_MW - direct - HTHP_HEAT_MW) / BOILER_EFF},
        "brine_heat_drawn_mw": {
            "geothermal_hex": direct,
            "hthp": HTHP_HEAT_MW - hthp_el,
            "absorption": drive,
        },
    }


def day(index: int, **overrides: Any) -> DayResult:
    """One balanced day: doublet 2 on planned outage on day 0, both producing on day 1."""
    flows = [300.0, 0.0] if index == 0 else [250.0, 200.0]
    base: dict[str, Any] = {
        "day_index": index,
        "date": dt.date(2027, 1, 1) + dt.timedelta(days=index),
        "ambient_dry_bulb_degc": 5.0,
        "heating_demand_mw": HEATING_MW,
        "cooling_demand_mw": COOLING_MW,
        "doublet_available": [True, False] if index == 0 else [True, True],
        "doublet_flow_m3h": flows,
        "injection_flow_m3h": list(flows),
        "production_temperature_degc": [86.0, 86.0],
        "injection_temperature_degc": [46.0, 46.0],
        "heating_supply_degc": 100.0,
        "heating_return_degc": 55.0,
        "cooling_supply_degc": 20.0,
        "cooling_return_degc": 26.0,
        **balanced_sources([q for q in flows if q > 0]),
        "margins": {"hex_approach_k": 3.0},
    }
    base.update(overrides)
    return DayResult.model_validate(base)


def annual(days: list[DayResult] | None = None, candidate_id: str = CANDIDATE_ID) -> AnnualResult:
    """Two-day full-mode result."""
    return AnnualResult(
        candidate_id=candidate_id, year=2027, interpretation="A", days=days or [day(0), day(1)]
    )


def fast_day(index: int, shift_mw: float = 0.1) -> DayResult:
    """Fast-mode day: ``shift_mw`` moved from the direct HEX to the boiler."""
    full = day(index)
    heat = dict(full.heat_by_source_mw)
    heat["geothermal_hex"] -= shift_mw
    heat["boiler"] += shift_mw
    return full.model_copy(update={"heat_by_source_mw": heat})


def hex_point(**changes: Any) -> HexPoint:
    """HEX-1 design point: brine 86 → 70 °C, network 55 → 82 °C, single pass.

    Approach 4 K; LMTD (4 − 15)/ln(4/15) = 8.32 K; ideal NTU 27/8.32 = 3.25 ≤ 3.5 ≤ 3.25/0.85;
    Δp 84 kPa = 24 kPa per NTU (RC-03).
    """
    data: dict[str, Any] = {
        "name": "HEX-1",
        "hot_in_degc": 86.0,
        "hot_out_degc": 70.0,
        "cold_in_degc": 55.0,
        "cold_out_degc": 82.0,
        "design_pressure_barg": 18.0,
        "min_brine_pressure_barg": 16.0,
        "ntu": 3.5,
        "pressure_drop_pa": 84_000.0,
        "flow_m3h": 300.0,
        "area_m2": 400.0,
        "plate_material": "titanium Gr.1",
    }
    data.update(changes)
    return HexPoint.model_validate(data)


def good_evidence(candidate_id: str = CANDIDATE_ID, **overrides: Any) -> VerificationEvidence:
    """Evidence on which every check passes (verdict ACCEPTED)."""
    data: dict[str, Any] = {
        "candidate": candidate(candidate_id),
        "project": project(),
        "annual": annual(candidate_id=candidate_id),
        "fast_annual": annual([fast_day(0), fast_day(1)], candidate_id),
        "kpis": KPISet(
            candidate_id=candidate_id,
            thermal_breakthrough_yr=33.9,
            exergy_efficiency_frac=0.42,
            exergy_dead_state="daily ambient dry-bulb (DB-34d)",
        ),
        "hex_points": [hex_point()],
        "heat_pumps": [
            HeatPumpPoint(
                name="HTHP",
                cop=HP_COP,
                condensing_degc=103.0,
                evaporating_degc=43.0,
                sink_out_degc=100.0,
                source_out_degc=46.0,
            )
        ],
        "absorption": [
            AbsorptionPoint(
                name="ABS-1",
                cop=ABS_COP,
                cop_min=0.55,
                cop_max=0.75,
                cooling_mw=COOLING_MW,
                rated_cooling_mw=3.0,
                pumping_power_mw=0.05,
                electric_chiller_power_mw=0.6,
            )
        ],
        "pumps": [
            PumpPoint(
                name="injection_pump",
                flow_m3h=300.0,
                pressure_rise_pa=1.0e6,
                shaft_power_w=300.0 / 3600.0 * 1.0e6 / 0.7,
            )
        ],
        "esps": [
            EspPoint(
                name="ESP-1", intake_pressure_barg=61.0, npsh_available_m=60.0, npsh_required_m=20.0
            )
        ],
        "subsurface": SubsurfaceEvidence(
            well_spacing_m=1200.0,
            interference_bar=0.6,
            thermal_breakthrough_yr=33.9,
            interference_within_limit=True,
            breakthrough_after_horizon=True,
            horizon_yr=30.0,
        ),
        "doublet_points": [
            DoubletPointEvidence(
                name=f"doublet_{i}",
                flow_m3h=300.0,
                feasible=True,
                flow_error_frac=1e-12,
                intake_margin_bar=0.46,
            )
            for i in (1, 2)
        ],
        "scaling": ScalingEvidence(
            status="insufficient data",
            mitigations=[
                Mitigation.CALCITE_INHIBITOR,
                Mitigation.TITANIUM_PLATES,
                Mitigation.OXYGEN_EXCLUSION,
                Mitigation.NO_COPPER,
            ],
        ),
        "brine_contact_materials": {"production_casing": "carbon steel Sch 80"},
    }
    data.update(overrides)
    return VerificationEvidence.model_validate(data)
