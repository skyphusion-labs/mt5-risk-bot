"""Numbers MEASURED on the live MT4 rig, and the fixtures built from them.

One module so that a measured number has exactly one home and a test can cite
it instead of restating it. The distinction this file keeps is the whole reason
it exists: what was measured, and what was merely CHOSEN to make a fixture
runnable. A chosen value that sits next to measured ones in an unlabelled
dataclass becomes a measurement to the next reader.

MEASURED on the live MT4 rig, 2026-09-24, by Conrad:

    XAUUSD  point 0.01, current spread 45 points ($0.45), ATR(14) H1 $17.22
    EURUSD  point 0.00001, current spread 1 point

    The `[risk] deviation_points` in force was the global default, 20. On gold
    that is $0.20 of tolerance against $0.45 of spread: less than half of one
    spread, on an instrument moving about $17 an hour. OrderSend rejects that
    intermittently and the log named no cause.

NOT MEASURED, and not invented as though it were: gold's contract size, tick
value, volume step and stop level. The fixture below sets them to the standard
100-ounce contract because sizing has to produce a non-zero lot for the PASS
case to mean anything, and it labels them, because Gil's broker has not been
read and this repo does not ship a number it has not seen.

XAGUSD is seeded in `config.example.toml` alongside gold because it has the same
SHAPE (a metal quoted in cents, spread an order of magnitude wider in points
than FX), and for no stronger reason than that. It has no fixture here and no
measured numbers, and it must not acquire either without a rig reading.
"""

from __future__ import annotations

from straightedge.models import SymbolSpec, Tick

#: Measured. The venue point for gold: one point is one cent.
GOLD_POINT = 0.01
#: Measured. 45 points, i.e. $0.45.
GOLD_SPREAD_POINTS = 45
#: Measured. ATR(14) on H1, in price units ($17.22).
GOLD_ATR = 17.22
#: Measured. One point on a 5-digit EURUSD feed.
FX_SPREAD_POINTS = 1
#: The global default that was in force, and is the shipped default.
GLOBAL_DEVIATION_POINTS = 20
#: Chosen, not measured: gold's price level only has to be plausible for the
#: stop and target distances below to be computed from the measured ATR.
GOLD_PRICE = 4000.00


def gold_spec(*, spread_points: int = GOLD_SPREAD_POINTS) -> SymbolSpec:
    """XAUUSD as the rig reported it, with the unmeasured fields labelled.

    MEASURED: `point`, `trade_tick_size` (one point), `spread`.
    CHOSEN so that sizing returns a lot at all: `trade_tick_value` and
    `trade_contract_size` are the standard 100-ounce contract ($1 per point per
    lot), and the volume and stop levels are ordinary retail values. None of
    those four has been read off Gil's broker.
    """
    return SymbolSpec(
        name="XAUUSD",
        digits=2,
        point=GOLD_POINT,
        trade_tick_size=GOLD_POINT,
        trade_tick_value=1.0,
        trade_contract_size=100.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        trade_stops_level=10,
        trade_freeze_level=0,
        filling_mode=1,
        currency_base="XAU",
        currency_profit="USD",
        currency_margin="USD",
        spread=spread_points,
    )


def gold_tick(*, spread_points: int = GOLD_SPREAD_POINTS, mid: float = GOLD_PRICE) -> Tick:
    """A gold tick carrying `spread_points` points of spread, centred on `mid`."""
    half = (spread_points * GOLD_POINT) / 2.0
    return Tick(time=0, bid=mid - half, ask=mid + half, last=mid)
