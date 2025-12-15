#!/usr/bin/env python3
"""
Test script to verify test sizing presets and USD cap functionality.
"""

from gabagool_lite.size_optimizer import SizeOptimizer, SizingConfig

def test_sizing_presets_and_usd_cap():
    """Test test presets and USD cap functionality."""

    print("=== TEST SIZING PRESETS & USD CAP ===\n")

    # Create optimizer with new config
    config = SizingConfig(
        risk_frac=0.001,
        L_one=0.03,
        max_size_cap=1000,
        MAX_USD_PER_MARKET=30.0,
        TEST_MODE_SIZES=[5, 10, 15, 25],
        MIN_TEST_SIZE_SHARES=5
    )
    optimizer = SizeOptimizer(config)

    # Test parameters
    up_price = 0.49
    down_price = 0.48  # Total notional = 0.97 per share
    bankroll_usd = 10000.0

    print(f"Test parameters:")
    print(f"  up_price: {up_price}")
    print(f"  down_price: {down_price}")
    print(f"  straddle_notional: {up_price + down_price}")
    print(f"  MAX_USD_PER_MARKET: {config.MAX_USD_PER_MARKET}")
    print(f"  TEST_MODE_SIZES: {config.TEST_MODE_SIZES}")
    print()

    # Calculate expected USD cap
    expected_usd_cap = int(config.MAX_USD_PER_MARKET / (up_price + down_price))
    print(f"Expected USD cap size: {expected_usd_cap} shares")
    print()

    # Test 1: Test mode - should pick from presets
    print("1. TEST MODE sizing:")
    size_test, debug_test = optimizer.optimize_size(
        up_price=up_price,
        down_price=down_price,
        bankroll_usd=bankroll_usd,
        available_balance=1000.0,
        liquidity_depth=100,
        test_mode=True
    )

    print(f"   Size chosen: {size_test}")
    print(f"   Mode: {debug_test['sizing_mode']}")
    print(f"   Used test preset: {debug_test.get('used_test_preset', 'N/A')}")
    print(f"   Preset candidate: {debug_test.get('test_preset_candidate', 'N/A')}")
    print(f"   USD cap: {debug_test.get('size_max_usd', 'N/A')}")
    print(f"   Final max: {debug_test.get('size_max_final', 'N/A')}")
    straddle_notional = debug_test.get('straddle_notional_per_share', 'N/A')
    if isinstance(straddle_notional, (int, float)):
        print(f"   Straddle notional: {straddle_notional:.3f}")
    else:
        print(f"   Straddle notional: {straddle_notional}")
    print()

    # Test 2: Live mode - should respect USD cap
    print("2. LIVE MODE sizing (with USD cap):")
    size_live, debug_live = optimizer.optimize_size(
        up_price=up_price,
        down_price=down_price,
        bankroll_usd=bankroll_usd,
        available_balance=1000.0,
        liquidity_depth=100,
        test_mode=False
    )

    print(f"   Size chosen: {size_live}")
    print(f"   Mode: {debug_live.get('sizing_mode', 'N/A')}")
    print(f"   USD cap: {debug_live.get('size_max_usd', 'N/A')}")
    print(f"   Final max: {debug_live.get('size_max_final', 'N/A')}")
    print()

    # Test 3: Edge case - very tight USD cap
    print("3. TIGHT USD CAP test (MAX_USD_PER_MARKET=5):")
    config_tight = SizingConfig(MAX_USD_PER_MARKET=5.0)
    optimizer_tight = SizeOptimizer(config_tight)

    size_tight, debug_tight = optimizer_tight.optimize_size(
        up_price=up_price,
        down_price=down_price,
        bankroll_usd=bankroll_usd,
        test_mode=True
    )

    print(f"   Size chosen: {size_tight}")
    print(f"   USD cap: {debug_tight.get('size_max_usd', 'N/A')}")
    print(f"   Reason: {debug_tight.get('reason', 'Success')}")
    print()

    # Validation
    print("=== VALIDATION ===")

    # Test mode validations
    test_preset_valid = size_test in config.TEST_MODE_SIZES
    usd_cap_respected_test = size_test <= expected_usd_cap

    print(f"✓ Test mode picks from presets: {test_preset_valid} ({size_test} in {config.TEST_MODE_SIZES})")
    print(f"✓ Test mode respects USD cap: {usd_cap_respected_test} ({size_test} <= {expected_usd_cap})")
    print(f"✓ Test mode uses TEST_PRESET mode: {debug_test['sizing_mode'] == 'TEST_PRESET'}")

    # Live mode validations
    usd_cap_respected_live = size_live <= expected_usd_cap
    print(f"✓ Live mode respects USD cap: {usd_cap_respected_live} ({size_live} <= {expected_usd_cap})")
    print(f"✓ Live mode uses RISK_BASED mode: {debug_live['sizing_mode'] == 'RISK_BASED'}")

    # Tight cap validation - should find preset 5
    tight_cap_works = size_tight == 5 and debug_tight.get('reason', 'Success') == 'Success'
    print(f"✓ Tight cap finds preset 5: {tight_cap_works}")

    all_good = (
        test_preset_valid and
        usd_cap_respected_test and
        debug_test['sizing_mode'] == 'TEST_PRESET' and
        usd_cap_respected_live and
        debug_live['sizing_mode'] == 'RISK_BASED' and
        tight_cap_works
    )

    print(f"\n🎯 OVERALL TEST RESULT: {'PASS' if all_good else 'FAIL'}")

    # Show the formula
    print("\n📊 USD CAP FORMULA:")
    print(f"size_max_usd = floor(MAX_USD_PER_MARKET / (up_price + down_price))")
    print(f"size_max_usd = floor({config.MAX_USD_PER_MARKET} / ({up_price} + {down_price}))")
    print(f"size_max_usd = floor({config.MAX_USD_PER_MARKET} / {up_price + down_price})")
    print(f"size_max_usd = floor({config.MAX_USD_PER_MARKET / (up_price + down_price):.3f})")
    print(f"size_max_usd = {expected_usd_cap}")

    return all_good

if __name__ == "__main__":
    test_sizing_presets_and_usd_cap()
