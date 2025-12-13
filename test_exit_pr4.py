import unittest
from paper_trader import GabagoolLikeStrategy, Position, Config, Decision

class TestExitStrategy(unittest.TestCase):
    def setUp(self):
        self.config = Config(
            MAX_EXPOSURE_USD=100.0,
            BASE_ORDER_USD=5.0,
            ENABLE_SELLING=True,
            TAKE_PROFIT_PCT=0.25, # 25%
            FORCE_EXIT_SECONDS=30
        )
        self.strategy = GabagoolLikeStrategy(self.config)

    def test_take_profit_yes(self):
        # Scenario: Bought YES at 0.50, Price now 0.65 (+30%)
        pos = Position(
            qty_yes=20.0,  # 10$ spent / 0.50
            cost_usd=10.0,
            qty_no=0.0
        )
        p_yes = 0.65
        p_no = 0.35
        
        dec = self.strategy.decide(pos, p_yes, p_no, time_remaining=100)
        
        print(f"TP Test: {dec}")
        self.assertEqual(dec.action, "SELL_YES")
        self.assertAlmostEqual(dec.usd, 20.0) # Sell All
        self.assertTrue("take_profit" in dec.reason)

    def test_time_exit_yes(self):
        # Scenario: Time < 30s
        pos = Position(
            qty_yes=20.0,
            cost_usd=10.0,
            qty_no=0.0
        )
        p_yes = 0.50
        p_no = 0.50
        
        dec = self.strategy.decide(pos, p_yes, p_no, time_remaining=25)
        
        print(f"Time Exit Test: {dec}")
        self.assertEqual(dec.action, "SELL_YES")
        self.assertAlmostEqual(dec.usd, 20.0) # Sell All
        self.assertTrue("time_force_exit" in dec.reason)

    def test_no_exit_weak_profit(self):
        # Scenario: Weak profit (+10%) -> should HOLD (and maybe buy more if cheap)
        # Here 0.55 is cheap, so it might BUY. But definitely NOT SELL.
        pos = Position(
            qty_yes=20.0,
            cost_usd=10.0
        )
        p_yes = 0.55 # +10% from 0.50 base. < 0.25 trigger.
        p_no = 0.45
        
        dec = self.strategy.decide(pos, p_yes, p_no, time_remaining=100)
        
        print(f"Weak Profit Test: {dec}")
        # Should NOT be SELL. 
        # Might be BUY (Cheap) or HOLD.
        self.assertNotEqual(dec.action, "SELL_YES")

if __name__ == '__main__':
    unittest.main()
