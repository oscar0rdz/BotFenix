

import logging
from collections import deque
from typing import Deque, Tuple, Optional

import numpy as np

from core.models import Signal
from analysis.market_stats import MarketStats
from config import (
    SYMBOL_CONFIG,
    DEFAULT_SYMBOL_CONFIG,
    VOL_MIN_PRICE_STD,
    VOLATILITY_HARD_MIN,
    VOLATILITY_MIN,
    VOLATILITY_NORMAL_MIN,
    VOLATILITY_MAX,
    SCORE_MIN_FOR_CANDIDATE,
    SCORE_SNIPER_MIN,
    IMBALANCE_LONG_ENTRY,
    IMBALANCE_SHORT_ENTRY,
    IMBALANCE_LONG_EXTREME,
    IMBALANCE_SHORT_EXTREME,
    CVD_SLOPE_WEAK_UP,
    CVD_SLOPE_WEAK_DOWN,
    CVD_SLOPE_STRONG_UP,
    CVD_SLOPE_STRONG_DOWN,
    HIGH_CONV_RISK_MULT,
    MIN_VOL_NORM,
    BONUS_VOL_NORM,
    # New parameters
    ANTI_CHASE_ENABLE,
    ANTI_CHASE_LOOKBACK_TICKS,
    ANTI_CHASE_MAX_MOVE_PCT,
    CVD_CONTEXT_ENABLE,
    CVD_CONTEXT_RATIO_THRESHOLD,
    CVD_CONTEXT_PENALTY_FACTOR,
    CVD_CONTEXT_ABS_MIN,
    CVD_CONTEXT_PENALTY,
    CVD_SLOPE_THRESHOLD,
    # SCORE WEIGHTS
    SCORE_WEIGHT_CVD,
    SCORE_WEIGHT_IMBALANCE,
    SCORE_WEIGHT_VOLUME,
    # CVD Score Components
    CVD_SCORE_STRONG_BULLISH,
    CVD_SCORE_WEAK_BULLISH,
    CVD_SCORE_NEUTRAL_BULLISH,
    CVD_SCORE_ADVERSE_STRONG,
    # Imbalance Score Components
    IMBALANCE_SCORE_BASE,
    IMBALANCE_SCORE_MAX,
    # Volume Score Components
    VOLUME_SCORE_MAX,
)

def get_symbol_cfg(symbol: str):
    """Fetches symbol-specific configuration or returns the default."""
    return SYMBOL_CONFIG.get(symbol, DEFAULT_SYMBOL_CONFIG)


def classify_cvd(cvd_slope: float) -> str:
    """
    Classifies the given CVD slope into distinct market sentiment categories.
    This helps in quickly identifying the strength and direction of market orders.
    """
    if cvd_slope >= CVD_SLOPE_STRONG_UP:
        return "CVD_STRONG_BULLISH"
    if cvd_slope >= CVD_SLOPE_WEAK_UP:
        return "CVD_WEAK_BULLISH"
    if cvd_slope <= CVD_SLOPE_STRONG_DOWN:
        return "CVD_STRONG_BEARISH"
    if cvd_slope <= CVD_SLOPE_WEAK_DOWN:
        return "CVD_WEAK_BEARISH"
    return "CVD_NEUTRAL"


def _imbalance_score(imbalance: Optional[float]) -> Tuple[float, float]:
    """
    Calculates a score based on order book imbalance.
    The score increases as the imbalance crosses a predefined entry threshold,
    indicating stronger pressure from one side of the book.
    Returns a tuple of (long_score, short_score).
    """
    if imbalance is None:
        return 0.0, 0.0

    long_score = 0.0
    short_score = 0.0

    # Score for long signals increases when positive imbalance exceeds the entry threshold.
    if imbalance > 0 and IMBALANCE_LONG_ENTRY < 1.0:
        if imbalance > IMBALANCE_LONG_ENTRY:
            # Scale score between base and max based on how far imbalance is from the entry point.
            frac = (imbalance - IMBALANCE_LONG_ENTRY) / max(1e-9, 1.0 - IMBALANCE_LONG_ENTRY)
            frac = min(1.0, max(0.0, frac))
            long_score = IMBALANCE_SCORE_BASE + frac * (IMBALANCE_SCORE_MAX - IMBALANCE_SCORE_BASE)
            
    # Score for short signals follows the same logic for negative imbalance.
    if imbalance < 0 and IMBALANCE_SHORT_ENTRY < 0.0:
        if imbalance < IMBALANCE_SHORT_ENTRY:
            denom = IMBALANCE_SHORT_ENTRY - (-1.0)
            frac = (IMBALANCE_SHORT_ENTRY - imbalance) / max(1e-9, denom)
            frac = min(1.0, max(0.0, frac))
            short_score = IMBALANCE_SCORE_BASE + frac * (IMBALANCE_SCORE_MAX - IMBALANCE_SCORE_BASE)

    return long_score, short_score


class ScalperStrategy:
    """
    An order-flow scalping strategy that generates trading signals based on a weighted scoring system.
    The core idea is to identify moments of high conviction by analyzing three key pillars of market data:
    1.  **CVD (Cumulative Volume Delta):** Measures the net difference between buying and selling volume, indicating market sentiment and momentum.
    2.  **Book Imbalance:** Shows the pressure on the order book from limit orders, signaling potential short-term price moves.
    3.  **Volume:** Normalized trading volume to confirm market activity and participation.

    A signal is generated when the combined, weighted score of these factors surpasses a minimum threshold.
    The strategy also includes filters to avoid unfavorable conditions like extremely low volatility or chasing runaway prices.
    """

    def __init__(self, symbol: str, stats: MarketStats):
        self.symbol = symbol
        self.stats = stats

        cfg = get_symbol_cfg(symbol)
        self.cvd_div = cfg["cvd_div"]
        self.min_imb = cfg["min_imb"]
        self.lookback_sec = cfg["lookback_sec"]

    def _is_data_sufficient(self, vol: Optional[float], smooth_imb: Optional[float], cvd_slope: Optional[float]) -> bool:
        """Check if all necessary market data points are available to make a decision."""
        return vol is not None and smooth_imb is not None and cvd_slope is not None

    def _is_history_insufficient(self) -> bool:
        """Check if the bot has collected enough historical data to reliably calculate metrics."""
        prices = list(self.stats.prices)
        cvds = list(self.stats.cvd_history)
        return len(prices) < 50 or len(cvds) < 50

    def _calculate_cvd_score(self, cvd_slope: float) -> Tuple[float, float]:
        """
        Calculates a score (0-100) for long and short signals based on CVD slope.
        Stronger trends in market orders receive higher scores.
        """
        cvd_class = classify_cvd(cvd_slope)
        long_score, short_score = 0.0, 0.0

        if cvd_class == "CVD_STRONG_BULLISH":
            long_score = 100.0
        elif cvd_class == "CVD_WEAK_BULLISH":
            long_score = 60.0
        elif cvd_class == "CVD_STRONG_BEARISH":
            short_score = 100.0
        elif cvd_class == "CVD_WEAK_BEARISH":
            short_score = 60.0
        
        # Penalize signals if CVD is weak or neutral
        if abs(cvd_slope) < CVD_SLOPE_THRESHOLD:
            long_score *= 0.5
            short_score *= 0.5

        return long_score, short_score

    def _calculate_imbalance_score(self, imbalance: float) -> Tuple[float, float]:
        """
        Calculates a score (0-100) based on book imbalance.
        The score is scaled based on how much the imbalance surpasses the entry threshold.
        """
        long_score, short_score = _imbalance_score(imbalance)
        # Normalize the scores to a 0-100 scale. Max raw score is IMBALANCE_SCORE_MAX.
        norm_long = (long_score / IMBALANCE_SCORE_MAX) * 100 if IMBALANCE_SCORE_MAX > 0 else 0
        norm_short = (short_score / IMBALANCE_SCORE_MAX) * 100 if IMBALANCE_SCORE_MAX > 0 else 0
        return norm_long, norm_short

    def _calculate_volume_score(self, vol_norm: float, vol: float) -> float:
        """
        Calculates a score (0-100) based on normalized volume.
        Higher volume confirms market interest and results in a higher score.
        The score is penalized in dead or chaotic markets.
        """
        # Penalize if volatility is outside the optimal range
        if not (VOLATILITY_MIN < vol < VOLATILITY_MAX):
            return 0.0
        
        if vol_norm < MIN_VOL_NORM:
            return 0.0

        if vol_norm > BONUS_VOL_NORM:
            return 100.0
        
        # Scaled score for volume in the acceptable range
        score = ((vol_norm - MIN_VOL_NORM) / (BONUS_VOL_NORM - MIN_VOL_NORM)) * 100
        return max(0.0, min(100.0, score))


    def generate_signal(self, ts: float, price: float, imbalance: float) -> Optional[Signal]:
        """
        Generates a trading signal by calculating and weighting scores for CVD, imbalance, and volume.

        The process is as follows:
        1.  Gather up-to-date market metrics (volatility, CVD slope, etc.).
        2.  Perform preliminary checks to ensure data is sufficient and valid.
        3.  Calculate normalized scores (0-100) for the three main components: CVD, Imbalance, and Volume.
        4.  Combine these scores using predefined weights to get a final score for LONG and SHORT.
        5.  If a score exceeds the minimum threshold, a signal is generated and classified as 'STANDARD' or 'SNIPER'.
        """

        # --- 1. Get Metrics ---
        from config import VOL_RET_LOOKBACK
        vol = self.stats.get_volatility(n_points=VOL_RET_LOOKBACK)
        smooth_imb = self.stats.get_smooth_imbalance(window=3)
        cvd_slope = self.stats.get_cvd_slope(lookback=7)
        vol_norm = self.stats.get_volume_normalized(lookback=100)

        # --- 2. Preliminary Filters ---
        if not self._is_data_sufficient(vol, smooth_imb, cvd_slope) or vol_norm is None:
            return None
        
        if self._is_history_insufficient():
            return None

        # Anti-Chase Filter: Prevent entering a trade if the price has already moved too far, too fast.
        if ANTI_CHASE_ENABLE and len(self.stats.prices) > ANTI_CHASE_LOOKBACK_TICKS:
            price_past = self.stats.prices[-ANTI_CHASE_LOOKBACK_TICKS]
            pct_move = abs(price - price_past) / price_past
            if pct_move > ANTI_CHASE_MAX_MOVE_PCT:
                return None # Market is moving too fast, wait for a consolidation.

        # --- 3. Score Calculation (0-100 for each component) ---
        cvd_long, cvd_short = self._calculate_cvd_score(cvd_slope)
        imb_long, imb_short = self._calculate_imbalance_score(smooth_imb)
        vol_score = self._calculate_volume_score(vol_norm, vol)

        # --- 4. Final Weighted Score ---
        # Each component score (0-100) is multiplied by its weight.
        # The total is divided by the sum of weights to keep the final score in the 0-100 range.
        total_weight = SCORE_WEIGHT_CVD + SCORE_WEIGHT_IMBALANCE + SCORE_WEIGHT_VOLUME
        
        score_long = (cvd_long * SCORE_WEIGHT_CVD +
                      imb_long * SCORE_WEIGHT_IMBALANCE +
                      vol_score * SCORE_WEIGHT_VOLUME) / total_weight
        
        score_short = (cvd_short * SCORE_WEIGHT_CVD +
                       imb_short * SCORE_WEIGHT_IMBALANCE +
                       vol_score * SCORE_WEIGHT_VOLUME) / total_weight
        
        # --- 5. Decision Logic ---
        final_side = None
        final_score = 0.0

        if score_long >= SCORE_MIN_FOR_CANDIDATE and score_long > score_short:
            final_side = "LONG"
            final_score = score_long
        
        elif score_short >= SCORE_MIN_FOR_CANDIDATE and score_short > score_long:
            final_side = "SHORT"
            final_score = score_short

        if final_side:
            # Classify signal as 'SNIPER' for high scores, allowing for higher risk allocation.
            if final_score >= SCORE_SNIPER_MIN:
                risk_mult = HIGH_CONV_RISK_MULT
                score_class = "SNIPER"
            else:
                risk_mult = 1.0
                score_class = "STANDARD"
            
            reason_str = (
                f"SCORE_{final_side}={final_score:.1f} ({score_class}) | "
                f"CVD: {cvd_long if final_side == 'LONG' else cvd_short:.0f}, "
                f"Imb: {imb_long if final_side == 'LONG' else imb_short:.0f}, "
                f"Vol: {vol_score:.0f}"
            )
            
            logging.info(f"[{self.symbol.upper()}] Signal Generated: {final_side} | Score: {final_score:.2f} ({score_class})")
            
            return Signal(
                symbol=self.symbol,
                side=final_side,
                score=final_score,
                reason=reason_str,
                risk_mult=risk_mult,
                score_class=score_class,
            )
            
        return None

