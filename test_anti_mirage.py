import time
import unittest
from gabagool_lite.straddle_strategy_passive import StraddleStrategyPassive, PassiveStraddleState

class TestAntiMirage(unittest.TestCase):
    def test_confirmation_logic(self):
        # Setup: 2 hits required, 100ms min_dt, 0.98 threshold
        strat = StraddleStrategyPassive(
            slug="test-market",
            entry_size_shares=20,
            entry_confirmations=2,
            entry_confirm_min_dt_ms=100,
            entry_threshold_sum_px=0.98
        )
        
        # Initial State
        self.assertEqual(strat._good_spread_hits, 0)
        
        # Tick 1: Good Spread
        # up_ask=0.30, down_ask=0.67 -> sum=0.97 (Good)
        res1 = strat.maybe_enter(0.29, 0.30, 0.66, 0.67, 600)
        self.assertEqual(strat._good_spread_hits, 1)
        self.assertEqual(res1['action'], 'IDLE')
        self.assertEqual(res1['reason'], 'ENTRY_CONFIRM_WAIT')
        
        # Tick 1b: Same spread, too soon (min_dt=100ms)
        res1b = strat.maybe_enter(0.29, 0.30, 0.66, 0.67, 600)
        self.assertEqual(strat._good_spread_hits, 1) # No increment
        
        # Wait > 100ms
        time.sleep(0.15)
        
        # Tick 2: Good Spread again
        res2 = strat.maybe_enter(0.29, 0.30, 0.66, 0.67, 600)
        self.assertEqual(res2['action'], 'OPEN_STRADDLE')
        self.assertEqual(strat._good_spread_hits, 0) # Reset after entry trigger
        
    def test_mirage_reset(self):
        strat = StraddleStrategyPassive(
            slug="test-market",
            entry_size_shares=20,
            entry_confirmations=2,
            entry_confirm_min_dt_ms=0,
            entry_threshold_sum_px=0.98
        )
        
        # Tick 1: Good
        strat.maybe_enter(0.29, 0.30, 0.66, 0.67, 600)
        self.assertEqual(strat._good_spread_hits, 1)
        
        # Tick 2: Bad Spread (1.01)
        res2 = strat.maybe_enter(0.35, 0.36, 0.64, 0.65, 600) # sum=1.01
        self.assertEqual(strat._good_spread_hits, 0) # RESET!
        self.assertEqual(res2['action'], 'IDLE')
        
        # Tick 3: Good again
        strat.maybe_enter(0.29, 0.30, 0.66, 0.67, 600)
        self.assertEqual(strat._good_spread_hits, 1)

if __name__ == '__main__':
    unittest.main()
