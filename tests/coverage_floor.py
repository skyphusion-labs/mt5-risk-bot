"""Decision logic for the per-file coverage floors.

Separate from `conftest.py` so the comparison itself is importable and testable.
A gate whose own failure path never executes is the defect this file exists to
avoid: `tests/test_coverage_floor.py` drives `evaluate` red on purpose.

What a per-file floor does and does not catch. It catches code in a named file
becoming unreached: a deleted test that was the only one exercising a branch, a
new function nobody calls, a whole test module removed. It does not catch a test
losing its assertions while still executing the same lines, and it does not
catch a test asserting the wrong thing. Coverage measures reach, never
correctness. The floor is one instrument, not the proof.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

#: The table in `pyproject.toml` that declares the floors.
TABLE = ("tool", "straightedge", "coverage_floors")


def read_floors(rootpath: Path) -> dict[str, float]:
    """Load `[tool.straightedge.coverage_floors]` from the package manifest.

    The floors live in `pyproject.toml` rather than a workflow file so one
    declaration covers the local run and every CI job, and so a change to the
    gate shows up as a reviewable diff in the manifest.
    """
    manifest = rootpath / "pyproject.toml"
    if not manifest.is_file():
        return {}
    with manifest.open("rb") as fh:
        data: dict = tomllib.load(fh)
    table: dict = data
    for key in TABLE:
        table = table.get(key, {})
        if not isinstance(table, dict):
            return {}
    return {str(k): float(v) for k, v in table.items()}


def evaluate(measured: dict[str, float | None], floors: dict[str, float]) -> list[str]:
    """Return one message per breached floor, empty when every floor is met.

    `measured[path]` of None means the file named in the manifest was not found,
    which is itself a failure: a floor pointing at nothing is a gate that cannot
    go red, and silently passing it is how the floor would rot along with the
    file it was meant to protect.
    """
    failures: list[str] = []
    for path, floor in sorted(floors.items()):
        value = measured.get(path)
        if value is None:
            failures.append(f"{path}: a floor of {floor:.0f}% is declared but the file is missing")
        elif value + 1e-9 < floor:
            failures.append(f"{path}: {value:.2f}% is below its floor of {floor:.0f}%")
    return failures


def line(path: str, value: float, floor: float) -> str:
    """One report line, matching the shape of pytest-cov's own summary."""
    verdict = "ok" if value + 1e-9 >= floor else "BELOW FLOOR"
    return f"{path} {value:.2f}% (floor {floor:.0f}%) {verdict}"
