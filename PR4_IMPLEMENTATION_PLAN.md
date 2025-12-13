# PR4: Active Exit Strategy (Take Profit & Time Exit)

## Goal
Transform the bot from "Binary Betting" (Buy & Hold) to "Active Trading" (Buy & Sell).
Implement **Take Profit** (+25%) and **Time-Based Exit** (T-30s) to lock in gains and avoid expiry volatility.

## Proposed Changes

### 1. Configuration (`config.py`)
Add parameters to control exit behavior.
```python
# --- EXIT STRATEGY ---
ENABLE_SELLING = True
TAKE_PROFIT_PCT = 0.25       # +25% Unrelized PnL -> SELL ALL
FORCE_EXIT_SECONDS = 30      # Sell all at T-30s
```

### 2. Strategy Logic (`paper_trader.py`)
- **Update `Position` class**: Add `reduce(qty_sold, revenue)` method to properly adjust `cost_usd` and `qty` when selling.
- **Update `GabagoolLikeStrategy`**:
    - Add `check_exit_conditions(pos, p_yes, p_no, time_rem)` method.
    - Logic:
        - Calculate `unrealized_pnl_pct`.
        - Check `time_rem <= FORCE_EXIT_SECONDS`.
        - If triggered, return `Decision("SELL_YES", pos.qty_yes, ...)` (Sell All).
    - Integrate into `decide()`: Call `check_exit_conditions` **before** Momentum Entry logic.

### 3. Execution Wiring (`live_arbitrage_bot.py`)
- **Update `execute_trade`**:
    - Support `SELL_YES` and `SELL_NO` sides.
    - **Logic**:
        - API Side: `"SELL"`
        - Limit Price: `max(price - SLIPPAGE, 0.01)` (Sell into bid).
        - **Skip Exposure Check**: Selling reduces risk, so ignore `MAX_EXPOSURE`.
        - Update `on_trade_success`: Handle `reduce` action.

### 4. Verification Plan

#### Automated Tests (`test_exit_pr4.py`)
1.  **Test Take Profit**:
    - Setup Position: Cost $10, Qty 20 (Avg $0.50).
    - Price moves to $0.70 (+40%).
    - Assert `decide()` returns `SELL_YES`.
2.  **Test Time Exit**:
    - Setup Position.
    - Time remaining = 29s.
    - Assert `decide()` returns `SELL_YES`.
3.  **Test Execution Logic (Mock)**:
    - Verify `execute_trade` calls `create_order(side="SELL")`.

#### Manual Verification
- Run `live_arbitrage_bot.py` with `TAKE_PROFIT_PCT = 0.01` (1%) on a live market.
- Watch it buy, then immediately sell once spread is crossed (or small move).
- **Crucial**: Verify `cost_usd` decreases and capital is freed up.
