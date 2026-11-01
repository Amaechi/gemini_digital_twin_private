"""Unit tests for gemini_agent.tools.model_builder_tools (TL-17, TL-18) — offline, sandboxed.

Every write goes to pytest's ``tmp_path``; registrations go to registry copies (the live
``COMPONENT_REGISTRY`` and the repository are never modified).
"""

import os

import pytest

from gemini_agent.runner.toolbox import ToolArgsError, ToolContext, default_toolbox
from gemini_agent.schemas.config import REPO_ROOT
from gemini_agent.tools import model_builder_tools as mbt
from gemini_agent.tools.model_builder_tools import (
    KNOWN_LIMITATIONS,
    ModelBuilderError,
    ModelSandbox,
    check_sandbox_root,
    model_file_paths,
    model_gap_analysis,
    read_reference_card,
    register_model,
    run_model_tests,
    screen_python,
)
from gemini_simulator import registry as registry_module
from gemini_simulator.registry import COMPONENT_REGISTRY
from unit_test.gemini_agent.agents import s7_scripted_model as s7

NAME = "free_cooling_rc07"
CLASS = "FreeCoolingRc07"
REFERENCE = "# RC-07 free cooling\n\nT_wb + 4.5 + 2 <= 20 degC.\n"


def _model(hex_approach_k: float = 2.0, class_name: str = CLASS) -> str:
    return s7.MODEL_TEMPLATE.format(
        class_name=class_name, model_name=NAME, hex_approach_k=hex_approach_k
    )


def _test(class_name: str = CLASS) -> str:
    return s7.TEST_TEMPLATE.format(class_name=class_name, model_name=NAME)


def _write_model(sandbox: ModelSandbox, model: str | None = None, test: str | None = None) -> None:
    paths = model_file_paths(NAME)
    sandbox.write(paths["model_init"], '"""Sandbox model."""\n')
    sandbox.write(paths["model"], model if model is not None else _model())
    sandbox.write(paths["reference"], REFERENCE)
    sandbox.write(paths["test_init"], "")
    sandbox.write(paths["test"], test if test is not None else _test())


@pytest.fixture()
def sandbox(tmp_path):
    """Empty sandbox under tmp_path."""
    return ModelSandbox(tmp_path / "sandbox")


@pytest.fixture()
def no_free_cooling():
    """Registry COPY without free_cooling (S7 starting point)."""
    return {k: v for k, v in COMPONENT_REGISTRY.items() if k != "free_cooling"}


# --------------------------------------------------------------------------- TL-17
def test_gap_analysis_live_registry_has_no_missing_types():
    """All types the C0–C8 layouts use are registered; unused ones and limitations are listed."""
    result = model_gap_analysis()
    report = result.data
    assert report.missing_component_types == [] and report.blocked_candidates == []
    required = {r.component_type: r for r in report.required}
    assert {"free_cooling", "heatpump", "absorption_chiller", "thermal_storage"} <= set(required)
    assert required["free_cooling"].needed_by == ["C2", "C3", "C4", "C5", "C6", "C7", "C8"]
    assert required["thermal_storage"].needed_by == ["C6"]
    assert not required["thermal_storage"].in_generated_plant  # LIM-PLT-01
    assert required["free_cooling"].in_generated_plant
    assert required["separator"].needed_by == ["C1", "C2", "C3", "C4", "C5", "C6", "C8"]
    assert required["heatpump"].model_class.endswith("HeatpumpBasic")
    assert "C0" in required["boiler"].needed_by and "C0" not in required["producer_well"].needed_by
    assert {"compressor", "co2_corrosion", "esp"} <= set(report.unused_registered_types)
    assert report.internal_api[0].present
    assert len(result.summary) <= 300 and "missing: none" in result.summary


def test_known_limitations_become_gap_entries():
    """Every PROGRESS WP-12 limitation is an entry with the candidates it affects."""
    report = model_gap_analysis().data
    entries = {lim.id: lim for lim in report.limitations}
    assert set(entries) == {lim.id for lim in KNOWN_LIMITATIONS} and len(entries) == 15
    assert {lim.area for lim in report.limitations} == {"subsurface", "plant", "gemini", "tooling"}
    assert entries["LIM-SUB-06"].affects == ["C4"]  # flashing > 100 °C: deep concept only
    assert entries["LIM-PLT-01"].affects == ["C6"]  # no storage component
    assert entries["LIM-PLT-05"].affects == ["C3", "C4"]  # absorption isolation loop
    assert entries["LIM-PLT-02"].affects == ["C2", "C3", "C5", "C6", "C7", "C8"]  # HP fouling
    assert entries["LIM-GEM-01"].affects == []  # Compressor is unused
    assert entries["LIM-SUB-08"].affects == []  # standalone 'esp' type is unused
    assert "C0" not in entries["LIM-SUB-01"].affects
    titles = " ".join(lim.title + lim.detail for lim in KNOWN_LIMITATIONS)
    for item in ("affinity", "PVT", "U = 10", "0.4 K", "fracture", "flashing", "Q/(Q_BEP"):
        assert item in titles, item
    for item in ("water-only", "storage", "fouling", "C_r", "abs(", "isolation", "Compressor"):
        assert item in titles, item


def test_removed_free_cooling_is_detected_as_buildable_gap(no_free_cooling):
    """S7 detection: free_cooling missing → RC-07, GM-07, a NEW folder name, C0/C1 unaffected."""
    result = model_gap_analysis(registry=no_free_cooling)
    (gap,) = result.data.missing
    assert gap.component_type == "free_cooling" and gap.reference_card == "RC-07"
    assert gap.gm_id == "GM-07" and gap.buildable
    assert gap.suggested_model_name == NAME
    assert not (REPO_ROOT / "src" / "gemini_model" / NAME).exists()
    assert result.data.blocked_candidates == ["C2", "C3", "C4", "C5", "C6", "C7", "C8"]
    assert any("free_cooling" in w for w in result.warnings)


def test_gap_without_reference_card_is_not_buildable():
    """A GEMINI-native type without a card cannot be rebuilt: escalate."""
    registry = {k: v for k, v in COMPONENT_REGISTRY.items() if k != "mixer"}
    (gap,) = model_gap_analysis(registry=registry).data.missing
    assert gap.component_type == "mixer" and not gap.buildable and "escalate" in gap.action
    assert "C0" not in gap.needed_by  # C0 has no doublets, hence no brine mixer


def test_gap_analysis_uses_candidate_ids(tmp_path):
    """With candidates the needs are listed per candidate id (WP-06 concept layouts)."""
    toolbox = default_toolbox()
    context = ToolContext(run_id="t", run_dir=tmp_path, config_dir=None)
    candidates = toolbox.execute("generate_candidates", {}, context).data.candidates
    report = model_gap_analysis(candidates).data
    by_type = {r.component_type: r.needed_by for r in report.required}
    by_id = {c.id: c.concept for c in candidates}
    assert {by_id[cid] for cid in by_type["absorption_chiller"]} == {"C3", "C4"}
    c7 = next(c.id for c in candidates if c.concept == "C7")
    assert c7 not in by_type["separator"]  # pressurised loop, no degassing


def test_required_types_match_the_generated_projects(tmp_path):
    """TL-17 needs = the types in WP-06 generate_plant projects (C0–C8 at 1–3 doublets)."""
    from gemini_agent.tools.config_generator import (
        PlantDispatch,
        design_loads,
        generate_plant,
        load_generator_basis,
    )

    toolbox = default_toolbox()
    context = ToolContext(run_id="t", run_dir=tmp_path, config_dir=None)
    candidates = toolbox.execute("generate_candidates", {}, context).data.candidates
    loads = design_loads(load_generator_basis())
    for candidate in candidates:
        for doublets in (1, 2, 3) if candidate.doublets else (0,):
            variant = candidate.model_copy(update={"doublets": doublets})
            project = generate_plant(variant, loads, PlantDispatch()).project
            built = {spec["type"] for spec in project["components"].values()}
            predicted = set(mbt.candidate_component_types(variant, conceptual=False))
            assert predicted == built, (variant.concept, doublets)
            conceptual = set(mbt.candidate_component_types(variant)) - predicted
            assert conceptual == ({"thermal_storage"} if variant.concept == "C6" else set())


# --------------------------------------------------------------------------- read_reference_card
def test_read_reference_card_returns_the_card():
    """RC-07 text, sections and digest; summary ≤ 300 characters."""
    result = read_reference_card("RC-07")
    card = result.data
    assert card.card_id == "RC-07" and card.path.startswith("data/knowledge/reference_cards/")
    assert "Free cooling (water-side economiser)" in card.sections
    assert "GM-07 FreeCooling" in card.feeds and "T_wb ≤ 20 − 4.5 − 2" in card.text
    assert len(card.sha256) == 64 and len(result.summary) <= 300


@pytest.mark.parametrize("card_id", ["RC-99", "RC-7", "../RC-07", "RC-07_heat"])
def test_read_reference_card_refuses_unknown_ids(card_id):
    """Only existing RC-xx ids (no paths)."""
    with pytest.raises(ModelBuilderError):
        read_reference_card(card_id)


# --------------------------------------------------------------------------- sandbox writes
def test_sandbox_writes_only_the_five_model_files(sandbox):
    """The five allowed files land in the sandbox, mirroring the repository layout."""
    _write_model(sandbox)
    written = sorted(
        str(p.relative_to(sandbox.root)) for p in sandbox.root.rglob("*") if p.is_file()
    )
    assert written == sorted(model_file_paths(NAME).values())
    assert not (REPO_ROOT / "src" / "gemini_model" / NAME).exists()


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/etc/passwd",
        "../src/gemini_model/free_cooling_rc07/free_cooling_rc07.py",
        "src/gemini_model/free_cooling_rc07/../../../gemini_simulator/registry.py",
        "src/gemini_model/free_cooling_rc07/./free_cooling_rc07.py",
        "src//gemini_model/free_cooling_rc07/free_cooling_rc07.py",
        "src\\gemini_model\\free_cooling_rc07\\free_cooling_rc07.py",
        "C:/src/gemini_model/free_cooling_rc07/free_cooling_rc07.py",
        "src/gemini_simulator/registry.py",
        "src/gemini_model/free_cooling/free_cooling.py",  # existing WP-02 model
        "src/gemini_model/heat_exchanger/REFERENCE.md",  # existing GEMINI model
        "src/gemini_model/free_cooling_rc07/helpers.py",  # not an allowed file
        "src/gemini_model/free_cooling_rc07/other_name.py",
        "unit_test/gemini_model/free_cooling_rc07/conftest.py",  # would inject code
        "unit_test/gemini_model/free_cooling_rc07/test_other.py",
        "src/gemini_model/__init__.py",
        "src/gemini_model/FreeCooling/FreeCooling.py",
        "src/gemini_model/class/class.py",  # keyword
        "src/gemini_model/test_x/test_x.py",  # reserved prefix
        "src/gemini_model/model_abstract/model_abstract.py",  # clashes with model_abstract.py
        "data/knowledge/reference_cards/RC-07_x.md",
    ],
)
def test_path_traversal_and_foreign_paths_are_refused(sandbox, path):
    """Only src/gemini_model/<new>/… and unit_test/gemini_model/<new>/… (five files)."""
    with pytest.raises(ModelBuilderError):
        sandbox.write(path, '"""x."""\n')
    assert not any(p.is_file() for p in sandbox.root.rglob("*"))


def test_symlinks_inside_the_sandbox_are_refused(tmp_path, sandbox):
    """A symlinked folder cannot redirect a write outside the sandbox."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (sandbox.root / "src").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ModelBuilderError, match="symlink"):
        sandbox.write(model_file_paths(NAME)["model"], _model())
    assert not any(outside.rglob("*"))


def test_symlinked_state_dir_is_refused(tmp_path, sandbox):
    """Reports are never read from or written through a symlink."""
    _write_model(sandbox)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (sandbox.root / mbt.STATE_DIR_NAME).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ModelBuilderError, match="symlink"):
        run_model_tests(sandbox, NAME)
    assert not any(outside.rglob("*"))


def test_sandbox_root_must_not_be_in_the_tracked_tree(tmp_path):
    """Repository root and its folders are refused; runs/ (git-ignored) and outside are fine."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    for bad in (repo, repo / "src", repo / "unit_test" / "x", repo / "data"):
        with pytest.raises(ModelBuilderError, match="outside the repository"):
            check_sandbox_root(bad, repo)
    for bad in (repo / "runs", repo / "runs" / "final" / "model_builder"):  # runs/final tracked
        with pytest.raises(ModelBuilderError, match="runs/final"):
            check_sandbox_root(bad, repo)
    assert check_sandbox_root(repo / "runs" / "r1" / "model_builder", repo).is_relative_to(repo)
    assert check_sandbox_root(tmp_path / "elsewhere", repo) == (tmp_path / "elsewhere").resolve()
    with pytest.raises(ModelBuilderError):
        ModelSandbox(REPO_ROOT)


@pytest.mark.parametrize(
    "source, kind, message",
    [
        ("import os\n", "model", "import of 'os'"),
        ("from subprocess import run\n", "model", "subprocess"),
        ("import socket\n", "test", "socket"),
        ("import pytest\n", "model", "pytest"),  # tests only
        ("x = open('f')\n", "model", "'open'"),
        ("x = eval('1')\n", "test", "'eval'"),
        ("x = __import__('os')\n", "model", "__import__"),
        ("x = ().__class__.__bases__\n", "model", "__bases__"),
        ("from .helpers import x\n", "model", "relative import"),
        ("x = __builtins__['open']\n", "model", "__builtins__"),
        ("x = getattr(int, 'real')\n", "model", "'getattr'"),
        ("setattr(object, 'x', 1)\n", "model", "'setattr'"),
        ("import typing\nm = typing.sys.modules\n", "model", "'(sys|modules)'"),
        ("import dataclasses\nx = dataclasses.sys\n", "model", "'sys'"),
        ("import numpy\nnumpy.savetxt('/tmp/x', [1])\n", "model", "'savetxt'"),
        ("import numpy as np\nnp.fromfile('/etc/passwd')\n", "model", "'fromfile'"),
        ("from numpy import save\n", "model", "'save'"),
        ("from numpy import *\n", "model", "star import"),
        ("import scipy.io\n", "model", "scipy.io"),
        ("from scipy import io\n", "model", "'(scipy.)?io'"),
        ("import numpy.ctypeslib\n", "model", "numpy.ctypeslib"),
        ("x = type.__subclasses__\n", "model", "__subclasses__"),
        ("x = (1).__class__\n", "model", "__class__"),
        ("x = '{0.__class__}'.format(1)\n", "model", "'__'"),
        ("import pytest\nx = pytest.importorskip('os')\n", "test", "importorskip"),
        ("import pytest\nx = pytest.MonkeyPatch\n", "test", "MonkeyPatch"),
        ("x = __file__\n", "model", "__file__"),
        ("def f(:\n", "model", "syntax error"),
    ],
)
def test_screen_python_refuses_unsafe_code(source, kind, message):
    """Import allow-list and banned builtins (defence in depth)."""
    with pytest.raises(ModelBuilderError, match=message):
        screen_python(source, kind)


def test_screen_python_accepts_model_and_test_templates():
    """The allow-list covers a normal GEMINI model and pytest test."""
    screen_python(_model(), "model")
    screen_python(_test(), "test")
    screen_python("from .free_cooling_rc07 import FreeCoolingRc07\n", "init")
    screen_python('"""Doc with __init__ in it."""\nif __name__ == "m":\n    pass\n', "model")


def test_write_refuses_oversized_and_unsafe_files(sandbox):
    """Size limit and screening apply before anything is written."""
    with pytest.raises(ModelBuilderError, match="too large"):
        sandbox.write(model_file_paths(NAME)["reference"], "x" * (mbt.MAX_FILE_BYTES + 1))
    with pytest.raises(ModelBuilderError, match="import of 'os'"):
        sandbox.write(model_file_paths(NAME)["model"], "import os\n")
    assert not any(p.is_file() for p in sandbox.root.rglob("*"))


# --------------------------------------------------------------------------- run_pytest
def test_run_pytest_passes_reference_example_tests(sandbox):
    """Subprocess pytest on the sandbox model; report stored with the file digest."""
    _write_model(sandbox)
    report = run_model_tests(sandbox, NAME, timeout_s=120)
    assert report.passed and report.tests == 7 and report.failures == 0 and not report.timed_out
    assert report.files_sha256 == sandbox.files_digest(NAME)
    assert sandbox.load_report(NAME) == report
    assert not list(sandbox.root.rglob("__pycache__"))


def test_run_pytest_reports_failures(sandbox):
    """A wrong HEX approach fails the RC-07 threshold tests; failed tests are named."""
    _write_model(sandbox, model=_model(hex_approach_k=0.0))
    report = run_model_tests(sandbox, NAME, timeout_s=120)
    assert not report.passed and report.failures == 3 and report.returncode == 1
    assert any("13.6-partial" in name for name in report.failed_tests)
    assert "assert" in report.output_tail


def test_run_pytest_times_out(sandbox):
    """A hanging test is killed at the timeout and reported."""
    _write_model(sandbox, test="def test_hangs():\n    while True:\n        pass\n")
    report = run_model_tests(sandbox, NAME, timeout_s=2)
    assert report.timed_out and not report.passed and report.returncode is None
    assert "timed out" in report.error and report.duration_s < 30


def test_run_pytest_fails_if_tests_edit_the_model(sandbox):
    """Files changed during the run (digest before ≠ after) never pass."""
    _write_model(sandbox)
    tamper = (
        "from pathlib import Path\n\n\ndef test_tamper():\n"
        f"    path = Path('{model_file_paths(NAME)['model']}')\n"
        "    path.write_text(path.read_text() + '# edited')\n"
    )
    (sandbox.root / model_file_paths(NAME)["test"]).write_text(tamper)  # bypasses the screen
    report = run_model_tests(sandbox, NAME)
    assert not report.passed and "changed during" in report.error


def test_run_pytest_needs_files_and_tests(sandbox):
    """Missing files, or a test file without tests, never pass (REF-02)."""
    report = run_model_tests(sandbox, NAME)
    assert not report.passed and "missing files" in report.error
    _write_model(sandbox, test='"""No tests."""\n')
    report = run_model_tests(sandbox, NAME)
    assert not report.passed and "no test ran" in report.error


# --------------------------------------------------------------------------- register_model
def test_register_only_after_a_passing_run_on_unchanged_files(sandbox, no_free_cooling):
    """No run / failed run / edited after the run → refused; then registered at runtime."""
    _write_model(sandbox, model=_model(hex_approach_k=0.0))
    with pytest.raises(ModelBuilderError, match="has not run"):
        register_model(sandbox, NAME, component_type="free_cooling", registry=no_free_cooling)
    run_model_tests(sandbox, NAME)
    with pytest.raises(ModelBuilderError, match="did not pass"):
        register_model(sandbox, NAME, component_type="free_cooling", registry=no_free_cooling)
    sandbox.write(model_file_paths(NAME)["model"], _model())
    run_model_tests(sandbox, NAME)
    sandbox.write(model_file_paths(NAME)["reference"], REFERENCE + "edited\n")
    with pytest.raises(ModelBuilderError, match="changed after"):
        register_model(sandbox, NAME, component_type="free_cooling", registry=no_free_cooling)
    sandbox.write(model_file_paths(NAME)["reference"], REFERENCE)
    record = register_model(sandbox, NAME, component_type="free_cooling", registry=no_free_cooling)
    cls = no_free_cooling["free_cooling"]
    assert cls.__name__ == CLASS and record.tests_passed == 7 and record.runtime_only
    assert "reviewed PR" in record.promotion
    unit = cls()
    unit.calculate_output({"ambient_wet_bulb_degc": 15.4, "cooling_load_w": 4.0e6})
    assert unit.get_output()["mode"] == "partial"
    assert COMPONENT_REGISTRY["free_cooling"].__name__ == "FreeCooling"  # live one untouched
    assert not list(sandbox.root.rglob("__pycache__"))


def test_register_refuses_existing_types_and_contract_breaks(sandbox):
    """Never replaces a registered type; REF-01 card citation and outputs are required."""
    _write_model(sandbox)
    run_model_tests(sandbox, NAME)
    registry = dict(COMPONENT_REGISTRY)
    with pytest.raises(ModelBuilderError, match="already registered"):
        register_model(sandbox, NAME, component_type="free_cooling", registry=registry)
    sandbox.write(model_file_paths(NAME)["reference"], "# no card id\n")
    run_model_tests(sandbox, NAME)
    with pytest.raises(ModelBuilderError, match="REF-01"):
        register_model(sandbox, NAME, registry=registry)
    sandbox.write(model_file_paths(NAME)["reference"], REFERENCE)
    run_model_tests(sandbox, NAME)
    with pytest.raises(ModelBuilderError, match="exactly one"):
        register_model(sandbox, NAME, class_name="Missing", registry=registry)
    assert NAME not in registry and registry == COMPONENT_REGISTRY


def test_register_requires_power_and_emission_outputs(sandbox):
    """A model without power_th/power_el/emission outputs is not registered (spec §7.2)."""
    model = _model().replace('"emission": 0.0,', "")
    _write_model(sandbox, model=model, test="def test_ok():\n    assert True\n")
    assert run_model_tests(sandbox, NAME).passed
    registry: dict = {}
    with pytest.raises(ModelBuilderError, match="emission"):
        register_model(sandbox, NAME, registry=registry)
    assert registry == {}


# --------------------------------------------------------------------------- runner bindings
def test_default_toolbox_binds_the_real_wp12_tools(tmp_path, monkeypatch):
    """The five Model Builder tools are real (stub=False) and sandboxed in <run_dir>."""
    toolbox = default_toolbox()
    for name in ("model_gap_analysis", "read_reference_card", "write_model_file"):
        assert not toolbox.spec(name).stub and toolbox.spec(name).owner == "WP-12"
    for name in ("run_pytest", "register_model"):
        assert not toolbox.spec(name).stub
    context = ToolContext(run_id="t", run_dir=tmp_path / "run", config_dir=None)
    gaps = toolbox.execute("model_gap_analysis", {}, context)
    assert gaps.data.missing_component_types == [] and len(gaps.data.limitations) == 15
    card = toolbox.execute("read_reference_card", {"card_id": "RC-07"}, context)
    assert card.data.card_id == "RC-07"
    for kind, path in model_file_paths(NAME).items():
        content = {"model": _model(), "test": _test(), "reference": REFERENCE}.get(kind, "")
        toolbox.execute("write_model_file", {"path": path, "content": content}, context)
    assert (tmp_path / "run" / "model_builder" / model_file_paths(NAME)["model"]).is_file()
    with pytest.raises(ModelBuilderError):
        toolbox.execute("write_model_file", {"path": "../escape.py", "content": ""}, context)
    with pytest.raises(ToolArgsError):
        toolbox.execute("run_pytest", {"model_name": "../x"}, context)
    tested = toolbox.execute("run_pytest", {"model_name": NAME, "timeout_s": 120}, context)
    assert tested.data.passed and "passed" in tested.summary
    copy = dict(COMPONENT_REGISTRY)
    monkeypatch.setattr(registry_module, "COMPONENT_REGISTRY", copy)
    result = toolbox.execute("register_model", {"model_name": NAME}, context)
    assert copy[NAME].__name__ == CLASS and len(result.summary) <= 300
    assert NAME not in COMPONENT_REGISTRY


def test_no_test_leaves_files_in_the_repository():
    """Sandbox work stays out of src/ and unit_test/ (S7 uses a NEW folder name)."""
    for area in ("src", "unit_test"):
        assert not (REPO_ROOT / area / "gemini_model" / NAME).exists()
    assert "free_cooling_rc07" not in COMPONENT_REGISTRY
    assert os.path.isdir(REPO_ROOT / "src" / "gemini_model" / "free_cooling")
