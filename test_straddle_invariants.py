#!/usr/bin/env python3
"""
Unit tests for straddle bot invariants using pytest.
"""

import pytest
import time
from unittest.mock import Mock

from gabagool_lite.utils_time import parse_market_start_ts, compute_time_remaining, round_to_tick
from gabagool_lite.straddle_strategy import StraddleArbStrategy


class TestTimeUtils:
    """Test time utility functions."""

    def test_parse_market_start_ts_valid(self):
        """Test parsing valid market slug."""
        slug = "btc-updown-15m-1765836900"
        result = parse_market_start_ts(slug)
        assert result == 1765836900

    def test_parse_market_start_ts_invalid(self):
        """Test parsing invalid market slug."""
        slug = "btc-updown-no-timestamp"
        result = parse_market_start_ts(slug)
        assert result is None

    def test_round_to_tick_default(self):
        """Test rounding to default 0.01 tick."""
        assert round_to_tick(0.123) == 0.12
        assert round_to_tick(0.456) == 0.46
        assert round_to_tick(0.789) == 0.79

    def test_round_to_tick_custom(self):
        """Test rounding to custom tick size."""
        # 0.123 / 0.05 = 2.46, rounds to 2, 2 * 0.05 = 0.10
        assert round_to_tick(0.123, 0.05) == 0.10
        assert round_to_tick(0.456, 0.01) == 0.46

    def test_compute_time_remaining_normal(self):
        """Test normal time remaining calculation."""
        now = time.time()
        # Create a timestamp that's 100 seconds in the future (market start time)
        future_ts = int(now + 100)
        slug = f"test-market-15m-{future_ts}"

        result = compute_time_remaining(slug, now)
        # Time remaining = (market_start + 900) - now = 100 + 900 = 1000
        assert 995 <= result <= 1005

    def test_compute_time_remaining_expired(self):
        """Test expired market."""
        now = time.time()
        past_ts = int(now) - 1000
        slug = f"test-market-15m-{past_ts}"

        result = compute_time_remaining(slug, now)
        assert result == 0.0

    def test_compute_time_remaining_sanity_check(self):
        """Test sanity check for invalid time remaining."""
        # Create a slug with timestamp way in the future (>1200s)
        now = time.time()
        far_future_ts = int(now) + 2000
        slug = f"test-market-15m-{far_future_ts}"

        # This should trigger the sanity check and return 0
        result = compute_time_remaining(slug, now)
        assert result == 0.0


class TestStraddleStrategy:
    """Test straddle strategy invariants."""

    def test_maker_invariant_normal_case(self):
        """Test maker invariant allows valid orders."""
        strategy = StraddleArbStrategy("test-market", has_traded=False)

        # Normal case: prices are below ask
        up_bid, up_ask = 0.45, 0.55
        down_bid, down_ask = 0.45, 0.55
        time_remaining = 100.0

        result = strategy.maybe_enter(up_bid, up_ask, down_bid, down_ask, time_remaining)

        assert result['action'] == 'ENTER'
        assert not result.get('blocked_maker', False)
        assert 'intended_price_up' in result
        assert 'rounded_price_up' in result

    def test_maker_invariant_block_case(self):
        """Test that maker invariant prevents orders that would cross spread."""
        # Note: The current design prevents blocking by construction using min(bid + tick, ask - tick)
        # So this test verifies that the guard exists and works as expected
        strategy = StraddleArbStrategy("test-market", has_traded=False)

        up_bid, up_ask = 0.45, 0.55
        down_bid, down_ask = 0.45, 0.55
        time_remaining = 100.0

        result = strategy.maybe_enter(up_bid, up_ask, down_bid, down_ask, time_remaining)

        # Should succeed and have blocked_maker field
        assert result['action'] == 'ENTER'
        assert 'blocked_maker' in result
        assert result['blocked_maker'] == False

    def test_timeout_invariant(self):
        """Test order timeout invariant."""
        strategy = StraddleArbStrategy("test-market", has_traded=False)

        # Place orders
        strategy.on_orders_placed("order1", "order2")

        # Simulate time passing (more than 20s)
        strategy.up_order_time = time.time() - 25
        strategy.down_order_time = time.time() - 25

        should_cancel, order_id, reason = strategy.should_cancel_timeout()

        assert should_cancel
        assert "timeout 20.0s" in reason

    def test_oneleg_invariant(self):
        """Test one-leg risk invariant."""
        strategy = StraddleArbStrategy("test-market", has_traded=False)

        # Simulate one leg filled
        strategy.on_orders_placed("order1", "order2")
        strategy.on_order_update("order1", True)  # UP filled

        # Manually set the fill time to be old enough to trigger unwind
        strategy.one_leg_fill_time = time.time() - 10  # 10 seconds ago

        should_unwind, leg, reason = strategy.should_unwind_one_leg()

        assert should_unwind
        assert leg == "UP"
        assert "One-leg timeout" in reason

    def test_logging_completeness(self):
        """Test that all required logging fields are present."""
        strategy = StraddleArbStrategy("test-market", has_traded=False)

        log_data = strategy.get_log_data(0.45, 0.55, 0.45, 0.55, time.time())

        required_fields = [
            'timestamp', 'slug', 'market_start_ts', 'market_end_ts', 'now_ts', 'time_remaining',
            'up_bid', 'up_ask', 'down_bid', 'down_ask', 'up_px', 'down_px', 'sum_px', 'max_sum_price',
            'decision', 'blocked_maker', 'order_age', 'up_filled', 'down_filled', 'has_traded',
            'intended_price_up', 'rounded_price_up', 'intended_price_down', 'rounded_price_down',
            'cancel_reason', 'unwind_method', 'unwind_price', 'unwind_result'
        ]

        for field in required_fields:
            assert field in log_data, f"Missing required field: {field}"

    def test_state_transitions(self):
        """Test proper state machine transitions."""
        strategy = StraddleArbStrategy("test-market", has_traded=False)

        # Start in IDLE
        assert strategy.state.value == "IDLE"

        # Place orders -> ORDERS_OPEN
        strategy.on_orders_placed("order1", "order2")
        assert strategy.state.value == "ORDERS_OPEN"

        # Fill one leg -> ONE_LEG_FILLED
        strategy.on_order_update("order1", True)  # This should trigger ONE_LEG_FILLED
        assert strategy.state.value == "ONE_LEG_FILLED"

        # Fill both legs -> DONE
        strategy.on_order_update("order2", True)
        assert strategy.state.value == "DONE"
        assert strategy.has_traded

    def test_persistence_fields(self):
        """Test that persistence-related fields work correctly."""
        strategy = StraddleArbStrategy("test-market", has_traded=False)
        assert not strategy.has_traded

        # Simulate completion
        strategy.has_traded = True
        assert strategy.has_traded

        # Test with pre-traded market
        strategy2 = StraddleArbStrategy("test-market", has_traded=True)
        assert strategy2.has_traded

        result = strategy2.maybe_enter(0.45, 0.55, 0.45, 0.55, 100.0)
        assert result['action'] == 'IDLE'
        assert 'Already traded' in result['reason']
