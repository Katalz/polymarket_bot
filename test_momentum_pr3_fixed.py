from paper_trader import Position, GabagoolLikeStrategy, Config

cfg = Config(MAX_EXPOSURE_USD=35.0, EPS_QTY=3.0, BASE_ORDER_USD=5.0)
strat = GabagoolLikeStrategy(cfg)

print("--- TEST SCENARIOS (PR3 Momentum) ---")

# 1. Momentum YES (Add to Winner)
# Pos: 20 YES (Imbal > EPS), Price: 0.50 (Good price/Edge if p_no=0.49 -> e=0.01)
pos_yes = Position(qty_yes=20.0, qty_no=0.0, cost_usd=10.0)
d1 = strat.decide(pos_yes, 0.50, 0.49)
print(f"1. Momentum YES: {d1.action} | {d1.reason}")
assert d1.action == "BUY_YES", "Should Add-on to YES conviction"
assert "add_on" in d1.reason

# 2. Block Hedge (Conviction YES, Edge negligible for Momentum Add-on)
# We use p_yes = 0.60 (Not Cheap) and Edge < 0.005 (e.g. p_no=0.399 -> e=0.001)
# 1 - (0.60 + 0.399) = 0.001.
# Result: Should NOT buy NO (Hedge blocked) AND NOT buy YES (No signal). -> HOLD.
d2 = strat.decide(pos_yes, 0.60, 0.399)
print(f"2. Hedge Blocked (Weak Edge): {d2.action} | {d2.reason}")
assert d2.action == "HOLD", "Should block BUY_NO (hedge) and wait"
# The reason might be 'hedge_blocked' OR 'momo_yes_waiting' depending on check order.
# Logic: Checks "Buy YES" first. If fails, checks "Buy NO" but returns "Blocked".
# Wait, let's check code order:
# it returns logic "hedge_blocked" if p_no < p_yes.
# Here 0.399 < 0.60. So it should return "hedge_blocked".
assert "hedge_blocked" in d2.reason or "waiting" in d2.reason

# 3. Hard Stop (Adverse Move)
# Avg Price = 10 / 20 = 0.50. Current Price = 0.40 (-20%).
d3 = strat.decide(pos_yes, 0.40, 0.59)
print(f"3. Hard Stop: {d3.action} | {d3.reason}")
assert d3.action == "HOLD", "Should Stop Opening"
assert "hard_stop_triggered" in d3.reason

# 4. Momentum NO
pos_no = Position(qty_yes=0.0, qty_no=20.0, cost_usd=10.0)
d4 = strat.decide(pos_no, 0.49, 0.50)
print(f"4. Momentum NO: {d4.action} | {d4.reason}")
assert d4.action == "BUY_NO", "Should Add-on to NO conviction"

print("All PR3 tests passed.")
