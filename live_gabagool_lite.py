
#!/usr/bin/env python3
"""
Live Gabagool-Lite
==================

Point d'entrée principal pour le bot "Gabagool-Lite" ($600 bankroll).
Stratégie Maker-Only 15m.
"""

import time
import logging
import sys
import os
import requests
from dotenv import load_dotenv
load_dotenv()
from dotenv import load_dotenv
load_dotenv()

# Put local dir in path
sys.path.append(os.getcwd())

from gabagool_lite.config import DEFAULT_CONFIG_600
from gabagool_lite.orchestrator import LiteOrchestrator
from clob_client import PolymarketCLOBClientAuthenticated
from market_discovery import scan_active_markets

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("gabagool_lite.log"),
        logging.StreamHandler(sys.stdout)
    ]
)

# Silence noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("py_clob_client").setLevel(logging.WARNING) # If it uses standard logging

logger = logging.getLogger("GabagoolLite")

def fetch_positions_safe(client) -> list:
    """
    Robust way to fetch positions, handling clob_client variations.
    """
    # 1. Try standard client methods
    if hasattr(client, "get_account_positions"):
        return client.get_account_positions()
    if hasattr(client, "get_positions"):
        return client.get_positions()
        
    # 2. Manual Request Fallback (if client lacks method but keys are known)
    # Using L2 Auth (API Keys) which is sufficient for viewing private data usually
    try:
        url = f"{DEFAULT_CONFIG_600.CLOB_HOST}/data/positions"
        headers = {
            "POLY-API-KEY": DEFAULT_CONFIG_600.CLOB_API_KEY,
            "POLY-API-SECRET": DEFAULT_CONFIG_600.CLOB_API_SECRET,
            "POLY-API-PASSPHRASE": DEFAULT_CONFIG_600.CLOB_API_PASSPHRASE,
        }
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 404:
             # Endpoint might differ? 
             # Try /positions
             resp = requests.get(f"{DEFAULT_CONFIG_600.CLOB_HOST}/positions", headers=headers, timeout=5)
             if resp.status_code == 200:
                 return resp.json()
    except Exception as e:
        logger.warning(f"Manual pos fetch failed: {e}")
        
    return []

def main():
    # 0. Check Dry Run Mode
    import sys
    DRY_RUN = False
    if len(sys.argv) > 1 and ("test" in sys.argv or "dry" in sys.argv[1]):
        DRY_RUN = True
        logger.warning("\033[93m" + "="*50)
        logger.warning("!!! RUNNING IN DRY RUN / SIMULATION MODE !!!")
        logger.warning("No real orders will be placed.")
        logger.warning("All generated orders will be IMMEIDATELY FILLED (Simulated).")
        logger.warning("="*50 + "\033[0m")

    logger.info("--- GABAGOOL LITE INITIALIZATION ---")
    
    # 1. Initialize API Client
    try:
        # Pass keys explicitly from the trusted config object
        wrapper = PolymarketCLOBClientAuthenticated(
            host=DEFAULT_CONFIG_600.CLOB_HOST,
            api_key=DEFAULT_CONFIG_600.CLOB_API_KEY,
            api_secret=DEFAULT_CONFIG_600.CLOB_API_SECRET,
            api_passphrase=DEFAULT_CONFIG_600.CLOB_API_PASSPHRASE,
            polygon_private_key=DEFAULT_CONFIG_600.POLYGON_PRIVATE_KEY,
            proxy_address=DEFAULT_CONFIG_600.POLYMARKET_PROXY_ADDRESS
        )
        client = wrapper.client
        logger.info("API Client Authenticated.")
    except Exception as e:
        logger.error(f"Failed to init client: {e}")
        return

    # 4. Initialize Orchestrator (Empty initially)
    # We will feed it markets dynamically
    orphan_config = DEFAULT_CONFIG_600
    orchestrator = LiteOrchestrator(client, orphan_config, [], dry_run=DRY_RUN)
    logger.info(f"Orchestrator initialized. Dry Run: {DRY_RUN}")

    # State for scanning
    last_scan_time = 0
    SCAN_INTERVAL = 10.0
    tickers = ["BTC"]
    
    # Track which slugs we are managing to avoid redundant update calls if we want optimizing
    # But orchestrator.strategies is the detailed source.

    # 5. Continuous Loop
    try:
        while True:
            cycle_start = time.time()
            
            # --- A. Scan & Add Markets ---
            if time.time() - last_scan_time > SCAN_INTERVAL:
                try:
                    # Scan Current AND Next (Pre-load)
                    candidates = scan_active_markets(tickers, include_next=True)
                    
                    if not candidates:
                        if not orchestrator.strategies:
                            logger.info("No markets found. Waiting...")
                    
                    for cand in candidates:
                        # Add to orchestrator (idempotent)
                        # Map keys if needed. scan returns {slug, token_id, no_token_id...}
                        # Strategy expects same.
                        orchestrator.add_strategy(cand)
                        
                    last_scan_time = time.time()
                    
                except Exception as e:
                    logger.error(f"Scan failed: {e}")

            # --- B. Refresh Data for Managed Markets ---
            # Fetch Global Positions once
            try:
                pos_list = fetch_positions_safe(client)
            except: 
                pos_list = []

            # Update each strategy
            active_slugs = list(orchestrator.strategies.keys())
            
            # Remove stopped/expired strategies? 
            # Current `strategy.py` doesn't self-destruct. 
            # We can purge if time_remaining < -60?
            # For now, keep them. Memory leak negligible for 24h run.
            
            for slug in active_slugs:
                strat = orchestrator.strategies[slug]
                
                # Fetch Book
                try:
                    # Helper to convert
                    def fmt_book(b_obj):
                        if not b_obj: return []
                        bids = getattr(b_obj, "bids", [])
                        asks = getattr(b_obj, "asks", [])
                        return {
                            "bids": [{"price": float(x.price), "size": float(x.size)} for x in bids],
                            "asks": [{"price": float(x.price), "size": float(x.size)} for x in asks]
                        }

                    # Fetch YES
                    book_y_raw = client.get_order_book(strat.tokens["YES"])
                    book_y = fmt_book(book_y_raw)
                    
                    # Fetch NO
                    try:
                        book_n_raw = client.get_order_book(strat.tokens["NO"])
                        book_n = fmt_book(book_n_raw)
                    except:
                        book_n = {"bids": [], "asks": []}
                    
                    # Calc Time Rem
                    # We need end_time. Strategy doesn't store end_time explicitly in __init__?
                    # It relies on orchestrator passing it?
                    # We can re-parse slug or store metadata in orchestrator.
                    # Parse from slug: btc-updown-15m-{ts}
                    # Slug TS is START TIME. End Time is Start + 900.
                    try:
                        parts = slug.split("-")
                        expiry = int(parts[-1]) + 900 # FIX: Add duration
                        # If slug format matches, else use 900?
                    except:
                        expiry = time.time() + 900
                        
                    rem = expiry - time.time()
                    
                    # Update State
                    orchestrator.update_market_state(slug, book_y, book_n, pos_list, time_remaining=rem)
                    
                except Exception as e:
                    msg = str(e)
                    if "404" in msg or "No orderbook" in msg:
                         logger.warning(f"[UPDATE] Market {slug} seems closed (404/No Book). Setting rem=-1.")
                         # Force end
                         orchestrator.update_market_state(slug, {}, {}, pos_list, time_remaining=-1.0)
                    else:
                         # Real error, log it
                         logger.error(f"Market update error {slug}: {e}")

            # --- C. Orchestrator Tick ---
            orchestrator.tick()
            
            # --- D. Heartbeat & Sleep ---
            elapsed = time.time() - cycle_start
            sleep_time = max(0.1, DEFAULT_CONFIG_600.TICK_INTERVAL_SEC - elapsed)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        logger.info("Stopping...")
    except Exception as e:
        logger.critical(f"Crash: {e}", exc_info=True)

if __name__ == "__main__":
    main()
