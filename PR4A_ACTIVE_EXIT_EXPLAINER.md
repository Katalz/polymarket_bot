# PR4A: Active Exit (SELL ALL)
*The "Eject Button" Strategy*

## The Concept
In the "Lite" version of the bot, we do not hedge (buy the opposite side). Instead, when we want to secure profit or avoid risk, we **SELL EVERYTHING** we own in that market.

This is an **"All-or-Nothing" Exit**. We do not sell 50%. We dump the full bag.

## Why "SELL ALL"?
1.  **Granularity**: With a $15 position, selling "half" ($7.50) is possible, but selling "10%" ($1.50) is impossible (failed by min ticket). Simplicity is safer: Exit completely.
2.  **Capital Recycling**: $140 is a small bankroll. By determining a trade is "Done" (Win or Loss), we free up 100% of the cash to hunt the next opportunity immediately.
3.  **Variance Control**: It converts a "Potential Win" into "Realized Cash".

## The Two Triggers

### 1. The "Take Profit" (Sniper Exit)
*   **Rule:** `If Unrealized PnL >= +25%`
*   **Action:** Sell 100% of YES shares into the Bid.
*   **Scenario:** You bought YES at $0.50. Setup allows it to pump to $0.63.
    *   *Gabagool* would hedge to lock profit.
    *   *We* simply sell at $0.63.
    *   **Result:** You banked +25% profit. You don't care if it goes to $0.99 or $0.00 afterwards. You won.

### 2. The "Time Exit" (Safety Eject)
*   **Rule:** `If Time Remaining <= 30 seconds`
*   **Action:** Sell 100% of YES shares.
*   **Reason:** The last 30 seconds of a 15m market are pure chaos (coin flips, settlements).
    *   Holding through this is gambling.
    *   Exiting ensures you keep whatever edge you gained during the 14m 30s of the match.

## Execution Flow (Live)
1.  **Check:** Every tick (0.5s), calculate PnL.
2.  **Trigger:** If +25% OR T-30s.
3.  **Order:** Send `limit_order(side="SELL", price=bid_price, size=ALL)`.
4.  **Confirm:** Wait for fill.
5.  **Reset:** Position is now 0. Capital is back in wallet.

## Summary
**PR4A transforms the bot from a "Holder" to a "Trader".**
It allows us to compound small wins (+25%) repeatedly instead of praying for big payouts (+100%) that might go to zero.
