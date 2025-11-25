import asyncio
import csv
import logging
from pathlib import Path
import time
from typing import List, Tuple

from config import (
    SYMBOLS,
    EMIT_INTERVAL,
    VOL_WINDOW_SIZE,
    VOL_RET_LOOKBACK,
    VOLATILITY_MAX,
    KILL_SWITCH_VOL_MULTIPLIER,
    KILL_SWITCH_COOLDOWN_SEC,
    ALLOWED_SESSIONS,
    LOG_LEVEL,
    TRADES_CSV_PATH,
    FEATURES_CSV_PATH,
)
from core.connector import BinanceConnector
from core.portfolio import GlobalPortfolio
from core.models import MarketSnapshot
from analysis.cvd_calc import CVDCalculator
from analysis.book_imbalance import order_book_imbalance
from analysis.market_stats import MarketStats
from strategy.scalper_logic import ScalperStrategy, get_symbol_cfg
from analysis.features_logger import FeaturesLogger
from display import display_status


def is_in_allowed_session(ts: float, sessions: List[Tuple[str, str]]):
    lt = time.gmtime(ts)
    cur_min = lt.tm_hour * 60 + lt.tm_min
    for start, end in sessions:
        sh, sm = map(int, start.split(":"))
        eh, em = map(int, end.split(":"))
        s_min = sh * 60 + sm
        e_min = eh * 60 + em
        if s_min <= cur_min <= e_min:
            return True
    return False


def ensure_trades_header(path: str):
    p = Path(path)
    if not p.exists():
        with p.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "symbol",
                    "side",
                    "entry_price",
                    "exit_price",
                    "qty",
                    "sl_price",
                    "tp_price",
                    "entry_ts",
                    "exit_ts",
                    "realized_pnl",
                    "fees_paid",
                    "reason",
                ]
            )


async def run_symbol_loop(
    symbol: str,
    portfolio: GlobalPortfolio,
    features_logger: FeaturesLogger,
) -> None:
    logging.info("Starting PAPER loop for %s", symbol.upper())

    connector = BinanceConnector(symbol=symbol, emit_interval=EMIT_INTERVAL)
    cvd_calc = CVDCalculator(maxlen=3600)
    stats = MarketStats(window_size=VOL_WINDOW_SIZE)
    strategy = ScalperStrategy(symbol=symbol, stats=stats)
    symbol_cfg = get_symbol_cfg(symbol)

    kill_until_ts = 0.0
    prev_vol: float | None = None

    async for snapshot in connector.stream():
        ts = snapshot.ts
        price = snapshot.mid_price

        cvd_current = cvd_calc.update_from_trades(snapshot.trades)
        
        imbalance = order_book_imbalance(
            snapshot.order_book.bids,
            snapshot.order_book.asks,
        )
        
        period_volume = sum(t.qty for t in snapshot.trades) if snapshot.trades else 0.0

        stats.update(price=price, cvd=cvd_current, imbalance=imbalance, volume=period_volume)
        vol = stats.get_volatility(n_points=VOL_RET_LOOKBACK)
        vol_norm = stats.get_volume_normalized(lookback=100)
        smooth_imbalance = stats.get_smooth_imbalance(window=3)
        cvd_slope = stats.get_cvd_slope(lookback=7)

        # Kill switch for absolute volatility
        if vol is not None and vol > VOLATILITY_MAX:
            kill_until_ts = ts + KILL_SWITCH_COOLDOWN_SEC
            logging.warning(f"[{symbol.upper()}] KILL SWITCH for absolute volatility activated until {int(kill_until_ts)}")

        # Kill switch for vol spike
        if vol is not None and prev_vol is not None and prev_vol > 0 and vol >= prev_vol * KILL_SWITCH_VOL_MULTIPLIER:
            kill_until_ts = ts + KILL_SWITCH_COOLDOWN_SEC
            logging.warning(f"[{symbol.upper()}] KILL SWITCH for volatility spike activated until {int(kill_until_ts)}")
        
        if vol is not None:
            prev_vol = vol

        # Position management
        closed_trades = portfolio.on_price_tick(
            symbol=symbol, ts=ts, price=price, vol=vol, imbalance=imbalance, vol_norm=vol_norm
        )

        if closed_trades:
            # Write closed trades to CSV
            for trade in closed_trades:
                with open(TRADES_CSV_PATH, "a", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        trade.symbol.upper(),
                        trade.side,
                        f"{trade.entry_price:.4f}",
                        f"{trade.exit_price:.4f}",
                        f"{trade.qty:.6f}",
                        f"{trade.sl_price:.4f}",
                        f"{trade.tp_price:.4f}",
                        f"{trade.entry_ts:.2f}",
                        f"{trade.exit_ts:.2f}",
                        f"{trade.realized_pnl:.6f}",
                        f"{trade.fees_paid:.6f}",
                        trade.reason,
                    ])
                logging.info(
                    "[TRADE SAVED] %s %s | PnL: %.4f USDT | Reason: %s",
                    trade.symbol.upper(),
                    trade.side,
                    trade.realized_pnl,
                    trade.reason,
                )

        in_session = is_in_allowed_session(ts, ALLOWED_SESSIONS)
        has_pos = portfolio.has_open_position(symbol)

        signal = None
        if ts >= kill_until_ts and in_session and not has_pos:
            signal = strategy.generate_signal(
                ts=ts,
                price=price,
                imbalance=imbalance,
            )

            if signal:
                portfolio.open_position(
                    symbol=symbol,
                    ts=ts,
                    price=price,
                    signal=signal,
                    recent_extreme_price=price, # Placeholder, adjust if necessary
                    entry_imbalance=imbalance,
                    side=signal.side,
                )
        
        # --- NEW TERMINAL DISPLAY ---
        # We only show the UI for the first symbol in the list to avoid overwriting the console
        if symbol == SYMBOLS[0]:
            display_status(
                symbol=symbol,
                price=price,
                cvd=cvd_current,
                imbalance=imbalance,
                vol=vol,
                portfolio=portfolio,
                strategy=strategy,
                signal=signal
            )

        # Feature logging is still useful for post-mortem analysis
        pos = portfolio.get_position(symbol)
        features_logger.log_snapshot(
            symbol=symbol, ts=ts, price=price, cvd=cvd_current, imbalance=imbalance, vol=vol,
            has_position=pos is not None, position_side=pos.side if pos else "",
            signal_side=signal.side if signal else None, signal_score=signal.score if signal else None,
            equity=portfolio.equity,
            smooth_imbalance=smooth_imbalance,
            vol_norm=vol_norm,
            cvd_slope=cvd_slope,
        )


async def run_all() -> None:
    portfolio = GlobalPortfolio()
    features_logger = FeaturesLogger(FEATURES_CSV_PATH)
    ensure_trades_header(TRADES_CSV_PATH)

    tasks = [
        asyncio.create_task(run_symbol_loop(symbol=s, portfolio=portfolio, features_logger=features_logger))
        for s in SYMBOLS
    ]
    await asyncio.gather(*tasks)


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        logging.info("Bot stopped by user.")


if __name__ == "__main__":
    main()