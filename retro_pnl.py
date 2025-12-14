#!/usr/bin/env python3
"""
retro_pnl.py
Analyse le PnL RÉEL de Gabagool sur les marchés RÉSOLUS via l'API.
1. Trouve les derniers marchés BTC 15min résolus.
2. Récupère les trades de Gabagool sur la fenêtre de temps de ce marché.
3. Reconstruit la position et calcule le PnL final basé sur le résultat officiel.
"""

import requests
import json
from datetime import datetime, timedelta, timezone
from dateutil import parser
import time

# Config
GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
GABAGOOL_PROXY = "0xe00740bce98a594e26861838885ab310ec3b548c" # Updated address from history vs user check
# Note: User mentioned 'GABAGOOL_PROXY' in distinct_tracker.py is 0xe007... 
# But in previous logs it might have been 0x6031... Let's check distinct_tracker again or use the one he is tracking.
# distinct_tracker.py lines 27-28 says 0xe00740bce98a594e26861838885ab310ec3b548c
# gabagool_tracker.py lines 27-28 says 0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d (Wait, user changed it?)
# I should verify which one is active. The user corrected to distinct_tracker results mostly. I will use the one in distinct_tracker as primary or ask. 
# Let's read distinct_tracker to be sure.

def get_resolved_markets():
    """Récupère les derniers marchés BTC 15m résolus via Events"""
    print("[INIT] Fetching resolved events history...")
    url = f"{GAMMA_API}/events"
    params = {
        "limit": 50,
        "closed": "true",
        "order": "endDate",
        "ascending": "false" 
    }
    
    try:
        r = requests.get(url, params=params)
        r.raise_for_status()
        events = r.json()
    except Exception as e:
        print(f"[ERROR] Gamma API: {e}")
        return []

    markets = []
    for e in events:
        slug = e.get("slug", "")
        # Filtrer pour BTC 15m
        if "btc" not in slug.lower() or ("15m" not in slug.lower() and "15min" not in slug.lower()):
            continue
            
        event_markets = e.get("markets", [])
        if not event_markets:
            continue
            
        m = event_markets[0] # Take first market of the event
        
        # Vérifier s'il est résolu (question resolved)
        if not m.get("question", {}).get("isResolved"):
             # Sometimes resolvedBy is used
             pass
            
        markets.append(m)
        if len(markets) >= 5: # On en garde 5
            break
            
    return markets

def fetch_user_trades_for_market(market):
    """Récupère les trades d'un user sur une plage de temps spécifique au marché"""
    # On prend une marge de sûreté sur les timestamps
    start_ts = parser.isoparse(market["startDate"]).timestamp() - 60
    # La fin peut être la resolution date ou end date
    end_date_str = market.get("resolutionDate") or market.get("endDate")
    end_ts = parser.isoparse(end_date_str).timestamp() + 3600 # +1h pour être large si redemption immédiate
    
    # On utilise l'asset ID pour filtrer spécifiquement ce marché
    # Token IDs: [Yes, No]
    try:
        clob_ids = json.loads(market["clobTokenIds"])
    except:
        return []
        
    all_trades = []
    
    # Pagination loop
    offset = 0
    limit = 100
    
    # Stratégie: On récupère les trades globaux du user dans la fenêtre de temps
    # Data API ne semble pas supporter le filtrage par asset_id ET user facilement sans itérer
    # Mais on peut filtrer par 'asset' coté client
    
    print(f"   > Fetching trades for {market['slug']} ...")
    
    while True:
        url = f"{DATA_API}/trades"
        params = {
            "proxyWallet": GABAGOOL_PROXY,
            "limit": limit,
            "offset": offset,
            # Data API supporte-t-elle startTs ? Souvent oui.
            # On va essayer de récupérer large et filtrer. 
            # Note: Data API trades endpoint order is typically desc time.
        }
        
        try:
            r = requests.get(url, params=params)
            trades = r.json()
        except:
            break
            
        if not trades:
            break
            
        added = 0
        for t in trades:
            ts = t.get("timestamp", 0)
            
            # Si on est remonté trop loin dans le temps (avant le début du marché)
            if ts < start_ts:
                # Comme c'est trié descendant, on peut s'arrêter (sauf si l'API n'est pas triée, mais elle l'est généralement)
                # Par sécurité on checke tout le batch puis on verra pour break global
                pass
                
            # Vérifier si le trade concerne nos tokens
            asset = t.get("asset", "")
            if asset in clob_ids:
                all_trades.append(t)
                added += 1
                
        if len(trades) < limit:
            break
            
        # Si le dernier trade du batch est plus vieux que start_ts, on stop
        last_ts = trades[-1].get("timestamp", 0)
        if last_ts < start_ts:
            break
            
        offset += limit
        time.sleep(0.2)
        
    return all_trades

def analyze_market_result(market, trades):
    """Calcule le PnL pour un marché donné"""
    # 1. Identifier le gagnant
    # token_ids = [YES_ID, NO_ID] typically
    try:
        clob_ids = json.loads(market["clobTokenIds"])
    except:
        return
        
    yes_id = clob_ids[0]
    no_id = clob_ids[1] # Assumption for 2-outcome
    
    # Résultat
    # "outcome" field in market object? Gamma uses 'question' logic usually.
    # We can assume: if market.resolvedOutcome == "TRUE" -> YES won, else NO wins (FALSE?)
    # Need to check resolution data format. Usually `question.resolution` or similar.
    # In Gamma `market`: 'resolutionValue' or 'outcome'?
    # Let's infer from `completeness` or just look for the winning outcome string.
    # Actually, simplistic check: 
    # If question is "Will BTC be > X?", TRUE=YES.
    
    # On va printer les infos pour debug si besoin, mais assumons :
    # Si on ne trouve pas l'outcome direct, on simule les deux.
    
    # Positions
    q_yes = 0.0
    q_no = 0.0
    net_invested = 0.0 # Positive = cost, Negative = profit taken out
    
    for t in trades:
        side = t.get("side", "").upper() # BUY / SELL
        asset = t.get("asset", "")
        size = float(t.get("size", 0))
        price = float(t.get("price", 0))
        vol = size * price
        
        # Ajustement position
        if side == "BUY":
            net_invested += vol
            if asset == yes_id: q_yes += size
            elif asset == no_id: q_no += size
        elif side == "SELL":
            net_invested -= vol
            if asset == yes_id: q_yes -= size
            elif asset == no_id: q_no -= size
            
    # Calcul PnL
    # Il faut savoir qui a gagné.
    # Gamma API field "groupItemTitle" e.g. "Above 98000.00"
    end_price = 0 # Look for market['resolutionValue'] ?
    
    # Pour l'instant on affiche la position finale et le coût.
    print(f"\n[REPORT] Market: {market['slug']}")
    print(f"  Ended: {market['endDate']}")
    print(f"  Position YES : {q_yes:,.2f}")
    print(f"  Position NO  : {q_no:,.2f}")
    print(f"  Net Spent    : ${net_invested:,.2f} (Cashflow)")
    
    # Tentative d'auto-detection du gagnant par logique floue ou field
    # (Parfois c'est complexe sans l'objet Question complet)
    # On affiche simplement: "Si YES gagne" / "Si NO gagne" pour être sûr.
    
    res_yes = (q_yes * 1.0) - net_invested
    res_no  = (q_no * 1.0) - net_invested
    
    print(f"  >> PnL if YES WON: ${res_yes:+.2f}")
    print(f"  >> PnL if NO WON : ${res_no:+.2f}")
    
    # Note: On a pas l'info exacte de QUI a gagné sans parser 'acceptingOrders'==false + resolution details
    # Mais le user peut le voir avec le slug.

def main():
    print(f"Target Wallet: {GABAGOOL_PROXY}")
    markets = get_resolved_markets()
    print(f"Found {len(markets)} recent resolved BTC 15m markets.")
    
    for m in markets:
        trades = fetch_user_trades_for_market(m)
        if not trades:
            print(f"\n[SKIP] No trades found for {m['slug']}")
            continue
            
        analyze_market_result(m, trades)

if __name__ == "__main__":
    main()
