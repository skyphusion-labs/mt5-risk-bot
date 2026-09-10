from __future__ import annotations

from typing import Protocol

from mt5_risk_bot.models import (
    Account,
    Bar,
    MarketOrder,
    OrderResult,
    PendingOrder,
    Position,
    SymbolSpec,
    Tick,
    WorkingOrder,
)


class Broker(Protocol):
    """Venue-neutral execution API. Paper, MT5, and MT4 implement this.

    Engine, desk, and risk never send MT5 request dicts. A new venue
    implements these methods. close_by may return unsupported.
    """

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def account(self) -> Account: ...
    def symbol(self, name: str) -> SymbolSpec: ...
    def tick(self, name: str) -> Tick: ...
    def rates(self, name: str, timeframe: str, count: int) -> list[Bar]: ...
    def positions(self, magic: int | None = None) -> list[Position]: ...
    def orders(self, magic: int | None = None) -> list[PendingOrder]: ...
    def select_symbol(self, name: str) -> bool: ...
    def check_market(self, order: MarketOrder) -> OrderResult: ...
    def market(self, order: MarketOrder) -> OrderResult: ...
    def check_working(self, order: WorkingOrder) -> OrderResult: ...
    def working(self, order: WorkingOrder) -> OrderResult: ...
    def modify_position(self, ticket: int, sl: float, tp: float, symbol: str = "") -> OrderResult: ...
    def modify_working(
        self,
        ticket: int,
        *,
        price: float | None = None,
        sl: float | None = None,
        tp: float | None = None,
        symbol: str = "",
        volume: float = 0.0,
        side: str = "",
        kind: str = "",
    ) -> OrderResult: ...
    def cancel(self, ticket: int) -> OrderResult: ...
    def close_position(
        self,
        ticket: int,
        *,
        symbol: str,
        side: str,
        volume: float,
        price: float,
        comment: str = "",
        magic: int = 0,
        deviation: int = 20,
    ) -> OrderResult: ...
    def close_by(self, ticket: int, other: int, symbol: str = "") -> OrderResult: ...
