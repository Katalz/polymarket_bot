from paper_trader import Position, GabagoolLikeStrategy, Config

# 1. Setup a test config with 35.0 Exposure Cap
cfg = Config(MAX_EXPOSURE_USD=35.0)
strat = GabagoolLikeStrategy(cfg)

print("--- TEST SCENARIOS (PR2 vs PR2.1) ---")

# Scenario 1: Single Leg, exposure=10 -> Should be ALLOWED
pos1 = Position(qty_yes=20.0, qty_no=0.0, cost_usd=10.0)
allowed1, reason1 = strat.scale_allowed(pos1, 0.5, 0.5)
print(f"Scenario 1 (Expo=10, Cap=35): {allowed1} | {reason1}")

# Scenario 2: Exposure Exactly 35.0 -> Should be BLOCKED (if >=) or ALLOWED (if >)
pos2 = Position(qty_yes=70.0, qty_no=0.0, cost_usd=35.0)
allowed2, reason2 = strat.scale_allowed(pos2, 0.5, 0.5)
print(f"Scenario 2 (Expo=35, Cap=35): {allowed2} | {reason2}")

# Scenario 3: Over Cap 35.01 -> Should be BLOCKED
pos3 = Position(qty_yes=70.1, qty_no=0.0, cost_usd=35.01)
allowed3, reason3 = strat.scale_allowed(pos3, 0.5, 0.5)
print(f"Scenario 3 (Expo=35.01, Cap=35): {allowed3} | {reason3}")

# Check exposure definition
print("\n--- DEFINITION CHECK ---")
print(f"Pos1 cost_usd: {pos1.cost_usd}")
print(f"Pos1 exposure_usd(): {pos1.exposure_usd()}")
