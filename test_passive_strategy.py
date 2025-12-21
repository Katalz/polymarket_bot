
import unittest
from gabagool_lite.straddle_strategy_passive import StraddleStrategyPassive, PassiveStraddleState

class TestPassiveStrategy(unittest.TestCase):
    def setUp(self):
        self.strat = StraddleStrategyPassive("test-market", entry_size_shares=5.0)

    def test_entry_conditions(self):
        # Good Entry
        # Spread = 0.49 + 0.49 = 0.98 (OK)
        # Inv = 0/0
        res = self.strat.maybe_enter(0.48, 0.49, 0.48, 0.49, 900)
        self.assertEqual(res['action'], 'OPEN_STRADDLE')
        
        # Bad Entry (Spread too wide)
        # Spread = 0.50 + 0.50 = 1.00 (> 0.98)
        res = self.strat.maybe_enter(0.49, 0.50, 0.49, 0.50, 900)
        self.assertEqual(res['action'], 'IDLE')
        
        # Bad Entry (Inv > 0)
        self.strat.filled_up_shares = 1.0
        res = self.strat.maybe_enter(0.48, 0.49, 0.48, 0.49, 900)
        self.assertEqual(res['action'], 'IDLE')

    def test_8m_cutoff_no_entry(self):
        # Time < 8m (480s), Inv 0/0 -> Should STOP
        res = self.strat.maybe_enter(0.49, 0.50, 0.49, 0.50, 479)
        self.assertEqual(res['action'], 'IDLE')
        self.assertEqual(self.strat.state, PassiveStraddleState.DONE)
        self.assertTrue(self.strat.market_done)

    def test_one_leg_patient_mode(self):
        # Setup: One Leg Filled (UP), Need DOWN
        self.strat.state = PassiveStraddleState.ONE_LEG_FILLED
        self.strat.filled_up_shares = 5.0
        self.strat.total_cost_up = 5.0 * 0.49 
        self.strat.filled_down_shares = 0.0
        
        # Time: 10m remaining (Patient Mode > 8m)
        # Avg Up Cost = 0.49
        
        # Case 1: Bad Spread (Ask Down 0.50 -> Sum = 0.99 > 0.98)
        # Args: up_ask, down_ask, time_remaining
        res = self.strat.get_management_action(0.50, 0.50, 600)
        self.assertEqual(res['action'], 'WAIT')
        
        # Case 2: Good Spread (Ask Down 0.48 -> Sum = 0.97 <= 0.98)
        res = self.strat.get_management_action(0.50, 0.48, 600)
        self.assertEqual(res['action'], 'REBALANCE_MAKER')
        self.assertEqual(res['leg'], 'DOWN')
        self.assertEqual(res['qty'], 5.0)

    def test_one_leg_safety_a_mode(self):
        # Setup: UP filled @ 0.49
        self.strat.state = PassiveStraddleState.ONE_LEG_FILLED
        self.strat.filled_up_shares = 5.0
        self.strat.total_cost_up = 5.0 * 0.49
        
        # Time: 7m remaining (Safety A: 8m -> 5m)
        # Rule: <= 1.00
        
        # Case 1: Ask Down 0.52 -> Sum = 1.01 (Too expensive)
        res = self.strat.get_management_action(0.50, 0.52, 420)
        self.assertEqual(res['action'], 'WAIT')
        
        # Case 2: Ask Down 0.51 -> Sum = 1.00 (OK for Safety A)
        res = self.strat.get_management_action(0.50, 0.51, 420)
        self.assertEqual(res['action'], 'REBALANCE_MAKER')
        
    def test_one_leg_safety_b_mode(self):
        # Setup: UP filled @ 0.49
        self.strat.state = PassiveStraddleState.ONE_LEG_FILLED
        self.strat.filled_up_shares = 5.0
        self.strat.total_cost_up = 5.0 * 0.49
        
        # Time: 3m remaining (Safety B: 5m -> 0m)
        # Rule: <= 1.01, Taker Allowed
        
        # Case 1: Ask Down 0.53 -> Sum = 1.02 (Too expensive even for panic)
        res = self.strat.get_management_action(0.50, 0.53, 180)
        self.assertEqual(res['action'], 'WAIT')
        
        # Case 2: Ask Down 0.52 -> Sum = 1.01 (OK for Panic)
        res = self.strat.get_management_action(0.50, 0.52, 180)
        self.assertEqual(res['action'], 'REBALANCE_URGENT')
        self.assertTrue(res['allow_taker'])
        self.assertFalse(res['force_taker']) # Not < 2m yet? check logic
        
        # Case 3: Ask Down 0.52, Time < 2m
        # Logic says: force_taker if time < 2.0 min
        res = self.strat.get_management_action(0.50, 0.52, 60)
        self.assertEqual(res['action'], 'REBALANCE_URGENT')
        self.assertTrue(res['force_taker'])

if __name__ == '__main__':
    unittest.main()
