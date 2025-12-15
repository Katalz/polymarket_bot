"""
Time utilities for Polymarket bot.
"""

import re
from typing import Optional

def round_to_tick(price: float, tick: float = 0.01) -> float:
    """
    Round price to the nearest tick size.

    Args:
        price: Price to round
        tick: Tick size (default 0.01)

    Returns:
        Rounded price
    """
    # Find the closest multiple of tick
    # Calculate how many ticks
    ticks = price / tick
    # Round to nearest integer
    rounded_ticks = round(ticks)
    # Convert back to price
    result = rounded_ticks * tick
    return round(result, 2)

def parse_market_start_ts(slug: str) -> Optional[float]:
    """
    Parse market start timestamp from slug.
    Expected format: *-15m-<start_epoch>
    """
    match = re.search(r'-15m-(\d+)$', slug)
    if match:
        return float(match.group(1))
    return None

def compute_time_remaining(slug: str, now_ts: float) -> float:
    """
    Compute time remaining for a market in seconds.

    market_start_ts = parse_market_start_ts(slug)
    market_end_ts = market_start_ts + 900  # 15 minutes = 900 seconds
    time_remaining = max(0, market_end_ts - now_ts)

    Args:
        slug: Market slug containing timestamp
        now_ts: Current timestamp

    Returns:
        Time remaining in seconds, or 0 if expired
    """
    market_start_ts = parse_market_start_ts(slug)
    if market_start_ts is None:
        return 0.0

    market_end_ts = market_start_ts + 900.0  # 15 minutes
    time_remaining = max(0.0, market_end_ts - now_ts)

    # Sanity checks
    if time_remaining > 1200.0 or time_remaining < 0.0:
        print(f"[CRITICAL] Invalid time_remaining: {time_remaining}s for slug {slug}")
        print("[EMERGENCY_STOP] Invalid market timing detected")
        return 0.0

    return time_remaining
