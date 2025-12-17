
import pytest
from gabagool_lite.straddle_strategy_reconstruct import StraddleReconstructStrategy

class TestReconstructStrategyHardening:
    @pytest.fixture
    def strategy(self):
        s = StraddleReconstructStrategy(slug="btc-test")
        s.MAX_TOTAL_SHARES = 10.0 # Force 10 for hardening tests to verify constraints
        return s

    def test_cap_full_block(self, strategy):
        """Test Critical Point #1: Cap Full Handling."""
        strategy.filled_up_shares = 5.0
        strategy.filled_down_shares = 5.0
        # Total 10. Cap 10.
        
        # Try to enter
        res = strategy.maybe_enter(0.10, 0.11, 0.10, 0.11, 300)
        assert res['action'] == 'IDLE'
        assert res['reason'] == 'CAP_FULL'

    def test_one_leg_lockdown_up(self, strategy):
        """Test Critical Point #2: ONE_LEG_INVENTORY Lockdown (Have UP, Block BOTH)."""
        strategy.filled_up_shares = 5.0
        strategy.filled_down_shares = 0.0
        # Imbalance 5. (Have UP, Need DOWN).
        
        # Market Check:
        # Up Price 0.10, Down Price 0.10. Sum 0.20. (Very favorable).
        # Normally "BOTH" would be attractive.
        # But One-Leg Rule must force "DOWN" only.
        
        res = strategy.maybe_enter(0.10, 0.11, 0.10, 0.11, 300)
        assert res['action'] == 'ENTER'
        assert res['up_size'] == 0
        assert res['down_size'] == 5.0 # Allowed to rebalance
        
        # Verify it did NOT try 5 UP + 5 DOWN.
        
    def test_one_leg_lockdown_down(self, strategy):
        """Test Critical Point #2: ONE_LEG_INVENTORY Lockdown (Have DOWN, Block BOTH)."""
        strategy.filled_up_shares = 0.0
        strategy.filled_down_shares = 5.0
        # Imbalance -5 (Have DOWN, Need UP).
        
        res = strategy.maybe_enter(0.10, 0.11, 0.10, 0.11, 300)
        assert res['action'] == 'ENTER'
        assert res['up_size'] == 5.0 # Allowed to rebalance
        assert res['down_size'] == 0
        
    def test_get_log_data_fields(self, strategy):
        """Verify new log fields exist."""
        strategy.filled_up_shares = 2.0
        strategy.filled_down_shares = 1.0
        
        data = strategy.get_log_data(0.5, 0.51, 0.5, 0.51, 1000)
        assert "inv_up" in data
        assert "inv_down" in data
        assert "total_inventory" in data
        assert "cap_remaining" in data
        assert data["inv_up"] == 2.0
        assert data["cap_remaining"] == 7.0 # 10 - 3
