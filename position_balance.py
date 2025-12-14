#!/usr/bin/env python3
"""
position_balance.py
Récupère les positions actuelles et l'équilibre par marché pour un compte donné via l'API Polymarket.
Affiche pour chaque marché :
- Les positions Yes et No
- Le gain et la perte potentiels
- L'équilibre (si les positions sont hedgées ou exposées)
"""

import requests
import json
from datetime import datetime, timezone
from collections import defaultdict
from typing import Dict, List, Optional

# =========================
# CONFIGURATION
# =========================

GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

# Adresse du wallet à analyser (par défaut Gabagool)
GABAGOOL_PROXY = "0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d"

# =========================
# FONCTIONS
# =========================

def fetch_all_trades(proxy_wallet: str, max_trades: int = 10000) -> List[Dict]:
    """
    Récupère TOUS les trades d'un wallet via l'API en utilisant la pagination
    
    Args:
        proxy_wallet: Adresse du wallet
        max_trades: Nombre maximum de trades à récupérer (sécurité)
    """
    all_trades = []
    offset = 0
    limit = 100  # Taille de batch
    
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
                # Plus de trades disponibles
                break
            
            all_trades.extend(batch)
            offset += limit
            
            # Afficher la progression
            if offset % 500 == 0:
                print(f"  ... {len(all_trades)} trades recuperes")
            
            # Si on a reçu moins que limit, c'est qu'on est à la fin
            if len(batch) < limit:
                break
                
    except Exception as e:
        print(f"[ERROR] Failed to fetch trades: {e}")
    
    return all_trades

def get_market_info(slug: str) -> Optional[Dict]:
    """Récupère les informations d'un marché par son slug"""
    try:
        url = f"{GAMMA_API}/events"
        params = {
            "slug": slug,
            "limit": 1
        }
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            events = r.json()
            if events and len(events) > 0:
                event = events[0]
                markets = event.get("markets") or []
                if markets:
                    market = markets[0]
                    
                    # Vérifier si le marché est réellement fermé en comparant la date
                    from dateutil import parser as date_parser
                    end_date_str = event.get("endDate", "")
                    is_past_end = False
                    if end_date_str:
                        try:
                            end_date = date_parser.isoparse(end_date_str)
                            now = datetime.now(timezone.utc)
                            is_past_end = now > end_date
                        except:
                            pass
                    
                    return {
                        "slug": slug,
                        "question": market.get("question", ""),
                        "active": event.get("active", False),
                        "closed": event.get("closed", False) or is_past_end,  # Considérer fermé si date passée
                        "endDate": event.get("endDate", ""),
                        "outcomes": json.loads(market.get("outcomes", "[]")),
                        "tokens": json.loads(market.get("clobTokenIds", "[]"))
                    }
    except Exception as e:
        print(f"[WARN] Could not fetch market info for {slug}: {e}")
    return None

def group_trades_by_market(trades: List[Dict]) -> Dict[str, List[Dict]]:
    """Groupe les trades par marché (slug)"""
    markets = defaultdict(list)
    
    for trade in trades:
        # Essayer plusieurs champs pour le slug
        slug = trade.get("slug") or trade.get("eventSlug") or trade.get("market")
        if slug:
            markets[slug].append(trade)
    
    return dict(markets)

def analyze_market_positions(trades: List[Dict]) -> Dict:
    """Analyse les positions pour un marché donné"""
    q_yes = 0.0
    q_no = 0.0
    exposure = 0.0
    
    # Compter les numéros de marché (pour distinguer BTC 15m #1, #2, etc.)
    market_count = 0
    
    for trade in trades:
        outcome = trade.get("outcome", "").lower()
        size = float(trade.get("size", 0))
        price = float(trade.get("price", 0))
        usd_volume = price * size
        
        # Déterminer si c'est Yes ou No
        if outcome in ["yes", "up"]:
            q_yes += size
        elif outcome in ["no", "down"]:
            q_no += size
        
        exposure += usd_volume
    
    # Calculer les scénarios de gain/perte
    # Si le marché finit YES : on gagne q_yes ($1 par part)
    pnl_if_yes = q_yes - exposure
    # Si le marché finit NO : on gagne q_no ($1 par part)
    pnl_if_no = q_no - exposure
    
    # Locked PnL = profit garanti (le pire des deux scénarios)
    locked_pnl = min(q_yes, q_no) - exposure if q_yes > 0 and q_no > 0 else -exposure
    
    # Vérifier si les positions sont équilibrées (hedgées)
    is_hedged = abs(q_yes - q_no) < 5.0  # Tolérance de 5 parts
    
    # Déterminer l'exposition nette
    net_exposure = q_yes - q_no
    exposed_side = "YES" if net_exposure > 0 else "NO" if net_exposure < 0 else "BALANCED"
    
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

def print_market_summary(slug: str, stats: Dict, market_info: Optional[Dict] = None):
    """Affiche le résumé d'un marché de manière formatée"""
    print(f"\n{'='*70}")
    print(f"MARCHE: {slug}")
    if market_info:
        print(f"Question: {market_info.get('question', 'N/A')}")
        print(f"Status: {'Fini' if market_info.get('closed') else 'Actif' if market_info.get('active') else 'Inactif'}")
    print(f"{'='*70}")
    
    print(f"\nPOSITIONS:")
    print(f"  - Yes  : {stats['q_yes']:>12.2f} parts")
    print(f"  - No   : {stats['q_no']:>12.2f} parts")
    print(f"  - Investissement : ${stats['exposure']:>10.2f}")
    print(f"  - Nombre de trades : {stats['trade_count']}")
    
    print(f"\nSCENARIOS DE RESULTAT:")
    pnl_yes_sign = "+" if stats['pnl_if_yes'] > 0 else ""
    pnl_no_sign = "+" if stats['pnl_if_no'] > 0 else ""
    print(f"  [{'GAIN' if stats['pnl_if_yes'] > 0 else 'PERTE'}] Si marche finit YES : ${stats['pnl_if_yes']:>+10.2f}")
    print(f"  [{'GAIN' if stats['pnl_if_no'] > 0 else 'PERTE'}] Si marche finit NO  : ${stats['pnl_if_no']:>+10.2f}")
    
    print(f"\nPROFIT GARANTI (Locked PnL):")
    locked_status = "GAIN" if stats['locked_pnl'] > 0 else "PERTE"
    print(f"  [{locked_status}] ${stats['locked_pnl']:>+10.2f}")
    
    print(f"\nEQUILIBRE:")
    if stats['is_hedged']:
        print(f"  [OK] HEDGE - Positions equilibrees")
    else:
        print(f"  [WARN] EXPOSE a {stats['exposed_side']} ({stats['net_exposure']:.2f} parts)")
    
    print(f"{'='*70}")

def main(wallet_address: str = GABAGOOL_PROXY, filter_status: str = "all"):
    """
    Fonction principale
    
    Args:
        wallet_address: Adresse du wallet à analyser
        filter_status: 'all', 'closed', 'active' - Filtre par statut de marché
    """
    print("\n" + "="*70)
    print("ANALYSE DES POSITIONS PAR MARCHE")
    print("="*70)
    print(f"Wallet: {wallet_address}")
    print(f"Source: API Polymarket (temps reel)")
    if filter_status != "all":
        status_name = "TERMINES" if filter_status == "closed" else "ACTIFS"
        print(f"Filtre: Marches {status_name} uniquement")
    print("="*70)
    
    # Récupérer tous les trades
    print("\nRecuperation des trades...")
    trades = fetch_all_trades(wallet_address)
    
    if not trades:
        print("Aucun trade trouve pour ce wallet")
        return
    
    print(f"{len(trades)} trades recuperes")
    
    # Grouper par marché
    markets = group_trades_by_market(trades)
    print(f"{len(markets)} marches distincts trouves")
    
    if filter_status != "all":
        print("\nRecuperation des statuts des marches (peut prendre du temps)...")
    
    # Trier les marchés par slug (approximativement par date)
    sorted_markets = sorted(markets.items())
    
    total_locked = 0.0
    total_exposure = 0.0
    markets_displayed = 0
    markets_filtered = 0
    
    # Analyser chaque marché
    for slug, market_trades in sorted_markets:
        stats = analyze_market_positions(market_trades)
        
        # Récupérer les informations du marché si filtrage actif
        market_info = None
        if filter_status != "all":
            market_info = get_market_info(slug)
            
            # Filtrer selon le statut
            if market_info:
                is_closed = market_info.get('closed', False)
                is_active = market_info.get('active', False)
                
                if filter_status == "closed" and not is_closed:
                    markets_filtered += 1
                    continue
                elif filter_status == "active" and not is_active:
                    markets_filtered += 1
                    continue
            else:
                # Si on ne peut pas récupérer l'info, on skip par sécurité
                markets_filtered += 1
                continue
        
        print_market_summary(slug, stats, market_info)
        markets_displayed += 1
        
        total_locked += stats['locked_pnl']
        total_exposure += stats['exposure']
    
    # Afficher le bilan global
    print(f"\n\n{'='*70}")
    print("BILAN GLOBAL")
    print(f"{'='*70}")
    print(f"Marches affiches : {markets_displayed}")
    if filter_status != "all":
        print(f"Marches filtres : {markets_filtered}")
    print(f"Investissement total : ${total_exposure:,.2f}")
    total_status = "GAIN" if total_locked > 0 else "PERTE"
    print(f"[{total_status}] Profit garanti total : ${total_locked:>+,.2f}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    import sys
    
    # Parse arguments
    wallet = GABAGOOL_PROXY
    filter_status = "all"
    
    if len(sys.argv) > 1:
        # Premier argument peut être l'adresse ou le filtre
        arg1 = sys.argv[1].lower()
        if arg1 in ["all", "closed", "active"]:
            filter_status = arg1
        else:
            wallet = sys.argv[1]
            
    if len(sys.argv) > 2:
        # Deuxième argument pour le filtre
        arg2 = sys.argv[2].lower()
        if arg2 in ["all", "closed", "active"]:
            filter_status = arg2
    
    main(wallet, filter_status)
