import os
import glob
import pandas as pd
import numpy as np

# Configuration
TRADES_DIR = r"e:\Cursor Project\Polymarket_bot_arbitrage\gabagool_trades"
MARKET_DURATION = 900  # 15 minutes

def get_start_time_from_filename(filename):
    try:
        base = os.path.basename(filename)
        timestamp_str = base.replace("gabagool_btc-updown-15m-", "").replace(".csv", "")
        return int(timestamp_str)
    except ValueError:
        return None

def analyze_complete():
    all_files = glob.glob(os.path.join(TRADES_DIR, "*.csv"))
    all_trades = []

    for f in all_files:
        start_time = get_start_time_from_filename(f)
        if start_time is None:
            continue
        
        try:
            df = pd.read_csv(f)
            # Expiry is Start + 15m
            expiry = start_time + MARKET_DURATION
            df['expiry'] = expiry
            df['time_to_expiry'] = df['expiry'] - df['timestamp']
            
            # Identify File/Market ID for grouping
            df['market_id'] = start_time
            
            all_trades.append(df)
        except Exception:
            pass

    full_df = pd.concat(all_trades, ignore_index=True)
    
    # 1. Timing Analysis
    print("\n=== TIMING ANALYSIS ===")
    print("Seconds before expiry (Max/Min):")
    print(f"Starts trading: {full_df['time_to_expiry'].max():.0f}s before expiry")
    print(f"Stops trading:  {full_df['time_to_expiry'].min():.0f}s before expiry")
    
    # Check for gaps
    # Group by market and find gaps?
    # We can just check the histogram of seconds
    print("Trade Count by 'Minutes to Expiry':")
    full_df['min_to_exp'] = full_df['time_to_expiry'] // 60
    print(full_df['min_to_exp'].value_counts().sort_index(ascending=False))

    # 2. Inventory Management
    print("\n=== INVENTORY & SIZING ===")
    # Calculate Net Inventory (q_yes - q_no) BEFORE the trade
    # q_yes in CSV is likely *after* the trade? 
    # Let's assume the CSV logs the state *resulting* from the trade (or current state).
    # Typically logs show the state *after* the update.
    # So 'prev_inventory' would be q_yes - size (if buy YES).
    
    # Let's approximate: Inventory Imbalance = (q_yes - q_no).
    full_df['imbalance'] = full_df['q_yes'] - full_df['q_no']
    
    # Analyze relationship: If Imbalance > X, do we Buy NO?
    # Create a 'next_trade_side' column? Hard with concatenated variances.
    # Instead, let's look at correlation between `imbalance` and `side` (mapped to 1/-1).
    full_df['side_val'] = full_df['side'].map({'YES': 1, 'NO': -1})
    
    # We want to see if High Positive Imbalance (Too many YES) leads to -1 (Sell YES / Buy NO).
    # Wait, in Poly, "Buy NO" increases q_no. "Sell YES" reduces q_yes.
    # The logs only show "side". Usually "YES" means "Buy YES", "NO" means "Buy NO".
    # Assuming "side" = "Buy Side".
    # If Imbalance > 0 (Long YES), we expect to Buy NO (side='NO') to hedge.
    
    # Let's group by Imbalance Deciles and seeing % of NO trades.
    full_df['imb_bucket'] = pd.qcut(full_df['imbalance'], 10, duplicates='drop')
    print("\n% NO Trades by Imbalance Bucket (High Imbalance = Long YES):")
    print(full_df.groupby('imb_bucket')['side'].apply(lambda x: (x=='NO').mean()))
    
    # 3. Sizing vs Imbalance
    # Do we trade larger sizes when imbalance is high?
    print("\nAvg Size by Imbalance Bucket:")
    print(full_df.groupby('imb_bucket')['size'].mean())

    # 4. Entry Conditions
    # When do we enter? 
    # Check Price distribution near 0.5 vs edges.
    print("\n=== ENTRY CONDITIONS ===")
    print(full_df['price'].describe())
    
    # Check if we trade when price is extreme (>0.9 or <0.1)
    extreme_trades = full_df[(full_df['price'] > 0.9) | (full_df['price'] < 0.1)]
    print(f"Extreme Price Trades (>0.9 or <0.1): {len(extreme_trades)}")
    
    # 5. Invariants
    print("\n=== INVARIANTS ===")
    # Max Exposure rule?
    print(f"Max Exposure: ${full_df['exposure'].max():.2f}")
    
    # Does 'locked_pnl' ever decrease significantly? (loss realization)
    # We need to sort by time within market
    full_df = full_df.sort_values(['market_id', 'timestamp'])
    full_df['locked_pnl_change'] = full_df.groupby('market_id')['locked_pnl'].diff()
    print("Min Locked PnL Change (Drawdown):", full_df['locked_pnl_change'].min())
    print("Max Locked PnL Change (Profit Lock):", full_df['locked_pnl_change'].max())

if __name__ == "__main__":
    analyze_complete()
