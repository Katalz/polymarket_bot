"""
Size Optimizer for Polymarket Straddle Micro-Arbitrage Bot.

Optimizes position sizing to maximize expected value under strict risk constraints.
"""

import math
import csv
import os
from typing import Dict, Optional, Tuple, List
from dataclasses import dataclass


@dataclass
class SizingConfig:
    """Configuration parameters for sizing optimization."""
    risk_frac: float = 0.001  # 0.1% of bankroll per trade
    L_one: float = 0.03  # One-leg loss per share (slippage + spread + fees)
    MAX_SUM_PRICE: float = 0.98
    TICK_SIZE: float = 0.01
    k_up: float = 200.0  # Fill decay parameter for UP leg
    k_down: float = 200.0  # Fill decay parameter for DOWN leg
    max_size_cap: int = 1000  # Hard cap on size per straddle
    test_size_shares: int = 10  # Fixed size for test mode (no real risk)

    # New USD exposure and test sizing configs
    MAX_USD_PER_MARKET: float = 30.0  # Max USD exposure per market
    TEST_MODE_SIZES: List[int] = None  # Will be set to [5, 10, 15, 25]
    MIN_TEST_SIZE_SHARES: int = 5  # Minimum test size

    def __post_init__(self):
        if self.TEST_MODE_SIZES is None:
            self.TEST_MODE_SIZES = [1, 5, 10]


class SizeOptimizer:
    """
    Optimizes straddle position sizing to maximize expected value.

    Uses probabilistic fill models and enforces strict risk constraints.
    """

    def __init__(self, config: Optional[SizingConfig] = None):
        self.config = config or SizingConfig()
        self.test_rotation_index = 0

    def calibrate_from_logs(self, log_directory: str = ".") -> None:
        """
        Calibrate k_up and k_down parameters from historical CSV logs.

        Args:
            log_directory: Directory containing straddle strategy log files
        """
        up_fill_data = []
        down_fill_data = []

        # Find all log files
        log_files = [f for f in os.listdir(log_directory)
                    if f.startswith("straddle_strategy_log") and f.endswith(".csv")]

        if not log_files:
            print(f"[CALIBRATION] No log files found in {log_directory}, using defaults")
            return

        print(f"[CALIBRATION] Processing {len(log_files)} log files...")

        for log_file in log_files:
            try:
                with open(os.path.join(log_directory, log_file), 'r') as f:
                    reader = csv.DictReader(f)

                    # Check if size column exists (may need to be added to logging)
                    if 'size' not in reader.fieldnames:
                        continue

                    for row in reader:
                        try:
                            size = int(float(row.get('size', 0)))
                            if size <= 0:
                                continue

                            # Check if this was an UP order attempt
                            if row.get('decision') == 'ORDERS_OPEN':
                                filled = row.get('up_filled', 'False').lower() == 'true'
                                up_fill_data.append((size, 1.0 if filled else 0.0))

                            # Check if this was a DOWN order attempt
                            if row.get('decision') == 'ORDERS_OPEN':
                                filled = row.get('down_filled', 'False').lower() == 'true'
                                down_fill_data.append((size, 1.0 if filled else 0.0))

                        except (ValueError, KeyError):
                            continue

            except Exception as e:
                print(f"[CALIBRATION] Error processing {log_file}: {e}")
                continue

        # Fit exponential decay models
        if up_fill_data:
            self.config.k_up = self._fit_exponential_decay(up_fill_data)
            print(".2f")

        if down_fill_data:
            self.config.k_down = self._fit_exponential_decay(down_fill_data)
            print(".2f")

        if not up_fill_data and not down_fill_data:
            print("[CALIBRATION] No valid calibration data found, using defaults")

    def _fit_exponential_decay(self, data: List[Tuple[int, float]]) -> float:
        """
        Fit exponential decay q(size) = exp(-size/k) to fill rate data.

        Uses maximum likelihood estimation for the exponential decay parameter.
        """
        if not data:
            return 200.0  # Default fallback

        # Group by size to get empirical fill rates
        size_groups = {}
        for size, filled in data:
            if size not in size_groups:
                size_groups[size] = {'total': 0, 'filled': 0}
            size_groups[size]['total'] += 1
            size_groups[size]['filled'] += filled

        # Convert to fill rates
        sizes = []
        fill_rates = []
        for size, stats in size_groups.items():
            if stats['total'] >= 3:  # Require minimum sample size
                rate = stats['filled'] / stats['total']
                if 0 < rate < 1:  # Avoid degenerate cases
                    sizes.append(size)
                    fill_rates.append(rate)

        if len(sizes) < 3:
            return 200.0  # Insufficient data

        # Fit exp(-size/k) using log-linear regression
        # ln(rate) = ln(const) - size/k
        # We estimate k from the slope
        try:
            import numpy as np
            from scipy.optimize import curve_fit

            def exp_decay(size, k):
                return np.exp(-size / k)

            # Initial guess for k
            k_guess = 200.0

            # Fit the curve
            popt, _ = curve_fit(exp_decay, sizes, fill_rates,
                              p0=[k_guess], bounds=(10, 1000))

            fitted_k = popt[0]
            print(f"[CALIBRATION] Fitted k={fitted_k:.1f} from {len(sizes)} data points")

            return max(10, min(1000, fitted_k))  # Clamp to reasonable range

        except Exception as e:
            print(f"[CALIBRATION] Fit failed: {e}, using default k=200")
            return 200.0

    def q_leg_fill(self, size: int, is_up: bool) -> float:
        """
        Estimate per-leg fill probability given size.

        Uses exponential decay: q(size) = exp(-size / k)
        """
        k = self.config.k_up if is_up else self.config.k_down
        return math.exp(-size / k)

    def optimize_size(self, up_price: float, down_price: float,
                     bankroll_usd: float, available_balance: Optional[float] = None,
                     liquidity_depth: Optional[float] = None, test_mode: bool = False) -> Tuple[int, Dict]:
        """
        Optimize straddle size to maximize expected value under risk constraints.

        Args:
            up_price: Price for UP leg
            down_price: Price for DOWN leg
            bankroll_usd: Total bankroll in USD
            available_balance: Available balance constraint (optional)
            liquidity_depth: Liquidity depth constraint (optional)

        Returns:
            Tuple of (optimal_size, debug_info_dict)
            Returns (0, info) if trade is invalid
        """
        # Check basic validity
        if up_price + down_price > self.config.MAX_SUM_PRICE:
            return 0, {'reason': 'Sum price too high', 'sum_price': up_price + down_price}

        # Calculate straddle notional (used for USD cap)
        straddle_notional_per_share = up_price + down_price

        # Calculate profit per successful straddle (both legs fill)
        profit_both = 1.0 - straddle_notional_per_share

        if test_mode:
            # TEST MODE: Choose from presets, bypass risk-based sizing
            size_max_risk = float('inf')  # No risk constraint in test mode
            risk_budget_usd = float('inf')
            sizing_mode = "TEST_PRESET"

            # Calculate max size from non-risk constraints first
            size_max_non_risk = self.config.max_size_cap

            if available_balance is not None:
                max_leg_price = max(up_price, down_price)
                size_max_balance = int(available_balance / max_leg_price)
                size_max_non_risk = min(size_max_non_risk, size_max_balance)

            if liquidity_depth is not None:
                size_max_liquidity = int(liquidity_depth)
                size_max_non_risk = min(size_max_non_risk, size_max_liquidity)

            # Apply USD cap
            size_max_usd = int(self.config.MAX_USD_PER_MARKET / straddle_notional_per_share)
            size_max_non_risk = min(size_max_non_risk, size_max_usd)

            # Find rotating preset that fits
            rotation_set = sorted(self.config.TEST_MODE_SIZES)
            target_preset = rotation_set[self.test_rotation_index % len(rotation_set)]
            
            # Apply constraints: min(target_preset, size_max_non_risk)
            # User requirement: "choose sizes from a small set like [1, 5, 10] shares (or min(10, size_max))"
            # We interpret this as: try target_preset, but cap at size_max_non_risk
            
            size_max = min(target_preset, size_max_non_risk)
            
            # If constrained to 0, fail
            if size_max < 1:
                 return 0, {
                    'reason': 'Test size constrained to zero',
                    'straddle_notional_per_share': straddle_notional_per_share,
                    'size_max_usd': size_max_usd,
                    'size_max_non_risk': size_max_non_risk,
                    'target_preset': target_preset,
                    'test_presets': rotation_set,
                    'size_block_reason': 'SKIP_NO_TEST_SIZE_FITS',
                    'test_mode': test_mode
                }
            
            # Increment rotation for next time we successfully pick a size
            self.test_rotation_index += 1
        else:
            # LIVE MODE: Risk-based sizing
            risk_budget_usd = bankroll_usd * self.config.risk_frac
            size_max_risk = int(risk_budget_usd / self.config.L_one)
            size_max = size_max_risk
            sizing_mode = "RISK_BASED"

        # Available balance constraint
        if available_balance is not None:
            # Assume we need to cover both legs at worst case (higher price)
            max_leg_price = max(up_price, down_price)
            size_max_balance = int(available_balance / max_leg_price)
            size_max = min(size_max, size_max_balance)

        # Liquidity depth constraint (e.g., % of visible depth)
        if liquidity_depth is not None:
            size_max_liquidity = int(liquidity_depth)
            size_max = min(size_max, size_max_liquidity)

        # Hard cap
        size_max = min(size_max, self.config.max_size_cap)

        # Validate notional
        if straddle_notional_per_share <= 0:
            return 0, {
                'reason': 'Invalid notional (sum <= 0)',
                'straddle_notional_per_share': straddle_notional_per_share,
                'size_max_risk': size_max_risk,
                'size_max_balance': size_max_balance if available_balance else None,
                'size_max_liquidity': size_max_liquidity if liquidity_depth else None,
                'size_max_cap': self.config.max_size_cap,
                'size_max_usd': 0,
                'size_max_final': 0,
                'usd_per_market_cap': self.config.MAX_USD_PER_MARKET,
                'size_chosen': 0,
                'size_block_reason': 'SKIP_INVALID_NOTIONAL',
                'test_mode': test_mode,
                'used_test_preset': test_mode,
                'test_preset_candidate': 0
            }

        size_max_usd = int(self.config.MAX_USD_PER_MARKET / straddle_notional_per_share)
        size_max = min(size_max, size_max_usd)

        if size_max < 1:
            return 0, {
                'reason': 'Insufficient capacity',
                'size_max_risk': size_max_risk,
                'size_max_balance': size_max_balance if available_balance else None,
                'size_max_liquidity': size_max_liquidity if liquidity_depth else None
            }

        if test_mode:
            # TEST MODE: Use fixed test size (already constrained by caps above)
            # Just validate that the size is positive and check EV for logging
            if size_max < 1:
                return 0, {
                    'sizing_mode': sizing_mode,
                    'reason': 'Test size constrained to zero by caps',
                    'size_max_risk': size_max_risk,
                    'size_max_balance': size_max_balance if available_balance else None,
                    'size_max_liquidity': size_max_liquidity if liquidity_depth else None,
                    'size_max_final': size_max,
                    'profit_both': profit_both,
                    'risk_budget_usd': risk_budget_usd,
                    'test_size_shares': self.config.test_size_shares
                }

            # Calculate EV for the test size (for logging/debugging)
            q_up = self.q_leg_fill(size_max, is_up=True)
            q_down = self.q_leg_fill(size_max, is_up=False)
            q_both = q_up * q_down
            q_one = q_up * (1 - q_down) + q_down * (1 - q_up)
            ev = (q_both * size_max * profit_both) - (q_one * size_max * self.config.L_one)

            debug_info = {
                'sizing_mode': sizing_mode,
                'size_max_risk': size_max_risk,
                'size_max_balance': size_max_balance if available_balance else None,
                'size_max_liquidity': size_max_liquidity if liquidity_depth else None,
                'size_max_final': size_max,
                'profit_both': profit_both,
                'risk_budget_usd': risk_budget_usd,
                'test_size_shares': self.config.test_size_shares,
                'size_evaluations': [{
                    'size': size_max,
                    'q_up': q_up,
                    'q_down': q_down,
                    'q_both': q_both,
                    'q_one': q_one,
                    'ev': ev
                }],
                'best_size': size_max,
                'best_ev': ev,
                'best_ev': ev,
                'ev_per_share': ev / size_max if size_max > 0 else 0,
                'test_mode': test_mode,
                'target_preset': target_preset if 'target_preset' in locals() else self.config.TEST_MODE_SIZES[0]
            }

            return size_max, debug_info

        else:
            # LIVE MODE: Full EV optimization
            size_grid = self._generate_size_grid(size_max)
            best_size = 1
            best_ev = float('-inf')

            debug_info = {
                'sizing_mode': sizing_mode,
                'size_max_risk': size_max_risk,
                'size_max_balance': size_max_balance if available_balance else None,
                'size_max_liquidity': size_max_liquidity if liquidity_depth else None,
                'size_max_final': size_max,
                'profit_both': profit_both,
                'risk_budget_usd': risk_budget_usd,
                'test_size_shares': None,
                'size_evaluations': []
            }

            for size in size_grid:
                # Calculate fill probabilities
                q_up = self.q_leg_fill(size, is_up=True)
                q_down = self.q_leg_fill(size, is_up=False)

                q_both = q_up * q_down
                q_one = q_up * (1 - q_down) + q_down * (1 - q_up)

                # Expected value
                ev = (q_both * size * profit_both) - (q_one * size * self.config.L_one)

                eval_info = {
                    'size': size,
                    'q_up': q_up,
                    'q_down': q_down,
                    'q_both': q_both,
                    'q_one': q_one,
                    'ev': ev
                }
                debug_info['size_evaluations'].append(eval_info)

                if ev > best_ev and ev > 0:  # Only positive EV trades
                    best_ev = ev
                    best_size = size

            # If no positive EV found, don't trade
            if best_ev <= 0:
                debug_info['reason'] = 'No positive EV found'
                return 0, debug_info

            debug_info['best_size'] = best_size
            debug_info['best_ev'] = best_ev
            debug_info['ev_per_share'] = best_ev / best_size

            return best_size, debug_info

    def _generate_size_grid(self, size_max: int) -> List[int]:
        """
        Generate discrete size grid for evaluation.

        Uses geometric progression: 1, 2, 5, 10, 20, 50, 100, ...
        """
        if size_max < 1:
            return []

        sizes = []
        current = 1

        while current <= size_max:
            sizes.append(current)
            if current == 1:
                current = 2
            elif current == 2:
                current = 5
            else:
                current *= 2
                # Add intermediate values for smoother optimization
                if current > 10:
                    intermediate = current // 2
                    if intermediate not in sizes and intermediate <= size_max:
                        sizes.append(intermediate)

        # Ensure size_max is included if not already
        if size_max not in sizes:
            sizes.append(size_max)

        return sorted(list(set(sizes)))

    def get_config_summary(self) -> Dict:
        """Get summary of current configuration."""
        return {
            'risk_frac': self.config.risk_frac,
            'L_one': self.config.L_one,
            'k_up': self.config.k_up,
            'k_down': self.config.k_down,
            'max_size_cap': self.config.max_size_cap,
            'test_size_shares': self.config.test_size_shares,
            'MAX_SUM_PRICE': self.config.MAX_SUM_PRICE
        }
