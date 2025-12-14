
import unittest
import sys
import os
import logging
from unittest.mock import MagicMock

# Setup path
sys.path.append(os.getcwd())

from gabagool_lite.strategy import GabagoolLiteStrategy, MarketState
from gabagool_lite.config import GabagoolLiteConfig
from gabagool_lite.inventory import InventoryLedger

# Quiet logs
logging.basicConfig(level=logging.CRITICAL)

class TestStrategyLogic(unittest.TestCase):
    def setUp(self):
        # Use Test Config
        self.cfg = GabagoolLiteConfig(
            TOTAL_BANKROLL=600.0,
            ACCUMULATE_YES_THRESHOLD=0.30, # Buy if Ask <= 0.30
            ACCUMULATE_NO_THRESHOLD=0.30,
            MIN_ORDER_USD=2.0,
            MAX_ORDER_USD=5.0,
            ORDER_USD=3.0,
            MIN_PRICE_ALLOWED=0.02, # Floor
            TIME_NO_ENTRY=300.0, # T-5m
            TIME_FORCE_EXIT=60.0 # T-60s
        )
        self.strat = GabagoolLiteStrategy(self.cfg, "test-market", "YES_ID", "NO_ID")
        self.strat.inventory_ledger = InventoryLedger(slug="test-market")

    def test_idle_when_prices_high(self):
        # Case: Prices are expensive (0.50). Should stay IDLE.
        book_yes = {'asks': [{'price': '0.50', 'size': '100'}], 'bids': [{'price': '0.45', 'size': '100'}]}
        book_no = {'asks': [{'price': '0.55', 'size': '100'}], 'bids': [{'price': '0.50', 'size': '100'}]}
        
        self.strat.update_market_data_prices(book_yes, book_no, time_remaining=1000)
        orders = self.strat.tick(is_active_market=True)
        
        self.assertEqual(self.strat.state, MarketState.IDLE)
        self.assertEqual(len(orders), 0)

    def test_accumulate_trigger(self):
        # Case: YES price drops to 0.25 (Threshold 0.30). Should ACCUMULATE.
        # Use spread 0.04 (Ask 0.25, Bid 0.21) to clearly pass "spread <= 0.05" check
        book_yes = {'asks': [{'price': '0.25', 'size': '500'}], 'bids': [{'price': '0.21', 'size': '500'}]}
        book_no = {'asks': [{'price': '0.80', 'size': '500'}], 'bids': [{'price': '0.76', 'size': '500'}]}
        
        self.strat.update_market_data_prices(book_yes, book_no, time_remaining=1000)
        orders = self.strat.tick(is_active_market=True)
        
        self.assertEqual(self.strat.state, MarketState.ACCUMULATE)
        self.assertEqual(self.strat.accum_side, "YES")
        
        # Verify Orders
        # Should generate Ladder orders on BID side (Maker)
        # We expect buy orders around best bid 0.20
        self.assertTrue(len(orders) > 0)
        for o in orders:
            self.assertEqual(o.side, "BUY")
            self.assertEqual(o.token_id, "YES_ID")
            self.assertTrue(0.02 <= float(o.price) <= 0.30) # Within safe bounds

    def test_min_price_floor_clamping(self):
        # Case: Bid is very low (0.01). Bot should NOT place orders below 0.02.
        book_yes = {'asks': [{'price': '0.25', 'size': '500'}], 'bids': [{'price': '0.01', 'size': '500'}]}
        book_no = {'asks': [{'price': '0.80', 'size': '5'}], 'bids': [{'price': '0.75', 'size': '5'}]}
        
        self.strat.update_market_data_prices(book_yes, book_no, time_remaining=1000)
        orders = self.strat.tick(is_active_market=True)
        
        for o in orders:
            self.assertGreaterEqual(float(o.price), 0.02)

    def test_time_no_entry(self):
        # Case: Price is good (0.25), but Time is T-200s (Limit is T-300s). 
        # Should NOT enter accumulation.
        book_yes = {'asks': [{'price': '0.25', 'size': '500'}], 'bids': [{'price': '0.20', 'size': '500'}]}
        book_no = {} # Irrelevant
        
        self.strat.update_market_data_prices(book_yes, book_no, time_remaining=200.0)
        orders = self.strat.tick(is_active_market=True)
        
        # Currently, if in IDLE and time < NO_ENTRY, it should stay IDLE.
        self.assertEqual(self.strat.state, MarketState.IDLE)
        self.assertEqual(len(orders), 0)

    def test_panic_exit(self):
        # Case: T-50s (Limit T-60s). Bot holds Inventory. Should Panic Sell.
        self.strat.inventory_ledger.q_yes = 100.0
        self.strat.inventory_ledger.apply_fill({"side": "BUY", "token_type": "YES", "size": 100, "price": 0.5, "trade_id": "setup", "timestamp": 123})
        
        book_yes = {'asks': [{'price': '0.55', 'size': '500'}], 'bids': [{'price': '0.50', 'size': '500'}]}
        book_no = {}
        
        self.strat.update_market_data_prices(book_yes, book_no, time_remaining=50.0)
        orders = self.strat.tick(is_active_market=True)
        
        # Should be in STOPPED or similar, generating SELL orders
        # The logic might force state -> HEDGE/STOPPED
        # panic trigger usually inside tick()
        
        # Note: If strategy implementation checks panic first
        # It should try to SELL YES.
        # Check order side
        has_sell = any(o.side == "SELL" and o.token_id == "YES_ID" for o in orders)
        self.assertTrue(has_sell, "Should generate Panic Sell orders")
        
    def test_order_sizing_constraints(self):
        # Verify sizes are clamped
        cfg_size_usd = 3.0
        # If I mock a price of 0.10, size should be ~30 shares.
        book_yes = {'asks': [{'price': '0.25', 'size': '500'}], 'bids': [{'price': '0.10', 'size': '500'}]}
        book_no = {}
        
        self.strat.update_market_data_prices(book_yes, book_no, time_remaining=1000)
        orders = self.strat.tick(is_active_market=True)
        
        for o in orders:
            val = float(o.size) * float(o.price)
            # Should be roughly within MIN/MAX Order USD (2.0 - 5.0)
            # Allow slight tick rounding float err
            self.assertTrue(val >= 1.9, f"Order value {val} too low")
            self.assertTrue(val <= 5.1, f"Order value {val} too high")

if __name__ == "__main__":
    unittest.main()
