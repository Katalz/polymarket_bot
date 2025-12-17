
import pytest
from gabagool_lite.straddle_strategy_reconstruct import StraddleReconstructStrategy

class TestReconstructAdvanced:
    @pytest.fixture
    def strategy(self):
        s = StraddleReconstructStrategy(slug="btc-test")
        # Ensure default params for deterministic testing
        s.USD_PER_ORDER = 2.0
        s.MAX_TOTAL_SHARES = 10.0
        return s

    def test_case_1_safe_reconstruct_option_a(self, strategy):
        """Case 1: Inv 5/0, Cap 10. Expect Option A (0, 5)."""
        strategy.filled_up_shares = 5.0
        strategy.filled_down_shares = 0.0
        # Imbalance 5. (Need DOWN).
        # Cap Left: 5. 
        # Option B (10 DOWN, 5 UP) Needs 15 cap. Failed.
        # Option A (5 DOWN) Needs 5 cap. Success.
        
        # We assume min_shares = 5.0 inside maybe_enter
        res = strategy.maybe_enter(0.10, 0.11, 0.10, 0.11, 300)
        
        assert res['action'] == 'ENTER'
        assert res['up_size'] == 0.0
        assert res['down_size'] == 5.0
        assert "FILL_MISSING_ONLY" in res['reason']

    def test_case_2_safe_reconstruct_option_a_reverse(self, strategy):
        """Case 2: Inv 0/5, Cap 10. Expect Option A (5, 0)."""
        strategy.filled_up_shares = 0.0
        strategy.filled_down_shares = 5.0
        # Imbalance -5. (Need UP).
        
        res = strategy.maybe_enter(0.10, 0.11, 0.10, 0.11, 300)
        
        assert res['action'] == 'ENTER'
        assert res['up_size'] == 5.0
        assert res['down_size'] == 0.0
        assert "FILL_MISSING_ONLY" in res['reason']

    def test_case_3_aggressive_reconstruct_option_b(self, strategy):
        """Case 3: Inv 5/0, Cap 20. Expect Option B (5 Up, 10 Down)."""
        strategy.MAX_TOTAL_SHARES = 20.0 # Force higher cap
        strategy.filled_up_shares = 5.0
        strategy.filled_down_shares = 0.0
        # Imbalance 5 (Need DOWN).
        # Cap Left 15.
        # Option B: Buy 10 Missing (DOWN) + 5 Existing (UP). Total 15.
        # 15 <= 15. Fits!
        
        res = strategy.maybe_enter(0.10, 0.11, 0.10, 0.11, 300)
        
        assert res['action'] == 'ENTER'
        assert res['up_size'] == 5.0  # Option B existing part
        assert res['down_size'] == 10.0 # Option B missing part
        assert "OVERWEIGHT_MISSING" in res['reason']

    def test_case_5_cap_blocked_min(self, strategy):
        """Case 5: Inv 9/0, Cap 10. Block because cap remaining (1) < min (5)."""
        strategy.filled_up_shares = 9.0
        strategy.filled_down_shares = 0.0
        # Cap Left 1. Min 5.
        
        res = strategy.maybe_enter(0.10, 0.11, 0.10, 0.11, 300)
        
        assert res['action'] == 'IDLE'
        assert "BLOCKED_BY_CAP_MIN" in res['reason']

    def test_case_6_imbalance_large_option_a(self, strategy):
        """Case 6: Imbalance > Min. Inv 20/0, Cap 40. Option A should adhere to Min Size first."""
        strategy.MAX_TOTAL_SHARES = 40.0
        strategy.filled_up_shares = 20.0
        strategy.filled_down_shares = 0.0
        # Imbalance 20.
        # Cap Left 20.
        # Opt B: Buy 10 DOWN, 5 UP. Total 15. Cap Left 20. 
        # Wait, if Option B fits, it will take Option B!
        # Let's force Cap such that Option B fails but A fits.
        # Opt B needs 15. Opt A needs 5.
        
        # To fail B but pass A, we need cap_left < 15 but >= 5.
        # Let's say Cap Left 10.
        # Total Shares 20. So Cap must be 30.
        strategy.MAX_TOTAL_SHARES = 30.0 
        
        res = strategy.maybe_enter(0.10, 0.11, 0.10, 0.11, 300)
        
        # Expect Option A
        assert res['action'] == 'ENTER'
        assert res['up_size'] == 0.0
        assert res['down_size'] == 5.0 # Min Size
        assert "FILL_MISSING_ONLY" in res['reason']
