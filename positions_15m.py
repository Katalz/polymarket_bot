#!/usr/bin/env python3
"""
positions_15m.py
Analyse uniquement les positions sur les marchés crypto 15min
"""

import requests
from datetime import datetime, timezone
from collections import defaultdict
from typing import Dict, List

# =========================
# CONFIGURATION
# =========================

DATA_API = "https://data-api.polymarket.com"
GABAGOOL_PROXY = "0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d"

# =========================
# FONCTIONS
# =========================

def fetch_all_trades(proxy_wallet: str, max_trades: int = 10000) -> List[Dict]:
    """Récupère tous les trades avec pagination"""
    all_trades = []
    offset = 0
    limit = 100
    
    print(f"Recuperation des trades (par batches de {limit})...")
    
    try:
        while len(all_trades) < max_trades:
            params = {
                "proxyWallet": proxy_wallet,
                "limit": limit,
                "offset": offset
            }
            
            r = requests.get(f"{DATA_API}/trades", params=params, timeout=10)
            if r.status_code != 200:
                print(f"[ERROR] API returned {r.status_code} at offset {offset}")
                break
            
            batch = r.json() or []
            
            if not batch:
                break
            
            all_trades.extend(batch)
            offset += limit
            
            if offset % 500 == 0:
                print(f"  ... {len(all_trades)} trades recuperes")
            
            if len(batch) < limit:
                break
                
    except Exception as e:
        print(f"[ERROR] Failed to fetch trades: {e}")
    
    return all_trades

def is_15min_market(slug: str) -> bool:
    """Vérifie si c'est un marché 15min"""
    s = (slug or "").lower()
    return ("15m" in s or "15min" in s) and any(crypto in s for crypto in ["btc", "eth", "sol", "xrp", "doge", "ada"])

def group_trades_by_market(trades: List[Dict]) -> Dict[str, List[Dict]]:
    """Groupe les trades par marché (filtre 15min uniquement)"""
    markets = defaultdict(list)
    
    for trade in trades:
        slug = trade.get("slug") or trade.get("eventSlug") or trade.get("market")
        if slug and is_15min_market(slug):
            markets[slug].append(trade)
    
    return dict(markets)

def analyze_market_positions(trades: List[Dict]) -> Dict:
    """Analyse les positions pour un marché"""
    q_yes = 0.0
    q_no = 0.0
    exposure = 0.0
    
    for trade in trades:
        outcome = trade.get("outcome", "").lower()
        size = float(trade.get("size", 0))
        price = float(trade.get("price", 0))
        usd_volume = price * size
        
        if outcome in ["yes", "up"]:
            q_yes += size
        elif outcome in ["no", "down"]:
            q_no += size
        
        exposure += usd_volume
    
    pnl_if_yes = q_yes - exposure
    pnl_if_no = q_no - exposure
    locked_pnl = min(q_yes, q_no) - exposure if q_yes > 0 and q_no > 0 else -exposure
    
    is_hedged = abs(q_yes - q_no) < 5.0
    net_exposure = q_yes - q_no
    exposed_side = "YES" if net_exposure > 0 else "NO" if net_exposure < 0 else "BAL"
    
    return {
        "q_yes": q_yes,
        "q_no": q_no,
        "exposure": exposure,
        "pnl_if_yes": pnl_if_yes,
        "pnl_if_no": pnl_if_no,
        "locked_pnl": locked_pnl,
        "is_hedged": is_hedged,
        "net_exposure": abs(net_exposure),
        "exposed_side": exposed_side,
        "trade_count": len(trades)
    }

def extract_crypto_from_slug(slug: str) -> str:
    """Extrait le nom de la crypto du slug"""
    s = slug.lower()
    if "btc" in s:
        return "BTC"
    elif "eth" in s:
        return "ETH"
    elif "sol" in s:
        return "SOL"
    elif "xrp" in s:
        return "XRP"
    elif "doge" in s:
        return "DOGE"
    elif "ada" in s:
        return "ADA"
    return "???"

def main(wallet_address: str = GABAGOOL_PROXY):
    """Fonction principale"""
    print("\n" + "="*100)
    print("POSITIONS SUR MARCHES CRYPTO 15MIN")
    print("="*100)
    print(f"Wallet: {wallet_address[:10]}...{wallet_address[-8:]}")
    print("="*100)
    
    # Récupérer tous les trades
    print()
    trades = fetch_all_trades(wallet_address)
    
    if not trades:
        print("Aucun trade trouve")
        return
    
    print(f"\nTotal: {len(trades)} trades recuperes")
    
    # Grouper par marché (filtre 15min)
    markets = group_trades_by_market(trades)
    print(f"Marches 15min trouves: {len(markets)}\n")
    
    if not markets:
        print("Aucun marche 15min trouve!")
        return
    
    # Trier les marchés par slug (approximativement par date)
    sorted_markets = sorted(markets.items())
    
    total_locked = 0.0
    total_exposure = 0.0
    
    # Grouper par crypto pour un résumé
    crypto_stats = defaultdict(lambda: {"locked": 0.0, "exposure": 0.0, "count": 0})
    
    # Format de tableau
    print(f"{'='*100}")
    print(f"{'MARCHE':<45} {'YES':>10} {'NO':>10} {'LOCKED PNL':>13} {'EXP':>5} {'TRADES':>6}")
    print(f"{'='*100}")
    
    # Analyser chaque marché
    for slug, market_trades in sorted_markets:
        stats = analyze_market_positions(market_trades)
        crypto = extract_crypto_from_slug(slug)
        
        # Accumuler les stats par crypto
        crypto_stats[crypto]["locked"] += stats['locked_pnl']
        crypto_stats[crypto]["exposure"] += stats['exposure']
        crypto_stats[crypto]["count"] += 1
        
        # Afficher une ligne compacte
        exp_indicator = stats['exposed_side'][:3]
        
        # Tronquer le nom du marché si trop long
        display_slug = slug[:42] + "..." if len(slug) > 45 else slug
        
        print(f"{display_slug:<45} "
              f"{stats['q_yes']:>10.1f} "
              f"{stats['q_no']:>10.1f} "
              f"${stats['locked_pnl']:>+12.2f} "
              f"{exp_indicator:>5} "
              f"{stats['trade_count']:>6}")
        
        total_locked += stats['locked_pnl']
        total_exposure += stats['exposure']
    
    print(f"{'='*100}")
    
    # Résumé par crypto
    print(f"\nRESUME PAR CRYPTO:")
    print(f"{'='*60}")
    print(f"{'CRYPTO':<10} {'MARCHES':>10} {'INVESTISSEMENT':>18} {'LOCKED PNL':>15}")
    print(f"{'='*60}")
    
    for crypto in sorted(crypto_stats.keys()):
        cstats = crypto_stats[crypto]
        print(f"{crypto:<10} "
              f"{cstats['count']:>10} "
              f"${cstats['exposure']:>17,.2f} "
              f"${cstats['locked']:>+14,.2f}")
    
    print(f"{'='*60}")
    
    # Bilan global
    print(f"\nBILAN GLOBAL (MARCHES 15MIN):")
    print(f"  Total marches: {len(markets)}")
    print(f"  Investissement total: ${total_exposure:,.2f}")
    total_status = "GAIN" if total_locked > 0 else "PERTE"
    print(f"  [{total_status}] Profit garanti total: ${total_locked:>+,.2f}")
    print(f"{'='*100}\n")


if __name__ == "__main__":
    import sys
    
    wallet = GABAGOOL_PROXY
    
    # Permettre de passer une adresse en argument
    if len(sys.argv) > 1 and sys.argv[1].startswith("0x"):
        wallet = sys.argv[1]
    
    main(wallet)
