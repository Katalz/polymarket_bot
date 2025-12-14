#!/usr/bin/env python3
"""
check_positions.py
Récupère les positions actuelles directement via l'endpoint /positions de l'API.
Plus rapide et précis que de recalculer depuis les trades !
"""

import requests
import json
from datetime import datetime, timezone
from collections import defaultdict
from typing import Dict, List, Optional
from dateutil import parser as date_parser

# =========================
# CONFIGURATION
# =========================

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

# Adresse du wallet à analyser (par défaut Gabagool)
GABAGOOL_PROXY = "0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d"

# =========================
# FONCTIONS
# =========================

def fetch_positions(wallet_address: str) -> List[Dict]:
    """Récupère toutes les positions actuelles d'un wallet"""
    try:
        url = f"{CLOB_API}/positions"
        params = {
            "user": wallet_address
        }
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            print(f"[ERROR] API returned {r.status_code}: {r.text}")
            return []
        
        data = r.json()
        # L'API retourne un objet avec les positions
        if isinstance(data, dict):
            return data.get("data", [])
        elif isinstance(data, list):
            return data
        return []
    except Exception as e:
        print(f"[ERROR] Failed to fetch positions: {e}")
        return []

def get_market_info(condition_id: str) -> Optional[Dict]:
    """Récupère les informations d'un marché par son condition_id"""
    try:
        url = f"{GAMMA_API}/markets/{condition_id}"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"[WARN] Could not fetch market info for {condition_id}: {e}")
    return None

def get_event_info(event_slug: str) -> Optional[Dict]:
    """Récupère les informations d'un événement par son slug"""
    try:
        url = f"{GAMMA_API}/events"
        params = {"slug": event_slug, "limit": 1}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            events = r.json()
            if events:
                event = events[0]
                # Vérifier si fermé en comparant la date
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
                    "slug": event.get("slug", ""),
                    "title": event.get("title", ""),
                    "active": event.get("active", False),
                    "closed": event.get("closed", False) or is_past_end,
                    "endDate": end_date_str
                }
    except Exception as e:
        print(f"[WARN] Could not fetch event info for {event_slug}: {e}")
    return None

def group_positions_by_market(positions: List[Dict]) -> Dict[str, Dict]:
    """Groupe les positions par marché (condition_id)"""
    markets = defaultdict(lambda: {"yes": 0.0, "no": 0.0, "positions": []})
    
    for pos in positions:
        # Récupérer les infos de la position
        condition_id = pos.get("condition_id", "")
        asset_id = pos.get("asset_id", "")
        size = float(pos.get("size", 0))
        
        # Déterminer si c'est Yes ou No basé sur l'outcome
        # Dans l'API positions, on peut avoir directement l'info
        market_info = pos.get("market", {})
        outcome = market_info.get("outcome", "")
        
        # Stocker la position
        markets[condition_id]["positions"].append(pos)
        
        # On va récupérer les détails après pour distinguer Yes/No
        
    return dict(markets)

def analyze_market_from_positions(positions: List[Dict]) -> Dict:
    """Analyse les positions pour un marché donné"""
    q_yes = 0.0
    q_no = 0.0
    
    # Récupérer les infos du marché depuis la première position
    market_slug = ""
    market_title = ""
    
    for pos in positions:
        size = float(pos.get("size", 0))
        
        # L'outcome peut être dans plusieurs endroits
        outcome = pos.get("outcome", "").lower()
        asset_id = pos.get("asset_id", "")
        market = pos.get("market", {})
        
        # Essayer de récupérer le slug
        if not market_slug:
            market_slug = market.get("slug", "") or market.get("market_slug", "")
            market_title = market.get("question", "") or market.get("title", "")
        
        # Déterminer Yes ou No
        # L'API positions donne directement la taille de chaque outcome
        if outcome in ["yes", "up"]:
            q_yes += size
        elif outcome in ["no", "down"]:
            q_no += size
    
    # Pour l'exposition, on ne peut pas la calculer exactement sans les prix
    # On va estimer avec la valeur moyenne de 0.5 par part
    exposure = (q_yes + q_no) * 0.5
    
    # Calculer les scénarios
    pnl_if_yes = q_yes - exposure
    pnl_if_no = q_no - exposure
    locked_pnl = min(q_yes, q_no) - exposure if q_yes > 0 and q_no > 0 else -exposure
    
    is_hedged = abs(q_yes - q_no) < 5.0
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
        "market_slug": market_slug,
        "market_title": market_title,
        "position_count": len(positions)
    }

def print_market_summary(condition_id: str, stats: Dict, event_info: Optional[Dict] = None):
    """Affiche le résumé d'un marché"""
    print(f"\n{'='*70}")
    print(f"MARCHE: {stats['market_slug'] or condition_id}")
    if stats['market_title']:
        print(f"Question: {stats['market_title']}")
    if event_info:
        status = "Fini" if event_info.get('closed') else "Actif" if event_info.get('active') else "Inactif"
        print(f"Status: {status}")
    print(f"{'='*70}")
    
    print(f"\nPOSITIONS ACTUELLES:")
    print(f"  - Yes  : {stats['q_yes']:>12.2f} parts")
    print(f"  - No   : {stats['q_no']:>12.2f} parts")
    print(f"  - Valeur estimee : ${stats['exposure']:>10.2f}")
    
    print(f"\nSCENARIOS DE RESULTAT:")
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
    print("ANALYSE DES POSITIONS ACTUELLES (via API /positions)")
    print("="*70)
    print(f"Wallet: {wallet_address}")
    if filter_status != "all":
        status_name = "TERMINES" if filter_status == "closed" else "ACTIFS"
        print(f"Filtre: Marches {status_name} uniquement")
    print("="*70)
    
    # Récupérer toutes les positions
    print("\nRecuperation des positions actuelles...")
    positions = fetch_positions(wallet_address)
    
    if not positions:
        print("Aucune position trouvee pour ce wallet")
        return
    
    print(f"{len(positions)} positions recuperees")
    
    # Grouper par marché (condition_id)
    markets_map = {}
    for pos in positions:
        condition_id = pos.get("condition_id", "")
        if not condition_id:
            continue
        
        if condition_id not in markets_map:
            markets_map[condition_id] = []
        markets_map[condition_id].append(pos)
    
    print(f"{len(markets_map)} marches distincts trouves")
    
    if filter_status != "all":
        print("\nRecuperation des statuts des marches (peut prendre du temps)...")
    
    total_value = 0.0
    total_locked = 0.0
    markets_displayed = 0
    markets_filtered = 0
    
    # Analyser chaque marché
    for condition_id, market_positions in sorted(markets_map.items()):
        stats = analyze_market_from_positions(market_positions)
        
        # Récupérer les infos de l'événement si filtrage actif
        event_info = None
        if filter_status != "all" and stats['market_slug']:
            event_info = get_event_info(stats['market_slug'])
            
            if event_info:
                is_closed = event_info.get('closed', False)
                is_active = event_info.get('active', False)
                
                if filter_status == "closed" and not is_closed:
                    markets_filtered += 1
                    continue
                elif filter_status == "active" and not is_active:
                    markets_filtered += 1
                    continue
            else:
                # Si on ne peut pas récupérer l'info, on skip par sécurité
                if filter_status != "all":
                    markets_filtered += 1
                    continue
        
        print_market_summary(condition_id, stats, event_info)
        markets_displayed += 1
        
        total_value += stats['exposure']
        total_locked += stats['locked_pnl']
    
    # Afficher le bilan global
    print(f"\n\n{'='*70}")
    print("BILAN GLOBAL")
    print(f"{'='*70}")
    print(f"Marches affiches : {markets_displayed}")
    if filter_status != "all":
        print(f"Marches filtres : {markets_filtered}")
    print(f"Valeur totale estimee : ${total_value:,.2f}")
    total_status = "GAIN" if total_locked > 0 else "PERTE"
    print(f"[{total_status}] Profit garanti total : ${total_locked:>+,.2f}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    import sys
    
    # Parse arguments
    wallet = GABAGOOL_PROXY
    filter_status = "all"
    
    if len(sys.argv) > 1:
        arg1 = sys.argv[1].lower()
        if arg1 in ["all", "closed", "active"]:
            filter_status = arg1
        else:
            wallet = sys.argv[1]
            
    if len(sys.argv) > 2:
        arg2 = sys.argv[2].lower()
        if arg2 in ["all", "closed", "active"]:
            filter_status = arg2
    
    main(wallet, filter_status)
