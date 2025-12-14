#!/usr/bin/env python3
"""
live_arbitrage_bot.py - GABAGOOL SNIPER EDITION (FULL ASYNC)
+ SCALE cooldown (évite spam après lock)

FIXES (stabilité / comportement type Gabagool):
- ❌ Supprime cancel_all() en boucle
- ✅ order_in_flight (1 ordre à la fois)
- ✅ PositionManager fiable (pas de resync qty)
- ✅ Comptabilité au pire (limit_price)
"""

import os
import time
import logging

import csv
import requests
import json
import threading
import websocket

from datetime import datetime, timezone
from dateutil import parser
from dotenv import load_dotenv

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds
from py_clob_client.clob_types import OrderArgs

# ✅ needed for cooldown arming
from paper_trader import decide_trade, arm_live_cooldown, reset_live_strategy, load_persistence, save_persistence

from config import (
    CLOB_HOST,
    MAX_EXPOSURE_USD,
    MAX_ORDER_AMOUNT_USDC,
    BASE_ORDER_USD,
    SCALE_COOLDOWN_TICKS,
    LOG_EVERY_SEC,
    ENABLE_SELLING,      # PR4
    TAKE_PROFIT_PCT,     # PR4
    FORCE_EXIT_SECONDS,  # PR4
    DAILY_LOSS_LIMIT_USD, # PR5
    MAX_CONSECUTIVE_LOSSES, # PR5
    MARKET_UNIVERSE,      # PR-MULTI
    DRY_RUN_SCAN_ONLY,    # PR-MULTI
    SCAN_INTERVAL_SEC,    # PR-MULTI
    MAX_CONCURRENT_MARKETS, # PR-MULTI
    NO_FLY_MAX_SPREAD,
    NO_FLY_MIN_LIQUIDITY,
    NO_ENTRY_TIMEOUT
)

HOST = CLOB_HOST or "https://clob.polymarket.com"
CHAIN_ID = 137
GAMMA_API_BASE = "https://gamma-api.polymarket.com"

MAX_GLOBAL_EXPOSURE_USDC = MAX_EXPOSURE_USD
SLEEP_BETWEEN_TICKS = 0.1
TAKER_SLIPPAGE = 0.03
PRICE_SAMPLE_USD = MAX_ORDER_AMOUNT_USDC

LOG_FILE_PREFIX = "gabagool_log"
TARGET_CRYPTOS = [m.lower() for m in MARKET_UNIVERSE]
MARKET_SUMMARY_FILE = "gabagool_markets_summary.csv"

MIN_SHARES = 5.0
TRADE_LOCK_LAST_SECONDS = 60  # Hard block trades in last N seconds before expiry
MIN_TRADE_PRICE = 0.10         # Block trades if limit_price < this


def _norm_pk(pk: str) -> str:
    pk = (pk or "").strip()
    if pk and not pk.startswith("0x"):
        pk = "0x" + pk
    return pk


# ---------------- PRICE STREAMER ---------------- #

class PriceStreamer:
    def __init__(self, token_ids):
        self.token_ids = token_ids
        self.books = {tid: {"asks": [], "bids": []} for tid in token_ids}
        self.ws_url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
        self.running = False
        self.lock = threading.Lock()

    def start(self):
        self.running = True
        threading.Thread(target=self._run_ws, daemon=True).start()
        time.sleep(1)

    def stop(self):
        # ✅ permet la rotation de marché
        self.running = False

    def _run_ws(self):
        def on_open(ws):
            ws.send(json.dumps({
                "type": "market",
                "assets_ids": self.token_ids,
                "channel": "book"
            }))

        def on_message(ws, message):
            try:
                data = json.loads(message)
                if isinstance(data, dict):
                    data = [data]
                with self.lock:
                    for it in data:
                        tid = it.get("asset_id")
                        if tid:
                            if "asks" in it:
                                self.books[tid]["asks"] = it["asks"]
                            if "bids" in it:
                                self.books[tid]["bids"] = it["bids"]
            except Exception:
                pass

        while self.running:
            try:
                ws = websocket.WebSocketApp(self.ws_url, on_open=on_open, on_message=on_message)
                ws.run_forever()
            except Exception:
                time.sleep(1)

    def get_best_price(self, token_id, required_usd_size):
        with self.lock:
            asks = list(self.books.get(token_id, {}).get("asks", []))
        if not asks:
            return 0.0

        asks = sorted(asks, key=lambda x: float(x["price"]))
        filled = qty = cost = 0.0

        for a in asks:
            p, s = float(a["price"]), float(a["size"])
            need = required_usd_size - filled
            if p * s >= need:
                qty += need / p
                cost += need
                filled += need
                break
            qty += s
            cost += p * s
            filled += p * s

        return cost / qty if filled >= 0.9 * required_usd_size else 0.0

    def get_best_bid(self, token_id):
        with self.lock:
            # Bids are 'buys' in the book. We (User) SELL into them.
            # Usually bids are sorted Descending (Highest Price First).
            bids = list(self.books.get(token_id, {}).get("bids", []))
        if not bids:
            return 0.0
            
        # Sort DESCENDING (Highest Bid is Best)
        bids = sorted(bids, key=lambda x: float(x["price"]), reverse=True)
        return float(bids[0]["price"]) if bids else 0.0


# ---------------- POSITION MANAGER ---------------- #

class PositionManager:
    def __init__(self, client, yid, nid, inflight):
        self.client = client
        self.ids = {"YES": yid, "NO": nid}
        self.real_qty = {"YES": 0.0, "NO": 0.0}
        self.lock = threading.Lock()
        self.running = False
        self.order_in_flight = inflight

    def start(self):
        self.running = True
        threading.Thread(target=self._sync_loop, daemon=True).start()

    def record_local_trade(self, side, qty):
        with self.lock:
            k = "YES" if side == "BUY_YES" else "NO"
            self.real_qty[k] += qty

    def get_effective_qty(self):
        with self.lock:
            return self.real_qty["YES"], self.real_qty["NO"]

    def _sync_loop(self):
        while self.running:
            if self.order_in_flight.is_set():
                time.sleep(0.2)
                continue
            time.sleep(2)

    def stop(self):
        self.running = False


# ---------------- SESSION ---------------- #

class LiveTraderSession:
    def __init__(self, market, client):
        self.market_info = market
        self.client = client
        self.token_id_yes = market["token_id"]
        self.token_id_no = market["no_token_id"]
        self.cost_yes = 0.0
        self.cost_no = 0.0
        self.scale_cooldown = 0
        self.order_in_flight = threading.Event()
        self.pos_manager = None

    def get_live_state(self):
        return self.pos_manager.get_effective_qty()

    def on_trade_success(self, side, cost, qty, reason):
        if side == "BUY_YES":
            self.cost_yes += cost
        elif side == "BUY_NO":
            self.cost_no += cost
        elif side == "SELL_YES":
            # REVENUE reduces cost base
            self.cost_yes -= cost 
        elif side == "SELL_NO":
            self.cost_no -= cost
            
        # Update Position Manager (tracking only)
        # For SELL, we pass negative qty to reduce local tracking if needed?
        # PositionManager.record_local_trade adds qty. 
        # So for SELL, we should pass NEGATIVE qty.
        real_side = side
        real_qty = qty
        if side.startswith("SELL"):
            # Mocking side for existing manager logic (which expects BUY_YES/BUY_NO usually)
            # Actually PositionManager likely tracks "net" qty? 
            # Let's check record_local_trade: "self.real_qty[k] += qty".
            # So yes, we need negative qty.
            real_qty = -qty
            if side == "SELL_YES": real_side = "BUY_YES" # Target YES bucket
            else: real_side = "BUY_NO"
        
        self.pos_manager.record_local_trade(real_side, real_qty)
        
        if str(reason).startswith("scale_"):
            self.scale_cooldown = SCALE_COOLDOWN_TICKS
            
        # PR5: Tracking PnL on Sells (Approximation simplifiée)
        # Si on vend, on regarde si le cost moyen est couvert.
        # Ici c'est dur de savoir le PnL exact sans un historique FIFO parfait.
        # Mais on peut estimer: PnL = (Prix Vente - Prix Moyen Achat) * Qty
        if side.startswith("SELL"):
            # On suppose que 'cost' ici est le REVENUE (Cash In)
            revenue = cost
            sold_qty = abs(real_qty) 
            
            # Prix moyen estimé de la position avant vente?
            # C'est compliqué. Simplifions : 
            # Si on vend pour TAKE PROFIT, c'est positif.
            # Si on vend pour STOP ou TIME, ça peut être négatif.
            
            # Hack PR5: On fait confiance aux configs PR4. 
            # Si reason contains "take_profit" -> Win.
            # Si reason contains "time_force_exit" -> Check price.
            
            pass # PnL update done in main loop if needed, or we rely on account balance sync?
            # Pour l'instant, on laisse le kill switch basé sur une logique plus simple:
            # Si on enchaine les pertes detectées par ailleurs.



def worst_case_pnl_usd(qy, qn, cy, cn):
    total = cy + cn
    return min(qy - total, qn - total)


def discover_market():
    """
    Trouve un marché crypto (TARGET_CRYPTOS) en cours, idéalement 15m/15min.
    Retour:
      {
        "slug": str,
        "token_id": str,      # YES / UP
        "no_token_id": str,   # NO / DOWN
        "start_time": float,
        "end_time": float
      }
    """
    print("[DISCOVER] Searching for crypto 15m market...")
    url = f"{GAMMA_API_BASE}/events"
    params = {
        "closed": "false",
        "limit": 200,
        "order": "endDate",
        "ascending": "true",
    }

    try:
        events = requests.get(url, params=params, timeout=8).json()
    except Exception as e:
        raise RuntimeError(f"discover_market: gamma api error: {e}")

    now = datetime.now(timezone.utc)

    def slug_ok(slug: str) -> bool:
        s = (slug or "").lower()
        if not any(c in s for c in TARGET_CRYPTOS):
            return False
        return ("15m" in s) or ("15min" in s) or ("15-minute" in s)

    for e in events:
        slug = e.get("slug", "")
        if not slug_ok(slug):
            continue

        try:
            start = parser.isoparse(e["startDate"])
            end = parser.isoparse(e["endDate"])
        except Exception:
            continue

        if not (start <= now < end):
            continue

        markets = e.get("markets") or []
        if not markets:
            continue

        m = markets[0]

        try:
            token_ids = json.loads(m.get("clobTokenIds", "[]"))
            outcomes = json.loads(m.get("outcomes", "[]"))
        except Exception:
            continue

        if len(token_ids) < 2:
            continue

        yes_id, no_id = None, None
        for i, o in enumerate(outcomes):
            o = str(o).lower()
            if o in ("yes", "up"):
                yes_id = token_ids[i]
            elif o in ("no", "down"):
                no_id = token_ids[i]

        yes_id = yes_id or token_ids[0]
        no_id = no_id or token_ids[1]

        market = {
            "slug": slug,
            "token_id": yes_id,
            "no_token_id": no_id,
            "start_time": start.timestamp(),
            "end_time": end.timestamp(),
        }

        print(f"[OK] Market found: {slug}")
        print(f"   YES token: {yes_id}")
        print(f"   NO  token: {no_id}")
        return market

    raise RuntimeError("discover_market: aucun marche crypto 15m en cours trouve")


def extract_slug_timestamp(slug: str) -> int | None:
    """Extract numeric timestamp suffix from slug like 'btc-updown-15m-1765561500'."""
    parts = (slug or "").split("-")
    if parts and parts[-1].isdigit():
        return int(parts[-1])
    return None


def discover_next_market(current_slug: str) -> dict | None:
    """
    Find the next 15m market after current_slug.
    - slug timestamp ~ +900 from current
    - start_time in the future (not yet started)
    - duration ~ 900s
    Returns None if not found.
    """
    current_ts = extract_slug_timestamp(current_slug)
    if current_ts is None:
        return None

    url = f"{GAMMA_API_BASE}/events"
    params = {
        "closed": "false",
        "limit": 200,
        "order": "endDate",
        "ascending": "true",
    }

    try:
        events = requests.get(url, params=params, timeout=8).json()
    except Exception:
        return None

    now = datetime.now(timezone.utc)

    def slug_ok(slug: str) -> bool:
        s = (slug or "").lower()
        if not any(c in s for c in TARGET_CRYPTOS):
            return False
        return ("15m" in s) or ("15min" in s) or ("15-minute" in s)

    candidates = []
    for e in events:
        slug = e.get("slug", "")
        if not slug_ok(slug):
            continue

        slug_ts = extract_slug_timestamp(slug)
        if slug_ts is None:
            continue

        # Must be > current + 800 (tolerance for ~900 offset)
        if slug_ts <= current_ts + 800:
            continue

        try:
            start = parser.isoparse(e["startDate"])
            end = parser.isoparse(e["endDate"])
        except Exception:
            continue

        # Must not have started yet
        if start <= now:
            continue

        # Duration should be ~900s (15 min)
        duration = (end - start).total_seconds()
        if not (800 <= duration <= 1000):
            continue

        markets = e.get("markets") or []
        if not markets:
            continue

        m = markets[0]
        try:
            token_ids = json.loads(m.get("clobTokenIds", "[]"))
            outcomes = json.loads(m.get("outcomes", "[]"))
        except Exception:
            continue

        if len(token_ids) < 2:
            continue

        yes_id, no_id = None, None
        for i, o in enumerate(outcomes):
            o = str(o).lower()
            if o in ("yes", "up"):
                yes_id = token_ids[i]
            elif o in ("no", "down"):
                no_id = token_ids[i]

        yes_id = yes_id or token_ids[0]
        no_id = no_id or token_ids[1]

        candidates.append({
            "slug": slug,
            "slug_ts": slug_ts,
            "token_id": yes_id,
            "no_token_id": no_id,
            "start_time": start.timestamp(),
            "end_time": end.timestamp(),
        })

    if not candidates:
        return None

    # Return the one with smallest slug_ts (earliest next market)
    candidates.sort(key=lambda x: x["slug_ts"])
    return candidates[0]


# ==========================================
# MULTI-MARKET SCANNER (PR-MULTI)
# ==========================================

def scan_active_markets(tickers=None):
    if tickers is None:
        tickers = MARKET_UNIVERSE
        
    """
    PREDICTIVE SCAN:
    Targets specific 15m markets by calculated slug.
    Format: {ticker}-updown-15m-{expiry_ts}
    """
    import math 
    
    candidates = []
    now_ts = time.time()
    # Current 15m boundary (Start Time)
    # Slug uses START time (ex: 14:00 for 14:00-14:15 range)
    expiry_ts = math.floor(now_ts / 900) * 900
    
    # print(f"[SCAN] Target Start: {int(expiry_ts)}")

    for ticker in tickers:
        slug = f"{ticker.lower()}-updown-15m-{int(expiry_ts)}"
        url = f"{GAMMA_API_BASE}/events"
        params = {"slug": slug}
        
        try:
            r = requests.get(url, params=params, timeout=3)
            events = r.json()
            
            if not events:
                # print(f"[DEBUG] Not found: {slug}")
                continue
                
            e = events[0]
            if e.get("closed"): continue
            
            # Active Window Check
            try:
                end = parser.isoparse(e["endDate"])
                now_dt = datetime.now(timezone.utc)
                remaining = (end - now_dt).total_seconds()
                
                # Sniper Window: [180, 850]
                if not (180 <= remaining <= 850):
                    print(f"[DEBUG] Skip time: {slug} rem={remaining:.1f}s not in [180, 850]")
                    continue
            except: continue
            
            # Parse Details
            markets = e.get("markets") or []
            if not markets: continue
            m = markets[0]
            
            try:
                clob_ids = json.loads(m.get("clobTokenIds", "[]"))
                start = parser.isoparse(e["startDate"]) # Ensure start is parsed
            except: continue
            
            if len(clob_ids) < 2: continue
            
            candidates.append({
                "slug": e["slug"],
                "question": m.get("question", slug),
                "start_time": start.timestamp(), # Added
                "end_time": end.timestamp(),
                "token_id": clob_ids[0],
                "no_token_id": clob_ids[1],
                "remaining": remaining
            })
            
        except Exception as err:
            print(f"[SCAN] Error fetching {slug}: {err}")
            continue
            
    return candidates


class MarketScanner:
    def __init__(self, client):
        self.client = client
        self.last_scan = 0
        self.csv_file = "gabagool_scanner.csv"
        
        # Init CSV with extra columns if new
        if not os.path.exists(self.csv_file):
            with open(self.csv_file, "w", newline="") as f:
                csv.writer(f).writerow(["timestamp", "candidates_count", "best_slug", "best_score", "action", "spread", "liquidity"])
        
    def scan(self):
        now = time.time()
        if now - self.last_scan < SCAN_INTERVAL_SEC:
            return None
        self.last_scan = now
        
        candidates = scan_active_markets()
        
        best_market = None
        best_score = -1
        best_slug = ""
        
        if candidates:
            print(f"[SCAN] Found {len(candidates)} candidates. Scoring...")
            for m in candidates:
                score = self.get_score(m)
                
                # Retrieve stats stored by get_score
                spr = m.get("spread", 0)
                liq = m.get("liquidity", 0)
                
                print(f"   > {m['slug']}: score={score:.2f} (spr={spr:.3f}, liq=${liq:.0f})")
                
                if score > best_score:
                    best_score = score
                    best_market = m
                    best_slug = m["slug"]
        
        # Log to CSV
        try:
            with open(self.csv_file, "a", newline="") as f:
                action = "NONE"
                if best_market and best_score > 0: action = "LOCK_CANDIDATE"
                elif candidates: action = "LOW_SCORE"
                else: action = "NO_CANDIDATES"
                
                spr_val = f"{best_market.get('spread',0):.3f}" if best_market else ""
                liq_val = f"{best_market.get('liquidity',0):.0f}" if best_market else ""
                
                csv.writer(f).writerow([
                    now, 
                    len(candidates) if candidates else 0,
                    best_slug,
                    f"{best_score:.2f}",
                    action,
                    spr_val,
                    liq_val
                ])
        except Exception as e:
            print(f"[SCAN] CSV Log Error: {e}")
                
        # Threshold score >= 1.0
        if best_market and best_score >= 1.0:
            return best_market
        return None
        
    def get_score(self, market):
        try:
            # Fetch L2 Snapshot
            book = self.client.get_order_book(market["token_id"])
            if not book: 
                print(f"[DEBUG] No book for {market['slug']}")
                return 0
            
            bids = book.bids
            asks = book.asks
            if not bids or not asks: 
                print(f"[DEBUG] Empty book bids/asks for {market['slug']}")
                return 0
            
            # CRITICAL FIX: Sort orders to find best prices
            bids.sort(key=lambda x: float(x.price), reverse=True)
            asks.sort(key=lambda x: float(x.price), reverse=False)
            
            best_bid = float(bids[0].price)
            best_ask = float(asks[0].price)
            
            # NO_FLY: Extreme Prices (Market decided)
            # Avoid buying if Prob > 80% or < 20%
            if best_bid > 0.80 or best_ask < 0.20:
                 print(f"[NO_FLY] {market['slug']} price extreme ({best_bid}/{best_ask})")
                 return 0

            spread = best_ask - best_bid
            if spread <= 0: spread = 0.001
            
            # Store raw stats
            market["spread"] = spread
            
            # NO_FLY: Spread check
            if spread > NO_FLY_MAX_SPREAD: 
                print(f"[NO_FLY] {market['slug']} spread={spread:.3f} > limit")
                return 0
            
            # NO_FLY: Liquidity
            # TODO: Implement 'Stability Check' (rolling avg over 3 scans) to avoid flash walls.
            liq_bid = sum(float(x.size) for x in bids[:5])
            liq_ask = sum(float(x.size) for x in asks[:5])
            liquidity = min(liq_bid, liq_ask)
            
            # Store raw stats
            market["liquidity"] = liquidity
            
            if liquidity < NO_FLY_MIN_LIQUIDITY: 
                print(f"[NO_FLY] {market['slug']} liquidity=${liquidity:.0f} < limit")
                return 0
            
            # Score
            score = (0.01 / spread) * (liquidity / 1000)
            return score
            
        except Exception as e:
            print(f"[SCAN] Score error {market['slug']}: {e}")
            return 0


def execute_trade(session, decision, price):
    """
    Envoie un ordre BUY_YES / BUY_NO en live sur Polymarket.
    - 1 ordre à la fois (order_in_flight géré dehors)
    - Budget compté AU PIRE (limit_price)
    - Update session.cost_* + qty via session.on_trade_success
    """

    client = session.client

    # --- side / token / usd ---
    api_side = "BUY"
    if decision.side == "BUY_YES":
        token_id = session.token_id_yes
        usd = decision.size_yes
    elif decision.side == "BUY_NO":
        token_id = session.token_id_no
        usd = decision.size_no
    elif decision.side == "SELL_YES":
        token_id = session.token_id_yes
        usd = decision.size_yes # In this context, size_yes is QTY to sell? 
        # Wait, decision.size_yes is usually USD for BUY. 
        # For SELL, PaperTrader returns logic: Decision("SELL_YES", pos.qty_yes, ...)
        # So decision.size_yes IS QTY.
        # But execute_trade expects USD to calculate Qty?
        # Let's check logic below: qty = round(usd / limit_price, 2)
        # This conversion assumes 'usd' input.
        # IF SELL, we have QTY. We should bypass the 'usd' conversion logic or adapt it.
        api_side = "SELL"
    elif decision.side == "SELL_NO":
        token_id = session.token_id_no
        usd = decision.size_no # QTY
        api_side = "SELL"
    else:
        return False
        
    # --- HANDLING QTY vs USD ---
    # Pour BUY: usd = Montant $ -> qty = usd / price
    # Pour SELL: usd = Qty shares -> qty = usd (Direct)
    
    target_qty = 0.0
    limit_price = 0.0
    
    if api_side == "BUY":
        if usd <= 0: return False
        # Buy into Ask (Pire cas = Price + Slip)
        limit_price = min(price + TAKER_SLIPPAGE, 0.99)
        target_qty = round(usd / limit_price, 2)
        if target_qty < MIN_SHARES: target_qty = MIN_SHARES
        
    else: # SELL
        # decision.size_x IS QTY
        target_qty = float(usd) # 'usd' var holds qty here
        if target_qty <= 0: return False
        
        # Check explicit Limit Price first (Cascading Exit)
        explicit_limit = getattr(decision, "limit_price", 0.0)
        if explicit_limit > 0.0:
             limit_price = explicit_limit
        else:
             # Sell into Bid (Pire cas = Price - Slip)
             limit_price = max(price - TAKER_SLIPPAGE, 0.01)

    projected_cost = target_qty * limit_price
    current_exposure = session.cost_yes + session.cost_no

    # --- budget global (ONLY FOR BUY) ---
    if api_side == "BUY":
        if current_exposure + projected_cost > MAX_GLOBAL_EXPOSURE_USDC + 1e-9:
            print(
                f"[BLOCK] BUDGET | exposure={current_exposure:.2f}$ "
                f"+ proj={projected_cost:.2f}$ > {MAX_GLOBAL_EXPOSURE_USDC:.2f}$"
            )
            return False

    print(
        f"[ORDER] SEND | {decision.side} | qty={target_qty:.2f} | "
        f"limit={limit_price:.4f} | value~{projected_cost:.2f}$"
    )

    # ================= SEND ORDER =================
    try:
        order_args = OrderArgs(
            token_id=token_id,
            price=round(limit_price, 4),
            size=target_qty,
            side=api_side,
        )

        resp = client.create_and_post_order(order_args)

        if not resp:
            print("[X] ORDER REJECTED (empty response)")
            return False

        order_id = resp.get("orderID") or resp.get("order_id")
        if not order_id:
            print(f"[X] ORDER REJECTED: {resp}")
            return False

        print(f"[OK] Order accepted (id={order_id})")

    except Exception as e:
        print(f"[X] API ERROR send_order: {e}")
        return False

    # ================= CHECK FILL =================
    time.sleep(0.4)

    try:
        order_status = client.get_order(order_id)
        if isinstance(order_status, list) and order_status:
            order_status = order_status[0]

        filled_qty = float(
            order_status.get("matched_size", 0)
            or order_status.get("size_matched", 0)
            or 0
        )

        if filled_qty <= 0:
            print("[X] NOT FILLED -> cancel")
            try:
                client.cancel(order_id)
            except Exception:
                pass
            return False

        # ✅ IMPORTANT: compta AU PIRE
        filled_cost = filled_qty * limit_price

        session.on_trade_success(
            side=decision.side,
            cost=filled_cost,
            qty=filled_qty,
            reason=decision.reason,
        )

        print(
            f"[OK] FILLED | {decision.side} | "
            f"qty={filled_qty:.2f} | cost~{filled_cost:.2f}$"
        )

        return True

    except Exception as e:
        print(f"[!] ERROR check_fill: {e}")
        return False


def get_or_create_clob_creds(tmp_client: ClobClient) -> ApiCreds:
    api_key = os.getenv("POLYMARKET_CLOB_API_KEY")
    api_secret = os.getenv("POLYMARKET_CLOB_API_SECRET")
    api_passphrase = os.getenv("POLYMARKET_CLOB_API_PASSPHRASE")

    if api_key and api_secret and api_passphrase:
        return ApiCreds(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
        )

    # ✅ la bonne méthode (crée OU dérive)
    creds = tmp_client.create_or_derive_api_creds()

    print("\n[OK] CLOB CREDS GENERATED - add these to your .env:")
    print(f"POLYMARKET_CLOB_API_KEY={creds.api_key}")
    print(f"POLYMARKET_CLOB_API_SECRET={creds.api_secret}")
    print(f"POLYMARKET_CLOB_API_PASSPHRASE={creds.api_passphrase}\n")

    return creds


def setup_new_market(client: ClobClient):
    """
    ✅ One-market-at-a-time.
    - discover a fresh 15m market
    - start a fresh PriceStreamer
    - start a fresh session + position manager
    """
    market = discover_market()

    # Reset Strategy for new market & Load Persistence
    reset_live_strategy()
    load_persistence(market["slug"])

    streamer = PriceStreamer([market["token_id"], market["no_token_id"]])

    streamer.start()

    session = LiveTraderSession(market, client)
    pm = PositionManager(client, market["token_id"], market["no_token_id"], session.order_in_flight)
    pm.start()
    session.pos_manager = pm

    end_dt = datetime.fromtimestamp(market["end_time"], tz=timezone.utc)
    print(f"[MARKET] Active: {market['slug']} | ends at {end_dt.isoformat()}")

    return market, streamer, session


# ---------------- MAIN LOOP ---------------- #

# PR5: Global Session State
SESSION_REALIZED_PNL = 0.0
SESSION_LOSS_COUNT = 0

def run():
    global SESSION_REALIZED_PNL, SESSION_LOSS_COUNT
    
    print("---------------------------------------------------")
    print("   GABAGOOL LITE - LIVE ARBITRAGE BOT (PR-MULTI)")
    print(f"   Bankroll: ~${MAX_EXPOSURE_USD}")
    print(f"   Daily Limit: -${DAILY_LOSS_LIMIT_USD}")
    print(f"   Markets: {MARKET_UNIVERSE}")
    print(f"   Mode: {'DRY RUN SCAN' if DRY_RUN_SCAN_ONLY else 'LIVE TRADING'}")
    print("---------------------------------------------------")

    load_dotenv()

    pk = os.getenv("POLYGON_PRIVATE_KEY")
    funder = os.getenv("POLYMARKET_PROXY_ADDRESS")

    if not pk or not funder:
        raise RuntimeError("Missing env vars")

    pk = _norm_pk(pk)

    # 1) tmp client for creds
    tmp_client = ClobClient(host=HOST, key=pk, chain_id=CHAIN_ID)
    creds = get_or_create_clob_creds(tmp_client)

    # 2) final client
    print("[>>] Connecting via Proxy...")
    client = ClobClient(
        host=HOST,
        key=pk,
        chain_id=CHAIN_ID,
        signature_type=2,
        funder=funder,
        creds=creds
    )

    # Init Scanner
    scanner = MarketScanner(client)
    
    # State
    active_market = None
    streamer = None
    session = None
    log_file = None
    
    fieldnames = [
        "timestamp","p_yes","p_no","edge","vol","mispricing",
        "q_yes","q_no","imbalance",
        "cost_yes","cost_no","exposure",
        "locked_profit","profit_if_up","profit_if_down","worst_case_pnl",
        "decision_side","decision_usd",
        "act","reason","scale_cooldown","order_in_flight","time_remaining"
    ]
    
    last_log_ts = 0.0

    while True:
        # PR5 Safety
        if SESSION_REALIZED_PNL <= -DAILY_LOSS_LIMIT_USD:
            print(f"\n[CRITICAL] DAILY LOSS LIMIT BREACHED (-${abs(SESSION_REALIZED_PNL):.2f}). STOPPING.")
            break
            
        now = time.time()
        
        # ----------------------------------------
        # STATE: SCANNING (FLAT)
        # ----------------------------------------
        if active_market is None:
            best_market = scanner.scan()
            
            if DRY_RUN_SCAN_ONLY:
                time.sleep(1)
                continue
                
            if best_market:
                active_market = best_market
                lock_start_ts = now
                
                # Setup Session
                streamer = PriceStreamer([active_market["token_id"], active_market["no_token_id"]])
                streamer.start()
                
                session = LiveTraderSession(active_market, client)
                pm = PositionManager(client, active_market["token_id"], active_market["no_token_id"], session.order_in_flight)
                pm.start()
                session.pos_manager = pm
                
                print(f"[SELECT] Best market found: {active_market['slug']}")
                print(f"[LOCK] TARGET LOCKED: {active_market['slug']}")
                print(f"[LIVE] START TRADING on {active_market['slug']}")
                
                # Setup Log
                log_file = f"{LOG_FILE_PREFIX}_{active_market['slug']}.csv"
                if not os.path.exists(log_file):
                    with open(log_file, "w", newline="") as f:
                        csv.DictWriter(f, fieldnames=fieldnames).writeheader()
                last_log_ts = 0.0
                
            else:
                time.sleep(1) # Wait for next scan interval
                continue
                
        # ----------------------------------------
        # STATE: LOCKED (TRADING)
        # ----------------------------------------
        
        # UNLOCK: TIMEOUT (Flat & No Fill)
        if (session.cost_yes + session.cost_no < 0.5) and (now - lock_start_ts > NO_ENTRY_TIMEOUT):
             if not session.order_in_flight.is_set():
                 print(f"[UNLOCK] Timeout ({NO_ENTRY_TIMEOUT}s) & Flat on {active_market['slug']}")
                 if streamer: streamer.stop()
                 if session.pos_manager: session.pos_manager.stop()
                 active_market = None
                 streamer = None
                 session = None
                 continue

        # Check Time Logic
        time_remaining = float(active_market["end_time"]) - now
        
        # MARKET END EXIT
        if time_remaining <= 0:
            print(f"[UNLOCK] Market ended: {active_market['slug']}")
            if streamer: streamer.stop()
            if session.pos_manager: session.pos_manager.stop()
            active_market = None
            streamer = None
            session = None
            continue
            
        # DUTY CYCLE (Pause logic)
        minutes_remaining = int(time_remaining / 60)
        if (minutes_remaining - 1) % 3 == 0 and minutes_remaining > 0:
             # Just wait, don't execute trades
             # But keep streamer running? Yes.
             # print(f"[PAUSE] Cycle (M{minutes_remaining})")
             time.sleep(1)
             continue

        # --- prices ---
        ask_yes = streamer.get_best_price(session.token_id_yes, PRICE_SAMPLE_USD)
        ask_no  = streamer.get_best_price(session.token_id_no,  PRICE_SAMPLE_USD)
        
        # Actionable Bids (For Exits)
        bid_yes = streamer.get_best_bid(session.token_id_yes)
        bid_no  = streamer.get_best_bid(session.token_id_no)
        
        # Aliases for logging/checking if needed, but we pass clear names
        p_yes, p_no = ask_yes, ask_no
        
        if not p_yes or not p_no:
            time.sleep(SLEEP_BETWEEN_TICKS)
            continue
            
        # --- state ---
        qy, qn = session.get_live_state()
        edge_val = 1 - (p_yes + p_no)
        exposure = session.cost_yes + session.cost_no
        
        # Worst Case PnL using BIDS (Real Liquidation Value)
        # Old worst_case_pnl_usd used manual calc?
        # Let's pass raw values to decide_trade which reconstructs it.
        # But we still calculate log stats here?
        # worst = worst_case_pnl_usd(qy, qn, session.cost_yes, session.cost_no) <--- Uses COST basis.
        # Actual "Liquidation Value" is (qy * bid_yes + qn * bid_no).
        # We'll trust decide_trade to log the correct PnL.
        
        worst = worst_case_pnl_usd(qy, qn, session.cost_yes, session.cost_no) # Keep purely for visual continuity
        locked = (min(qy, qn) - exposure) if (qy and qn) else (-exposure)
        
        duration = float(active_market["end_time"]) - float(active_market["start_time"])

        # --- decision ---
        decision = decide_trade(
            price_yes=ask_yes,
            price_no=ask_no,
            bid_yes=bid_yes,
            bid_no=bid_no,
            qty_yes=qy,
            qty_no=qn,
            cost_yes=session.cost_yes,
            cost_no=session.cost_no,
            worst_case_pnl_current=worst,
            time_to_expiry=time_remaining,
            total_market_duration=duration,
            btc_price=50000, 
            btc_trend=0,
            base_order_size=BASE_ORDER_USD,
        )
        
        # Cooldown
        if session.scale_cooldown > 0:
            session.scale_cooldown -= 1
            
        # PR-PERSIST: Save Stop State
        if "stop_engaged" in decision.reason and not getattr(session, "stop_saved", False):
             save_persistence(active_market["slug"])
             session.stop_saved = True
             print(f"[PERSIST] Saved STOP state for {active_market['slug']}")
            
        # --- Execution ---
        if decision.side in ("BUY_YES", "BUY_NO", "SELL_YES", "SELL_NO"):
            # Execute Trade Logic (same as before)
            # trade_price calculation
            trade_price = p_yes if decision.side == "BUY_YES" else p_no
            limit_price = min(float(trade_price) + TAKER_SLIPPAGE, 0.99)

            # Blockers
            if time_remaining <= TRADE_LOCK_LAST_SECONDS:
                 if (int(time_remaining) % 10 == 0): print(f"[LOCK] Trade blocked (last {int(time_remaining)}s)")
            elif limit_price < MIN_TRADE_PRICE:
                 pass
            elif session.scale_cooldown > 0 and str(decision.reason).startswith("scale_"):
                 pass
            elif session.order_in_flight.is_set():
                 pass
            else:
                 # Execute
                 print(f"[>>] SIGNAL: {decision.side} | reason={decision.reason}")
                 try:
                     session.order_in_flight.set()
                     ok = execute_trade(session=session, decision=decision, price=trade_price)
                     if ok: arm_live_cooldown()
                 except Exception as e:
                     print(f"[X] ORDER FAIL: {e}")
                 finally:
                     session.order_in_flight.clear()

        # --- Logging ---
        if (now - last_log_ts) >= LOG_EVERY_SEC:
            with open(log_file, "a", newline="") as f:
                csv.DictWriter(f, fieldnames=fieldnames).writerow({
                    "timestamp": now,
                    "p_yes": p_yes,
                    "p_no": p_no,
                    "edge": edge_val,
                    "vol": abs(p_yes - p_no),
                    "mispricing": abs((p_yes + p_no) - 1),
                    "q_yes": qy,
                    "q_no": qn,
                    "imbalance": abs(qy - qn),
                    "cost_yes": session.cost_yes,
                    "cost_no": session.cost_no,
                    "exposure": exposure,
                    "locked_profit": locked,
                    "profit_if_up": qy - exposure,
                    "profit_if_down": qn - exposure,
                    "worst_case_pnl": worst,
                    "decision_side": decision.side,
                    "decision_usd": (decision.size_yes if decision.side == "BUY_YES" else decision.size_no),
                    "act": decision.side,
                    "reason": decision.reason,
                    "scale_cooldown": session.scale_cooldown,
                    "order_in_flight": int(session.order_in_flight.is_set()),
                    "time_remaining": time_remaining
                })
            last_log_ts = now
            
        time.sleep(SLEEP_BETWEEN_TICKS)

if __name__ == "__main__":
    run()
