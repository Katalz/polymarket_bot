
import os
import csv
import time
from datetime import datetime
from typing import Dict, Any

from gabagool_lite.strategy import GabagoolLiteStrategy

class CSVBotLogger:
    def __init__(self, base_dir: str = "logs"):
        self.base_dir = base_dir
        if not os.path.exists(base_dir):
            os.makedirs(base_dir)
            
        self.writers = {}
        self.files = {}
        
    def _get_market_dir(self, slug: str) -> str:
        """Create and return market-specific directory"""
        path = os.path.join(self.base_dir, slug)
        if not os.path.exists(path):
            os.makedirs(path)
        return path
        
    def _init_writer(self, slug: str):
        """Initialize CSV writer for a market"""
        market_dir = self._get_market_dir(slug)
        filepath = os.path.join(market_dir, "history.csv")
        
        file_exists = os.path.exists(filepath)
        
        f = open(filepath, "a", newline="", encoding="utf-8")
        
        fields = [
            "timestamp", "dt", "slug", "state", 
            "best_bid_yes", "best_ask_yes", 
            "best_bid_no", "best_ask_no",
            "q_yes", "q_no", 
            "fills_yes", "fills_no",
            "exposure", "mtm_pnl", "lock_pnl",
            "decision", "time_rem",
            # Hybrid
            "btc_price", "btc_delta", "btc_conf", "btc_bias",
            "fair_yes", "fair_no"
        ]
        
        writer = csv.DictWriter(f, fieldnames=fields)
        if not file_exists:
            writer.writeheader()
            
        self.files[slug] = f
        self.writers[slug] = writer
        
    def log_tick(self, strat: GabagoolLiteStrategy, decision_reason: str = ""):
        """Log a single tick state for a strategy"""
        slug = strat.slug
        if slug not in self.writers:
            self._init_writer(slug)
            
        metrics = strat.metrics
        inv = metrics.inventory
        
        row = {
            "timestamp": int(time.time()),
            "dt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "slug": slug,
            "state": strat.state.name,
            "best_bid_yes": metrics.book_yes.best_bid if metrics.book_yes else 0.0,
            "best_ask_yes": metrics.book_yes.best_ask if metrics.book_yes else 1.0, # Ask 1.0 if unknown (safe)
            "best_bid_no": metrics.book_no.best_bid if metrics.book_no else 0.0,
            "best_ask_no": metrics.book_no.best_ask if metrics.book_no else 1.0,
            "q_yes": inv.q_yes,
            "q_no": inv.q_no,
            "fills_yes": inv.fills_yes,
            "fills_no": inv.fills_no,
            "exposure": round(metrics.exposure_usd, 2),
            "mtm_pnl": round(metrics.mark_to_market_pnl, 2),
            "lock_pnl": round(metrics.locked_pnl, 2),
            "lock_pnl": round(metrics.locked_pnl, 2),
            "decision": decision_reason or strat.last_decision_log or "IDLE", # Use strategy cache if available
            "time_rem": int(getattr(strat, "market_time_remaining", 0)),
            
            # Hybrid
            "btc_price": round(metrics.btc_now, 2),
            "btc_delta": round(metrics.delta_btc, 2),
            "btc_conf": round(metrics.conf, 2),
            "btc_bias": metrics.bias,
            "fair_yes": round(metrics.book_yes.microprice, 3) if metrics.book_yes else 0,
            "fair_no": round(metrics.book_no.microprice, 3) if metrics.book_no else 0
        }
        
        try:
            self.writers[slug].writerow(row)
            self.files[slug].flush() # Ensure write to disk immediately
        except Exception as e:
            print(f"CSV log error {slug}: {e}")
            
    def close(self):
        for f in self.files.values():
            try: f.close()
            except: pass
