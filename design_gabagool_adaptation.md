# Design Document: Gabagool Strategy Adaptation

This document outlines the proposed changes to align your existing trading bot with the logic, timing, and behavioral patterns reverse-engineered from the Gabagool bot.

## 1. Strategy Differences (Current vs. Gabagool)

| Feature | Your Likely "Standard" Bot | Gabagool (Reverse-Engineered) |
| :--- | :--- | :--- |
| **Inventory Goal** | **Neutrality (Mean Reversion)**<br>Strives to keep `q_yes ≈ q_no`. Hedges exposure. | **Directional Momentum**<br>Allows/Encourages imbalance (`q_yes >> q_no`). Adds to winning positions. |
| **Trading Cycle** | **Continuous**<br>Evaluates logic every tick/second. | **Duty Cycle (2-on-1-off)**<br>Trades for 2 mins, Pauses for 1 min. Strict rhythm. |
| **Market Entry** | **At Start (T=0)**<br>Waits for market open event. | **Pre-Loading (T-60s)**<br>Connects and prepares ~60s before official open. |
| **Sizing Logic** | **Static / Risk-Based**<br>Fixed size or % of bankroll. | **Confidence-Based**<br>Scales size aggressively (up to 3x) when trend is confirmed. |
| **Edge Selection** | **Spread Capture**<br>Bids/Asks around mid-price. | **Extreme Probabilities**<br>Significant volume at `<10%` and `>90%` (Certainty Arbitrage). |

### Critical Deviations
*   **Directional vs. Neutral:** This is the biggest fundamental shift. Switching to momentum logic changes the bot from a market maker to a directional trader.
*   **Duty Cycle:** Implementing the pause is critical to matching the specific "burst" liquidity behavior of Gabagool.

## 2. Proposed Behavioral Changes

### A. Shift from Hedging to Trend Following
*   **Change:** Instead of placing orders to neutralize delta (e.g., if Long 100 YES, sell YES/buy NO), the bot will now allow delta to expand in the direction of profit.
*   **Reason:** Gabagool data shows it often ends with massive unidirectional exposure. It profits from price convergence to 0 or 1, not just spread chopping.
*   **Impact:** Higher variance in PnL, but potentially much higher capture rate on trending markets.

### B. Implement "2-on-1-off" Duty Cycle
*   **Change:** Introduce a strict timing state machine.
  *   *Active:* Minutes 15, 14, 12, 11, 9, 8...
  *   *Pause:* Minutes 13, 10, 7, 4, 1.
*   **Reason:** Reverse-engineering showed zero trades in the "Pause" minutes. This likely lets the bot avoid "churning" during noisy periods or allows it to "snipe" liquidity that builds up during its pause.
*   **Impact:** Reduced transaction costs; cleaner entry points.

### C. Pre-Loading Markets
*   **Change:** Bot must initiate connection and websocket subscription to the *next* market 60 seconds before the *current* market expires.
*   **Reason:** Gabagool's first trade often happens at T=0 or T-1s. To compete, you must be connected before the bell rings.
*   **Impact:** Capturing the initial mispricing volatility.

## 3. Inventory & Directionality Model

We will replace the "Target Neutrality" model with a "Target Trend" model.

*   **Directional Conviction:** Defined by the current Inventory Imbalance.
    *   If `(q_yes - q_no) > Threshold`: **Conviction is YES**.
*   **Decision Logic:**
    *   **If Conviction is YES:**
        *   Allowed: **Buy YES**, **Sell NO**.
        *   Forbidden: **Buy NO** (Hedging), **Sell YES** (Taking Profit too early).
        *   *Exception:* Hard trailing stop triggered.
    *   **If Conviction is NEUTRAL:**
        *   Behave as a standard Market Maker (bid/ask both sides) to build initial position.

## 4. Time & Cycle Control

We will implement a `CycleManager` class to govern the "2-on-1-off" behavior.

*   **Why not just "Modulo 3"?**
    *   A rigid clock is predictable. We want to implement this as a state machine that *can* override the pause if an extreme opportunity (e.g., price crosses 0.10/0.90) is detected, providing a "Hybrid" approach that is safer than Gabagool's rigid blind pause.
    *   *However*, to start, we will respect the rigid Gabagool rhythm to validate the baseline.

## 5. Risk & Safety Gates

While we are adopting aggressive logic, we must retain strict safety rails, especially since we aren't hedging.

*   **Unchanged (Hard Stops):**
    *   **Max Drawdown:** If `locked_pnl < -LIMIT`, close all positions immediately.
    *   **Expiry Cutoff:** Hard stop trading at `T-30s`. Settlement risk is not worth the alpha.
    *   **Max NOTIONAL Exposure:** Scales down Gabagool's $11k to your risk appetite (e.g., $500).

*   **Relaxed Controls:**
    *   **Delta Limit:** Previously strict `0`. Now allowed to float up to `Max Exposure`.
    *   **Skew:** Order book skew checks (bidding only if OB is balanced) will be relaxed to allow aggressive taking.

## 6. Validation Checklist

Use this checklist to approve the implementation plan:

### Logic Validation
- [ ] **Accept:** Directional Logic. Strategy will NOT attempt to hedge delta to zero.
- [ ] **Accept:** Inventory determines trade direction (Long YES -> Buy more YES).
- [ ] **Accept:** Buying NO when Long YES is permitted ONLY for stop-loss/hard exit.

### Timing Validation
- [ ] **Accept:** Bot will shut down pricing logic during minutes 13, 10, 7, 4, and 1 explicitly.
- [ ] **Accept:** Bot will subscribe to new markets 60 seconds *before* start.

### Risk Validation
- [ ] **Adjust:** Max Exposure per market set to **$____** (User to define).
- [ ] **Adjust:** Base Order Size set to **$____** (User to define).
