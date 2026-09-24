"""Enforce the per-file coverage floors declared in `pyproject.toml`.

Why this exists. The repo's gate is `--cov-fail-under=80` applied package-wide
(`pyproject.toml`, `.github/workflows/code-coverage.yml`). A package-wide floor
cannot go red for one file: the well-covered modules carry the average while a
single delivered venue rots. `broker/mt4_live.py` read 87% while 18 of 305 tests
touched it, every method once, through a stub that returned native dicts. The
number was true and it meant nothing.

The comparison itself lives in `tests/coverage_floor.py` so that it is
importable and its red path is executed by `tests/test_coverage_floor.py`.
This module is only the pytest wiring.

**The wiring is the part that nearly shipped broken.** The first version of this
plugin measured correctly, printed `BELOW FLOOR`, and exited 0, because
`pytest_sessionfinish` runs too late to affect the exit status: the terminal
reporter's own `pytest_sessionfinish` is a hook wrapper, so a plain
implementation runs *before* the summary and a mutation of `session.exitstatus`
there is discarded. pytest-cov solves the same problem the same way and says so
in a comment above its own `pytest_runtestloop` wrapper (`pytest_cov/plugin.py`,
"by the time pytest_sessionfinish runs, it's too late to set testsfailed"). So
the verdict is reached in a `pytest_runtestloop` wrapper and registered by
incrementing `session.testsfailed`, which is what turns it into a non-zero exit.
`tryfirst` makes this wrapper the outermost one, so its post-yield half runs
after pytest-cov has stopped collection and the data is complete.

That failure is worth keeping in the docstring: a gate that prints red and exits
zero is indistinguishable from a passing gate to CI, which is the exact defect
class this whole change set is about.

Two things it deliberately does not do silently:

- When coverage is not being collected it says so and passes. `ci.yml` runs
  `pytest --override-ini addopts=`, which strips `--cov`, so the floors are
  inert in that job by design; the coverage job is where they bite.
- When the run is a partial selection it says so and passes, because a floor
  measured against a subset of the suite is meaningless.

It never skips without printing why.
"""

from __future__ import annotations

import io

import pytest
from coverage_floor import evaluate, line, read_floors

_LINES = pytest.StashKey[list]()
_HEADER = "per-file coverage floors"


def _is_full_run(config: pytest.Config) -> bool:
    """A floor only means something when the whole suite ran.

    `config.args` is the resolved selection: it equals `testpaths` when no test
    path was named on the command line. `-k` and `-m` narrow a run without
    touching `config.args`, so both are checked too.
    """
    if getattr(config.option, "keyword", ""):
        return False
    if getattr(config.option, "markexpr", ""):
        return False
    return list(config.args) == list(config.getini("testpaths"))


def _coverage(config: pytest.Config):
    plugin = config.pluginmanager.get_plugin("_cov")
    controller = getattr(plugin, "cov_controller", None)
    return getattr(controller, "cov", None)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtestloop(session: pytest.Session):
    """Measure the floors and register a breach as a test failure.

    Outermost wrapper, so the post-yield half runs after pytest-cov has finished
    collecting. `session.testsfailed` is the only lever that still changes the
    exit status at this point.
    """
    result = yield

    config = session.config
    floors = read_floors(config.rootpath)
    if not floors:
        return result

    cov = _coverage(config)
    if cov is None:
        config.stash[_LINES] = [
            f"{_HEADER}: not checked, coverage is not being collected in this run"
        ]
        return result
    if not _is_full_run(config):
        config.stash[_LINES] = [
            f"{_HEADER}: not checked, this run is a partial selection ({list(config.args)})"
        ]
        return result

    measured: dict[str, float | None] = {}
    for relative in sorted(floors):
        target = config.rootpath / relative
        measured[relative] = (
            cov.report(morfs=[str(target)], file=io.StringIO()) if target.is_file() else None
        )

    lines = [
        f"{_HEADER}: {line(relative, value, floors[relative])}"
        for relative, value in measured.items()
        if value is not None
    ]
    failures = evaluate(measured, floors)
    if failures:
        lines.append("")
        lines.append(f"FAIL {_HEADER} not reached:")
        lines.extend(f"  {message}" for message in failures)
        session.testsfailed += 1
    config.stash[_LINES] = lines
    return result


@pytest.hookimpl(trylast=True)
def pytest_terminal_summary(terminalreporter, exitstatus, config: pytest.Config) -> None:
    """Print the verdict next to pytest-cov's own table."""
    del exitstatus
    for text in config.stash.get(_LINES, []):
        terminalreporter.write_line(text)
