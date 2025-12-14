#!/usr/bin/env python3
"""
quick_positions.py
Version rapide qui affiche les positions sans vérifier les statuts des marchés
Utile pour avoir un aperçu rapide
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

def group_trades_by_market(trades: List[Dict]) -> Dict[str, List[Dict]]:
    """Groupe les trades par marché"""
    markets = defaultdict(list)
    
    for trade in trades:
        slug = trade.get("slug") or trade.get("eventSlug") or trade.get("market")
        if slug:
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

def main(wallet_address: str = GABAGOOL_PROXY, show_details: bool = False):
    """Fonction principale"""
    print("\n" + "="*70)
    print("ANALYSE RAPIDE DES POSITIONS")
    print("="*70)
    print(f"Wallet: {wallet_address[:10]}...{wallet_address[-8:]}")
    print("="*70)
    
    # Récupérer tous les trades
    print()
    trades = fetch_all_trades(wallet_address)
    
    if not trades:
        print("Aucun trade trouve")
        return
    
    print(f"\nTotal: {len(trades)} trades recuperes")
    
    # Grouper par marché
    markets = group_trades_by_market(trades)
    print(f"Total: {len(markets)} marches distincts\n")
    
    # Trier les marchés par slug
    sorted_markets = sorted(markets.items())
    
    total_locked = 0.0
    total_exposure = 0.0
    
    # Format de tableau
    print(f"{'='*140}")
    print(f"{'MARCHE':<50} {'YES':>12} {'NO':>12} {'LOCKED PNL':>14} {'EXP':>6} {'TRADES':>7}")
    print(f"{'='*140}")
    
    # Analyser chaque marché
    for slug, market_trades in sorted_markets:
        stats = analyze_market_positions(market_trades)
        
        # Afficher une ligne compacte
        locked_sign = "+" if stats['locked_pnl'] >= 0 else ""
        exp_indicator = stats['exposed_side'][:3]
        
        # Tronquer le nom du marché si trop long
        display_slug = slug[:47] + "..." if len(slug) > 50 else slug
        
        print(f"{display_slug:<50} "
              f"{stats['q_yes']:>12.1f} "
              f"{stats['q_no']:>12.1f} "
              f"${stats['locked_pnl']:>+13.2f} "
              f"{exp_indicator:>6} "
              f"{stats['trade_count']:>7}")
        
        total_locked += stats['locked_pnl']
        total_exposure += stats['exposure']
        
        # Afficher les détails si demandé
        if show_details:
            print(f"  -> Investissement: ${stats['exposure']:.2f}")
            print(f"  -> Si YES: ${stats['pnl_if_yes']:+.2f} | Si NO: ${stats['pnl_if_no']:+.2f}")
            if not stats['is_hedged']:
                print(f"  -> Expose a {stats['exposed_side']} ({stats['net_exposure']:.1f} parts)")
            print()
    
    # Bilan global
    print(f"{'='*140}")
    print(f"\nBILAN GLOBAL:")
    print(f"  Marches: {len(markets)}")
    print(f"  Investissement total: ${total_exposure:,.2f}")
    total_status = "GAIN" if total_locked > 0 else "PERTE"
    print(f"  [{total_status}] Profit garanti total: ${total_locked:>+,.2f}")
    print(f"{'='*140}\n")


if __name__ == "__main__":
    import sys
    
    wallet = GABAGOOL_PROXY
    show_details = False
    
    # Parse arguments
    for arg in sys.argv[1:]:
        if arg.lower() in ["-d", "--details", "details"]:
            show_details = True
        elif arg.startswith("0x"):
            wallet = arg
    
    main(wallet, show_details)
