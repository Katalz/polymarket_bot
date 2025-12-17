import pytest
from gabagool_lite.straddle_strategy_reconstruct import StraddleReconstructStrategy

class TestReconstructStrategy:
    
    @pytest.fixture
    def strategy(self):
        return StraddleReconstructStrategy(slug="btc-test")

    def test_initial_entry_sizing(self, strategy):
        """Test symmetric entry sizing ($2 per leg) with Cap 10."""
        strategy.MAX_TOTAL_SHARES = 10.0
        # Price 0.49 -> 5 shares each. Total 10.
        # Cap 10. Limit 5 per leg.
        # 5 >= 5 Min.
        # Result: ENTER (5UP, 5DOWN).
        res = strategy.maybe_enter(0.49, 0.50, 0.48, 0.49, 300)
        assert res['action'] == 'ENTER'
        assert res['up_size'] == 5.0
        assert res['down_size'] == 5.0

    def test_initial_entry_symmetry_disparate_prices(self, strategy):
        """Test symmetric entry with high price disparity (0.13 vs 0.85)."""
        strategy.MAX_TOTAL_SHARES = 20.0 # Default
        
        # Up: 0.13 -> $2 / 0.13 = ~15 shares.
        # Down: 0.85 -> $2 / 0.85 = ~2.3 shares -> Min 5.
        
        # Without symmetry enforcement: 15 UP / 5 DOWN (Asymmetric).
        # WITH symmetry enforcement: Should be equal.
        # Likely limited by Cap (10 per leg). So 10 UP / 10 DOWN.
        
        res = strategy.maybe_enter(0.13, 0.14, 0.85, 0.86, 300)
        assert res['action'] == 'ENTER'
        assert res['up_size'] == res['down_size']
        assert res['up_size'] == 10.0 # Cap limited 20/2 = 10

    def test_hard_cap_enforcement(self, strategy):
        """Test strict cap of 10 shares blocking trade if cap space < 5."""
        strategy.MAX_TOTAL_SHARES = 10.0
        strategy.filled_up_shares = 6.0 # 4 left
        strategy.filled_down_shares = 0.0
        
        # Needed 6. Cap 4. Min 5.
        # Cap < Min -> Block.
        res = strategy.maybe_enter(0.49, 0.50, 0.10, 0.11, 300)
        assert res['action'] == 'IDLE' 

    def test_rebalance_logic_cap10(self, strategy):
        """Test rebalancing priority with Cap 10."""
        strategy.MAX_TOTAL_SHARES = 10.0
        # 5 UP filled.
        # Need 5 DOWN.
        # Cap Left 5. Min 5.
        # Should ALLOW 5 DOWN.
        
        strategy.filled_up_shares = 5.0
        strategy.filled_down_shares = 0.0
        
        res = strategy.maybe_enter(0.10, 0.11, 0.10, 0.11, 300)
        assert res['action'] == 'ENTER'
        assert res['up_size'] == 0
        assert res['down_size'] == 5.0

    def test_min_size_enforcement_high_price(self, strategy):
        """Test forcing 5 shares check against Cap 10."""
        strategy.MAX_TOTAL_SHARES = 10.0
        strategy.filled_up_shares = 0.0
        strategy.filled_down_shares = 0.0
        
        # New Entry. Cap 10 left.
        # Limits 5 each.
        # Forces min 5.
        # 5+5=10 <= 10.
        # Should ENTER.
        
        res = strategy.maybe_enter(0.90, 0.91, 0.07, 0.08, 300)
        assert res['action'] == 'ENTER'
        assert res['up_size'] == 5.0
        assert res['down_size'] == 5.0

    def test_balanced_symmetric_entry_blocked(self, strategy):
        """Test balanced entry blocked if cap < 5 per leg."""
        strategy.MAX_TOTAL_SHARES = 10.0
        strategy.filled_up_shares = 2.0
        strategy.filled_down_shares = 2.0
        # Cap left 6. Limit 3 per leg.
        # 3 < 5 -> Block.
        
        res = strategy.maybe_enter(0.40, 0.41, 0.40, 0.41, 300)
        assert res['action'] == 'IDLE'
        assert "TEST_SIZE_TOO_SMALL" in res['reason']
        
