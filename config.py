"""
config.py - Configuration GABAGOOL CLEAN (paper + live)

- MAX_EXPOSURE_PER_MARKET: expo totale max par marché
- MAX_LOSS_PER_MARKET: perte max tolérée UNE FOIS que 2 jambes existaient déjà
- START_EDGE_MIN / SCALE_EDGE_MIN: garde-fous non-trading
- SCALE params: scale après lock (mais pas trop restrictif)
"""

import os
from dotenv import load_dotenv

load_dotenv()

# =========
# Live keys
# =========
POLYGON_PRIVATE_KEY = os.getenv("POLYGON_PRIVATE_KEY")
POLYMARKET_PROXY_ADDRESS = os.getenv("POLYMARKET_PROXY_ADDRESS")

CLOB_API_KEY = os.getenv("CLOB_API_KEY")
CLOB_API_SECRET = os.getenv("CLOB_API_SECRET")
CLOB_API_PASSPHRASE = os.getenv("CLOB_API_PASSPHRASE")
CLOB_HOST = os.getenv("CLOB_HOST", "https://clob.polymarket.com")

# =========================
# Paper base
# =========================
PAPER_START_BALANCE = 1000.0

# =========================
# Risque / expo
# =========================
# Trop bas à 12 => tu bloques presque tout (et tu ne scales jamais comme lui)
# Trop bas à 12 => tu bloques presque tout (et tu ne scales jamais comme lui)
# MAX_EXPOSURE_PER_MARKET is deprecated. Use MAX_EXPOSURE_USD.
# Sizing Pilot $140: 16.50$ allow 3 bullets of ~5.50$.
MAX_EXPOSURE_USD = 16.50       
BASE_ORDER_USD = 5.0          # Base Ticket

# =========================
# EXIT STRATEGY (PR4: ACTIVE TRADING)
# =========================
ENABLE_SELLING: bool = True
TAKE_PROFIT_PCT: float = 0.25        # +25% Gain -> SELL EVERYTHING
FORCE_EXIT_SECONDS: int = 30         # Sell at T-30s to avoid final volatility

# --- VITESSE ---
# Tirez vite.cket

# Gabagool tolère des worst-case plus profonds avant d'arrêter le scale
# (appliqué seulement si 2 jambes existaient déjà avant le trade)
MAX_LOSS_PER_MARKET = 18.0

# =========================
# GLOBAL SAFETY (CIRCUIT BREAKERS)
# =========================
DAILY_LOSS_LIMIT_USD: float = 35.0   # Stop bot if cumulative loss > $35 (25% bankroll)
MAX_CONSECUTIVE_LOSSES: int = 3      # Stop bot if 3 losing trades in a row

# =========================
# Live sizing
# =========================
# --- ENTRÉES : MODE SNIPER (ANTI-CASINO) ---
# On n'achète plus les "coin flips" à 0.54. On veut du vrai discount.
CHEAP_YES_MAX: float = 0.49  
CHEAP_NO_MAX: float = 0.49

# --- EDGE (LE SECRET DU VOLUME) ---
# On filtre un peu plus le bruit.
SCALE_EDGE_MIN: float = 0.015       
SCALE_EDGE_STRONG: float = 0.04      

# --- TOLÉRANCE DÉSÉQUILIBRE ---
# Base ticket du bot live (sert de plafond par trade via compute_trade_cap())
MAX_ORDER_AMOUNT_USDC = 3.0

# =========================
# Non-trading (edge)
# =========================
# Gabagool entre/sale même avec un edge modéré
START_EDGE_MIN = 0.010   # 1.0 %
LOG_EVERY_SEC = 2.0

# =========================
# Scale mode
# =========================
# IMPORTANT: si LOCK_ENTER/KEEP trop petits, "scale mode" ne reflète rien
# -> on les met assez hauts pour ne scaler que quand la position a du lock réel
LOCK_ENTER_USD = 0.40
LOCK_KEEP_USD  = 0.20

# Par scale : typiquement 2$ à 6$ (fréquence > taille)
SCALE_MAX_ORDER_USD = 6.0

# Proportion du surplus de lock utilisée (si tu laisses cette logique)
SCALE_FRACTION = 0.60

# Cheap zone : là où le scale a le meilleur profil de convexité
SCALE_CHEAP_THRESHOLD = 0.30

# (Optionnel, pas utilisé dans ton paper_trader actuel)
SCALE_COOLDOWN_TICKS = 15

# Stop conditions
SCALE_STOP_EXPOSURE_USD = 110.0
SCALE_STOP_LOCK_USD = 9999.0  # on désactive le stop lock (sinon tu bloques trop tôt)
SCALE_STOP_TIME_TO_END_SEC = 90
