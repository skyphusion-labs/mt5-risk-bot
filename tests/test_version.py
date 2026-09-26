"""The package declares its version twice, so the two have to be pinned together.

Found while adding the watchdog, on `main`, not guessed: `pyproject.toml` read
`version = "1.5.0"` and `src/straightedge/__init__.py` read
`__version__ = "1.4.2"`. `pip install -e .` reports the first and
`straightedge doctor` prints the second, so a 1.5.0 install told its operator it
was 1.4.2, which is the number that goes into a handover checklist and into any
bug report Gil ever files.

Nothing could have caught it: no test read either declaration. One of the two
was always going to be forgotten in a release commit, and it was. This is the
gate, and it is deliberately dumb: two strings, one comparison, no tolerance.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import straightedge

ROOT = Path(__file__).resolve().parents[1]


def test_package_version_matches_the_manifest() -> None:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        manifest = tomllib.load(fh)["project"]["version"]
    print(f"version: __init__ {straightedge.__version__} vs manifest {manifest}")
    assert straightedge.__version__ == manifest, (
        f"straightedge.__version__ is {straightedge.__version__} and "
        f"pyproject.toml says {manifest}. `doctor` prints the first and the "
        "installed distribution reports the second, so an operator reading "
        "either one has been told a different release than the other."
    )
