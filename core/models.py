from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple, Optional


@dataclass
class TradeEvent:
    ts: float
    price: float
    qty: float
    is_buy: bool


@dataclass
class OrderBookSnapshot:
    ts: float
    bids: List[Tuple[float, float]]
    asks: List[Tuple[float, float]]


@dataclass
class MarketSnapshot:
    ts: float
    mid_price: float
    order_book: OrderBookSnapshot
    trades: List[TradeEvent] = field(default_factory=list)


@dataclass
class Signal:
    symbol: str
    side: str          # "LONG" or "SHORT"
    score: float       # 0-100
    reason: str
    risk_mult: float = 1.0      # 1.0 normal, >1.0 sniper
    score_class: str = ""       # "STANDARD" or "SNIPER"


@dataclass
class Position:
    symbol: str
    side: str
    entry_price: float
    qty: float
    initial_qty: float
    sl_price: float
    tp_price: float
    entry_ts: float
    entry_imbalance: float
    risk_pct: float              # % of equity risked at SL when opening

    breakeven_done: bool = False
    partial_taken: bool = False
    max_favorable_price: Optional[float] = None
    min_favorable_price: Optional[float] = None

    exit_ts: Optional[float] = None
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    is_closed: bool = False
    open_reason: str = ""

    # NEW: total entry fee (maker)
    entry_fee_total: float = 0.0
    estimated_fees: float = 0.0
    type_label: str = "STANDARD"

    # Fields for smart INVALID_IMB
    invalid_imb_count: int = 0         # consecutive samples with adverse imbalance
    adverse_ticks: int = 0             # adverse price ticks since entry

    def update_max_favorable(self, last_price: float):
        """Keeps the best price in favor of the position."""
        if self.max_favorable_price is None:
            self.max_favorable_price = last_price
            return

        if self.side == "LONG":
            if last_price > self.max_favorable_price:
                self.max_favorable_price = last_price
        else:
            if last_price < self.max_favorable_price:
                self.max_favorable_price = last_price



@dataclass
class TradeRecord:
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    qty: float
    sl_price: float
    tp_price: float
    entry_ts: float
    exit_ts: float
    realized_pnl: float
    fees_paid: float
    reason: str