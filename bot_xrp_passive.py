#!/usr/bin/env python3
"""
bot_xrp_passive.py
Passive Risk-First Straddle Runner for XRP 15m Markets.
"""

import os
import sys
import time
import csv
import json
import requests
from datetime import datetime, timezone
from dateutil import parser as date_parser
from dotenv import load_dotenv
import argparse

from gabagool_lite.straddle_strategy_passive import StraddleStrategyPassive, PassiveStraddleState
from gabagool_lite.polymarket_client import PolymarketClientWrapper
from gabagool_lite.safe_wrapper import SafeBuyOnlyClientWrapper
from gabagool_lite.utils_time import compute_time_remaining
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs
from gabagool_lite.market_data import PollingProvider, WSProvider

# CONFIG
HOST = "https://clob.polymarket.com"
CHAIN_ID = 137
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
LOG_FILE_PREFIX = "passive_log"
ENTRY_SIZE_SHARES = 20.0 # Confirmed user param: $10/side -> ~20 shares
LATE_REBALANCE_MAX_SUM = 1.01 # Confirmed 5m->0m param

def discover_market(target_crypto="xrp"):
    """
    Find active 15m market for target crypto.
    Try direct API approach first, fallback to constructed slugs.
    """
    print(f"[DISCOVER] Searching for {target_crypto.upper()} 15m markets...")
    
    # Method 1: Try markets API with crypto filter
    try:
        url = f"{GAMMA_API_BASE}/markets"
        params = {"closed": "false", "limit": 50, "order": "endDate", "ascending": "true"}
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        markets = data if isinstance(data, list) else data.get("markets", [])
        
        now = datetime.now(timezone.utc)
        
        for m in markets:
            slug = m.get("slug", "")
            if target_crypto.lower() not in slug.lower() or "15m" not in slug.lower():
                continue
                
            end_str = m.get("endDate")
            if not end_str: continue
            
            try:
                end = date_parser.isoparse(end_str)
            except: continue
            
            # Skip if already ended or very close to end (e.g. < 1m) to avoid startup noise
            if end.timestamp() < now.timestamp() + 60:
                continue
                
            # Parse Tokens
            token_ids = json.loads(m.get("clobTokenIds", "[]"))
            outcomes = json.loads(m.get("outcomes", "[]"))
            
            if len(token_ids) < 2: continue
            
            up_id, down_id = None, None
            for i, o in enumerate(outcomes):
                o_str = str(o).lower()
                if o_str in ("yes", "up"): up_id = token_ids[i]
                elif o_str in ("no", "down"): down_id = token_ids[i]
                
            if up_id and down_id:
                print(f"[FOUND] {slug}")
                return {
                    "slug": slug,
                    "up_token_id": up_id,
                    "down_token_id": down_id,
                    "end_ts": end.timestamp()
                }
    except Exception as e:
        print(f"[!] Discovery Method 1 Error: {e}")
        
    # Method 2: Fallback to constructed slugs if API fails
    print("[DISCOVER] Trying constructed slugs...")
    now_ts = int(time.time())

    # Try nearby timestamps
    base = (now_ts // 900) * 900
    candidate_timestamps = []
    for i in range(3):
        candidate_timestamps.append(base + (i * 900))

    for ts in sorted(list(set(candidate_timestamps))):
        if ts + 900 <= now_ts: continue
        if ts > now_ts + 30: continue

        slug = f"{target_crypto.lower()}-updown-15m-{ts}"
        try:
            url = f"{GAMMA_API_BASE}/markets?slug={slug}"
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    if not data: continue
                    market_data = data[0]
                else:
                    market_data = data
                
                if market_data.get("active", True):
                    print(f"[DISCOVER] Found via slug: {slug}")
                    end_str = market_data.get("endDate")
                    if not end_str: continue
                    end = date_parser.isoparse(end_str)
                    token_ids = json.loads(market_data.get("clobTokenIds", "[]"))
                    outcomes = json.loads(market_data.get("outcomes", "[]"))

                    up_id, down_id = None, None
                    for i, o in enumerate(outcomes):
                        if str(o).lower() in ("yes", "up"): up_id = token_ids[i]
                        elif str(o).lower() in ("no", "down"): down_id = token_ids[i]

                    if up_id and down_id:
                        print(f"[FOUND] {slug}")
                        return {
                            "slug": slug,
                            "up_token_id": up_id,
                            "down_token_id": down_id,
                            "end_ts": end.timestamp()
                        }
        except: continue
            
    print(f"[DISCOVER] No active {target_crypto} 15m markets found")    
    return None

def run():
    load_dotenv()
    
    parser = argparse.ArgumentParser(description="Passive Straddle Bot")
    parser.add_argument("--size", type=float, default=ENTRY_SIZE_SHARES, help="Entry size in shares")
    parser.add_argument("--event-driven", action="store_true", help="Use WebSocket instead of Polling")
    parser.add_argument("--entry-confirmations", type=int, default=1, help="Number of consecutive good hits before entry")
    parser.add_argument("--entry-min-dt", type=int, default=0, help="Min ms between confirmation hits")
    parser.add_argument("--entry-threshold", type=float, default=0.99, help="Max sum_px to allow entry")
    args = parser.parse_args()
    
    # AUTH
    pk = os.getenv("POLYGON_PRIVATE_KEY")
    funder = os.getenv("POLYMARKET_PROXY_ADDRESS")
    if not pk:
        print("Missing POLYGON_PRIVATE_KEY")
        return

    creds = ApiCreds(
        api_key=os.getenv("POLYMARKET_CLOB_API_KEY"),
        api_secret=os.getenv("POLYMARKET_CLOB_API_SECRET"),
        api_passphrase=os.getenv("POLYMARKET_CLOB_API_PASSPHRASE")
    )
    
    try:
        real_client = ClobClient(host=HOST, key=pk, chain_id=CHAIN_ID, signature_type=2, funder=funder, creds=creds)
        pm_wrapper = SafeBuyOnlyClientWrapper(PolymarketClientWrapper(real_client))
        print(f"[AUTH] Connected. Proxy={funder}")
    except Exception as e:
        print(f"[!] Auth Failed: {e}")
        return

    current_market = None
    strategy = None
    log_file = None
    last_log_ts = 0
    last_sync_ts = 0
    provider = None
    
    # Instrumentation Variables
    last_sum_px = 0.0
    last_stats_log_ts = 0
    orders_placed = 0
    orders_canceled = 0
    last_inv_log_ts = 0
    
    use_ws = args.event_driven
    if use_ws:
        print("[MODE] Event-Driven (WS) Enabled")
    else:
        print("[MODE] Polling (Default)")
    
    # Main Loop
    while True:
        # 1. Market Discovery
        if not current_market:
            m = discover_market("xrp")
            if m:
                current_market = m
                strategy = StraddleStrategyPassive(
                    m["slug"], 
                    entry_size_shares=args.size,
                    entry_confirmations=args.entry_confirmations,
                    entry_confirm_min_dt_ms=args.entry_min_dt,
                    entry_threshold_sum_px=args.entry_threshold
                )
                
                # Setup Log
                log_file = f"{LOG_FILE_PREFIX}_{m['slug']}.csv"
                if not os.path.exists(log_file):
                    with open(log_file, "w", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=[
                            "timestamp", "slug", "decision", "up_px", "down_px", "sum_px",
                            "inv_up", "inv_down", "skip_reason", "market_done", "time_remaining"
                        ])
                        writer.writeheader()
                
                # Setup Provider
                if use_ws:
                    try:
                        provider = WSProvider(m["slug"], m["up_token_id"], m["down_token_id"])
                    except ImportError:
                        print("[!] Websocket lib missing. Fallback to Polling.")
                        provider = PollingProvider(pm_wrapper)
                else:
                    provider = PollingProvider(pm_wrapper)
            else:
                time.sleep(5)
                continue

        # 0. FRESH DATA SYNC (Poll Orders at start of loop)
        for oid, side in [(strategy.up_order_id, "UP"), (strategy.down_order_id, "DOWN")]:
            if oid:
                s, px, sz = pm_wrapper.check_order_status(oid)
                if s == "NOT_FOUND":
                     print(f"[SYNC] {side} Order {oid} NOT FOUND on API. Clearing Zombie ID.")
                     strategy.on_order_canceled(oid)
                     continue
                     
                if sz > 0:
                    strategy.on_order_update(oid, s, fill_price=px, filled_size=sz)
                    if s is True and sz >= (strategy.entry_size_shares * 0.99):
                         print(f"[FILL] {side} Filled Complete @ {px}")
                    else:
                         # print(f"[FILL] {side} Partial {sz} @ {px}")
                         pass

        # 2. Time Check
        now = time.time()
        time_remaining = compute_time_remaining(current_market["slug"], now)
        
        if time_remaining <= 0:
            print("[MARKET] Expired.")
            if provider and hasattr(provider, 'close'): provider.close()
            current_market = None
            strategy = None
            provider = None
            time.sleep(2)
            continue
            
        # 2b. Ratchet Sync (Every 10s)
        if now - last_sync_ts > 10.0:
            try:
                 up_bal = pm_wrapper.get_token_balance(current_market["up_token_id"])
                 down_bal = pm_wrapper.get_token_balance(current_market["down_token_id"])
                 if up_bal is not None and down_bal is not None:
                     strategy.sync_manual_inventory(up_bal, down_bal)
                     last_sync_ts = now
            except Exception as e:
                print(f"[!] Sync Error: {e}")

        # 3. Data Fetch
        snapshot = provider.get_snapshot(current_market["up_token_id"], current_market["down_token_id"])
        if not snapshot:
            try:
                up_bid, up_ask = pm_wrapper.get_orderbook(current_market["up_token_id"])
                down_bid, down_ask = pm_wrapper.get_orderbook(current_market["down_token_id"])
                snapshot = {'up_bid': float(up_bid), 'up_ask': float(up_ask), 'down_bid': float(down_bid), 'down_ask': float(down_ask)}
            except:
                time.sleep(1)
                continue

        up_bid, up_ask = snapshot['up_bid'], snapshot['up_ask']
        down_bid, down_ask = snapshot['down_bid'], snapshot['down_ask']
        sum_px = up_ask + down_ask
        snapshot_ts = snapshot.get('last_update_ts', 0)

        # 3b. Spread Change Logging
        if abs(sum_px - last_sum_px) > 1e-6:
            print(f"[SPREAD_UPDATE] up={up_ask:.3f} down={down_ask:.3f} sum={sum_px:.3f} ts={now:.3f}")
            last_sum_px = sum_px

        # 4. Strategy Tick
        ts_tick_start = time.time()
        enter_result = None
        
        # B/C) Management / Surplus Logic
        mgmt_res = strategy.get_management_action(up_ask, down_ask, time_remaining)
        action = mgmt_res.get("action")
        
        if action == "SELL_URGENT":
             leg = mgmt_res.get("leg")
             qty = mgmt_res.get("qty")
             token_id = current_market["up_token_id"] if leg == "UP" else current_market["down_token_id"]
             print(f"[EXEC] SURPLUS DETECTED! Selling {qty:.2f} {leg} as Taker.")
             try:
                 order_args = OrderArgs(token_id=token_id, price=0.01, size=qty, side="SELL")
                 resp = pm_wrapper.client.client.create_and_post_order(order_args)
                 if resp:
                     strategy.on_order_update("SURPLUS_EXIT", filled=True, fill_price=0.05, filled_size=qty)
             except Exception as e:
                 print(f"[!] Surplus Sell Failed: {e}")

        # A) Entry Logic
        elif strategy.state == PassiveStraddleState.IDLE:
            enter_result = strategy.maybe_enter(up_bid, up_ask, down_bid, down_ask, time_remaining)
            if enter_result["action"] == "OPEN_STRADDLE":
                print(f"[EXEC] OPENING STRADDLE: {enter_result['size']} @ UP {enter_result['up_price']} / DOWN {enter_result['down_price']}")
                up_oid = pm_wrapper.place_limit_maker(current_market["up_token_id"], enter_result['up_price'], enter_result['size'])
                down_oid = pm_wrapper.place_limit_maker(current_market["down_token_id"], enter_result['down_price'], enter_result['size'])
                if up_oid: 
                    strategy.register_order("UP", up_oid, now)
                    orders_placed += 1
                if down_oid: 
                    strategy.register_order("DOWN", down_oid, now)
                    orders_placed += 1
                # strategy.state transition now handled inside register_order
            elif enter_result["action"] == "IDLE":
                if now - last_log_ts > 10:
                     print(f"[IDLE] Wait... Spread={(up_ask+down_ask):.3f} Reason={enter_result.get('reason')}")
                     last_log_ts = now
        
        # B) Management Logic (One-Leg)
        elif strategy.state == PassiveStraddleState.ONE_LEG_FILLED and action in ["REBALANCE_MAKER", "REBALANCE_URGENT"]:
             leg = mgmt_res.get("leg")
             qty = mgmt_res.get("qty")
             token_id = current_market["up_token_id"] if leg == "UP" else current_market["down_token_id"]
             
             if action == "REBALANCE_URGENT" and mgmt_res.get("force_taker"):
                  limit_price = min(0.99, mgmt_res.get("max_pay_price", 0.99))
                  try:
                      print(f"[EXEC] URGENT TAKER REBALANCE: {qty} {leg} @ {limit_price:.2f}")
                      order_args = OrderArgs(token_id=token_id, price=limit_price, size=qty, side="BUY")
                      resp = pm_wrapper.client.client.create_and_post_order(order_args)
                      if resp and isinstance(resp, dict):
                          orders_placed += 1
                          oid = resp.get("orderID") or resp.get("order_id")
                          strategy.register_order(leg, oid, time.time())
                          strategy.on_order_update(oid, filled=True, fill_price=limit_price, filled_size=qty)
                  except Exception as e:
                      print(f"[!] Taker Failed: {e}")

             else:
                  # MAKER REBALANCE
                  target_px = up_bid if leg == "UP" else down_bid
                  should_replace = True
                  existing_oid = strategy.up_order_id if leg == "UP" else strategy.down_order_id
                  if existing_oid:
                      try:
                           o_data = pm_wrapper.get_order(existing_oid)
                           if abs(float(o_data.get("price", 0.0)) - target_px) < 0.001:
                               should_replace = False
                               if int(now) % 10 == 0:
                                    print(f"[REBALANCE] Holding {leg} @ {target_px} (Already at target)")
                      except: pass

                  if should_replace:
                      if existing_oid:
                          success = pm_wrapper.cancel_order(existing_oid)
                          if success:
                              orders_canceled += 1
                              s, px, sz = pm_wrapper.check_order_status(existing_oid)
                              if sz > 0: strategy.on_order_update(existing_oid, s, fill_price=px, filled_size=sz)
                              strategy.on_order_canceled(existing_oid)
                              
                              # CRITICAL FIX: Re-verify state after cancellation!
                              if strategy.state in [PassiveStraddleState.STRADDLE_COMPLETE, PassiveStraddleState.DONE]:
                                   print(f"[SAFETY] Rebalance aborted: {leg} filled during cancel.")
                                   should_replace = False
                          else: should_replace = False

                      if should_replace:
                          print(f"[EXEC] REBALANCE MAKER: {qty:.2f} {leg} @ {target_px:.2f}")
                          oid = pm_wrapper.place_limit_maker(token_id, target_px, qty)
                          if oid:
                              strategy.register_order(leg, oid, now)
                              orders_placed += 1
                              strategy._last_reprice_ts = now
             
        elif action == "WAIT":
             if int(now) % 10 == 0:
                 print(f"[ONE-LEG] Wait... {mgmt_res.get('reason')}")
                 
        # 4b. Instrumentation: Latency & One-Leg Inventory
        if snapshot_ts > 0:
            lat_ms = (time.time() - snapshot_ts) * 1000
            if int(now) % 5 == 0 and lat_ms < 5000: # Periodic sample
                 print(f"[DECISION_LATENCY] spread_update → decision = {lat_ms:.1f} ms")

        if strategy.state == PassiveStraddleState.ONE_LEG_FILLED and now - last_inv_log_ts > 10:
             print(f"[INVENTORY] one-leg state: up={strategy.filled_up_shares:.1f} down={strategy.filled_down_shares:.1f} time_remaining={time_remaining:.0f}s reason={mgmt_res.get('reason')}")
             last_inv_log_ts = now

        # 4c. Instrumentation: Order Stats
        if now - last_stats_log_ts > 10:
             active_cnt = (1 if strategy.up_order_id else 0) + (1 if strategy.down_order_id else 0)
             print(f"[ORDER_STATS] placed={orders_placed} canceled={orders_canceled} active={active_cnt}")
             last_stats_log_ts = now

        # C) Watch Mode (Done/Complete)
        elif strategy.state in [PassiveStraddleState.DONE, PassiveStraddleState.STRADDLE_COMPLETE]:
            if now - last_log_ts > 10:
                print(f"[WATCH] {strategy.state.value}. Spread={(up_ask+down_ask):.3f} TimeRem={time_remaining:.0f}s")
                last_log_ts = now

        # D) Timeout Check
        to_cancel = strategy.check_timeouts(now)
        for oid, side in to_cancel:
            print(f"[TIMEOUT] Cancelling {side} order {oid} (>15s)")
            try:
                success = pm_wrapper.cancel_order(oid)
                if success:
                    orders_canceled += 1
                    s, px, sz = pm_wrapper.check_order_status(oid)
                    if sz > 0: strategy.on_order_update(oid, s, fill_price=px, filled_size=sz)
                    strategy.on_order_canceled(oid)
            except Exception as e:
                print(f"[!] Cancel Exception: {e}")

        # 6. Logging
        log_data = strategy.get_log_data(up_bid, up_ask, down_bid, down_ask, now, enter_result=enter_result)
        log_data.update({"timestamp": now, "slug": current_market["slug"], "time_remaining": time_remaining})
        with open(log_file, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(log_data.keys()))
            writer.writerow(log_data)
        
        provider.wait_for_update(timeout=1.0) 

if __name__ == "__main__":
    run()
