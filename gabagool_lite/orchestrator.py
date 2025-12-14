
"""
Gabagool-Lite Orchestrator
==========================

Coordonne les stratégies sur plusieurs marchés.
Gère l'exécution des ordres (diffing logic) et la gestion globale du risque.
Strictement un seul marché "ACTIVE" (accumulating) à la fois.
"""

import time
import logging
from typing import Dict, List, Optional
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs

from gabagool_lite.config import GabagoolLiteConfig
from gabagool_lite.strategy import GabagoolLiteStrategy, MarketState
from gabagool_lite.metrics import format_metrics_log
from gabagool_lite.csv_logger import CSVBotLogger
from gabagool_lite.inventory import InventoryLedger
import requests
import time
from dateutil import parser as date_parser

logger = logging.getLogger(__name__)

CHAINLINK_URL = "https://data.chain.link/api/query-timescale?query=LIVE_STREAM_REPORTS_QUERY&variables=%7B%22feedId%22%3A%220x00039d9e45394f473ab1f050a1b963e6b05351e52d71e507509ada0c95ed75b8%22%7D"

class LiteOrchestrator:
    def __init__(self, client: ClobClient, config: GabagoolLiteConfig, markets: List[Dict], dry_run: bool = False):
        self.client = client
        self.cfg = config
        self.dry_run = dry_run
        self.strategies: Dict[str, GabagoolLiteStrategy] = {}
        self.active_market_slug: Optional[str] = None
        
        # Initialize strategies
        for m in markets:
            slug = m["slug"]
            self.strategies[slug] = GabagoolLiteStrategy(
                config, 
                slug, 
                m["token_id"], 
                m["no_token_id"]
            )
            
        # Init Inventory Ledgers
        self.inventory: Dict[str, InventoryLedger] = {}
        for m in markets:
            self.inventory[m["slug"]] = InventoryLedger(slug=m["slug"])
            # Inject reference into strategy
            self.strategies[m["slug"]].inventory_ledger = self.inventory[m["slug"]]
            
        self.csv_logger = CSVBotLogger(base_dir="logs")
        self.sanity_check_counter = 0
        
        # Hybrid/Lag Momentum State
        self.btc_price_now: float = 0.0
        self.btc_price_ts: float = 0.0
        self.btc_starts: Dict[str, float] = {} # slug -> start_price
        self.last_btc_poll = 0.0

    def add_strategy(self, market: Dict):
        """Dynamically add a new market strategy."""
        slug = market["slug"]
        if slug in self.strategies:
            return
            
        logger.info(f"[ORCHESTRATOR] Adding new strategy: {slug}")
        self.strategies[slug] = GabagoolLiteStrategy(
            self.cfg, 
            slug, 
            market["token_id"], 
            market["no_token_id"] # Use 'no_token_id' or mapped key?
            # Tracker usually provides {token_id_yes, token_id_no} or market discovery {token_id, no_token_id}
            # scan_active_markets returns {token_id, no_token_id}
        )
        # Handle key mismatch if tracker vs scan format differs.
        # scan returns: token_id, no_token_id.
        # Strategy init: token_id, no_token_id.
        # But wait, market dict passed here comes from scan_active_markets directly or via tracker?
        # Let's verify usage.
        
        # Init Inventory
        self.inventory[slug] = InventoryLedger(slug=slug)
        self.strategies[slug].inventory_ledger = self.inventory[slug]
        
        # Ensure CSV writer init? logic handles it on log_tick.

    def _poll_btc_price(self):
        """
        Fetches 'Real' BTC Price from Chainlink CEX Streams.
        Updates self.btc_price_now and self.btc_price_ts.
        Throttle: 0.5s (poll as often as possible to catch fresh updates)
        """
        if time.time() - self.last_btc_poll < 0.5:
            return
            
        try:
            r = requests.get(CHAINLINK_URL, timeout=2)
            data = r.json()
            # Path: data -> liveStreamReports -> nodes -> [0]
            node = data["data"]["liveStreamReports"]["nodes"][0]
            
            raw_price = float(node["price"])
            price = raw_price / 1e18
            
            # Timestamp (ISO: 2025-12-14T20:29:01+00:00)
            iso_ts = node["validFromTimestamp"]
            dt = date_parser.parse(iso_ts)
            ts = dt.timestamp()
            
            if price > 0:
                self.btc_price_now = price
                self.btc_price_ts = ts # Trust Source Timestamp
                self.last_btc_poll = time.time()
                
                lag = time.time() - ts
                logger.info(f"[BTC] Chainlink: ${price:.2f} (Lag: {time.time()-ts:.1f}s)")
        except Exception as e:
            logger.error(f"[BTC] Poll failed: {e}")
            pass # Keep old values
            
     
    def tick(self):
        """
        Boucle principale appelée périodiquement.
        """
        # 0. Poll Data
        self._poll_fills()
        if self.cfg.USE_HYBRID_MOMENTUM:
            self._poll_btc_price()

        # 1. Update Data & Determine Active Market
        self.active_market_slug = self._select_active_market()
        if self.active_market_slug:
             # logger.info(f"Active Market Selected: {self.active_market_slug}") 
             pass
        else:
             # logger.debug("No Active Market selected (all too expensive?)")
             pass
        
        # 2. Process Each Market
        for slug, strat in self.strategies.items():
            is_active = (slug == self.active_market_slug)
            
            # Inject BTC Data (CRITICAL FIX)
            if hasattr(strat, "update_btc_data"):
                strat.update_btc_data(self.btc_price_now, self.btc_price_ts)
            
            # Run Strategy Logic
            desired_orders = strat.tick(is_active)
            
            # Execute Orders (Diffing)
            self._reconcile_orders(strat, desired_orders)
            
            # Logging
            if strat.metrics.state != "IDLE" or is_active:
                logger.info(format_metrics_log(strat.metrics))
                
            # Log state to CSV
            if strat.metrics.book_yes: # Only log if data initialized
                self.csv_logger.log_tick(strat)
                
    def _select_active_market(self) -> Optional[str]:
        """
        Sélectionne le marché actif unique.
        Priorité :
        1. Marchés en état HEDGE/LOCKED (il faut finir le travail)
        2. Marché avec opportunité d'accumulation la plus forte (prix le plus bas)
        """
        # 1. Priority to existing positions
        for slug, strat in self.strategies.items():
            if strat.state in [MarketState.HEDGE, MarketState.LOCKED]:
                return slug
                
        # 2. Find best accumulation candidate
        best_slug = None
        min_price = 1.0
        
        for slug, strat in self.strategies.items():
            # Check eligibility
            if strat.state == MarketState.STOPPED:
                continue
                
            # Check prices
            # Warning: book_yes can be None if update failed
            if not strat.metrics.book_yes or not strat.metrics.book_no:
                continue

            # We look for ANY side cheap enough
            p_yes = strat.metrics.book_yes.best_ask
            p_no = strat.metrics.book_no.best_ask
            
            candidate_price = min(p_yes, p_no)
            
            # Relaxed selection threshold to allow Strategy to log rejection
            # We want to select the "best accumulation candidate" even if price is 0.40 (acc thresh 0.20)
            # So that strategy.tick() runs with is_active=True and logs "Price 0.40 > 0.20"
            selection_threshold = 0.95 
            
            if candidate_price <= selection_threshold and candidate_price < min_price:
                min_price = candidate_price
                best_slug = slug
                
        return best_slug

    def _reconcile_orders(self, strat: GabagoolLiteStrategy, desired_orders: List[OrderArgs]):
        """
        Algorithme de réconciliation "Smart Diff".
        Minimise les appels API en ne modifiant que ce qui change.
        """
        # ============================================
        # DRY RUN / SIMULATION MODE
        # ============================================
        if self.dry_run:
            if not desired_orders:
                return

            # Simulate IMMEDIATE FILL for all desired orders
            # User wants to "assume it fills" to see strategy flow.
            # We skip "Cancel" logic because fills are instant -> no open orders remain.
            
            for order in desired_orders:
                # Mock Fill
                sim_fill = {
                    "side": order.side.upper(),
                    "token_id": order.token_id,
                    "token_type": "YES" if order.token_id == strat.tokens["YES"] else "NO", # Derived
                    "size": float(order.size),
                    "price": float(order.price),
                    "trade_id": f"sim_{int(time.time()*1000)}",
                    "timestamp": int(time.time())
                }
                
                # Update Ledger
                self.inventory[strat.slug].apply_fill(sim_fill)
                
                logger.info(f"[DRY RUN] \033[93mSIMULATED FILL\033[0m: {sim_fill['side']} {sim_fill['token_type']} {sim_fill['size']} @ {sim_fill['price']}")
                
            # Clear desired (they are filled)
            return

        # ============================================
        # REAL MODE
        # ============================================
        # 1. Get Actual Open Orders (This should ideally be cached or fetched once globally)
        # For 'Lite', fetching per market is safer but slower.
        # Assuming we can get orders for these tokens.
        
        try:
            # Fetch orders for both tokens
            # Fetch orders with robust filtering
            # Method 1: Try passing market/asset_id
            # Method 2: Fetch all and filter
            
            # Since get_orders(token_id=...) failed, we suspect it needs 'market' or doesn't filter.
            # We'll try fetching ALL open orders once (efficient) or per market.
            # Let's try fetching all open orders (no args) and filter manually.
            # This avoids "unexpected keyword" issues if we guess wrong name.
            
            all_orders = []
            # Try fetching per token explicitly (often more reliable than global)
            try:
                o_yes = self.client.get_orders(order_args=OrderArgs(token_id=strat.tokens["YES"])) # Some clients need args object
            except:
                try: o_yes = self.client.get_orders(market=strat.tokens["YES"]) 
                except: o_yes = []
            
            try:
               o_no = self.client.get_orders(market=strat.tokens["NO"])
            except: o_no = []
            
            # Use specific results or fallback to global
            if o_yes or o_no:
                 all_orders = (o_yes or []) + (o_no or [])
            else:
                 # Global fallback
                 try: all_orders = self.client.get_orders() or []
                 except: all_orders = []

            # Filter for this market's tokens
            relevant_tokens = {strat.tokens["YES"], strat.tokens["NO"]}
            current_orders = []
            
            for o in all_orders:
                # Normalize order object/dict
                # py_clob_client order obj usually has 'asset_id' or 'token_id'
                # Check dict or object attributes
                if isinstance(o, dict):
                    tid = o.get("asset_id") or o.get("token_id")
                else:
                    tid = getattr(o, "asset_id", None) or getattr(o, "token_id", None)
                
                if tid and tid in relevant_tokens:
                    current_orders.append(o)
            
            # CRITICAL: Update Metrics for Heartbeat
            strat.metrics.open_orders_count = len(current_orders)
            if len(current_orders) > 0:
                 logger.debug(f"[{strat.slug}] Found {len(current_orders)} open orders.")

        except Exception as e:
            logger.error(f"[{strat.slug}] Failed to fetch orders: {e}")
            return

        # 2. Map existing orders
        # Key = (side, price) approx
        existing_map = {}
        for o in current_orders:
            # Struct varies depending on client (dict or obj)
            # Adapt safe access
            oid = o.get("orderID") or o.get("id")
            price = float(o.get("price", 0))
            side = o.get("side", "").upper()
            size = float(o.get("size", 0))
            existing_map[oid] = {"price": price, "side": side, "size": size, "obj": o}

        # 3. Match Logic
        to_cancel = []
        to_create = []
        
        # Copy desired to valid list to mutate
        remaining_desired = desired_orders.copy()
        
        # Check existing orders: Keep or Cancel?
        for oid, info in existing_map.items():
            keep = False
            for i, d in enumerate(remaining_desired):
                # Match criteria: Side same, Price very close
                # Size: if we want to be strict, check size. 
                # For accumulation, size >= desired is usually ok, but strict is better.
                price_match = abs(info["price"] - float(d.price)) < 0.001
                side_match = info["side"] == d.side.upper()
                
                if price_match and side_match:
                    # It matches! Keep this order.
                    keep = True
                    # Remove from desired (satisfied)
                    remaining_desired.pop(i)
                    break
            
            if not keep:
                to_cancel.append(oid)
        
        # Whatever is left in remaining_desired must be created
        to_create = remaining_desired
        
        # 4. Execute Diff
        if not to_cancel and not to_create:
            return # Nothing to do
            
        logger.info(f"[{strat.slug}] Reconcile: Cancel {len(to_cancel)}, Create {len(to_create)}")
        
        # Bulk Cancel if supported, else loop
        if to_cancel:
            try:
                self.client.cancel(to_cancel) # Assuming client supports list of IDs
            except:
                # Fallback one by one
                for oid in to_cancel:
                    try: self.client.cancel(oid)
                    except: pass
                    
        # Create
        for order_arg in to_create:
            try:
                self.client.create_and_post_order(order_arg)
            except Exception as e:
                logger.error(f"[{strat.slug}] Order Create Failed: {e}")

    def _poll_fills(self):
        """Poll recent fills and update local ledgers."""
        try:
            # Polymarket API endpoint for trades (user fills)
            # Need to filter by our maker address (the proxy)
            # We assume config has PROXY_ADDRESS
            proxy = self.cfg.POLYMARKET_PROXY_ADDRESS or self.client.funder
            
            # Fetch recent trades (limit 20 is enough for polling)
            url = f"https://clob.polymarket.com/data/trades?maker_address={proxy}&limit=20"
            r = requests.get(url, timeout=3)
            if r.status_code == 200:
                trades = r.json()
                for t in trades:
                    # t format: {price, size, side, asset_id, timestamp, hash...}
                    token_id = t.get("asset_id")
                    
                    # Find which ledger this belongs to
                    target_ledger = None
                    token_type = None
                    
                    for slug, strat in self.strategies.items():
                        if token_id == strat.tokens["YES"]:
                            target_ledger = self.inventory[slug]
                            token_type = "YES"
                            break
                        elif token_id == strat.tokens["NO"]:
                            target_ledger = self.inventory[slug]
                            token_type = "NO"
                            break
                    
                    if target_ledger:
                        # Normalize trade dict for ledger
                        # trade_id is usually 'hash' or 'match_id'? API docs usually say 'hash'.
                        fill_data = {
                            "side": t.get("side", "").upper(),
                            "token_id": token_id,
                            "token_type": token_type,
                            "size": float(t.get("size", 0)),
                            "price": float(t.get("price", 0)),
                            "trade_id": t.get("hash") or t.get("match_id") or str(t.get("timestamp")),
                            "timestamp": int(t.get("timestamp", 0))
                        }
                        target_ledger.apply_fill(fill_data)
            else:
               pass
               # logger.warning(f"Poll fills error: {r.status_code}")

        except Exception as e:
            logger.error(f"Poll fills exception: {e}")

    def update_market_state(self, slug: str, book_yes, book_no, positions, time_remaining: float = 9999.0):
        """Called by main loop to inject data into strategies."""
        if slug in self.strategies:
            strat = self.strategies[slug]
            
            # Update Price Data
            # Hybrid Momentum Injection
            btc_t0 = 0.0
            btc_now = self.btc_price_now
            
            if self.cfg.USE_HYBRID_MOMENTUM and btc_now > 0:
                if slug not in self.btc_starts:
                    self.btc_starts[slug] = btc_now
                    logger.info(f"[{slug}] Hybrid Tracking Start: BTC T0 = ${btc_now:.2f}")
                
            btc_t0 = self.btc_starts.get(slug, 0.0)
            
            strat.update_market_data_prices(book_yes, book_no, time_remaining, btc_now=btc_now, btc_t0=btc_t0)
            
            # Sanity Check Inventory (Periodic)
            ledger = self.inventory[slug]
            
            # Extract API Qty
            api_q_yes = 0.0
            api_q_no = 0.0
            
            for p in positions:
                if p["asset_id"] == strat.tokens["YES"]: api_q_yes = float(p["size"])
                if p["asset_id"] == strat.tokens["NO"]: api_q_no = float(p["size"])
            
            # Compare
            self.sanity_check_counter += 1
            # SKIP Sanity Check in Dry Run (User Req: Logic Divergence is expected)
            if not self.dry_run and self.sanity_check_counter % 10 == 0: # Every ~10 ticks
                ok, msg = ledger.sanity_check(api_q_yes, api_q_no)
                if not ok:
                    logger.critical(f"[{slug}] {msg}")
                    logger.critical("!!! EMERGENCY STOP: INVENTORY DIVERGENCE !!!")
                    strat.state = MarketState.STOPPED # Force Stop
                    # Trigger cancel all immediately?
                    try: self.client.cancel_all()
                    except: pass

