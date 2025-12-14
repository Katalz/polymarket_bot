"""
Gabagool-Lite Configuration
============================

Optimisé pour un capital de ~600$ avec stratégie maker-first.
"""

from dataclasses import dataclass, field
from typing import List
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass
class GabagoolLiteConfig:
    """Configuration complète pour la stratégie Gabagool-Lite."""
    
    # ============================================
    # CAPITAL & RISK MANAGEMENT
    # ============================================
    TOTAL_BANKROLL: float = 600.0           # Capital total disponible
    MAX_CAPITAL_PER_MARKET: float = 200.0   # Max engagé sur UN marché
    MAX_LOSS_PER_MARKET: float = 80.0       # Stop loss dur par marché
    
    # ============================================
    # ORDER SIZING
    # ============================================
    ORDER_USD: float = 3.0                   # Taille unitaire d'ordre (2-5$ recommandé)
    MIN_ORDER_USD: float = 2.0               # Minimum absolu
    MAX_ORDER_USD: float = 5.0               # Maximum par ordre
    MIN_SHARES: float = 5.0                  # Minimum de shares par ordre
    
    # ============================================
    # ORDER SAFETY & COMPLIANCE
    # ============================================
    MIN_NOTIONAL_USD: float = 1.25           # Minimum valid order value (~1$)
    MIN_PRICE_ALLOWED: float = 0.02          # Floor price (ignore below 2c)
    
    # ============================================
    # LADDER CONFIGURATION
    # ============================================
    N_LADDER_ORDERS: int = 4                 # Nombre d'ordres dans la ladder
    LADDER_TICK_SIZE: float = 0.01           # Écart entre niveaux (1 cent)
    LADDER_OFFSET_TICKS: int = 1             # Offset du best_bid pour p0
    REQUOTE_THRESHOLD: float = 0.02          # Re-quote si mid bouge de >2c
    
    # ============================================
    # ACCUMULATION TRIGGERS
    # ============================================
    
    # ============================================
    # HOUSE STRATEGY (Dynamic Fair Value)
    # ============================================
    REQUIRED_EDGE: float = 0.02              # Marge requise vs Fair Value pour accumuler
    MIN_HEDGE_LIQUIDITY_USD: float = 50.0    # Liquidité min requise en face avant d'entrer
    
    # HYBRID MOMENTUM (Legacy/Fallback)
    USE_HYBRID_MOMENTUM: bool = True
    D_REF: float = 200.0                     # Delta BTC reference ($)
    CONF_MIN: float = 0.60                   # Min confidence to enter
    CONF_EXIT: float = 0.30                  # Min confidence to hold/continue (anti-latch)
    HYBRID_EDGE_MIN: float = 0.01            # Edge required in Hybrid mode
    BTC_PRICE_SOURCE: str = "coinbase"       # coinbase, binance, etc.
    
    # LAG EXPLOITATION STRATEGY (New)
    LAG_DELTA_THRESHOLD: float = 120.0       # Price move required ($)
    LAG_SENSITIVITY: float = 0.0002          # Impact of price move on probability
    LAG_EDGE_ENTRY: float = 0.03             # Minimum edge (Fair - Ask)
    LAG_STABILITY_TIME: float = 60.0         # Seconds delta must remain high
    LAG_STABILITY_TICKS: int = 2             # Ticks edge must remain high
    LAG_FRESHNESS_MAX: float = 3.0           # Max age of price data (s) - Strict (was 30.0 for debug, 2.0 original)
    
    
    # Deprecated / Fallback
    ACCUMULATE_YES_THRESHOLD: float = 0.30   # Legacy
    ACCUMULATE_NO_THRESHOLD: float = 0.30    # Legacy
    
    # Alternative: seuil dynamique (deprecated logic but kept for struct)
    USE_DYNAMIC_THRESHOLD: bool = False
    DYNAMIC_THRESHOLD_SPREAD_MULT: float = 2.0
    
    # ============================================
    # HEDGE / LOCK LOGIC
    # ============================================
    HEDGE_TRIGGER_PRICE: float = 0.45        # Hedge si best_ask_opposite <= 0.45
    HEDGE_RATIO: float = 1.0                 # q_no_target = q_yes * ratio (1.0 = équilibré)
    LOCK_MARGIN: float = 0.02                # Lock si avg_yes + avg_no <= 1 - margin
    MIN_LOCK_PROFIT_USD: float = 0.50        # Profit minimum pour considérer "locké"
    
    # ============================================
    # TIMING & COOLDOWNS
    # ============================================
    TICK_INTERVAL_SEC: float = 0.2           # Intervalle boucle principale
    ORDER_COOLDOWN_SEC: float = 0.5          # Cooldown entre ordres (legacy)
    STOP_RECOVERY_SEC: float = 300.0         # Temps avant retry après stop (5 min)
    
    # Execution Limits
    MAX_ORDERS_PER_TICK: int = 2             # Max new orders created per tick
    COOLDOWN_AFTER_FILL: float = 5.0         # Seconds to wait after a fill before new orders
    
    
    # Time Gates (Seconds remaining)
    TIME_NO_ENTRY: float = 900.0             # T-15m: Plus de nouvelles positions (15m strategy = enter early)
    TIME_FORCE_EXIT: float = 60.0            # T-60s: Force unwind/exit
    
    # ============================================
    # MARKET SELECTION
    # ============================================
    MARKET_UNIVERSE: List[str] = field(default_factory=lambda: ["BTC", "ETH", "SOL", "XRP"])
    SCAN_INTERVAL_SEC: float = 5.0
    MIN_LIQUIDITY_USD: float = 50.0          # Liquidité minimum (top 5 levels)
    MAX_SPREAD: float = 0.04                 # Spread max acceptable (4c)
    
    # ============================================
    # STRATEGY THRESHOLDS (USD)
    # ============================================
    MIN_EXPOSURE_FOR_HEDGE: float = 15.0     # Min $ engaged before considering hedge
    MIN_PROFIT_TO_LOCK: float = 0.50         # Min $ guaranteed profit to switch to LOCKED
    
    # ============================================
    # MAKER-ONLY CONSTRAINTS
    # ============================================
    MAKER_ONLY: bool = True                  # Jamais de market order agressif
    FALLBACK_MARKETABLE_LIMIT: bool = False  # Si True, permet marketable limit en stop
    MAX_SLIPPAGE_FALLBACK: float = 0.03      # Slippage max pour fallback
    
    # ============================================
    # LOGGING
    # ============================================
    LOG_EVERY_SEC: float = 1.0
    LOG_DIR: str = "gabagool_lite_logs"
    VERBOSE: bool = True
    
    # ============================================
    # API CREDENTIALS (from env)
    # ============================================
    POLYGON_PRIVATE_KEY: str = field(default_factory=lambda: os.getenv("POLYGON_PRIVATE_KEY", ""))
    POLYMARKET_PROXY_ADDRESS: str = field(default_factory=lambda: os.getenv("POLYMARKET_PROXY_ADDRESS", ""))
    CLOB_API_KEY: str = field(default_factory=lambda: os.getenv("POLYMARKET_CLOB_API_KEY", ""))
    CLOB_API_SECRET: str = field(default_factory=lambda: os.getenv("POLYMARKET_CLOB_API_SECRET", ""))
    CLOB_API_PASSPHRASE: str = field(default_factory=lambda: os.getenv("POLYMARKET_CLOB_API_PASSPHRASE", ""))
    CLOB_HOST: str = field(default_factory=lambda: os.getenv("CLOB_HOST", "https://clob.polymarket.com"))


# ============================================
# CONFIGURATION RECOMMANDÉE POUR 600$
# ============================================

DEFAULT_CONFIG_600 = GabagoolLiteConfig(
    TOTAL_BANKROLL=600.0,
    MAX_CAPITAL_PER_MARKET=200.0,
    MAX_LOSS_PER_MARKET=80.0,
    MIN_ORDER_USD=1.3,       # Adjusted for Micro Validation
    MAX_ORDER_USD=1.5,       # Adjusted for Micro Validation
    ORDER_USD=1.5,           # Adjusted for Micro Validation
    N_LADDER_ORDERS=2,       # Reduce ladder steps
    MIN_NOTIONAL_USD=1.25,   # APi safety
    MIN_PRICE_ALLOWED=0.02,  # Price floor
    
    # Strategy
    REQUIRED_EDGE=0.02,      # 2 cents edge
    MIN_HEDGE_LIQUIDITY_USD=50.0,
    
    ACCUMULATE_YES_THRESHOLD=0.99,  # Disabled (Legacy)
    ACCUMULATE_NO_THRESHOLD=0.99,   # Disabled (Legacy)
    MIN_EXPOSURE_FOR_HEDGE=15.0,
    HEDGE_TRIGGER_PRICE=0.45,       # Stop accumulating if price spikes
    
    # Time
    TIME_NO_ENTRY=300.0,     # T-5m (Best for 15m markets)
    TIME_FORCE_EXIT=60.0,
)


# ============================================
# CONFIGURATION CONSERVATIVE (moins de trades)
# ============================================

CONSERVATIVE_CONFIG = GabagoolLiteConfig(
    TOTAL_BANKROLL=600.0,
    MAX_CAPITAL_PER_MARKET=150.0,
    MAX_LOSS_PER_MARKET=50.0,
    ORDER_USD=2.0,
    N_LADDER_ORDERS=3,
    ACCUMULATE_YES_THRESHOLD=0.25,
    ACCUMULATE_NO_THRESHOLD=0.25,
    HEDGE_TRIGGER_PRICE=0.40,
    LOCK_MARGIN=0.03,
    TIME_NO_ENTRY=600.0,
    MIN_EXPOSURE_FOR_HEDGE=20.0
)


# ============================================
# CONFIGURATION AGRESSIVE (plus de trades)
# ============================================

AGGRESSIVE_CONFIG = GabagoolLiteConfig(
    TOTAL_BANKROLL=600.0,
    MAX_CAPITAL_PER_MARKET=250.0,
    MAX_LOSS_PER_MARKET=100.0,
    ORDER_USD=5.0,
    N_LADDER_ORDERS=5,
    ACCUMULATE_YES_THRESHOLD=0.35,
    ACCUMULATE_NO_THRESHOLD=0.35,
    HEDGE_TRIGGER_PRICE=0.50,
    LOCK_MARGIN=0.01,
    TIME_NO_ENTRY=120.0
)
