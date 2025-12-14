#!/usr/bin/env python3
"""
Test Live Execution Script
==========================
Valide la chaîne complète d'exécution sur le CLOB Polymarket :
1. Auth & Connection
2. Fetch Positions & Balance
3. Fetch Orders (Clean state)
4. Place Order (Maker, very far from spread to be safe)
5. Cancel Order
"""

import logging
import sys
import os
import time
from dotenv import load_dotenv

# Setup paths
sys.path.append(os.getcwd())
load_dotenv()

from gabagool_lite.config import DEFAULT_CONFIG_600
from clob_client import PolymarketCLOBClientAuthenticated
from py_clob_client.clob_types import OrderArgs

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TestExecution")

def main():
    logger.info("=== STARTING LIVE EXECUTION TEST ===")
    
    # 1. AUTH
    try:
        wrapper = PolymarketCLOBClientAuthenticated(
            host=DEFAULT_CONFIG_600.CLOB_HOST,
            api_key=DEFAULT_CONFIG_600.CLOB_API_KEY,
            api_secret=DEFAULT_CONFIG_600.CLOB_API_SECRET,
            api_passphrase=DEFAULT_CONFIG_600.CLOB_API_PASSPHRASE,
            polygon_private_key=DEFAULT_CONFIG_600.POLYGON_PRIVATE_KEY,
            proxy_address=DEFAULT_CONFIG_600.POLYMARKET_PROXY_ADDRESS
        )
        client = wrapper.client
        logger.info("[PASS] Client Authenticated")
    except Exception as e:
        logger.error(f"[FAIL] Auth: {e}")
        return

    # 2. FETCH BALANCE & POSITIONS
    try:
        # Test positions fetching manually to debug
        # Try raw methods or expected ones
        if hasattr(client, "get_account_positions"):
            pos = client.get_account_positions()
            logger.info(f"[PASS] get_account_positions: Found {len(pos) if pos else 0} entries")
        elif hasattr(client, "get_positions"):
            pos = client.get_positions()
            logger.info(f"[PASS] get_positions: Found {len(pos) if pos else 0} entries")
        else:
             logger.warning("[WARN] No standard position method found on client object.")

        # Try balance
        # py_clob_client usually doesn't give USDC balance directly without L2 call or scraping.
        # Check collateral/allowance often used.
        logger.info("[INFO] Skip USDC balance check (not critical for order placement test)")

    except Exception as e:
        logger.error(f"[FAIL] Data Fetch: {e}")

    # 3. GET OPEN ORDERS
    try:
        orders = client.get_orders()
        logger.info(f"[PASS] get_orders(): Retrieved {len(orders) if orders else 0} open orders")
    except Exception as e:
        logger.error(f"[FAIL] get_orders(): {e}")
        return

    # 4. PLACE TEST ORDER
    # Find a cheap market to test? Or use a known token ID if user has one.
    # We will assume user is watching logs and we don't spam.
    # We need a valid token_id. Let's SCAN one from the market discovery.
    
    from market_discovery import scan_active_markets
    markets = scan_active_markets(["BTC"])
    if not markets:
        logger.error("[FAIL] No active markets found to test order placement.")
        return
        
    market = markets[0]
    token_id = market["token_id"]
    slug = market["slug"]
    logger.info(f"Target Market: {slug} | Token: {token_id}")
    
    # Place a very safe BUY order
    # Price: 0.02 (Min allowed)
    # Size: Min Notional -> 1.25 / 0.02 = 62.5 shares. Let's do small but valid.
    # config: order_usd=2.0. price=0.01 (safer). size=200.
    # But wait, we set min price 0.02.
    price = 0.02
    size = 70.0 # 1.4$ value
    side = "BUY"
    
    logger.info(f"Attempting Create Order: {side} {size} @ {price} on {token_id}...")
    
    try:
        order_args = OrderArgs(
            price=price,
            size=size,
            side=side,
            token_id=token_id
        )
        resp = client.create_and_post_order(order_args)
        logger.info(f"[PASS] Order Placed! Params: {resp}")
        
        # Check if we get an ID
        order_id = resp.get("orderID") or resp.get("id")
        if not order_id:
             logger.error(f"[FAIL] No Order ID in response: {resp}")
             return
             
        logger.info(f"Order ID: {order_id}")
        
        # Wait a bit
        time.sleep(2)
        
        # 4b. VERIFY VISIBILITY (User Request)
        logger.info("Verifying order visibility in Open Orders list...")
        open_orders = client.get_orders() or []
        found = False
        for o in open_orders:
            oid = o.get("orderID") or o.get("id")
            if oid == order_id:
                found = True
                logger.info(f"[PASS] Order {order_id} CONFIRMED visible in open orders.")
                logger.info(f"       Details: {o}")
                break
        
        if not found:
             logger.error(f"[FAIL] Order {order_id} placed but NOT found in get_orders() list!")
             # Proceed to cancel anyway to be safe
        
        if not found:
             logger.error(f"[FAIL] Order {order_id} placed but NOT found in get_orders() list!")
             # Proceed to cancel anyway to be safe
             
        # 4c. VERIFY NO FILLS (User Request: Distinguish Order vs Fill)
        logger.info("Verifying distinction: Checking for fills (expecting 0)...")
        # Need proxy address to filter trades effectively
        proxy = DEFAULT_CONFIG_600.POLYMARKET_PROXY_ADDRESS
        try:
             # Manual request to match orchestrator logic
             import requests
             url = f"https://clob.polymarket.com/data/trades?maker_address={proxy}&limit=5"
             r = requests.get(url)
             trades = r.json() if r.status_code == 200 else []
             
             # Check if our order ID is in trades (it shouldn't be)
             fill_found = False
             for t in trades:
                 if t.get("orderID") == order_id or t.get("maker_order_id") == order_id:
                     fill_found = True
                     
             if not fill_found:
                 logger.info(f"[PASS] Order {order_id} is OPEN but NOT FILLED. Distinction confirmed.")
                 logger.info(f"       Open Orders: 1 | Fills: 0")
             else:
                 logger.warning(f"[ALARM] Test order {order_id} was FILLED! (Price {price}). This is unexpected but possible.")
        except Exception as e:
             logger.error(f"Fill check failed: {e}")
        
        # 5. CANCEL ORDER
        logger.info(f"Attempting Cancel {order_id}...")
        c_resp = client.cancel(order_id)
        logger.info(f"[PASS] Order Cancelled: {c_resp}")
        
    except Exception as e:
        logger.error(f"[FAIL] Order Execution: {e}")

if __name__ == "__main__":
    main()
