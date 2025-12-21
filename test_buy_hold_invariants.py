
import unittest
from unittest.mock import MagicMock
import time
from gabagool_lite.straddle_strategy_buy_hold import StraddleBuyHoldStrategy
from bot_btc_buy_hold import SafeClientWrapper

class TestBuyHoldInvariants(unittest.TestCase):
    def setUp(self):
        self.strategy = StraddleBuyHoldStrategy(slug="test-slug")
        # Mock some filled inventory
        self.strategy.filled_up_shares = 0.0
        self.strategy.filled_down_shares = 0.0
        self.strategy.total_cost_up = 0.0
        self.strategy.total_cost_down = 0.0
        self.strategy.MAX_TOTAL_SHARES = 100.0 # High cap to avoid interfering with logic tests

    def test_invariant_no_reinforce_if_unbalanced(self):
        """
        Verify that if inventory is unbalanced, the strategy NEVER returns a symmetric REINFORCE action,
        even if the price condition for reinforcement is met.
        """
        # 1. Setup Imbalance (Have 2 UP, Need 2 DOWN)
        self.strategy.filled_up_shares = 2.0
        self.strategy.filled_down_shares = 0.0
        self.strategy.total_cost_up = 1.0 # Avg 0.50
        
        # 2. Setup Market Condition that woud trigger Rebalance AND Reinforce
        # Rebalance target: need DOWN.
        # Reinforce target: Sum < 0.98. Let's make market super cheap (0.40/0.40 = 0.80) to tempt it.
        up_bid, up_ask = 0.40, 0.40
        down_bid, down_ask = 0.40, 0.40
        
        time_rem = 600 # 10 mins (Valid for rebalance and reinforce)
        
        res = self.strategy.maybe_enter(up_bid, up_ask, down_bid, down_ask, time_rem)
        
        # 3. Assert Action is REBALANCE (Buy DOWN only)
        # Should buy DOWN. size approx imbalance.
        self.assertEqual(res['action'], 'ENTER')
        self.assertEqual(res['leg'], 'DOWN')
        self.assertGreater(res['down_size'], 0)
        self.assertEqual(res['up_size'], 0.0)
        
        # Ensure reason mentions REBALANCE
        self.assertIn("REBALANCE", res['reason'])

    def test_invariant_rebalance_blocked_by_cost_deadlock(self):
        """
        Verify DEADLOCK protection logic.
        If rebalance cost (synthetic) > 1.0, block it (IDLE).
        Unless Time < 5 min, then allow up to 1.02.
        """
        # Setup Imbalance: Have UP (Avg 0.60), Need DOWN.
        self.strategy.filled_up_shares = 1.0
        self.strategy.total_cost_up = 0.60 
        self.strategy.filled_down_shares = 0.0
        
        # Market for DOWN is expensive: 0.42. 
        # Synth Cost = 0.60 + 0.42 = 1.02.
        # This is > 1.00 (Standard Max).
        
        # Case A: Time > 5 min (600s). Expected: BLOCKED.
        # Arguments: up_bid, up_ask, down_bid, down_ask
        # If we need DOWN, we buy at down_ask=0.42.
        res = self.strategy.maybe_enter(0.0, 0.0, 0.41, 0.42, 600)
        self.assertEqual(res['action'], 'IDLE')
        self.assertIn("REBALANCE_BLOCKED_COST", res['reason'])
        
        # Case B: Time < 5 min (200s). Tolerance 1.02. Expected: ENTER.
        res = self.strategy.maybe_enter(0.0, 0.0, 0.41, 0.42, 200)
        self.assertEqual(res['action'], 'ENTER')
        self.assertEqual(res['leg'], 'DOWN')
        
    def test_invariant_reinforce_strictness(self):
        """
        Verify Reinforce only happens if Market < Avg_Sum - 0.02.
        """
        # Setup Balanced Inventory
        self.strategy.filled_up_shares = 1.0
        self.strategy.filled_down_shares = 1.0
        self.strategy.total_cost_up = 0.50
        self.strategy.total_cost_down = 0.50 
        # Avg Sum = 1.00.
        # Reinforce Target = 0.98.
        
        # Case A: Market = 0.48/0.49 = 0.97. Strictly better than 0.98 Target.
        # This removes floating point equality risk.
        res = self.strategy.maybe_enter(0.48, 0.49, 0.48, 0.49, 600)
        self.assertEqual(res['action'], 'ENTER', msg=f"Action mismatch. Reason: {res.get('reason')}")
        self.assertEqual(res['size'], 5.0) # Chunk size
        
        # Case B: Market = 0.50(Ask UP) + 0.49(Ask DOWN) = 0.99. 
        # Target = 0.98. 0.99 > 0.98. Expected: BLOCKED.
        # Arguments: up_bid, up_ask, down_bid, down_ask
        res = self.strategy.maybe_enter(0.50, 0.50, 0.49, 0.49, 600)
        self.assertEqual(res['action'], 'IDLE', msg=f"Should block. Reason: {res.get('reason')}")

    def test_invariant_time_freeze(self):
        """
        Verify absolute freeze < 3 min.
        """
        res = self.strategy.maybe_enter(0.1, 0.1, 0.1, 0.1, 100) # 1m 40s
        self.assertEqual(res['action'], 'IDLE')
        self.assertIn("TIME_FREEZE_GATE", res['reason'])

    def test_wrapper_failsafe(self):
        """
        Verify SafeClientWrapper returns None on panic methods.
        """
        client = MagicMock()
        wrapper = SafeClientWrapper(client)
        
        # Test Unwind
        res = wrapper.unwind_position("123", 10)
        self.assertIsNone(res) # Should catch and return None
        
        # Test Limit Sell
        # Method signature fallback check
        res = wrapper.place_limit_maker("123", 0.5, 10, side="SELL")
        self.assertIsNone(res)

if __name__ == '__main__':
    unittest.main()
