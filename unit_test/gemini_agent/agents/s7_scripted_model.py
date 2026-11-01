"""Scripted coder for the S7 demo: a FunctionModel that writes FreeCooling from RC-07.

The files are what a coder model is expected to produce from the RC-07 "Free cooling
(water-side economiser)" section: full free cooling when T_wb + tower approach (4.5 K) + HEX
approach (2 K) <= the 20 °C supply, partial pre-cooling while it is below the 26 °C return, and
fan + pump power 2 % of the heat moved. ``broken=True`` drops the HEX approach (a typical
coding slip) so its reference-example test fails and the builder must feed the failure back.
No network connection is opened.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from unit_test.gemini_agent.llm_fakes import user_prompt

MODEL_TEMPLATE = '''"""Free cooling (water-side economiser) written by the Model Builder from RC-07.

Source: RC-07 "Free cooling (water-side economiser)": available when
T_wb + approach_tower + approach_HEX <= T_chilled,supply (T_wb <= 20 - 4.5 - 2 = 13.5 degC);
partial free cooling (pre-cooling the chiller return) while T_wb + 6.5 < T_return (26 degC);
power = fan + pumps, about 2 % of the heat moved [A].
"""

from gemini_model.model_abstract import StaticModel


class {class_name}(StaticModel):
    """Water-side economiser: full, partial (pre-cooling) or off (RC-07)."""

    def __init__(self):
        """Set the RC-07 defaults."""
        super().__init__()
        self.parameters = {{
            "tower_approach_k": 4.5,  # RC-07 [A] 4-5 K
            "hex_approach_k": {hex_approach_k},  # RC-07 [A]
            "aux_power_fraction": 0.02,  # RC-07 [A] fan + pumps, of the heat moved
            "chilled_water_temperature_out_degc": 20.0,  # supply (spec)
            "chilled_water_temperature_in_degc": 26.0,  # return (DB-32)
        }}
        self.output = {{}}

    def calculate_output(self, u, x=None):
        """Free cooling and residual chiller load.

        Inputs: ``ambient_wet_bulb_degc`` (degC) and ``cooling_load_w`` (W).
        """
        p = self.parameters
        t_wb = float(u["ambient_wet_bulb_degc"])
        load = float(u["cooling_load_w"])
        if load < 0.0:
            raise ValueError("cooling_load_w must be >= 0")
        t_supply = float(
            u.get("chilled_water_temperature_out_degc", p["chilled_water_temperature_out_degc"])
        )
        t_return = float(
            u.get("chilled_water_temperature_in_degc", p["chilled_water_temperature_in_degc"])
        )
        if t_return <= t_supply:
            raise ValueError("chilled-water return must be above the supply")
        t_fc = t_wb + p["tower_approach_k"] + p["hex_approach_k"]
        if t_fc <= t_supply:
            mode, fraction = "full", 1.0
        elif t_fc < t_return:
            mode, fraction = "partial", (t_return - t_fc) / (t_return - t_supply)
        else:
            mode, fraction = "off", 0.0
        q_fc = fraction * load
        power_el = p["aux_power_fraction"] * q_fc
        self.output = {{
            "mode": mode,
            "economiser_temperature_degc": t_fc,
            "cooling_load_w": load,
            "free_cooling_w": q_fc,
            "residual_cooling_w": load - q_fc,
            "free_cooling_fraction": fraction if load > 0.0 else 0.0,
            "chilled_water_temperature_out_degc": t_return - fraction * (t_return - t_supply),
            "heat_rejected_w": q_fc + power_el,
            "power_th": q_fc,
            "power_el": power_el,
            "emission": 0.0,
        }}
'''

TEST_TEMPLATE = '''"""Reference-example tests of {class_name} (RC-07 free cooling, REF-02)."""

import pytest

from gemini_model.{model_name}.{model_name} import {class_name}

LOAD_W = 4.0e6


def run(t_wb, load_w=LOAD_W):
    """Run the model once."""
    unit = {class_name}()
    unit.calculate_output({{"ambient_wet_bulb_degc": t_wb, "cooling_load_w": load_w}})
    return unit.get_output()


@pytest.mark.parametrize(
    "t_wb, mode", [(13.5, "full"), (13.6, "partial"), (19.4, "partial"), (19.5, "off")]
)
def test_rc07_availability_thresholds(t_wb, mode):
    """RC-07: full when T_wb + 4.5 + 2 <= 20 degC (T_wb <= 13.5); partial while < 26 degC."""
    assert run(t_wb)["mode"] == mode


def test_rc07_july_partial_precooling():
    """RC-07 table: July T_wb 15.4 degC -> economiser 21.9 degC, fraction (26 - 21.9) / 6."""
    out = run(15.4)
    assert out["economiser_temperature_degc"] == pytest.approx(21.9)
    assert out["free_cooling_fraction"] == pytest.approx((26.0 - 21.9) / 6.0)
    assert out["chilled_water_temperature_out_degc"] == pytest.approx(21.9)


def test_rc07_power_is_two_percent_of_heat_moved():
    """RC-07: fan + pumps about 2 % of the heat moved; no emission."""
    out = run(5.0)
    assert out["power_th"] == pytest.approx(LOAD_W)
    assert out["power_el"] == pytest.approx(0.02 * LOAD_W)
    assert out["heat_rejected_w"] == pytest.approx(1.02 * LOAD_W)
    assert out["emission"] == 0.0


def test_negative_load_is_refused():
    """Invalid input raises."""
    with pytest.raises(ValueError):
        run(5.0, -1.0)
'''


def card_section(card_text: str, heading: str) -> str:
    """One '## heading' section of a reference card."""
    lines, keep = [], False
    for line in card_text.splitlines():
        if line.startswith("## "):
            keep = line[3:].strip().startswith(heading)
        if keep:
            lines.append(line)
    return "\n".join(lines)


def scripted_draft(request: dict[str, Any], *, broken: bool = False) -> dict[str, Any]:
    """Return the draft for a build request (REFERENCE.md copied from the card it was given)."""
    names = {"class_name": request["class_name"], "model_name": request["model_name"]}
    reference = (
        f"# {request['reference_card']} — {request['component_type']} (copied from the card)\n\n"
        + card_section(request["card_text"], "Free cooling")
        + "\n"
    )
    return {
        "class_name": request["class_name"],
        "model_code": MODEL_TEMPLATE.format(hex_approach_k=0.0 if broken else 2.0, **names),
        "test_code": TEST_TEMPLATE.format(**names),
        "reference_md": reference,
        "notes": "Equations and defaults from RC-07 'Free cooling (water-side economiser)'.",
    }


def build_request(messages: list[Any]) -> dict[str, Any]:
    """Return the build request JSON the Model Builder put in the prompt."""
    return json.loads(user_prompt(messages).split("BUILD REQUEST:\n", 1)[1])


class ScriptedCoder:
    """FunctionModel callable: a broken draft for the first ``broken_drafts`` requests."""

    def __init__(self, broken_drafts: int = 0) -> None:
        """Count calls; the first ``broken_drafts`` drafts fail their own tests."""
        self.broken_drafts = broken_drafts
        self.calls = 0
        self.requests: list[dict[str, Any]] = []

    def __call__(self, messages: list[Any], info: AgentInfo) -> ModelResponse:
        """Return the scripted draft as the structured output."""
        self.calls += 1
        request = build_request(messages)
        self.requests.append(request)
        draft = scripted_draft(request, broken=self.calls <= self.broken_drafts)
        return ModelResponse(parts=[TextPart(json.dumps(draft))])


def scripted_model(coder: ScriptedCoder) -> FunctionModel:
    """Wrap a scripted coder in a FunctionModel."""
    return FunctionModel(coder, model_name="scripted-coder")
