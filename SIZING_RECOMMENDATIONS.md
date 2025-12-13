# Sizing Calibration: $140 Micro-Bankroll Pilot

## Context & Constraints
*   **Total Capital:** $140.00
*   **Strategy:** Directional Momentum (High Variance, Buy-Only).
*   **Platform constraints:** Min viable ticket ~$5.00 (fees/slippage/dust).
*   **Goal:** Survivability > Profit.

## Recommended Parameters

| Parameter | Value | Justification | Confidence |
| :--- | :---: | :--- | :---: |
| **`BASE_ORDER_USD`** | **$5.00** | The absolute floor for Polymarket execution. Below $5, spread/fees eat edge (>1-2% cost). | 100% |
| **`MAX_EXPOSURE_USD`** | **$16.50** | Allows exactly **3 bullets** of $5.50 (Entry + 2 Adds). <br>Risk is ~12% of bankroll per market. High, but unavoidable given $5 floor. | 85% |
| **`MAX_CONSECUTIVE_MARKETS`** | **1** | **Strict Serial Trading.** Never trade concurrent markets.<br>2 markets = 24% risk = Ruin risk too high. | 100% |
| **`DAILY_STOP_LOSS`** | **$35.00** | If you lose 2 full markets ($33) in a day, STOP daily session.<br>Preserves ~75% of capital for next day. | 90% |

## Risk Analysis: The "Survival Math"
With $140 and a $16.50 cap:
*   **Total "Lives" (Full Losses):** ~8.5 consecutive losses to Ruin ($0).
*   **Survivability:** Moderate. A directional strategy without active exit can realistically hit a 3-loss streak (-$50 / -35%).
*   **Comparison:** The previous setting ($35 Cap) allowed only 4 losses to Ruin. $16.50 doubles your longevity.

## Verdict: GO / NO-GO?

**Verdict:** 🟡 **CONDITIONAL GO**

**Condition:** You must accept that **Risk of Ruin is ~25%** even with these conservative settings because the account size ($140) is small relative to the minimum ticket ($5).
*   Ideally, this strategy needs $500+ to trade safer (1-2% risk).
*   At $140, you are trading "Tuition Fees".

**Recommended Configuration Update:**
```python
# config.py
MAX_EXPOSURE_USD = 16.50  # ~12% risk
BASE_ORDER_USD = 5.0      # Min ticket
MAX_CONSECUTIVE_MARKETS = 1
```
