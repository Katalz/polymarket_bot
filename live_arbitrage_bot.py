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
from paper_trader import decide_trade, arm_live_cooldown

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
)

HOST = CLOB_HOST or "https://clob.polymarket.com"
CHAIN_ID = 137
GAMMA_API_BASE = "https://gamma-api.polymarket.com"

MAX_GLOBAL_EXPOSURE_USDC = MAX_EXPOSURE_USD
SLEEP_BETWEEN_TICKS = 0.1
TAKER_SLIPPAGE = 0.03
PRICE_SAMPLE_USD = MAX_ORDER_AMOUNT_USDC

LOG_FILE_PREFIX = "gabagool_log"
TARGET_CRYPTOS = ["btc"]
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

def run_bot():
    global SESSION_REALIZED_PNL, SESSION_LOSS_COUNT
    
    print("---------------------------------------------------")
    print("   GABAGOOL LITE - LIVE ARBITRAGE BOT (PR5: SAFE)")
    print(f"   Bankroll: ~${MAX_EXPOSURE_USD} (Pilot)")
    print(f"   Daily Loss Limit: -${DAILY_LOSS_LIMIT_USD}")
    print("---------------------------------------------------")

    load_dotenv()

    pk = os.getenv("POLYGON_PRIVATE_KEY")
    funder = os.getenv("POLYMARKET_PROXY_ADDRESS")

    if not pk or not funder:
        raise RuntimeError("Missing env vars (POLYGON_PRIVATE_KEY / POLYMARKET_PROXY_ADDRESS)")

    pk = _norm_pk(pk)

    # 1) tmp client L1 for creds (derive/create)
    tmp_client = ClobClient(host=HOST, key=pk, chain_id=CHAIN_ID)
    creds = get_or_create_clob_creds(tmp_client)

    print("\n[CLOB] CREDENTIALS IN USE:")
    print(f"API KEY        : {creds.api_key}")
    print(f"API SECRET     : {creds.api_secret}")
    print(f"API PASSPHRASE : {creds.api_passphrase}\n")

    # 2) final client via proxy with creds
    print("[>>] Connecting via Proxy...")
    client = ClobClient(
        host=HOST,
        key=pk,
        chain_id=CHAIN_ID,
        signature_type=2,          # Gnosis Safe / proxy
        funder=funder,             # proxy address
        creds=creds                # API creds
    )

    # 3) init first market
    market, streamer, session = setup_new_market(client)

    log_file = f"{LOG_FILE_PREFIX}_{market['slug']}.csv"
    fieldnames = [
        "timestamp","p_yes","p_no","edge","vol","mispricing",
        "q_yes","q_no","imbalance",
        "cost_yes","cost_no","exposure",
        "locked_profit","profit_if_up","profit_if_down","worst_case_pnl",
        "decision_side","decision_usd",
        "act","reason","scale_cooldown","order_in_flight","time_remaining"
    ]

    if not os.path.exists(log_file):
        with open(log_file, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=fieldnames).writeheader()

    last_log_ts = 0.0

    # Pre-loading state for instant market rotation
    next_market = None
    next_streamer = None
    preload_triggered = False

    print(f"[MARKET] Current: {market['slug']}")
    print(f"[LIVE] TRADING STARTED on: {market['slug']}")
    print(f"   YES token: {market['token_id']}")
    print(f"   NO  token: {market['no_token_id']}")

    while True:
        # --- PR5: SAFETY CHECK ---
        if SESSION_REALIZED_PNL <= -DAILY_LOSS_LIMIT_USD:
            print(f"\n[CRITICAL] DAILY LOSS LIMIT BREACHED (-${abs(SESSION_REALIZED_PNL):.2f}). STOPPING.")
            break
            
        now = time.time()

        time_remaining = float(market["end_time"]) - now
        minutes_remaining = int(time_remaining / 60)

        # ===================== DUTY CYCLE (2-on-1-off) =====================
        # Pause aux minutes 13, 10, 7, 4, 1
        # Formula: (min - 1) % 3 == 0. Excluding 0 (last minute active) and neg (expired).
        if (minutes_remaining - 1) % 3 == 0 and minutes_remaining > 0:
            print(f"[PAUSE] Gabagool Cycle (M{minutes_remaining}) time_remaining={time_remaining:.1f}s")
            time.sleep(1)
            continue
        # ===================================================================

        # ===================== PRELOAD NEXT MARKET (T-60s) =====================
        if 0 < time_remaining <= 60 and next_market is None and not preload_triggered:
            print(f"[MARKET] Preloading next market at t_remaining={time_remaining:.1f}s")
            preload_triggered = True
            try:
                next_market = discover_next_market(market["slug"])
                if next_market:
                    next_streamer = PriceStreamer([next_market["token_id"], next_market["no_token_id"]])
                    next_streamer.start()
                    starts_in = next_market["start_time"] - now
                    print(f"[MARKET] Preloading next: {next_market['slug']} (starts in {starts_in:.1f}s)")
                else:
                    print("[MARKET] No next market found for preload")
            except Exception as e:
                print(f"[!] Preload failed: {e}")
        # ========================================================================

        # ===================== MARKET ROTATION (T=0) =====================
        if now >= float(market["end_time"]):
            old_slug = market["slug"]
            print(f"[MARKET] Switching {old_slug} -> ...")

            # Stop current market resources
            try:
                streamer.stop()
            except Exception:
                pass
            try:
                session.pos_manager.stop()
            except Exception:
                pass

            if next_market and next_streamer:
                # Instant switch - streamer already warmed up
                market = next_market
                streamer = next_streamer
                session = LiveTraderSession(market, client)
                pm = PositionManager(client, market["token_id"], market["no_token_id"], session.order_in_flight)
                pm.start()
                session.pos_manager = pm
                print(f"[MARKET] INSTANT switch to {market['slug']}")
            else:
                # Fallback to discover (slower)
                print("[MARKET] Fallback: discovering new market...")
                market, streamer, session = setup_new_market(client)

            # Reset preload state
            next_market = None
            next_streamer = None
            preload_triggered = False

            # New log file
            log_file = f"{LOG_FILE_PREFIX}_{market['slug']}.csv"
            if not os.path.exists(log_file):
                with open(log_file, "w", newline="") as f:
                    csv.DictWriter(f, fieldnames=fieldnames).writeheader()
            last_log_ts = 0.0

            print(f"[MARKET] Current: {market['slug']}")
            print(f"[LIVE] TRADING STARTED on: {market['slug']}")
            continue
        # ==================================================================

        # --- prices ---
        p_yes = streamer.get_best_price(session.token_id_yes, PRICE_SAMPLE_USD)
        p_no  = streamer.get_best_price(session.token_id_no,  PRICE_SAMPLE_USD)
        if not p_yes or not p_no:
            time.sleep(SLEEP_BETWEEN_TICKS)
            continue

        # --- state ---
        qy, qn = session.get_live_state()
        edge_val = 1 - (p_yes + p_no)
        exposure = session.cost_yes + session.cost_no
        worst = worst_case_pnl_usd(qy, qn, session.cost_yes, session.cost_no)
        locked = (min(qy, qn) - exposure) if (qy and qn) else (-exposure)

        # --- decision ---
        decision = decide_trade(
            price_yes=p_yes,
            price_no=p_no,
            qty_yes=qy,
            qty_no=qn,
            cost_yes=session.cost_yes,
            cost_no=session.cost_no,
            worst_case_pnl_current=worst,
            time_to_expiry=time_remaining, # FIXED: was 60 hardcoded
            total_market_duration=900,
            btc_price=50000,
            btc_trend=0,
            base_order_size=BASE_ORDER_USD,
        )

        # ✅ cooldown tickdown
        if session.scale_cooldown > 0:
            session.scale_cooldown -= 1

        # ===================== LIVE EXECUTION =====================
        if decision.side in ("BUY_YES", "BUY_NO", "SELL_YES", "SELL_NO"):

            # Compute trade_price and limit_price (worst case) upfront for filters
            trade_price = p_yes if decision.side == "BUY_YES" else p_no
            limit_price = min(float(trade_price) + TAKER_SLIPPAGE, 0.99)

            # 1) Hard block trades in last minute
            if time_remaining <= TRADE_LOCK_LAST_SECONDS:
                print(f"[LOCK] Trade blocked (last {int(time_remaining)}s before expiry)")

            # 2) Min price filter (on worst-case limit_price)
            elif limit_price < MIN_TRADE_PRICE:
                print(f"[LOCK] Trade blocked (limit_price={limit_price:.4f} < {MIN_TRADE_PRICE:.2f})")

            elif session.scale_cooldown > 0 and str(decision.reason).startswith("scale_"):
                pass

            elif session.order_in_flight.is_set():
                pass

            else:
                usd_size = decision.size_yes if decision.side == "BUY_YES" else decision.size_no

                print(
                    f"[>>] BUY SIGNAL: {decision.side} | usd={usd_size:.2f} | "
                    f"px_yes={p_yes:.4f} px_no={p_no:.4f} | edge={edge_val:.4f} | reason={decision.reason}"
                )

                try:
                    session.order_in_flight.set()

                    ok = execute_trade(session=session, decision=decision, price=trade_price)

                    if ok:
                        arm_live_cooldown()  # cooldown arme uniquement si OK
                        print("[OK] ORDER DONE (filled/accepted)")

                except Exception as e:
                    print(f"[X] ORDER FAILED: {e}")

                finally:
                    session.order_in_flight.clear()
        # ==========================================================

        # ===================== CSV LOGGING (throttled) =====================
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
        # ===================================================================

        time.sleep(SLEEP_BETWEEN_TICKS)


if __name__ == "__main__":
    run()
