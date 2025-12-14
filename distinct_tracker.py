#!/usr/bin/env python3
"""
gabagool_tracker.py
Bot qui capture les trades de Gabagool en temps réel sur les marchés BTC 15min.
- Tourne en fond
- Sauvegarde dans un CSV par marché
- Rotation automatique vers le marché suivant à l'expiration
"""

import os
import time
import csv
import json
import requests
from datetime import datetime, timezone
from dateutil import parser

# =========================
# CONFIGURATION
# =========================

GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

# Adresse Gabagool
GABAGOOL_PROXY = "0xe00740bce98a594e26861838885ab310ec3b548c"

# Intervalle de polling (secondes)
POLL_INTERVAL = 2.0

# Dossier de sortie pour les CSV
OUTPUT_DIR = "distinct_trades"

# Cryptos à tracker
TARGET_CRYPTOS = ["btc"]

# =========================
# CLASSES
# =========================

class GabagoolTracker:
    def __init__(self):
        self.current_market = None
        self.current_csv = None
        self.seen_tx_hashes = set()  # Pour dédupliquer par hash
        self.trades_buffer = []  # Buffer de tous les trades en mémoire
        self.state = {"q_yes": 0, "q_no": 0, "expo": 0}
        self.books_cache = {}  # {token_id: {"bid": float, "ask": float, "ts": float}}
        
        # Créer le dossier de sortie
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
    def discover_current_market(self):
        """Trouve le marché crypto 15m actif"""
        print("[DISCOVER] Searching for active BTC 15m market...")
        
        url = f"{GAMMA_API}/events"
        params = {
            "closed": "false",
            "limit": 200,
            "order": "endDate",
            "ascending": "true",
        }
        
        try:
            events = requests.get(url, params=params, timeout=10).json()
        except Exception as e:
            print(f"[ERROR] API error: {e}")
            return None
        
        now = datetime.now(timezone.utc)
        
        def slug_ok(slug):
            s = (slug or "").lower()
            if not any(c in s for c in TARGET_CRYPTOS):
                return False
            return ("15m" in s) or ("15min" in s)
        
        for e in events:
            slug = e.get("slug", "")
            if not slug_ok(slug):
                continue
            
            try:
                start = parser.isoparse(e["startDate"])
                end = parser.isoparse(e["endDate"])
            except:
                continue
            
            # Le marché doit être actif (déjà commencé, pas encore fini)
            if not (start <= now < end):
                continue
            
            markets = e.get("markets") or []
            if not markets:
                continue
            
            m = markets[0]
            
            try:
                token_ids = json.loads(m.get("clobTokenIds", "[]"))
                outcomes = json.loads(m.get("outcomes", "[]"))
            except:
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
                "token_id_yes": yes_id,
                "token_id_no": no_id,
                "start_time": start.timestamp(),
                "end_time": end.timestamp(),
            }
            
            print(f"[OK] Found: {slug}")
            print(f"     Ends at: {end.isoformat()}")
            return market
        
        print("[WARN] No active BTC 15m market found")
        return None
    
    def setup_market(self, market):
        """Configure le tracking pour un nouveau marché"""
        self.current_market = market
        self.seen_tx_hashes = set()
        self.books_cache = {}
        self.trades_buffer = []  # Reset buffer for new market
        self.state = {"q_yes": 0, "q_no": 0, "expo": 0}  # Reset state

        
        # Créer le fichier CSV
        csv_path = os.path.join(OUTPUT_DIR, f"gabagool_{market['slug']}.csv")
        self.current_csv = csv_path
        
        # Charger les trades existants si le fichier existe déjà
        if os.path.exists(csv_path):
            print(f"[CSV] Loading existing trades from {csv_path}")
            self.load_existing_trades()
        else:
            # Créer le header pour nouveau fichier
            with open(csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "time_utc", "timestamp", "side", "price", "size", 
                    "usd_volume", "q_yes", "q_no", "exposure", "locked_pnl",
                    "tx_hash", "outcome", "maker_taker", "book_bid", "book_ask"
                ])
        
        print(f"[MARKET] Tracking: {market['slug']}")
        print(f"[CSV] Output: {csv_path}")
    
    def load_existing_trades(self):
        """Charge les trades existants depuis le CSV"""
        try:
            with open(self.current_csv, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    tx_hash = row.get("tx_hash", "")
                    if tx_hash:
                        self.seen_tx_hashes.add(tx_hash)
                        # Convertir les types numériques
                        trade = {
                            "time_utc": row.get("time_utc", ""),
                            "timestamp": int(row.get("timestamp", 0)) if row.get("timestamp") else 0,
                            "side": row.get("side", ""),
                            "price": float(row.get("price", 0)) if row.get("price") else 0.0,
                            "size": float(row.get("size", 0)) if row.get("size") else 0.0,
                            "usd_volume": float(row.get("usd_volume", 0)) if row.get("usd_volume") else 0.0,
                            "tx_hash": tx_hash,
                            "outcome": row.get("outcome", ""),
                            "maker_taker": row.get("maker_taker", ""),
                            "book_bid": float(row.get("book_bid", 0)) if row.get("book_bid") else 0.0,
                            "book_ask": float(row.get("book_ask", 0)) if row.get("book_ask") else 0.0
                        }
                        self.trades_buffer.append(trade)
            print(f"[INFO] Loaded {len(self.trades_buffer)} existing trades")
        except Exception as e:
            print(f"[WARN] Could not load existing trades: {e}")
    
    def fetch_gabagool_trades(self):
        """Récupère les trades récents de Gabagool"""
        try:
            params = {
                "proxyWallet": GABAGOOL_PROXY,
                "limit": 100  # Derniers 100 trades
            }
            r = requests.get(f"{DATA_API}/trades", params=params, timeout=10)
            if r.status_code != 200:
                return []
            return r.json() or []
        except Exception as e:
            print(f"[ERROR] Fetch trades: {e}")
            return []
            
    def fetch_order_book(self, token_id):
        """Récupère le orderbook pour un token"""
        try:
            r = requests.get(f"{CLOB_API}/book", params={"token_id": token_id}, timeout=5)
            if r.status_code == 200:
                return r.json()
        except:
            pass
        return None

    def refresh_books(self):
        """Met à jour le cache des books pour le marché actuel"""
        if not self.current_market:
            return

        ts = time.time()
        # On met à jour pour YES et NO (ou tout token pertinent)
        for tid in [self.current_market["token_id_yes"], self.current_market["token_id_no"]]:
            book = self.fetch_order_book(tid)
            if book:
                # Parser best bid/ask
                bids = book.get("bids", [])
                asks = book.get("asks", [])
                
                best_bid = 0.0
                if bids:
                    try:
                        # On trie explicitement pour être sûr d'avoir le meilleur bid (plus haut)
                        # Le format est [{"price": "0.xx", "size": "yy"}, ...]
                        # On convertit en float pour le tri
                        sorted_bids = sorted(bids, key=lambda x: float(x["price"]), reverse=True)
                        best_bid = float(sorted_bids[0]["price"])
                        # print(f"[DEBUG] Token {tid} Best Bid: {best_bid} (Raw Top: {sorted_bids[0]})")
                    except Exception as e:
                        print(f"[WARN] Bid sort error: {e}")
                        pass
                
                best_ask = 0.0
                if asks:
                    try:
                        # On trie explicitement pour être sûr d'avoir le meilleur ask (plus bas)
                        sorted_asks = sorted(asks, key=lambda x: float(x["price"]), reverse=False)
                        best_ask = float(sorted_asks[0]["price"])
                        # print(f"[DEBUG] Token {tid} Best Ask: {best_ask} (Raw Top: {sorted_asks[0]})")
                    except Exception as e:
                        print(f"[WARN] Ask sort error: {e}")
                        pass
                
                # Debug temporaire si on voit 0.01/0.99
                if best_bid <= 0.02 and best_ask >= 0.98:
                     print(f"[DEBUG] SUSPICIOUS BOOK for {tid}: Bid={best_bid}, Ask={best_ask}. Count: {len(bids)} bids, {len(asks)} asks.")
                     if bids: print(f"   Top Bids: {[b['price'] for b in sorted_bids[:3]]}")
                     if asks: print(f"   Top Asks: {[a['price'] for a in sorted_asks[:3]]}")

                self.books_cache[tid] = {
                    "bid": best_bid,
                    "ask": best_ask,
                    "ts": ts
                }
            
    
    def process_trades(self, trades):
        """Traite les nouveaux trades et les ajoute au buffer"""
        if not self.current_market:
            return
        
        target_slug = self.current_market["slug"]
        new_count = 0
        
        for t in trades:
            # Filtrer par slug
            if t.get("slug") != target_slug and t.get("eventSlug") != target_slug:
                continue
            
            # Dédupliquer par hash de transaction uniquement
            tx_hash = t.get("transactionHash", "")
            if not tx_hash or tx_hash in self.seen_tx_hashes:
                continue
            
            self.seen_tx_hashes.add(tx_hash)
            
            # Déterminer le side
            outcome = t.get("outcome", "").lower()
            if outcome in ["yes", "up"]:
                side = "YES"
            elif outcome in ["no", "down"]:
                side = "NO"
            else:
                side = "UNKNOWN"
            
            # Stocker dans le buffer (pas encore de calcul de métriques)
            timestamp = t.get("timestamp", 0)
            time_utc = datetime.utcfromtimestamp(timestamp).isoformat() if timestamp else ""
            price = float(t.get("price", 0))
            size = float(t.get("size", 0))
            usd = price * size
            
            # Déterminer Maker/Taker
            # L'objet trade `t` a un champ `side` qui est BUY ou SELL
            raw_side = t.get("side", "").upper() # BUY / SELL
            # `asset` est le token ID
            asset = t.get("asset", "")
            
            # Vérifier si le trade appartient au marché actuel
            if self.current_market:
                valid_tokens = [self.current_market["token_id_yes"], self.current_market["token_id_no"]]
                if asset not in valid_tokens:
                    # Ce trade n'est pas pour ce marché (probablement le prochain marché qui a commencé)
                    # On l'ignore pour ne pas polluer ce CSV
                    continue
            
            role = "UNKNOWN"
            book_bid, book_ask = 0.0, 0.0
            
            if asset in self.books_cache:
                book = self.books_cache[asset]
                book_bid = book["bid"]
                book_ask = book["ask"]
                book_ts = book["ts"]
                
                # Check delay
                if abs(book_ts - timestamp) < 60: # Tolérance 1 min
                    # Si le book est vide (0.0), on ne peut pas inférer
                    if book_bid > 0 or book_ask > 0:
                        if raw_side == "BUY":
                            # Si ask existe et price >= ask -> TAKER
                            if book_ask > 0 and price >= book_ask:
                                role = "TAKER"
                            # Si bid existe et price <= bid -> MAKER
                            elif book_bid > 0 and price <= book_bid:
                                role = "MAKER"
                            else:
                                # Entre les deux
                                dist_ask = abs(price - book_ask) if book_ask > 0 else 999
                                dist_bid = abs(price - book_bid) if book_bid > 0 else 999
                                
                                if dist_ask < dist_bid:
                                    role = "TAKER"
                                else:
                                    role = "MAKER"
                        elif raw_side == "SELL":
                            if book_bid > 0 and price <= book_bid:
                                role = "TAKER"
                            elif book_ask > 0 and price >= book_ask:
                                role = "MAKER"
                            else:
                                dist_ask = abs(price - book_ask) if book_ask > 0 else 999
                                dist_bid = abs(price - book_bid) if book_bid > 0 else 999
                                
                                if dist_bid < dist_ask:
                                    role = "TAKER"
                                else:
                                    role = "MAKER"
                else:
                    role = "UNKNOWN (DELAY)"

            trade_data = {
                "time_utc": time_utc,
                "timestamp": timestamp,
                "side": side,
                "price": price,
                "size": size,
                "usd_volume": usd,
                "tx_hash": tx_hash,
                "outcome": outcome,
                "maker_taker": role,
                "book_bid": book_bid,
                "book_ask": book_ask
            }
            
            self.trades_buffer.append(trade_data)
            new_count += 1
            print(f"[TRADE] {side} {raw_side} | {role} | {size:.2f} @ {price:.4f} (Book: {book_bid}/{book_ask})")
        
        if new_count > 0:
            print(f"[INFO] {new_count} new trades added to buffer")
            # Réécrire le CSV trié après chaque batch
            self.write_sorted_csv()
    
    def write_sorted_csv(self):
        """Trie les trades par timestamp et recalcule les métriques, puis écrit le CSV"""
        if not self.trades_buffer:
            return
        
        # Normaliser tous les timestamps en int pour permettre le tri
        for trade in self.trades_buffer:
            ts = trade.get("timestamp", 0)
            if isinstance(ts, str):
                try:
                    trade["timestamp"] = int(ts) if ts else 0
                except:
                    trade["timestamp"] = 0
            elif ts is None:
                trade["timestamp"] = 0
            
            # Normaliser aussi les autres champs numériques
            for field in ["price", "size", "usd_volume"]:
                val = trade.get(field, 0)
                if isinstance(val, str):
                    try:
                        trade[field] = float(val) if val else 0.0
                    except:
                        trade[field] = 0.0
                elif val is None:
                    trade[field] = 0.0
        
        # Trier par timestamp (maintenant tous sont des int)
        sorted_trades = sorted(self.trades_buffer, key=lambda x: x.get("timestamp", 0))
        
        # Recalculer les métriques cumulatives
        state = {"q_yes": 0, "q_no": 0, "expo": 0}
        
        for trade in sorted_trades:
            side = trade["side"]
            size = float(trade["size"])
            usd = float(trade["usd_volume"])
            
            if side == "YES":
                state["q_yes"] += size
            elif side == "NO":
                state["q_no"] += size
            state["expo"] += usd
            
            # Calculer locked PnL
            q_yes = state["q_yes"]
            q_no = state["q_no"]
            expo = state["expo"]
            locked = min(q_yes, q_no) - expo if q_yes > 0 and q_no > 0 else -expo
            
            # Mettre à jour le trade avec les métriques
            trade["q_yes"] = q_yes
            trade["q_no"] = q_no
            trade["exposure"] = expo
            trade["locked_pnl"] = locked
        
        # Mettre à jour l'état global
        self.state = state
        
        # Réécrire le CSV complet
        with open(self.current_csv, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "time_utc", "timestamp", "side", "price", "size", 
                "usd_volume", "q_yes", "q_no", "exposure", "locked_pnl",
                "tx_hash", "outcome", "maker_taker", "book_bid", "book_ask"
            ])
            
            for trade in sorted_trades:
                writer.writerow([
                    trade["time_utc"],
                    trade["timestamp"],
                    trade["side"],
                    trade["price"],
                    trade["size"],
                    trade["usd_volume"],
                    trade["q_yes"],
                    trade["q_no"],
                    trade["exposure"],
                    trade["locked_pnl"],
                    trade["tx_hash"],
                    trade["outcome"],
                    trade.get("maker_taker", ""),
                    trade.get("book_bid", 0),
                    trade.get("book_ask", 0)
                ])
    
    def print_status(self):
        """Affiche le statut actuel"""
        if not self.current_market:
            return
        
        now = time.time()
        remaining = self.current_market["end_time"] - now
        
        q_yes = self.state["q_yes"]
        q_no = self.state["q_no"]
        expo = self.state["expo"]
        locked = min(q_yes, q_no) - expo if q_yes > 0 and q_no > 0 else -expo
        
        print(f"[STATUS] {self.current_market['slug']} | "
              f"Time: {remaining:.0f}s | "
              f"YES: {q_yes:.0f} | NO: {q_no:.0f} | "
              f"Expo: ${expo:.0f} | Locked: ${locked:.0f}")
    
    def run(self):
        """Boucle principale du tracker"""
        print("=" * 60)
        print("GABAGOOL TRADE TRACKER - Real-time Capture")
        print("=" * 60)
        print(f"Target: {GABAGOOL_PROXY}")
        print(f"Output: {OUTPUT_DIR}/")
        print(f"Poll interval: {POLL_INTERVAL}s")
        print("=" * 60)
        
        last_status = 0
        
        while True:
            try:
                now = time.time()
                
                # Vérifier si on a besoin d'un nouveau marché
                if self.current_market is None or now >= self.current_market["end_time"]:
                    if self.current_market:
                        print(f"\n[MARKET] {self.current_market['slug']} ended")
                        print(f"[FINAL] YES: {self.state['q_yes']:.2f} | NO: {self.state['q_no']:.2f}")
                        locked = min(self.state['q_yes'], self.state['q_no']) - self.state['expo']
                        print(f"[FINAL] Exposure: ${self.state['expo']:.2f} | Locked PnL: ${locked:.2f}")
                    
                    # Trouver le prochain marché
                    market = self.discover_current_market()
                    if market:
                        self.setup_market(market)
                    else:
                        print("[WAIT] No market found, retrying in 30s...")
                        time.sleep(30)
                        continue
                
                # Récupérer et traiter les trades
                self.refresh_books() # Mise à jour des books avant traitement
                trades = self.fetch_gabagool_trades()
                self.process_trades(trades)
                
                # Afficher le statut toutes les 10 secondes
                if now - last_status >= 10:
                    self.print_status()
                    last_status = now
                
                # Attendre avant le prochain poll
                time.sleep(POLL_INTERVAL)
                
            except KeyboardInterrupt:
                print("\n[STOP] Tracker stopped by user")
                break
            except Exception as e:
                print(f"[ERROR] {e}")
                time.sleep(5)


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    tracker = GabagoolTracker()
    tracker.run()
