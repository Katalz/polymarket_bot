import time
import pytest
from gabagool_lite.straddle_strategy_reconstruct import StraddleReconstructStrategy

class TestInventorySyncProtection:
    
    @pytest.fixture
    def strategy(self):
        s = StraddleReconstructStrategy(slug="btc-test")
        s.filled_up_shares = 10.0
        s.filled_down_shares = 0.0
        s.last_fill_ts = time.time() - 5.0 # 5 seconds ago
        return s

    def test_sync_protection_prevents_wipe_within_grace(self, strategy):
        """Verify that a drop to 0.0 is ignored during grace period."""
        # API returns 0.0, but we are in grace period (5s < 30s)
        strategy.sync_state(0.0, 0.0)
        
        # Should stay at 10.0
        assert strategy.filled_up_shares == 10.0
        assert strategy.filled_down_shares == 0.0

    def test_sync_allows_legitimate_increase_within_grace(self, strategy):
        """Verify that an increase IS allowed even during grace period."""
        # API returns 12.0 (maybe another partial fill we didn't track yet)
        strategy.sync_state(12.0, 0.0)
        
        # Should update to 12.0
        assert strategy.filled_up_shares == 12.0

    def test_sync_allows_wipe_after_grace_period(self, strategy):
        """Verify that a drop TO 0.0 is allowed AFTER grace period (e.g. manual sale)."""
        # Set last fill to 31 seconds ago
        strategy.last_fill_ts = time.time() - 31.0
        
        # API returns 0.0
        strategy.sync_state(0.0, 0.0)
        
        # Should update to 0.0
        assert strategy.filled_up_shares == 0.0

    def test_sync_ignores_down_leg_wipe(self, strategy):
        """Verify protection works for both legs."""
        strategy.filled_up_shares = 10.0
        strategy.filled_down_shares = 10.0
        strategy.last_fill_ts = time.time() - 2.0 # Just filled
        
        # API returns 10.0 UP, but 0.0 DOWN (lag)
        strategy.sync_state(10.0, 0.0)
        
        assert strategy.filled_down_shares == 10.0 # Protected
