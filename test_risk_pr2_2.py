from paper_trader import Position, GabagoolLikeStrategy, Config

# 1. Setup a test config with 35.0 Exposure Cap
cfg = Config(MAX_EXPOSURE_USD=35.0, SCALE_EDGE_MIN=0.005) # ensure low edge hurdle for specific test
strat = GabagoolLikeStrategy(cfg)

print("--- TEST SCENARIOS (PR2.2) ---")

# Mock the edge check by ensuring prices provide > 0.005 edge or by mocking internal logic if needed.
# Since we control inputs, we will pick prices that yield edge < required but we want to test scale_allowed directly.
# However, scale_allowed internally calculates edge.
# strategy.scale_allowed(pos, p_yes, p_no)
# p_yes=0.5, p_no=0.49 -> edge = 0.01 (1%) -> > SCALE_EDGE_MIN (0.5%) -> Should Pass Edge Check.

p_yes_test = 0.50
p_no_test = 0.49

# Scenario 1: Single Leg, exposure=10 -> scale_allowed SHOULD BE TRUE
pos1 = Position(qty_yes=20.0, qty_no=0.0, cost_usd=10.0)
allowed1, reason1 = strat.scale_allowed(pos1, p_yes_test, p_no_test)
print(f"Scenario 1 (Expo=10, Cap=35): {allowed1} | {reason1}")
assert allowed1 == True, "Single leg scaling should be allowed if under cap"

# Scenario 2: Cap Exact
pos2 = Position(qty_yes=70.0, qty_no=0.0, cost_usd=35.0)
allowed2, reason2 = strat.scale_allowed(pos2, p_yes_test, p_no_test)
print(f"Scenario 2 (Expo=35.0, Cap=35.0): {allowed2} | {reason2}")
# Expect False because >=
assert allowed2 == False, "Exact cap should block (>=)"

# Scenario 3: Over Cap
pos3 = Position(qty_yes=70.0, qty_no=0.0, cost_usd=35.01)
allowed3, reason3 = strat.scale_allowed(pos3, p_yes_test, p_no_test)
print(f"Scenario 3 (Expo=35.01, Cap=35.0): {allowed3} | {reason3}")
assert allowed3 == False, "Over cap should block"

# Scenario 4: Float Guard / Precision (Optional)
# If we have 34.9999999999 -> Should allow
pos4 = Position(qty_yes=69.9, qty_no=0.0, cost_usd=34.9999999)
allowed4, reason4 = strat.scale_allowed(pos4, p_yes_test, p_no_test)
print(f"Scenario 4 (Expo=34.999..., Cap=35.0): {allowed4} | {reason4}")
assert allowed4 == True, "Just under cap should allow"

print("\n--- CONFIG UNIFICATION CHECK ---")
# Check if we can just rely on MAX_EXPOSURE_USD
print(f"Config MAX_EXPOSURE_USD: {cfg.MAX_EXPOSURE_USD}")
