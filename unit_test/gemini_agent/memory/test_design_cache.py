"""Unit tests for gemini_agent.memory.design_cache (spec §10.2 design tier)."""

import json
from pathlib import Path

import numpy as np
import pytest

from gemini_agent.memory.design_cache import (
    CACHE_FILE,
    DesignCache,
    canonical_json,
    config_hash,
)
from gemini_agent.schemas.results import KPISet

REPO = Path(__file__).resolve().parents[3]
PROJECT = json.loads(
    (REPO / "simulator_examples" / "projects" / "doublet_plant.json").read_text(encoding="utf-8")
)


def test_hash_is_canonical():
    """Key order, comments, int/float spelling and -0.0 do not change the hash."""
    a = {"b": 2, "a": [1, 0.0], "_comment": "x"}
    b = {"a": [1.0, -0.0], "b": 2.0}
    assert canonical_json(a) == canonical_json(b) == '{"a":[1.0,0.0],"b":2.0}'
    assert config_hash(a) == config_hash(b)
    assert config_hash(a, namespace="simulate_annual:fast:v1") != config_hash(
        a, namespace="simulate_annual:full:v1"
    )
    assert config_hash({"x": np.float64(1.5), "y": np.array([1, 2])}) == config_hash(
        {"x": 1.5, "y": [1, 2]}
    )
    assert config_hash({"id": "C1", "q": 1}, ignore=["id"]) == config_hash(
        {"id": "C2", "q": 1}, ignore=["id"]
    )


def test_hash_changes_with_physics_and_refuses_nan():
    """A changed parameter is a different design; NaN cannot be hashed."""
    changed = json.loads(json.dumps(PROJECT))
    changed["components"]["secondary_splitter"]["parameters"]["split_fractions"] = [0.7, 0.3]
    assert config_hash(PROJECT) != config_hash(changed)
    commented = {**PROJECT, "_note": "a comment only"}
    assert config_hash(PROJECT) == config_hash(commented)
    with pytest.raises(ValueError, match="non-finite"):
        canonical_json({"q": float("nan")})
    with pytest.raises(TypeError):
        canonical_json({"q": object()})


def test_put_get_and_relabel(tmp_path):
    """Entries persist across instances; hits count; KPIs are relabelled for the requester."""
    cache = DesignCache(tmp_path)
    assert cache.path == tmp_path / CACHE_FILE
    kpis = KPISet(candidate_id="C2-2d", lcoh_per_mwh=55.0, unmet_heating_mwh=0.0)
    entry = cache.put(PROJECT, namespace="simulate_annual:full:v1", kpis=kpis, run_id="r1")
    assert entry.candidate_id == "C2-2d" and entry.source_run == "r1"
    cache.close()

    cache = DesignCache(tmp_path / CACHE_FILE)
    key = cache.key(PROJECT, "simulate_annual:full:v1")
    assert key == entry.config_hash
    assert cache.get(cache.key(PROJECT, "simulate_annual:fast:v1")) is None
    hit = cache.get(key, candidate_id="C2-2d-copy")
    assert hit is not None and hit.hits == 1
    assert hit.kpis is not None and hit.kpis.candidate_id == "C2-2d-copy"
    assert hit.kpis.lcoh_per_mwh == 55.0
    assert cache.stats() == {"entries": 1, "hits": 1}
    assert [e.config_hash for e in cache.entries("simulate_annual:full:v1")] == [key]
    assert cache.entries("other") == []
    cache.close()


def test_get_or_compute_runs_once(tmp_path):
    """The deterministic computation runs on a miss only."""
    cache = DesignCache(tmp_path / "cache.sqlite")
    calls = []

    def compute():
        calls.append(1)
        return KPISet(candidate_id="C1", co2_t_per_yr=10.0), {"days": 365}

    first, hit1 = cache.get_or_compute({"q": 300}, compute, namespace="ns", candidate_id="C1")
    second, hit2 = cache.get_or_compute({"q": 300.0}, compute, namespace="ns", candidate_id="C9")
    assert (hit1, hit2) == (False, True) and len(calls) == 1
    assert second.result == {"days": 365} and second.kpis.candidate_id == "C9"
    assert first.config_hash == second.config_hash
    cache.close()


def test_stub_results_are_refused_and_dotted_directories_work(tmp_path):
    """A stub result is never cached; 'runs/v1.0' is a directory, not a cache file."""
    cache = DesignCache(tmp_path / "v1.0")
    assert cache.path == tmp_path / "v1.0" / CACHE_FILE
    with pytest.raises(ValueError, match="stub"):
        cache.put({"q": 1}, namespace="ns", stub=True)
    assert cache.stats() == {"entries": 0, "hits": 0}
    cache.close()
