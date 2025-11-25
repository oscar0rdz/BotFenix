from __future__ import annotations

import logging
import time
from typing import List, Optional

from core.models import Position, TradeRecord, Signal
from config import (
    RISK_PER_TRADE,
    SL_BUFFER_PCT,
    LEVERAGE,
    DAILY_MAX_LOSS_PCT,
    MAX_TRADES_PER_DAY,
    MIN_IMBALANCE,
    TIME_STOP_LOW_VOL_SEC,
    TIME_STOP_HIGH_VOL_SEC,
    HIGH_VOL_THRESHOLD,
    BREAKEVEN_TRIGGER_PCT,
    TRAILING_STOP_PCT,
    PARTIAL_TP_FRACTION,
    REWARD_RISK_BASE,
)


class PaperWallet:
    """Portfolio simulator for live paper trading with advanced risk management."""

    def __init__(
        self,
        initial_balance: float,
        fee_rate: float = 0.0004,  # 0.04% taker
        risk_per_trade: float = RISK_PER_TRADE,
        reward_risk: float = REWARD_RISK_BASE,
        sl_buffer_pct: float = SL_BUFFER_PCT,
        leverage: float = LEVERAGE,
        max_daily_loss_pct: float = DAILY_MAX_LOSS_PCT,
        max_trades_per_day: int = MAX_TRADES_PER_DAY,
        min_imbalance: float = MIN_IMBALANCE,
    ) -> None:
        self.balance: float = initial_balance
        self.fee_rate = fee_rate
        self.risk_per_trade = risk_per_trade
        self.reward_risk = reward_risk
        self.sl_buffer_pct = sl_buffer_pct
        self.leverage = leverage

        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_trades_per_day = max_trades_per_day
        self.min_imbalance = min_imbalance

        self.position: Optional[Position] = None
        self.trade_history: List[TradeRecord] = []
        self.last_equity: float = initial_balance
        self._open_reason: str = ""

        # Daily statistics
        self._current_day: Optional[int] = None
        self._start_of_day_balance: float = initial_balance
        self._trades_today: int = 0

    # ---------- internal utilities ----------

    def has_open_position(self) -> bool:
        return self.position is not None

    def _reset_if_new_day(self, ts: float) -> None:
        day = time.gmtime(ts).tm_yday  # use UTC for consistency
        if self._current_day is None or day != self._current_day:
            self._current_day = day
            self._start_of_day_balance = self.balance
            self._trades_today = 0
            logging.info(
                "[DAY RESET] New day detected. Start balance=%.2f",
                self._start_of_day_balance,
            )

    def _daily_drawdown_pct(self) -> float:
        if self._start_of_day_balance <= 0:
            return 0.0
        return (self.balance - self._start_of_day_balance) / self._start_of_day_balance

    def can_open_new_trade(self, ts: float) -> bool:
        self._reset_if_new_day(ts)
        dd = self._daily_drawdown_pct()
        if dd <= -self.max_daily_loss_pct:
            logging.info(
                "Daily loss limit reached: %.2f%% (dd=%.2f%%). "
                "No more trades will be opened today.",
                self.max_daily_loss_pct * 100,
                dd * 100,
            )
            return False
        if self._trades_today >= self.max_trades_per_day:
            logging.info(
                "Daily trade limit reached: %d trades.",
                self.max_trades_per_day,
            )
            return False
        return True

    def _calc_qty_from_risk(self, entry_price: float, sl_price: float) -> Optional[float]:
        risk_amount = self.balance * self.risk_per_trade
        sl_distance = abs(entry_price - sl_price)
        if sl_distance <= 0:
            logging.warning(
                "SL distance <= 0. entry_price=%.4f sl_price=%.4f",
                entry_price,
                sl_price,
            )
            return None
        qty = risk_amount / sl_distance

        # Avoid impossible sizes given the leverage
        notional = entry_price * qty
        margin_required = notional / self.leverage
        if margin_required > self.balance:
            scale = (self.balance * self.leverage * 0.9) / notional  # use ~90% as maximum
            qty *= scale
            logging.info(
                "Adjusting qty for margin: notional=%.2f margin_req=%.2f "
                "balance=%.2f -> qty=%.6f",
                notional,
                margin_required,
                self.balance,
                qty,
            )

        return qty

    # ---------- position opening ----------

    def open_long(
        self,
        ts: float,
        price: float,
        recent_min_price: float,
        signal: Signal,
        entry_imbalance: float,
    ) -> Optional[Position]:
        if self.position is not None:
            return None
        if not self.can_open_new_trade(ts):
            return None

        sl_price = recent_min_price * (1.0 - self.sl_buffer_pct)
        qty = self._calc_qty_from_risk(entry_price=price, sl_price=sl_price)
        if qty is None or qty <= 0:
            return None

        tp_price = price + (price - sl_price) * self.reward_risk

        pos = Position(
            side="LONG",
            entry_price=price,
            qty=qty,
            initial_qty=qty,
            sl_price=sl_price,
            tp_price=tp_price,
            entry_ts=ts,
            entry_imbalance=entry_imbalance,
            max_favorable_price=price,
            min_favorable_price=price,
        )

        self.position = pos
        self._open_reason = signal.reason
        logging.info(
            "[OPEN LONG] price=%.2f sl=%.2f tp=%.2f qty=%.6f risk=%.2f%% reason=%s",
            price,
            sl_price,
            tp_price,
            qty,
            self.risk_per_trade * 100,
            signal.reason,
        )
        return pos

    def open_short(
        self,
        ts: float,
        price: float,
        recent_max_price: float,
        signal: Signal,
        entry_imbalance: float,
    ) -> Optional[Position]:
        if self.position is not None:
            return None
        if not self.can_open_new_trade(ts):
            return None

        sl_price = recent_max_price * (1.0 + self.sl_buffer_pct)
        qty = self._calc_qty_from_risk(entry_price=price, sl_price=sl_price)
        if qty is None or qty <= 0:
            return None

        tp_price = price - (sl_price - price) * self.reward_risk

        pos = Position(
            side="SHORT",
            entry_price=price,
            qty=qty,
            initial_qty=qty,
            sl_price=sl_price,
            tp_price=tp_price,
            entry_ts=ts,
            entry_imbalance=entry_imbalance,
            max_favorable_price=price,
            min_favorable_price=price,
        )

        self.position = pos
        self._open_reason = signal.reason
        logging.info(
            "[OPEN SHORT] price=%.2f sl=%.2f tp=%.2f qty=%.6f risk=%.2f%% reason=%s",
            price,
            sl_price,
            tp_price,
            qty,
            self.risk_per_trade * 100,
            signal.reason,
        )
        return pos

    # ---------- dynamic position management ----------

    def _update_trade_management(self, pos: Position, price: float, vol: Optional[float]) -> None:
        """Breakeven + trailing based on favorable movement and volatility."""
        move = (price - pos.entry_price) if pos.side == "LONG" else (pos.entry_price - price)

        # Update favorable extremes
        if pos.side == "LONG":
            pos.max_favorable_price = max(pos.max_favorable_price or price, price)
        else:
            pos.min_favorable_price = min(pos.min_favorable_price or price, price)

        # Breakeven trigger: move SL to entry when the price covers commissions
        if not pos.breakeven_done:
            trigger_abs = pos.entry_price * BREAKEVEN_TRIGGER_PCT
            if move >= trigger_abs:
                old_sl = pos.sl_price
                pos.sl_price = pos.entry_price
                pos.breakeven_done = True
                logging.info(
                    "[BREAKEVEN %s] move=%.6f old_sl=%.2f new_sl=%.2f",
                    pos.side,
                    move,
                    old_sl,
                    pos.sl_price,
                )

        # Trailing stop: after breakeven, follow the price
        if pos.breakeven_done:
            if pos.side == "LONG":
                trail_sl = price * (1.0 - TRAILING_STOP_PCT)
                if trail_sl > pos.sl_price:
                    pos.sl_price = trail_sl
            else:
                trail_sl = price * (1.0 + TRAILING_STOP_PCT)
                if trail_sl < pos.sl_price:
                    pos.sl_price = trail_sl

    def _mark_to_market(self, price: float) -> float:
        if not self.position:
            self.last_equity = self.balance
            return self.last_equity

        pos = self.position
        if pos.side == "LONG":
            unrealized = (price - pos.entry_price) * pos.qty
        else:
            unrealized = (pos.entry_price - price) * pos.qty

        entry_fee = pos.entry_price * pos.qty * self.fee_rate
        exit_fee_estimate = price * pos.qty * self.fee_rate
        self.last_equity = self.balance + unrealized - entry_fee - exit_fee_estimate
        return self.last_equity

    # ---------- price tick: SL/TP, partial, invalidation, time stop ----------

    def on_price_tick(
        self,
        ts: float,
        price: float,
        vol: Optional[float] = None,
        imbalance: Optional[float] = None,
    ) -> Optional[TradeRecord]:
        """Updates equity and closes the position if SL/TP/time-stop/invalidation is reached."""
        self._reset_if_new_day(ts)
        closed_trade: Optional[TradeRecord] = None

        if self.position:
            pos = self.position

            # Active management: breakeven + trailing
            self._update_trade_management(pos, price, vol)

            # --- Partial TP ---
            if not pos.partial_taken:
                if pos.side == "LONG" and price >= pos.tp_price:
                    partial_qty = pos.qty * PARTIAL_TP_FRACTION
                    if partial_qty > 0:
                        entry_fee = pos.entry_price * partial_qty * self.fee_rate
                        exit_fee = pos.tp_price * partial_qty * self.fee_rate
                        pnl = (pos.tp_price - pos.entry_price) * partial_qty - (entry_fee + exit_fee)
                        self.balance += pnl
                        self._trades_today += 1

                        trade = TradeRecord(
                            side=pos.side,
                            entry_price=pos.entry_price,
                            exit_price=pos.tp_price,
                            qty=partial_qty,
                            sl_price=pos.sl_price,
                            tp_price=pos.tp_price,
                            entry_ts=pos.entry_ts,
                            exit_ts=ts,
                            realized_pnl=pnl,
                            fees_paid=entry_fee + exit_fee,
                            reason=f"TP_PARTIAL | {self._open_reason}",
                        )
                        self.trade_history.append(trade)

                        pos.qty -= partial_qty
                        pos.partial_taken = True

                        logging.info(
                            "[PARTIAL TP LONG] tp=%.2f qty_closed=%.6f pnl=%.4f balance=%.4f",
                            pos.tp_price,
                            partial_qty,
                            pnl,
                            self.balance,
                        )

                elif pos.side == "SHORT" and price <= pos.tp_price:
                    partial_qty = pos.qty * PARTIAL_TP_FRACTION
                    if partial_qty > 0:
                        entry_fee = pos.entry_price * partial_qty * self.fee_rate
                        exit_fee = pos.tp_price * partial_qty * self.fee_rate
                        pnl = (pos.entry_price - pos.tp_price) * partial_qty - (entry_fee + exit_fee)
                        self.balance += pnl
                        self._trades_today += 1

                        trade = TradeRecord(
                            side=pos.side,
                            entry_price=pos.entry_price,
                            exit_price=pos.tp_price,
                            qty=partial_qty,
                            sl_price=pos.sl_price,
                            tp_price=pos.tp_price,
                            entry_ts=pos.entry_ts,
                            exit_ts=ts,
                            realized_pnl=pnl,
                            fees_paid=entry_fee + exit_fee,
                            reason=f"TP_PARTIAL | {self._open_reason}",
                        )
                        self.trade_history.append(trade)

                        pos.qty -= partial_qty
                        pos.partial_taken = True

                        logging.info(
                            "[PARTIAL TP SHORT] tp=%.2f qty_closed=%.6f pnl=%.4f balance=%.4f",
                            pos.tp_price,
                            partial_qty,
                            pnl,
                            self.balance,
                        )

            # --- Full SL / TP / invalidation / time stop ---
            exit_price = None
            exit_label = ""

            # Hard SL / TP (rest)
            if pos.side == "LONG":
                if price <= pos.sl_price:
                    exit_price = pos.sl_price
                    exit_label = "SL"
                elif pos.partial_taken and price >= pos.tp_price:
                    exit_price = pos.tp_price
                    exit_label = "TP"
            else:  # SHORT
                if price >= pos.sl_price:
                    exit_price = pos.sl_price
                    exit_label = "SL"
                elif pos.partial_taken and price <= pos.tp_price:
                    exit_price = pos.tp_price
                    exit_label = "TP"

            # Invalidation of thesis by imbalance (wall disappears / turns around)
            if exit_price is None and imbalance is not None:
                if pos.side == "LONG" and pos.entry_imbalance >= self.min_imbalance:
                    if imbalance < -self.min_imbalance * 0.25:
                        exit_price = price
                        exit_label = "INVALID_IMB"
                elif pos.side == "SHORT" and pos.entry_imbalance <= -self.min_imbalance:
                    if imbalance > self.min_imbalance * 0.25:
                        exit_price = price
                        exit_label = "INVALID_IMB"

            # Time stop (patch 3)
            if exit_price is None:
                time_in_trade = ts - pos.entry_ts
                if vol is not None and vol >= HIGH_VOL_THRESHOLD:
                    t_limit = TIME_STOP_HIGH_VOL_SEC
                else:
                    t_limit = TIME_STOP_LOW_VOL_SEC

                if time_in_trade >= t_limit:
                    exit_price = price
                    exit_label = "TIME_STOP"

            # If any exit reason was triggered, we close the rest of the position
            if exit_price is not None and pos.qty > 0:
                if pos.side == "LONG":
                    pnl_price = (exit_price - pos.entry_price) * pos.qty
                else:
                    pnl_price = (pos.entry_price - exit_price) * pos.qty

                entry_fee = pos.entry_price * pos.qty * self.fee_rate
                exit_fee = exit_price * pos.qty * self.fee_rate
                total_fees = entry_fee + exit_fee
                realized = pnl_price - total_fees

                self.balance += realized
                self._trades_today += 1

                trade = TradeRecord(
                    side=pos.side,
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    qty=pos.qty,
                    sl_price=pos.sl_price,
                    tp_price=pos.tp_price,
                    entry_ts=pos.entry_ts,
                    exit_ts=ts,
                    realized_pnl=realized,
                    fees_paid=total_fees,
                    reason=f"{exit_label} | {self._open_reason}",
                )
                self.trade_history.append(trade)

                logging.info(
                    "[CLOSE %s %s] exit=%.2f qty=%.6f pnl=%.4f fees=%.4f balance=%.4f reason=%s",
                    pos.side,
                    exit_label,
                    exit_price,
                    pos.qty,
                    realized,
                    total_fees,
                    self.balance,
                    self._open_reason,
                )

                self.position = None
                self._open_reason = ""
                closed_trade = trade

        # We update the equity with the current price
        self._mark_to_market(price)

        return closed_trade
