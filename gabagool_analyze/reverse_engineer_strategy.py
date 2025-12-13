import os
import glob
import pandas as pd
import numpy as np
from datetime import datetime

# Configuration
TRADES_DIR = r"e:\Cursor Project\Polymarket_bot_arbitrage\gabagool_trades"

def get_expiry_from_filename(filename):
    try:
        # Expected format: gabagool_btc-updown-15m-1765570500.csv
        # Extract the last part: 1765570500
        base = os.path.basename(filename)
        timestamp_str = base.replace("gabagool_btc-updown-15m-", "").replace(".csv", "")
        return int(timestamp_str)
    except ValueError:
        return None

def analyze_strategy():
    all_files = glob.glob(os.path.join(TRADES_DIR, "*.csv"))
    print(f"Found {len(all_files)} files.")

    all_trades = []

    for f in all_files:
        expiry = get_expiry_from_filename(f)
        if expiry is None:
            continue
        
        try:
            df = pd.read_csv(f)
            df['expiry'] = expiry
            df['time_to_expiry'] = df['expiry'] - df['timestamp']
            
            # Filter out strange rows if any
            if 'side' not in df.columns or 'price' not in df.columns:
                continue
                
            all_trades.append(df)
        except Exception as e:
            print(f"Error reading {f}: {e}")

    if not all_trades:
        print("No valid trades found.")
        return

    full_df = pd.concat(all_trades, ignore_index=True)
    
    print("\n--- GLOBAL STATS ---")
    print(f"Total Trades Analyzed: {len(full_df)}")
    print(full_df['side'].value_counts())
    
    print("\n--- PRICE ANALYSIS ---")
    yes_trades = full_df[full_df['side'] == 'YES']
    no_trades = full_df[full_df['side'] == 'NO']
    
    print("YES Price Stats:")
    print(yes_trades['price'].describe())
    print("\nNO Price Stats:")
    print(no_trades['price'].describe())
    
    print("\n--- TIMING ANALYSIS (Seconds before expiry) ---")
    print("Earliest trade (seconds before expiry):", full_df['time_to_expiry'].max())
    print("Latest trade (seconds before expiry):", full_df['time_to_expiry'].min())
    
    # Analyze trade frequency by time bucket
    full_df['minute_bucket'] = (full_df['time_to_expiry'] // 60)
    print("\nTrades per minute bucket (minutes before expiry):")
    min_buckets = full_df['minute_bucket'].value_counts().sort_index(ascending=False)
    print(min_buckets)

    print("\n--- SIZING ANALYSIS ---")
    print("Size (Contracts) Stats:")
    print(full_df['size'].describe())
    print("\nUSD Volume Stats:")
    print(full_df['usd_volume'].describe())

    print("\n--- HEDGING & EXPOSURE ---")
    # Assuming q_yes and q_no track the cumulative position in the logs
    # We want to see the max imbalance
    full_df['imbalance'] = (full_df['q_yes'] - full_df['q_no']).abs()
    print("Max Imbalance (Contracts):", full_df['imbalance'].max())
    print("Average Imbalance:", full_df['imbalance'].mean())
    print("Max Exposure ($):", full_df['exposure'].max())

    print("\n--- LOCKED PNL ---")
    print("Max Locked PnL:", full_df['locked_pnl'].max())
    print("Min Locked PnL:", full_df['locked_pnl'].min())
    
    # Specific Logic Detection
    # 1. Does it trade close to 0.50?
    center_trades = full_df[(full_df['price'] >= 0.45) & (full_df['price'] <= 0.55)]
    print(f"\nTrades between 0.45 and 0.55: {len(center_trades)} ({len(center_trades)/len(full_df)*100:.2f}%)")
    
    # 2. Does it trade at the very end?
    last_minute_trades = full_df[full_df['time_to_expiry'] < 60]
    print(f"Trades in the last 60 seconds: {len(last_minute_trades)}")

    # 3. Analyze Entry Triggers (Edge)
    # Are there columns for edge in the csv? The user prompt said: "Key Data Columns: timestamp, p_yes, p_no, edge..."
    # Let me check if 'edge' is in the columns.
    if 'edge' in full_df.columns:
        print("\n--- EDGE ANALYSIS ---")
        print(full_df['edge'].describe())
    else:
        print("\n'edge' column not found in these CSVs.")
        # If 'p_yes' and 'p_no' exist, calculate spread/edge?
        # The prompt listed `p_yes`, `p_no` as key columns, but the view_file output showed `price`, `side`.
        # Wait, the prompt said "Key Data Columns" then listed `p_yes`, `p_no`, `edge`.
        # BUT the `view_file` output I saw earlier (lines 1-282 of 1765571400.csv) ONLY showed:
        # time_utc,timestamp,side,price,size,usd_volume,q_yes,q_no,exposure,locked_pnl,tx_hash,outcome
        # It did NOT show `edge`, `p_yes`, `p_no` as separate columns on valid lines.
        # Wait, looking at file 1765571400.csv again...
        # It DOES NOT indicate `p_yes` or `p_no` or `edge` columns.
        # It seems the prompt descriptive text might be assuming I have access to some enriched data or I might need to derive it.
        # Or maybe I only looked at some files.
        # Actually, if the CSVs I have don't have it, I have to rely on `price` and `side`.

    # Let's derive "Edge" if possible or just infer it?
    # If side=YES, Price=0.3, maybe the "edge" is deviation from 0.5?
    # Or maybe deviation from an external oracle price? I don't have external price.
    # So I will stick to analyzing `price`.

if __name__ == "__main__":
    analyze_strategy()
