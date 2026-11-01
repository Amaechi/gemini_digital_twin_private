"""Evidence bundles for DesignVerifier tests: one design that passes every check."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from gemini_agent.schemas.candidate import Candidate
from gemini_agent.schemas.results import AnnualResult, DayResult, KPISet
from gemini_agent.verify.design_verifier import (
    AbsorptionPoint,
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


def project() -> dict[str, Any]:
    """Return the GEMINI doublet example plant: one well pair, HEX, boiler and CHP."""
    return json.loads(PROJECT_PATH.read_text(encoding="utf-8"))


def candidate(candidate_id: str = CANDIDATE_ID, **overrides: Any) -> Candidate:
    """Two-doublet C2 candidate at the design-basis depth."""
    data: dict[str, Any] = {
        "id": candidate_id,
        "concept": "C2",
        "doublets": 2,
        "depth_m": 2500.0,
        "design_flow_m3h": 300.0,
        "gas_handling": "pressurised_reinject",
        "heating_return_degc": 55.0,
        "cooling_return_degc": 26.0,
        "equipment_sizes": {"hthp_heating_mw": 6.0, "absorption_cooling_mw": 3.0},
    }
    data.update(overrides)
    return Candidate.model_validate(data)


def day(index: int, **overrides: Any) -> DayResult:
    """One balanced day: doublet 2 on planned outage on day 0, both producing on day 1."""
    base: dict[str, Any] = {
        "day_index": index,
        "date": dt.date(2027, 1, 1) + dt.timedelta(days=index),
        "ambient_dry_bulb_degc": 5.0,
        "heating_demand_mw": 20.0,
        "cooling_demand_mw": 2.0,
        "doublet_available": [True, False] if index == 0 else [True, True],
        "doublet_flow_m3h": [300.0, 0.0] if index == 0 else [250.0, 200.0],
        "injection_flow_m3h": [300.0, 0.0] if index == 0 else [250.0, 200.0],
        "production_temperature_degc": [86.0, 86.0],
        "injection_temperature_degc": [46.0, 46.0],
        "heating_supply_degc": 100.0,
        "heating_return_degc": 55.0,
        "cooling_supply_degc": 20.0,
        "cooling_return_degc": 26.0,
        "heat_by_source_mw": {"geothermal_hex": 14.0, "hthp": 6.0},
        "cool_by_source_mw": {"absorption": 2.0},
        "electricity_by_consumer_mw": {"esp": 0.4, "injection_pump": 0.3, "hthp": 1.5},
        "margins": {"hex_approach_k": 3.0},
    }
    base.update(overrides)
    return DayResult.model_validate(base)


def annual(days: list[DayResult] | None = None, candidate_id: str = CANDIDATE_ID) -> AnnualResult:
    """Two-day full-mode result."""
    return AnnualResult(
        candidate_id=candidate_id, year=2027, interpretation="A", days=days or [day(0), day(1)]
    )


def good_evidence(candidate_id: str = CANDIDATE_ID, **overrides: Any) -> VerificationEvidence:
    """Evidence on which every check passes (verdict ACCEPTED)."""
    fast_days = [day(i, heat_by_source_mw={"geothermal_hex": 13.9, "hthp": 6.0}) for i in range(2)]
    data: dict[str, Any] = {
        "candidate": candidate(candidate_id),
        "project": project(),
        "annual": annual(candidate_id=candidate_id),
        "fast_annual": annual(fast_days, candidate_id),
        "kpis": KPISet(
            candidate_id=candidate_id,
            thermal_breakthrough_yr=33.9,
            exergy_efficiency_frac=0.42,
            exergy_dead_state="daily ambient dry-bulb (DB-34d)",
        ),
        "hex_points": [
            HexPoint(
                name="HEX-1",
                hot_in_degc=86.0,
                hot_out_degc=58.0,
                cold_in_degc=55.0,
                cold_out_degc=82.0,
                design_pressure_bar=16.0,
                ntu=3.0,
                pressure_drop_pa=72_000.0,
                flow_m3h=300.0,
                area_m2=400.0,
                plate_material="titanium Gr.1",
            )
        ],
        "heat_pumps": [
            HeatPumpPoint(name="HTHP", cop=4.0, sink_degc=101.0, source_degc=44.0, max_lift_k=60.0)
        ],
        "absorption": [
            AbsorptionPoint(
                name="ABS-1",
                cop=0.65,
                cop_min=0.55,
                cop_max=0.75,
                cooling_mw=2.0,
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
                name="ESP-1",
                intake_pressure_bar=25.0,
                release_pressure_bar=20.0,
                npsh_available_m=60.0,
                npsh_required_m=20.0,
            )
        ],
        "subsurface": SubsurfaceEvidence(
            well_spacing_m=1200.0, interference_bar=0.6, thermal_breakthrough_yr=33.9
        ),
        "scaling": ScalingEvidence(
            status="insufficient data",
            mitigations=[
                Mitigation.PRESSURISED_LOOP,
                Mitigation.TITANIUM_PLATES,
                Mitigation.OXYGEN_EXCLUSION,
                Mitigation.NO_COPPER,
            ],
        ),
        "brine_contact_materials": {"production_casing": "carbon steel Sch 80"},
    }
    data.update(overrides)
    return VerificationEvidence.model_validate(data)
