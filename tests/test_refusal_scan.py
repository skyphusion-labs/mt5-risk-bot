"""The roster's own instrument, tested against the shapes that defeated it (#61).

`tests/test_refusal_reasons.py` holds a roster of refusal reasons and asserts it
against `risk.py` itself, so the count cannot drift. The scanner behind that
assertion used a regex requiring a closing quote, so a reason built by
concatenation was invisible and the roster read GREEN while the module carried a
reason no case covered.

Issue #61 asked for two things and this file proves both:

1. the shapes resolve (concatenation, f-string, module constant, conditional);
2. **a site the scanner cannot read fails LOUDLY** rather than being skipped.
   That is the real fix regardless of matching strategy: an instrument must
   report that it stopped being able to measure.

Every case feeds SYNTHETIC source, because a test that could only run against
the real `risk.py` could not exercise the failure modes: it would be pinned to
whatever shapes that file happens to contain today, which is how the hole opened
in the first place. The scanner is also run against the real module here, once,
to prove the synthetic cases are about the same code path.
"""

from __future__ import annotations

import re
from pathlib import Path

from refusal_scan import scan_reasons

HEAD = '''
class RiskManager:
    def evaluate(self):
'''


def _scan(body: str, *, prelude: str = ""):
    return scan_reasons(prelude + HEAD + body)


def test_the_regex_form_still_resolves() -> None:
    """The shape the old scanner handled must not regress."""
    scan = _scan('        return RiskDecision(allowed=False, reason="max_positions")\n')
    assert scan.names == {"max_positions"}
    assert scan.prefixes == frozenset()
    assert not scan.unresolved


def test_a_reason_built_by_concatenation_is_seen() -> None:
    """THE #61 CASE. This is the literal shape that read as absent.

    `reason="spec_not_measured:" + join(...)`: the old regex needed a quote
    immediately after the name and found none, so the reason was dropped and the
    roster's count silently stopped being exhaustive.
    """
    scan = _scan(
        '        return RiskDecision(\n'
        '            allowed=False,\n'
        '            reason="spec_not_measured:" + ",".join(sorted(missing)),\n'
        '        )\n'
    )
    assert "spec_not_measured" in scan.names
    assert "spec_not_measured" in scan.prefixes, (
        "a prefix reason reported as a whole reason would be asserted with == "
        "by a later case and never match the runtime string"
    )
    assert not scan.unresolved


def test_a_concatenated_module_constant_is_seen() -> None:
    """The shape the deviation gate uses: a named constant plus an f-string."""
    scan = _scan(
        '        return RiskDecision(allowed=False, reason=THE_NAME + f":{x}")\n',
        prelude='THE_NAME = "deviation_below_spread"\n',
    )
    assert scan.names == {"deviation_below_spread"}
    assert scan.prefixes == {"deviation_below_spread"}
    assert not scan.unresolved


def test_a_bare_module_constant_is_seen() -> None:
    """`reason=SOME_CONSTANT` is a named reason, not a forward.

    Without the module-constant map this would look like a local variable, be
    filed as forwarding, and go missing while every assertion still passed.
    """
    scan = _scan(
        '        return RiskDecision(allowed=False, reason=THE_NAME)\n',
        prelude='THE_NAME = "margin_buffer"\n',
    )
    assert scan.names == {"margin_buffer"}
    assert scan.prefixes == frozenset()
    assert not scan.forwarded


def test_an_f_string_reason_is_seen_as_a_prefix() -> None:
    scan = _scan('        return RiskDecision(allowed=False, reason=f"stops_level:{n}")\n')
    assert scan.names == {"stops_level"}
    assert scan.prefixes == {"stops_level"}


def test_a_conditional_names_both_branches() -> None:
    scan = _scan('        return RiskDecision(reason="a_gate" if x else "b_gate")\n')
    assert scan.names == {"a_gate", "b_gate"}


def test_an_or_fallback_names_the_literal_and_forwards_the_rest() -> None:
    """`return self._halt_reason or "halted"`, which risk.py really does."""
    scan = scan_reasons(
        "class RiskManager:\n"
        "    def circuit_reason(self) -> str:\n"
        '        return self._halt_reason or "halted"\n'
    )
    assert scan.names == {"halted"}
    assert scan.forwarded == ("self._halt_reason",)
    assert not scan.unresolved


def test_a_site_the_scanner_cannot_read_is_reported_not_skipped() -> None:
    """The REAL fix. A scanner that skips what it cannot parse is the defect.

    A list subscript is not a shape this scanner resolves, and the answer has to
    be "I could not read line N", never silence. `unresolved` is what the roster
    asserts on FIRST, before it compares any counts, because a denominator built
    on a partial read is not a denominator.
    """
    scan = _scan('        return RiskDecision(allowed=False, reason=REASONS[k])\n')
    assert scan.names == frozenset()
    assert len(scan.unresolved) == 1
    assert "REASONS[k]" in scan.unresolved[0]
    assert "line " in scan.unresolved[0]


def test_an_unreadable_f_string_is_reported_too() -> None:
    """An f-string that does not OPEN with a literal names nothing readable."""
    scan = _scan('        return RiskDecision(allowed=False, reason=f"{prefix}_gate")\n')
    assert scan.names == frozenset()
    assert len(scan.unresolved) == 1


def test_a_concatenation_with_an_unreadable_head_is_reported() -> None:
    """Only the LEFT side can name the reason, so an unreadable left side fails."""
    scan = _scan('        return RiskDecision(allowed=False, reason=parts[0] + ":x")\n')
    assert scan.names == frozenset()
    assert len(scan.unresolved) == 1


def test_a_halt_call_and_a_halt_assignment_are_both_seen() -> None:
    """Two more naming sites risk.py uses, neither of them a `reason=` keyword."""
    scan = scan_reasons(
        "class RiskManager:\n"
        "    def circuit(self):\n"
        '        self._halt_reason = "state_unreadable"\n'
        '        return self._halt("daily_loss")\n'
    )
    assert scan.names == {"state_unreadable", "daily_loss"}


def test_an_empty_reason_clears_one_rather_than_naming_one() -> None:
    """`self._halt_reason = ""` must not enter the roster as a reason called ""."""
    scan = scan_reasons(
        "class RiskManager:\n"
        "    def clear(self) -> str:\n"
        '        self._halt_reason = ""\n'
        '        return ""\n'
    )
    assert scan.names == frozenset()
    assert not scan.unresolved


def test_a_str_method_outside_the_class_is_not_a_reason_site() -> None:
    """`classify_symbol` returns "fx"/"not_fx", which are not refusal reasons.

    Scoping the return scan to RiskManager is what keeps them out, so it is
    asserted rather than left to luck.
    """
    scan = scan_reasons(
        'SYMBOL_FX = "fx"\n'
        "def classify_symbol(s: str) -> str:\n"
        "    return SYMBOL_FX\n"
        "class RiskManager:\n"
        "    def circuit_reason(self) -> str:\n"
        '        return "halt_file"\n'
    )
    assert scan.names == {"halt_file"}


def test_the_superseded_regex_really_was_blind_to_it() -> None:
    """Kept executable rather than described in prose.

    "The old scanner could not see a concatenated reason" is a claim, and a
    reviewer should not have to take it on trust to believe the change was
    needed. The regex below is the one that shipped. It matches the quoted form
    and returns nothing at all for the concatenated one, which is the whole
    defect: not a wrong answer, an empty one that looked like absence.
    """
    old = r'reason="([a-z_]+)"'
    quoted = 'reason="max_positions"'
    concatenated = 'reason="spec_not_measured:" + ",".join(sorted(missing))'

    assert re.findall(old, quoted) == ["max_positions"]
    assert re.findall(old, concatenated) == []

    seen = _scan(f"        return RiskDecision(allowed=False, {concatenated})\n")
    assert "spec_not_measured" in seen.names


def test_the_real_module_reads_clean_and_names_both_prefix_reasons() -> None:
    """The synthetic cases above are about this file's real code path.

    Both prefix-shaped reasons are here on purpose: `spec_not_measured` is the
    one #61 named, and `deviation_below_spread` was written in the SAME shape
    rather than a friendlier one, so the fix stays proven against the form that
    broke the scanner instead of against a form that avoids it.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "straightedge" / "risk.py"
    scan = scan_reasons(src.read_text(encoding="utf-8"))
    assert not scan.unresolved, repr(list(scan.unresolved))
    assert scan.prefixes == {"spec_not_measured", "deviation_below_spread"}
    assert "spec_not_measured" in scan.names
