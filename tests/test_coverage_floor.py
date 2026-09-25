"""Drive the per-file coverage floor red on purpose.

The floor added for `broker/mt4_live.py` replaces a package-wide number that
could not go red for one file. Replacing one unfalsifiable gate with another
would be no gain, so the comparison's failing path executes here rather than
only in the reviewer's terminal.
"""

from __future__ import annotations

from pathlib import Path

from coverage_floor import TABLE, evaluate, line, read_floors

MT4 = "src/straightedge/broker/mt4_live.py"


class TestEvaluate:
    def test_a_file_above_its_floor_passes(self) -> None:
        assert evaluate({MT4: 97.59}, {MT4: 97.0}) == []

    def test_a_file_exactly_on_its_floor_passes(self) -> None:
        assert evaluate({MT4: 97.0}, {MT4: 97.0}) == []

    def test_a_file_below_its_floor_fails_and_names_both_numbers(self) -> None:
        failures = evaluate({MT4: 86.94}, {MT4: 97.0})
        assert len(failures) == 1
        assert MT4 in failures[0]
        assert "86.94" in failures[0]
        assert "97" in failures[0]

    def test_the_pre_transcript_measurement_would_breach_the_floor(self) -> None:
        """86.94% is what `mt4_live.py` measured before the golden transcripts.

        The floor has to reject the number the file carried while its coverage
        came from 18 happy-path calls through a dict stub. If this passes, the
        floor was set too low to be worth declaring.
        """
        assert evaluate({MT4: 86.94}, {MT4: 97.0})

    def test_a_missing_file_is_a_failure_not_a_pass(self) -> None:
        """A floor aimed at a path that does not exist cannot go red on content.

        Treating it as satisfied is how a floor outlives the file it guarded.
        """
        failures = evaluate({MT4: None}, {MT4: 97.0})
        assert len(failures) == 1
        assert "missing" in failures[0]

    def test_several_breaches_are_all_reported(self) -> None:
        floors = {MT4: 97.0, "src/straightedge/risk.py": 90.0}
        failures = evaluate({MT4: 10.0, "src/straightedge/risk.py": 20.0}, floors)
        assert len(failures) == 2

    def test_a_measured_file_with_no_declared_floor_is_ignored(self) -> None:
        """Only declared files are gated; everything else is the package-wide job."""
        assert evaluate({"src/straightedge/desk.py": 1.0}, {}) == []

    def test_no_floors_declared_is_not_a_failure(self) -> None:
        assert evaluate({MT4: 1.0}, {}) == []


class TestReportLine:
    def test_ok_line(self) -> None:
        assert line(MT4, 97.59, 97.0).endswith("97.59% (floor 97%) ok")

    def test_below_floor_line_says_so(self) -> None:
        assert "BELOW FLOOR" in line(MT4, 86.94, 97.0)


class TestReadFloors:
    def test_the_repo_declares_a_floor_for_the_mt4_adapter(self) -> None:
        """The floor must be in `pyproject.toml`, not in a workflow file.

        `.github/workflows/ci.yml` is being edited by other work in flight, and
        a gate declared in two places drifts. One declaration, read by every
        runner.
        """
        floors = read_floors(Path(__file__).resolve().parents[1])
        assert MT4 in floors
        assert floors[MT4] >= 90.0

    def test_a_tree_without_a_manifest_yields_no_floors(self, tmp_path: Path) -> None:
        assert read_floors(tmp_path) == {}

    def test_a_manifest_without_the_table_yields_no_floors(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
        assert read_floors(tmp_path) == {}

    def test_floors_are_read_as_numbers(self, tmp_path: Path) -> None:
        table = ".".join(TABLE)
        (tmp_path / "pyproject.toml").write_text(
            f'[{table}]\n"a/b.py" = 91\n', encoding="utf-8"
        )
        assert read_floors(tmp_path) == {"a/b.py": 91.0}
