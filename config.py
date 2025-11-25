"""
Bot configuration for paper trading with an initial balance of 50 USD.
"""

# --- Market / Exchange ---

SYMBOLS = [
    "btcusdt",
    "ethusdt",
    "solusdt",
]

EXCHANGE = "binance_futures"
EMIT_INTERVAL = 1.0   # seconds between snapshots

# --- Global Capital Management ---

INITIAL_BALANCE = 50.0
LEVERAGE = 20

# Base risk per trade (for "normal" signals)
BASE_RISK_PER_TRADE = 0.03    # 3% of equity on standard trades

# Risk multiplier for "SNIPER" signals (very high score)
HIGH_CONV_RISK_MULT = 1.6      # ~4.8% on SNIPER trades (3% * 1.6)

# Limit of simultaneous total risk (sum of % of all open positions)
MAX_TOTAL_RISK_PCT_OPEN = 0.10 # maximum 10% simultaneous risk

# Daily loss limit:
DAILY_MAX_LOSS_PCT  = 0.06     # -6% of equity per day
DAILY_MAX_LOSS_ABS  = 3.0      # -3 absolute USDT per day

MAX_TRADES_PER_DAY = 35        # max trades per day (global)

# Frequency control per symbol
MAX_TRADES_PER_HOUR_PER_SYMBOL = 10   # Max 10 trades/hour for more activity
COOLDOWN_AFTER_TRADE_SEC = 5.0        # Wait 5s after closing a trade

# Minimum ratio between TP and total fees (entry+exit)
MIN_RR_VS_FEES = 3.0  # TP must be at least 3x the cost in fees

# Risk per trade (proportion of equity)
RISK_PER_TRADE = 0.03      # 3% - same as BASE_RISK_PER_TRADE

# Minimum risk:reward ratio (not counting fees)
MIN_RR = 1.5                # between 1.5 and 2.5 is reasonable for scalping

REWARD_RISK_BASE   = 2.5
SL_BUFFER_PCT      = 0.0008

# --- Fees (paper mode) ---

FEE_MAKER = 0.0002   # 0.02% for entry (limit / maker)
FEE_TAKER = 0.0004   # 0.04% for exit (market / taker)

# --- Tick size per symbol (for precise calculations) ---

TICK_SIZE = {
    "btcusdt": 0.1,
    "ethusdt": 0.01,
    "solusdt": 0.01,
}

# --- Score and entry trigger ---

# Minimum score to consider a candidate direction
SCORE_MIN_FOR_CANDIDATE = 45.0

# Minimum score per tick to count as "confirmation"
SCORE_CONFIRM_MIN = 50.0

# Threshold from which we consider a very strong signal (SNIPER)
SCORE_SNIPER_MIN = 90.0

# Consecutive ticks required with score >= SCORE_CONFIRM_MIN
CONFIRM_TICKS_REQUIRED = 0

# --- SCORE WEIGHTS ---
SCORE_WEIGHT_CVD = 35.0
SCORE_WEIGHT_IMBALANCE = 45.0
SCORE_WEIGHT_VOLUME = 15.0
SCORE_WEIGHT_CONFIRMATION = 5.0

# --- CVD Score Components ---
CVD_SCORE_STRONG_BULLISH = 25.0
CVD_SCORE_WEAK_BULLISH = 15.0
CVD_SCORE_NEUTRAL_BULLISH = 5.0
CVD_SCORE_ADVERSE_STRONG = -10.0

# --- Imbalance Score Components ---
IMBALANCE_SCORE_BASE = 26.0
IMBALANCE_SCORE_MAX = 40.0

# --- Volume Score Components ---
VOLUME_SCORE_MAX = 20.0

# --- Confirmation Score Components ---
CONFIRMATION_SCORE = 15.0

# --- CVD SLOPE THRESHOLD ---
CVD_SLOPE_THRESHOLD = 10.0


# --- Normalized volume filters ---

# Minimum normalized volume to operate (0–1 scale)
MIN_VOL_NORM = 0.3

# Score bonus if volume is very high
BONUS_VOL_NORM = 0.7

# --- Imbalance filters for entry validation ---
# (used on smoothed imbalance)

# Normal threshold to consider relevant imbalance
IMBALANCE_LONG_ENTRY = 0.40
IMBALANCE_SHORT_ENTRY = -0.40

# Extreme walls: activate guerrilla mode even with low volatility
IMBALANCE_LONG_EXTREME = 0.80
IMBALANCE_SHORT_EXTREME = -0.80

# --- Exit due to INVALID_IMB (persistent adverse imbalance) ---

INVALID_IMB_ENABLE = True  # Enable closing due to adverse imbalance

# |Imb_smooth| from which we consider the orderbook very adverse
INVALID_IMB_THRESHOLD = 0.95

# Number of consecutive samples with adverse Imb to close
INVALID_IMB_CONSEC_SAMPLES = 4

# Minimum time since entry to allow closing by INVALID_IMB
INVALID_IMB_MIN_AGE_SEC = 5.0

# Minimum adverse ticks from entry to accept INVALID_IMB
INVALID_IMB_MIN_ADVERSE_TICKS = 3

# --- Config per symbol (to adapt thresholds) ---

SYMBOL_CONFIG = {
    "btcusdt": {
        "cvd_div": 3.0,        # "visible" CVD divergence
        "min_imb": 0.24,       # minimum interesting imbalance
        "lookback_sec": 180,   # 3 minutes for extremes
    },
    "ethusdt": {
        "cvd_div": 3.5,
        "min_imb": 0.26,
        "lookback_sec": 150,   # 2.5 minutes
    },
    "solusdt": {
        "cvd_div": 4.0,
        "min_imb": 0.28,
        "lookback_sec": 120,   # 2 minutes
    },
}

DEFAULT_SYMBOL_CONFIG = {
    "cvd_div": 3.0,
    "min_imb": 0.25,
    "lookback_sec": 180,
}

# --- CVD slopes (in units of your cumulative CVD) ---

# Reasonable limits based on observation of real logs
CVD_SLOPE_STRONG_UP = 100.0    # strong buying flow
CVD_SLOPE_WEAK_UP = 30.0       # mild buying flow
CVD_SLOPE_STRONG_DOWN = -100.0 # strong selling flow
CVD_SLOPE_WEAK_DOWN = -30.0    # mild selling flow

# --- CVD Context (avoid going against the dominant flow) ---
CVD_CONTEXT_ENABLE = True
CVD_CONTEXT_WINDOW_TICKS = 60          # ~1 minute of history for context
CVD_CONTEXT_RATIO_THRESHOLD = 1.5      # how much larger the current CVD must be vs the average
CVD_CONTEXT_PENALTY_FACTOR = 0.5       # reduce CVD weight by half if it goes against the context

# Absolute CVD values to consider "dominant flow"
CVD_CONTEXT_ABS_MIN = 15000.0          # For ETH/SOL (adjust according to symbol)
CVD_CONTEXT_PENALTY = 40.0             # Penalty in score if it goes against the context

# --- ANTI-CHASE Filter (do not chase advanced movements) ---
ANTI_CHASE_ENABLE = True
ANTI_CHASE_LOOKBACK_TICKS = 5          # look ~5s back
ANTI_CHASE_MAX_MOVE_PCT = 0.0020       # 0.20% max movement allowed

# --- Volatility engine / stats ---

VOL_WINDOW_SIZE = 1000
VOL_RET_LOOKBACK = 60          # ~1 minute of returns to calculate vol

# --- Layered volatility system ---

# Completely dead market: only worth it if there is an extreme wall
VOLATILITY_HARD_MIN = 0.00003

# Min vol to consider entries in "low volatility" mode
VOLATILITY_MIN = 0.00004

# Normal vol to use full logic (trends, divergences, etc.)
VOLATILITY_NORMAL_MIN = 0.00020

# Maximum volatility before kill switch
VOLATILITY_MAX = 0.00250

# Minimum standard deviation of PRICE (not returns)
VOL_MIN_PRICE_STD = 3.0

KILL_SWITCH_VOL_MULTIPLIER = 4.0
KILL_SWITCH_COOLDOWN_SEC = 900 # 15 minutes without new entries if there is a spike

# --- Time stop and volatility classification ---

# Maximum lifetime of a position before closing due to timeout
MAX_TRADE_LIFETIME_SEC = 600.0

# Dynamic time-stop according to volatility
MAX_TRADE_LIFETIME_LOW_VOL_SEC = 600.0
MAX_TRADE_LIFETIME_HIGH_VOL_SEC = 300.0
VOL_NORM_HIGH_THRESHOLD = 0.7

# --- Exit for lack of progress (NO_PROGRESS) ---
NO_PROGRESS_EXIT_ENABLE = False
NO_PROGRESS_MIN_AGE_SEC = 15.0
NO_PROGRESS_MIN_MFE_PCT = 0.0005
NO_PROGRESS_GIVEBACK_PCT = 0.00025

# --- Active management (breakeven, trailing, partial TP) ---

BREAKEVEN_TRIGGER_PCT = 0.0025
TRAILING_STOP_PCT = 0.0010
PARTIAL_TP_FRACTION = 0.5

# TP1 to cover fees with 20x leverage
PARTIAL_TP_TRIGGER_PCT = 0.0025

# --- Allowed sessions (UTC) ---

ALLOWED_SESSIONS = [
    ("00:00", "23:59"),        # all day while testing
]

LOG_LEVEL = "INFO"

# --- Log paths ---

TRADES_CSV_PATH = "trades_paper_global.csv"
FEATURES_CSV_PATH = "features_snapshots.csv"
