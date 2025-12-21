import time
import threading
import json
import logging
import math

from typing import Optional, Dict
from .interfaces import MarketDataProvider

# Optional Import for Websocket
try:
    import websocket
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False

class PollingProvider(MarketDataProvider):
    """
    Default fallback provider using existing Polling logic.
    """
    def __init__(self, pm_wrapper):
        self.pm_wrapper = pm_wrapper
        
    def get_snapshot(self, up_token_id: str, down_token_id: str) -> Optional[Dict[str, float]]:
        try:
            up_bid, up_ask = self.pm_wrapper.get_orderbook(up_token_id)
            down_bid, down_ask = self.pm_wrapper.get_orderbook(down_token_id)
            return {
                'up_bid': float(up_bid),
                'up_ask': float(up_ask),
                'down_bid': float(down_bid), 
                'down_ask': float(down_ask)
            }
        except Exception as e:
            print(f"[POLL] Error: {e}")
            return None

    def wait_for_update(self, timeout: float = 1.0):
        # Polling just sleeps
        time.sleep(timeout)


class WSProvider(MarketDataProvider):
    """
    Event-Driven provider using a background thread.
    - Maintains local orderbook state.
    - Triggers 'event' on significant change (sum_px).
    - Falls back to stale/None if connection lost.
    """
    def __init__(self, slug: str, up_token_id: str, down_token_id: str):
        if not WS_AVAILABLE:
            raise ImportError("websocket-client not installed. Use pip install websocket-client.")
            
        self.slug = slug
        self.up_token_id = up_token_id
        self.down_token_id = down_token_id
        
        # State
        self.book = {
            'up_bid': 0.0, 'up_ask': 0.0,
            'down_bid': 0.0, 'down_ask': 0.0
        }
        self.last_update_ts = 0.0
        self.connected = False
        
        # Concurrency
        self.lock = threading.Lock()
        self.update_event = threading.Event()
        self.stop_event = threading.Event()
        
        # Config
        self.WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
        self.STALE_TIMEOUT = 5.0
        
        self.last_sum_px = 0.0
        self.last_log_ts = 0.0
        
        # Instrumentation
        self.update_count = 0
        self.last_stats_ts = time.time()

        # Thread
        self.thread = threading.Thread(target=self._run_ws, daemon=True)
        self.thread.start()

    def get_snapshot(self, up_token_id: str, down_token_id: str) -> Optional[Dict[str, float]]:
        with self.lock:
            # Check staleness or disconnect
            if not self.connected:
                return None
                
            res = self.book.copy()
            res['last_update_ts'] = self.last_update_ts
            return res

    def wait_for_update(self, timeout: float = 1.0):
        # Block until event is set or timeout
        # We clear event before waiting to ensure we wait for NEW update
        self.update_event.wait(timeout)
        self.update_event.clear()

    def _run_ws(self):
        while not self.stop_event.is_set():
            try:
                # Disable default debug logging
                # websocket.enableTrace(True)
                
                self.ws = websocket.WebSocketApp(
                    self.WS_URL,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close
                )
                self.ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                print(f"[WS] Connect Error: {e}")
                time.sleep(5) # Backoff

    def _on_open(self, ws):
        print(f"[WS] Connected to {self.slug}")
        self.connected = True
        
        # Subscribe
        # We need to subscribe to the Token IDs
        msg = {
            "type": "Market",
            "assets_ids": [self.up_token_id, self.down_token_id]
        }
        ws.send(json.dumps(msg))

    def _on_close(self, ws, close_status_code, close_msg):
        print(f"[WS] Disconnected: {close_msg}")
        self.connected = False
        self.book = {'up_bid': 0.0, 'up_ask': 0.0, 'down_bid': 0.0, 'down_ask': 0.0}
        # Clear internal detailed books too
        if hasattr(self, '_books'):
            del self._books

    def _on_error(self, ws, error):
        print(f"[WS] Error: {error}")

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            
            # Instrumentation: Count messages
            with self.lock:
                self.update_count += 1
                now = time.time()
                if now - self.last_stats_ts > 5.0:
                    dur = now - self.last_stats_ts
                    rate = self.update_count / dur
                    print(f"[WS_STATS] updates={self.update_count} in {dur:.1f}s (~{rate:.1f}/sec)")
                    self.update_count = 0
                    self.last_stats_ts = now

            # Support both Snapshot and Update events
            # Standard CLOB WS format: [{"event_type": "book", "asset_id": "...", "bids": [], "asks": []}]
            
            # Usually comes as list
            if isinstance(data, list):
                for item in data:
                    self._process_update(item)
            else:
                self._process_update(data)
                
        except Exception as e:
            print(f"[WS] Parse Error: {e}")

    def _process_update(self, item):
        asset_id = item.get("asset_id")
        if not asset_id: return
        
        # Identify side
        if asset_id == self.up_token_id:
            prefix = "up"
        elif asset_id == self.down_token_id:
            prefix = "down"
        else:
            return

        with self.lock:
            # Check for Snapshot vs Update
            event_type = item.get("event_type", "")
            
            # We need to maintain a local simplified book (dict of price->size)
            if not hasattr(self, '_books'):
                self._books = {
                    'up': {'bids': {}, 'asks': {}},
                    'down': {'bids': {}, 'asks': {}}
                }
            
            # If Snapshot ("book"), clear existing specific book to avoid zombies
            if event_type == "book":
                self._books[prefix] = {'bids': {}, 'asks': {}}
            
            current_book = self._books[prefix]
            
            bids = item.get("bids", [])
            asks = item.get("asks", [])
            
            # Apply updates
            for b in bids:
                px = float(b.get("price", 0))
                sz = float(b.get("size", 0))
                # For snapshots, size is absolute. For updates, size 0 means remove.
                # CLOB sends size 0 for deletion in "price_change" too? 
                # Actually, "price_change" sends new size. If 0, it's gone.
                # Yes.
                if sz <= 1e-9: current_book['bids'].pop(px, None)
                else: current_book['bids'][px] = sz
                
            for a in asks:
                px = float(a.get("price", 0))
                sz = float(a.get("size", 0))
                if sz <= 1e-9: current_book['asks'].pop(px, None)
                else: current_book['asks'][px] = sz

            # Compute Bests
            if current_book['bids']:
                self.book[f'{prefix}_bid'] = max(current_book['bids'].keys())
            else:
                self.book[f'{prefix}_bid'] = 0.0
                
            if current_book['asks']:
                self.book[f'{prefix}_ask'] = min(current_book['asks'].keys())
            else:
                self.book[f'{prefix}_ask'] = 0.0 # No ask = 0? Or infinite?
                
            self.last_update_ts = time.time()
            
            # Trigger Logic
            sum_px = self.book.get('up_ask', 0) + self.book.get('down_ask', 0)
            
            # Significant change? (0.01 change in sum_px)
            # Or any change in bests?
            # User: "best ask/bid change d’au moins 0.01 ou changement de sum_px"
            if abs(sum_px - self.last_sum_px) >= 0.005: # Rounding tolerance
                self.update_event.set()
                self.last_sum_px = sum_px
                
            # Log periodically
            if time.time() - self.last_log_ts > 10: # Heartbeat log
                 print(f"[WS] Bests: UP {self.book['up_bid']:.2f}/{self.book['up_ask']:.2f} DOWN {self.book['down_bid']:.2f}/{self.book['down_ask']:.2f}")
                 self.last_log_ts = time.time()

    def close(self):
        self.stop_event.set()
        if hasattr(self, 'ws'):
            self.ws.close()
