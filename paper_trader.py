# paper_trader.py
# Gabagool-like: scale sur équilibre + edge, pas sur lock positif obligatoire.

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Any
import time
import math

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

    # --- GARDE-FOUS (Loose) ---
    # Autorisez le bot à "perdre" virtuellement 3$ au début pour construire la position
    LOCK_KEEP_USD: float = -3.0          
    
    # Votre capital réel (laissez une marge de sécurité)
    MAX_EXPOSURE_USD: float = 16.50       

    # --- SIZING (CRUCIAL POUR 140€) ---
    # Il faut être au-dessus du minimum Polymarket (souvent 5$ notionnel ou 5 shares)
    # 5.0$ est le minimum floor.
    BASE_ORDER_USD: float = 5.0          
    SCALE_ORDER_USD: float = 5.0        
    MIN_SHARES: float = 5.0        # Minimum requis par Polymarket
    HARD_SAFETY_CAP: float = 15.0  # Sécurité absolue : on ne paie jamais plus de 15$ pour un ticket
    # --- VITESSE ---
    # Tirez vite.
    COOLDOWN_SEC: float = 0.2
    # Simulation (mets une petite fee/slippage si tu veux un backtest réaliste)
    FEE_RATE: float = 0.001              # 0.10%
    SLIPPAGE: float = 0.001              # 0.10%

    # --- EXIT STRATEGY (PR4) ---
    ENABLE_SELLING: bool = True
    TAKE_PROFIT_PCT: float = 0.25        # +25% Unrelized PnL -> SELL ALL
    FORCE_EXIT_SECONDS: int = 30         # Sell all at T-30s

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

    def __repr__(self) -> str:
        return (f"Position(q_yes={self.qty_yes:.4f}, q_no={self.qty_no:.4f}, "
                f"cost={self.cost_usd:.4f}, lock={self.locked_profit_usd():.4f}, "
                f"imb={self.imbalance():.4f})")


# -----------------------------
# Utilities
# -----------------------------

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

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

class GabagoolLikeStrategy:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def can_trade(self, pos: Position) -> bool:
        return (time.time() - pos.last_trade_ts) >= self.cfg.COOLDOWN_SEC

    def scale_allowed(self, pos: Position, p_yes: float, p_no: float) -> (bool, str):
        e = edge(p_yes, p_no)
        expo = pos.exposure_usd()
        
        # 1. Protection plafond absolu
        if expo >= self.cfg.MAX_EXPOSURE_USD:
            return False, f"[RISK] scale_blocked:exposure_cap exposure={expo:.2f} cap={self.cfg.MAX_EXPOSURE_USD}"

        # 2. STRATÉGIE DE VOLUME DYNAMIQUE (Le Secret APY)
        # Ratio d'utilisation du capital (ex: 0.10 pour 10%, 0.90 pour 90%)
        usage_ratio = expo / self.cfg.MAX_EXPOSURE_USD
        
        # Seuil d'edge requis dynamique :
        # - Au début (0% utilisé) : On accepte -1% d'edge (on paie le spread pour entrer)
        # - Au milieu (50% utilisé) : On veut 0.5% d'edge positif
        # - À la fin (90% utilisé) : On veut 3% d'edge (cherry picking)
        
        if usage_ratio < 0.20:
            required_edge = -0.015 # Très agressif au début
        elif usage_ratio < 0.50:
            required_edge = 0.005  # Standard
        else:
            required_edge = 0.03   # Sélectif à la fin

        if e < required_edge:
             return False, f"scale_blocked:edge_too_low({e:.3f}<{required_edge})"

        # 3. Vérification de l'équilibre (SUPPRIMÉE pour Gabagool Directionnel)
        # On n'exige plus d'avoir 2 jambes ni un Locked Profit positif.
        # Seul le Risk Cap (Max Exposure) compte (déjà vérifié ci-dessus).
        
        return True, "scale:allowed_exposure_ok"

    def check_exit_conditions(self, pos: Position, p_yes: float, p_no: float, time_rem: float) -> Optional[Decision]:
        """
        PR4: Active Exit Logic (Take Profit & Time Exit)
        """
        if not self.cfg.ENABLE_SELLING:
            return None
            
        # 1. TIME EXIT (T-30s)
        # On ne veut pas subir la volatilité finale de la résolution.
        if time_rem <= self.cfg.FORCE_EXIT_SECONDS:
            if pos.qty_yes > 1.0: # Petite tolérance pour dust
                 return Decision("SELL_YES", pos.qty_yes, f"[EXIT] time_force_exit t={time_rem:.0f}s")
            if pos.qty_no > 1.0:
                 return Decision("SELL_NO", pos.qty_no, f"[EXIT] time_force_exit t={time_rem:.0f}s")
        
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

    def decide(self, pos: Position, p_yes: float, p_no: float, time_remaining: float = 900) -> Decision:
        # Basic sanity & Edge calculation
        p_yes = clamp(p_yes, 0.0001, 0.9999)
        p_no = clamp(p_no, 0.0001, 0.9999)
        e = edge(p_yes, p_no)

        # 0) PR4: Check Exits FIRST
        exit_dec = self.check_exit_conditions(pos, p_yes, p_no, time_remaining)
        if exit_dec:
            return exit_dec

        # --- FONCTION INTERNE : SIZING INTELLIGENT (Min Ticket Fix) ---
        def _get_valid_usd(target_usd: float, price: float) -> tuple[float, str]:
            """
            Vérifie si le montant target_usd est suffisant pour acheter MIN_SHARES.
            Si non, augmente le montant (Upgrade).
            Si dépasse HARD_SAFETY_CAP, bloque (Safety).
            """
            # 1. Estimation du prix limite (pire cas avec slippage pour être sûr d'avoir les parts)
            # On ajoute une petite marge de sécurité de 1% sur le prix pour le calcul du ticket
            limit_price = min(price * (1.0 + self.cfg.SLIPPAGE + 0.01), 0.99)
            
            # 2. Coût minimum absolu pour respecter la règle des 5 shares (ou autre MIN_SHARES)
            min_required_usd = self.cfg.MIN_SHARES * limit_price
            
            final_usd = target_usd
            msg = ""

            # 3. UPGRADE : Si on veut mettre moins que le minimum légal, on force le minimum
            if final_usd < min_required_usd:
                final_usd = min_required_usd
                # msg = f"(forced min {final_usd:.2f}$)" 

            # 4. SAFETY : Si le montant final dépasse la sécurité absolue (ex: 15$), on annule tout
            if final_usd > self.cfg.HARD_SAFETY_CAP:
                return 0.0, f"blocked:ticket_too_expensive_>{self.cfg.HARD_SAFETY_CAP}"
            
            return final_usd, msg
        # --------------------------------------------------------------

        # 1) Si pas de position: entrer opportuniste (cheap) OU edge évident
        if pos.qty_yes == 0 and pos.qty_no == 0:
            if p_yes <= self.cfg.CHEAP_YES_MAX:
                usd, msg = _get_valid_usd(self.cfg.BASE_ORDER_USD, p_yes)
                if usd > 0: return Decision("BUY_YES", usd, f"enter:cheap_yes e={e:.4f}{msg}")
                else: return Decision("HOLD", 0.0, msg)

            if p_no <= self.cfg.CHEAP_NO_MAX:
                usd, msg = _get_valid_usd(self.cfg.BASE_ORDER_USD, p_no)
                if usd > 0: return Decision("BUY_NO", usd, f"enter:cheap_no e={e:.4f}{msg}")
                else: return Decision("HOLD", 0.0, msg)
            
            # Edge-first entry
            if e >= self.cfg.SCALE_EDGE_MIN:
                if p_yes < p_no:
                    usd, msg = _get_valid_usd(self.cfg.BASE_ORDER_USD, p_yes)
                    if usd > 0: return Decision("BUY_YES", usd, f"enter:edge_first_yes e={e:.4f}{msg}")
                else:
                    usd, msg = _get_valid_usd(self.cfg.BASE_ORDER_USD, p_no)
                    if usd > 0: return Decision("BUY_NO", usd, f"enter:edge_first_no e={e:.4f}{msg}")
            
            return Decision("HOLD", 0.0, f"hold:no_signal e={e:.4f}")

        # 2) DIRECTIONAL MOMENTUM (Gabagool Logic)
        # Remplacement du "Hedge systematic" par "Conviction systematic"
        imbalance = pos.qty_yes - pos.qty_no
        total_qty = pos.qty_yes + pos.qty_no
        
        # --- A. HARD STOP (Stop Opening on Adverse Move) ---
        # Si on perd > 15% sur notre position moyenne, on arrête de renforcer (Stop Bleeding).
        # Invariant PR3: Avg Price = Total Cost / Total Qty (Buy-only model)
        if total_qty > 0:
            avg_price = pos.cost_usd / total_qty
            
            # Check Stop YES
            if imbalance > self.cfg.EPS_QTY: 
                if p_yes < avg_price * 0.85:
                    return Decision("HOLD", 0.0, f"[STOP] hard_stop_triggered adverse_move_yes (avg={avg_price:.2f} cur={p_yes:.2f})")
            
            # Check Stop NO
            elif imbalance < -self.cfg.EPS_QTY:
                if p_no < avg_price * 0.85:
                     return Decision("HOLD", 0.0, f"[STOP] hard_stop_triggered adverse_move_no (avg={avg_price:.2f} cur={p_no:.2f})")

        # --- B. MOMENTUM LOGIC ---
        
        # Cas 1: Long YES (Conviction YES)
        if imbalance > self.cfg.EPS_QTY:
             # INTERDICTION DE HEDGE (BUY NO)
             # Sauf si on voulait coder un "Panic Exit" mais ici on fait simple : HOLD.
             # On veut ADD-ON sur YES.
             
             if p_yes <= self.cfg.CHEAP_YES_MAX or e >= self.cfg.SCALE_EDGE_MIN:
                  # sizing standard (pas de martingale)
                  usd, msg = _get_valid_usd(self.cfg.BASE_ORDER_USD, p_yes)
                  if usd > 0:
                       return Decision("BUY_YES", usd, f"[MOMO] conviction=YES action=BUY_YES reason=add_on e={e:.4f}{msg}")
             
             # Si l'edge favorise NO, on ne hedge pas, on HOLD (Blocked)
             if p_no < p_yes:
                  return Decision("HOLD", 0.0, f"[MOMO] hedge_blocked reason=conviction_mode_yes (edge favors NO)")
             
             return Decision("HOLD", 0.0, f"hold:momo_yes_waiting e={e:.4f}")

        # Cas 2: Long NO (Conviction NO)
        if imbalance < -self.cfg.EPS_QTY:
             # INTERDICTION DE HEDGE (BUY YES)
             
             if p_no <= self.cfg.CHEAP_NO_MAX or e >= self.cfg.SCALE_EDGE_MIN:
                  usd, msg = _get_valid_usd(self.cfg.BASE_ORDER_USD, p_no)
                  if usd > 0:
                       return Decision("BUY_NO", usd, f"[MOMO] conviction=NO action=BUY_NO reason=add_on e={e:.4f}{msg}")
             
             if p_yes < p_no:
                  return Decision("HOLD", 0.0, f"[MOMO] hedge_blocked reason=conviction_mode_no (edge favors YES)")

             return Decision("HOLD", 0.0, f"hold:momo_no_waiting e={e:.4f}")
        
        # 3) Cas Neutre / Faible Imbalance -> Comportement Opportuniste (conservé ci-dessous si pas return avant)
        # Si on retombe ici, c'est qu'on est équilibré. On laisse la logique de "Scale" normale prendre le relais.


        # 4) Deux jambes et équilibré: SCALING (Accumulation)
        allowed, reason = self.scale_allowed(pos, p_yes, p_no)
        
        if allowed:
            mult = self.cfg.SCALE_MULT_STRONG if e >= self.cfg.SCALE_EDGE_STRONG else 1.0
            raw_usd = self.cfg.SCALE_ORDER_USD * mult

            # Simulation avec le montant corrigé (Min Ticket)
            # Car si on force un montant plus gros, le worst case change !
            
            if p_yes <= p_no:
                # Test BUY YES
                usd, msg = _get_valid_usd(raw_usd, p_yes)
                if usd <= 0: return Decision("HOLD", 0.0, msg)

                px_eff = apply_fee_and_slippage(self.cfg, p_yes, "BUY")
                qty = usd_to_qty(usd, px_eff)
                worst_after = projected_worst_case(pos, add_yes=qty, add_cost=usd)

                if worst_after >= self.cfg.LOCK_KEEP_USD:
                    return Decision("BUY_YES", usd, f"{reason}:buy_yes e={e:.4f}{msg}")
                else:
                    return Decision("HOLD", 0.0, f"hold:scale_risk_too_high (worst:{worst_after:.2f})")

            else:
                # Test BUY NO
                usd, msg = _get_valid_usd(raw_usd, p_no)
                if usd <= 0: return Decision("HOLD", 0.0, msg)
                
                px_eff = apply_fee_and_slippage(self.cfg, p_no, "BUY")
                qty = usd_to_qty(usd, px_eff)
                worst_after = projected_worst_case(pos, add_no=qty, add_cost=usd)

                if worst_after >= self.cfg.LOCK_KEEP_USD:
                    return Decision("BUY_NO", usd, f"{reason}:buy_no e={e:.4f}{msg}")
                else:
                    return Decision("HOLD", 0.0, f"hold:scale_risk_too_high (worst:{worst_after:.2f})")

        return Decision("HOLD", 0.0, f"hold:{reason} e={e:.4f}")


# -----------------------------
# Paper execution layer
# -----------------------------

class PaperTrader:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.pos = Position()
        self.strategy = GabagoolLikeStrategy(cfg)

    def step(self, p_yes: float, p_no: float) -> Dict[str, Any]:
        d = self.strategy.decide(self.pos, p_yes, p_no)

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

@dataclass(frozen=True)
class LiveDecision:
    side: str            # "BUY_YES", "BUY_NO", "HOLD"
    size_yes: float      # USD à engager si BUY_YES
    size_no: float       # USD à engager si BUY_NO
    reason: str

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

    d = _LIVE_STRAT.decide(pos, float(price_yes), float(price_no))

    # HOLD direct
    if d.action == "HOLD" or float(d.usd) <= 0.0:
        return LiveDecision("HOLD", 0.0, 0.0, d.reason)

    # cooldown live anti-spam (basé sur dernier trade réellement exécuté)
    if (time.time() - float(_LIVE_LAST_TRADE_TS)) < float(_LIVE_CFG.COOLDOWN_SEC):
        return LiveDecision("HOLD", 0.0, 0.0, "hold:cooldown_live")

    base_order_size = float(base_order_size)

    # ---- BUY_YES ----
    if d.action == "BUY_YES":
        min_ticket = _min_ticket_usd(price_yes)
        if base_order_size < min_ticket:
            return LiveDecision("HOLD", 0.0, 0.0, f"hold:min_ticket_yes>{base_order_size:.2f}")

        usd = min(float(d.usd), base_order_size)
        if usd <= 0.0:
            return LiveDecision("HOLD", 0.0, 0.0, "hold:usd_zero")

        return LiveDecision("BUY_YES", usd, 0.0, d.reason)

    # ---- BUY_NO ----
    if d.action == "BUY_NO":
        min_ticket = _min_ticket_usd(price_no)
        if base_order_size < min_ticket:
            return LiveDecision("HOLD", 0.0, 0.0, f"hold:min_ticket_no>{base_order_size:.2f}")

        usd = min(float(d.usd), base_order_size)
        if usd <= 0.0:
            return LiveDecision("HOLD", 0.0, 0.0, "hold:usd_zero")

        return LiveDecision("BUY_NO", 0.0, usd, d.reason)

    # ---- SELL_YES (PR4) ----
    if d.action == "SELL_YES":
        qty = float(d.usd) # d.usd holds QTY for SELL
        if qty <= 0: return LiveDecision("HOLD", 0.0, 0.0, "hold:sell_qty_zero")
        # size_yes carries the QTY
        return LiveDecision("SELL_YES", qty, 0.0, d.reason)

    # ---- SELL_NO (PR4) ----
    if d.action == "SELL_NO":
        qty = float(d.usd)
        if qty <= 0: return LiveDecision("HOLD", 0.0, 0.0, "hold:sell_qty_zero")
        return LiveDecision("SELL_NO", 0.0, qty, d.reason)

    return LiveDecision("HOLD", 0.0, 0.0, d.reason)