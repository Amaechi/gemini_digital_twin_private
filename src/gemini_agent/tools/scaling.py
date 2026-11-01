"""TL-22 ``scaling_check``: calcite/gypsum scaling screen, CO₂-release margin and mitigations.

Source: RC-12 (Guidebook p.150–163; DiPippo §4.4, §6.6, Ch16, Ch23); spec §6 TL-22, ENG-09/10.

* Ryznar index ``RI = 2 pH_s − pH_a`` with ``pH_s = pCa + pALK + K_c(T)``; pCa = −log10[Ca²⁺,
  mol/L], pALK = −log10[alkalinity, eq/L]. Classes: < 4 extreme, 4–5.5 heavy, 5.5–6.2 slight,
  6.2–7 little or none, > 7 none (corrosive tendency).
* Gypsum relative saturation ``RS = 10^(log Ca + log SO₄ − log K_gyp)``; 0.8–1.2 ambiguous.
* CO₂-release pressure margin: lowest loop pressure minus the release (bubble) pressure.

``K_c(T)`` and ``log K_gyp(T)`` are tabulated in the Guidebook (p.160–163) but are NOT on RC-12, so
they are inputs that come with the brine analysis. The brine chemistry is not provided by the
organisers: without it the tool returns ``status = "insufficient data"``, the list of missing
analytes and the mitigations the design must carry (RC-12 tool behaviour) — the same set the
DesignVerifier requires in PD-07: titanium plates, O₂ exclusion, no copper alloys, plus a
calcite inhibitor dosed downhole when degassing or a pressurised loop when reinjecting gas.

Pass/fail rule (SCL-RULES): fail if RI < 5.5 (heavy/extreme), RS > 1.2 or a negative CO₂-release
margin on a pressurised loop; otherwise "insufficient data" while any input is missing; otherwise
fail if a required mitigation is missing from the design, else pass. Slight scaling (RI 5.5–6.2)
or ambiguous gypsum (RS 0.8–1.2) requires the calcite inhibitor.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import Field

from gemini_agent.schemas.common import (
    SUMMARY_MAX_CHARS,
    GasHandling,
    StrictModel,
    ToolResult,
)
from gemini_agent.verify.design_verifier import Mitigation, ScalingEvidence

TOOL_ID = "TL-22"
RI_EXTREME_BELOW = 4.0
RI_HEAVY_BELOW = 5.5
RI_SLIGHT_BELOW = 6.2
RI_LITTLE_MAX = 7.0
"""Ryznar classes (RC-12, Guidebook p.160–163)."""
RS_AMBIGUOUS_LOW = 0.8
RS_AMBIGUOUS_HIGH = 1.2
"""Gypsum relative saturation ambiguous band (RC-12)."""
ANALYTES: tuple[str, ...] = ("Ca", "alkalinity", "pH", "Cl", "SO4", "TDS")
"""Brine analytes RC-12 lists as missing (spec ENG-09)."""
MATERIAL_MITIGATIONS = frozenset(
    {Mitigation.TITANIUM_PLATES, Mitigation.OXYGEN_EXCLUSION, Mitigation.NO_COPPER}
)
"""Always required for this saline, CO₂/H₂S-bearing brine (RC-12 Corrosion and materials)."""

RyznarClass = Literal["extreme", "heavy", "slight", "little or none", "none (corrosive)"]


def saturation_ph(calcium_mol_per_l: float, alkalinity_eq_per_l: float, k_c: float) -> float:
    """``pH_s = pCa + pALK + K_c(T)`` (RC-12, Guidebook p.160–163)."""
    if calcium_mol_per_l <= 0 or alkalinity_eq_per_l <= 0:
        raise ValueError("calcium and alkalinity must be > 0")
    return -math.log10(calcium_mol_per_l) - math.log10(alkalinity_eq_per_l) + k_c


def ryznar_index(ph_saturation: float, ph_actual: float) -> float:
    """``RI = 2 pH_s − pH_a`` (RC-12)."""
    return 2.0 * ph_saturation - ph_actual


def classify_ryznar(ri: float) -> RyznarClass:
    """RC-12 classes: < 4 extreme, 4–5.5 heavy, 5.5–6.2 slight, 6.2–7 little/none, > 7 none."""
    if ri < RI_EXTREME_BELOW:
        return "extreme"
    if ri < RI_HEAVY_BELOW:
        return "heavy"
    if ri < RI_SLIGHT_BELOW:
        return "slight"
    if ri <= RI_LITTLE_MAX:
        return "little or none"
    return "none (corrosive)"


def gypsum_relative_saturation(
    calcium_mol_per_l: float, sulfate_mol_per_l: float, log_k_gypsum: float
) -> float:
    """``RS = 10^(log Ca + log SO₄ − log K_gyp)`` (RC-12)."""
    if calcium_mol_per_l <= 0 or sulfate_mol_per_l <= 0:
        raise ValueError("calcium and sulfate must be > 0")
    return 10.0 ** (math.log10(calcium_mol_per_l) + math.log10(sulfate_mol_per_l) - log_k_gypsum)


class BrineChemistry(StrictModel):
    """Brine analysis at the screened temperature; every field optional (missing ⇒ listed)."""

    temperature_degc: float
    ph_actual: float | None = Field(default=None, gt=0, lt=14)
    calcium_mol_per_l: float | None = Field(default=None, gt=0)
    alkalinity_eq_per_l: float | None = Field(default=None, gt=0)
    sulfate_mol_per_l: float | None = Field(default=None, gt=0)
    chloride_mg_per_l: float | None = Field(default=None, ge=0)
    tds_mg_per_l: float | None = Field(default=None, ge=0)
    k_c: float | None = Field(default=None, description="K_c(T), Guidebook p.160–163 table.")
    log_k_gypsum: float | None = Field(default=None, description="log10 K_gyp(T).")


class ScalingRequest(StrictModel):
    """Input of TL-22."""

    gas_handling: GasHandling
    chemistry: BrineChemistry | None = None
    design_mitigations: list[Mitigation] = Field(default_factory=list)
    co2_release_pressure_barg: float | None = Field(
        default=None, description="Bare release (bubble) pressure, TL-05 ``bubble_point``."
    )
    min_loop_pressure_barg: float | None = Field(
        default=None, description="Lowest brine pressure in the loop (pressurised variant)."
    )
    co2_corrosion_rate_mm_per_yr: float | None = Field(
        default=None, ge=0, description="e.g. from GEMINI CO2Corrosion; reported only."
    )


class ScalingReport(StrictModel):
    """Output of TL-22."""

    status: Literal["pass", "fail", "insufficient data"]
    missing_inputs: list[str]
    ryznar_index: float | None = None
    ryznar_class: RyznarClass | None = None
    ryznar_margin: float | None = Field(default=None, description="RI − 5.5; ≥ 0 passes.")
    gypsum_rs: float | None = None
    gypsum_margin: float | None = Field(default=None, description="1.2 − RS; ≥ 0 passes.")
    co2_release_margin_bar: float | None = None
    co2_corrosion_rate_mm_per_yr: float | None = None
    required_mitigations: list[Mitigation]
    missing_mitigations: list[Mitigation]
    recommendations: list[str]
    evidence: ScalingEvidence


def required_mitigations(gas_handling: GasHandling, *, inhibitor: bool = False) -> set[Mitigation]:
    """Return the mitigations RC-12 requires for this gas handling and scaling tendency.

    Materials always; calcite inhibitor when degassing or for slight scaling; pressurised loop
    when the gas is reinjected.
    """
    required = set(MATERIAL_MITIGATIONS)
    if gas_handling == GasHandling.DEGAS_USE or inhibitor:
        required.add(Mitigation.CALCITE_INHIBITOR)
    if gas_handling == GasHandling.PRESSURISED_REINJECT:
        required.add(Mitigation.PRESSURISED_LOOP)
    return required


_RECOMMENDATIONS = {
    Mitigation.TITANIUM_PLATES: "titanium HEX plates for saline brine (Guidebook p.219; RC-12)",
    Mitigation.OXYGEN_EXCLUSION: "closed, oxygen-free loop; > 50 ppb O2 pits steel (p.150–152)",
    Mitigation.NO_COPPER: "no Cu/CuNi in brine contact, incl. absorption generator tubes (p.152)",
    Mitigation.CALCITE_INHIBITOR: "dose calcite inhibitor downhole below the CO2-release depth "
    "(polyacrylate 0.5–2.5 ppm, typical 0.75; DiPippo Ch16)",
    Mitigation.PRESSURISED_LOOP: "keep pressure above the CO2-release point everywhere (Guidebook "
    "p.160, 234; DiPippo §4.4)",
}


def scaling_check(request: ScalingRequest) -> ToolResult[ScalingReport]:
    """TL-22: scaling/corrosion screen with margins, or "insufficient data" plus mitigations."""
    chem = request.chemistry
    missing: list[str] = []
    if chem is None:
        missing = list(ANALYTES) + ["K_c(T)", "log K_gyp(T)"]
    else:
        fields = {
            "Ca": chem.calcium_mol_per_l,
            "alkalinity": chem.alkalinity_eq_per_l,
            "pH": chem.ph_actual,
            "Cl": chem.chloride_mg_per_l,
            "SO4": chem.sulfate_mol_per_l,
            "TDS": chem.tds_mg_per_l,
            "K_c(T)": chem.k_c,
            "log K_gyp(T)": chem.log_k_gypsum,
        }
        missing = [name for name, value in fields.items() if value is None]
    pressurised = request.gas_handling == GasHandling.PRESSURISED_REINJECT
    if pressurised and (
        request.co2_release_pressure_barg is None or request.min_loop_pressure_barg is None
    ):
        missing.append("CO2-release and minimum loop pressure")

    ri = ri_class = ri_margin = rs = rs_margin = None
    inhibitor = False
    if chem is not None:
        ca, alk, ph, k_c = (
            chem.calcium_mol_per_l,
            chem.alkalinity_eq_per_l,
            chem.ph_actual,
            chem.k_c,
        )
        if ca is not None and alk is not None and ph is not None and k_c is not None:
            ri = ryznar_index(saturation_ph(ca, alk, k_c), ph)
            ri_class = classify_ryznar(ri)
            ri_margin = ri - RI_HEAVY_BELOW
            inhibitor = ri < RI_SLIGHT_BELOW
        so4, log_k_gyp = chem.sulfate_mol_per_l, chem.log_k_gypsum
        if ca is not None and so4 is not None and log_k_gyp is not None:
            rs = gypsum_relative_saturation(ca, so4, log_k_gyp)
            rs_margin = RS_AMBIGUOUS_HIGH - rs
            inhibitor = inhibitor or rs >= RS_AMBIGUOUS_LOW
    co2_margin = None
    if request.co2_release_pressure_barg is not None and request.min_loop_pressure_barg is not None:
        co2_margin = request.min_loop_pressure_barg - request.co2_release_pressure_barg

    required = required_mitigations(request.gas_handling, inhibitor=inhibitor)
    lacking = sorted(required - set(request.design_mitigations), key=lambda m: m.value)
    failures = []
    if ri_margin is not None and ri_margin < 0:
        failures.append(f"RI {ri:.2f} ({ri_class}) < {RI_HEAVY_BELOW}")
    if rs_margin is not None and rs_margin < 0:
        failures.append(f"gypsum RS {rs:.2f} > {RS_AMBIGUOUS_HIGH}")
    if pressurised and co2_margin is not None and co2_margin < 0:
        failures.append(f"loop {co2_margin:.1f} bar below the CO2-release pressure")
    lacking_text = f"mitigations missing from the design: {[m.value for m in lacking]}"

    # RC-12: without the analysis the status is "insufficient data" (the verifier's PD-07 then
    # fails a design that lacks the required mitigations); a computed failure always fails.
    if failures:
        status: Literal["pass", "fail", "insufficient data"] = "fail"
    elif missing:
        status = "insufficient data"
    elif lacking:
        status = "fail"
    else:
        status = "pass"
    parts = failures + ([lacking_text] if lacking else [])
    if missing:
        parts.append(f"MISSING INPUT: {', '.join(missing)}")
    detail = "; ".join(parts) or "RI/RS/CO2 margins >= 0, mitigations in the design"
    evidence = ScalingEvidence(
        status=status, mitigations=list(request.design_mitigations), detail=detail[:500]
    )
    report = ScalingReport(
        status=status,
        missing_inputs=missing,
        ryznar_index=ri,
        ryznar_class=ri_class,
        ryznar_margin=ri_margin,
        gypsum_rs=rs,
        gypsum_margin=rs_margin,
        co2_release_margin_bar=co2_margin,
        co2_corrosion_rate_mm_per_yr=request.co2_corrosion_rate_mm_per_yr,
        required_mitigations=sorted(required, key=lambda m: m.value),
        missing_mitigations=lacking,
        recommendations=[_RECOMMENDATIONS[m] for m in sorted(required, key=lambda m: m.value)],
        evidence=evidence,
    )
    warnings = []
    if missing:
        warnings.append(f"MISSING INPUT: {', '.join(missing)} (RC-12)")
    if ri_class == "none (corrosive)":
        warnings.append(f"RI {ri:.2f} > 7: corrosive tendency (materials, RC-12)")
    if rs is not None and RS_AMBIGUOUS_LOW <= rs <= RS_AMBIGUOUS_HIGH:
        warnings.append(f"gypsum RS {rs:.2f} in the ambiguous band 0.8–1.2")
    summary = f"scaling {status}: " + detail
    return ToolResult[ScalingReport](
        data=report,
        summary=summary[:SUMMARY_MAX_CHARS],
        warnings=warnings,
        assumption_ids=["SCL-RULES", "DB-36"],
        sources=["RC-12", "spec §6 TL-22"],
    )


__all__ = [
    "TOOL_ID",
    "BrineChemistry",
    "ScalingReport",
    "ScalingRequest",
    "classify_ryznar",
    "gypsum_relative_saturation",
    "required_mitigations",
    "ryznar_index",
    "saturation_ph",
    "scaling_check",
]
