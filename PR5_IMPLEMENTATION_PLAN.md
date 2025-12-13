# PR5: Daily Loss Limit (Circuit Breaker)

## Goal
Implement a strict **Daily Loss Limit** to address user's fear of "losing everything".
If the bot loses a undefined amount (e.g., $35, ~25% of bankroll) in a single session, it **permanently stops** trading until restarted.

## Proposed Changes

### 1. Configuration (`config.py`)
Add global safety parameters.
```python
# =========================
# GLOBAL SAFETY (CIRCUIT BREAKERS)
# =========================
DAILY_LOSS_LIMIT_USD: float = 35.0   # Stop bot if cumulative loss > $35
MAX_CONSECUTIVE_LOSSES: int = 3      # Stop bot if 3 losing trades in a row
```

### 2. Live Session Tracking (`live_arbitrage_bot.py`)
- **Track Global PnL:** Add `session_pnl_usd` variable to `LiveTraderSession` or global scope.
- **Update Logic:** In `on_trade_success` (or a new `on_trade_close`), update PnL.
    - *Note:* Real-time PnL tracking is complex without selling. But with PR4 (Exit), we realize PnL.
    - *Simplification:* We can only track "Realized Loss" easily. Unrealized is harder.
    - **Logic:** Each time we `SELL`, we calculate PnL. If negative, add to `daily_loss`.
    - Also, if we `HOLD` to expiry and it settles to 0, that's a loss. But bot might not catch settlement event immediately.
    - **Best Proxy:** Track `Realized PnL` from explicit Sells. For expiry losses, we might depend on restart.
    - *Refinement:* Since this is a pilot, we will track **Realized PnL from Active Exits** and **Manual Stops**.

### 3. Kill Switch (`live_arbitrage_bot.py`)
- Add check at start of `run_bot` loop:
  ```python
  if cumulative_loss >= DAILY_LOSS_LIMIT_USD:
      print("[CRITICAL] DAILY LOSS LIMIT BREACHED. SHUTTING DOWN.")
      break
  ```

## Verification Plan

### Automated Test (`test_safety_pr5.py`)
1.  **Test Loss Limit**:
    - Mock Session. PnL = -$30.
    - Trade -> Loss -$10.
    - PnL = -$40.
    - Assert `check_safety_gates()` returns `STOP`.

### Manual Verification
1.  Set `DAILY_LOSS_LIMIT_USD = 1.0` (Testing).
2.  Run bot.
3.  Let it buy (Cost $5).
4.  Force a Sell at loss (or wait for natural loss).
5.  Verify bot stops.
