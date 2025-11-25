import numpy as np
from collections import deque
from typing import Optional, Deque

from config import CVD_CONTEXT_WINDOW_TICKS


class MarketStats:
    """Short-term stats for each symbol."""

    def __init__(self, window_size: int = 1000):
        self.prices: Deque[float] = deque(maxlen=window_size)
        self.cvd_history: Deque[float] = deque(maxlen=window_size)
        self.imbalances: Deque[float] = deque(maxlen=window_size)
        self.returns: Deque[float] = deque(maxlen=window_size)
        self.volumes: Deque[float] = deque(maxlen=window_size)  # New: for normalized vol
        
        # History of CVD values for context (smaller window)
        self.cvd_values: Deque[float] = deque(maxlen=CVD_CONTEXT_WINDOW_TICKS)

    def update(self, price: float, cvd: float, imbalance: float, volume: float = 0.0):
        # Calculate return before adding the new price
        if self.prices:
            prev = self.prices[-1]
            if prev > 0:
                ret = (price - prev) / prev
                self.returns.append(ret)
        
        self.prices.append(price)
        self.cvd_history.append(cvd)
        self.imbalances.append(imbalance)
        self.volumes.append(volume)
        
        # Save CVD for context
        self.cvd_values.append(cvd)

    def is_volatile_enough(self, min_std: float):
        if len(self.prices) < 50:
            return False
        std_dev = float(np.std(self.prices))
        return std_dev > min_std

    def get_dynamic_thresholds(self, default_pos: float, default_neg: float):
        if len(self.imbalances) < 100:
            return default_pos, default_neg
        arr = np.array(self.imbalances, dtype=float)
        buy_th = float(np.percentile(arr, 90))
        sell_th = float(np.percentile(arr, 10))
        return buy_th, sell_th

    def get_volatility(self, n_points: int) -> Optional[float]:
        """Calculates volatility as the std of returns."""
        if len(self.returns) < n_points:
            return None
        tail = list(self.returns)[-n_points:]
        if len(tail) < 2:
            return None
        return float(np.std(tail, ddof=1))

    def get_smooth_imbalance(self, window: int = 3) -> Optional[float]:
        """
        Returns the smoothed imbalance by averaging the last `window` ticks.
        - window=3 -> reacts quickly (3s) but avoids entering on a single spoofing spike.
        """
        if not self.imbalances:
            return None

        if len(self.imbalances) < window:
            # With few data, we use the last one to not block the bot
            return float(self.imbalances[-1])

        recent = list(self.imbalances)[-window:]
        return float(np.mean(recent))

    def get_cvd_slope(self, lookback: int = 7) -> Optional[float]:
        """
        CVD slope in the last `lookback` ticks.
        ~7 ticks with 1 Hz feed = ~7s window.
        > 0  -> dominant aggressive buying flow.
        < 0  -> dominant aggressive selling flow.
        """
        if len(self.cvd_history) <= lookback:
            return None

        return float(self.cvd_history[-1] - self.cvd_history[-1 - lookback])
    
    def get_last_cvd(self) -> Optional[float]:
        """Returns the last CVD value."""
        if not self.cvd_history:
            return None
        return float(self.cvd_history[-1])
    
    def get_volume_normalized(self, lookback: int = 100) -> Optional[float]:
        """
        Returns normalized volume (0–1) based on recent percentiles.
        - 0.0 = volume at percentile 0 (recent minimum)
        - 1.0 = volume at percentile 100 (recent maximum)
        """
        if len(self.volumes) < 20:  # Minimum data
            return None
        
        recent = list(self.volumes)[-lookback:] if len(self.volumes) >= lookback else list(self.volumes)
        if len(recent) < 2:
            return None
        
        current_vol = recent[-1]
        min_vol = float(np.min(recent))
        max_vol = float(np.max(recent))
        
        if max_vol <= min_vol:
            return 0.5  # Constant volume -> neutral
        
        normalized = (current_vol - min_vol) / (max_vol - min_vol)
        return float(np.clip(normalized, 0.0, 1.0))
