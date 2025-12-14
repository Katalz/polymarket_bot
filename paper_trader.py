# paper_trader.py
# Gabagool-like: scale sur équilibre + edge, pas sur lock positif obligatoire.

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Any
import time
import math
import json
import os

# -----------------------------
# Config
# -----------------------------

@dataclass
class Config:
    # --- ENTRÉES : SOYEZ MOINS DIFFICILE ---
    # Gabagool achète même à 0.60$ si ça bouge. Ne attendez pas 0.45$.
    CHEAP_YES_MAX: float = 0.55  
    CHEAP_NO_MAX: float = 0.55

    # --- EDGE (LE SECRET DU VOLUME) ---
    # Avec 140€, vous devez tourner vite. N'attendez pas 5% de profit.
    # 0.5% (0.005) suffit pour accumuler.
    SCALE_EDGE_MIN: float = 0.005       
    SCALE_EDGE_STRONG: float = 0.03      

    # --- TOLÉRANCE DÉSÉQUILIBRE ---
    # Acceptez d'avoir 2 ou 3 parts de différence, ce n'est pas grave.
    EPS_QTY: float = 3.0                 

    # --- CAPITAL ($643) ---
    BANKROLL: float = 643.0
    BASE_ORDER_USD: float = 8.0          
    MAX_EXPOSURE_USD: float = 24.0       
    MAX_BULLETS: int = 3
    MAX_CONCURRENT: int = 1  # Logic check needed if enforced here

    # --- SAFETY ---
    DAILY_LOSS_LIMIT_USD: float = 20.0
    HARD_SAFETY_CAP: float = 30.0
    
    # --- EXITS ---
    TAKE_PROFIT_PCT: float = 0.12        # +12%
    FORCE_EXIT_SECONDS: int = 30 
    
    # --- EDGE & GATING ---
    EDGE_NET_ENTRY_MIN: float = -0.01   # +0.4%
    EDGE_NET_ADDON_MIN: float = 0.008    # +0.8%
    
    # --- EXECUTION ---
    SLIPPAGE: float = 0.01 
    FEE_RATE: float = 0.00
    MIN_SHARES: float = 10.0  # Approx $5-10
    COOLDOWN_SEC: float = 0.5
    ENABLE_SELLING: bool = True
    
    # Legacy
    SCALE_ORDER_USD: float = 8.0
    LOCK_KEEP_USD: float = -3.0 # Legacy allow


# -----------------------------
# Position + accounting
# -----------------------------

@dataclass
class Position:
    qty_yes: float = 0.0
    qty_no: float = 0.0
    cost_usd: float = 0.0  # coût total payé (incluant fees) - recettes si tu vends (ici on fait buy only)
    last_trade_ts: float = 0.0

    def has_two_legs(self) -> bool:
        return self.qty_yes > 0 and self.qty_no > 0

    def min_qty(self) -> float:
        return min(self.qty_yes, self.qty_no)

    def exposure_usd(self) -> float:
        # Sur marchés type YES/NO, ton "exposure" correspond au cash investi
        return self.cost_usd

    def locked_profit_usd(self) -> float:
        # Lock comptable simple: min(qY, qN) - coût
        # (La jambe min est celle qui "paye" le mieux en résolution)
        return self.min_qty() - self.cost_usd

    def imbalance(self) -> float:
        return abs(self.qty_yes - self.qty_no)

    def reduce(self, qty_sold: float, revenue_usd: float, side: str):
        """
        Réduit la position après une vente (SELL).
        Met à jour cost_usd (diminue) et qty.
        """
        if side == "SELL_YES":
            ratio = 0.0
            if self.qty_yes > 0:
                ratio = qty_sold / self.qty_yes
            self.qty_yes = max(0.0, self.qty_yes - qty_sold)
            
            # Recette vient réduire le coût (Cash In)
            # MAIS attention, pour le PnL realized, on devrait séparer.
            # Ici pour "exposure", on réduit simplement le cost du montant récupéré.
            # Si on a vendu à profit, cost peut devenir négatif (c'est normal, c'est du profit réalisé).
            self.cost_usd -= revenue_usd

        elif side == "SELL_NO":
            ratio = 0.0
            if self.qty_no > 0:
                ratio = qty_sold / self.qty_no
            self.qty_no = max(0.0, self.qty_no - qty_sold)
            self.cost_usd -= revenue_usd

    def worst_case_pnl(self) -> float:
        # Returns the guaranteed minimum PnL (Locked Profit or Max Loss)
        # Assuming payout is 1.0 per share.
        return min(self.qty_yes, self.qty_no) - self.cost_usd

    def __repr__(self) -> str:
        return (f"Position(q_yes={self.qty_yes:.4f}, q_no={self.qty_no:.4f}, "
                f"cost={self.cost_usd:.4f}, lock={self.locked_profit_usd():.4f}, "
                f"imb={self.imbalance():.4f})")


# -----------------------------
# Utilities
# -----------------------------

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def total_qty(pos: Position) -> float:
    return pos.qty_yes + pos.qty_no


def edge(price_yes: float, price_no: float) -> float:
    return 1.0 - (price_yes + price_no)


def projected_worst_case(
    pos: Position,
    add_yes: float = 0.0,
    add_no: float = 0.0,
    add_cost: float = 0.0,
) -> float:
    """
    Worst-case après un trade hypothétique.
    Utilisé pour bloquer un scale qui dégraderait le lock.
    """
    qy = pos.qty_yes + add_yes
    qn = pos.qty_no + add_no
    cost = pos.cost_usd + add_cost

    # Si une seule jambe après trade → pire cas = -coût total
    if qy <= 0 or qn <= 0:
        return -cost

    return min(qy, qn) - cost

def apply_fee_and_slippage(cfg: Config, px: float, side: str) -> float:
    """
    Ajuste un prix pour simuler slippage + fees.
    Pour un BUY: tu paies plus cher (slippage +) et fees +
    """
    if side.upper() == "BUY":
        px2 = px * (1.0 + cfg.SLIPPAGE)
        return px2 * (1.0 + cfg.FEE_RATE)
    else:
        px2 = px * (1.0 - cfg.SLIPPAGE)
        return px2 * (1.0 - cfg.FEE_RATE)

def usd_to_qty(usd: float, px_effective: float) -> float:
    if px_effective <= 0:
        return 0.0
    return usd / px_effective


# -----------------------------
# Strategy logic
# -----------------------------

@dataclass
class Decision:
    action: str                         # "BUY_YES", "BUY_NO", "HOLD"
    usd: float = 0.0
    reason: str = ""
    limit_px: float = 0.0               # Optional: Specific Limit Price for Execution

class GabagoolLikeStrategy:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.stop_engaged = False 
        self.bad_edge_ticks = 0 # PR Review: Smoothing counter for Quality Stop

    def can_trade(self, pos: Position) -> bool:
        return (time.time() - pos.last_trade_ts) >= self.cfg.COOLDOWN_SEC
        
    def scale_allowed(self, pos: Position, p_yes: float, p_no: float) -> (bool, str):
        if self.stop_engaged:
            return False, "scale_blocked:hard_stop_active"
            
        e = edge(p_yes, p_no)
        expo = pos.exposure_usd()
        
        # 1. Protection plafond absolu
        if expo >= self.cfg.MAX_EXPOSURE_USD:
            return False, f"[RISK] scale_blocked:exposure_cap exposure={expo:.2f} cap={self.cfg.MAX_EXPOSURE_USD}"

        # 2. STRATÉGIE DE VOLUME DYNAMIQUE (Le Secret APY)
        # Ratio d'utilisation du capital (ex: 0.10 pour 10%, 0.90 pour 90%)
        usage_ratio = expo / self.cfg.MAX_EXPOSURE_USD
        
        if usage_ratio < 0.20:
            required_edge = -0.015 # Très agressif au début
        elif usage_ratio < 0.50:
            required_edge = 0.005  # Standard
        else:
            required_edge = 0.03   # Sélectif à la fin

        if e < required_edge:
             return False, f"scale_blocked:edge_too_low({e:.3f}<{required_edge})"

        return True, "scale:allowed_exposure_ok"

    def check_exit_conditions(self, pos: Position, p_yes: float, p_no: float, time_rem: float) -> Optional[Decision]:
        """
        PR4: Active Exit Logic (Take Profit & Time Exit)
        """
        if not self.cfg.ENABLE_SELLING:
            return None
            
        # 1. TIME EXIT (T-30s)
        # On ne veut pas subir la volatilité finale de la résolution.
        # 1. TIME EXIT (T-30s)
        if time_rem <= self.cfg.FORCE_EXIT_SECONDS:
            # Cascading Logic
            # T-30s to T-5s: Aggressive Limit at Current Bid (Snap)
            # T-5s: Panic Market Sell (handled by default 0.0 limit in live bot if we send 0.0 here?)
            # Actually, we specify price.
            
            is_panic = (time_rem <= 5.0)
            tag = "panic_t5" if is_panic else f"limit_t{int(time_rem)}"
            
            # Target Price
            # If Panic: 0.0 (Live bot handles as 'Max Slippage')
            # If Limit: Current Bid
            px_yes = 0.0 if is_panic else p_yes # p_yes passed in check_exit is ASK? No deciding method passes ASK.
            # check_exit_conditions calls with p_yes, p_no.
            # In decide(): check_exit_conditions(pos, bid_yes, bid_no, ...)
            # So p_yes IS BID YES.
            
            target_px_yes = 0.0 if is_panic else p_yes
            target_px_no = 0.0 if is_panic else p_no
            
            if pos.qty_yes > 1.0:
                 return Decision("SELL_YES", pos.qty_yes, f"[EXIT] time_{tag}", limit_px=target_px_yes)
            if pos.qty_no > 1.0:
                 return Decision("SELL_NO", pos.qty_no, f"[EXIT] time_{tag}", limit_px=target_px_no)
        
        # 2. TAKE PROFIT (Sniper)
        # On calcule le PnL latent. 
        # Note: cost_usd peut être partagé, mais ici on est en directional (une seule jambe dominante).
        
        # Cas YES dominant
        if pos.qty_yes > pos.qty_no + self.cfg.EPS_QTY:
            # Valeur actuelle de la position YES
            market_val = pos.qty_yes * p_yes
            # PnL approx (on suppose cost_usd est 100% YES ici car directional)
            if pos.cost_usd > 0:
                pnl_pct = (market_val - pos.cost_usd) / pos.cost_usd
                if pnl_pct >= self.cfg.TAKE_PROFIT_PCT:
                    return Decision("SELL_YES", pos.qty_yes, f"[EXIT] take_profit pnl={pnl_pct*100:.1f}% raw_val={market_val:.2f}$")

        # Cas NO dominant
        elif pos.qty_no > pos.qty_yes + self.cfg.EPS_QTY:
             market_val = pos.qty_no * p_no
             if pos.cost_usd > 0:
                pnl_pct = (market_val - pos.cost_usd) / pos.cost_usd
                if pnl_pct >= self.cfg.TAKE_PROFIT_PCT:
                    return Decision("SELL_NO", pos.qty_no, f"[EXIT] take_profit pnl={pnl_pct*100:.1f}% raw_val={market_val:.2f}$")
        
        return None

    def decide(self, pos: Position, ask_yes: float, ask_no: float, bid_yes: float, bid_no: float, time_remaining: float = 900) -> Decision:
        # --- 1) METRICS & SANITIZATION ---
        ask_yes = clamp(ask_yes, 0.0001, 0.9999)
        ask_no = clamp(ask_no, 0.0001, 0.9999)
        bid_yes = clamp(bid_yes, 0.0001, 0.9999)
        bid_no = clamp(bid_no, 0.0001, 0.9999)
        
        # Edge (Ask basis)
        edge_raw = edge(ask_yes, ask_no)
        spread_cost = max(0.00, (ask_yes + ask_no) - 1.0)
        edge_net = edge_raw - spread_cost
        
        # Valuation (Bid basis) for Stop/Risk
        unrealized_pnl = (pos.qty_yes * bid_yes + pos.qty_no * bid_no) - pos.cost_usd
        total_q = pos.qty_yes + pos.qty_no
        
        # --- 2) EXITS (WATERFALL) ---
        # Low-level check
        exit_dec = self.check_exit_conditions(pos, bid_yes, bid_no, time_remaining)
        if exit_dec:
             exit_dec.reason = f"{exit_dec.reason} [STATS] raw={edge_raw:.3f} spr={spread_cost:.3f} net={edge_net:.3f} pnl={unrealized_pnl:.2f}"
             return exit_dec
             
        # --- 3) STOP LOGIC (STATE MACHINE) ---
        # A. Risk Stop: Circuit Breaker (-12$)
        if total_q > 0 and unrealized_pnl <= -12.0:
             if not self.stop_engaged:
                  self.stop_engaged = True
                  # Fallthrough

        # B. Quality Stop: Net Edge < -0.01 for 8 ticks
        if total_q > 0 and edge_net < -0.01:
             self.bad_edge_ticks += 1
             if self.bad_edge_ticks >= 8:
                 self.stop_engaged = True
        else:
             self.bad_edge_ticks = 0

        # --- 4) IF STOP ENGAGED ---
        if self.stop_engaged:
             # COLLAPSE EXIT: Ultimate Bailout
             if edge_net < -0.05 and unrealized_pnl < -8.0:
                  if pos.qty_yes > 1: return Decision("SELL_YES", pos.qty_yes, f"[EXIT] collapse_bailout net={edge_net:.3f} pnl={unrealized_pnl:.2f}")
                  if pos.qty_no > 1: return Decision("SELL_NO", pos.qty_no, f"[EXIT] collapse_bailout net={edge_net:.3f} pnl={unrealized_pnl:.2f}")
             
             return Decision("HOLD", 0.0, f"hold:stop_engaged [STATS] net={edge_net:.3f} pnl={unrealized_pnl:.2f}")
             
        # --- 5) ENTRY & ADD-ON GATES ---
        is_entry = (total_q < 1.0)
        
        # A. Gating Check
        required_edge = self.cfg.EDGE_NET_ENTRY_MIN if is_entry else self.cfg.EDGE_NET_ADDON_MIN
        if edge_net <= required_edge:
             return Decision("HOLD", 0.0, f"hold:no_value [STATS] net={edge_net:.3f} < req={required_edge:.3f}")

        # B. Max Exposure Check
        if (pos.cost_usd + self.cfg.BASE_ORDER_USD) > self.cfg.MAX_EXPOSURE_USD:
             return Decision("HOLD", 0.0, f"hold:max_exposure [STATS] cost={pos.cost_usd:.2f} > max={self.cfg.MAX_EXPOSURE_USD}")
             
        # --- 6) EXECUTION (SNIPER) ---
        msg = f"sniper:fire [STATS] net={edge_net:.3f} spr={spread_cost:.3f}"
        
        # Side Selection
        target_side = "BUY_YES"
        target_price = ask_yes
        if pos.qty_no > pos.qty_yes:
             target_side = "BUY_YES"
             target_price = ask_yes
        elif pos.qty_yes > pos.qty_no:
             target_side = "BUY_NO"
             target_price = ask_no
        else:
             target_side = "BUY_YES"
             target_price = ask_yes
             
        # Min Shares Logic
        req_usd = max(self.cfg.BASE_ORDER_USD, self.cfg.MIN_SHARES * target_price * 1.01)
        
        # Safety Cap
        if req_usd > self.cfg.HARD_SAFETY_CAP:
             return Decision("HOLD", 0.0, f"hold:safety_cap {req_usd:.2f} > {self.cfg.HARD_SAFETY_CAP}")
             
        return Decision(target_side, req_usd, msg)


# -----------------------------
# Paper execution layer
# -----------------------------

class PaperTrader:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.pos = Position()
        self.strategy = GabagoolLikeStrategy(cfg)

    def step(self, p_yes: float, p_no: float) -> Dict[str, Any]:
        d = self.strategy.decide(self.pos, p_yes, p_no, 0.0, 0.0) # Dummy bids

        log: Dict[str, Any] = {
            "ts": time.time(),
            "p_yes": p_yes,
            "p_no": p_no,
            "edge": edge(p_yes, p_no),
            "action": d.action,
            "usd": d.usd,
            "reason": d.reason,
            "pos_before": repr(self.pos),
        }

        if d.action == "HOLD" or d.usd <= 0:
            log["pos_after"] = repr(self.pos)
            return log

        if not self.strategy.can_trade(self.pos):
            log["action"] = "HOLD"
            log["reason"] = "cooldown"
            log["pos_after"] = repr(self.pos)
            return log

        # Execute BUY
        if d.action == "BUY_YES":
            px_eff = apply_fee_and_slippage(self.cfg, p_yes, "BUY")
            qty = usd_to_qty(d.usd, px_eff)
            self.pos.qty_yes += qty
            self.pos.cost_usd += d.usd
            self.pos.last_trade_ts = time.time()
            log["fill_px_eff"] = px_eff
            log["fill_qty"] = qty

        elif d.action == "BUY_NO":
            px_eff = apply_fee_and_slippage(self.cfg, p_no, "BUY")
            qty = usd_to_qty(d.usd, px_eff)
            self.pos.qty_no += qty
            self.pos.cost_usd += d.usd
            self.pos.last_trade_ts = time.time()
            log["fill_px_eff"] = px_eff
            log["fill_qty"] = qty

        log["pos_after"] = repr(self.pos)
        log["lock_usd"] = self.pos.locked_profit_usd()
        log["imbalance"] = self.pos.imbalance()
        log["exposure_usd"] = self.pos.exposure_usd()
        return log


# -----------------------------
# Example usage (remplace par ton feed de prix)
# -----------------------------
if __name__ == "__main__":
    cfg = Config(
        SCALE_EDGE_MIN=0.04,
        EPS_QTY=0.05,
        LOCK_KEEP_USD=-0.50,
        BASE_ORDER_USD=2.0,
        SCALE_ORDER_USD=2.0,
        COOLDOWN_SEC=0.5,
    )
    bot = PaperTrader(cfg)

    # Exemple: flux de prix (p_yes, p_no)
    prices = [
        (0.52, 0.44),  # edge=0.04 -> entrée edge-first sur NO (moins cher)
        (0.51, 0.43),
        (0.50, 0.42),
        (0.49, 0.41),  # edge=0.10 -> scale fort possible si équilibré
        (0.48, 0.40),
    ]

    for (py, pn) in prices:
        out = bot.step(py, pn)
        print(out["action"], out["usd"], out["reason"], out["pos_after"])
        time.sleep(0.2)

# -------------------------------------------------------------------
# Live adapter (compat avec live_arbitrage_bot.py)
# live attend: decision.side, decision.size_yes, decision.size_no, decision.reason
# -------------------------------------------------------------------

def reset_live_strategy() -> None:
    """Resets the global strategy instance for a new market."""
    global _LIVE_STRAT
    _LIVE_STRAT = GabagoolLikeStrategy(_LIVE_CFG)
    
STATE_FILE = "trader_state.json"

def load_persistence(slug: str) -> bool:
    """
    Loads persisted state for a specific market slug.
    Returns True if stop_engaged was restored.
    """
    if not os.path.exists(STATE_FILE):
        return False
        
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
            
        market_state = data.get(slug, {})
        if market_state.get("stop_engaged", False):
            _LIVE_STRAT.stop_engaged = True
            print(f"[PERSIST] Restored STOP state for {slug}")
            return True
            
    except Exception as e:
        print(f"[PERSIST] Load error: {e}")
        
    return False

def save_persistence(slug: str) -> None:
    """
    Saves current strategy state (specifically stop_engaged) for the slug.
    """
    try:
        data = {}
        if os.path.exists(STATE_FILE):
             try:
                 with open(STATE_FILE, "r") as f:
                     data = json.load(f)
             except: pass # corrupted file?
             
        # Update slug
        data[slug] = {
            "stop_engaged": _LIVE_STRAT.stop_engaged,
            "ts": time.time()
        }
        
        with open(STATE_FILE, "w") as f:
            json.dump(data, f, indent=2)
            
    except Exception as e:
        print(f"[PERSIST] Save error: {e}")

@dataclass(frozen=True)
class LiveDecision:
    side: str
    size_yes: float
    size_no: float
    reason: str
    limit_price: float = 0.0

_LIVE_CFG = Config()
_LIVE_STRAT = GabagoolLikeStrategy(_LIVE_CFG)

def set_live_config(cfg: Config) -> None:
    global _LIVE_CFG, _LIVE_STRAT
    _LIVE_CFG = cfg
    _LIVE_STRAT = GabagoolLikeStrategy(_LIVE_CFG)

LIVE_MIN_SHARES: float = 5.0
LIVE_TAKER_SLIPPAGE: float = 0.03  # doit matcher TAKER_SLIPPAGE côté live

# cooldown live (anti spam) — armé via arm_live_cooldown() après trade ok
_LIVE_LAST_TRADE_TS: float = 0.0

def arm_live_cooldown(ts: Optional[float] = None) -> None:
    """
    A appeler UNIQUEMENT quand un ordre est réellement accepted/filled (ok=True).
    Exemple dans run():
        ok = execute_trade(...)
        if ok: arm_live_cooldown()
    """
    global _LIVE_LAST_TRADE_TS
    _LIVE_LAST_TRADE_TS = float(time.time() if ts is None else ts)

def _min_ticket_usd(px: float) -> float:
    """
    Worst-case minimum ticket si ton live force MIN_SHARES à limit_px.
    """
    px = clamp(float(px), 0.0001, 0.9999)
    limit_px = min(px + LIVE_TAKER_SLIPPAGE, 0.99)
    return LIVE_MIN_SHARES * limit_px

def decide_trade(
    *,
    price_yes: float,
    price_no: float,
    bid_yes: float = 0.0,
    bid_no: float = 0.0,
    qty_yes: float,
    qty_no: float,
    cost_yes: float,
    cost_no: float,
    worst_case_pnl_current: float = 0.0,
    time_to_expiry: float = 0.0,
    total_market_duration: float = 0.0,
    btc_price: float = 0.0,
    btc_trend: float = 0.0,
    base_order_size: float = 6.0,
) -> LiveDecision:
    """
    Adapter live:
    - reconstruit Position (buy-only) à partir des inputs live
    - applique stratégie paper
    - anti-spam: cooldown LIVE basé sur _LIVE_LAST_TRADE_TS (dernier trade réellement exécuté)
    - sécurité MIN_SHARES: si min_ticket > base_order_size => HOLD (sinon risque sur-dépense)
    - IMPORTANT: le cooldown est armé APRES succès via arm_live_cooldown()
    """
    # Position "vue live"
    pos = Position(
        qty_yes=float(qty_yes),
        qty_no=float(qty_no),
        cost_usd=float(cost_yes + cost_no),
        last_trade_ts=0.0,  # cooldown géré via _LIVE_LAST_TRADE_TS
    )

    d = _LIVE_STRAT.decide(
        pos, 
        float(price_yes), 
        float(price_no), 
        float(bid_yes), 
        float(bid_no), 
        float(time_to_expiry)
    )

    # HOLD direct
    if d.action == "HOLD" or float(d.usd) <= 0.0:
        return LiveDecision("HOLD", 0.0, 0.0, d.reason)

    # cooldown live anti-spam (basé sur dernier trade réellement exécuté)
    if (time.time() - float(_LIVE_LAST_TRADE_TS)) < float(_LIVE_CFG.COOLDOWN_SEC):
        return LiveDecision("HOLD", 0.0, 0.0, "hold:cooldown_live")

    base_order_size = float(base_order_size)
    limit_px = getattr(d, "limit_px", 0.0)

    # ---- BUY_YES ----
    if d.action == "BUY_YES":
        min_ticket = _min_ticket_usd(price_yes)
        if base_order_size < min_ticket:
            return LiveDecision("HOLD", 0.0, 0.0, f"hold:min_ticket_yes>{base_order_size:.2f}")

        usd = min(float(d.usd), base_order_size)
        if usd <= 0.0:
            return LiveDecision("HOLD", 0.0, 0.0, "hold:usd_zero")

        return LiveDecision("BUY_YES", usd, 0.0, d.reason, limit_price=limit_px)

    # ---- BUY_NO ----
    if d.action == "BUY_NO":
        min_ticket = _min_ticket_usd(price_no)
        if base_order_size < min_ticket:
            return LiveDecision("HOLD", 0.0, 0.0, f"hold:min_ticket_no>{base_order_size:.2f}")

        usd = min(float(d.usd), base_order_size)
        if usd <= 0.0:
            return LiveDecision("HOLD", 0.0, 0.0, "hold:usd_zero")

        return LiveDecision("BUY_NO", 0.0, usd, d.reason, limit_price=limit_px)

    # ---- SELL_YES (PR4) ----
    if d.action == "SELL_YES":
        qty = float(d.usd)
        if qty <= 0: return LiveDecision("HOLD", 0.0, 0.0, "hold:sell_qty_zero")
        return LiveDecision("SELL_YES", qty, 0.0, d.reason, limit_price=limit_px)

    # ---- SELL_NO (PR4) ----
    if d.action == "SELL_NO":
        qty = float(d.usd)
        if qty <= 0: return LiveDecision("HOLD", 0.0, 0.0, "hold:sell_qty_zero")
        return LiveDecision("SELL_NO", 0.0, qty, d.reason, limit_price=limit_px)

    return LiveDecision("HOLD", 0.0, 0.0, d.reason)