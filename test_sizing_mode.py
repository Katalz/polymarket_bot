#!/usr/bin/env python3
"""
Test script to verify sizing optimizer test mode functionality.
"""

from gabagool_lite.size_optimizer import SizeOptimizer, SizingConfig

def test_sizing_modes():
    """Test both live and test modes of the sizing optimizer."""

    # Create optimizer with test_size_shares = 10
    config = SizingConfig(
        risk_frac=0.001,
        L_one=0.03,
        k_up=200.0,
        k_down=200.0,
        max_size_cap=1000,
        test_size_shares=10
    )
    optimizer = SizeOptimizer(config)

    # Test parameters
    up_price = 0.45
    down_price = 0.45
    bankroll_usd = 10000.0

    print("=== SIZING OPTIMIZER TEST ===\n")

    # Test 1: LIVE MODE (risk-based)
    print("1. LIVE MODE (risk-based sizing):")
    size_live, debug_live = optimizer.optimize_size(
        up_price=up_price,
        down_price=down_price,
        bankroll_usd=bankroll_usd,
        available_balance=5000.0,
        liquidity_depth=500,
        test_mode=False
    )

    print(f"   Size: {size_live}")
    print(f"   Mode: {debug_live['sizing_mode']}")
    print(".2f")
    print(f"   Risk budget: ${debug_live['risk_budget_usd']:.2f}")
    print(f"   Max risk size: {debug_live['size_max_risk']}")
    print(f"   Final max size: {debug_live['size_max_final']}")
    print()

    # Test 2: TEST MODE (fixed size)
    print("2. TEST MODE (fixed size = 10):")
    size_test, debug_test = optimizer.optimize_size(
        up_price=up_price,
        down_price=down_price,
        bankroll_usd=bankroll_usd,
        available_balance=5000.0,
        liquidity_depth=500,
        test_mode=True
    )

    print(f"   Size: {size_test}")
    print(f"   Mode: {debug_test['sizing_mode']}")
    print(f"   Test size: {debug_test['test_size_shares']}")
    print(f"   Risk budget: {debug_test['risk_budget_usd']}")
    print(f"   Final max size: {debug_test['size_max_final']}")
    print()

    # Test 3: Test mode with constraining caps
    print("3. TEST MODE with tight constraints:")
    size_constrained, debug_constrained = optimizer.optimize_size(
        up_price=up_price,
        down_price=down_price,
        bankroll_usd=bankroll_usd,
        available_balance=2.0,  # Very tight balance constraint
        liquidity_depth=5,      # Tight liquidity constraint
        test_mode=True
    )

    print(f"   Size: {size_constrained}")
    print(f"   Mode: {debug_constrained['sizing_mode']}")
    print(f"   Max balance size: {debug_constrained['size_max_balance']}")
    print(f"   Max liquidity size: {debug_constrained['size_max_liquidity']}")
    print(f"   Final max size: {debug_constrained['size_max_final']}")
    print()

    # Validation
    print("=== VALIDATION ===")
    print(f"✓ Live mode size > 0: {size_live > 0}")
    print(f"✓ Test mode size = 10: {size_test == 10}")
    print(f"✓ Test mode respects caps: {size_constrained <= 5}")  # Should be constrained by liquidity
    print(f"✓ Live mode uses risk constraints: {debug_live['sizing_mode'] == 'RISK_BASED'}")
    print(f"✓ Test mode bypasses risk: {debug_test['sizing_mode'] == 'TEST_FIXED'}")

    # Final result
    all_good = (
        size_live > 0 and
        size_test == 10 and
        size_constrained <= 5 and
        debug_live['sizing_mode'] == 'RISK_BASED' and
        debug_test['sizing_mode'] == 'TEST_FIXED'
    )

    print(f"\n🎯 TEST RESULT: {'PASS' if all_good else 'FAIL'}")
    return all_good

if __name__ == "__main__":
    test_sizing_modes()
