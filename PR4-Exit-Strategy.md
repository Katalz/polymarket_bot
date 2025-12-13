# PR4: Exit Strategy (Turning "Casino" into "Trading")

## The Problem: "Buy & Pray"
Currently, the bot enters (`BUY`) but never exits. It waits for market settlement (0 or 1).
*   **Pros:** Captures full delta (100% gain if correct).
*   **Cons:** Binary outcome. If price goes up +30% then crashes, we lose everything. This feels like "Casino".

## The Solution: Active Management
We enable the bot to **SELL** positions before expiry.

### 1. Take Profit (Securing Wins)
*   **Rule:** If `Unrealized PnL > +30%` (configurable) -> **SELL**.
*   **Logic:** We capture the momentum burst. We don't care about the final result.
*   **Math:** With fees/spread, +30% raw move is a net ~20% win. This stacks up fast.

### 2. Time-Based Exit (No Settlement Gamble)
*   **Rule:** At `T - 30s` (Time remaining), **SELL EVERYTHING**.
*   **Logic:** Avoid the final coin-flip volatility of the last seconds.
*   **Context:** Gabagool often dumps before the end to recycle capital.

### 3. Panic Sell (Stop Loss) - *Optional/Risky*
*   **Rule:** If `Unrealized PnL < -50%` -> SELL.
*   **Risk:** selling into a crash is often selling the bottom. Often better to hold (Momentum implies volatility).
*   **Recommendation:** OFF for now.

## Configuration Updates
```python
ENABLE_SELLING = True
TAKE_PROFIT_PCT = 0.25  # Sell if +25% gain
FORCE_EXIT_SECONDS = 30 # Sell all at T-30s
```

## Impact on "Casino" Feeling
*   **Before:** You bet $15, you wait 15 mins, you have $0 or $28.
*   **After:** You bet $15, price spikes, you sell for $19. You bank $4. Repeat.
