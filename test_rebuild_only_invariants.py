
import unittest
from unittest.mock import MagicMock
import time

from gabagool_lite.straddle_strategy_rebuild_only import StraddleRebuildStrategy
from gabagool_lite.safe_wrapper import SafeBuyOnlyClientWrapper

class TestRebuildInvariants(unittest.TestCase):
    
    def test_no_sell_wrapper(self):
        """Test invariant: SafeWrapper must block SELL orders."""
        mock_client = MagicMock()
        safe_client = SafeBuyOnlyClientWrapper(mock_client)
        
        # Test 1: Explicit SELL (if supported) allowed by base, blocked by wrapper
        res = safe_client.place_limit_maker("token", 0.5, 10, side="SELL")
        self.assertIsNone(res, "Wrapper should return None on SELL")
        mock_client.place_limit_maker.assert_not_called()
        
        # Test 2: Unwind attempt
        res = safe_client.unwind_position("token", 10)
        self.assertIsNone(res, "Wrapper should return None on UNWIND")
        
        # Test 3: Buy allowed
        safe_client.place_limit_maker("token", 0.5, 10, side="BUY")
        mock_client.place_limit_maker.assert_called_with("token", 0.5, 10)

    def test_rebalance_strict_priority(self):
        """Test invariant: Strategy prioritizes rebalance over reinforcement always."""
        strategy = StraddleRebuildStrategy("test-slug")
        strategy.rebalance_tolerance_shares = 0.1
        strategy.filled_up_shares = 10.0
        strategy.filled_down_shares = 5.0 # Imbalance +5 UP (Need DOWN)
        
        # Attempt to enter. Should trigger REBALANCE DOWN.
        # Even if reinforce conditions are met (e.g. cheap prices).
        up_ask = 0.1
        down_ask = 0.1 # Very cheap, sum=0.2
        
        res = strategy.maybe_enter(0.09, 0.1, 0.09, 0.1, time_remaining=600)
        
        self.assertEqual(res['action'], 'ENTER')
        self.assertEqual(res['leg'], 'DOWN') # Must buy missing leg
        self.assertEqual(res['up_size'], 0.0) # No UP buying
        self.assertGreater(res['down_size'], 0.0)
        
    def test_perpetual_rebuild_logic(self):
        """Test invariant: Rebuild allowed even in 'late' phase (time < 3m) if unbalanced."""
        strategy = StraddleRebuildStrategy("test-slug")
        strategy.filled_up_shares = 10.0
        strategy.filled_down_shares = 5.0 # Imbalance
        
        # Late Phase (e.g. 100s remaining)
        res = strategy.maybe_enter(0.4, 0.45, 0.4, 0.45, time_remaining=100)
        
        self.assertEqual(res['action'], 'ENTER', "Should allow rebalance in late phase")
        self.assertEqual(res['leg'], 'DOWN')
        
    def test_time_freeze_for_reinforce(self):
        """Test invariant: New Reinforce blocked in late phase."""
        strategy = StraddleRebuildStrategy("test-slug")
        strategy.filled_up_shares = 10.0
        strategy.filled_down_shares = 10.0 # Balanced
        
        # Late Phase (e.g. 100s remaining)
        res = strategy.maybe_enter(0.4, 0.45, 0.4, 0.45, time_remaining=100)
        
        self.assertEqual(res['action'], 'IDLE')
        self.assertIn("TIME_FREEZE_GATE", res['reason'])

    def test_cost_persistence(self):
        """Test invariant: Strategy restores state correctly."""
        initial = {
            "has_traded": True,
            "filled_up_shares": 50.0,
            "filled_down_shares": 50.0,
            "total_cost_up": 25.0,
            "total_cost_down": 25.0
        }
        strategy = StraddleRebuildStrategy("test-slug", initial_state=initial)
        
        self.assertEqual(strategy.filled_up_shares, 50.0)
        self.assertEqual(strategy.total_cost_up, 25.0)
        self.assertTrue(strategy.has_traded)
        
        # Verify export matches import
        current = strategy.get_current_state()
        self.assertEqual(current['filled_up_shares'], 50.0)
        self.assertEqual(current['total_cost_up'], 25.0)

if __name__ == '__main__':
    unittest.main()
