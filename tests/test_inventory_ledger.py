
import unittest
import logging
import sys
import os
sys.path.append(os.getcwd())
from gabagool_lite.inventory import InventoryLedger

# Configure logging to verify outputs
logging.basicConfig(level=logging.INFO)

class TestInventoryLedger(unittest.TestCase):
    def setUp(self):
        self.ledger = InventoryLedger(slug="test-market")

    def test_buy_aggregation(self):
        # Buy 1: 10 @ 0.50
        self.ledger.apply_fill({
            "side": "BUY", "token_type": "YES", "size": 10, "price": 0.50,
            "trade_id": "tx1", "timestamp": 100
        })
        self.assertEqual(self.ledger.q_yes, 10.0)
        self.assertEqual(self.ledger.cost_yes, 5.0) # 10 * 0.5
        
        # Buy 2: 10 @ 0.60
        self.ledger.apply_fill({
            "side": "BUY", "token_type": "YES", "size": 10, "price": 0.60,
            "trade_id": "tx2", "timestamp": 101
        })
        self.assertEqual(self.ledger.q_yes, 20.0)
        self.assertEqual(self.ledger.cost_yes, 11.0) # 5 + 6
        self.assertAlmostEqual(self.ledger.avg_cost_yes, 0.55)

    def test_sell_logic_avg_cost(self):
        # Setup: Buy 20 @ 0.50 avg (Total Cost 10)
        self.ledger.apply_fill({
            "side": "BUY", "token_type": "YES", "size": 20, "price": 0.50,
            "trade_id": "tx1", "timestamp": 100
        })
        
        # SELL 10 @ 0.80
        # Realized PnL should be: (0.80 - 0.50) * 10 = +3.0
        # Removed Cost: 0.50 * 10 = 5.0
        # Remaining: Q=10, Cost = 5.0
        self.ledger.apply_fill({
            "side": "SELL", "token_type": "YES", "size": 10, "price": 0.80,
            "trade_id": "tx2", "timestamp": 102
        })
        
        self.assertEqual(self.ledger.q_yes, 10.0)
        self.assertEqual(self.ledger.cost_yes, 5.0)
        self.assertAlmostEqual(self.ledger.realized_pnl, 3.0)
        self.assertAlmostEqual(self.ledger.avg_cost_yes, 0.50) # Avg cost remains same!

    def test_mtm_pnl_best_bid(self):
        # Setup: Buy 10 @ 0.50 (Cost 5.0)
        self.ledger.apply_fill({
            "side": "BUY", "token_type": "YES", "size": 10, "price": 0.50,
            "trade_id": "tx1", "timestamp": 100
        })
        
        # Scenario: Market Crashes to 0.10 Best Bid
        # MtM = (10 * 0.10) - 5.0 = 1.0 - 5.0 = -4.0
        mtm = self.ledger.compute_mtm_pnl(bid_yes=0.10, bid_no=0.0)
        self.assertAlmostEqual(mtm, -4.0)
        
        # Scenario: Market Moons to 0.90 Best Bid
        # MtM = (10 * 0.90) - 5.0 = 9.0 - 5.0 = +4.0
        mtm = self.ledger.compute_mtm_pnl(bid_yes=0.90, bid_no=0.0)
        self.assertAlmostEqual(mtm, 4.0)

    def test_deduplication(self):
        fill = {
            "side": "BUY", "token_type": "NO", "size": 100, "price": 0.1,
            "trade_id": "dup1", "timestamp": 100
        }
        self.ledger.apply_fill(fill)
        self.assertEqual(self.ledger.q_no, 100)
        
        # Apply same
        self.ledger.apply_fill(fill)
        self.assertEqual(self.ledger.q_no, 100) # Should NOT increase

    def test_sanity_check(self):
        self.ledger.q_yes = 10.0
        ok, msg = self.ledger.sanity_check(api_q_yes=10.0, api_q_no=0.0)
        self.assertTrue(ok)
        
        # Divergence
        ok, msg = self.ledger.sanity_check(api_q_yes=15.0, api_q_no=0.0)
        self.assertFalse(ok)
        print(f"Sanity Msg: {msg}")

if __name__ == "__main__":
    unittest.main()
