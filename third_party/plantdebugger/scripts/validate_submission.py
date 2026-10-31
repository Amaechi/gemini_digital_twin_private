#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    "README.md",
    "docker-compose.yml",
    "docker-compose.cloud.yml",
    "docs/architecture.svg",
    "docs/architecture.png",
    "docs/architecture_cloud.png",
    "docs/collaborative_loop.png",
    "docs/architecture.md",
    "docs/AGENT_ORCHESTRATION.md",
    "services/plant-debugger/plant_debugger/agent/adk_agent.py",
    "services/plant-debugger/plant_debugger/field_ops/adk_orchestration.py",
    "services/plant-debugger/plant_debugger/field_ops/collaborative_manager.py",
    "services/plant-debugger/plant_debugger/field_ops/manager.py",
    "services/plant-debugger/plant_debugger/field_ops/storage.py",
    "services/plant-debugger/plant_debugger/field_ops/vision.py",
    "services/plant-debugger/plant_debugger/web_auth.py",
    "services/plant-debugger/plant_debugger/runner/incident_runner.py",
    "services/plant-debugger/plant_debugger/safety/action_guard.py",
    "services/plant-sim/plant_sim/model.py",
    "deploy/bootstrap_gcp.sh",
    "deploy/deploy.sh",
    "deploy/cloud_up.sh",
]


def main() -> int:
    missing = [item for item in REQUIRED if not (ROOT / item).exists()]
    if missing:
        print("Missing required files:")
        for item in missing:
            print(f"  - {item}")
        return 1
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=ROOT,
        env={
            **__import__("os").environ,
            "PYTHONPATH": f"{ROOT / 'services/plant-sim'}:{ROOT / 'services/plant-debugger'}",
        },
        check=False,
    )
    if result.returncode:
        return result.returncode
    print("Human Physical I/O submission evidence files are present and tests pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
