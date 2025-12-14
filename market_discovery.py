"""
Fonctions pour la découverte automatique des marchés Bitcoin 15 minutes actifs.
"""

import time
import requests
import json
import math
from datetime import datetime, timezone
from dateutil import parser
from typing import List, Dict, Optional

GAMMA_API_BASE = "https://gamma-api.polymarket.com"


def generate_btc_15min_slugs(hours_behind: int = 2, hours_ahead: int = 24) -> List[str]:
    """
    Génère les slugs potentiels des marchés BTC 15 min autour de l'heure actuelle.

    Args:
        hours_behind: Nombre d'heures à générer en arrière
        hours_ahead: Nombre d'heures à générer devant

    Returns:
        Liste des slugs potentiels
    """
    slugs = []
    current_time = time.time()

    # Arrondir au quart d'heure le plus proche en arrière
    # Par exemple si on est à 01:35, commencer à 01:30
    current_quarter = int(current_time // (15 * 60)) * (15 * 60)

    # Générer des timestamps toutes les 15 minutes
    total_slots = (hours_behind + hours_ahead) * 4  # 4 marchés par heure
    start_timestamp = current_quarter - (hours_behind * 60 * 60)

    for i in range(total_slots):
        timestamp = int(start_timestamp + (i * 15 * 60))
        slug = f"btc-updown-15m-{timestamp}"
        slugs.append(slug)

    return slugs


def get_active_btc_15min_markets() -> List[Dict]:
    """
    Récupère automatiquement tous les marchés Bitcoin 15 minutes actifs.

    Returns:
        List de dicts avec 'slug', 'start_time', 'end_time', etc.
    """
    try:
        print("Generation des slugs BTC 15min potentiels...")
        potential_slugs = generate_btc_15min_slugs(hours_ahead=48)  # 48h devant
        print(f"slugs {len(potential_slugs)} slugs generes")

        btc_15min_markets = []

        for slug in potential_slugs:
            try:
                # Tester si le marché existe et est actif
                market_response = requests.get(f"https://gamma-api.polymarket.com/events/slug/{slug}")
                market_response.raise_for_status()
                market_data = market_response.json()

                markets = market_data.get("markets", [])
                if not markets:
                    continue

                market = markets[0]  # Premier marché (normalement le seul)
                market_id = market.get("id")
                active = market.get("active", False)
                closed = market.get("closed", False)

                # Vérifier que c'est actif et pas fermé
                if active and not closed:
                    # Extraire le timestamp depuis le slug
                    parts = slug.split("-")
                    if len(parts) >= 4 and parts[-1].isdigit():
                        timestamp = int(parts[-1])

                        market_info = {
                            "slug": slug,
                            "market_id": market_id,
                            "start_time": timestamp,
                            "end_time": timestamp + (15 * 60),  # +15 minutes
                            "title": market.get("question", ""),
                            "active": active
                        }
                        btc_15min_markets.append(market_info)
                        print(f"Marche actif trouve: {slug}")

            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 404:
                    # Marché n'existe pas encore, c'est normal
                    continue
                else:
                    print(f"Erreur HTTP pour {slug}: {e}")
            except Exception as e:
                print(f"Erreur pour {slug}: {e}")
                continue

        # Trier par timestamp croissant
        btc_15min_markets.sort(key=lambda x: x["start_time"])

        print(f"Trouve {len(btc_15min_markets)} marches BTC 15 min actifs")
        for market in btc_15min_markets:
            start_str = time.strftime('%H:%M:%S', time.localtime(market['start_time']))
            end_str = time.strftime('%H:%M:%S', time.localtime(market['end_time']))
            print(f"marche {market['slug']} ({start_str} -> {end_str})")

        return btc_15min_markets

    except Exception as e:
        print(f"Erreur recuperation marches BTC 15 min: {e}")
        import traceback
        traceback.print_exc()
        return []


def get_current_active_market(markets_list: List[Dict]) -> Optional[Dict]:
    """
    Identifie le marché actuellement actif (en cours).

    Args:
        markets_list: Liste des marchés depuis get_active_btc_15min_markets()

    Returns:
        Dict du marché actif, ou None si aucun actif
    """
    current_time = time.time()

    for market in markets_list:
        if market["active"] and market["start_time"] <= current_time <= market["end_time"]:
            print(f"Marche actif trouve: {market['slug']}")
            print(f"   Debut: {time.strftime('%H:%M:%S', time.localtime(market['start_time']))}")
            print(f"   Fin: {time.strftime('%H:%M:%S', time.localtime(market['end_time']))}")
            return market

    print("Aucun marche actif trouve actuellement")
    return None


def get_next_market_in_sequence(markets_list: List[Dict], current_market_slug: str) -> Optional[Dict]:
    """
    Trouve le prochain marché dans la séquence après le marché actuel.

    Args:
        markets_list: Liste complète des marchés
        current_market_slug: Slug du marché actuel

    Returns:
        Dict du prochain marché, ou None si dernier
    """
    # Trier par timestamp
    sorted_markets = sorted(markets_list, key=lambda x: x["start_time"])

    for i, market in enumerate(sorted_markets):
        if market["slug"] == current_market_slug:
            if i + 1 < len(sorted_markets):
                next_market = sorted_markets[i + 1]
                print(f"Prochain marche dans la sequence: {next_market['slug']}")
                return next_market
            else:
                print("Dernier marche de la sequence atteint")
                return None

    return None


def wait_for_next_market(markets_list: List[Dict], current_market_slug: str) -> Optional[Dict]:
    """
    Attend le prochain marché dans la séquence.

    Args:
        markets_list: Liste complète des marchés
        current_market_slug: Slug du marché actuel

    Returns:
        Dict du prochain marché quand il devient actif, ou None si timeout
    """
    next_market = get_next_market_in_sequence(markets_list, current_market_slug)
    if not next_market:
        return None

    print(f"Attente du prochain marche: {next_market['slug']}")
    print(f"   Demarrage prevu: {time.strftime('%H:%M:%S', time.localtime(next_market['start_time']))}")

    # Attendre que le marché devienne actif
    while time.time() < next_market["start_time"]:
        remaining = int(next_market["start_time"] - time.time())
        if remaining % 60 == 0:  # Afficher chaque minute
            print(f"   {remaining // 60} minutes restantes...")
        time.sleep(10)  # Vérifier toutes les 10 secondes

    print(f"Marche {next_market['slug']} devient actif !")
    return next_market


def scan_active_markets(tickers: List[str], include_next: bool = False) -> List[Dict]:
    """
    Scans for active 15m markets for the given tickers.
    Format: {ticker}-updown-15m-{expiry_ts}
    If include_next=True, also scans for the NEXT 15m market (early detection).
    """
    candidates = []
    now_ts = time.time()
    # Current 15m boundary (Start Time)
    current_expiry_ts = math.floor(now_ts / 900) * 900
    
    # Expiry candidates to check
    expirations = [current_expiry_ts]
    if include_next:
         expirations.append(current_expiry_ts + 900) # Next 15m block
    
    for expiry_ts in expirations:
        # Optimization: Early check against current time
        # expiry_ts is the START of the 15m block.
        # We want to skip if the market has already ENDED (Start + 900 < Now).
        # We add a small buffer (e.g., skip if less than 30s remaining).
        market_end_time = expiry_ts + 900
        if market_end_time < (now_ts + 30): 
            # Market ended or ending in <30s
            # print(f"[SCAN] Skip {expiry_ts}: Ended or too close")
            continue

        for ticker in tickers:
            slug = f"{ticker.lower()}-updown-15m-{int(expiry_ts)}"
            url = f"{GAMMA_API_BASE}/events"
            params = {"slug": slug}
        
        try:
            r = requests.get(url, params=params, timeout=3)
            events = r.json()
            
            if not events:
                 continue
                 
            e = events[0]
            if e.get("closed"): 
                print(f"[SCAN] Rejected {slug}: CLOSED")
                continue
            
            # Active Window Check
            try:
                start = parser.isoparse(e["startDate"])
                end = parser.isoparse(e["endDate"])
                now_dt = datetime.now(timezone.utc)
                remaining = (end - now_dt).total_seconds()
                
                # Sniper Window: [60, 880] (Start + few mins -> End - few mins)
                # If include_next is True, we allow early detection (up to 930s remaining = 30s before start)
                upper_bound = 930 if include_next else 880
                
                # If too early (> upper_bound) or too late (< 60s remaining)
                # Note: If looking for next market, remaining might be ~915s.
                if not (60 <= remaining <= upper_bound):
                    print(f"[SCAN] Rejected {slug}: Time window (rem={remaining:.0f}s not in [60, {upper_bound}])")
                    continue
            except Exception as e_parse: 
                print(f"[SCAN] Rejected {slug}: Date parse error {e_parse}")
                continue 
                print(f"[SCAN] Rejected {slug}: Date parse error {e_parse}")
                continue
            
            # Parse Details
            markets = e.get("markets") or []
            if not markets: 
                print(f"[SCAN] Rejected {slug}: No markets data")
                continue
            m = markets[0]
            
            try:
                clob_ids = json.loads(m.get("clobTokenIds", "[]"))
            except: 
                raw = m.get("clobTokenIds")
                if isinstance(raw, list): clob_ids = raw
                else: clob_ids = []
                
            if len(clob_ids) < 2: 
                print(f"[SCAN] Rejected {slug}: Not enough tokens ({len(clob_ids)})")
                continue
            
            candidates.append({
                "slug": e["slug"],
                "token_id": clob_ids[0],
                "no_token_id": clob_ids[1],
                "start_time": start.timestamp(),
                "end_time": end.timestamp(),
            })
            
        except Exception as err:
            print(f"[SCAN] Error fetching {slug}: {err}")
            continue
            
    return candidates
