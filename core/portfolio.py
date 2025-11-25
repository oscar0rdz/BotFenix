from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

from core.models import Position, TradeRecord, Signal
from config import (
    INITIAL_BALANCE,
    BASE_RISK_PER_TRADE,
    MAX_TOTAL_RISK_PCT_OPEN,
    DAILY_MAX_LOSS_PCT,
    DAILY_MAX_LOSS_ABS,
    MAX_TRADES_PER_DAY,
    REWARD_RISK_BASE,
    SL_BUFFER_PCT,
    LEVERAGE,
    BREAKEVEN_TRIGGER_PCT,
    TRAILING_STOP_PCT,
    PARTIAL_TP_FRACTION,
    PARTIAL_TP_TRIGGER_PCT,
    FEE_MAKER,
    FEE_TAKER,
    MIN_RR_VS_FEES,
    MAX_TRADES_PER_HOUR_PER_SYMBOL,
    COOLDOWN_AFTER_TRADE_SEC,
    INVALID_IMB_ENABLE,
    INVALID_IMB_THRESHOLD,
    INVALID_IMB_CONSEC_SAMPLES,
    INVALID_IMB_MIN_AGE_SEC,
    INVALID_IMB_MIN_ADVERSE_TICKS,
    TICK_SIZE,
    # NO_PROGRESS parameters
    NO_PROGRESS_EXIT_ENABLE,
    NO_PROGRESS_MIN_AGE_SEC,
    NO_PROGRESS_MIN_MFE_PCT,
    NO_PROGRESS_GIVEBACK_PCT,
    # Dynamic TIME_STOP parameters
    MAX_TRADE_LIFETIME_LOW_VOL_SEC,
    MAX_TRADE_LIFETIME_HIGH_VOL_SEC,
    VOL_NORM_HIGH_THRESHOLD,
)


class GlobalPortfolio:
    """Global portfolio shared among all symbols."""

    def __init__(self):
        self.balance: float = INITIAL_BALANCE
        self.equity: float = INITIAL_BALANCE

        self.fee_maker = FEE_MAKER
        self.fee_taker = FEE_TAKER

        self.positions: Dict[str, Position] = {}
        self.trade_history: List[TradeRecord] = []

        self._current_day: Optional[int] = None
        self._start_of_day_balance: float = INITIAL_BALANCE
        self._trades_today: int = 0
        
        # Frequency tracking per symbol
        self.last_exit_ts: Dict[str, float] = {}        # last close per symbol
        self.trades_last_hour: Dict[str, List[float]] = {}  # timestamps of trades per hour

    # -------- risk/day utilities --------

    def _reset_if_new_day(self, ts: float):
        day = time.gmtime(ts).tm_yday
        if self._current_day is None or day != self._current_day:
            self._current_day = day
            self._start_of_day_balance = self.balance
            self._trades_today = 0
            logging.info(
                "[DAY RESET] New day. Start balance=%.2f",
                self._start_of_day_balance,
            )

    def _daily_drawdown_pct(self):
        if self._start_of_day_balance <= 0:
            return 0.0
        return (self.balance - self._start_of_day_balance) / self._start_of_day_balance

    def _daily_drawdown_abs(self):
        """Loss in USD since the start of the day."""
        return self.balance - self._start_of_day_balance

    def _total_risk_pct_open(self):
        """Sum of the risk % of all open positions."""
        return sum(p.risk_pct for p in self.positions.values())

    def _max_new_risk_pct_allowed(self):
        return max(0.0, MAX_TOTAL_RISK_PCT_OPEN - self._total_risk_pct_open())

    # -------- queries --------

    def has_open_position(self, symbol: str):
        return symbol in self.positions

    def get_position(self, symbol: str):
        return self.positions.get(symbol)

    # -------- opening positions --------

    def _calc_qty_from_risk(
        self,
        entry_price: float,
        sl_price: float,
        risk_pct: float,
    ):
        sl_dist = abs(entry_price - sl_price)
        if sl_dist <= 0:
            logging.warning("SL distance <= 0: entry=%.4f sl=%.4f", entry_price, sl_price)
            return None

        risk_amount = self.balance * risk_pct
        qty = risk_amount / sl_dist

        notional = entry_price * qty
        margin_required = notional / LEVERAGE
        if margin_required > self.balance:
            scale = (self.balance * LEVERAGE * 0.9) / notional
            qty *= scale
            logging.info(
                "Adjusting qty for margin: notional=%.2f margin_req=%.2f balance=%.2f -> qty=%.6f",
                notional,
                margin_required,
                self.balance,
                qty,
            )

        return qty

    def _can_open_trade(self, ts: float, risk_pct: float):
        self._reset_if_new_day(ts)

        # Relative drawdown
        dd = self._daily_drawdown_pct()
        if dd <= -DAILY_MAX_LOSS_PCT:
            logging.info(
                "Daily loss limit (%%) reached: dd=%.2f%%, max=%.2f%%",
                dd * 100,
                DAILY_MAX_LOSS_PCT * 100,
            )
            return False

        # Absolute drawdown (e.g., -3 USD)
        dd_abs = self._daily_drawdown_abs()
        if dd_abs <= -DAILY_MAX_LOSS_ABS:
            logging.info(
                "Absolute daily loss limit reached: loss=%.2f USDT (max=%.2f)",
                -dd_abs,
                DAILY_MAX_LOSS_ABS,
            )
            return False

        if self._trades_today >= MAX_TRADES_PER_DAY:
            logging.info("Daily trade limit reached: %d", MAX_TRADES_PER_DAY)
            return False

        max_new_risk = self._max_new_risk_pct_allowed()
        if risk_pct > max_new_risk + 1e-9:
            logging.info(
                "Cannot open trade: new_risk=%.2f%%, open_risk=%.2f%%, max_total=%.2f%%",
                risk_pct * 100,
                self._total_risk_pct_open() * 100,
                MAX_TOTAL_RISK_PCT_OPEN * 100,
            )
            return False

        return True

    def open_position(
        self,
        symbol: str,
        ts: float,
        price: float,
        signal: Signal,
        recent_extreme_price: float,
        entry_imbalance: float,
        side: str,
    ):

        if symbol in self.positions:
            return None
        
        # Validate cooldown after closing
        last_exit = self.last_exit_ts.get(symbol, 0.0)
        if ts - last_exit < COOLDOWN_AFTER_TRADE_SEC:
            return None
        
        # Validate trade limit per hour
        trades_hour = self.trades_last_hour.get(symbol, [])
        trades_hour = [t for t in trades_hour if ts - t < 3600]  # filter last hour
        self.trades_last_hour[symbol] = trades_hour
        if len(trades_hour) >= MAX_TRADES_PER_HOUR_PER_SYMBOL:
            return None
        
        # Dynamic risk based on signal score
        base = BASE_RISK_PER_TRADE
        risk_pct = base * signal.risk_mult

        # Do not exceed available total risk
        max_new_risk = self._max_new_risk_pct_allowed()
        if max_new_risk <= 0:
            logging.info(
                "Global risk saturated, not opening new position on %s",
                symbol.upper(),
            )
            return None
        if risk_pct > max_new_risk:
            logging.info(
                "Adjusting risk due to global limit: requested=%.2f%%, allowed=%.2f%%",
                risk_pct * 100,
                max_new_risk * 100,
            )
            risk_pct = max_new_risk

        if not self._can_open_trade(ts, risk_pct=risk_pct):
            return None

        if side == "LONG":
            sl_price = recent_extreme_price * (1.0 - SL_BUFFER_PCT)
        else:
            sl_price = recent_extreme_price * (1.0 + SL_BUFFER_PCT)

        qty = self._calc_qty_from_risk(entry_price=price, sl_price=sl_price, risk_pct=risk_pct)
        if qty is None or qty <= 0:
            return None

        if side == "LONG":
            tp_price = price + (price - sl_price) * REWARD_RISK_BASE
        else:
            tp_price = price - (sl_price - price) * REWARD_RISK_BASE
        
        # Validate that TP compensates for fees (MIN_RR_VS_FEES)
        estimated_fees = (price * qty * self.fee_maker) + (tp_price * qty * self.fee_taker)
        tp_profit_gross = abs(tp_price - price) * qty
        
        if tp_profit_gross < estimated_fees * MIN_RR_VS_FEES:
            logging.debug(
                "[%s] Insufficient TP vs fees: profit_gross=%.4f, fees=%.4f, ratio=%.2f (min=%.1f)",
                symbol.upper(),
                tp_profit_gross,
                estimated_fees,
                tp_profit_gross / estimated_fees if estimated_fees > 0 else 0,
                MIN_RR_VS_FEES,
            )
            return None

        pos = Position(
            symbol=symbol,
            side=side,
            entry_price=price,
            qty=qty,
            initial_qty=qty,
            sl_price=sl_price,
            tp_price=tp_price,
            entry_ts=ts,
            entry_imbalance=entry_imbalance,
            risk_pct=risk_pct,
            max_favorable_price=price,
            min_favorable_price=price,
            open_reason=signal.reason,
            estimated_fees=estimated_fees,
            type_label=signal.score_class or "STANDARD",
        )
        pos.min_adverse_price = price
        pos.max_adverse_price = price

        # Entry fee (as maker)
        entry_fee = price * qty * self.fee_maker
        self.balance -= entry_fee
        pos.entry_fee_total = entry_fee

        self.positions[symbol] = pos
        logging.info(
            "[OPEN %s %s] %s price=%.2f sl=%.2f tp=%.2f qty=%.6f risk=%.2f%% reason=%s",
            symbol.upper(),
            side,
            symbol.upper(),
            price,
            sl_price,
            tp_price,
            qty,
            risk_pct * 100,
            signal.reason,
        )
        return pos

    # -------- active management: breakeven / trailing / equity --------

    def _update_trade_management(self, pos: Position, price: float):
        move = (price - pos.entry_price) if pos.side == "LONG" else (pos.entry_price - price)

        # MFE/MAE tracking
        if pos.side == "LONG":
            pos.max_favorable_price = max(
                getattr(pos, "max_favorable_price", pos.entry_price),
                price
            )
            pos.min_adverse_price = min(
                getattr(pos, "min_adverse_price", pos.entry_price),
                price
            )
        else:  # SHORT
            pos.min_favorable_price = min(
                getattr(pos, "min_favorable_price", pos.entry_price),
                price
            )
            pos.max_adverse_price = max(
                getattr(pos, "max_adverse_price", pos.entry_price),
                price
            )

        # breakeven
        if not pos.breakeven_done:
            trigger_abs = pos.entry_price * BREAKEVEN_TRIGGER_PCT
            if move >= trigger_abs:
                old_sl = pos.sl_price
                pos.sl_price = pos.entry_price
                pos.breakeven_done = True
                logging.info(
                    "[BREAKEVEN %s %s] move=%.6f old_sl=%.2f new_sl=%.2f",
                    pos.symbol.upper(),
                    pos.side,
                    move,
                    old_sl,
                    pos.sl_price,
                )

        # trailing after breakeven
        if pos.breakeven_done:
            if pos.side == "LONG":
                trail_sl = price * (1.0 - TRAILING_STOP_PCT)
                if trail_sl > pos.sl_price:
                    pos.sl_price = trail_sl
            else:
                trail_sl = price * (1.0 + TRAILING_STOP_PCT)
                if trail_sl < pos.sl_price:
                    pos.sl_price = trail_sl

    def _recompute_equity(self, prices: Dict[str, float]):
        """Equity = balance + unrealized PnL (estimating fees)."""
        eq = self.balance
        for symbol, pos in self.positions.items():
            price = prices.get(symbol)
            if price is None:
                continue
            if pos.side == "LONG":
                unreal = (price - pos.entry_price) * pos.qty
            else:
                unreal = (pos.entry_price - price) * pos.qty
            # The entry fee is already subtracted from balance.
            # We only need to estimate the exit fee.
            exit_fee_est = price * pos.qty * self.fee_taker
            eq += unreal - exit_fee_est
        self.equity = eq

    def _update_adverse_ticks(self, pos: Position, price: float):
        """Updates adverse ticks from entry based on the side."""
        tick = TICK_SIZE.get(pos.symbol, 0.0)
        if tick <= 0:
            pos.adverse_ticks = 0
            return

        if pos.side == "LONG":
            move = max(0.0, pos.entry_price - price)
        else:
            move = max(0.0, price - pos.entry_price)

        pos.adverse_ticks = int(move / tick)

    def _check_invalid_imb_close(
        self,
        pos: Position,
        imbalance: Optional[float],
        now_ts: float,
    ) -> Optional[str]:
        """
        Early closure due to truly adverse imbalance:
        - LONG: imbalance <= -threshold
        - SHORT: imbalance >= +threshold
        """
        if not INVALID_IMB_ENABLE or imbalance is None:
            pos.invalid_imb_count = 0
            return None

        age = now_ts - pos.entry_ts
        if age < INVALID_IMB_MIN_AGE_SEC:
            pos.invalid_imb_count = 0
            return None

        if pos.side == "LONG":
            adverse = imbalance <= -INVALID_IMB_THRESHOLD
        else:
            adverse = imbalance >= INVALID_IMB_THRESHOLD

        if not adverse:
            pos.invalid_imb_count = 0
            return None

        pos.invalid_imb_count += 1
        if pos.invalid_imb_count < INVALID_IMB_CONSEC_SAMPLES:
            return None

        if pos.adverse_ticks < INVALID_IMB_MIN_ADVERSE_TICKS:
            return None

        reason = (
            f"INVALID_IMB | side={pos.side} Imb_smooth={imbalance:.2f} "
            f"age={age:.1f}s adv_ticks={pos.adverse_ticks}"
        )
        return reason

    def _check_no_progress(self, pos: Position, price: float, ts: float) -> bool:
        """
        Detects trades that have not progressed sufficiently in favor
        and are starting to give back part of the small MFE they had.

        Returns:
            True -> if the position should be closed for NO_PROGRESS
            False -> if the condition is not met
        """
        if not NO_PROGRESS_EXIT_ENABLE:
            return False

        age_sec = ts - pos.entry_ts
        if age_sec < NO_PROGRESS_MIN_AGE_SEC:
            return False

        # You must have these fields updated on each tick
        max_fav_price = getattr(pos, "max_favorable_price", None)
        min_fav_price = getattr(pos, "min_favorable_price", None)

        if max_fav_price is None or min_fav_price is None:
            return False

        entry = pos.entry_price

        if pos.side == "LONG":
            mfe_pct = (max_fav_price - entry) / entry
            current_pct = (price - entry) / entry
        else:  # SHORT
            mfe_pct = (entry - min_fav_price) / entry
            current_pct = (entry - price) / entry

        # It never moved "decently" in favor -> we don't apply NO_PROGRESS
        if mfe_pct < NO_PROGRESS_MIN_MFE_PCT:
            return False

        # If it has already given back a good part of the MFE -> it should be cut
        if current_pct <= (mfe_pct - NO_PROGRESS_GIVEBACK_PCT):
            return True

        return False

    def _get_time_stop_limit(self, vol_norm: Optional[float]) -> float:
        """
        Returns the trade lifetime limit based on normalized volatility.
        vol_norm must be in [0, 1].
        """
        if vol_norm is None:
            return MAX_TRADE_LIFETIME_LOW_VOL_SEC

        if vol_norm >= VOL_NORM_HIGH_THRESHOLD:
            return MAX_TRADE_LIFETIME_HIGH_VOL_SEC
        else:
            return MAX_TRADE_LIFETIME_LOW_VOL_SEC

    # -------- tick per symbol: SL/TP/invalid/time-stop --------

    def on_price_tick(
        self,
        symbol: str,
        ts: float,
        price: float,
        vol: Optional[float],
        imbalance: Optional[float],
        vol_norm: Optional[float] = None,
    ):
        self._reset_if_new_day(ts)
        closed: List[TradeRecord] = []

        pos = self.positions.get(symbol)
        if pos:
            # active management
            self._update_trade_management(pos, price)
            self._update_adverse_ticks(pos, price)

            # quick partial TP (0.2% from entry) to secure fees + small profit
            if not pos.partial_taken:
                tp1_long = pos.entry_price * (1.0 + PARTIAL_TP_TRIGGER_PCT)
                tp1_short = pos.entry_price * (1.0 - PARTIAL_TP_TRIGGER_PCT)
                
                if pos.side == "LONG" and price >= tp1_long:
                    closed += self._close_partial(pos, ts, tp1_long, label="TP1_FAST")
                elif pos.side == "SHORT" and price <= tp1_short:
                    closed += self._close_partial(pos, ts, tp1_short, label="TP1_FAST")

            # exit reasons for the rest
            exit_price = None
            exit_label = ""

            # SL / TP
            if pos.side == "LONG":
                if price <= pos.sl_price:
                    exit_price = pos.sl_price
                    exit_label = "SL"
                elif pos.partial_taken and price >= pos.tp_price:
                    exit_price = pos.tp_price
                    exit_label = "TP"
            else:
                if price >= pos.sl_price:
                    exit_price = pos.sl_price
                    exit_label = "SL"
                elif pos.partial_taken and price <= pos.tp_price:
                    exit_price = pos.tp_price
                    exit_label = "TP"

            # smart INVALID_IMB: persistent adverse imbalance
            if exit_price is None:
                invalid_reason = self._check_invalid_imb_close(pos, imbalance, ts)
                if invalid_reason:
                    exit_price = price
                    exit_label = invalid_reason
                    logging.info(
                        "[%s] %s",
                        symbol.upper(),
                        invalid_reason,
                    )

            # NO_PROGRESS: trade that doesn't take off and gives back
            if exit_price is None:
                if self._check_no_progress(pos, price, ts):
                    exit_price = price
                    exit_label = "NO_PROGRESS"
                    logging.info(
                        "[%s] Closing with reason: %s",
                        symbol.upper(),
                        exit_label
                    )

            # Dynamic time-stop: close based on volatility
            if exit_price is None:
                time_in_trade = ts - pos.entry_ts
                # Use vol_norm to determine dynamic limit
                time_limit = self._get_time_stop_limit(vol_norm=vol_norm)
                
                if time_in_trade >= time_limit:
                    exit_price = price
                    exit_label = "TIME_STOP"
                    logging.info(
                        "[%s] TIME_STOP: trade live for %.1fs (limit=%.1fs)",
                        symbol.upper(),
                        time_in_trade,
                        time_limit,
                    )

            # full close
            if exit_price is not None and pos.qty > 0:
                closed += self._close_full(pos, ts, exit_price, label=exit_label)

        # we recompute equity with all known prices
        self._recompute_equity(prices={symbol: price})
        return closed

    def _close_partial(self, pos: Position, ts: float, exit_price: float, label: str):
        trades: List[TradeRecord] = []
        qty_close = pos.qty * PARTIAL_TP_FRACTION
        if qty_close <= 0:
            return trades

        pnl_price = (
            (exit_price - pos.entry_price) * qty_close
            if pos.side == "LONG"
            else (pos.entry_price - exit_price) * qty_close
        )

        # Only exit fee (taker) for the closed part
        exit_fee = exit_price * qty_close * self.fee_taker
        total_fees = exit_fee

        realized = pnl_price - total_fees

        self.balance += realized
        self._trades_today += 1

        tr = TradeRecord(
            symbol=pos.symbol,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            qty=qty_close,
            sl_price=pos.sl_price,
            tp_price=pos.tp_price,
            entry_ts=pos.entry_ts,
            exit_ts=ts,
            realized_pnl=realized,
            fees_paid=total_fees,
            reason=f"{label} | {pos.open_reason}",
        )
        self.trade_history.append(tr)

        pos.qty -= qty_close
        pos.partial_taken = True

        logging.info(
            "[%s] [PARTIAL %s %s] exit=%.2f qty=%.6f pnl=%.4f fees=%.4f balance=%.4f",
            pos.symbol.upper(),
            pos.side,
            label,
            exit_price,
            qty_close,
            realized,
            total_fees,
            self.balance,
        )
        return [tr]

    def _close_full(self, pos: Position, ts: float, exit_price: float, label: str):
        qty_close = pos.qty
        if qty_close <= 0:
            return []

        pnl_price = (
            (exit_price - pos.entry_price) * qty_close
            if pos.side == "LONG"
            else (pos.entry_price - exit_price) * qty_close
        )

        # Only exit fee (taker) for the closed part
        exit_fee = exit_price * qty_close * self.fee_taker
        total_fees = exit_fee

        realized = pnl_price - total_fees

        self.balance += realized
        self._trades_today += 1

        tr = TradeRecord(
            symbol=pos.symbol,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            qty=qty_close,
            sl_price=pos.sl_price,
            tp_price=pos.tp_price,
            entry_ts=pos.entry_ts,
            exit_ts=ts,
            realized_pnl=realized,
            fees_paid=total_fees,
            reason=f"{label} | {pos.open_reason}",
        )
        self.trade_history.append(tr)
        
        # Update last close timestamp for cooldown
        self.last_exit_ts[pos.symbol] = ts
        trades_hour = self.trades_last_hour.get(pos.symbol, [])
        trades_hour = [t for t in trades_hour if ts - t < 3600]
        trades_hour.append(ts)
        self.trades_last_hour[pos.symbol] = trades_hour

        logging.info(
            "[%s] [CLOSE %s %s] exit=%.2f qty=%.6f pnl=%.4f fees=%.4f balance=%.4f reason=%s",
            pos.symbol.upper(),
            pos.side,
            label,
            exit_price,
            qty_close,
            realized,
            total_fees,
            self.balance,
            pos.open_reason,
        )

        del self.positions[pos.symbol]
        return [tr]

